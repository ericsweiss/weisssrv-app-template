"""Tests for the gates in tests/validate_render.py that have no binary to run.

Each arm is proved against a fixture pipeline and a fixture library; a real
library checkout passes and so proves nothing about failure.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml
from conftest import require_env

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


def _stub_git(monkeypatch, tags: list[str], dirty: list[str] | None = None) -> None:
    """Both git reads are stubbed: the fixture library is not a git checkout."""
    monkeypatch.setattr(validate_render, "_checkout_ref", lambda path: tags)
    monkeypatch.setattr(validate_render, "_checkout_dirty", lambda path: dirty or [])


def test_a_checkout_at_the_pinned_tag_is_accepted(tmp_path, capsys, monkeypatch) -> None:
    lib = _library(tmp_path, HEADERED)
    _stub_git(monkeypatch, [LIB_REF])
    assert validate_render.check_include_contract(_pipeline(tmp_path), lib, LIB_REF) == []


def test_a_dirty_checkout_is_reported(tmp_path, capsys, monkeypatch) -> None:
    """Uncommitted library changes mean the contract was checked against files
    no release carries."""
    lib = _library(tmp_path, HEADERED)
    _stub_git(monkeypatch, [LIB_REF], ["ci/lint/thing.yml"])
    failures = validate_render.check_include_contract(_pipeline(tmp_path), lib, LIB_REF)
    assert failures == ["include contract"]
    assert "uncommitted" in capsys.readouterr().out


def test_a_checkout_at_another_tag_is_reported(tmp_path, capsys, monkeypatch) -> None:
    lib = _library(tmp_path, HEADERED)
    monkeypatch.setattr(validate_render, "_checkout_ref", lambda path: ["v9.9.9"])
    failures = validate_render.check_include_contract(_pipeline(tmp_path), lib, LIB_REF)
    assert failures == ["include contract"]
    assert "the wrong templates" in capsys.readouterr().out


def test_a_ref_mismatch_can_be_waived(tmp_path, capsys, monkeypatch) -> None:
    """`--allow-ref-mismatch` is for validating against an unreleased library."""
    lib = _library(tmp_path, HEADERED)
    _stub_git(monkeypatch, [])
    failures = validate_render.check_include_contract(
        _pipeline(tmp_path), lib, LIB_REF, allow_ref_mismatch=True
    )
    out = capsys.readouterr().out
    assert failures == []
    assert "warning:" in out
    assert "SKIPPED (ref unverified)" in out
    assert f"{'include contract':<{validate_render.LABEL_WIDTH}} ok" not in out


def test_a_library_without_the_engine_is_reported(tmp_path) -> None:
    problems = validate_render.check_registered_copies(tmp_path / "lib")
    assert problems and "check-vendored-copies.py" in problems[0]


def test_a_missing_manifest_is_reported(tmp_path, monkeypatch) -> None:
    """A manifest that is not there must fail loudly, not gate nothing."""
    checker = tmp_path / "lib" / "scripts" / "check-vendored-copies.py"
    checker.parent.mkdir(parents=True)
    checker.write_text("")
    monkeypatch.setattr(validate_render.render_app, "REPO_ROOT", tmp_path / "empty")
    problems = validate_render.check_registered_copies(tmp_path / "lib")
    assert problems and "vendored-manifest.yml is missing" in problems[0]


def _copies_engine(tmp_path: Path) -> Path:
    """A library checkout whose comparison engine passes, so the verdict under
    test comes from the ref check rather than the engine."""
    checker = tmp_path / "lib" / "scripts" / "check-vendored-copies.py"
    checker.parent.mkdir(parents=True)
    checker.write_text("raise SystemExit(0)\n")
    return tmp_path / "lib"


def _drifting_copies_engine(tmp_path: Path) -> Path:
    """A library checkout whose comparison engine reports drift, so the verdict
    under test comes from the engine rather than the ref check."""
    checker = tmp_path / "lib" / "scripts" / "check-vendored-copies.py"
    checker.parent.mkdir(parents=True)
    checker.write_text(
        'import sys\nprint("drift: scripts/check-doc-links.py", file=sys.stderr)\n'
        "raise SystemExit(1)\n"
    )
    return tmp_path / "lib"


def test_the_copies_gate_reports_drift_the_engine_found(tmp_path, capsys, monkeypatch) -> None:
    """A local edit to a byte-identical library copy must red the gate, and the
    engine's own diff must reach the log."""
    _stub_git(monkeypatch, [LIB_REF])
    failures = validate_render.check_registered_copies(_drifting_copies_engine(tmp_path), LIB_REF)
    captured = capsys.readouterr()
    assert failures == ["registered copies"]
    assert f"{'registered copies':<{validate_render.LABEL_WIDTH}} FAILED" in captured.out
    assert "drift: scripts/check-doc-links.py" in captured.err


