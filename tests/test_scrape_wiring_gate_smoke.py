"""Prove the template's scrape-wiring gate can FAIL.

The rendered manifests pass it by construction, so a run over them is not
evidence: only a mutated pair shows the gate is armed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import render_app

GATE = render_app.REPO_ROOT / "template" / "scripts" / "check-scrape-wiring.py"
LABELS = {"app.kubernetes.io/name": "tidepool"}


def _deployment() -> dict:
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "tidepool"},
        "spec": {
            "template": {
                "metadata": {"labels": LABELS},
                "spec": {
                    "containers": [
                        {
                            "name": "app",
                            "image": "example/app:1",
                            "ports": [{"name": "http", "containerPort": 8080}],
                        }
                    ]
                },
            }
        },
    }


def _workload(kind: str = "Deployment") -> dict:
    """The same pod template under each kind the gate reads."""
    workload = _deployment()
    workload["kind"] = kind
    if kind == "StatefulSet":
        workload["spec"]["serviceName"] = "tidepool"
    return workload


def _service(labels: dict | None = None, ports: list | None = None) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": "tidepool", "labels": labels or LABELS},
        "spec": {
            "selector": LABELS,
            "ports": ports or [{"name": "http", "port": 8080, "targetPort": "http"}],
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


def _pod_monitor() -> dict:
    return {
        "apiVersion": "monitoring.coreos.com/v1",
        "kind": "PodMonitor",
        "metadata": {"name": "tidepool-pods"},
        "spec": {
            "selector": {"matchLabels": LABELS},
            "podMetricsEndpoints": [{"port": "http", "path": "/metrics"}],
        },
    }


def _allow(ports: list | None = None, infer: bool = False) -> dict:
    spec = {
        "podSelector": {"matchLabels": LABELS},
        "ingress": [
            {
                "from": [
                    {
                        "namespaceSelector": {
                            "matchLabels": {"kubernetes.io/metadata.name": "observability"}
                        }
                    }
                ],
                "ports": [{"protocol": "TCP", "port": 8080}] if ports is None else ports,
            }
        ],
    }
    if not infer:
        spec["policyTypes"] = ["Ingress"]
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "allow-scrape-from-observability"},
        "spec": spec,
    }


def _write(tmp_path: Path, documents: list[dict], name: str = "flux") -> Path:
    directory = tmp_path / name
    directory.mkdir()
    (directory / "manifests.yaml").write_text(yaml.safe_dump_all(documents))
    return directory


def _run(directory: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), str(directory), *extra], capture_output=True, text=True
    )


def _base(monitor: dict, policy: dict | None) -> list[dict]:
    documents = [_deployment(), _service(), monitor]
    if policy is not None:
        documents.append(policy)
    return documents


# Parametrized over the factories, not their results: a document built once at
# collection is shared by every case in every test that takes it.
MONITORS = pytest.mark.parametrize(
    "monitor_factory", [_service_monitor, _pod_monitor], ids=["service", "pod"]
)


@MONITORS
def test_a_wired_pair_passes(tmp_path, monitor_factory):
    result = _run(_write(tmp_path, _base(monitor_factory(), _allow())))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("kind", ["Deployment", "StatefulSet", "DaemonSet"])
def test_every_workload_kind_is_read(tmp_path, kind):
    """A monitored workload is as often a StatefulSet or DaemonSet as a
    Deployment; a kind the gate does not read reds a wired tree."""
    documents = [_workload(kind), _service(), _service_monitor(), _allow()]
    result = _run(_write(tmp_path, documents))
    assert result.returncode == 0, result.stdout + result.stderr


@MONITORS
def test_no_policy_at_all_fails(tmp_path, monitor_factory):
    """The vacuous pass this gate exists to prevent: the drift shows up as a
    TargetDown page in the platform's Alertmanager, not here."""
    result = _run(_write(tmp_path, _base(monitor_factory(), None)))
    assert result.returncode == 1, result.stdout + result.stderr


