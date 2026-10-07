#!/usr/bin/env python3
"""Assert the scrape NetworkPolicy admits Prometheus on the port the monitor scrapes.

An unmodelled peer or selector shape is not credited and is named in the failure.
Exits 0 clean, 1 on a violation, 2 on an operator error or an empty corpus.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(2) from None

SCRAPE_NAMESPACE = "observability"
WORKLOAD_KINDS = ("Deployment", "StatefulSet", "DaemonSet")
ENDPOINT_KEYS = {"PodMonitor": "podMetricsEndpoints", "ServiceMonitor": "endpoints"}

# Policy shapes this gate only partly models, named in the failure message.
UNMODELLED: list[str] = []


class GateError(Exception):
    """An operator error: the gate could not run, so it proves nothing."""


class Violation(Exception):
    """The scrape wiring in this repo is wrong."""


def note(message: str) -> None:
    if message not in UNMODELLED:
        UNMODELLED.append(message)


def describe(document: dict) -> str:
    """The document as a failure message names it."""
    metadata = document.get("metadata")
    name = metadata.get("name") if isinstance(metadata, dict) else None
    return f"{document.get('kind')} {name or '<unnamed>'}"


def mapping(value: object, subject: str, field: str) -> dict:
    """A field the gate reads as a mapping; a scalar or list there cannot be checked."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise GateError(f"{subject}: {field} is not a mapping")
    return value


def load_documents(base: pathlib.Path) -> tuple[list[dict], list[str]]:
    """Every kinded document under the directory, and the files that would not read.

    kustomize accepts a JSON resource, so JSON is read as well as YAML.
    """
    documents: list[dict] = []
    errors: list[str] = []
    for extension in ("*.yaml", "*.yml", "*.json"):
        for path in sorted(base.rglob(extension)):
            try:
                # The handle, not the text: PyYAML names the stream in its mark,
                # so a parse error points at the file, not at "<unicode string>".
                with path.open(encoding="utf-8") as handle:
                    loaded = list(yaml.safe_load_all(handle))
            except (OSError, UnicodeDecodeError) as error:
                errors.append(f"{path}: unreadable: {error}")
                continue
            except yaml.YAMLError as error:
                errors.append(f"{path}: unparseable YAML: {error}")
                continue
            documents += [d for d in loaded if isinstance(d, dict) and d.get("kind")]
    return documents, errors


def matching_workloads(
    documents: list[dict], pods: dict
) -> tuple[dict[str, int], set[int], dict]:
    """Port names, port numbers and widened labels of the workloads `pods` selects.

    A policy naming a label beyond the Service selector still admits the
    scrape, so the subset test runs against the pod labels, not the selector.
    """
    names: dict[str, int] = {}
    numbers: set[int] = set()
    shared: dict | None = None
    for document in documents:
        if document.get("kind") not in WORKLOAD_KINDS:
            continue
        subject = describe(document)
        spec = mapping(document.get("spec"), subject, "spec")
        template = mapping(spec.get("template"), subject, "spec.template")
        labels = mapping(
            mapping(template.get("metadata"), subject, "spec.template.metadata").get("labels"),
            subject,
            "spec.template.metadata.labels",
        )
        if not pods.items() <= labels.items():
            continue
        if shared is None:
            shared = dict(labels)
        else:
            shared = {key: value for key, value in shared.items() if labels.get(key) == value}
        pod = mapping(template.get("spec"), subject, "spec.template.spec")
        for container in pod.get("containers") or []:
            for port in container.get("ports") or []:
                number = port.get("containerPort")
                if not number:
                    continue
                numbers.add(number)
                if port.get("name"):
                    names[port["name"]] = number
    return names, numbers, {**pods, **(shared or {})}


def spellings(port: int | str, names: dict[str, int]) -> set[int | str]:
    """A port as a NetworkPolicy may legally name it: the number and its name."""
    number = names.get(port, port)
    return {number} | {name for name, value in names.items() if value == number}


def services_for(documents: list[dict], selector: dict) -> list[dict]:
    """Every Service carrying the monitor's labels."""
    matched = []
    for document in documents:
        if document.get("kind") != "Service":
            continue
        metadata = mapping(document.get("metadata"), describe(document), "metadata")
        labels = mapping(metadata.get("labels"), describe(document), "metadata.labels")
        if selector.items() <= labels.items():
            matched.append(document)
    return matched


def resolve_target(
    documents: list[dict], pods: dict, port: int | str, name: str
) -> tuple[set[int | str], dict]:
    """The pod port's spellings and the labels of the pods that serve it."""
    names, numbers, labels = matching_workloads(documents, pods)
    if port is None:
        raise Violation(f"{name}: a Service port names no pod port, so nothing is scraped")
    if isinstance(port, str) and port not in names:
        raise Violation(f"{name}: no workload carrying {pods} declares a port named {port!r}")
    if isinstance(port, int) and port not in numbers:
        raise Violation(f"{name}: no workload carrying {pods} declares a port {port}")
    return spellings(port, names), labels


