#!/usr/bin/env python3
"""Render the template and run the REAL toolchain over the result.

The pytest suite asserts structure with no external binaries, so it runs
anywhere. This runs what a generated repo's own pipeline runs — yamllint,
`kustomize build`, kubeconform, ruff, the doc-link checker and the library-pin
gate — against a render, which is the only way to find out that a repo this
template produces would fail its own gates.

  tests/validate_render.py                              # fixture A
  tests/validate_render.py --answers tests/answers-unlike.yml
  tests/validate_render.py --data ci_shape=none         # one answer overridden
  tests/validate_render.py --keep /tmp/render           # leave the tree behind
  tests/validate_render.py --lib-path /path/to/lib      # + the vendored-copy gate

ONE render per invocation. Both fixtures matter — the shaped one has the
reference cluster's shape, which makes it blind to a value hardcoded FROM that
cluster; the contrast one renders every optional component off and a different
CI shape. `--data` covers the branches neither fixture answers: `ci_shape=none`
is the shape that ships no pipeline, and `secrets_backend=none` (with
`enable_registry_pull_secret=false`, the other ExternalSecret) is the tree with
no secret surface at all.

Exit 0 when every gate passes, 1 on a failure, 2 when a required tool is
missing — a validator that quietly skips itself is not one.
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

import render_app

CATALOG = (
    "https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/"
    "{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json"
)
REQUIRED_TOOLS = ("yamllint", "kustomize", "kubeconform", "ruff")

# This repository's vendored-copy manifest, read by the library's engine.
VENDORED_MANIFEST = "scripts/vendored-manifest.yml"


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
        print(f"  {name:<22} {status}")
        if result.returncode != 0:
            self.failures.append(name)
            sys.stdout.write(result.stdout)
            sys.stderr.write(result.stderr)
        return result.stdout


def validate(root: Path, answers: dict) -> list[str]:
    runner = Runner(root)
    runner.gate("yamllint", "yamllint", "--strict", "-c", ".yamllint", ".")

    built = runner.gate("kustomize build", "kustomize", "build", "kubernetes/flux")
    if built:
        runner.gate(
            "kubeconform",
            "kubeconform",
            "-strict",
            "-ignore-missing-schemas",
            "-kubernetes-version",
            str(answers["k8s_version"]),
            "-schema-location",
            "default",
            "-schema-location",
            CATALOG,
            "-summary",
            stdin=built,
        )

    runner.gate("ruff", "ruff", "check", "--no-cache", "--output-format", "concise", "scripts")
    runner.gate("doc links", sys.executable, "scripts/check-doc-links.py")

    if (root / ".gitlab-ci.yml").is_file():
        runner.gate(
            "library pins",
            sys.executable,
            "scripts/check-lib-pins.py",
            "--project",
            str(answers["lib_project"]),
        )
    return runner.failures


def check_registered_copies(lib_path: Path) -> list[str]:
    """Run the library's comparison engine over THIS repository's manifest.

    The copies are here, not in the render: the workflows and scripts under
    `template/` are rendered into a tenant, and the ones at the root are what
    this repo runs on itself. Both are byte-identical to the library, and
    `scripts/vendored-manifest.yml` is where that relationship is written down —
    including the lint profiles this repo deliberately FORKS, where the drift is
    silent in the other direction (the library moves and the fork never absorbs
    it).

    The manifest is OWNED HERE, so moving a copy inside this repository is a
    local change rather than a library release event. What stays library-side is
    the engine and the offer list (`scripts/vendorable-paths.yml`): every `lib:`
    path in the manifest must be a path the library supports vendoring at the
    pinned ref.
    """
    checker = lib_path / "scripts" / "check-vendored-copies.py"
    if not checker.is_file():
        return [
            f"{lib_path} ships no scripts/check-vendored-copies.py — the vendored-copy "
            "gate cannot run, and it must not silently skip"
        ]
    result = subprocess.run(
        [
            sys.executable,
            str(checker),
            "--manifest",
            str(render_app.REPO_ROOT / VENDORED_MANIFEST),
            "--repo-root",
            str(render_app.REPO_ROOT),
            "--lib-path",
            str(lib_path),
        ],
        capture_output=True,
        text=True,
    )
    status = "ok" if result.returncode == 0 else "FAILED"
    print(f"  {'registered copies':<22} {status}")
    if result.returncode:
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        return ["registered copies"]
    return []


_INPUT_REF = re.compile(r"^\$\[\[\s*inputs\.([A-Za-z0-9_-]+)\s*\]\]$")

# Keys a CI template's job document may hold that are not jobs.
_NOT_A_JOB = {
    "include", "variables", "stages", "workflow", "default", "image", "spec",
    # Legal global-level keywords; without them a mapping here reads as a
    # stage-less job and lands in the implicit-test arm.
    "cache", "services", "before_script", "after_script", "pages",
}

# GitLab's own default for a job that declares no `stage:`. Resolving a
# stage-less job to nothing instead would make it invisible to the stage arm —
# and "test" is precisely the stage a pipeline with custom `stages:` is most
# likely NOT to declare, so it is the case worth catching.
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


def check_include_contract(root: Path, lib_path: Path) -> list[str]:
    """Cross-check the generated pipeline against the library templates it pins.

    Two failures GitLab only reports when a tenant pushes, both invisible to a
    render: an `inputs:` key the template does not declare ("unknown input"),
    and a job whose resolved stage is missing from the pipeline's `stages:`
    ("chosen stage does not exist"). This is also the gate that catches the
    inverse of a passed input — an input the consumer needs to override and
    does not is still a judgement call, but an input that cannot exist is not.

    A job's stage is resolved the way GitLab resolves it: an absent `stage:` is
    `test`, not "unknown", so a stage-less library job lands in a stage a
    custom `stages:` list very likely does not declare. The one case left
    unchecked is a job that carries `extends` and no stage of its own — its
    stage is inherited from a job this gate does not follow, so guessing `test`
    there would invent a failure.
    """
    pipeline = root / ".gitlab-ci.yml"
    if not pipeline.is_file():
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
        # The `spec:` header is OPTIONAL — a template with no inputs is legal
        # and its FIRST document already holds jobs. Reading docs[0] as a header
        # unconditionally would drop every job in such a template on the floor,
        # and the stage arm below would then inspect nothing and report ok.
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
                    # The stage comes from the extended job, which may live in
                    # another document or another file. Resolving that chain is
                    # out of scope, so the job is left unchecked rather than
                    # measured against a default it never takes.
                    continue
                else:
                    stage = _IMPLICIT_STAGE
                if stage is not None and stage not in stages:
                    problems.append(
                        f"{rel}: job {job!r} resolves to stage {stage!r}, "
                        f"which the pipeline does not declare"
                    )
    status = "ok" if not problems else "FAILED"
    print(f"  {'include contract':<22} {status}")
    for problem in problems:
        print(f"    {problem}")
    return ["include contract"] if problems else []


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
        help="weisssrv-lib checkout — enables the vendored-copy and include-contract gates.",
    )
    args = parser.parse_args()

    missing = [tool for tool in REQUIRED_TOOLS if not shutil.which(tool)]
    if missing:
        print(f"error: not on PATH: {', '.join(missing)}", file=sys.stderr)
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
            failures += check_registered_copies(args.lib_path)
            failures += check_include_contract(root, args.lib_path)
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
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
