"""Hold the GitHub shape's template-owned workflow and the one PyYAML pin.

`.github/workflows/manifest-gates.yml` is the shape's only CI run of the three manifest
gates, and its PyYAML pin is held equal to the GitLab pipeline's and the pre-commit hooks'.
"""

from __future__ import annotations

import copy
import re
import shutil
from pathlib import Path

import pytest
import yaml

import render_app

WORKFLOW = Path(".github/workflows/manifest-gates.yml")

# The gates the GitLab shape runs as netpol-check, scrape-wiring-check and
# kustomization-check. A dropped step leaves its invariant ungated on this
# shape while every other job still greens.
EXPECTED_GATES = {
    "scripts/check-netpol-except-parity.py",
    "scripts/check-scrape-wiring.py",
    "scripts/check-kustomization.py",
}

SCRIPT_PATH = re.compile(r"scripts/[\w.-]+\.py")


@pytest.fixture(scope="session")
def github_render(tmp_path_factory) -> Path:
    """The github shape: answers-unlike answers `ci_shape: github`."""
    return render_app.render(
        tmp_path_factory.mktemp("render-github"),
        answers=render_app.ANSWERS_B,
        dest_name="render-github",
    )


@pytest.fixture(scope="session")
def gitlab_render(tmp_path_factory) -> Path:
    """The gitlab_selfhosted shape, for the pins it ships and the file it must not."""
    return render_app.render(
        tmp_path_factory.mktemp("render-gitlab"),
        dest_name="render-gitlab",
    )


@pytest.fixture(scope="session")
def none_render(tmp_path_factory) -> Path:
    return render_app.render(
        tmp_path_factory.mktemp("render-none"),
        answers=render_app.ANSWERS_B,
        dest_name="render-none",
        data={"ci_shape": "none"},
    )


def _workflow(root: Path) -> dict:
    return yaml.safe_load((root / WORKFLOW).read_text(encoding="utf-8"))


def _gate_paths(workflow: dict) -> set[str]:
    paths: set[str] = set()
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            paths.update(SCRIPT_PATH.findall(step.get("run") or ""))
    return paths


def _check_gates(workflow: dict, root: Path) -> None:
    """Assert the workflow runs exactly the three gates and each one is there."""
    paths = _gate_paths(workflow)
    missing = sorted(path for path in paths if not (root / path).is_file())
    assert not missing, f"{WORKFLOW} names scripts the render does not ship: {missing}"
    assert paths == EXPECTED_GATES, (
        f"{WORKFLOW} runs {sorted(paths)}, not the three manifest gates "
        f"{sorted(EXPECTED_GATES)}"
    )


def _pyyaml_pins(root: Path) -> dict[str, set[str]]:
    """Every PyYAML pin this render ships, by source. One CI shape renders, so a
    missing pipeline file contributes nothing rather than failing."""
    pins: dict[str, set[str]] = {}
    workflow = root / WORKFLOW
    if workflow.is_file():
        pins["github-workflow"] = {_workflow(root)["env"]["PYYAML_VERSION"]}
    pipeline = root / ".gitlab-ci.yml"
    if pipeline.is_file():
        gate = render_app.load_ci(pipeline)[".pyyaml-gate"]
        pins["gitlab-pyyaml-gate"] = {gate["variables"]["PYYAML_VERSION"]}
    precommit = root / ".pre-commit-config.yaml"
    if precommit.is_file():
        config = yaml.safe_load(precommit.read_text(encoding="utf-8"))
        found = {
            dep.split("==", 1)[1]
            for repo in config["repos"]
            for hook in repo.get("hooks") or []
            for dep in hook.get("additional_dependencies") or []
            if dep.startswith("pyyaml==")
        }
        if found:
            pins["pre-commit"] = found
    return pins


def _check_one_pin(roots: list[Path]) -> None:
    pins: dict[str, set[str]] = {}
    for root in roots:
        for source, versions in _pyyaml_pins(root).items():
            pins.setdefault(source, set()).update(versions)
    assert set(pins) == {"github-workflow", "gitlab-pyyaml-gate", "pre-commit"}, (
        f"a PyYAML pin went away — collected {sorted(pins)}"
    )
    versions = set().union(*pins.values())
    assert len(versions) == 1, f"the shapes gate on different PyYAML releases: {pins}"


def test_the_manifest_gates_workflow_runs_exactly_the_three_gates(github_render):
    """Every named script exits non-zero on a path that is not there, and a
    dropped step leaves its invariant ungated on this shape."""
    _check_gates(_workflow(github_render), github_render)


def test_a_dropped_gate_step_fails_the_workflow_check(github_render):
    workflow = copy.deepcopy(_workflow(github_render))
    workflow["jobs"]["manifest-gates"]["steps"].pop()
    with pytest.raises(AssertionError):
        _check_gates(workflow, github_render)


def test_a_gate_step_naming_a_missing_script_fails_the_workflow_check(github_render):
    workflow = copy.deepcopy(_workflow(github_render))
    workflow["jobs"]["manifest-gates"]["steps"][-1]["run"] = (
        "python3 scripts/check-nothing.py kubernetes/flux"
    )
    with pytest.raises(AssertionError):
        _check_gates(workflow, github_render)


def test_the_manifest_gates_workflow_ships_only_in_the_github_shape(
    github_render, gitlab_render, none_render
):
    assert (github_render / WORKFLOW).is_file()
    for root in (gitlab_render, none_render):
        assert not (root / WORKFLOW).exists(), f"{WORKFLOW} survived into {root.name}"


def test_one_pyyaml_pin_across_the_render(github_render, gitlab_render):
    """The workflow's `PYYAML_VERSION` comment claims equality with the GitLab
    pipeline's pin and the pre-commit hooks'. Nothing else holds it."""
    _check_one_pin([github_render, gitlab_render])


def test_a_drifted_pyyaml_pin_fails_the_pin_check(tmp_path, github_render, gitlab_render):
    drifted = tmp_path / "drifted"
    shutil.copytree(github_render, drifted)
    target = drifted / WORKFLOW
    shipped = _pyyaml_pins(drifted)["github-workflow"].pop()
    target.write_text(
        target.read_text(encoding="utf-8").replace(shipped, f"{shipped}.post1"),
        encoding="utf-8",
    )
    assert _pyyaml_pins(drifted)["github-workflow"] != {shipped}, "the rewrite missed the pin"
    with pytest.raises(AssertionError):
        _check_one_pin([drifted, gitlab_render])