def pod_port(
    documents: list[dict], selector: dict, endpoint: dict, kind: str, name: str
) -> list[tuple[set[int | str], dict, str]]:
    """One (port spellings, pod labels, subject) triple per target this endpoint creates.

    prometheus-operator scrapes EVERY Service a ServiceMonitor selects, so a
    policy proven for one of them says nothing about the rest.
    """
    named = endpoint.get("port")
    target = endpoint.get("targetPort")
    # `portNumber` is a PodMonitor-only spelling of the container port.
    number = endpoint.get("portNumber") if kind == "PodMonitor" else None
    if named is None and target is None and number is None:
        if kind == "PodMonitor":
            raise GateError(
                f"{name}: a PodMonitor endpoint names none of `port`, `portNumber` "
                "or `targetPort`"
            )
        raise GateError(f"{name}: an endpoint names neither `port` nor `targetPort`")

    if kind == "PodMonitor":
        # A PodMonitor names a container port directly: there is no Service hop.
        direct = named if named is not None else (number if number is not None else target)
        return [(*resolve_target(documents, selector, direct, name), "")]

    # A ServiceMonitor's selector picks SERVICES, so the pods are the ones each
    # chosen Service selects, not whatever carries the monitor labels.
    services = services_for(documents, selector)
    if not services:
        raise Violation(
            f"{name}: no Service carries the monitor's labels {selector}: "
            "the scrape has no target"
        )
    targets = []
    for service in services:
        subject = f" (Service {(service.get('metadata') or {}).get('name')})"
        service_spec = mapping(service.get("spec"), describe(service), "spec")
        pods = mapping(service_spec.get("selector"), describe(service), "spec.selector") or selector
        if named is None:
            # `targetPort` names a pod port directly, by name or by number.
            targets.append((*resolve_target(documents, pods, target, name), subject))
            continue
        for port in service_spec.get("ports") or []:
            if port.get("name") == named:
                # An absent or null targetPort defaults to the Service port.
                resolved = port.get("targetPort") or port.get("port")
                targets.append((*resolve_target(documents, pods, resolved, name), subject))
                break
    # A Service without the named port is no target, so only a monitor whose
    # every selected Service lacks it has nothing to scrape.
    if not targets:
        raise Violation(f"{name}: no Service carrying {selector} declares a port named {named!r}")
    return targets


def scrape_allow(documents: list[dict]) -> str | None:
    """The name of a NetworkPolicy admitting the observability namespace, if any.

    It is the repo's own statement that something here is scraped, so it stands
    in for the answer that rendered the monitor.
    """
    for document in documents:
        if document.get("kind") != "NetworkPolicy":
            continue
        spec = mapping(document.get("spec"), describe(document), "spec")
        for rule in spec.get("ingress") or []:
            for peer in rule.get("from") or []:
                namespaces = peer.get("namespaceSelector") or {}
                labels = namespaces.get("matchLabels") or {}
                if labels.get("kubernetes.io/metadata.name") == SCRAPE_NAMESPACE:
                    return (document.get("metadata") or {}).get("name")
    return None


def admits(policy: dict, pods: dict, accepted: set[int | str]) -> bool:
    """Does this NetworkPolicy let the observability namespace in on the port?"""
    spec = mapping(policy.get("spec"), describe(policy), "spec")
    rules = spec.get("ingress") or []
    declared = spec.get("policyTypes")
    # The API infers Ingress from the presence of `ingress:` when policyTypes is
    # absent, and an empty list round-trips as absent, so it infers too.
    if not ("Ingress" in declared if declared else bool(rules)):
        return False
    selected = mapping(spec.get("podSelector"), describe(policy), "spec.podSelector")
    name = (policy.get("metadata") or {}).get("name")
    # An empty podSelector selects every pod; matchExpressions is not modelled,
    # so a policy spelled that way is not credited with admitting the scrape.
    if set(selected) - {"matchLabels"}:
        note(f"{name}: its podSelector uses matchExpressions, which this gate does not model")
        return False
    if not (selected.get("matchLabels") or {}).items() <= pods.items():
        return False
    for rule in rules:
        peers = rule.get("from") or []
        # An absent or empty `from` matches every source, observability included.
        scoped = not peers
        for peer in peers:
            namespaces = peer.get("namespaceSelector")
            if namespaces is None:
                # An ingress ipBlock covering the observability pod CIDR does admit
                # the scrape, but this gate resolves no CIDRs.
                if peer.get("ipBlock"):
                    note(
                        f"{name}: its scrape peer is an ipBlock, which this gate does not "
                        "resolve against the observability pod CIDR"
                    )
                continue
            labels = namespaces.get("matchLabels") or {}
            if labels.get("kubernetes.io/metadata.name") == SCRAPE_NAMESPACE:
                if peer.get("podSelector"):
                    note(
                        f"{name}: its scrape peer scopes the namespace with a podSelector "
                        "this gate does not model"
                    )
                    continue
                scoped = True
            # An empty namespaceSelector selects every namespace, observability
            # included, but only a peer with no podSelector admits every pod.
            elif not namespaces:
                if peer.get("podSelector"):
                    note(
                        f"{name}: its scrape peer combines an empty namespaceSelector with "
                        "a podSelector, which this gate does not model"
                    )
                else:
                    scoped = True
            elif set(namespaces) - {"matchLabels"}:
                note(
                    f"{name}: its scrape peer selects the namespace with matchExpressions, "
                    "which this gate does not model"
                )
            elif "kubernetes.io/metadata.name" not in labels:
                note(
                    f"{name}: its scrape peer selects the namespace by labels other than "
                    "kubernetes.io/metadata.name, which this gate does not resolve to a name"
                )
        if not scoped:
            continue
        ports = rule.get("ports")
        # An absent or empty `ports` matches every port.
        if not ports:
            return True
        for entry in ports:
            if not isinstance(entry, dict):
                continue
            if (entry.get("protocol") or "TCP") != "TCP":
                continue
            # An entry with no `port` matches every port for the protocol.
            if "port" not in entry:
                return True
            low = entry.get("port")
            high = entry.get("endPort")
            if low in accepted:
                return True
            # `endPort` makes a numeric `port` the low end of a range.
            if isinstance(low, int) and isinstance(high, int) and any(
                isinstance(value, int) and low <= value <= high for value in accepted
            ):
                return True
    return False


