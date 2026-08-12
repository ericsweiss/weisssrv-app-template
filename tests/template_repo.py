"""Drive this template the way a new project does, on a throwaway copy.

`scripts/rename.sh` and `scripts/select-ci.sh` are thin wrappers over the
weisssrv-lib `weisssrv-new-project` CLI, so the gate runs the WRAPPERS — their
own argument handling and the per-shape file inventory are what this repository
owns — with the CLI resolved by conftest. Nothing here writes inside the
checkout.

`tests/` is deliberately left out of the copy: it is the template's own gate,
not tenant payload, and copying it would have the token scans reading
themselves.
"""

from __future__ import annotations

import functools
import hashlib
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Assembled rather than written literally: rename.sh substitutes these tokens
# across the whole tree, so a literal here would rewrite the gate's own
# constants in any project that keeps this suite and then renames.
APP_TOKEN = "changeme" + "-app"
GROUP_TOKEN = "changeme" + "-group"

# The fixture rename. A hyphenated slug and a NESTED group are both values a
# naive sed-based substitution would have had to escape.
APP = "recipe-box"
GROUP = "eric/apps"

SHAPES = ("gitlab", "github", "none")

# Repo-root-relative paths a CI-shape selection may delete. Everything else must
# be identical whichever shape is chosen.
CI_PATHS = frozenset(
    {
        ".gitlab-ci.yml",
        ".gitlab/secret-detection-ruleset.toml",
        ".github/workflows/ci.yml",
        ".github/workflows/build-image.yml",
        # Shape B's release path (vendored semantic-release.py --platform github).
        ".github/workflows/release.yml",
    }
)

# What each shape must drop, exhaustively (docs/CI-SHAPES.md + select-ci.sh's
# usage). The doc-derived claims are checked separately; this is the full
# inventory those prose claims summarise.
SHAPE_DROPS = {
    "gitlab": frozenset(
        {
            ".github/workflows/ci.yml",
            ".github/workflows/build-image.yml",
            ".github/workflows/release.yml",
        }
    ),
    "github": frozenset({".gitlab-ci.yml", ".gitlab/secret-detection-ruleset.toml"}),
    "none": CI_PATHS,
}

# GitLab HOST metadata: issue/MR templates are not CI and survive every shape.
HOST_METADATA = (
    ".gitlab/issue_templates/Bug.md",
    ".gitlab/issue_templates/Feature.md",
    ".gitlab/merge_request_templates/Default.md",
)

# Directories a shape drop can empty; removed only when nothing is left in them.
CI_PARENT_DIRS = (".github", ".gitlab")

_EXCLUDE_FROM_COPY = ("tests",)


# --------------------------------------------------------------------------
# The throwaway copy
# --------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def tracked_files() -> tuple[str, ...]:
    """What a fresh clone of this template contains, minus tests/."""
    res = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
        capture_output=True,
        check=True,
    )
    names = (n for n in res.stdout.decode("utf-8").split("\0") if n)
    # `git ls-files` still lists an uncommitted deletion — which is exactly what
    # a project that has run select-ci.sh looks like.
    return tuple(
        n
        for n in names
        if n.split("/", 1)[0] not in _EXCLUDE_FROM_COPY and (REPO_ROOT / n).is_file()
    )


def copy_template(dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    for rel in tracked_files():
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / rel, out)
    # A real project is a git work tree and the CLI prefers `git ls-files` over
    # walking, so init one — otherwise the gate exercises the fallback path.
    for argv in (["init", "-q"], ["add", "-A"]):
        subprocess.run(
            ["git", "-C", str(dest), *argv], capture_output=True, check=False
        )
    return dest


def clone_project(src: Path, dest: Path) -> Path:
    shutil.copytree(src, dest, symlinks=True)
    return dest


# --------------------------------------------------------------------------
# Reading a project back
# --------------------------------------------------------------------------


def files(root: Path) -> list[str]:
    """Every file under `root`, root-relative, excluding the git dir."""
    out = []
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if rel.parts[0] == ".git":
            continue
        if path.is_file():
            out.append(rel.as_posix())
    return sorted(out)


