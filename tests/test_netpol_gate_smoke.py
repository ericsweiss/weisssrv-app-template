"""Prove the vendored egress-fence gate can FAIL.

The rendered manifests pass it by construction, so a run over them is not
evidence: only a mutated except-list shows the gate is armed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import render_app

# The gate ships to every generated repo from here, so this is the copy the
# tenant's own egress edits are gated by. The library's byte-identity engine
# keeps it equal to weisssrv-lib.
GATE = render_app.REPO_ROOT / "template" / "scripts" / "check-netpol-except-parity.py"

# Both canonical lists come from the gate itself, so the fixtures cannot drift
# from the constant. `reserved-full` is the one the rendered manifests use.
_parity = render_app.load_gate("check-netpol-except-parity.py", "template/scripts")

CANONICAL_LISTS = [_parity.LAN_FENCE, _parity.RESERVED_FULL]
CANONICAL_IDS = ["lan-fence", "reserved-full"]


def _policy(excepts: list[str]) -> dict:
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "allow-egress-public", "namespace": "tidepool"},
        "spec": {
            "podSelector": {},
            "policyTypes": ["Egress"],
            "egress": [{"to": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": excepts}}]}],
        },
    }


def _run(directory: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), str(directory)], capture_output=True, text=True
    )


def _write(tmp_path: Path, excepts: list[str]) -> Path:
    directory = tmp_path / "flux"
    directory.mkdir()
    (directory / "networkpolicy.yaml").write_text(yaml.safe_dump(_policy(excepts)))
    return directory


@pytest.mark.parametrize("canonical", CANONICAL_LISTS, ids=CANONICAL_IDS)
def test_the_canonical_fence_passes(tmp_path, canonical):
    assert _run(_write(tmp_path, canonical)).returncode == 0


def test_an_emptied_except_list_fails(tmp_path):
    """The edit that most directly re-opens the LAN."""
    assert all(CANONICAL_LISTS), "the gate exposed an empty canonical list"
    result = _run(_write(tmp_path, []))
    assert result.returncode == 1, result.stdout + result.stderr


@pytest.mark.parametrize("canonical", CANONICAL_LISTS, ids=CANONICAL_IDS)
def test_a_shortened_except_list_fails(tmp_path, canonical):
    """Dropping the metadata address alone is enough to fail it."""
    assert "169.254.0.0/16" in canonical
    result = _run(_write(tmp_path, [c for c in canonical if c != "169.254.0.0/16"]))
    assert result.returncode == 1, result.stdout + result.stderr


def test_a_directory_with_no_policies_is_an_error(tmp_path):
    """A renamed manifest subtree must red the gate, not pass it silently."""
    directory = tmp_path / "flux"
    directory.mkdir()
    (directory / "configmap.yaml").write_text(
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: not-a-policy\n"
    )
    result = _run(directory)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "is not a gate" in result.stderr
