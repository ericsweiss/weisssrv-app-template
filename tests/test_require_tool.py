"""The fail-vs-skip rules, which decide whether a gate missing its binary or its
checkout reports that or certifies a check it never ran.
"""

from __future__ import annotations

import pytest
from conftest import require_env, require_tool

GATE = "require_tool self-test"


def _missing_tool(ci_optional: bool = False):
    return lambda: require_tool("definitely-not-a-binary", GATE, ci_optional=ci_optional)


def _unset_env():
    return require_env("DEFINITELY_NOT_SET_ANYWHERE", GATE)


@pytest.mark.parametrize(
    ("call", "ci", "expected"),
    [
        pytest.param(_missing_tool(), True, "failed", id="missing-binary-under-ci-fails"),
        pytest.param(_missing_tool(ci_optional=True), True, "skipped", id="ci-optional-skips"),
        pytest.param(_missing_tool(), False, "skipped", id="missing-binary-locally-skips"),
        pytest.param(lambda: require_tool("python3", GATE), True, "passed", id="present-binary"),
        pytest.param(_unset_env, True, "failed", id="unset-env-under-ci-fails"),
        pytest.param(_unset_env, False, "skipped", id="unset-env-locally-skips"),
    ],
)
def test_outcome(monkeypatch, call, ci, expected):
    if ci:
        monkeypatch.setenv("CI", "true")
    else:
        monkeypatch.delenv("CI", raising=False)
    try:
        call()
        outcome = "passed"
    except pytest.fail.Exception:
        outcome = "failed"
    except pytest.skip.Exception:
        outcome = "skipped"
    assert outcome == expected


def test_a_set_variable_returns_its_value(monkeypatch):
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("SOME_CHECKOUT", "/tmp/lib")
    assert require_env("SOME_CHECKOUT", GATE) == "/tmp/lib"