@MONITORS
def test_a_drifted_policy_port_fails(tmp_path, monitor_factory):
    policy = _allow(ports=[{"protocol": "TCP", "port": 9090}])
    result = _run(_write(tmp_path, _base(monitor_factory(), policy)))
    assert result.returncode == 1, result.stdout + result.stderr


@MONITORS
def test_a_udp_only_policy_fails(tmp_path, monitor_factory):
    """A port entry defaults to TCP; an explicit UDP admits nothing the scrape
    uses, and passing it is the silent direction."""
    policy = _allow(ports=[{"protocol": "UDP", "port": 8080}])
    result = _run(_write(tmp_path, _base(monitor_factory(), policy)))
    assert result.returncode == 1, result.stdout + result.stderr


@pytest.mark.parametrize(
    "policy_kwargs",
    [
        {"ports": []},
        {"ports": [{"port": "http"}]},
        {"infer": True},
        {"ports": [{"protocol": "TCP", "port": 8000, "endPort": 9000}]},
        {"ports": [{"protocol": "TCP"}]},
    ],
    ids=["empty-ports", "named-port", "inferred-ingress", "port-range", "protocol-only"],
)
def test_the_legal_policy_spellings_pass(tmp_path, policy_kwargs):
    """The API admits each of these, so failing one blocks a merge on a policy
    that works."""
    result = _run(_write(tmp_path, _base(_service_monitor(), _allow(**policy_kwargs))))
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_monitor_with_no_endpoints_is_an_error(tmp_path):
    """A schema change that empties the endpoint list must red the gate, not
    let it report a clean run over nothing. Exit 2: nothing could be checked."""
    monitor = _service_monitor()
    monitor["spec"]["endpoints"] = []
    result = _run(_write(tmp_path, _base(monitor, _allow())))
    assert result.returncode == 2, result.stdout + result.stderr
    assert "no endpoints" in result.stdout + result.stderr


def test_a_stale_service_port_name_fails(tmp_path):
    monitor = _service_monitor()
    monitor["spec"]["endpoints"] = [{"port": "metrics"}]
    result = _run(_write(tmp_path, _base(monitor, _allow())))
    assert result.returncode == 1, result.stdout + result.stderr


def test_a_peer_combining_a_pod_selector_fails(tmp_path):
    """An unmodelled peer shape must fail loudly rather than be credited."""
    policy = _allow()
    policy["spec"]["ingress"][0]["from"][0]["podSelector"] = {
        "matchLabels": {"app.kubernetes.io/name": "not-prometheus"}
    }
    result = _run(_write(tmp_path, _base(_service_monitor(), policy)))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "does not model" in result.stdout + result.stderr


def test_a_tree_with_no_monitor_is_a_clean_no_op(tmp_path):
    result = _run(_write(tmp_path, [_deployment(), _service()]))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "nothing to check" in result.stdout


# A Service labelled beyond the pods it selects: `metadata.labels` is what the
# monitor matches, `spec.selector` is what the pods carry.
RELABELLED = {**LABELS, "app.kubernetes.io/component": "api"}


def test_a_service_labelled_beyond_its_pods_passes(tmp_path):
    """Kubernetes does not require a Service's own labels to equal the labels of
    the pods it selects, so reading one as the other reds a wired repo."""
    documents = [_deployment(), _service(labels=RELABELLED), _service_monitor(), _allow()]
    result = _run(_write(tmp_path, documents))
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_drifted_port_behind_a_relabelled_service_still_fails(tmp_path):
    policy = _allow(ports=[{"protocol": "TCP", "port": 9090}])
    documents = [_deployment(), _service(labels=RELABELLED), _service_monitor(), policy]
    result = _run(_write(tmp_path, documents))
    assert result.returncode == 1, result.stdout + result.stderr


def test_a_pod_selector_of_match_expressions_is_not_credited(tmp_path):
    """matchLabels is the only pod selector this gate models; reading an absent
    one as select-all credits a policy that may select nothing."""
    policy = _allow()
    policy["spec"]["podSelector"] = {
        "matchExpressions": [
            {"key": "app.kubernetes.io/name", "operator": "In", "values": ["some-other-app"]}
        ]
    }
    result = _run(_write(tmp_path, _base(_service_monitor(), policy)))
    assert result.returncode == 1, result.stdout + result.stderr


