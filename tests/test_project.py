"""What must be true of the finished project, whichever shape it kept.

These run after the documented two-command flow (rename, then select a shape),
because that is the tree a tenant actually pushes: the placeholders are gone and
one CI shape is left.
"""

from __future__ import annotations

import py_compile
import subprocess

import yaml

import template_repo as tr


def test_every_yaml_file_parses(shaped):
    _, root = shaped
    broken = {}
    for rel in tr.files(root):
        if not rel.endswith((".yaml", ".yml")):
            continue
        try:
            list(yaml.safe_load_all((root / rel).read_text(encoding="utf-8")))
        except yaml.YAMLError as exc:
            broken[rel] = str(exc)
    assert broken == {}


def test_every_shell_script_parses(shaped):
    _, root = shaped
    scripts = [rel for rel in tr.files(root) if rel.endswith(".sh")]
    assert scripts, "no shell scripts in the rendered project"
    for rel in scripts:
        proc = subprocess.run(
            ["bash", "-n", str(root / rel)], capture_output=True, text=True
        )
        assert proc.returncode == 0, f"{rel}: {proc.stderr}"


def test_every_python_script_compiles(shaped, tmp_path):
    """The two vendored scripts (link check, release) have no lint job here."""
    _, root = shaped
    sources = [rel for rel in tr.files(root) if rel.endswith(".py")]
    assert sources, "no Python scripts in the rendered project"
    for rel in sources:
        py_compile.compile(
            str(root / rel), cfile=str(tmp_path / "out.pyc"), doraise=True
        )


def test_the_kustomization_lists_only_manifests_that_exist(shaped):
    """A resource entry pointing at a deleted file fails the tenant's Flux
    Kustomization, not the pipeline."""
    _, root = shaped
    flux = root / "kubernetes" / "flux"
    resources = yaml.safe_load((flux / "kustomization.yaml").read_text(encoding="utf-8"))[
        "resources"
    ]
    assert resources
    missing = [name for name in resources if not (flux / name).is_file()]
    assert missing == []


def test_optional_manifests_are_real_files_reachable_by_one_uncomment(shaped):
    """The opt-in add-ons are live YAML in kubernetes/flux/optional/, listed by
    that directory's own kustomization (so CI schema-validates them) and offered
    as a commented resource line in the live kustomization (so enabling one is
    uncommenting a single line)."""
    _, root = shaped
    flux = root / "kubernetes" / "flux"
    optional = flux / "optional"

    on_disk = {
        p.name
        for p in optional.iterdir()
        if p.is_file() and p.name != "kustomization.yaml"
    }
    assert on_disk, "kubernetes/flux/optional/ is empty"

    listed = yaml.safe_load(
        (optional / "kustomization.yaml").read_text(encoding="utf-8")
    )["resources"]
    assert set(listed) == on_disk, "optional/kustomization.yaml is out of step with its dir"

    live = (flux / "kustomization.yaml").read_text(encoding="utf-8")
    offered = {
        line.split("- optional/", 1)[1].split()[0]
        for line in live.splitlines()
        if line.lstrip().startswith("# - optional/")
    }
    assert offered == on_disk, "every optional manifest needs a commented resource line"

    active = yaml.safe_load(live)["resources"]
    assert not [r for r in active if r.startswith("optional/")], (
        "an optional manifest is enabled in the shipped template"
    )


def test_no_manifest_ships_a_commented_out_resource(shaped):
    """House rule: alternates ship as real files in optional/, never as
    commented-out YAML no linter can validate."""
    _, root = shaped
    offenders = [
        rel
        for rel, text in tr.text_files(root)
        if rel.startswith("kubernetes/")
        and any(line.lstrip().startswith("# apiVersion:") for line in text.splitlines())
    ]
    assert offenders == []
