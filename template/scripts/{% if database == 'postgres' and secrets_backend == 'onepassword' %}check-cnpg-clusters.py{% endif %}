#!/usr/bin/env python3
"""Assert every CloudNativePG Cluster is archived, retained, drainable and kept.
Reads the rendered corpus on stdin; exit 0 clean, 1 findings, 2 operator error.
Contract and flags: docs/SCRIPTS.md.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    from gate_common import (  # noqa: E402  (resolved from this script's own directory)
        doc_key,
        doc_namespace,
        load_corpus,
    )
except ImportError:
    print(
        "ERROR: gate_common.py must be vendored beside this gate "
        "(see scripts/vendorable-paths.yml)", file=sys.stderr,
    )
    raise SystemExit(2) from None

CNPG_API = "postgresql.cnpg.io/"
PLUGIN_API = "barmancloud.cnpg.io/"
BARMAN_PLUGIN = "barman-cloud.cloudnative-pg.io"
PRUNE_ANNOTATION = "kustomize.toolkit.fluxcd.io/prune"
_WINDOW = re.compile(r"^(\d+)([dwm])$")
_UNIT_DAYS = {"d": 1, "w": 7, "m": 30}


def retention_days(policy: object) -> int | None:
    """Days in a Barman recovery-window policy such as `30d`, else None."""
    match = _WINDOW.match(str(policy).strip()) if policy is not None else None
    return int(match.group(1)) * _UNIT_DAYS[match.group(2)] if match else None


def _of(docs: list[dict], api: str, kind: str) -> list[dict]:
    return [
        d for d in docs
        if str(d.get("apiVersion", "")).startswith(api) and d.get("kind") == kind
    ]


def _plugin_store(spec: dict) -> str | None:
    """The ObjectStore an enabled WAL-archiving Barman plugin entry names ("" for none), else None."""
    for plugin in spec.get("plugins") or []:
        if (isinstance(plugin, dict) and plugin.get("name") == BARMAN_PLUGIN
                and plugin.get("enabled", True) is not False
                and plugin.get("isWALArchiver") is True):
            name = (plugin.get("parameters") or {}).get("barmanObjectName")
            return str(name) if name else ""
    return None


def _window(days: int | None) -> str:
    return f"{days} day(s)" if days is not None else "no recovery window"


def _by_plugin(schedule: dict) -> bool:
    """True when a ScheduledBackup runs through the Barman Cloud plugin."""
    return (schedule.get("method") == "plugin"
            and (schedule.get("pluginConfiguration") or {}).get("name") == BARMAN_PLUGIN)


def violations(docs: list[dict], min_days: int) -> tuple[list[str], int]:
    """-> (violations, clusters inspected)."""
    clusters = _of(docs, CNPG_API, "Cluster")
    stores = {
        (doc_namespace(d), (d.get("metadata") or {}).get("name")): d
        for d in _of(docs, PLUGIN_API, "ObjectStore")
    }
    scheduled: dict[tuple, list[dict]] = {}
    for d in _of(docs, CNPG_API, "ScheduledBackup"):
        sched = d.get("spec") or {}
        if not sched.get("suspend"):
            key = (doc_namespace(d), (sched.get("cluster") or {}).get("name"))
            scheduled.setdefault(key, []).append(sched)
    out: list[str] = []
    for cluster in clusters:
        where = doc_key(cluster)
        meta = cluster.get("metadata") or {}
        spec = cluster.get("spec") or {}
        namespace = doc_namespace(cluster)
        store_name = _plugin_store(spec)
        in_tree = (spec.get("backup") or {}).get("barmanObjectStore")
        store = stores.get((namespace, store_name)) if store_name else None
        if store_name == "":
            out.append(f"  {where}: Barman plugin entry names no barmanObjectName")
        elif store_name is not None and store is None:
            out.append(f"  {where}: names ObjectStore {store_name!r}, which the corpus does not ship")
        elif store is not None:
            days = retention_days((store.get("spec") or {}).get("retentionPolicy"))
            if days is None or days < min_days:
                out.append(f"  {where}: ObjectStore {store_name} keeps {_window(days)}, under the {min_days}-day floor")
            if ((store.get("metadata") or {}).get("annotations") or {}).get(PRUNE_ANNOTATION) != "disabled":
                out.append(f"  {where}: ObjectStore {store_name} has no {PRUNE_ANNOTATION}: disabled, "
                           "so removing the file deletes the backup target")
        elif in_tree:
            days = retention_days((spec.get("backup") or {}).get("retentionPolicy"))
            if days is None or days < min_days:
                out.append(f"  {where}: backup.retentionPolicy keeps {_window(days)}, under the {min_days}-day floor")
        else:
            out.append(f"  {where}: no WAL archiver, so point-in-time recovery is impossible")
        schedules = scheduled.get((namespace, meta.get("name")), [])
        if not schedules:
            out.append(f"  {where}: no ScheduledBackup targets this cluster (a suspended one does not count)")
        elif store_name is not None and not any(_by_plugin(s) for s in schedules):
            out.append(f"  {where}: no ScheduledBackup with method: plugin and "
                       f"pluginConfiguration.name {BARMAN_PLUGIN} targets this plugin-archived cluster")
        elif store_name is None and in_tree and not any(
                s.get("method", "barmanObjectStore") == "barmanObjectStore" for s in schedules):
            out.append(f"  {where}: no ScheduledBackup with method barmanObjectStore (the default) "
                       "targets this in-tree-archived cluster")
        raw = spec.get("instances", 1)
        if isinstance(raw, bool) or not isinstance(raw, int):
            out.append(f"  {where}: spec.instances {raw!r} is not an integer "
                       "(pipe the post-substitution build)")
            instances = None
        else:
            instances = raw
        if instances == 1 and spec.get("enablePDB", True) is not False:
            out.append(f"  {where}: one instance with a PodDisruptionBudget blocks every node drain; set enablePDB: false")
        if (meta.get("annotations") or {}).get(PRUNE_ANNOTATION) != "disabled":
            out.append(f"  {where}: no {PRUNE_ANNOTATION}: disabled, so removing the file deletes the database")
    return out, len(clusters)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--min-retention-days", type=int, default=30,
                        help="recovery-window floor in days (default 30, at least 1)")
    args = parser.parse_args(argv)
    if args.min_retention_days < 1:
        parser.error("--min-retention-days must be at least 1")
    docs = load_corpus()
    found, seen = violations(docs, args.min_retention_days)
    if found:
        print("CloudNativePG clusters missing a backup, drain or prune safeguard:", file=sys.stderr)
        print("\n".join(found), file=sys.stderr)
        return 1
    if not seen:
        print(
            f"ERROR: inspected 0 CloudNativePG Clusters in {len(docs)} document(s); "
            "a gate that checks nothing is not a gate. Pipe the build that declares them.",
            file=sys.stderr,
        )
        return 2
    print(f"CloudNativePG policy OK: {seen} cluster(s) archived, retained, drainable and kept")
    return 0


if __name__ == "__main__":
    sys.exit(main())