def test_a_select_all_pod_selector_passes(tmp_path):
    """An empty podSelector selects every pod, the monitored ones included."""
    policy = _allow()
    policy["spec"]["podSelector"] = {}
    result = _run(_write(tmp_path, _base(_service_monitor(), policy)))
    assert result.returncode == 0, result.stdout + result.stderr


def test_an_empty_namespace_selector_passes(tmp_path):
    """An empty namespaceSelector selects every namespace, so it admits the
    scrape and failing it blocks a merge on a policy that works."""
    policy = _allow()
    policy["spec"]["ingress"][0]["from"] = [{"namespaceSelector": {}}]
    result = _run(_write(tmp_path, _base(_service_monitor(), policy)))
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_rule_with_no_from_passes(tmp_path):
    """An absent `from` matches every source, the same way an absent `ports`
    matches every port."""
    policy = _allow()
    del policy["spec"]["ingress"][0]["from"]
    result = _run(_write(tmp_path, _base(_service_monitor(), policy)))
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_target_port_nothing_serves_fails(tmp_path):
    """A scrape on a port no container declares is `up == 0` however wide the
    policy is, so an empty `ports:` must not carry it."""
    monitor = _pod_monitor()
    monitor["spec"]["podMetricsEndpoints"] = [{"targetPort": 9999}]
    result = _run(_write(tmp_path, _base(monitor, _allow(ports=[]))))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "9999" in result.stdout + result.stderr


def test_an_unnamed_container_port_scraped_by_number_passes(tmp_path):
    deployment = _deployment()
    deployment["spec"]["template"]["spec"]["containers"][0]["ports"] = [{"containerPort": 8080}]
    monitor = _pod_monitor()
    monitor["spec"]["podMetricsEndpoints"] = [{"targetPort": 8080}]
    result = _run(_write(tmp_path, [deployment, _service(), monitor, _allow()]))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("peer_file", ["a-peers.yaml", "z-peers.yaml"])
def test_a_second_labelled_service_is_consulted(tmp_path, peer_file):
    """A headless peer Service carrying the same labels must not decide the
    verdict, whichever way the filename sort puts it."""
    peers = _service(ports=[{"name": "peer", "port": 7946, "targetPort": "peer"}])
    peers["metadata"]["name"] = "tidepool-peers"
    directory = tmp_path / "flux"
    directory.mkdir()
    (directory / peer_file).write_text(yaml.safe_dump(peers))
    (directory / "m-rest.yaml").write_text(
        yaml.safe_dump_all([_deployment(), _service(), _service_monitor(), _allow()])
    )
    result = _run(directory)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_service_without_the_monitor_labels_is_not_selected(tmp_path):
    """prometheus-operator scrapes only the Services the monitor's selector
    matches, so an unrelated Service needs no scrape policy."""
    side = {"app.kubernetes.io/name": "unrelated-sidecar"}
    sidecar = _deployment()
    sidecar["metadata"]["name"] = "sidecar"
    sidecar["spec"]["template"]["metadata"]["labels"] = side
    sidecar["spec"]["template"]["spec"]["containers"][0]["ports"] = [
        {"name": "http", "containerPort": 9999}
    ]
    service = _service(
        labels=side, ports=[{"name": "http", "port": 9999, "targetPort": "http"}]
    )
    service["metadata"]["name"] = "sidecar"
    service["spec"]["selector"] = side
    documents = [*_base(_service_monitor(), _allow()), sidecar, service]
    result = _run(_write(tmp_path, documents))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "sidecar" not in result.stdout + result.stderr


def test_a_port_range_that_excludes_the_scrape_fails(tmp_path):
    policy = _allow(ports=[{"protocol": "TCP", "port": 9000, "endPort": 9100}])
    result = _run(_write(tmp_path, _base(_service_monitor(), policy)))
    assert result.returncode == 1, result.stdout + result.stderr