def digest(root: Path) -> dict[str, str]:
    """path -> sha256, for asserting a tree did not move."""
    return {
        rel: hashlib.sha256((root / rel).read_bytes()).hexdigest()
        for rel in files(root)
    }


def text_files(root: Path):
    """(relative path, text) for every file that decodes as UTF-8."""
    for rel in files(root):
        try:
            yield rel, (root / rel).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue


# --------------------------------------------------------------------------
# Running the setup scripts
# --------------------------------------------------------------------------


def run_script(
    root: Path, script: str, *args: str, env: dict, check: bool = False
) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        [str(root / "scripts" / script), *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )
    if check and proc.returncode != 0:
        raise AssertionError(
            f"{script} {' '.join(args)} failed ({proc.returncode})\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc


def rename(root: Path, app: str = APP, group: str = GROUP, *, env, check=True):
    return run_script(root, "rename.sh", app, group, env=env, check=check)


def select_ci(root: Path, shape: str, *, env, check=True):
    return run_script(root, "select-ci.sh", shape, env=env, check=check)


# --------------------------------------------------------------------------
# The keep/drop claims prose makes about each shape
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Claim:
    keep: frozenset
    drop: frozenset


# A shape name only counts where it is the COMMAND ARGUMENT: bare, and not part
# of a dotted path — `.github/workflows/` contains "github", and matching that
# would hand the gitlab line's claims to the github shape.
def _shape_re(shape: str) -> re.Pattern:
    return re.compile(rf"(?<![.\w-]){shape}(?=\s)")


_PATH_RE = re.compile(r"\.[A-Za-z0-9][A-Za-z0-9._/-]*")


def _paths(clause: str) -> frozenset:
    return frozenset(m.group(0).rstrip("/.,)") for m in _PATH_RE.finditer(clause))


def shape_claims(text: str) -> dict[str, Claim]:
    """Per-shape keep/drop claims made by a prose source.

    Reads docs/CI-SHAPES.md's selector block and select-ci.sh's own usage with
    one parser, so neither can drift from the behaviour without the gate saying
    so. `keep neither` / `drop both` resolve to the union of the paths the other
    shapes name as droppable.
    """
    clauses: dict[str, tuple[str, str]] = {}
    for line in text.splitlines():
        if "keep" not in line and "drop" not in line:
            continue
        for shape in SHAPES:
            if shape in clauses or not _shape_re(shape).search(line):
                continue
            head, saw_drop, tail = line.partition("drop")
            clauses[shape] = (head.partition("keep")[2], tail if saw_drop else "")

    parsed = {s: (_paths(k), _paths(d), k, d) for s, (k, d) in clauses.items()}
    droppable = frozenset().union(*(drop for _, drop, _, _ in parsed.values()))

    claims = {}
    for shape, (keep, drop, keep_clause, drop_clause) in parsed.items():
        if (not keep and "neither" in keep_clause) or (not drop and "both" in drop_clause):
            keep, drop = frozenset(), droppable
        claims[shape] = Claim(keep, drop)
    return claims


def lib_ref(script: str) -> str:
    """The weisssrv-lib tag a wrapper script resolves the CLI from.

    The scripts no longer restate the tag: they read variables.WEISSSRV_LIB_REF
    from .gitlab-ci.yml (the single source), honouring a WEISSSRV_LIB_REF env
    override. Assert that derivation and return the resolved tag, so the
    single-source test stays meaningful without a second literal to keep in step.
    """
    text = (REPO_ROOT / "scripts" / script).read_text(encoding="utf-8")
    assert "WEISSSRV_LIB_REF:-$(sed" in text and ".gitlab-ci.yml" in text, (
        f"scripts/{script} must derive LIB_REF from .gitlab-ci.yml's WEISSSRV_LIB_REF"
    )
    source = re.search(
        r'^\s{2}WEISSSRV_LIB_REF:\s*"([^"]+)"',
        (REPO_ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8"),
        re.M,
    )
    assert source, "WEISSSRV_LIB_REF not found in .gitlab-ci.yml"
    return source.group(1)
