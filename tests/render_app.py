"""Render this template into a throwaway directory.

The copier invocation is the library's vendored tests/copier_render.py; what
stays here is this repository's own.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import yaml

import copier_render

REPO_ROOT = Path(__file__).resolve().parent.parent
ANSWERS = REPO_ROOT / "tests" / "answers-weisssrv-shaped.yml"
ANSWERS_B = REPO_ROOT / "tests" / "answers-unlike.yml"
VENDORED_MANIFEST = REPO_ROOT / "scripts" / "vendored-manifest.yml"

# What CI's validate-rendered-app job creates inside the project directory: a full
# library clone and the extracted kustomize/kubeconform tarballs. Copying
# either into the template source is pointless I/O.
_EXTRA_IGNORE = (".tmp", ".bin")


def declared_lib_ref(path: Path, input_name: str = "lib_ref") -> str | None:
    """The library tag a pipeline declares: `spec.inputs.<input_name>.default`."""
    entry = ci_yaml.spec_inputs(path.read_text()).get(input_name)
    return entry.get("default") if isinstance(entry, dict) else None


def load_gate(name: str, directory: str):
    """Import a hyphenated gate from `directory` by path."""
    path = REPO_ROOT / directory / name
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The pipeline parser this repo vendors from the library.
ci_yaml = load_gate("ci_yaml.py", "scripts")
CILoader = ci_yaml.CILoader
load_ci = ci_yaml.load_ci


def runner_tagged(ci: dict) -> tuple[list[str], list[str]]:
    """The include files that pass a `tags` input, and the local jobs that set `tags`.
    Asserts every include's `inputs`, when present, is a mapping."""
    bare = [str(include["file"]) for include in ci["include"] if isinstance(include, dict)
            and "inputs" in include and not isinstance(include["inputs"], dict)]
    assert not bare, f"includes whose `inputs` is not a mapping: {bare}"
    includes = [
        str(include["file"]) for include in ci["include"]
        if isinstance(include, dict) and "tags" in (include.get("inputs") or {})
    ]
    jobs = [name for name, body in ci.items() if isinstance(body, dict) and "tags" in body]
    return includes, jobs


def rendered_name(consumer: str) -> str:
    """The path a template file renders to: jinja tags, empty segments and the
    `.jinja` suffix dropped. A conditional name's tags wrap the whole name."""
    bare = re.sub(r"\{%.*?%\}", "", consumer)
    return "/".join(part for part in bare.split("/") if part).removesuffix(".jinja")


def consumer_paths(
    sections: tuple[str, ...] = ("vendored", "forked"),
    document: dict | None = None,
) -> list[str]:
    """Every consumer path the manifest's `sections` name, in either entry form.
    `document`, when given, stands in for the parsed VENDORED_MANIFEST."""
    if document is None:
        document = yaml.safe_load(VENDORED_MANIFEST.read_text(encoding="utf-8")) or {}
    return [
        entry if isinstance(entry, str) else entry["consumer"]
        for section in sections
        for entry in document.get(section) or []
    ]


def vendored_render_paths(manifest: Path = VENDORED_MANIFEST) -> frozenset[str]:
    """Every `vendored:` copy under template/, as a path relative to a render.

    A declared fork diverges on purpose, so it is scanned as this repo's own file.
    """
    document = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    return frozenset(
        rendered_name(consumer[len("template/") :])
        for consumer in consumer_paths(("vendored",), document)
        if consumer.startswith("template/")
    )


def copy_source(scratch: Path) -> Path:
    return copier_render.copy_source(REPO_ROOT, scratch, extra_ignore=_EXTRA_IGNORE)


def answers_without(scratch: Path, name: str) -> Path:
    """Write ANSWERS minus `name` into `scratch`, so only its default decides it."""
    answers = yaml.safe_load(ANSWERS.read_text(encoding="utf-8"))
    del answers[name]
    path = scratch / "answers-without.yml"
    path.write_text(yaml.safe_dump(answers), encoding="utf-8")
    return path


def render(
    scratch: Path,
    answers: Path = ANSWERS,
    dest_name: str = "render",
    data: dict[str, str] | None = None,
) -> Path:
    """Render the working tree with `answers`; return the generated repo root."""
    return copier_render.render(
        REPO_ROOT,
        scratch,
        answers=answers,
        dest_name=dest_name,
        data=data,
        extra_ignore=_EXTRA_IGNORE,
    )


if __name__ == "__main__":
    raise SystemExit(copier_render.cli_main(REPO_ROOT, ANSWERS, "app-template-"))