def test_a_policy_selecting_a_label_beyond_the_service_selector_passes(tmp_path):
    """A policy may name a pod label the Service selector omits, so the subset
    test runs against the pod labels."""
    deployment = _deployment()
    deployment["spec"]["template"]["metadata"]["labels"] = RELABELLED
    policy = _allow()
    policy["spec"]["podSelector"] = {"matchLabels": RELABELLED}
    result = _run(_write(tmp_path, [deployment, _service(), _service_monitor(), policy]))
    assert result.returncode == 0, result.stdout + result.stderr


def _two_workloads() -> list[dict]:
    workloads = []
    for component in ("api", "worker"):
        deployment = _deployment()
        deployment["metadata"]["name"] = f"tidepool-{component}"
        deployment["spec"]["template"]["metadata"]["labels"] = {
            **LABELS,
            "app.kubernetes.io/component": component,
        }
        workloads.append(deployment)
    return workloads


def test_a_label_only_one_selected_workload_carries_is_not_credited(tmp_path):
    """Two workloads share the Service selector, so only the labels both carry
    widen it: a policy naming one component does not admit the other's pods."""
    policy = _allow()
    policy["spec"]["podSelector"] = {"matchLabels": RELABELLED}
    documents = [*_two_workloads(), _service(), _service_monitor(), policy]
    result = _run(_write(tmp_path, documents))
    assert result.returncode == 1, result.stdout + result.stderr


def test_a_label_only_the_last_selected_workload_carries_is_not_credited(tmp_path):
    """The narrowing is an intersection, not last-wins: naming the last
    workload's own component must not admit the first workload's pods."""
    policy = _allow()
    policy["spec"]["podSelector"] = {
        "matchLabels": {**LABELS, "app.kubernetes.io/component": "worker"}
    }
    documents = [*_two_workloads(), _service(), _service_monitor(), policy]
    result = _run(_write(tmp_path, documents))
    assert result.returncode == 1, result.stdout + result.stderr


def test_a_policy_on_the_shared_labels_of_two_workloads_passes(tmp_path):
    documents = [*_two_workloads(), _service(), _service_monitor(), _allow()]
    result = _run(_write(tmp_path, documents))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("target", [9999, "metrics"], ids=["number", "name"])
def test_a_service_target_port_nothing_serves_fails(tmp_path, target):
    """A ServiceMonitor resolves through the Service's targetPort: when no
    container declares it the scrape is `up == 0` however wide the policy is."""
    service = _service(ports=[{"name": "http", "port": 9090, "targetPort": target}])
    documents = [_deployment(), service, _service_monitor(), _allow(ports=[])]
    result = _run(_write(tmp_path, documents))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "declares a port" in result.stdout + result.stderr


def test_a_path_that_is_not_a_directory_is_an_error(tmp_path):
    """Exit 2: the gate could not run, which is not the same as a violation."""
    target = tmp_path / "flux.yaml"
    target.write_text(yaml.safe_dump(_deployment()))
    result = _run(target)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "is not a directory" in result.stdout + result.stderr


def test_an_unparseable_manifest_is_an_error(tmp_path):
    """Exit 2 naming the file: a half-finished edit is the common state a
    pre-commit hook runs on, and a traceback names no file."""
    directory = _write(tmp_path, _base(_service_monitor(), _allow()))
    (directory / "broken.yaml").write_text("bad: [unclosed\n")
    result = _run(directory)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "broken.yaml" in result.stderr
    assert "<unicode string>" not in result.stderr
    assert "Traceback" not in result.stderr


def _split_services() -> list[dict]:
    """Two labelled Services on different pods and ports: two scrape targets."""
    documents = []
    for component, port in (("api", 8080), ("worker", 9090)):
        pods = {**LABELS, "app.kubernetes.io/component": component}
        deployment = _deployment()
        deployment["metadata"]["name"] = f"tidepool-{component}"
        deployment["spec"]["template"]["metadata"]["labels"] = pods
        containers = deployment["spec"]["template"]["spec"]["containers"]
        containers[0]["ports"] = [{"name": "http", "containerPort": port}]
        service = _service(ports=[{"name": "http", "port": port, "targetPort": "http"}])
        service["metadata"]["name"] = f"tidepool-{component}"
        service["spec"]["selector"] = pods
        documents += [deployment, service]
    return documents