def check(documents: list[dict]) -> int:
    """Every monitor's scraped port, against the policies beside it."""
    monitors = [d for d in documents if d.get("kind") in ENDPOINT_KEYS]
    if not monitors:
        declared = scrape_allow(documents)
        if declared:
            raise GateError(
                f"{declared} admits namespace {SCRAPE_NAMESPACE} but no ServiceMonitor "
                "or PodMonitor is present: a gate that checks nothing is not a gate. "
                "Restore the monitor, or remove the scrape allow with it."
            )
        print(
            f"{len(documents)} document(s), no ServiceMonitor or PodMonitor; "
            "nothing to check"
        )
        return 0

    policies = [d for d in documents if d.get("kind") == "NetworkPolicy"]
    for monitor in monitors:
        kind = monitor["kind"]
        name = (monitor.get("metadata") or {}).get("name")
        spec = mapping(monitor.get("spec"), describe(monitor), "spec")
        scope = mapping(spec.get("namespaceSelector"), describe(monitor), "spec.namespaceSelector")
        names = scope.get("matchNames") or []
        # NamespaceSelector carries `any` and `matchNames` only, and the tree names
        # no namespace, so one matchNames entry reads as this directory's own.
        if set(scope) - {"any", "matchNames"} or scope.get("any") or len(names) > 1:
            raise GateError(
                f"{name}: spec.namespaceSelector scopes the scrape outside this directory, "
                "whose NetworkPolicies cover one namespace only. Leave it out, or scope it "
                "to this namespace with `any: false` or a single `matchNames` entry; a wider "
                "scrape must be checked where those policies live."
            )
        selector = mapping(
            mapping(spec.get("selector"), describe(monitor), "spec.selector").get("matchLabels"),
            describe(monitor),
            "spec.selector.matchLabels",
        )
        if not selector:
            raise GateError(
                f"{name}: the monitor selects every pod, so no policy can be matched to it"
            )
        endpoints = spec.get(ENDPOINT_KEYS[kind]) or []
        if not endpoints:
            raise GateError(f"{name}: the monitor declares no endpoints, so nothing is checked")
        for endpoint in endpoints:
            for accepted, pods, subject in pod_port(documents, selector, endpoint, kind, name):
                if any(admits(policy, pods, accepted) for policy in policies):
                    continue
                numbers = sorted(v for v in accepted if not isinstance(v, str))
                port = numbers[0] if numbers else sorted(accepted)[0]
                message = (
                    f"{name}{subject}: no NetworkPolicy admits namespace {SCRAPE_NAMESPACE} "
                    f"on port {port}, so Prometheus cannot reach this target"
                )
                raise Violation("\n".join([message, *UNMODELLED]))

    print(f"{len(monitors)} monitor(s); every scraped port is admitted from {SCRAPE_NAMESPACE}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "directory",
        nargs="?",
        default="kubernetes/flux",
        help="manifest directory to check (default: %(default)s)",
    )
    arguments = parser.parse_args()

    base = pathlib.Path(arguments.directory)
    if not base.is_dir():
        print(
            f"ERROR: {base} is not a directory: the gate was pointed at a path that "
            "does not exist",
            file=sys.stderr,
        )
        return 2

    documents, errors = load_documents(base)
    if errors:
        print("ERROR: a manifest could not be read:", file=sys.stderr)
        print("\n".join(errors), file=sys.stderr)
        return 2
    if not documents:
        print(
            f"ERROR: no manifest under {base} carries a `kind` — a gate that checks "
            "nothing is not a gate; check that the manifests are still there.",
            file=sys.stderr,
        )
        return 2

    try:
        return check(documents)
    except GateError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    except Violation as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
