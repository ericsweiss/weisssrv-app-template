"""Hold every path a rendered file names to the files that render beside it.

Covers the prose form a Markdown link check cannot see: a path written inline
in a doc, a comment or a Taskfile.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import render_app

# Suffixes whose prose is scanned. A rendered manifest names no repo file.
SCANNED = {".md", ".jinja", ".yml", ".yaml", ".toml", ".cfg", ".py", ".sh", ""}

IGNORED_DIRS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache"}

# The three trees whose membership is answer-driven, so a citation of one is a
# claim about this render. A path in the operator's repo is out of scope.
CITATION = re.compile(
    r"(?<![\w/.-])("
    r"docs/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+\.md"
    r"|scripts/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+\.(?:py|sh)"
    r"|\.github/workflows/[A-Za-z0-9._-]+\.ya?ml"
    r")"
)

# A citation followed by this names the template repo, not the reader's own, so
# the path is not expected to exist in the render. It may wrap onto one more line.
ELSEWHERE = re.compile(r"[ \t#]*(?:\n[ \t#]*)?(?:in |\()?(?:the )?app template")


def citations_missing_targets(root: Path, vendored: set[str] = frozenset()) -> list[str]:
    """-> one problem line per cited path that did not render under `root`."""
    problems: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or IGNORED_DIRS & set(path.parts):
            continue
        relative = path.relative_to(root).as_posix()
        if relative in vendored or path.suffix not in SCANNED:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for match in CITATION.finditer(text):
            cited = match.group(1)
            if (root / cited).exists() or ELSEWHERE.match(text, match.end()):
                continue
            line = text.count("\n", 0, match.start()) + 1
            problems.append(f"{relative}:{line} cites {cited}, which this render does not ship")
    return problems


@pytest.fixture(scope="session")
def vendored() -> frozenset[str]:
    """Library copies carry the library's prose, fixed upstream, not here."""
    return render_app.vendored_render_paths()


@pytest.mark.parametrize("render", ["rendered_a", "rendered_unlike"])
def test_the_render_cites_only_its_own_files(request, render, vendored):
    assert citations_missing_targets(request.getfixturevalue(render), vendored) == []


@pytest.mark.parametrize(
    "name, text, cited",
    [
        (
            "docs/ONBOARDING.md",
            "Read docs/ONBOARDING.md, then scripts/no-such-gate.py.\n",
            ["scripts/no-such-gate.py"],
        ),
        ("note.md", "See docs/CI-SHAPES.md in the app template.\n", []),
        ("gate.py", "# Mirrors docs/CI-SHAPES.md\n#      in the app template.\n", []),
        # One blank comment line more than the wrapped case.
        (
            "gate.py",
            "# Mirrors docs/CI-SHAPES.md\n#\n#      in the app template.\n",
            ["docs/CI-SHAPES.md"],
        ),
        (
            "note.md",
            "See docs/runbooks/restore.md and scripts/lib/helper.py.\n",
            ["docs/runbooks/restore.md", "scripts/lib/helper.py"],
        ),
    ],
    ids=["unrendered", "qualified", "wrapped-qualifier", "qualifier-too-far", "subdirectory"],
)
def test_a_citation_is_held_to_the_render(tmp_path, name, text, cited):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    problems = citations_missing_targets(tmp_path)
    assert len(problems) == len(cited), problems
    for problem, target in zip(problems, cited, strict=True):
        assert target in problem


def test_the_vendored_exemption_covers_copies_only(vendored):
    """A fork's prose is this repo's to keep right."""
    assert "scripts/check-doc-links.py" in vendored
    assert "ruff.toml" not in vendored


def test_a_jinja_copy_is_exempt_under_its_rendered_name(tmp_path):
    manifest = tmp_path / "vendored-manifest.yml"
    manifest.write_text(
        "vendored:\n"
        "  - consumer: template/docs/x.md.jinja\n"
        "  - consumer: \"template/{% if a %}y.md{% endif %}.jinja\"\n"
        "  - consumer: scripts/z.py\n"
        "forked:\n"
        "  - consumer: template/w.md\n",
        encoding="utf-8",
    )
    assert render_app.vendored_render_paths(manifest) == {"docs/x.md", "y.md"}


@pytest.fixture(scope="session")
def rendered_a(tmp_path_factory) -> Path:
    return render_app.render(tmp_path_factory.mktemp("citations-a"))


@pytest.fixture(scope="session")
def rendered_unlike(tmp_path_factory) -> Path:
    return render_app.render(
        tmp_path_factory.mktemp("citations-b"),
        answers=render_app.ANSWERS_B,
        dest_name="render-b",
    )
