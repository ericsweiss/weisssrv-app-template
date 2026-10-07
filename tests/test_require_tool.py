"""The fail-vs-skip rules, which decide whether a gate missing its binary or its
checkout reports that or certifies a check it never ran.
"""

from __future__ import annotations

import os

import pytest
from conftest import require_env, require_tool

MISSING = "definitely-not-a-binary"
GATE = "require_tool self-test"


def _outcome(ci_optional: bool = False) -> str:
    try:
        require_tool(MISSING, GATE, "Install it in the job.", ci_optional=ci_optional)
    except pytest.fail.Exception:
        return "failed"
    except pytest.skip.Exception:
        return "skipped"
    return "passed"


@pytest.fixture
def ambient_ci():
    """CI as a job sets it: in the real environment, not via monkeypatch, so a
    fixture-scoped view of the variable is exercised too."""
    previous = os.environ.get("CI")
    os.environ["CI"] = "true"
    yield
    if previous is None:
        del os.environ["CI"]
    else:
        os.environ["CI"] = previous


def test_a_missing_binary_fails_when_ci_is_set(ambient_ci):
    assert _outcome() == "failed", (
        "a tool-driven gate skipped in CI, so it certified a check it never ran"
    )


def test_a_ci_optional_gate_skips_in_ci(ambient_ci):
    assert _outcome(ci_optional=True) == "skipped", (
        "a ci_optional gate went red in CI instead of deferring to render-validate"
    )


def test_a_missing_binary_skips_without_ci(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    assert _outcome() == "skipped", "a workstation without the tool must not go red"


def test_a_present_binary_passes(monkeypatch):
    monkeypatch.setenv("CI", "true")
    require_tool("python3", GATE)


def _env_outcome() -> str:
    try:
        require_env("DEFINITELY_NOT_SET_ANYWHERE", GATE, "Set it in the job.")
    except pytest.fail.Exception:
        return "failed"
    except pytest.skip.Exception:
        return "skipped"
    return "passed"


def test_an_unset_variable_fails_when_ci_is_set(ambient_ci):
    assert _env_outcome() == "failed", (
        "a checkout-driven gate skipped in CI, so it reported on nothing"
    )


def test_an_unset_variable_skips_without_ci(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    assert _env_outcome() == "skipped", "a workstation without the checkout must not go red"


def test_a_set_variable_returns_its_value(monkeypatch):
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("SOME_CHECKOUT", "/tmp/lib")
    assert require_env("SOME_CHECKOUT", GATE) == "/tmp/lib"