def test_the_copies_gate_passes_when_the_engine_finds_nothing(
    tmp_path, capsys, monkeypatch
) -> None:
    """The other direction: an engine that exits 0 on a verified checkout is the
    only shape that may print `ok`."""
    _stub_git(monkeypatch, [LIB_REF])
    assert validate_render.check_registered_copies(_copies_engine(tmp_path), LIB_REF) == []
    assert f"{'registered copies':<{validate_render.LABEL_WIDTH}} ok" in capsys.readouterr().out


def test_the_copies_gate_fails_on_a_checkout_at_another_tag(tmp_path, capsys, monkeypatch) -> None:
    """Byte-identity is a claim about the pinned ref: computed against another
    tree it is not that claim, so the row must not read `ok`."""
    lib = _copies_engine(tmp_path)
    monkeypatch.setattr(validate_render, "_checkout_ref", lambda path: ["v9.9.9"])
    failures = validate_render.check_registered_copies(lib, LIB_REF)
    out = capsys.readouterr().out
    assert failures == ["registered copies"]
    assert "the wrong templates" in out
    assert "registered copies      ok" not in out


def test_a_copies_ref_mismatch_is_reported_unverified_when_waived(
    tmp_path, capsys, monkeypatch
) -> None:
    """`--allow-ref-mismatch` is the path docs/ARCHITECTURE.md sends people
    down, so it must not print an authoritative-looking verdict."""
    lib = _copies_engine(tmp_path)
    monkeypatch.setattr(validate_render, "_checkout_ref", lambda path: ["v9.9.9"])
    failures = validate_render.check_registered_copies(lib, LIB_REF, allow_ref_mismatch=True)
    out = capsys.readouterr().out
    assert failures == []
    assert "warning:" in out
    assert "SKIPPED (ref unverified)" in out
    assert "registered copies      ok" not in out


def test_a_copies_gate_with_no_pinned_ref_is_reported(tmp_path, capsys) -> None:
    """No pinned ref means byte-identity has no subject, and the gate must say
    so rather than compare against whatever is checked out."""
    failures = validate_render.check_registered_copies(_copies_engine(tmp_path))
    assert failures == ["registered copies"]
    assert "pins no library ref" in capsys.readouterr().out


def test_a_commit_carrying_several_tags_is_accepted(tmp_path, capsys) -> None:
    """The only test that runs the real `git tag --points-at`. A release commit
    may carry more than one tag, and git orders them by refname, so comparing
    against a single one reds a checkout that IS at the pinned ref."""
    lib = _library(tmp_path, HEADERED)
    for command in (
        ["git", "init", "-q"],
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
         "--allow-empty", "-m", "release"],
        ["git", "tag", "alpha-alias"],
        ["git", "tag", LIB_REF],
    ):
        subprocess.run(command, cwd=lib, check=True, capture_output=True)
    assert validate_render._checkout_ref(lib) == ["alpha-alias", LIB_REF]
    assert validate_render.check_include_contract(_pipeline(tmp_path), lib, LIB_REF) == []


def test_a_library_git_cannot_read_is_reported(tmp_path, capsys) -> None:
    """A git failure is not 'the checkout is at no tag': the gate has verified
    nothing and must say so."""
    lib = _library(tmp_path, HEADERED)
    failures = validate_render.check_include_contract(_pipeline(tmp_path), lib, LIB_REF)
    assert failures == ["include contract"]
    assert "cannot read" in capsys.readouterr().out


