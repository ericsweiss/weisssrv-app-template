#!/usr/bin/env python3
"""Render the template and run the real toolchain over the result.

Usage, the gates it runs, the git-init step and the exit codes are in
docs/ARCHITECTURE.md, Running the real toolchain.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import render_app

CATALOG_TEMPLATE = (
    "https://raw.githubusercontent.com/datreeio/CRDs-catalog/{ref}/"
    "{{{{.Group}}}}/{{{{.ResourceKind}}}}_{{{{.ResourceAPIVersion}}}}.json"
)
REQUIRED_TOOLS = ("yamllint", "kustomize", "kubeconform", "ruff", "git")

# This repository's vendored-copy manifest, read by the library's engine.
VENDORED_MANIFEST = "scripts/vendored-manifest.yml"

# Width of the gate-name column every verdict line is printed in.
LABEL_WIDTH = 24


class Runner:
    """Runs each gate, prints a one-line verdict, and remembers the failures."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.failures: list[str] = []

    def gate(self, name: str, *command: str, stdin: str | None = None) -> str:
        result = subprocess.run(
            command, cwd=self.root, input=stdin, capture_output=True, text=True
        )
        status = "ok" if result.returncode == 0 else "FAILED"
        print(f"  {name:<{LABEL_WIDTH}} {status}")
        if result.returncode != 0:
            self.failures.append(name)
            sys.stdout.write(result.stdout)
            sys.stderr.write(result.stderr)
        return result.stdout


_SKIPPED = re.compile(r"Skipped:\s*(\d+)")
_FOUND = re.compile(r"(\d+) resource(?:s)? found")


def _kubeconform_skips(summary: str, label: str = "kubeconform skips") -> list[str]:
    """-> a failure when the run validated nothing, or skipped a resource.

    Most interesting kinds here are CRs, so a moved catalog path would leave the gate
    validating the built-in kinds alone and still printing ok.
    """
    match = _SKIPPED.search(summary)
    if not match:
        print(f"  {label:<{LABEL_WIDTH}} FAILED")
        print("    kubeconform printed no summary, so the skip count is unknown")
        return [label]
    found = _FOUND.search(summary)
    if found and not int(found.group(1)):
        print(f"  {label:<{LABEL_WIDTH}} FAILED")
        print("    kubeconform validated nothing: the input carried no resources")
        return [label]
    skipped = int(match.group(1))
    print(f"  {label:<{LABEL_WIDTH}} {'ok' if not skipped else 'FAILED'}")
    if skipped:
        print(f"    {skipped} resource(s) had no schema and were not validated")
        return [label]
    return []


def _kubeconform(
    runner: Runner, label: str, skips_label: str, catalog: str, k8s_version, stdin: str
) -> None:
    """Run kubeconform over `stdin` and add the skip failures it reports."""
    summary = runner.gate(
        label,
        "kubeconform",
        "-strict",
        "-ignore-missing-schemas",
        "-kubernetes-version",
        str(k8s_version),
        "-schema-location",
        "default",
        "-schema-location",
        catalog,
        "-summary",
        stdin=stdin,
    )
    if label not in runner.failures:
        runner.failures += _kubeconform_skips(summary, skips_label)


def _onboarding_wiring(root: Path) -> str:
    """-> the first ```yaml fence of the rendered ONBOARDING, the manifests the
    operator applies by hand. Empty when the page carries none."""
    page = root / "docs" / "ONBOARDING.md"
    if not page.is_file():
        return ""
    parts = page.read_text(encoding="utf-8").split("```yaml")
    return parts[1].split("```")[0] if len(parts) > 1 else ""


def _make_git_tree(root: Path) -> None:
    """Track every rendered file, so scripts/check-doc-links.py scans them all."""
    for command in (("git", "init", "-q"), ("git", "add", "-A")):
        subprocess.run(command, cwd=root, check=True, capture_output=True)


