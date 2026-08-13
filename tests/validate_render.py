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
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import render_app

CATALOG = (
    "https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/"
    "{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json"
)
REQUIRED_TOOLS = ("yamllint", "kustomize", "kubeconform", "ruff")

# Key under `consumers:` in the library's scripts/vendored-paths.yml.
CONSUMER = "weisssrv-app-template"


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
    """Run the library's registry-driven gate over THIS repository.

    The copies are here, not in the render: the workflows and scripts under
    `template/` are rendered into a tenant, and the ones at the root are what
    this repo runs on itself. Both are byte-identical to the library, and the
    registry (weisssrv-lib/scripts/vendored-paths.yml) is the only place that
    relationship is written down — including the lint profiles this repo
    deliberately FORKS, where the drift is silent in the other direction (the
    library moves and the fork never absorbs it).

    Declared in the library rather than here so a file it starts or stops
    shipping reaches every consumer's gate at the next bump.
    """
    checker = lib_path / "scripts" / "check-vendored-copies.py"
    registry = lib_path / "scripts" / "vendored-paths.yml"
    if not checker.is_file() or not registry.is_file():
        return [
            f"{lib_path} ships no scripts/check-vendored-copies.py + vendored-paths.yml "
            "— the registry gate cannot run, and it must not silently skip"
        ]
    result = subprocess.run(
        [
            sys.executable,
            str(checker),
            "--consumer",
            CONSUMER,
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
        help="weisssrv-lib checkout — enables the vendored-copy registry gate.",
    )
    args = parser.parse_args()

    missing = [tool for tool in REQUIRED_TOOLS if not shutil.which(tool)]
    if missing:
        print(f"error: not on PATH: {', '.join(missing)}", file=sys.stderr)
        return 2

    import yaml

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