def test_a_library_working_tree_git_cannot_read_is_reported(tmp_path, capsys, monkeypatch) -> None:
    """`git status` failing is not 'the checkout is clean': the gate has
    verified nothing about whether it is reading released files."""
    lib = _library(tmp_path, HEADERED)
    monkeypatch.setattr(validate_render, "_checkout_ref", lambda path: [LIB_REF])

    def _explode(path):
        raise validate_render.GitUnavailable("git exited 128")

    monkeypatch.setattr(validate_render, "_checkout_dirty", _explode)
    failures = validate_render.check_include_contract(_pipeline(tmp_path), lib, LIB_REF)
    assert failures == ["include contract"]
    assert "cannot read" in capsys.readouterr().out


def test_a_pipeline_with_no_lib_ref_variable_fails_the_self_check(
    tmp_path, capsys, monkeypatch
) -> None:
    """With WEISSSRV_LIB_REF gone the ref is None, and the contract gate would
    otherwise validate against whatever the checkout happens to be at."""
    root = tmp_path / "self"
    root.mkdir()
    (root / ".gitlab-ci.yml").write_text(yaml.safe_dump({"stages": ["lint"], "include": []}))
    monkeypatch.setattr(validate_render.render_app, "REPO_ROOT", root)
    assert validate_render.check_own_include_contract(tmp_path / "lib") == [
        "include contract (self)"
    ]
    assert "WEISSSRV_LIB_REF is unset" in capsys.readouterr().out


def test_a_repo_with_no_pipeline_of_its_own_is_not_a_failure(tmp_path, monkeypatch) -> None:
    """The self-check reads the file, so the guard has to come first or the gate
    dies with a traceback instead of taking the graceful arm."""
    root = tmp_path / "self"
    root.mkdir()
    monkeypatch.setattr(validate_render.render_app, "REPO_ROOT", root)
    assert validate_render.check_own_include_contract(tmp_path / "lib") == []


def test_a_taskfile_with_no_catalog_ref_is_rejected(tmp_path) -> None:
    """An unpinned catalog silently changes what has a schema."""
    (tmp_path / "Taskfile.yml").write_text("version: '3'\nvars: {}\n")
    with pytest.raises(SystemExit):
        validate_render._catalog(tmp_path)


def test_the_catalog_url_embeds_the_pinned_ref(tmp_path) -> None:
    (tmp_path / "Taskfile.yml").write_text("version: '3'\nvars:\n  CRD_CATALOG_REF: abc123\n")
    assert "abc123" in validate_render._catalog(tmp_path)


def test_onboarding_wiring_is_empty_without_a_fence(tmp_path) -> None:
    assert validate_render._onboarding_wiring(tmp_path) == ""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "ONBOARDING.md").write_text("# Onboarding\n\nNo fence here.\n")
    assert validate_render._onboarding_wiring(tmp_path) == ""


def test_onboarding_wiring_returns_the_first_fence(tmp_path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "ONBOARDING.md").write_text(
        "```yaml\nkind: GitRepository\n```\n\n```yaml\nkind: Other\n```\n"
    )
    assert validate_render._onboarding_wiring(tmp_path).strip() == "kind: GitRepository"


def test_this_repos_pipeline_satisfies_the_include_contract() -> None:
    """The gate reads the RENDERED pipeline everywhere else, and this repo pins
    the same library from its own .gitlab-ci.yml — a default-less input added
    upstream fails it at pipeline creation with nothing else here watching."""
    lib = require_env(
        "WEISSSRV_LIB_PATH",
        "own include-contract",
        "The python-tests job's setup_command clones the library into it.",
    )
    assert Path(lib).is_dir(), (
        f"WEISSSRV_LIB_PATH={lib} is not a directory — the contract gate verified nothing"
    )
    assert validate_render.check_own_include_contract(Path(lib)) == []


# A verbatim `kubeconform -summary` line, so a change to its wording fails here
# rather than degrading the gate to a vacuous pass.
KUBECONFORM_SUMMARY = (
    "Summary: 1 resource found parsing stdin - Valid: 1, Invalid: 0, Errors: 0, Skipped: 0"
)


def test_no_skipped_resource_passes() -> None:
    assert validate_render._kubeconform_skips(KUBECONFORM_SUMMARY) == []


def test_an_empty_build_fails(capsys) -> None:
    """kubeconform exits 0 on empty input, so a build that rendered nothing
    would otherwise report a clean gate."""
    summary = "Summary: 0 resource found parsing stdin - Valid: 0, Invalid: 0, Errors: 0, Skipped: 0"
    assert validate_render._kubeconform_skips(summary) == ["kubeconform skips"]
    assert "no resources" in capsys.readouterr().out


