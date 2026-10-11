"""Every `needs: optional: true` resolves to a job something creates.

GitLab ignores one that names no job, so a renamed include job turns the
dependency off. Driven with the vendored gate, over both pipelines.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import render_app
from conftest import require_env

REPO_ROOT = render_app.REPO_ROOT
GATE = REPO_ROOT / "scripts" / "check-ci-include-job-names.py"
TEMPLATE_GATE = (
    REPO_ROOT
    / "template"
    / "scripts"
    / "{% if ci_shape == 'gitlab_selfhosted' %}check-ci-include-job-names.py{% endif %}"
)
_HINT = "The python-tests job's setup_command clones the library into it."

# GitLab ships Jobs/Secret-Detection.gitlab-ci.yml with the instance, so the
# library's secret-detection template includes a file in neither checkout.
# Undeclared, the gate stops at exit 2 rather than resolving a partial job set.
SECRET_DETECTION_BLIND_SPOT = (
    "Jobs/Secret-Detection.gitlab-ci.yml=GitLab ships this template with the "
    "instance, so it is in neither checkout"
)


@pytest.fixture(scope="module")
def lib_path() -> Path:
    """The library checkout the `project:` includes resolve in.

    Unset on a workstation without a clone, so this skips there; under $CI the
    job lost it and the gate would certify a check it never ran.
    """
    path = Path(require_env("WEISSSRV_LIB_PATH", "optional needs", _HINT))
    assert path.is_dir(), (
        f"WEISSSRV_LIB_PATH={path} is not a directory — the gate resolved no include"
    )
    return path


def _run(gate: Path, repo_root: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(gate), "--repo-root", str(repo_root), *extra],
        capture_output=True,
        text=True,
        check=False,
    )


def test_both_vendored_copies_ship() -> None:
    """The root copy gates this pipeline and the shape-gated one ships to a
    tenant; without either, every assertion below is about the wrong tree."""
    assert GATE.is_file(), f"this repository ships no {GATE.name}"
    assert TEMPLATE_GATE.is_file(), "template/scripts ships no conditional copy"


def test_this_repos_optional_needs_resolve(lib_path) -> None:
    """The three optional needs on `pr-agent-review` name jobs two library
    includes create, so a `job_name` input or a rename upstream breaks them."""
    result = _run(
        GATE,
        REPO_ROOT,
        "--lib-path",
        str(lib_path),
        "--require-optional-needs",
        "--allow-unreadable-include",
        SECRET_DETECTION_BLIND_SPOT,
    )
    assert result.returncode == 0, f"{result.stdout}{result.stderr}"


def test_the_rendered_pipelines_optional_needs_resolve(lib_path, tmp_path_factory) -> None:
    """Driven with the copy the render ships, from the render root: the tenant
    gets the gate and the pipeline it gates in one tree."""
    rendered = render_app.render(tmp_path_factory.mktemp("render-optional-needs"))
    gate = rendered / "scripts" / "check-ci-include-job-names.py"
    assert gate.is_file(), "the GitLab-shape render ships no check-ci-include-job-names.py"
    result = _run(
        gate,
        rendered,
        "--lib-path",
        str(lib_path),
        "--require-optional-needs",
        "--allow-unreadable-include",
        SECRET_DETECTION_BLIND_SPOT,
    )
    assert result.returncode == 0, f"{result.stdout}{result.stderr}"


def test_a_dangling_optional_need_is_reported(tmp_path) -> None:
    """Mutation case: a need naming a job nothing creates has to be a finding,
    or the two cases above would pass on a half-finished rename."""
    (tmp_path / ".gitlab-ci.yml").write_text(
        "real-job:\n"
        '  script: ["true"]\n'
        "waiting-job:\n"
        '  script: ["true"]\n'
        "  needs:\n"
        "    - job: renamed-away\n"
        "      optional: true\n",
        encoding="utf-8",
    )
    result = _run(GATE, tmp_path, "--require-optional-needs")
    assert result.returncode == 1, f"{result.stdout}{result.stderr}"
    assert "renamed-away" in result.stderr