def _allow_component(component: str, port: int) -> dict:
    policy = _allow(ports=[{"protocol": "TCP", "port": port}])
    policy["metadata"]["name"] = f"allow-scrape-{component}"
    policy["spec"]["podSelector"] = {
        "matchLabels": {**LABELS, "app.kubernetes.io/component": component}
    }
    return policy


def test_a_second_selected_service_with_no_policy_fails(tmp_path):
    """prometheus-operator scrapes EVERY Service a monitor selects, so a policy
    proven for the first says nothing about the second."""
    documents = [*_split_services(), _service_monitor(), _allow_component("api", 8080)]
    result = _run(_write(tmp_path, documents))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "tidepool-worker" in result.stderr


def test_every_selected_service_with_its_own_policy_passes(tmp_path):
    documents = [
        *_split_services(),
        _service_monitor(),
        _allow_component("api", 8080),
        _allow_component("worker", 9090),
    ]
    result = _run(_write(tmp_path, documents))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("peer", "expected"),
    [
        (
            {
                "namespaceSelector": {
                    "matchExpressions": [
                        {
                            "key": "kubernetes.io/metadata.name",
                            "operator": "In",
                            "values": ["observability"],
                        }
                    ]
                }
            },
            "matchExpressions",
        ),
        ({"namespaceSelector": {"matchLabels": {"team": "platform"}}}, "labels other than"),
        ({"ipBlock": {"cidr": "10.42.0.0/16"}}, "ipBlock"),
        (
            {"namespaceSelector": {}, "podSelector": {"matchLabels": {"k8s-app": "prometheus"}}},
            "empty namespaceSelector",
        ),
    ],
    ids=["ns-match-expressions", "ns-other-label", "ip-block", "empty-ns-with-pods"],
)
def test_an_unmodelled_peer_shape_is_named_in_the_failure(tmp_path, peer, expected):
    """Declining an unmodelled shape is right; doing it silently sends a tenant
    to debug a policy that works."""
    policy = _allow()
    policy["spec"]["ingress"][0]["from"] = [peer]
    result = _run(_write(tmp_path, _base(_service_monitor(), policy)))
    assert result.returncode == 1, result.stdout + result.stderr
    output = result.stdout + result.stderr
    assert expected in output, output
    assert "does not " in output


def test_an_egress_only_policy_type_is_not_credited(tmp_path):
    """policyTypes: [Egress] applies no ingress rule, so the namespace
    default-deny still blocks Prometheus."""
    policy = _allow()
    policy["spec"]["policyTypes"] = ["Egress"]
    result = _run(_write(tmp_path, _base(_service_monitor(), policy)))
    assert result.returncode == 1, result.stdout + result.stderr


def test_a_service_port_with_no_target_port_defaults_to_the_port(tmp_path):
    """An omitted targetPort equals `port`, so reading it as absent reds a
    Service a tenant legally writes."""
    service = _service(ports=[{"name": "http", "port": 8080}])
    result = _run(_write(tmp_path, [_deployment(), service, _service_monitor(), _allow()]))
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_drifted_policy_port_behind_a_defaulted_target_port_fails(tmp_path):
    service = _service(ports=[{"name": "http", "port": 8080}])
    policy = _allow(ports=[{"protocol": "TCP", "port": 9090}])
    result = _run(_write(tmp_path, [_deployment(), service, _service_monitor(), policy]))
    assert result.returncode == 1, result.stdout + result.stderr