def _catalog(root: Path) -> str:
    """The CRD-catalog URL the rendered Taskfile pins, so this gate and the
    tenant's own `task flux-lint` resolve the same schemas."""
    taskfile = yaml.safe_load((root / "Taskfile.yml").read_text()) or {}
    ref = (taskfile.get("vars") or {}).get("CRD_CATALOG_REF")
    if not ref:
        raise SystemExit("Taskfile.yml sets no CRD_CATALOG_REF: the schema source is unpinned")
    return CATALOG_TEMPLATE.format(ref=ref)


def validate(root: Path, answers: dict) -> list[str]:
    _make_git_tree(root)
    catalog = _catalog(root)
    runner = Runner(root)
    runner.gate("yamllint", "yamllint", "--strict", "-c", ".yamllint", ".")

    built = runner.gate("kustomize build", "kustomize", "build", "kubernetes/flux")
    if "kustomize build" not in runner.failures:
        _kubeconform(
            runner, "kubeconform", "kubeconform skips", catalog, answers["k8s_version"], built
        )

    wiring = _onboarding_wiring(root)
    if wiring:
        _kubeconform(
            runner,
            "onboarding wiring",
            "onboarding skips",
            catalog,
            answers["k8s_version"],
            wiring,
        )
    else:
        print(f"  {'onboarding wiring':<{LABEL_WIDTH}} FAILED")
        print("    docs/ONBOARDING.md carries no YAML wiring block")
        runner.failures.append("onboarding wiring")

    runner.gate("ruff", "ruff", "check", "--no-cache", "--output-format", "concise", "scripts")
    runner.gate("doc links", sys.executable, "scripts/check-doc-links.py")
    runner.gate(
        "netpol except", sys.executable, "scripts/check-netpol-except-parity.py", "kubernetes/flux"
    )
    runner.gate(
        "scrape wiring", sys.executable, "scripts/check-scrape-wiring.py", "kubernetes/flux"
    )
    runner.gate(
        "kustomization", sys.executable, "scripts/check-kustomization.py", "kubernetes/flux"
    )

    if (root / ".gitlab-ci.yml").is_file():
        runner.gate(
            "library pins",
            sys.executable,
            "scripts/check-lib-pins.py",
            "--project",
            str(answers["lib_project"]),
        )
    return runner.failures


def check_registered_copies(
    lib_path: Path,
    expected_ref: str | None = None,
    allow_ref_mismatch: bool = False,
    checkout: tuple[bool, list[str]] | None = None,
) -> list[str]:
    """Run the library's comparison engine over this repository's copy manifest.

    Byte-identity is a claim about `expected_ref`, so an unverified checkout is
    reported as unverified, never as ok.
    """
    label = "registered copies"
    checker = lib_path / "scripts" / "check-vendored-copies.py"
    if not checker.is_file():
        return [
            f"{lib_path} ships no scripts/check-vendored-copies.py — the vendored-copy "
            "gate cannot run, and it must not silently skip"
        ]
    manifest = render_app.REPO_ROOT / VENDORED_MANIFEST
    if not manifest.is_file():
        return [
            f"{manifest} is missing — this repository's vendored copies would go "
            "ungated, and the gate must not silently skip"
        ]
    if not expected_ref:
        print(f"  {label:<{LABEL_WIDTH}} FAILED")
        print("    this repository pins no library ref, so byte-identity has no subject")
        return [label]
    ok, failures = checkout or _report_checkout(
        label, lib_path, expected_ref, allow_ref_mismatch
    )
    if failures:
        return [label]
    if not ok:
        print(f"  {label:<{LABEL_WIDTH}} SKIPPED (ref unverified)")
        return []
    result = subprocess.run(
        [
            sys.executable,
            str(checker),
            "--manifest",
            str(manifest),
            "--repo-root",
            str(render_app.REPO_ROOT),
            "--lib-path",
            str(lib_path),
        ],
        capture_output=True,
        text=True,
    )
    status = "ok" if result.returncode == 0 else "FAILED"
    print(f"  {label:<{LABEL_WIDTH}} {status}")
    if result.returncode:
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        return [label]
    return []


