"""Smoke run of the vendored scrape-wiring gate: one pass, one armed failure.

test_render.py runs the gate over every render through the real task and CI
lines. The library's own suite holds the behavioural cases.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

import render_app

GATE = render_app.REPO_ROOT / "template" / "scripts" / "check-scrape-wiring.py"
LABELS = {"app.kubernetes.io/name": "tidepool"}


def _workload() -> dict:
    container = {
        "name": "app",
        "image": "example/app:1",
        "ports": [{"name": "http", "containerPort": 8080}],
    }
    spec = {"template": {"metadata": {"labels": LABELS}, "spec": {"containers": [container]}}}
    metadata = {"name": "tidepool"}
    return {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": metadata, "spec": spec}


def _service() -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": "tidepool", "labels": LABELS},
        "spec": {
            "selector": LABELS,
            "ports": [{"name": "http", "port": 8080, "targetPort": "http"}],
        },
    }


def _service_monitor() -> dict:
    return {
        "apiVersion": "monitoring.coreos.com/v1",
        "kind": "ServiceMonitor",
        "metadata": {"name": "tidepool"},
        "spec": {
            "selector": {"matchLabels": LABELS},
            "endpoints": [{"port": "http", "path": "/metrics"}],
        },
    }


def _allow() -> dict:
    observability = {"kubernetes.io/metadata.name": "observability"}
    scraper = {"namespaceSelector": {"matchLabels": observability}}
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "allow-scrape-from-observability"},
        "spec": {
            "podSelector": {"matchLabels": LABELS},
            "policyTypes": ["Ingress"],
            "ingress": [{"from": [scraper], "ports": [{"protocol": "TCP", "port": 8080}]}],
        },
    }


def _write(tmp_path: Path, documents: list[dict]) -> Path:
    directory = tmp_path / "flux"
    directory.mkdir()
    (directory / "manifests.yaml").write_text(yaml.safe_dump_all(documents))
    return directory


def _run(directory: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), str(directory)], capture_output=True, text=True, timeout=120
    )


def test_a_wired_tree_passes(tmp_path):
    result = _run(_write(tmp_path, [_workload(), _service(), _service_monitor(), _allow()]))
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_policy_admitting_the_wrong_port_fails(tmp_path):
    """The render passes by construction, so only a mutation shows the gate is armed."""
    policy = _allow()
    policy["spec"]["ingress"][0]["ports"] = [{"protocol": "TCP", "port": 9090}]
    result = _run(_write(tmp_path, [_workload(), _service(), _service_monitor(), policy]))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Traceback" not in result.stderr, result.stderr
    assert "no NetworkPolicy admits namespace observability on port 8080" in result.stderr
