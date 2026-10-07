"""Assert the doc-link and lib-pin gates pass over this repo's own tree.

Behavioural and mutation cases live with each gate, check-lib-pins in the vendored
tests/test_check_lib_pins.py. These two runs cover the tree a contributor edits.
"""

from __future__ import annotations

import subprocess
import sys

import render_app

REPO_ROOT = render_app.REPO_ROOT


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120
    )


def test_this_repos_own_doc_links_resolve():
    """tests/test_render.py runs this gate inside a render, which says nothing
    about the Markdown in this repo."""
    result = _run("scripts/check-doc-links.py")
    assert result.returncode == 0, result.stdout + result.stderr


def test_this_repos_own_lib_pins_match_the_single_source():
    """The smoke test test_check_lib_pins.py assigns to the consumer. The
    include refs are literals GitLab will not interpolate, so a bump without
    --fix leaves a stale pin behind."""
    project = render_app.load_ci(REPO_ROOT / ".gitlab-ci.yml")["variables"]["LIB_PROJECT"]
    result = _run("scripts/check-lib-pins.py", "--project", project)
    assert result.returncode == 0, result.stdout + result.stderr