_INPUT_REF = re.compile(r"^\$\[\[\s*inputs\.([A-Za-z0-9_-]+)\s*\]\]$")

# Keys a CI template's job document may hold that are not jobs.
_NOT_A_JOB = {
    "include", "variables", "stages", "workflow", "default", "image", "spec",
    # Legal global-level keywords; without them a mapping here reads as a
    # stage-less job and lands in the implicit-test arm.
    "cache", "services", "before_script", "after_script", "pages",
}

# GitLab's own default for a job that declares no `stage:`. Resolving one to
# nothing instead would hide it from the stage arm, and `test` is the stage a
# pipeline with custom `stages:` is most likely not to declare.
_IMPLICIT_STAGE = "test"


def _resolve(value, inputs: dict, passed: dict):
    """Resolve `$[[ inputs.x ]]` against what the consumer passed, else the default."""
    match = _INPUT_REF.match(value) if isinstance(value, str) else None
    if not match:
        return value
    name = match.group(1)
    if name in passed:
        return passed[name]
    return (inputs.get(name) or {}).get("default")


class GitUnavailable(Exception):
    """git could not be asked what the library checkout is at."""


def _checkout_ref(lib_path: Path) -> list[str]:
    """-> the tags the library checkout's HEAD is at, in no particular order.
    A release commit may carry more than one."""
    try:
        result = subprocess.run(
            ["git", "-C", str(lib_path), "tag", "--points-at", "HEAD"],
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise GitUnavailable(str(error)) from error
    if result.returncode:
        raise GitUnavailable(result.stderr.strip() or f"git exited {result.returncode}")
    return result.stdout.split()


def _checkout_dirty(lib_path: Path) -> list[str]:
    """-> up to three tracked paths modified in the library checkout, so the gate is not
    reading unreleased files. A git failure is not "the checkout is clean": the gate has
    verified nothing and must say so."""
    try:
        result = subprocess.run(
            ["git", "-C", str(lib_path), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
        )
    except OSError as error:
        raise GitUnavailable(str(error)) from error
    if result.returncode:
        raise GitUnavailable(result.stderr.strip() or f"git exited {result.returncode}")
    return [line[3:] for line in result.stdout.splitlines()][:3]


def _report_checkout(
    label: str, lib_path: Path, expected_ref: str, allow_ref_mismatch: bool
) -> tuple[bool, list[str]]:
    """-> (the checkout is verifiably at `expected_ref` and clean, gate failures).

    Every gate reading library files has the same subject, so an unverified checkout is
    either a failure or, waived, a warning the caller must not print as a verdict.
    """
    try:
        actual = _checkout_ref(lib_path)
        reason = (
            f"library checkout {lib_path} is at {actual or None!r}, but the pinned ref is "
            f"{expected_ref!r} — library files would be read from the wrong templates"
            if expected_ref not in actual
            else ""
        )
        if not reason and (dirty := _checkout_dirty(lib_path)):
            reason = (
                f"library checkout {lib_path} is at {expected_ref} but has uncommitted "
                f"changes to {', '.join(dirty)} — library files would be read unreleased"
            )
    except GitUnavailable as error:
        print(f"  {label:<{LABEL_WIDTH}} FAILED")
        print(f"    cannot read {lib_path}'s git state, so the pinned ref is unverified: {error}")
        return False, [label]
    if not reason:
        return True, []
    if not allow_ref_mismatch:
        print(f"  {label:<{LABEL_WIDTH}} FAILED")
        print(f"    {reason}")
        return False, [label]
    print(f"  warning: {reason}")
    return False, []


def check_include_contract(
    root: Path,
    lib_path: Path,
    expected_ref: str | None = None,
    allow_ref_mismatch: bool = False,
    label: str = "include contract",
    checkout: tuple[bool, list[str]] | None = None,
) -> list[str]:
    """Cross-check the generated pipeline against the library templates it pins.

    Catches an undeclared `inputs:` key and a resolved stage missing from
    `stages:`. The checkout must be at `expected_ref`, the tag the pipeline pins.
    """
    pipeline = root / ".gitlab-ci.yml"
    if not pipeline.is_file():
        return []
    if expected_ref:
        ok, failures = checkout or _report_checkout(
            label, lib_path, expected_ref, allow_ref_mismatch
        )
        if failures:
            return [label]
        if not ok:
            print(f"  {label:<{LABEL_WIDTH}} SKIPPED (ref unverified)")
            return []
    ci = render_app.load_ci(pipeline)
    # No stages: key means GitLab's implicit defaults; .pre/.post always exist.
    declared = ci.get("stages")
    stages = set(declared if declared is not None else ("build", "test", "deploy"))
    stages.update({".pre", ".post"})
    problems: list[str] = []
    includes = ci.get("include") or []
    # GitLab accepts `include:` as a single mapping as well as a list.
    if isinstance(includes, dict):
        includes = [includes]
    for include in includes:
        if not isinstance(include, dict) or "project" not in include:
            continue
        rel = str(include["file"]).lstrip("/")
        source = lib_path / rel
        if not source.is_file():
            problems.append(f"{rel} is not in the library checkout")
            continue
        docs = [d for d in yaml.load_all(source.read_text(), Loader=render_app.CILoader) if d]
        # The `spec:` header is optional: a template with no inputs is legal
        # and its first document already holds jobs. Reading docs[0] as a header
        # unconditionally would drop every job in such a template.
        header = docs[0] if docs and isinstance(docs[0], dict) and "spec" in docs[0] else None
        inputs = ((header or {}).get("spec") or {}).get("inputs") or {}
        job_docs = docs[1:] if header is not None else docs
        passed = include.get("inputs") or {}
        for key in passed:
            if key not in inputs:
                problems.append(f"{rel} declares no input {key!r}")
        # GitLab treats a default-less input as REQUIRED: an omitted one fails
        # pipeline creation, which this gate must surface rather than pass.
        for key, declaration in inputs.items():
            if key not in passed and "default" not in (declaration or {}):
                problems.append(f"{rel} requires input {key!r}, but none is passed")
        for doc in job_docs:
            for name, body in doc.items():
                if name in _NOT_A_JOB or name.startswith(".") or not isinstance(body, dict):
                    continue
                job = _resolve(name, inputs, passed)
                if "stage" in body:
                    stage = _resolve(body["stage"], inputs, passed)
                elif "extends" in body:
                    # The stage comes from the extended job, possibly in
                    # another file. Resolving that chain is out of scope, so the
                    # job is left unchecked.
                    continue
                else:
                    stage = _IMPLICIT_STAGE
                if stage is not None and stage not in stages:
                    problems.append(
                        f"{rel}: job {job!r} resolves to stage {stage!r}, "
                        f"which the pipeline does not declare"
                    )
    status = "ok" if not problems else "FAILED"
    print(f"  {label:<{LABEL_WIDTH}} {status}")
    for problem in problems:
        print(f"    {problem}")
    return [label] if problems else []


def _own_lib_ref() -> str | None:
    """The library ref THIS repository pins, which is the ref its vendored
    copies are byte-identical to."""
    own = render_app.REPO_ROOT / ".gitlab-ci.yml"
    if not own.is_file():
        return None
    return (render_app.load_ci(own).get("variables") or {}).get("WEISSSRV_LIB_REF")


def check_own_include_contract(
    lib_path: Path,
    allow_ref_mismatch: bool = False,
    checkout: tuple[bool, list[str]] | None = None,
) -> list[str]:
    """Put the contract gate over THIS repository's own pipeline.

    The library serves it too, so a default-less input added upstream would
    otherwise first appear as a pipeline-creation failure on the ref bump.
    """
    own = render_app.REPO_ROOT / ".gitlab-ci.yml"
    label = "include contract (self)"
    if not own.is_file():
        return []
    ref = _own_lib_ref()
    if not ref:
        print(f"  {label:<{LABEL_WIDTH}} FAILED")
        print("    variables.WEISSSRV_LIB_REF is unset, so the pinned ref is unverified")
        return [label]
    return check_include_contract(
        render_app.REPO_ROOT, lib_path, ref, allow_ref_mismatch, label=label, checkout=checkout
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--answers", type=Path, default=render_app.ANSWERS)
    parser.add_argument(
        "--data",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Override one answer from the fixture; repeatable.",
    )
    parser.add_argument("--keep", type=Path, help="Copy the render here before cleaning up.")
    parser.add_argument(
        "--lib-path",
        type=Path,
        help="weisssrv-lib checkout AT the pinned ref — enables the vendored-copy "
        "and include-contract gates.",
    )
    parser.add_argument(
        "--allow-ref-mismatch",
        action="store_true",
        help="Warn instead of failing when --lib-path is not at the pinned ref. The "
        "vendored-copy gate then reports the ref unverified rather than a verdict.",
    )
    args = parser.parse_args()

    missing = [tool for tool in REQUIRED_TOOLS if not shutil.which(tool)]
    if missing:
        print(f"error: not on PATH: {', '.join(missing)}", file=sys.stderr)
        return 2

    # copier is a module, not a PATH binary, so the check above cannot see it.
    if subprocess.run(
        [sys.executable, "-m", "copier", "--version"], capture_output=True, check=False
    ).returncode:
        print("error: copier is not installed", file=sys.stderr)
        return 2

    answers = yaml.safe_load(args.answers.read_text())
    overrides = {}
    for pair in args.data:
        key, _, value = pair.partition("=")
        if not _:
            print(f"error: --data takes KEY=VALUE, got {pair!r}", file=sys.stderr)
            return 2
        overrides[key] = value
    # The gates below read the answers to decide what to validate against, so
    # they must see the same values the render did.
    answers.update(overrides)

    label = " ".join([args.answers.name, *args.data])
    scratch = Path(tempfile.mkdtemp(prefix="app-template-validate-"))
    try:
        print(f"rendering {label}")
        root = render_app.render(scratch, answers=args.answers, data=overrides)
        failures = validate(root, answers)
        if args.lib_path:
            # One verdict per distinct ref: three gates ask the same question of
            # the same checkout, and each would re-probe git and reprint it.
            own_ref, render_ref = _own_lib_ref(), answers.get("lib_ref")
            verdicts = {
                ref: _report_checkout(
                    "library checkout", args.lib_path, ref, args.allow_ref_mismatch
                )
                for ref in dict.fromkeys(ref for ref in (own_ref, render_ref) if ref)
            }
            failures += check_registered_copies(
                args.lib_path,
                own_ref,
                args.allow_ref_mismatch,
                checkout=verdicts.get(own_ref),
            )
            if (root / ".gitlab-ci.yml").is_file() and not render_ref:
                print(f"  {'include contract':<{LABEL_WIDTH}} FAILED")
                print("    lib_ref is unset in the answers, so the pinned ref is unverified")
                failures.append("include contract")
            else:
                failures += check_include_contract(
                    root,
                    args.lib_path,
                    render_ref,
                    args.allow_ref_mismatch,
                    checkout=verdicts.get(render_ref),
                )
            failures += check_own_include_contract(
                args.lib_path, args.allow_ref_mismatch, checkout=verdicts.get(own_ref)
            )
        if args.keep:
            shutil.copytree(root, args.keep, dirs_exist_ok=True)
    finally:
        if not os.environ.get("WEISSSRV_KEEP_SCRATCH"):
            shutil.rmtree(scratch, ignore_errors=True)

    if failures:
        print(f"\n{label}: {len(failures)} gate(s) failed: {', '.join(failures)}")
        return 1
    print(f"\n{label}: every gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
