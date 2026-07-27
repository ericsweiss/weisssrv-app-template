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
