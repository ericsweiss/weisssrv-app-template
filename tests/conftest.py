"""Put tests/ on sys.path so render_app is importable from every test module.

Also holds the fail-vs-skip rules for a gate that needs something the test
process does not carry: require_tool for a binary, require_env for a checkout.
"""

import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


def require_tool(
    name: str, gate_name: str, install_hint: str = "", ci_optional: bool = False
) -> None:
    """Stop a binary-driven gate that has no binary instead of skipping in CI.

    Under $CI a missing tool means the job never installed it, so the gate would
    certify a check it never ran. ci_optional defers to validate-rendered-app instead.
    """
    if shutil.which(name):
        return
    if os.environ.get("CI"):
        if not ci_optional:
            pytest.fail(
                f"{name} is not on PATH — the {gate_name} gate cannot run. "
                + (install_hint or "Install it in the job.")
            )
        pytest.skip(f"{name} not on PATH — validate-rendered-app runs the {gate_name} gate")
    pytest.skip(f"{name} not on PATH")


def require_env(name: str, gate_name: str, install_hint: str = "") -> str:
    """The value of an environment variable a gate needs, or stop instead of skip.

    Same rule as require_tool: a workstation without the checkout skips, but under
    $CI an unset variable means the job lost it, so the gate would report nothing.
    """
    value = os.environ.get(name)
    if value:
        return value
    if os.environ.get("CI"):
        pytest.fail(
            f"{name} is unset — the {gate_name} gate cannot run. "
            + (install_hint or "Set it in the job.")
        )
    pytest.skip(f"{name} is unset")