def test_a_skipped_resource_fails_and_names_the_count(capsys) -> None:
    """A skip means a CR validated against no schema at all, which is most of
    what a generated repo ships."""
    summary = KUBECONFORM_SUMMARY.replace("Skipped: 0", "Skipped: 3")
    assert validate_render._kubeconform_skips(summary) == ["kubeconform skips"]
    assert "3 resource(s)" in capsys.readouterr().out


@pytest.mark.parametrize("summary", ["", "kubeconform: could not parse stdin"])
def test_a_missing_summary_fails(summary, capsys) -> None:
    """No summary is not zero skips: the gate has counted nothing."""
    assert validate_render._kubeconform_skips(summary) == ["kubeconform skips"]
    assert "skip count is unknown" in capsys.readouterr().out


# --------------------------------------------------------------------------
# The harness: Runner.gate's accounting and main()'s exit code
# --------------------------------------------------------------------------


def test_a_failing_gate_is_recorded_and_printed(tmp_path, capsys) -> None:
    """Every gate is accounted through Runner.gate, and main() turns the tally
    into the exit code: a lost failure greens a broken render."""
    runner = validate_render.Runner(tmp_path)
    runner.gate("probe", sys.executable, "-c", "raise SystemExit(1)")
    assert runner.failures == ["probe"]
    assert f"{'probe':<{validate_render.LABEL_WIDTH}} FAILED" in capsys.readouterr().out


def test_a_passing_gate_is_not_recorded(tmp_path, capsys) -> None:
    runner = validate_render.Runner(tmp_path)
    runner.gate("probe", sys.executable, "-c", "pass")
    assert runner.failures == []
    assert f"{'probe':<{validate_render.LABEL_WIDTH}} ok" in capsys.readouterr().out


# The kubeconform summary the skip check reads: one resource validated, none
# skipped, so the stubbed run adds no failure of its own.
_SUMMARY = "1 resource found, Valid: 1, Invalid: 0, Errors: 0, Skipped: 0"


def test_validate_runs_every_gate(tmp_path, monkeypatch) -> None:
    """validate() is the only place a render meets the real toolchain, and
    nothing else asserts its gate list: a dropped gate must fail here."""
    labels: list[str] = []

    def record(self, name, *command, stdin=None):
        labels.append(name)
        return _SUMMARY

    monkeypatch.setattr(validate_render.Runner, "gate", record)
    monkeypatch.setattr(validate_render, "_make_git_tree", lambda root: None)
    monkeypatch.setattr(validate_render, "_catalog", lambda root: "catalog")
    monkeypatch.setattr(validate_render, "_onboarding_wiring", lambda root: "---\n")
    answers = {"k8s_version": "1.36.0", "lib_project": "x/y", "app_namespace": "ns"}
    validate_render.validate(tmp_path, answers)
    # tmp_path carries no .gitlab-ci.yml, so "library pins" is out of scope.
    assert set(labels) == {
        "yamllint",
        "kustomize build",
        "kubeconform",
        "onboarding wiring",
        "ruff",
        "doc links",
        "netpol except",
        "scrape wiring",
        "kustomization",
    }


def _stub_main(monkeypatch, tmp_path: Path, failures: list[str]) -> None:
    """Run main() over a stubbed render, with the PATH check out of the way."""
    monkeypatch.setattr(sys, "argv", ["validate_render.py"])
    monkeypatch.setattr(validate_render, "REQUIRED_TOOLS", ())
    monkeypatch.setattr(
        validate_render.render_app, "render", lambda scratch, **kwargs: tmp_path
    )
    monkeypatch.setattr(validate_render, "validate", lambda root, answers: failures)


def test_main_fails_when_a_gate_failed(tmp_path, capsys, monkeypatch) -> None:
    _stub_main(monkeypatch, tmp_path, ["yamllint"])
    assert validate_render.main() == 1
    assert "1 gate(s) failed: yamllint" in capsys.readouterr().out


def test_main_passes_when_every_gate_passed(tmp_path, capsys, monkeypatch) -> None:
    _stub_main(monkeypatch, tmp_path, [])
    assert validate_render.main() == 0
    assert "every gate passed" in capsys.readouterr().out
