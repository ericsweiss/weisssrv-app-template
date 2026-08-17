"""Tests for the gates in tests/validate_render.py that have no binary to run.

`check_include_contract` cross-checks the generated pipeline against the
library templates it pins, and it is the only gate here whose FAILURE mode is
silence: a template shape it mis-reads produces no problem line, so the gate
reports ok on a pipeline GitLab would reject at push time. Every arm is
therefore proved against a fixture pipeline and a fixture library — a real
library checkout is expected to pass and so proves nothing about failure.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

import validate_render

LIB_PROJECT = "eric/weisssrv-lib"
LIB_REF = "v1.2.3"
TEMPLATE_FILE = "/ci/lint/thing.yml"


def _library(tmp_path: Path, body: str, rel: str = TEMPLATE_FILE) -> Path:
    lib = tmp_path / "lib"
    path = lib / rel.lstrip("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body))
    return lib


def _pipeline(
    tmp_path: Path,
    passed: dict | None = None,
    stages: tuple[str, ...] | None = ("lint",),
    rel: str = TEMPLATE_FILE,
) -> Path:
    """A generated repo holding one `include:` of the fixture template.

    `stages=None` omits the key, which is how a pipeline asks for GitLab's
    implicit defaults (build/test/deploy).
    """
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    include = {"project": LIB_PROJECT, "ref": LIB_REF, "file": rel}
    if passed is not None:
        include["inputs"] = passed
    ci: dict = {}
    if stages is not None:
        ci["stages"] = list(stages)
    ci["include"] = [include]
    (root / ".gitlab-ci.yml").write_text(yaml.safe_dump(ci, sort_keys=False))
    return root


def _check(root: Path, lib: Path, capsys) -> str:
    """-> the gate's printed problem lines (empty when it found nothing)."""
    failures = validate_render.check_include_contract(root, lib)
    out = capsys.readouterr().out
    problems = "\n".join(
        line for line in out.splitlines() if not line.strip().startswith("include contract")
    )
    assert bool(failures) is bool(problems.strip()), "a failure must print its reason"
    return problems


HEADERED = """\
    ---
    spec:
      inputs:
        stage:
          default: lint
    ---
    thing:
      stage: $[[ inputs.stage ]]
      script:
        - echo hi
    """

# The same job with NO `spec:` header — legal for a template that takes no
# inputs, and the shape that makes docs[0] a job document rather than a header.
HEADERLESS = """\
    ---
    thing:
      stage: {stage}
      script:
        - echo hi
    """


def test_a_declared_stage_passes(tmp_path, capsys) -> None:
    lib = _library(tmp_path, HEADERED)
    assert _check(_pipeline(tmp_path), lib, capsys) == ""


def test_an_undeclared_stage_is_reported(tmp_path, capsys) -> None:
    lib = _library(tmp_path, HEADERED)
    problems = _check(_pipeline(tmp_path, passed={"stage": "verify"}), lib, capsys)
    assert "resolves to stage 'verify'" in problems


def test_an_input_the_template_does_not_declare_is_reported(tmp_path, capsys) -> None:
    lib = _library(tmp_path, HEADERED)
    problems = _check(_pipeline(tmp_path, passed={"tags": []}), lib, capsys)
    assert "declares no input 'tags'" in problems


def test_a_default_less_input_must_be_passed(tmp_path, capsys) -> None:
    """GitLab treats a default-less input as REQUIRED — omitting it fails
    pipeline creation, so the gate must say so rather than pass."""
    lib = _library(
        tmp_path,
        """\
        ---
        spec:
          inputs:
            image:
              description: no default, therefore required
        ---
        thing:
          stage: lint
          script:
            - echo hi
        """,
    )
    problems = _check(_pipeline(tmp_path), lib, capsys)
    assert "requires input 'image', but none is passed" in problems


def test_a_headerless_template_still_has_its_jobs_inspected(tmp_path, capsys) -> None:
    """The blind spot: with no `spec:` header the jobs live in the FIRST
    document, so a gate that always skips docs[0] inspects nothing at all and
    reports ok on a pipeline GitLab rejects."""
    lib = _library(tmp_path, HEADERLESS.format(stage="verify"))
    problems = _check(_pipeline(tmp_path), lib, capsys)
    assert "resolves to stage 'verify'" in problems


def test_a_headerless_template_with_a_declared_stage_passes(tmp_path, capsys) -> None:
    """The other direction — the fix must not report every headerless job."""
    lib = _library(tmp_path, HEADERLESS.format(stage="lint"))
    assert _check(_pipeline(tmp_path), lib, capsys) == ""


STAGELESS = """\
    ---
    thing:
      script:
        - echo hi
    """


def test_a_job_with_no_stage_resolves_to_test(tmp_path, capsys) -> None:
    """GitLab puts a stage-less job in `test`. A pipeline with custom stages
    usually has no `test`, so reading the absent key as "unknown" hides exactly
    the failure this arm exists for."""
    lib = _library(tmp_path, STAGELESS)
    problems = _check(_pipeline(tmp_path), lib, capsys)
    assert "resolves to stage 'test'" in problems


@pytest.mark.parametrize(
    "stages", [("lint", "test"), None], ids=["declared-test", "implicit-defaults"]
)
def test_a_stageless_job_passes_where_test_exists(tmp_path, capsys, stages) -> None:
    lib = _library(tmp_path, STAGELESS)
    assert _check(_pipeline(tmp_path, stages=stages), lib, capsys) == ""


def test_a_job_that_extends_is_out_of_scope(tmp_path, capsys) -> None:
    """Its stage is inherited from a job this gate does not follow, so guessing
    `test` would invent a failure. Documented limitation, pinned so a later
    edit has to choose it deliberately."""
    lib = _library(
        tmp_path,
        """\
        ---
        .base:
          stage: lint
        ---
        thing:
          extends: .base
          script:
            - echo hi
        """,
    )
    assert _check(_pipeline(tmp_path), lib, capsys) == ""


def test_a_missing_template_is_reported(tmp_path, capsys) -> None:
    lib = _library(tmp_path, HEADERED)
    problems = _check(_pipeline(tmp_path, rel="/ci/lint/absent.yml"), lib, capsys)
    assert "not in the library checkout" in problems


def test_a_repo_without_a_pipeline_is_not_a_failure(tmp_path, capsys) -> None:
    """The `ci_shape: github` and `none` renders ship no .gitlab-ci.yml."""
    lib = _library(tmp_path, HEADERED)
    root = tmp_path / "no-pipeline"
    root.mkdir()
    assert validate_render.check_include_contract(root, lib) == []
