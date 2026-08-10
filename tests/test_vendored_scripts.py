"""The vendored library scripts must be byte-identical to the library's.

`scripts/` holds copies of weisssrv-lib tooling — the CI jobs run them from this
path rather than cloning the library at job time. Nothing else notices when a
copy drifts: the fix the library shipped is simply absent, and the next
re-vendoring silently reverts whatever was edited here. Anything in `scripts/`
with no upstream twin and no entry in LOCAL is reported too, because a copy the
library stopped shipping is exactly as invisible as one that drifted.

The comparison runs against the checkout at $WEISSSRV_LIB_PATH (the CI job
clones the library into .tmp/lib) and only when that checkout is actually at
`variables.WEISSSRV_LIB_REF` — comparing against another ref would send whoever
reads the failure to re-copy the wrong file.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

import template_repo as tr

# Written here, with no library twin.
LOCAL = frozenset(
    {
        "rename.sh",
        "select-ci.sh",
        "lib-cli.sh",
        "apply-cluster-identity.sh",
        "cluster-identity.env",
    }
)

DEFAULT_LIB_PATH = tr.REPO_ROOT.parent / "weisssrv-lib"


def _lib_ref() -> str:
    ci = yaml.safe_load((tr.REPO_ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8"))
    return ci["variables"]["WEISSSRV_LIB_REF"]


def _unavailable(reason: str):
    if os.environ.get("WEISSSRV_REQUIRE_CLI"):
        pytest.fail(f"WEISSSRV_REQUIRE_CLI is set but {reason}")
    pytest.skip(reason)


@pytest.fixture(scope="session")
def lib_checkout() -> Path:
    """A weisssrv-lib checkout that is provably at the pinned ref."""
    lib = Path(os.environ.get("WEISSSRV_LIB_PATH", DEFAULT_LIB_PATH))
    if not (lib / "scripts").is_dir():
        _unavailable(f"no weisssrv-lib checkout at {lib} (set WEISSSRV_LIB_PATH)")
    ref = _lib_ref()

    def rev(what: str) -> str | None:
        proc = subprocess.run(
            ["git", "-C", str(lib), "rev-parse", "--verify", "--quiet", what],
            capture_output=True,
            text=True,
        )
        return proc.stdout.strip() or None

    head, pinned = rev("HEAD"), rev(f"{ref}^{{commit}}")
    if pinned is None:
        _unavailable(f"{lib} has no commit for {ref}; cannot compare against the pin")
    if head != pinned:
        _unavailable(f"{lib} is not at {ref}; a comparison would name the wrong file")
    return lib


def test_vendored_scripts_match_the_library(lib_checkout: Path):
    lib_scripts = lib_checkout / "scripts"
    drifted, orphaned, compared = [], [], 0
    for path in sorted((tr.REPO_ROOT / "scripts").iterdir()):
        if not path.is_file() or path.name in LOCAL:
            continue
        upstream = lib_scripts / path.name
        if not upstream.is_file():
            orphaned.append(path.name)
            continue
        compared += 1
        if upstream.read_bytes() != path.read_bytes():
            drifted.append(path.name)

    assert not drifted, (
        "vendored scripts differ from weisssrv-lib at the pinned ref "
        f"(re-copy them and review the diff): {', '.join(drifted)}"
    )
    assert not orphaned, (
        "scripts/ holds files the library does not ship and LOCAL does not "
        f"declare: {', '.join(orphaned)}"
    )
    assert compared, "no vendored scripts were compared — did scripts/ move?"
