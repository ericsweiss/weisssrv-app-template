"""Hold the GitHub shape's manifest-gate run and the one PyYAML pin.

The vendored `.github/workflows/ci.yml`'s `manifest-gates` job is the shape's only CI run
of the three gates, and its PyYAML pin is the one the other two sources follow.
"""

from __future__ import annotations

import copy
import re
import shutil
from pathlib import Path

import pytest
import yaml

import render_app

WORKFLOW = Path(".github/workflows/ci.yml")

# The gates the GitLab shape runs as netpol-check, scrape-wiring-check and
# kustomization-check. The vendored job warns and skips a gate the repo does not
# ship, so a render that stops shipping one leaves its invariant ungated green.
EXPECTED_GATES = {
    "check-netpol-except-parity.py",
    "check-scrape-wiring.py",
    "check-kustomization.py",
}

# The job calls each gate through its own `run_gate <script> [args...]` helper,
# which prefixes `scripts/` itself.
RUN_GATE = re.compile(r"run_gate ([\w.-]+\.py)")


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


def _gate_names(workflow: dict) -> set[str]:
    names: set[str] = set()
    for step in workflow["jobs"]["manifest-gates"]["steps"]:
        names.update(RUN_GATE.findall(step.get("run") or ""))
    return names


def _check_gates(workflow: dict, root: Path) -> None:
    """Assert the job runs exactly the three gates and the render ships each."""
    names = _gate_names(workflow)
    missing = sorted(name for name in names if not (root / "scripts" / name).is_file())
    assert not missing, f"{WORKFLOW} names scripts the render does not ship: {missing}"
    assert names == EXPECTED_GATES, (
        f"{WORKFLOW} runs {sorted(names)}, not the three manifest gates "
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


def test_the_manifest_gates_job_runs_exactly_the_three_gates(github_render):
    """The job skips a gate the repo does not ship, so the render has to carry
    all three, and a dropped call leaves its invariant ungated on this shape."""
    _check_gates(_workflow(github_render), github_render)


def _mutate(workflow: dict, old: str, new: str) -> dict:
    """The job's gate calls live in one shell step; rewrite that step's text."""
    workflow = copy.deepcopy(workflow)
    steps = workflow["jobs"]["manifest-gates"]["steps"]
    step = next(s for s in steps if RUN_GATE.search(s.get("run") or ""))
    assert old in step["run"], f"the mutation missed: {old}"
    step["run"] = step["run"].replace(old, new)
    return workflow


def test_a_dropped_gate_call_fails_the_job_check(github_render):
    workflow = _mutate(_workflow(github_render), "run_gate check-kustomization.py", ":")
    with pytest.raises(AssertionError):
        _check_gates(workflow, github_render)


def test_a_gate_call_naming_a_missing_script_fails_the_job_check(github_render):
    workflow = _mutate(
        _workflow(github_render), "run_gate check-kustomization.py", "run_gate check-nothing.py"
    )
    with pytest.raises(AssertionError):
        _check_gates(workflow, github_render)


def test_the_ci_workflow_ships_only_in_the_github_shape(
    github_render, gitlab_render, none_render
):
    assert (github_render / WORKFLOW).is_file()
    for root in (gitlab_render, none_render):
        assert not (root / WORKFLOW).exists(), f"{WORKFLOW} survived into {root.name}"


def test_one_pyyaml_pin_across_the_render(github_render, gitlab_render):
    """The vendored workflow's `PYYAML_VERSION` is the library's, and the GitLab
    pipeline's pin and the pre-commit hooks' follow it. Nothing else holds them."""
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