def test_a_null_target_port_defaults_to_the_port(tmp_path):
    """The API defaults a null targetPort to `port`, so reading it as a port in
    its own right checks a port number that is not there."""
    service = _service(ports=[{"name": "http", "port": 8080, "targetPort": None}])
    result = _run(_write(tmp_path, [_deployment(), service, _service_monitor(), _allow()]))
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_json_policy_is_read(tmp_path):
    """kustomize accepts a JSON resource, so a gate that globs only YAML reds a
    tree whose policy is JSON."""
    directory = tmp_path / "flux"
    directory.mkdir()
    (directory / "manifests.yaml").write_text(
        yaml.safe_dump_all([_deployment(), _service(), _service_monitor()])
    )
    (directory / "networkpolicy.json").write_text(json.dumps(_allow()))
    result = _run(directory)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "scope",
    [{"matchNames": ["other", "another"]}, {"any": True}, {"ownNamespace": True}],
    ids=["two-names", "any", "not-a-field"],
)
def test_a_scrape_scoped_beyond_this_namespace_is_an_error(tmp_path, scope):
    """The policies beside the monitor cover one namespace, so a scrape scoped
    elsewhere cannot be checked here and must not read as a clean run."""
    monitor = _service_monitor()
    monitor["spec"]["namespaceSelector"] = scope
    result = _run(_write(tmp_path, _base(monitor, _allow())))
    assert result.returncode == 2, result.stdout + result.stderr
    assert "namespaceSelector" in result.stderr


@pytest.mark.parametrize(
    "scope,extra",
    [
        ({"any": False}, ()),
        ({"matchNames": ["recipe-box"]}, ("--namespace", "recipe-box")),
    ],
    ids=["any-false", "one-name"],
)
def test_an_own_namespace_monitor_passes(tmp_path, scope, extra):
    """NamespaceSelector has only `any` and `matchNames`, so these are the
    spellings a tenant can write for its own namespace. A matchNames entry is
    credited only against the namespace the caller names."""
    monitor = _service_monitor()
    monitor["spec"]["namespaceSelector"] = scope
    result = _run(_write(tmp_path, _base(monitor, _allow())), *extra)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_matchnames_entry_unverified_by_the_caller_is_an_error(tmp_path):
    """A caller that names no namespace cannot have the entry checked, so the
    gate refuses rather than crediting whatever the monitor claims."""
    monitor = _service_monitor()
    monitor["spec"]["namespaceSelector"] = {"matchNames": ["recipe-box"]}
    result = _run(_write(tmp_path, _base(monitor, _allow())))
    assert result.returncode == 2, result.stdout + result.stderr
    assert "--namespace" in result.stderr


def test_an_empty_policy_types_list_still_credits_the_scrape(tmp_path):
    """`policyTypes: []` is omitempty, so the API reads it as the absent field
    and infers Ingress from the rules; refusing it reds a wired repo."""
    policy = _allow()
    policy["spec"]["policyTypes"] = []
    result = _run(_write(tmp_path, _base(_service_monitor(), policy)))
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_scrape_allow_with_no_monitor_is_an_error(tmp_path):
    """The policy is the repo's own statement that something here is scraped, so
    a vanished monitor is drift, not an empty check."""
    result = _run(_write(tmp_path, [_deployment(), _service(), _allow()]))
    assert result.returncode == 2, result.stdout + result.stderr
    assert "not a gate" in result.stderr


def test_a_directory_with_no_kinded_manifest_is_an_error(tmp_path):
    """Exit 2: manifests that moved out from under the gate read as a clean pass."""
    directory = tmp_path / "flux"
    directory.mkdir()
    (directory / "notes.yaml").write_text("# no object here\n")
    result = _run(directory)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "not a gate" in result.stderr


def test_a_missing_pyyaml_is_an_operator_error(tmp_path):
    """Exit 2, not 1: a CI image without PyYAML is not a wiring violation."""
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "yaml.py").write_text('raise ImportError("stub")\n')
    result = subprocess.run(
        [sys.executable, str(GATE), str(tmp_path)],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(stub)},
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "PyYAML required" in result.stderr


def test_a_non_utf8_manifest_is_an_operator_error(tmp_path):
    """Exit 2, not 1: a file the gate cannot decode is junk it was pointed at,
    not drifted scrape wiring, and must not surface as a traceback."""
    directory = tmp_path / "flux"
    directory.mkdir()
    (directory / "manifests.yaml").write_bytes(b"kind: \xff\xfeConfigMap\n")
    result = _run(directory)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "unreadable" in result.stderr
    assert "Traceback" not in result.stderr
