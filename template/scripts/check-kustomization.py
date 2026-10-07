#!/usr/bin/env python3
"""Assert kustomization.yaml lists every manifest, and lists at least one.

An unlisted manifest is inert, an emptied list prunes every applied object.
Exits 0 clean, 1 on a violation, 2 on an operator error or no kustomization.yaml.
"""

from __future__ import annotations

import argparse
import pathlib
import posixpath
import sys

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required: pip install pyyaml", file=sys.stderr)
    raise SystemExit(2) from None

KUSTOMIZATION_NAMES = ("kustomization.yaml", "kustomization.yml")
# A Component is applied into its parent's render and may contribute through any
# of these keys, so the resources-only check below does not apply to one.
CONTENT_KEYS = (
    "resources", "bases", "components", "patches", "patchesStrategicMerge",
    "patchesJson6902", "configMapGenerator", "secretGenerator", "replacements",
    "images", "labels", "helmCharts",
)


class GateError(Exception):
    """An operator error: the gate could not run, so it proves nothing."""


def is_local(name: str) -> bool:
    """A remote base is fetched by kustomize, so it is not a path on disk."""
    if name.startswith(("git::", "git@", "ssh://")) or "://" in name:
        return False
    if "?" in name:  # ?ref=, ?timeout=, ?submodules=
        return False
    head, separator, _ = name.partition("//")
    if separator and head and not head.startswith("."):  # host/org/repo//path
        return False
    return not any(part.endswith(".git") for part in name.split("/"))


def normalize(name: str) -> str:
    """kustomize treats `./foo.yaml`, `foo.yaml` and `foo/` as one path."""
    return posixpath.normpath(name)


def referenced_paths(doc: dict) -> set[str]:
    """Every path kustomization.yaml names, however it names it."""
    referenced = {
        normalize(n) for n in doc.get("resources") or [] if isinstance(n, str) and is_local(n)
    }
    for key in ("bases", "components", "crds", "transformers",
                "generators", "configurations"):
        referenced |= {
            normalize(e) for e in doc.get(key) or [] if isinstance(e, str) and is_local(e)
        }
    for entry in doc.get("patchesStrategicMerge") or []:
        # kustomize accepts an inline patch document here as well as a path.
        if isinstance(entry, str) and "\n" not in entry:
            referenced.add(normalize(entry))
    for key in ("patches", "patchesJson6902", "replacements"):
        for entry in doc.get(key) or []:
            path = entry.get("path") if isinstance(entry, dict) else entry
            if isinstance(path, str):
                referenced.add(normalize(path))
    for key in ("configMapGenerator", "secretGenerator"):
        for generator in doc.get(key) or []:
            if not isinstance(generator, dict):
                continue
            for field in ("files", "envs"):
                for entry in generator.get(field) or []:
                    if isinstance(entry, str):
                        referenced.add(normalize(entry.split("=", 1)[-1]))
    for chart in doc.get("helmCharts") or []:
        values = chart.get("valuesFile") if isinstance(chart, dict) else None
        if isinstance(values, str):
            referenced.add(normalize(values))
    openapi = doc.get("openapi")
    if isinstance(openapi, dict) and isinstance(openapi.get("path"), str):
        referenced.add(normalize(openapi["path"]))
    return referenced


def manifests_on_disk(base: pathlib.Path, referenced: set[str]) -> set[str]:
    """Manifests under the directory, minus anything a listed directory covers."""
    listed_dirs = [n for n in referenced if (base / n).is_dir()]
    on_disk = set()
    for extension in ("*.yaml", "*.yml", "*.json"):
        for path in base.rglob(extension):
            relative = path.relative_to(base).as_posix()
            if relative in KUSTOMIZATION_NAMES:
                continue
            if any(relative.startswith(directory + "/") for directory in listed_dirs):
                continue
            on_disk.add(relative)
    return on_disk


def carries_an_object(path: pathlib.Path) -> bool:
    """A manifest whose documents hold no `kind` renders nothing."""
    try:
        with path.open(encoding="utf-8") as handle:
            return any(
                isinstance(doc, dict) and doc.get("kind")
                for doc in yaml.safe_load_all(handle)
            )
    except (OSError, UnicodeDecodeError) as error:
        raise GateError(f"{path}: unreadable: {error}") from error
    except yaml.YAMLError as error:
        raise GateError(f"{path}: unparseable YAML: {error}") from error


def load(base: pathlib.Path) -> dict | None:
    """The directory's kustomization, or None when it has none."""
    for name in KUSTOMIZATION_NAMES:
        path = base / name
        if path.is_file():
            try:
                # The handle, not the text: PyYAML names the stream in its mark,
                # so a parse error points at the file, not at "<unicode string>".
                with path.open(encoding="utf-8") as handle:
                    loaded = yaml.safe_load(handle) or {}
            except (OSError, UnicodeDecodeError) as error:
                raise GateError(f"{path}: unreadable: {error}") from error
            except yaml.YAMLError as error:
                raise GateError(f"{path}: unparseable YAML: {error}") from error
            if not isinstance(loaded, dict):
                raise GateError(f"{path}: kustomization.yaml is not a mapping")
            return loaded
    return None


def check(
    base: pathlib.Path, visited: set[pathlib.Path], root: bool = True
) -> tuple[list[str], int]:
    """Problems with one directory and every directory it lists, and its resource count."""
    resolved = base.resolve()
    if resolved in visited:
        return [], 0
    visited.add(resolved)

    doc = load(base)
    if doc is None:
        absent = f"{base / 'kustomization.yaml'} does not exist: kustomize builds nothing here"
        if root:
            raise GateError(absent)
        return [absent], 0

    listed = [
        name
        for key in ("resources", "bases")
        for name in doc.get(key) or []
        if isinstance(name, str)
    ]
    if doc.get("kind") == "Component" and not root:
        # A Component legally carries patches only; an emptied one is inert.
        if not any(doc.get(key) for key in CONTENT_KEYS):
            return [f"{base}: the Component contributes nothing, so it is inert"], 0
    elif not listed:
        return [f"{base}: kustomization.yaml lists no resources, so the render is empty"], 0

    problems = []
    referenced = referenced_paths(doc)
    missing = sorted(
        f"{base / name}" for name in referenced if is_local(name) and not (base / name).exists()
    )
    if missing:
        problems.append(f"kustomization.yaml names paths that do not exist: {missing}")
    empty = sorted(
        f"{base / name}"
        for name in listed
        if is_local(name) and (base / name).is_file() and not carries_an_object(base / name)
    )
    if empty:
        problems.append(
            "manifests that carry no object, so kustomize renders nothing from them "
            f"and the cluster prunes what they used to apply: {empty}"
        )
    unlisted = sorted(f"{base / name}" for name in manifests_on_disk(base, referenced) - referenced)
    if unlisted:
        problems.append(f"manifests present but not listed, so Flux never builds them: {unlisted}")
    for name in sorted(referenced):
        child = base / name
        if is_local(name) and child.is_dir():
            problems += check(child, visited, root=False)[0]
    return problems, len(listed)


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
    try:
        problems, count = check(base, set())
    except GateError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1

    print(f"kustomization.yaml lists {count} resources; no manifest is unlisted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
