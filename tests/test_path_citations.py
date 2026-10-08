"""Hold every path a rendered file names to the files that render beside it.

Covers the prose form a Markdown link check cannot see: a path written inline
in a doc, a comment or a Taskfile.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

import render_app

REPO_ROOT = render_app.REPO_ROOT
MANIFEST = REPO_ROOT / "scripts" / "vendored-manifest.yml"

# Suffixes whose prose is scanned. A rendered manifest names no repo file.
SCANNED = {".md", ".jinja", ".yml", ".yaml", ".toml", ".cfg", ".py", ".sh", ""}

IGNORED_DIRS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache"}

# The three trees whose membership is answer-driven, so a citation of one is a
# claim about this render. Anything else (kubernetes/clusters/... in the
# operator's repo) is deliberately out of scope.
CITATION = re.compile(
    r"(?<![\w/.-])("
    r"docs/[A-Za-z0-9._-]+\.md"
    r"|scripts/[A-Za-z0-9._-]+\.(?:py|sh)"
    r"|\.github/workflows/[A-Za-z0-9._-]+\.ya?ml"
    r")"
)

# A citation followed by this names the template repo, not the reader's own, so
# the path is not expected to exist in the render.
ELSEWHERE = re.compile(r"\A[\s#]*(?:in |\()?(?:the )?app template")


def _vendored_paths() -> set[str]:
    """Rendered paths this repo vendors byte-identically from the library.

    Their prose is the library's and is fixed upstream, so a citation in one is
    not this template's to rewrite.
    """
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8")) or {}
    paths: set[str] = set()
    for section in ("vendored", "forked"):
        for item in manifest.get(section) or []:
            consumer = item if isinstance(item, str) else (item.get("consumer") or "")
            if not consumer.startswith("template/"):
                continue
            # The jinja in a conditional consumer path wraps the whole name, so
            # dropping the tags leaves the path the render produces.
            paths.add(re.sub(r"\{%.*?%\}", "", consumer[len("template/") :]))
    return paths


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
            if (root / cited).exists() or ELSEWHERE.match(text[match.end() : match.end() + 24]):
                continue
            line = text.count("\n", 0, match.start()) + 1
            problems.append(f"{relative}:{line} cites {cited}, which this render does not ship")
    return problems


@pytest.fixture(scope="session")
def vendored() -> set[str]:
    return _vendored_paths()


def test_shaped_render_cites_only_its_own_files(rendered_a, vendored):
    assert citations_missing_targets(rendered_a, vendored) == []


def test_unlike_render_cites_only_its_own_files(rendered_unlike, vendored):
    assert citations_missing_targets(rendered_unlike, vendored) == []


def test_a_citation_of_an_unrendered_file_is_caught(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "ONBOARDING.md").write_text(
        "Read docs/ONBOARDING.md, then scripts/no-such-gate.py.\n", encoding="utf-8"
    )
    problems = citations_missing_targets(tmp_path)
    assert len(problems) == 1, problems
    assert "scripts/no-such-gate.py" in problems[0]


def test_the_app_template_qualifier_is_accepted(tmp_path):
    (tmp_path / "note.md").write_text(
        "See docs/CI-SHAPES.md in the app template.\n", encoding="utf-8"
    )
    assert citations_missing_targets(tmp_path) == []


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
