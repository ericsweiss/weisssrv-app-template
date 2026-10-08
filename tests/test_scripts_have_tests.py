#!/usr/bin/env python3
"""Every script this template ships is exercised by a suite in tests/.

Two roots ship code: scripts/, run over this repo, and template/scripts/, which
lands in a generated tenant. Vendored copies are exempt via the manifest.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import render_app
import yaml

REPO_ROOT = render_app.REPO_ROOT
TESTS = Path(__file__).resolve().parent
SCRIPT_ROOTS = (REPO_ROOT / "scripts", REPO_ROOT / "template" / "scripts")
MANIFEST = REPO_ROOT / "scripts" / "vendored-manifest.yml"

# Scripts that ship without a suite on purpose: repo-relative path -> reason.
# An explicit dict, not a filter, so an exemption is a visible edit a reviewer sees.
EXEMPT: dict[str, str] = {}


def vendored_paths(manifest: Path = MANIFEST) -> set[str]:
    """Consumer paths the manifest registers, vendored and forked alike.

    A template/ consumer path carries the jinja conditional that is part of the
    real filename, so these compare against the tree without rewriting.
    """
    config = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    paths = set()
    for section in ("vendored", "forked"):
        for entry in config.get(section) or []:
            paths.add(entry if isinstance(entry, str) else entry["consumer"])
    return paths


def collect(roots, exempt, vendored, base: Path = REPO_ROOT) -> list[Path]:
    """Shipped scripts under `roots`: executable, or .py/.sh (a vendored copy
    ships mode 644). __pycache__ is generated, not shipped."""
    found = []
    for root in roots:
        for path in sorted(Path(root).rglob("*")):
            if "__pycache__" in path.parts or not path.is_file():
                continue
            rel = path.relative_to(base).as_posix()
            if rel in exempt or rel in vendored:
                continue
            if os.access(path, os.X_OK) or path.suffix in (".py", ".sh"):
                found.append(path)
    return found


VENDORED = vendored_paths()
ALL_SCRIPTS = collect(SCRIPT_ROOTS, {}, set())
SHIPPED = collect(SCRIPT_ROOTS, EXEMPT, VENDORED)


def suites_naming(name: str) -> list[Path]:
    """Suites in tests/ that name `name` and define at least one test."""
    naming = []
    for suite in sorted(TESTS.glob("test_*.py")):
        body = suite.read_text(encoding="utf-8")
        if name in body and re.search(r"^\s*def test_", body, re.M):
            naming.append(suite)
    return naming


def test_the_walk_sees_the_scripts_this_repo_ships():
    """Guard the guard: a bad walk would make every assertion below vacuous."""
    assert len(ALL_SCRIPTS) >= 6


def test_the_manifest_subtraction_is_a_real_subtraction():
    """Guard the guard: a manifest that registered nothing would send every
    library copy through the coverage check and fail on all of them. Every
    script here is a registered copy today, so SHIPPED is legitimately empty."""
    assert len(SHIPPED) < len(ALL_SCRIPTS), "the manifest subtracted no script at all"


def test_every_vendored_script_path_exists():
    """A manifest entry naming a script that is gone silently widens the exempt
    set for whatever lands at that path next."""
    missing = [
        path
        for path in sorted(VENDORED)
        if path.endswith((".py", ".sh")) and not (REPO_ROOT / path).exists()
    ]
    assert not missing, (
        "vendored-manifest.yml names scripts that do not exist: " + ", ".join(missing)
    )


def test_every_shipped_script_is_exercised():
    """One test, not one per script: SHIPPED is empty while every script is a
    registered library copy, and an empty parametrize has no ids to build."""
    uncovered = [
        script.relative_to(REPO_ROOT).as_posix()
        for script in SHIPPED
        if not suites_naming(script.name)
    ]
    assert not uncovered, (
        f"named by no suite in tests/: {', '.join(uncovered)} — add one, or name "
        "the script in EXEMPT with a reason."
    )


@pytest.mark.parametrize("key", sorted(EXEMPT))
def test_no_exemption_is_stale(key: str):
    """An exemption outlives its script, or the suite it was waiting for lands:
    either way the entry has to go."""
    assert (REPO_ROOT / key).exists(), f"{key} is exempt but does not exist"
    assert not suites_naming(Path(key).name), (
        f"{key} is exempt yet a suite already names it — drop the EXEMPT entry."
    )


def test_an_uncovered_script_is_reported(tmp_path):
    """Mutation proof: the collector surfaces a script no suite names, and
    non-script files stay out of the set."""
    # Assembled, so this file does not itself become the suite that names it.
    probe = "un" + "loved.sh"
    (tmp_path / probe).write_text("")
    (tmp_path / "notes.md").write_text("")
    assert [p.name for p in collect([tmp_path], {}, set(), base=tmp_path)] == [probe]
    assert not suites_naming(probe)


def test_an_exemption_is_an_explicit_edit(tmp_path):
    """Opting a script out is possible, but only by naming it with a reason."""
    script = tmp_path / "loose.sh"
    script.write_text("")
    assert collect([tmp_path], {}, set(), base=tmp_path)
    assert collect([tmp_path], {"loose.sh": "test fixture"}, set(), base=tmp_path) == []


def test_a_vendored_copy_is_subtracted_by_the_manifest():
    """The manifest, not a name filter, is what exempts a vendored copy."""
    vendored_script = REPO_ROOT / "scripts" / "check-doc-links.py"
    assert vendored_script.relative_to(REPO_ROOT).as_posix() in VENDORED
    assert vendored_script in ALL_SCRIPTS
    assert vendored_script not in SHIPPED


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
