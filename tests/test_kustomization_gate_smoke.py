"""Prove the template's kustomization gate can FAIL.

The rendered manifests pass by construction, so only a mutated resource list shows the
gate is armed. A ref-less remote base (`github.com/org/repo`) is out of scope.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import render_app

GATE = render_app.REPO_ROOT / "template" / "scripts" / "check-kustomization.py"
MANIFEST = "---\napiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: settings\n"
JSON_MANIFEST = '{"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "j"}}\n'
EMPTIED_MANIFEST = "---\n# every object removed\n"
INLINE_PATCH = (
    "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: settings\n"
    "spec:\n  replicas: 3\n"
)


def _tree(tmp_path: Path, kustomization: dict, files: dict[str, str] | None = None) -> Path:
    directory = tmp_path / "flux"
    directory.mkdir()
    for name, body in (files or {"deployment.yaml": MANIFEST}).items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    (directory / "kustomization.yaml").write_text(yaml.safe_dump(kustomization))
    return directory


def _run(directory: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), str(directory)], capture_output=True, text=True
    )


def test_a_complete_list_passes(tmp_path):
    result = _run(_tree(tmp_path, {"resources": ["deployment.yaml"]}))
    assert result.returncode == 0, result.stdout + result.stderr


def test_an_empty_list_fails(tmp_path):
    """kustomize exits 0 on an emptied list and the cluster-side Kustomization
    then prunes every object this repo applied."""
    result = _run(_tree(tmp_path, {"resources": []}))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "lists no resources" in result.stderr


def test_a_listed_path_that_does_not_exist_fails(tmp_path):
    result = _run(_tree(tmp_path, {"resources": ["deployment.yaml", "ghost.yaml"]}))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "do not exist" in result.stderr


def test_a_manifest_on_disk_but_unlisted_fails(tmp_path):
    files = {"deployment.yaml": MANIFEST, "configmap.yaml": MANIFEST}
    result = _run(_tree(tmp_path, {"resources": ["deployment.yaml"]}, files))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "not listed" in result.stderr


@pytest.mark.parametrize("entry", ["./deployment.yaml", "deployment.yaml"])
def test_both_spellings_of_a_file_entry_pass(tmp_path, entry):
    """kustomize takes `./foo.yaml` as well as `foo.yaml`, so the gate must
    normalize before it compares."""
    result = _run(_tree(tmp_path, {"resources": [entry]}))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("entry", ["extras", "extras/", "./extras"])
def test_a_listed_directory_covers_its_children(tmp_path, entry):
    files = {
        "deployment.yaml": MANIFEST,
        "extras/configmap.yaml": MANIFEST,
        "extras/kustomization.yaml": "---\nresources:\n  - configmap.yaml\n",
    }
    result = _run(_tree(tmp_path, {"resources": ["deployment.yaml", entry]}, files))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("key", ["configMapGenerator", "secretGenerator"])
@pytest.mark.parametrize("entry", ["./settings.yaml", "settings=./settings.yaml"])
def test_a_generator_source_is_not_unlisted(tmp_path, key, entry):
    """kustomize spells a generator source `<key>=<path>` as well as a bare path,
    so a gate that reads the whole entry reds a repo that builds."""
    files = {"deployment.yaml": MANIFEST, "settings.yaml": "key: value\n"}
    kustomization = {
        "resources": ["deployment.yaml"],
        key: [{"name": "settings", "files": [entry]}],
    }
    result = _run(_tree(tmp_path, kustomization, files))
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_generator_env_file_that_does_not_exist_fails(tmp_path):
    kustomization = {
        "resources": ["deployment.yaml"],
        "secretGenerator": [{"name": "s", "envs": ["ghost.env"]}],
    }
    result = _run(_tree(tmp_path, kustomization))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "do not exist" in result.stderr


def test_two_kustomizations_listing_each_other_terminate(tmp_path):
    """A cycle between two directories must not recurse without end."""
    root = tmp_path / "flux"
    for name, peer in (("a", "../b"), ("b", "../a")):
        directory = root / name
        directory.mkdir(parents=True)
        (directory / "kustomization.yaml").write_text(yaml.safe_dump({"resources": [peer]}))
    result = subprocess.run(
        [sys.executable, str(GATE), str(root / "a")], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_directory_with_no_kustomization_is_an_error(tmp_path):
    """Exit 2: the gate could not run, which is not the same as a violation."""
    directory = tmp_path / "flux"
    directory.mkdir()
    result = _run(directory)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "does not exist" in result.stderr


def test_a_path_that_is_not_a_directory_is_an_error(tmp_path):
    target = tmp_path / "flux.yaml"
    target.write_text(MANIFEST)
    result = _run(target)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "is not a directory" in result.stderr


def test_an_unparseable_kustomization_is_an_error(tmp_path):
    """Exit 2 naming the file: a half-finished edit is the common state a
    pre-commit hook runs on, and a traceback names no file."""
    directory = tmp_path / "flux"
    directory.mkdir()
    (directory / "deployment.yaml").write_text(MANIFEST)
    (directory / "kustomization.yaml").write_text("a: [\n")
    result = _run(directory)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "kustomization.yaml" in result.stderr
    assert "<unicode string>" not in result.stderr
    assert "Traceback" not in result.stderr


def test_a_bases_only_kustomization_passes(tmp_path):
    """`bases:` is deprecated but kustomize still builds it, so failing a tree
    whose only entry is a base reds a repo that renders."""
    files = {
        "extras/configmap.yaml": MANIFEST,
        "extras/kustomization.yaml": "---\nresources:\n  - configmap.yaml\n",
    }
    result = _run(_tree(tmp_path, {"bases": ["extras"]}, files))
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_bases_entry_that_does_not_exist_fails(tmp_path):
    kustomization = {"resources": ["deployment.yaml"], "bases": ["ghost-base"]}
    result = _run(_tree(tmp_path, kustomization))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "do not exist" in result.stderr


def test_a_patch_only_component_passes(tmp_path):
    """A Component is applied into its parent's render and legally declares only
    patches, so the empty-resources rule must not reach it."""
    files = {
        "deployment.yaml": MANIFEST,
        "comp/patch.yaml": "---\napiVersion: apps/v1\nkind: Deployment\n"
        "metadata:\n  name: settings\nspec:\n  replicas: 3\n",
        "comp/kustomization.yaml": "---\nkind: Component\npatches:\n  - path: patch.yaml\n",
    }
    kustomization = {"resources": ["deployment.yaml"], "components": ["comp"]}
    result = _run(_tree(tmp_path, kustomization, files))
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_component_that_contributes_nothing_fails(tmp_path):
    files = {
        "deployment.yaml": MANIFEST,
        "comp/kustomization.yaml": "---\nkind: Component\n",
    }
    kustomization = {"resources": ["deployment.yaml"], "components": ["comp"]}
    result = _run(_tree(tmp_path, kustomization, files))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "contributes nothing" in result.stderr


@pytest.mark.parametrize(
    "remote",
    [
        "https://github.com/org/repo//dir?ref=v1.0.0",
        "github.com/org/repo//dir?ref=v1.0.0",
        "git@github.com:org/repo.git//dir?ref=v1",
        "gitlab.com/org/repo//base?ref=v1",
        "gitlab.example.com/org/repo.git//base?ref=v1",
        "git::https://gitlab.example.com/org/repo//base?ref=v1",
        # No `?` and no `//` path, so the forced-protocol prefix alone decides
        # this one: the arm that reads the scheme stays load-bearing.
        "git::gitlab.example.com/org/repo",
    ],
)
def test_a_remote_base_is_not_existence_checked(tmp_path, remote):
    """kustomize fetches a PINNED remote base from any forge, so it is not a
    path on disk and existence-checking it reds a tree that builds."""
    result = _run(_tree(tmp_path, {"resources": ["deployment.yaml", remote]}))
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_local_sibling_of_a_remote_base_is_still_checked(tmp_path):
    """The remote guard must not widen into skipping local paths."""
    kustomization = {
        "resources": ["deployment.yaml", "ghost.yaml", "github.com/org/repo//dir?ref=v1"]
    }
    result = _run(_tree(tmp_path, kustomization))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "ghost.yaml" in result.stderr


def test_an_inline_strategic_merge_patch_passes(tmp_path):
    """kustomize accepts an inline patch document under the deprecated key, so
    reading every entry as a path reds a tree that builds."""
    kustomization = {"resources": ["deployment.yaml"], "patchesStrategicMerge": [INLINE_PATCH]}
    result = _run(_tree(tmp_path, kustomization))
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_strategic_merge_patch_path_that_does_not_exist_fails(tmp_path):
    kustomization = {"resources": ["deployment.yaml"], "patchesStrategicMerge": ["ghost.yaml"]}
    result = _run(_tree(tmp_path, kustomization))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "do not exist" in result.stderr


def test_a_listed_manifest_that_carries_no_object_fails(tmp_path):
    """kustomize and kubeconform both exit 0 on an emptied body, and the
    cluster-side Kustomization then prunes what it used to apply."""
    files = {"deployment.yaml": EMPTIED_MANIFEST}
    result = _run(_tree(tmp_path, {"resources": ["deployment.yaml"]}, files))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "carry no object" in result.stderr
    assert "deployment.yaml" in result.stderr


def test_a_listed_manifest_that_carries_an_object_passes(tmp_path):
    files = {"deployment.yaml": MANIFEST, "extra.json": JSON_MANIFEST}
    kustomization = {"resources": ["deployment.yaml", "extra.json"]}
    result = _run(_tree(tmp_path, kustomization, files))
    assert result.returncode == 0, result.stdout + result.stderr


def test_an_unlisted_json_manifest_fails(tmp_path):
    """kustomize accepts a JSON resource, so a scan that globs only YAML credits
    an inert manifest."""
    files = {"deployment.yaml": MANIFEST, "extra.json": JSON_MANIFEST}
    result = _run(_tree(tmp_path, {"resources": ["deployment.yaml"]}, files))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "not listed" in result.stderr


def test_an_unlisted_manifest_inside_a_listed_directory_fails(tmp_path):
    """A listed directory is built by its own kustomization.yaml: a manifest that
    list omits is just as inert as one omitted at the top."""
    files = {
        "deployment.yaml": MANIFEST,
        "extras/listed.yaml": MANIFEST,
        "extras/forgotten.yaml": MANIFEST,
        "extras/kustomization.yaml": "---\nresources:\n  - listed.yaml\n",
    }
    result = _run(_tree(tmp_path, {"resources": ["deployment.yaml", "extras"]}, files))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "extras/forgotten.yaml" in result.stderr


def test_an_unlisted_sibling_sharing_a_listed_directorys_name_prefix_fails(tmp_path):
    """A listed directory covers what is under it, not every sibling whose name
    starts with the same characters."""
    files = {
        "deployment.yaml": MANIFEST,
        "extras/listed.yaml": MANIFEST,
        "extras/kustomization.yaml": "---\nresources:\n  - listed.yaml\n",
        "extras-overlay.yaml": MANIFEST,
    }
    result = _run(_tree(tmp_path, {"resources": ["deployment.yaml", "extras"]}, files))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "extras-overlay.yaml" in result.stderr


def test_a_listed_directory_with_no_kustomization_fails(tmp_path):
    files = {"deployment.yaml": MANIFEST, "extras/configmap.yaml": MANIFEST}
    result = _run(_tree(tmp_path, {"resources": ["deployment.yaml", "extras"]}, files))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "builds nothing here" in result.stderr


# Factories, not documents: a dict built at collection is shared by every case.
@pytest.mark.parametrize(
    "extra_factory",
    [
        lambda: {"patches": [{"path": "ghost-patch.yaml"}]},
        lambda: {"components": ["ghost-component"]},
        lambda: {"helmCharts": [{"name": "c", "valuesFile": "ghost-values.yaml"}]},
        lambda: {"openapi": {"path": "ghost-schema.json"}},
    ],
    ids=["patch", "component", "helm-values", "openapi"],
)
def test_a_referenced_path_that_does_not_exist_fails(tmp_path, extra_factory):
    """Every referenced path is existence-checked, not just `resources:`: a ghost
    patch or component fails here rather than at `kustomize build`."""
    result = _run(_tree(tmp_path, {"resources": ["deployment.yaml"], **extra_factory()}))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "do not exist" in result.stderr


def test_a_missing_pyyaml_is_an_operator_error(tmp_path):
    """Exit 2, not 1: a CI image without PyYAML is not a broken kustomization."""
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "yaml.py").write_text('raise ImportError("stub")\n')
    result = subprocess.run(
        [sys.executable, str(GATE), str(tmp_path)],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(stub)},
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "PyYAML required" in result.stderr


def test_a_non_utf8_manifest_is_an_operator_error(tmp_path):
    """Exit 2, not 1: a file the gate cannot decode is junk it was pointed at,
    not a resource list that drifted, and must not surface as a traceback."""
    directory = _tree(tmp_path, {"resources": ["deployment.yaml"]})
    (directory / "deployment.yaml").write_bytes(b"kind: \xff\xfeConfigMap\n")
    result = _run(directory)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "unreadable" in result.stderr
    assert "Traceback" not in result.stderr


def test_a_non_utf8_kustomization_is_an_operator_error(tmp_path):
    directory = _tree(tmp_path, {"resources": ["deployment.yaml"]})
    (directory / "kustomization.yaml").write_bytes(b"resources:\n  - \xff\xfe.yaml\n")
    result = _run(directory)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "unreadable" in result.stderr
    assert "Traceback" not in result.stderr
