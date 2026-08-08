"""Fixtures for the template's own gate.

Every test runs against a throwaway copy of this repository, driven through
`scripts/rename.sh` / `scripts/select-ci.sh`. Those wrappers exec the
weisssrv-lib `weisssrv-new-project` CLI, so the CLI has to be resolvable — see
`script_env`. Set WEISSSRV_REQUIRE_CLI=1 (the CI job does) to turn a missing CLI
into a failure instead of a skip, so the suite can never go green by skipping
everything.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import template_repo

CLI = "weisssrv-new-project"
# A library checkout to run the CLI out of when it is not installed. Defaults to
# the sibling clone the repos are developed in.
LIB_PATH_ENV = "WEISSSRV_LIB_PATH"
DEFAULT_LIB_PATH = template_repo.REPO_ROOT.parent / "weisssrv-lib"


def _write_shim(bindir: Path, pythonpath: str | None) -> Path:
    """A `weisssrv-new-project` on PATH for an importable-but-unscripted CLI.

    `pip install --user` puts the console script in ~/.local/bin, which is not
    on PATH in the python images, and a plain checkout has no script at all —
    both cases run the same module.
    """
    shim = bindir / CLI
    prefix = f'PYTHONPATH="{pythonpath}${{PYTHONPATH:+:$PYTHONPATH}}" ' if pythonpath else ""
    shim.write_text(f'#!/bin/sh\n{prefix}exec "{sys.executable}" -m weisssrv_lib_cli "$@"\n')
    shim.chmod(0o755)
    return shim


def _importable(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _provide_cli(bindir: Path) -> str | None:
    """Put the CLI on PATH under `bindir`. Returns why it could not, or None."""
    if _importable("weisssrv_lib_cli"):
        _write_shim(bindir, None)
        return None
    cli_dir = Path(os.environ.get(LIB_PATH_ENV, DEFAULT_LIB_PATH)) / "cli"
    if not (cli_dir / "weisssrv_lib_cli" / "cli.py").is_file():
        return (
            f"no {CLI} on PATH, weisssrv_lib_cli is not importable, and no "
            f"library checkout at {cli_dir} (set {LIB_PATH_ENV})"
        )
    if not _importable("ruamel.yaml"):
        return f"library checkout at {cli_dir} needs ruamel.yaml, which is not installed"
    _write_shim(bindir, str(cli_dir))
    return None


def _unavailable(reason: str):
    if os.environ.get("WEISSSRV_REQUIRE_CLI"):
        pytest.fail(f"WEISSSRV_REQUIRE_CLI is set but {reason}")
    pytest.skip(reason)


@pytest.fixture(scope="session")
def script_env(tmp_path_factory) -> dict:
    """The environment the setup scripts run under, with the CLI on PATH."""
    env = dict(os.environ)
    if not shutil.which(CLI):
        bindir = tmp_path_factory.mktemp("cli-bin")
        reason = _provide_cli(bindir)
        if reason:
            _unavailable(reason)
        env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    proc = subprocess.run(
        [CLI, "--version"], env=env, capture_output=True, text=True
    )
    if proc.returncode != 0:
        _unavailable(f"{CLI} --version failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return env


@pytest.fixture(scope="session", autouse=True)
def _is_the_template():
    """The gate asserts on the template's placeholders, so it only applies to
    the template. A project generated from it has already substituted them."""
    text = "".join(
        (template_repo.REPO_ROOT / rel).read_text(encoding="utf-8", errors="ignore")
        for rel in template_repo.tracked_files()
    )
    if template_repo.APP_TOKEN not in text:
        pytest.skip(
            "no placeholder tokens in this checkout — it is a project generated "
            "from the template, not the template; delete tests/"
        )


@pytest.fixture(scope="session")
def pristine(tmp_path_factory) -> Path:
    """An untouched copy: the baseline every 'nothing else moved' check uses."""
    return template_repo.copy_template(tmp_path_factory.mktemp("pristine"))


@pytest.fixture
def project(tmp_path) -> Path:
    """A fresh copy per test, for the cases that mutate or must fail."""
    return template_repo.copy_template(tmp_path / "project")


@pytest.fixture(scope="session")
def renamed(tmp_path_factory, script_env) -> Path:
    root = template_repo.copy_template(tmp_path_factory.mktemp("renamed"))
    template_repo.rename(root, env=script_env)
    return root


@pytest.fixture(scope="session")
def shapes(renamed, tmp_path_factory, script_env) -> dict:
    """The documented two-command flow, once per CI shape."""
    base = tmp_path_factory.mktemp("shapes")
    out = {}
    for shape in template_repo.SHAPES:
        root = template_repo.clone_project(renamed, base / shape)
        template_repo.select_ci(root, shape, env=script_env)
        out[shape] = root
    return out


@pytest.fixture(scope="session", params=template_repo.SHAPES)
def shaped(request, shapes) -> tuple:
    return request.param, shapes[request.param]
