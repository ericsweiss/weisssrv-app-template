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

REPO_ROOT = render_app.REPO_ROOT
TESTS = Path(__file__).resolve().parent
SCRIPT_ROOTS = (REPO_ROOT / "scripts", REPO_ROOT / "template" / "scripts")

# Scripts that ship without a suite on purpose: repo-relative path -> reason.
# An explicit dict, not a filter, so an exemption is a visible edit a reviewer sees.
EXEMPT: dict[str, str] = {}


def _is_script_path(path: str) -> bool:
    """A manifest consumer path names a script once it is rendered."""
    return render_app.rendered_name(path).endswith((".py", ".sh"))


def collect(roots, exempt, vendored, base: Path = REPO_ROOT) -> list[Path]:
    """Shipped scripts under `roots`: executable, or .py/.sh once a conditional
    name's jinja tags are dropped. __pycache__ is generated, not shipped."""
    found = []
    for root in roots:
        for path in sorted(Path(root).rglob("*")):
            if "__pycache__" in path.parts or not path.is_file():
                continue
            rel = path.relative_to(base).as_posix()
            if rel in exempt or rel in vendored:
                continue
            if os.access(path, os.X_OK) or _is_script_path(path.name):
                found.append(path)
    return found


# Consumer paths the manifest registers, vendored and forked alike. A template/
# path carries the jinja conditional that is part of the real filename.
VENDORED = set(render_app.consumer_paths())
ALL_SCRIPTS = collect(SCRIPT_ROOTS, {}, set())
SHIPPED = collect(SCRIPT_ROOTS, EXEMPT, VENDORED)


def suites_naming(name: str, tests: Path = TESTS) -> list[Path]:
    """Suites in `tests` that name `name` as a whole filename and define a test."""
    spelled = re.compile(r"(?<![\w.-])" + re.escape(name) + r"(?![\w-])")
    naming = []
    for suite in sorted(tests.glob("test_*.py")):
        body = suite.read_text(encoding="utf-8")
        if spelled.search(body) and re.search(r"^\s*def test_", body, re.M):
            naming.append(suite)
    return naming


def test_the_walk_sees_the_scripts_this_repo_ships():
    """Guard the guard: a bad walk would make every assertion below vacuous.
    Every script the manifest registers under a script root must be walked."""
    walked = {path.relative_to(REPO_ROOT).as_posix() for path in ALL_SCRIPTS}
    roots = tuple(root.relative_to(REPO_ROOT).as_posix() + "/" for root in SCRIPT_ROOTS)
    registered = {
        path for path in VENDORED if path.startswith(roots) and _is_script_path(path)
    }
    assert registered, "the manifest registers no script under a script root"
    assert registered <= walked, f"the walk missed: {sorted(registered - walked)}"


def test_the_manifest_subtraction_is_a_real_subtraction():
    """Guard the guard: a manifest that registered nothing would send every
    library copy through the coverage check and fail on all of them. While
    every script is a registered copy, SHIPPED is empty."""
    assert len(SHIPPED) < len(ALL_SCRIPTS), "the manifest subtracted no script at all"


def test_every_vendored_script_path_exists():
    """A manifest entry naming a script that is gone silently widens the exempt
    set for whatever lands at that path next."""
    missing = [
        path
        for path in sorted(VENDORED)
        if _is_script_path(path) and not (REPO_ROOT / path).exists()
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
        if not suites_naming(render_app.rendered_name(script.name))
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


def test_a_conditional_script_name_is_collected(tmp_path):
    """A template script gated by a jinja path is not executable and its raw
    suffix is the closing tag, so the name is judged with the tags dropped."""
    name = "{% if ci_shape == 'github' %}gate.py{% endif %}"
    (tmp_path / name).write_text("")
    (tmp_path / "{% if ci_shape == 'github' %}notes.md{% endif %}").write_text("")
    assert [p.name for p in collect([tmp_path], {}, set(), base=tmp_path)] == [name]


def test_a_conditional_script_is_covered_by_its_rendered_name(tmp_path):
    """A suite spells a conditional script by the name it renders to, so that
    name is the one the coverage lookup searches for."""
    name = "{% if ci_shape == 'github' %}gate.py{% endif %}"
    (tmp_path / "test_gate.py").write_text("def test_it():\n    run('gate.py')\n")
    assert suites_naming(render_app.rendered_name(name), tests=tmp_path)
    assert not suites_naming(name, tests=tmp_path)


def test_a_longer_filename_does_not_count_as_naming(tmp_path):
    """Coverage needs the whole filename, not a substring of a longer one."""
    (tmp_path / "test_other.py").write_text("def test_it():\n    run('my-gate.py')\n")
    assert not suites_naming("gate.py", tests=tmp_path)


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
