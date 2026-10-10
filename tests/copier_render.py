"""Render a copier template into a throwaway directory, for a template's tests.

The source is copied to a scratch directory with .git left behind, so the
working tree is rendered rather than committed HEAD. Contract: docs/SCRIPTS.md.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence

# Build output and caches: copying them wastes time, and .git changes what
# copier renders.
IGNORED = (".git", "__pycache__", "*.pyc", ".pytest_cache", ".ruff_cache", ".render")


def copy_source(repo_root: Path, scratch: Path, dest_name: str = "template-src",
                extra_ignore: Sequence[str] = ()) -> Path:
    src = scratch / dest_name
    shutil.copytree(
        repo_root, src, ignore=shutil.ignore_patterns(*IGNORED, *extra_ignore)
    )
    return src


def copier_argv(source: Path, dest: Path, answers: Path,
                data: Optional[Dict[str, str]] = None,
                python: str = sys.executable) -> List[str]:
    overrides: List[str] = []
    for key, value in (data or {}).items():
        overrides += ["--data", "%s=%s" % (key, value)]
    return [
        python, "-m", "copier", "copy",
        "--defaults", "--overwrite", "--trust",
        "--data-file", str(answers),
        *overrides,
        str(source), str(dest),
    ]


def render(repo_root: Path, scratch: Path, answers: Path,
           dest_name: str = "render", data: Optional[Dict[str, str]] = None,
           extra_ignore: Sequence[str] = (), runner=subprocess.run) -> Path:
    """Render the working tree with `answers`; return the generated repo root.

    `data` overrides individual answers on top of the file, which is how a
    second shape is covered without a second full fixture.
    """
    source = copy_source(repo_root, scratch, extra_ignore=extra_ignore)
    dest = scratch / dest_name
    runner(copier_argv(source, dest, answers, data), check=True)
    return dest


def cli_main(repo_root: Path, answers: Path, prefix: str,
             argv: Optional[Sequence[str]] = None) -> int:
    """`python tests/render_<x>.py --out DIR` — render once for inspection."""
    parser = argparse.ArgumentParser(description="Render the template for inspection.")
    parser.add_argument("--out", type=Path,
                        help="Directory to render into (must not exist).")
    parser.add_argument("--answers", type=Path, default=answers)
    args = parser.parse_args(argv)

    scratch = Path(tempfile.mkdtemp(prefix=prefix))
    dest = render(repo_root, scratch, answers=args.answers)
    if args.out:
        shutil.copytree(dest, args.out)
        shutil.rmtree(scratch, ignore_errors=True)
        dest = args.out
    print(dest)
    return 0


def check_registered_copies(lib_path: Path, repo_root: Path,
                            manifest_relpath: str = "scripts/vendored-manifest.yml",
                            label: str = "registered copies",
                            ref: Optional[str] = None) -> List[str]:
    """Run the library's comparison engine over this repository's manifest.

    The consumer owns the manifest and a missing engine is a failure, never a
    skip. `ref` is the consumer's own pin, so the compare target is that release.
    """
    checker = lib_path / "scripts" / "check-vendored-copies.py"
    if not checker.is_file():
        return [
            "%s ships no scripts/check-vendored-copies.py — the vendored-copy "
            "gate cannot run, and it must not silently skip" % lib_path
        ]
    manifest = repo_root / manifest_relpath
    if not manifest.is_file():
        return [
            "%s does not exist — the vendored-copy gate has nothing to check, "
            "and it must not silently skip" % manifest
        ]
    argv = [
        sys.executable, str(checker),
        "--manifest", str(manifest),
        "--repo-root", str(repo_root),
        "--lib-path", str(lib_path),
    ]
    if ref:
        argv += ["--ref", str(ref)]
    result = subprocess.run(argv, capture_output=True, text=True)
    print("  %-22s %s" % (label, "ok" if result.returncode == 0 else "FAILED"))
    if result.returncode:
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        return [label]
    return []
