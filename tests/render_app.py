"""Render this template into a throwaway directory.

The copier invocation is the library's vendored tests/copier_render.py; what
stays here is this repository's own.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import copier_render

REPO_ROOT = Path(__file__).resolve().parent.parent
ANSWERS = REPO_ROOT / "tests" / "answers-weisssrv-shaped.yml"
ANSWERS_B = REPO_ROOT / "tests" / "answers-unlike.yml"

# What CI's validate-rendered-app job creates inside the project directory: a full
# library clone and the extracted kustomize/kubeconform tarballs. Copying
# either into the template source is pointless I/O.
_EXTRA_IGNORE = (".tmp", ".bin")


class CILoader(yaml.SafeLoader):
    """GitLab CI YAML carries `!reference [...]`, which SafeLoader rejects.

    A SafeLoader subclass: the added constructor degrades ANY unknown tag to its
    plain sequence value (or None), so no object is ever constructed.
    """


CILoader.add_multi_constructor(
    "",
    lambda loader, suffix, node: (
        loader.construct_sequence(node, deep=True)
        if isinstance(node, yaml.SequenceNode)
        else None
    ),
)


def load_ci(path: Path) -> dict:
    return parse_ci(path.read_text())


def parse_ci(text: str) -> dict:
    """The jobs document of pipeline YAML already in memory.

    GitLab's inputs syntax makes a pipeline file two documents, `spec:` then the
    jobs, so the last mapping is the one a caller wants.
    """
    documents = [d for d in yaml.load_all(text, Loader=CILoader) if isinstance(d, dict)]
    return documents[-1] if documents else {}


def declared_lib_ref(path: Path, input_name: str = "lib_ref") -> str | None:
    """The library tag a pipeline declares: `spec.inputs.<input_name>.default`.

    The single source every `ref:` below it reads, because an input interpolates
    before `include:` resolves and a variable does not exist there yet.
    """
    documents = [
        d for d in yaml.load_all(path.read_text(), Loader=CILoader) if isinstance(d, dict)
    ]
    header = next((d for d in documents if "spec" in d), None)
    inputs = ((header or {}).get("spec") or {}).get("inputs") or {}
    entry = inputs.get(input_name)
    return entry.get("default") if isinstance(entry, dict) else None


def load_gate(name: str, directory: str):
    """Import a hyphenated gate from `directory` by path."""
    path = REPO_ROOT / directory / name
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def copy_source(scratch: Path) -> Path:
    return copier_render.copy_source(REPO_ROOT, scratch, extra_ignore=_EXTRA_IGNORE)


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
