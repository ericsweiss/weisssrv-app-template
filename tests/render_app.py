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

# What CI's render-validate job creates inside the project directory: a full
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
    return yaml.load(path.read_text(), Loader=CILoader)


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
