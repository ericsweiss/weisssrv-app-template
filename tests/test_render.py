"""Render the template and assert what must be true of every generated repo.

The invariants under test are listed in docs/ARCHITECTURE.md, Invariants the
render tests hold. Rendering is session-scoped.
"""

from __future__ import annotations

import base64
import copy
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from fnmatch import fnmatch, fnmatchcase
from pathlib import Path

import jinja2
import pytest
import yaml
from conftest import require_tool

import render_app
import validate_render

REPO_ROOT = render_app.REPO_ROOT
FLUX = "kubernetes/flux"

# Both YAML suffixes, so a manifest named `.yml` cannot escape the per-manifest
# assertions while the kustomization counts still pass.
YAML_SUFFIXES = ("yaml", "yml")

# A denylist, not an allowlist: the files most likely to carry a hardcoded
# value are extensionless (Dockerfile, CODEOWNERS). Anything read_text()
# decodes is scanned, and the decode error is the filter.
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".pyc"}

# Files a text walk could not decode, kept so a drop out of the leakage and
# literal scans fails instead of reading as a pass.
UNREADABLE: list[tuple[str, str]] = []

# Local tool caches inside the working tree. tests/render_app.py keeps them
# out of a render, so a scan of the template source skips them too.
IGNORED_DIRS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".render",
                ".tmp", ".bin"}

# The RFC-reserved and private IPv4 space the public-egress rule must except,
# read from the tenant-side gate this repo vendors so the constant has one
# source. tests/validate_render.py runs that gate over the render as well.
RESERVED_FULL = render_app.load_gate(
    "check-netpol-except-parity.py", "template/scripts"
).RESERVED_FULL

# Every question copier.yml declares, computed ones included. The fixtures
# answer only the ASKED set, so a name taken from the answers cannot see a
# computed question leak into the render unsubstituted.
QUESTION_NAMES = sorted(
    name
    for name in yaml.safe_load((REPO_ROOT / "copier.yml").read_text())
    if not name.startswith("_")
)


def _copier_data(overrides: dict) -> dict[str, str]:
    """`overrides` as copier's --data takes them: every value a string, a
    boolean in YAML's lowercase spelling."""
    return {
        key: str(value).lower() if isinstance(value, bool) else str(value)
        for key, value in overrides.items()
    }


@pytest.fixture(scope="session")
def answers() -> dict:
    return yaml.safe_load(render_app.ANSWERS.read_text())


@pytest.fixture(scope="session")
def answers_b() -> dict:
    return yaml.safe_load(render_app.ANSWERS_B.read_text())


# The answers file each answer fixture loads, by fixture name.
_ANSWER_PATHS = {"answers": render_app.ANSWERS, "answers_b": render_app.ANSWERS_B}


def _render(factory, label: str) -> Path:
    """Render RENDERS[label]: the answers file and overrides `repo` reads for it too."""
    _, answers_fixture, overrides = RENDERS[label]
    return render_app.render(
        factory.mktemp(f"render-{label}"),
        answers=_ANSWER_PATHS[answers_fixture],
        dest_name=f"render-{label}",
        data=_copier_data(overrides) or None,
    )


@pytest.fixture(scope="session")
def rendered(tmp_path_factory) -> Path:
    return _render(tmp_path_factory, "shaped")


@pytest.fixture(scope="session")
def rendered_b(tmp_path_factory) -> Path:
    """A second render from unlike answers, every optional component off.

    It is what makes a hardcoded value visible; under the shaped fixture a
    carried-over literal renders identically to a correct substitution.
    """
    return _render(tmp_path_factory, "unlike")


@pytest.fixture(scope="session")
def rendered_none(tmp_path_factory) -> Path:
    """Fixture A with ci_shape overridden: the render that ships no pipeline."""
    return _render(tmp_path_factory, "none-shape")


@pytest.fixture(scope="session")
def rendered_no_secrets(tmp_path_factory) -> Path:
    """The `secrets_backend: none` render: no ExternalSecret, no secret env
    block, no ClusterSecretStore. The pull credential is overridden with it
    because it is an ExternalSecret too.
    """
    return _render(tmp_path_factory, "no-secrets")


# The library project the gitlab-unlike render answers. It must differ from
# both fixtures, which share copier's default, or an `include: project:`
# written as a literal renders correctly everywhere else in this suite.
ALT_LIB_PROJECT = "seaworks/brinemoor-lib"

# Fixture B, moved onto the GitLab shape with the image build on. Every other
# value stays fixture B's and differs from fixture A's, except lib_ref.
GITLAB_UNLIKE_OVERRIDES = {
    "ci_shape": "gitlab_selfhosted",
    # The GitLab pipeline is only legal on a GitLab forge, and copier.yml's
    # validator says so, so the shape override carries the forge with it.
    "forge": "gitlab",
    "enable_image_build": True,
    "lib_project": ALT_LIB_PROJECT,
}


@pytest.fixture(scope="session")
def rendered_gitlab_unlike(tmp_path_factory, answers, answers_b) -> Path:
    """The GitLab pipeline, rendered from answers unlike the reference
    cluster's — the only render in which the pipeline exists and no answer is
    fixture A's. See docs/CI-SHAPES.md.
    """
    assert ALT_LIB_PROJECT not in {answers["lib_project"], answers_b["lib_project"]}, (
        "ALT_LIB_PROJECT now coincides with a fixture answer — pick one neither uses"
    )
    return _render(tmp_path_factory, "gitlab-unlike")


@pytest.fixture(scope="session")
def rendered_gitlab_no_build(tmp_path_factory) -> Path:
    """The GitLab pipeline with the image build off: no `build` stage, no
    docker-build include, no Dockerfile. The combination no fixture answers."""
    return _render(tmp_path_factory, "gitlab-no-build")


@pytest.fixture(scope="session")
def rendered_github_none(tmp_path_factory) -> Path:
    """No pipeline on a GitHub-hosted repo: the file set the `forge` answer
    changes on its own, with that forge's word for a reviewed change. `enable_hpa`
    rides along so the default HPA-off, VPA-on pair is rendered somewhere."""
    return _render(tmp_path_factory, "github-none")


@pytest.fixture(scope="session")
def rendered_forge_other(tmp_path_factory) -> Path:
    """A forge that is neither GitLab nor GitHub: no forge metadata renders, and
    `change_request` is answered rather than computed. It is also the only render
    with the HPA on, the VPA off and SSO with no LAN hostname."""
    return _render(tmp_path_factory, "forge-other")


# Kept out of RENDERS: it proves one filter, not a file set, so it needs no
# validate-rendered-app line of its own.
MIXED_CASE_GIT_NAMESPACE = "BrineMoor-Works"


@pytest.fixture(scope="session")
def rendered_mixed_case_git_namespace(tmp_path_factory, answers, answers_b) -> Path:
    """A namespace answered in mixed case, which no fixture does.

    The image build is on, so the arms that spell the image path only for a
    building repo are rendered text too.
    """
    assert MIXED_CASE_GIT_NAMESPACE != MIXED_CASE_GIT_NAMESPACE.lower()
    assert MIXED_CASE_GIT_NAMESPACE not in {answers["git_namespace"], answers_b["git_namespace"]}
    return render_app.render(
        tmp_path_factory.mktemp("render-mixed-git-ns"),
        answers=render_app.ANSWERS_B,
        dest_name="render-mixed-git-ns",
        data=_copier_data(
            {"git_namespace": MIXED_CASE_GIT_NAMESPACE, "enable_image_build": True}
        ),
    )


@pytest.fixture(scope="session")
def rendered_image_github(tmp_path_factory) -> Path:
    """The contrast fixture with the image build on.

    `github` plus `enable_image_build: true` gates the build-image workflow's
    conditional path, and no fixture answers that combination.
    """
    return _render(tmp_path_factory, "github-image")


@pytest.fixture(scope="session")
def rendered_unlike_full(tmp_path_factory) -> Path:
    """The contrast fixture with every component on: the ServiceMonitor, HPA, VPA, PDB,
    internal route, pull credential and forward-auth middleware all come from answers
    that are not the reference cluster's."""
    return _render(tmp_path_factory, "unlike-full")


@pytest.fixture(scope="session")
def rendered_unlike_db(tmp_path_factory) -> Path:
    return _render(tmp_path_factory, "unlike-db")


@pytest.fixture(scope="session")
def rendered_private(tmp_path_factory) -> Path:
    return _render(tmp_path_factory, "private")


@dataclass(frozen=True)
class Repo:
    """One rendered repository plus the answers that produced it."""

    label: str
    path: Path
    answers: dict


# label -> (render fixture, answer fixture, the answers that render overrode).
# The single source: _render builds each tree from its entry and `repo` reads
# the same entry, so the assertions and the tree stay on one answer set.
RENDERS = {
    "shaped": ("rendered", "answers", {}),
    "unlike": ("rendered_b", "answers_b", {}),
    "none-shape": ("rendered_none", "answers", {"ci_shape": "none"}),
    "no-secrets": (
        "rendered_no_secrets",
        "answers",
        {"secrets_backend": "none", "enable_registry_pull_secret": False},
    ),
    "github-none": (
        "rendered_github_none",
        "answers",
        {
            "ci_shape": "none",
            "forge": "github",
            "change_request": "pull request",
            "enable_hpa": False,
        },
    ),
    "github-image": ("rendered_image_github", "answers_b", {"enable_image_build": True}),
    "forge-other": (
        "rendered_forge_other",
        "answers",
        {
            "ci_shape": "none",
            "forge": "other",
            "change_request": "pull request",
            "enable_vpa": False,
            "enable_internal_ingress": False,
        },
    ),
    "gitlab-unlike": ("rendered_gitlab_unlike", "answers_b", GITLAB_UNLIKE_OVERRIDES),
    "gitlab-no-build": ("rendered_gitlab_no_build", "answers", {"enable_image_build": False}),
    # Every optional component on, so the optional manifests and the forward-auth
    # middleware are scanned for a carried-over reference-cluster literal.
    "unlike-full": (
        "rendered_unlike_full",
        "answers_b",
        {
            "enable_servicemonitor": True,
            "enable_internal_ingress": True,
            "enable_hpa": True,
            "enable_vpa": True,
            "enable_registry_pull_secret": True,
            "enable_image_build": True,
            "enable_sso": True,
            "replica_count": 3,
        },
    ),
    # The only render in which kubernetes/db comes from answers unlike fixture A's.
    "unlike-db": (
        "rendered_unlike_db",
        "answers_b",
        {"secrets_backend": "onepassword", "database": "postgres"},
    ),
    # The public route off: SSO goes with the public route it gates.
    "private": (
        "rendered_private",
        "answers",
        {"enable_public_ingress": False, "enable_sso": False},
    ),
}


@pytest.fixture(scope="session", params=list(RENDERS))
def repo(request) -> Repo:
    render_fixture, answers_fixture, overrides = RENDERS[request.param]
    answers = {**request.getfixturevalue(answers_fixture), **overrides}
    return Repo(request.param, request.getfixturevalue(render_fixture), answers)


def _read_text(path: Path) -> str | None:
    """The file's text, or None once the undecodable path is recorded."""
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        UNREADABLE.append((str(path), exc.__class__.__name__))
        return None


def _text_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix in BINARY_SUFFIXES:
            continue
        text = _read_text(path)
        if text is not None:
            yield path, text


def _template_files():
    """The template sources, skipping the local tool caches copier excludes."""
    for path in sorted((REPO_ROOT / "template").rglob("*")):
        if not path.is_file() or path.suffix in BINARY_SUFFIXES:
            continue
        if IGNORED_DIRS & set(path.relative_to(REPO_ROOT).parts):
            continue
        text = _read_text(path)
        if text is not None:
            yield path, text


def _manifests(root: Path, stem: str = "*", recursive: bool = False) -> list[Path]:
    """Flux manifests whose name matches `stem`, under both YAML suffixes."""
    flux = root / FLUX
    walk = flux.rglob if recursive else flux.glob
    return sorted(path for suffix in YAML_SUFFIXES for path in walk(f"{stem}.{suffix}"))


def _kustomization(root: Path) -> dict:
    return yaml.safe_load((root / FLUX / "kustomization.yaml").read_text())


def _docs(root: Path) -> list[dict]:
    docs: list[dict] = []
    for path in _manifests(root):
        docs += [d for d in yaml.safe_load_all(path.read_text()) if isinstance(d, dict)]
    return docs


# --------------------------------------------------------------------------
# The render itself
# --------------------------------------------------------------------------


def test_render_produces_a_repository(repo):
    assert (repo.path / ".copier-answers.yml").is_file(), (
        "no answers file — copier update would not work"
    )
    assert (repo.path / FLUX / "kustomization.yaml").is_file()


def test_answers_file_records_the_fixture(repo):
    recorded = yaml.safe_load((repo.path / ".copier-answers.yml").read_text())
    assert recorded["app_slug"] == repo.answers["app_slug"]
    assert recorded["ci_shape"] == repo.answers["ci_shape"]


def test_no_unrendered_jinja(repo):
    """No Jinja survives into the render.

    A `{% ... %}` block means a templated file is missing its .jinja suffix; a
    `{{ <question> }}` is an answer that reached the output unsubstituted.
    """
    leak = re.compile(r"\{\{-?\s*(" + "|".join(QUESTION_NAMES) + r")\s*[|}-]")
    offenders = []
    for path, text in _text_files(repo.path):
        # The vendored library scripts are Python, not template sources.
        if path.relative_to(repo.path).parts[0] == "scripts":
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if "{%" in line or leak.search(line):
                offenders.append(f"{path.relative_to(repo.path)}:{lineno} {line.strip()[:70]}")
    assert not offenders, "unrendered Jinja survived the render:\n  " + "\n  ".join(offenders)


def test_every_text_file_was_read(repo):
    """A file the walk cannot decode silently leaves every leakage, unrendered-
    expression and literal scan, which reads as a pass. Both walks are drained
    here so the result does not depend on test order."""
    _ = list(_text_files(repo.path))
    _ = list(_template_files())
    assert not UNREADABLE, "files dropped out of the text scans: " + ", ".join(
        f"{path} ({exc})" for path, exc in UNREADABLE
    )


def test_the_two_fixtures_answer_differently(answers, answers_b):
    """The contrast fixture only proves anything while its answers differ. A
    key that drifts back into agreement silently disarms the leak check."""
    assert set(answers) == set(answers_b), "the two fixtures answer different question sets"
    shared = {k for k, v in answers.items() if answers_b[k] == v}
    # The library is the same upstream in both on purpose: a fixture pinned
    # elsewhere would exercise includes no real tenant gets. Both publish a
    # public route; the `private` render covers `false`.
    assert shared <= {"lib_ref", "lib_project", "enable_public_ingress"}, (
        "answers that must differ between the fixtures now coincide: "
        + ", ".join(sorted(shared))
    )


# Answers whose fixture-A value cannot serve as evidence in a cross-render diff.
# Keep this list SHORT: an entry here is coverage given up, so each names the
# targeted gate that replaces it.
CROSS_RENDER_EXEMPT = {
    "k8s_version": (
        "the GitHub shape's workflows are vendored byte-identically from the "
        "library and carry its own literal — test_k8s_version_reaches_the_gitlab_"
        "shape is the targeted gate, and docs/CI-SHAPES.md records the limitation"
    ),
    "secret_item": "'App Secrets' is ordinary prose in the docs the render ships",
    "privileged_runner_tag": (
        "'infrastructure' is also the platform's Flux tree and Kustomization name — "
        "test_the_pipeline_carries_no_fixture_a_values, "
        "test_the_build_runner_step_ships_with_the_tagged_build and "
        "test_the_skill_names_the_answered_build_runner_tag are the targeted gates"
    ),
    "db_roles_item": (
        "'Database' is ordinary prose, the O6b heading among it — "
        "test_the_role_secrets_read_the_answered_item is the targeted gate"
    ),
    "forge": (
        "'gitlab' is also fixture B's secrets_backend answer and the forge's "
        "ordinary name in prose — test_ci_shape_keeps_exactly_its_own_files and "
        "test_forge_vocabulary_matches_the_forge are the targeted gates"
    ),
}


# A line naming one of the family's own repositories legitimately carries their
# path wherever the generated repo lives: they are upstreams, not site identity.
UPSTREAM_REPOS = ("weisssrv-lib", "weisssrv-app-template", "weisssrv-cluster-template")


# Byte-identical library copies, read from the manifest so a template-owned file
# beside them stays scanned. They cannot take an answer, and the vendored GitHub
# workflows narrate the GitLab job, so "merge request" there is another forge's.
VENDORED_FILES = render_app.vendored_render_paths()


# Every render built on fixture B. A fixture-A value in one of these is copied,
# never substituted, so each is scanned — the components-off contrast fixture
# alone leaves the optional manifests and the Dockerfile unscanned.
CONTRAST_RENDERS = [label for label, (_, base, _) in RENDERS.items() if base == "answers_b"]


def _fixture_a_leaks(root: Path, answers: dict, effective: dict) -> list[str]:
    """Where a fixture-A answer survives as a literal in a render built on B."""
    leaks = []
    for key, value in answers.items():
        if key in CROSS_RENDER_EXEMPT or not isinstance(value, str):
            continue
        if value == str(effective.get(key)) or len(value) < 4:
            continue
        needle = re.compile(rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])")
        for path, text in _text_files(root):
            relative = path.relative_to(root)
            if relative.as_posix() in VENDORED_FILES:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                # copier's own bookkeeping keys (_src_path, _commit) record where
                # the render came from, which under pytest is a scratch path
                # containing the caller's username.
                if path.name == ".copier-answers.yml" and line.startswith("_"):
                    continue
                if needle.search(line) and not any(repo in line for repo in UPSTREAM_REPOS):
                    leaks.append(f"{relative}:{lineno} {key}={value}")
    return leaks


@pytest.mark.parametrize("label", CONTRAST_RENDERS)
def test_render_b_carries_no_fixture_a_values(request, answers, label):
    """No answer from fixture A may appear anywhere in a render built on fixture
    B: the scan that separates 'substituted' from 'copied'. Word boundaries,
    because a short answer is otherwise a substring of ordinary English.
    """
    render_fixture, answers_fixture, overrides = RENDERS[label]
    root = request.getfixturevalue(render_fixture)
    effective = {**request.getfixturevalue(answers_fixture), **overrides}
    leaks = _fixture_a_leaks(root, answers, effective)
    assert not leaks, (
        f"fixture A's answers appear in the {label} render — those values "
        "are hardcoded, not substituted:\n  " + "\n  ".join(sorted(set(leaks)))
    )


def test_the_fixture_a_scan_reaches_a_template_owned_file(tmp_path, request, answers):
    """The vendored exemption is per file, not per directory: a value in a file
    beside the vendored workflows, and not registered as one, is still found."""
    root = tmp_path / "render"
    shutil.copytree(request.getfixturevalue("rendered_b"), root)
    target = root / ".github" / "workflows" / "tenant-owned.yml"
    target.write_text(f"# {answers['app_slug']}\n")
    leaks = _fixture_a_leaks(root, answers, request.getfixturevalue("answers_b"))
    assert [leak for leak in leaks if "tenant-owned.yml" in leak], leaks


# Reference-cluster spellings, named outright: the A/B diff above cannot see a
# literal both fixtures happen to answer identically.
FORBIDDEN_LITERALS = ("esweiss.com", "ericsweiss.com", "weisssrv")
FORBIDDEN_PATTERNS = {
    "reference-cluster address": re.compile(r"\b10\.0\.10\.\d{1,3}\b"),
    "reference-cluster node name": re.compile(r"\bpve-[a-z]+-\d+\b"),
    "reference-cluster mailbox": re.compile(r"[\w.+-]+@(?:erics|es)weiss\.com\b"),
}

# The provenance link the rendered README carries: this template's own home,
# which is not the tenant's cluster.
TEMPLATE_HOME = "git.ericsweiss.com/eric/weisssrv-app-template"


def _allowed_spellings(answers: dict) -> tuple[str, ...]:
    """Spellings that legitimately carry a forbidden form: the render's own
    answers, the family's upstream repositories, this template's home. Longest
    first, so a URL is removed before the domain inside it."""
    carriers = {
        str(value)
        for value in answers.values()
        if str(value)
        and (
            any(literal in str(value) for literal in FORBIDDEN_LITERALS)
            or any(pattern.search(str(value)) for pattern in FORBIDDEN_PATTERNS.values())
        )
    }
    allowed = carriers | set(UPSTREAM_REPOS) | {TEMPLATE_HOME}
    return tuple(sorted(allowed, key=len, reverse=True))


def _reference_cluster_literals(root: Path, answers: dict) -> list[str]:
    """Every forbidden spelling in `root` an answer does not account for."""
    allowed = _allowed_spellings(answers)
    offenders = []
    for path, text in _text_files(root):
        relative = path.relative_to(root).as_posix()
        for lineno, line in enumerate(text.splitlines(), 1):
            # copier records where the render came from, which under pytest is a
            # scratch path carrying the caller's username.
            if path.name == ".copier-answers.yml" and line.startswith("_"):
                continue
            redacted = line
            for spelling in allowed:
                redacted = redacted.replace(spelling, "")
            for literal in FORBIDDEN_LITERALS:
                if literal in redacted:
                    offenders.append(f"{relative}:{lineno} literal {literal!r}")
            for arm, pattern in FORBIDDEN_PATTERNS.items():
                match = pattern.search(redacted)
                if match:
                    offenders.append(f"{relative}:{lineno} {arm} {match.group(0)!r}")
    return sorted(set(offenders))


def _forbidden_arms() -> str:
    return ", ".join([*FORBIDDEN_LITERALS, *sorted(FORBIDDEN_PATTERNS)])


def test_no_reference_cluster_literal_reaches_the_render(repo):
    """A tenant inherits no part of the reference cluster's identity."""
    offenders = _reference_cluster_literals(repo.path, repo.answers)
    assert not offenders, (
        f"reference-cluster values reached the {repo.label} render (arms checked: "
        f"{_forbidden_arms()}):\n  " + "\n  ".join(offenders)
    )


def test_the_forbidden_literal_arms_find_a_planted_value(tmp_path, request, answers_b):
    """Each arm on its own, so a regex that stops matching is not read as a
    clean render."""
    root = tmp_path / "planted"
    shutil.copytree(request.getfixturevalue("rendered_b"), root)
    planted = root / "docs" / "PLANTED.md"
    planted.write_text(
        "Resolver 10.0.10.150 lives on pve-nas-01.\n"
        "Mail eric@esweiss.com about the weisssrv cluster at app.ericsweiss.com.\n"
    )
    offenders = _reference_cluster_literals(root, answers_b)
    for arm in (*FORBIDDEN_LITERALS, *FORBIDDEN_PATTERNS):
        assert any(arm in offender for offender in offenders), (
            f"the {arm!r} arm matched nothing in the planted file: {offenders}"
        )


# Everything the GitLab shape renders as pipeline configuration.
PIPELINE_PATHS = (".gitlab-ci.yml", ".gitlab")


def _pipeline_files(root: Path):
    for name in PIPELINE_PATHS:
        target = root / name
        if target.is_file():
            yield target, target.read_text()
        elif target.is_dir():
            yield from _text_files(target)


# The answer the gitlab-unlike render cannot answer differently, so agreement
# with fixture A proves nothing: `ci_shape` is the override itself.
PIPELINE_UNPROVABLE = ("ci_shape",)


def test_the_pipeline_carries_no_fixture_a_values(
    rendered_gitlab_unlike, answers, answers_b
):
    """The anti-hardcode scan applied to .gitlab-ci.yml, which fixture B never
    renders. `k8s_version` is un-exempted here, its CROSS_RENDER_EXEMPT
    collision being outside the pipeline.
    """
    effective = {**answers_b, **GITLAB_UNLIKE_OVERRIDES}
    leaks = []
    for key, value in answers.items():
        if key in PIPELINE_UNPROVABLE or not isinstance(value, str) or len(value) < 4:
            continue
        if value == str(effective.get(key)):
            continue
        needle = re.compile(rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])")
        for path, text in _pipeline_files(rendered_gitlab_unlike):
            for lineno, line in enumerate(text.splitlines(), 1):
                if needle.search(line):
                    leaks.append(
                        f"{path.relative_to(rendered_gitlab_unlike)}:{lineno} {key}={value}"
                    )
    assert not leaks, (
        "fixture A's answers appear in a pipeline rendered from unlike answers — "
        "those values are hardcoded in .gitlab-ci.yml.jinja, not substituted:\n  "
        + "\n  ".join(sorted(set(leaks)))
    )


# The answers the pipeline is REQUIRED to carry. Without this the test above
# passes on an empty pipeline: absence of the wrong value is not presence of the
# right one, and the two together are what prove substitution.
PIPELINE_ANSWERS = (
    "app_slug",
    "app_namespace",
    "git_host",
    "k8s_version",
    "ci_cpu_selector",
    "privileged_runner_tag",
    "lib_project",
    "lib_ref",
)


@pytest.mark.parametrize("key", PIPELINE_ANSWERS)
def test_the_pipeline_names_the_answered_value(rendered_gitlab_unlike, answers_b, key):
    value = str({**answers_b, **GITLAB_UNLIKE_OVERRIDES}[key])
    text = "\n".join(text for _, text in _pipeline_files(rendered_gitlab_unlike))
    assert value in text, f"the rendered pipeline never names {key}={value}"


# The other forge's word and its abbreviation. The abbreviation needs word
# boundaries and the answered case, or every "MR" inside a longer token matches.
_FORGE_WORDS = {
    "merge request": re.compile(r"\bMRs?\b"),
    "pull request": re.compile(r"\bPRs?\b"),
}


def _forge_vocabulary_offenders(root: Path, wrong: str) -> list[str]:
    abbreviation = _FORGE_WORDS[wrong]
    offenders = []
    for path, text in _text_files(root):
        relative = path.relative_to(root)
        if relative.as_posix() in VENDORED_FILES:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if wrong in line.lower() or abbreviation.search(line):
                offenders.append(f"{relative}:{lineno} {line.strip()[:70]}")
    return offenders


@pytest.mark.parametrize(
    "render_fixture,wrong",
    [
        ("rendered_b", "merge request"),
        ("rendered", "pull request"),
        # The two renders where `forge` and `ci_shape` disagree, each answering
        # `change_request` for its own forge.
        ("rendered_github_none", "merge request"),
        ("rendered_forge_other", "merge request"),
    ],
)
def test_forge_vocabulary_matches_the_forge(request, render_fixture, wrong):
    """A GitHub tenant has no merge requests, and the reverse.

    Every file takes the word from `change_request`, so one wrong spelling left
    as a literal shows up here as the other forge's vocabulary."""
    offenders = _forge_vocabulary_offenders(request.getfixturevalue(render_fixture), wrong)
    assert not offenders, (
        "the other forge's vocabulary survived — substitute {{ change_request }}:\n  "
        + "\n  ".join(offenders)
    )


# Files that carry {{ change_request }} on every shape.
FORGE_VOCABULARY_FILES = (
    "README.md",
    "CLAUDE.md",
    "docs/VERSIONING.md",
    "docs/ONBOARDING.md",
    ".claude/skills/project-development/SKILL.md",
)


@pytest.mark.parametrize(
    "render_fixture,right",
    [
        ("rendered_b", "pull request"),
        ("rendered", "merge request"),
        ("rendered_github_none", "pull request"),
        ("rendered_forge_other", "pull request"),
    ],
)
def test_the_forges_own_vocabulary_is_substituted(request, render_fixture, right):
    """Absence of the wrong word is not presence of the right one: neutralising
    {{ change_request }} to a bare noun passes the scan above."""
    root = request.getfixturevalue(render_fixture)
    for name in FORGE_VOCABULARY_FILES:
        path = root / name
        assert path.is_file(), f"{name} does not render on this shape"
        assert right in path.read_text(encoding="utf-8").lower(), (
            f"{name}: {right!r} never appears — the change_request substitution was lost"
        )


# --------------------------------------------------------------------------
# The file set matches the answers
# --------------------------------------------------------------------------

# answer -> the manifest it renders. Every one is BOTH a file and a line in
# kustomization.yaml: a component that exists but is not listed is inert, and a
# line with no file fails `kustomize build`.
COMPONENT_MANIFESTS = {
    "enable_servicemonitor": ["servicemonitor.yaml"],
    "enable_internal_ingress": ["ingressroute-internal.yaml", "certificate-internal.yaml"],
    "enable_hpa": ["hpa.yaml"],
    "enable_vpa": ["vpa.yaml"],
    "enable_registry_pull_secret": ["externalsecret-registry.yaml"],
}


@pytest.mark.parametrize("answer,manifests", COMPONENT_MANIFESTS.items())
def test_optional_components_are_all_or_nothing(repo, answer, manifests):
    enabled = bool(repo.answers.get(answer))
    listed = _kustomization(repo.path)["resources"]
    for manifest in manifests:
        exists = (repo.path / FLUX / manifest).is_file()
        assert exists is enabled, f"{manifest} present={exists} but {answer}={enabled}"
        assert (manifest in listed) is enabled, (
            f"{manifest} listed={manifest in listed} but {answer}={enabled}"
        )


def test_every_listed_resource_exists(repo):
    """kustomize fails on a missing resource, but only when someone runs it;
    this fails on the render that produced the mismatch."""
    for resource in _kustomization(repo.path)["resources"]:
        assert (repo.path / FLUX / resource).is_file(), f"{resource} is listed but absent"


def test_every_manifest_is_listed(repo):
    listed = set(_kustomization(repo.path)["resources"])
    on_disk = {p.name for p in _manifests(repo.path)} - {"kustomization.yaml"}
    assert on_disk == listed, (
        "a manifest Flux never builds is inert, and inert manifests rot: "
        f"unlisted={sorted(on_disk - listed)}"
    )


def test_secrets_backend_shapes_the_whole_secret_surface(repo):
    backend = repo.answers["secrets_backend"]
    manifest = repo.path / FLUX / "externalsecret.yaml"
    assert manifest.is_file() is (backend != "none")
    deployment = (repo.path / FLUX / "deployment.yaml").read_text()
    assert ("secretKeyRef" in deployment) is (backend != "none"), (
        "the Deployment's secret env block must appear exactly when a backend does"
    )
    if backend == "none":
        assert "ExternalSecret" not in [d["kind"] for d in _docs(repo.path)], (
            "with no backend there is no ClusterSecretStore to read from, so ANY "
            "ExternalSecret left in the tree — the registry pull credential is "
            "one — names a store the operator was told not to create"
        )
        return
    store = yaml.safe_load(manifest.read_text())["spec"]["secretStoreRef"]["name"]
    assert store == f"{backend}-{repo.answers['app_slug']}", (
        "the store name is what the operator creates; a mismatch is a secret "
        "that never syncs"
    )
    slug = repo.answers["app_slug"]
    workload = yaml.safe_load((repo.path / FLUX / "deployment.yaml").read_text())
    refs = [
        entry["valueFrom"]["secretKeyRef"]
        for container in workload["spec"]["template"]["spec"]["containers"]
        for entry in container.get("env") or []
        if (entry.get("valueFrom") or {}).get("secretKeyRef", {}).get("name") == f"{slug}-secrets"
    ]
    assert refs, "the Deployment binds no key from the synced Secret"
    for ref in refs:
        assert "optional" not in ref, (
            "an optional binding starts the pod without the secret, and "
            "docs/ONBOARDING.md tells the operator it will not"
        )


def test_pdb_tracks_the_replica_count(repo):
    """`minAvailable: 1` on a single replica blocks every voluntary eviction, so
    a node holding it can never drain."""
    expected = repo.answers["replica_count"] > 1 or repo.answers["enable_hpa"]
    assert (repo.path / FLUX / "pdb.yaml").is_file() is expected


def test_hpa_and_deployment_do_not_both_own_replicas(repo):
    deployment = yaml.safe_load((repo.path / FLUX / "deployment.yaml").read_text())
    if repo.answers["enable_hpa"]:
        assert "replicas" not in deployment["spec"], (
            "with an HPA the Deployment must ship no replicas — Flux server-side "
            "apply would fight the HPA over the field on every reconcile"
        )
    else:
        assert deployment["spec"]["replicas"] == repo.answers["replica_count"]


def test_the_vpa_leaves_cpu_to_the_hpa(repo):
    """Two autoscalers on one resource fight, so with an HPA the VPA is
    memory-only; on its own it recommends both."""
    if not repo.answers["enable_vpa"]:
        assert not (repo.path / FLUX / "vpa.yaml").is_file()
        return
    vpa = yaml.safe_load((repo.path / FLUX / "vpa.yaml").read_text())
    controlled = vpa["spec"]["resourcePolicy"]["containerPolicies"][0]["controlledResources"]
    expected = ["memory"] if repo.answers["enable_hpa"] else ["cpu", "memory"]
    assert controlled == expected


def test_the_autoscalers_name_the_deployment_they_scale(repo):
    """An autoscaler naming a workload that does not exist is inert: kustomize
    builds it, kubeconform passes it, and nothing ever scales. The name is read
    from the rendered Deployment, so a hardcoded slug in either file fails.
    """
    deployment = yaml.safe_load((repo.path / FLUX / "deployment.yaml").read_text())
    target = {"apiVersion": "apps/v1", "kind": "Deployment", "name": deployment["metadata"]["name"]}
    hpa_path, vpa_path = repo.path / FLUX / "hpa.yaml", repo.path / FLUX / "vpa.yaml"
    if hpa_path.is_file():
        hpa = yaml.safe_load(hpa_path.read_text())
        assert hpa["spec"]["scaleTargetRef"] == target, (
            f"the HPA scales {hpa['spec']['scaleTargetRef']}, not the rendered Deployment"
        )
        if (repo.path / FLUX / "pdb.yaml").is_file():
            assert hpa["spec"]["minReplicas"] >= 2, (
                "the PDB's `minAvailable: 1` blocks every voluntary eviction at "
                "one replica, so a node holding the pod can never drain"
            )
    if vpa_path.is_file():
        vpa = yaml.safe_load(vpa_path.read_text())
        assert vpa["spec"]["targetRef"] == target, (
            f"the VPA targets {vpa['spec']['targetRef']}, not the rendered Deployment"
        )


def test_scrape_policy_ships_with_the_servicemonitor(repo):
    """The ServiceMonitor without the NetworkPolicy scrapes into a default-deny
    namespace and reports `up == 0`; the policy without the ServiceMonitor opens
    a port nothing uses."""
    policies = [d["metadata"]["name"] for d in _docs(repo.path) if d["kind"] == "NetworkPolicy"]
    assert ("allow-scrape-from-observability" in policies) is repo.answers[
        "enable_servicemonitor"
    ]


def test_public_egress_excepts_the_whole_reserved_set(repo):
    """The public-egress rule excepts the whole reserved IPv4 set.

    A short list is not a syntax error: it lets the app reach loopback, the LAN
    or cloud-metadata. Asserted here so a binary-free pytest run covers it too.
    """
    policies = {d["metadata"]["name"]: d for d in _docs(repo.path) if d["kind"] == "NetworkPolicy"}
    egress = policies["allow-egress-public"]["spec"]["egress"]
    public = [
        entry["ipBlock"]
        for rule in egress
        for entry in rule.get("to", [])
        if "ipBlock" in entry and entry["ipBlock"]["cidr"] == "0.0.0.0/0"
    ]
    assert len(public) == 1, "exactly one rule may open 0.0.0.0/0"
    assert public[0]["except"] == RESERVED_FULL, "the gate compares the list in order"


def test_ports_agree_across_the_manifests(repo):
    port = repo.answers["app_port"]
    service = yaml.safe_load((repo.path / FLUX / "service.yaml").read_text())
    assert service["spec"]["ports"][0]["port"] == port
    deployment = yaml.safe_load((repo.path / FLUX / "deployment.yaml").read_text())
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["ports"][0]["containerPort"] == port
    for doc in _docs(repo.path):
        if doc["kind"] == "NetworkPolicy":
            for rule in doc["spec"].get("ingress", []):
                for entry in rule.get("ports", []):
                    assert entry["port"] == port, f"{doc['metadata']['name']} allows a stale port"


# --------------------------------------------------------------------------
# Alerts, routing and identity
# --------------------------------------------------------------------------


def test_alert_expressions_are_namespace_scoped(repo):
    """Every alert expression scopes its selector to the app namespace.

    PrometheusRules evaluate cluster-wide, so an unscoped `absent()` stops
    returning anything as soon as another namespace has a like-named Deployment.
    """
    rule = yaml.safe_load((repo.path / FLUX / "prometheusrule.yaml").read_text())
    namespace = repo.answers["app_namespace"]
    for group in rule["spec"]["groups"]:
        for alert in group["rules"]:
            assert f'namespace="{namespace}"' in alert["expr"], (
                f"{alert['alert']} does not scope its selector to the namespace"
            )
    # Pin the set itself: iterating whatever is there cannot notice an alert
    # that stopped shipping, and docs/ARCHITECTURE.md names each one.
    alerts = {r["alert"] for g in rule["spec"]["groups"] for r in g["rules"]}
    assert alerts == {"AppDown", "AppAbsent"}, (
        f"the shipped alert set changed: {sorted(alerts)}"
    )
    architecture = (repo.path / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    for name in sorted(alerts):
        assert name in architecture, f"{name} ships but docs/ARCHITECTURE.md does not name it"


def test_routes_and_certificates_pair_up(repo):
    """Each IngressRoute must name a TLS Secret some Certificate issues, covering the
    hostname the route serves. A missing Secret serves the platform's default
    certificate; a Secret holding another name fails the handshake."""
    docs = _docs(repo.path)
    issued = {
        d["spec"]["secretName"]: set(d["spec"]["dnsNames"])
        for d in docs
        if d["kind"] == "Certificate"
    }
    routes = [d for d in docs if d["kind"] == "IngressRoute"]
    used = {d["spec"]["tls"]["secretName"] for d in routes}
    assert used <= set(issued), (
        f"routes reference unissued TLS secrets: {sorted(used - set(issued))}"
    )
    assert set(issued) == used, f"certificates nothing uses: {sorted(set(issued) - used)}"
    served = set()
    for route in routes:
        names = set(
            re.findall(r"Host\(`([^`]+)`\)", " ".join(r["match"] for r in route["spec"]["routes"]))
        )
        assert names, f"{route['metadata']['name']} matches no Host()"
        covered = issued[route["spec"]["tls"]["secretName"]]
        assert names <= covered, (
            f"{route['metadata']['name']} serves {sorted(names - covered)} "
            f"on a certificate issued for {sorted(covered)}"
        )
        served |= names
    slug = repo.answers["app_slug"]
    wanted = {
        f"{slug}.{repo.answers['external_domain']}": repo.answers["enable_public_ingress"],
        f"{slug}.{repo.answers['internal_domain']}": repo.answers["enable_internal_ingress"],
    }
    assert served == {host for host, on in wanted.items() if on}, sorted(served)
    if not repo.answers["enable_public_ingress"]:
        public = f"{slug}.{repo.answers['external_domain']}"
        for doc in ("README.md", "docs/ONBOARDING.md", "docs/ARCHITECTURE.md", "docs/RUNBOOKS.md"):
            assert public not in (repo.path / doc).read_text(), f"{doc} names {public}"


def _public_route(repo) -> dict:
    if not repo.answers["enable_public_ingress"]:
        pytest.skip("no public route on this render")
    return yaml.safe_load((repo.path / FLUX / "ingressroute.yaml").read_text())


def test_hostnames_come_from_the_answers(repo):
    slug, external = repo.answers["app_slug"], repo.answers["external_domain"]
    route = _public_route(repo)
    assert f"Host(`{slug}.{external}`)" in route["spec"]["routes"][0]["match"]
    assert route["metadata"]["annotations"]["external-dns.alpha.kubernetes.io/target"] == external


def _quantity_mib(value: str) -> int:
    assert str(value).endswith("Mi"), f"expected a plain Mi quantity, got {value!r}"
    return int(str(value)[:-2])


def test_the_ephemeral_limit_stays_above_the_tmp_volume(repo):
    """The limit covers the /tmp emptyDir plus the node's rotated container
    logs. At or below the volume's own sizeLimit the kubelet evicts the pod
    before the volume ever fills."""
    deployment = yaml.safe_load((repo.path / FLUX / "deployment.yaml").read_text())
    pod = deployment["spec"]["template"]["spec"]
    resources = pod["containers"][0]["resources"]
    limit = _quantity_mib(resources["limits"]["ephemeral-storage"])
    tmp = next(v for v in pod["volumes"] if v["name"] == "tmp")
    assert limit > _quantity_mib(tmp["emptyDir"]["sizeLimit"])
    # An inverted pair passes yamllint, kustomize and kubeconform: nothing but
    # the API server checks request against limit, and it does so at reconcile.
    assert _quantity_mib(resources["requests"]["ephemeral-storage"]) <= limit
    assert _quantity_mib(resources["requests"]["memory"]) <= _quantity_mib(
        resources["limits"]["memory"]
    )


def test_sso_middleware_matches_the_answer(repo):
    route = _public_route(repo)
    middlewares = [m["name"] for m in route["spec"]["routes"][0]["middlewares"]]
    assert ("authentik-auth" in middlewares) is repo.answers["enable_sso"]


def test_image_names_the_answered_registry(repo):
    deployment = yaml.safe_load((repo.path / FLUX / "deployment.yaml").read_text())
    image = deployment["spec"]["template"]["spec"]["containers"][0]["image"]
    expected = (
        f"{repo.answers['registry_host']}/{repo.answers['git_namespace'].lower()}/"
        f"{repo.answers['app_slug']}"
    )
    assert image.startswith(expected + ":"), image


def test_a_mixed_case_git_namespace_is_lowercased_only_in_the_image(
    rendered_mixed_case_git_namespace, answers_b
):
    """A registry rejects an upper-case path segment, so the image lower-cases
    the namespace. Every other use keeps the answered spelling."""
    root = rendered_mixed_case_git_namespace
    deployment = yaml.safe_load((root / FLUX / "deployment.yaml").read_text())
    image = deployment["spec"]["template"]["spec"]["containers"][0]["image"]
    assert f"/{MIXED_CASE_GIT_NAMESPACE.lower()}/" in image, image
    assert MIXED_CASE_GIT_NAMESPACE not in image, image

    # The image path is spelled by hand in several docs as well as the manifest.
    registry = answers_b["registry_host"]
    repository = f"{registry}/{MIXED_CASE_GIT_NAMESPACE.lower()}/{answers_b['app_slug']}"
    naming = set()
    for path, text in _text_files(root):
        assert f"{registry}/{MIXED_CASE_GIT_NAMESPACE}/" not in text, (
            f"{path.relative_to(root)} names the image without lower-casing the namespace"
        )
        if repository in text:
            naming.add(str(path.relative_to(root)))
    for expected in ("README.md", "docs/ONBOARDING.md", FLUX + "/deployment.yaml"):
        assert expected in naming, f"{expected} does not name the image repository"

    assert MIXED_CASE_GIT_NAMESPACE in (root / "CODEOWNERS").read_text()
    assert MIXED_CASE_GIT_NAMESPACE in _onboarding(root)


def test_every_image_path_lowercases_the_namespace():
    """A registry refuses an upper-case path segment, and the image reference is
    spelled by hand in several templates, each of which needs the filter."""
    pattern = re.compile(r"\{\{ registry_host \}\}/\{\{ git_namespace([^}]*)\}\}")
    spellings = 0
    for path, text in _template_files():
        for match in pattern.finditer(text):
            spellings += 1
            assert "| lower" in match.group(1), f"{path.name} spells the image path without | lower"
    assert spellings >= 5, f"the image-path scan matched only {spellings} spellings"


def test_the_readme_names_pyyaml_in_every_shape(repo):
    """`task lint` runs the egress fence on every shape, and that gate exits on
    a missing PyYAML, so the prerequisite list cannot be shape-gated."""
    readme = (repo.path / "README.md").read_text()
    assert "pyyaml" in readme, "the prerequisite list omits pyyaml"


def test_the_readme_names_the_vpa_answer(repo):
    readme = (repo.path / "README.md").read_text()
    assert ("a VPA" in readme) is repo.answers["enable_vpa"]


def test_license_names_the_answered_holder(repo):
    """The generated README links this file as the repository's own licence, so
    the holder has to be the tenant's. A static LICENSE puts the TEMPLATE
    author's name on someone else's work, in a file nobody re-reads."""
    copyright_lines = [
        line
        for line in (repo.path / "LICENSE").read_text().splitlines()
        if line.startswith("Copyright")
    ]
    assert len(copyright_lines) == 1, copyright_lines
    assert copyright_lines[0].endswith(repo.answers["copyright_holder"]), copyright_lines[0]


def _onboarding(root: Path) -> str:
    return (root / "docs" / "ONBOARDING.md").read_text(encoding="utf-8")


def _onboarding_step(root: Path, heading: str) -> str:
    """The body of one `### {heading}` step, up to the next step or section."""
    return re.split(r"\n##+ ", _onboarding(root).split(f"\n### {heading}", 1)[1])[0]


def _onboarding_wiring_text(root: Path) -> str:
    """The operator wiring file, out of its Markdown fence."""
    return _onboarding(root).split("```yaml")[1].split("```")[0]


def _onboarding_wiring(root: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(_onboarding_wiring_text(root)) if isinstance(d, dict)]


def _onboarding_store(root: Path) -> dict:
    stores = [d for d in _onboarding_wiring(root) if d.get("kind") == "ClusterSecretStore"]
    assert len(stores) == 1, "the wiring file must carry exactly one store"
    return stores[0]


# The cluster repo's tenant gates, run on the wiring file alone. Every render
# caps `tenant-local` claims, so the storage-class arm is on.
TENANT_GATES = (
    ("check-tenant-wiring.py", "--tenant-storage-class"),
    ("check-tenant-traefik-isolation.py",),
)


def _tenant_gate_results(wiring: str, scratch: Path, slug: str) -> dict[str, tuple[int, str]]:
    """Each tenant gate's exit code and output over `wiring` saved as one file."""
    path = scratch / f"{slug}.yaml"
    path.write_text(wiring, encoding="utf-8")
    results = {}
    for gate, *flags in TENANT_GATES:
        done = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / gate), *flags,
             "--repo-root", str(scratch), str(path)],
            capture_output=True, text=True, check=False,
        )
        results[gate] = (done.returncode, done.stdout + done.stderr)
    return results


def test_the_onboarding_wiring_passes_the_cluster_tenant_gates(repo, tmp_path):
    """The cluster repo's pipeline refuses a wiring file these gates fail: PSS
    labels, caps, prune guards, store scope and vault, the Kustomizations, the
    hostname grant and its Deny bindings."""
    results = _tenant_gate_results(
        _onboarding_wiring_text(repo.path), tmp_path, repo.answers["app_slug"]
    )
    for gate, (code, output) in results.items():
        assert code == 0, f"{gate} exited {code}:\n{output}"


def _unscope_store(wiring: list[dict], namespace: str) -> None:
    (store,) = [d for d in wiring if d.get("kind") == "ClusterSecretStore"]
    del store["spec"]["conditions"]


def _drop_certificate_binding(wiring: list[dict], namespace: str) -> None:
    name = f"tenant-certificates-{namespace}"
    kept = [d for d in wiring if d.get("metadata", {}).get("name") != name]
    assert len(kept) == len(wiring) - 1, f"the wiring carries no {name}"
    wiring[:] = kept


def _uncap_tenant_local(wiring: list[dict], namespace: str) -> None:
    (quota,) = [d for d in wiring if d.get("kind") == "ResourceQuota"]
    del quota["spec"]["hard"]["tenant-local.storageclass.storage.k8s.io/requests.storage"]


@pytest.mark.parametrize(
    ("mutate", "gate", "message"),
    [
        (_unscope_store, "check-tenant-wiring.py", "has no spec.conditions"),
        (_drop_certificate_binding, "check-tenant-traefik-isolation.py",
         "has no binding of tenant-certificates"),
        (_uncap_tenant_local, "check-tenant-wiring.py",
         "does not cap persistent volume claims"),
    ],
    ids=["unscoped-store", "no-certificate-binding", "uncapped-tenant-local"],
)
def test_the_tenant_gates_fail_a_broken_wiring(rendered, answers, tmp_path, mutate, gate, message):
    """Negative controls: each parametrized breach reds the same extraction and
    invocation the positive case runs, so a dropped gate or flag fails here."""
    wiring = _onboarding_wiring(rendered)
    mutate(wiring, answers["app_namespace"])
    results = _tenant_gate_results(
        yaml.safe_dump_all(wiring, sort_keys=False), tmp_path, answers["app_slug"]
    )
    code, output = results[gate]
    assert code == 1, f"{gate} exited {code}:\n{output}"
    assert message in output, output


def test_a_render_with_no_backend_wires_no_store(rendered_no_secrets):
    # Assert on the documents, not the prose: the `none` branch of step O1
    # tells the operator to skip "the `ClusterSecretStore` in O3".
    kinds = [d["kind"] for d in _onboarding_wiring(rendered_no_secrets)]
    assert "ClusterSecretStore" not in kinds, (
        "a store the tenant has nothing to read from, and one more "
        "cluster-scoped object for the operator to keep scoped"
    )
    assert "externalsecret.yaml" not in _kustomization(rendered_no_secrets)["resources"]


def test_the_wiring_kustomization_points_at_this_repo(repo):
    """The wiring Kustomization is the whole link between cluster and repo. The
    tenant gates hold its shape; this holds it to this repo's trees and answers."""
    slug = repo.answers["app_slug"]
    docs = {
        d["metadata"]["name"]: d["spec"]
        for d in _onboarding_wiring(repo.path)
        if d.get("kind") == "Kustomization"
    }
    # The database component adds its own Kustomization (kubernetes/db).
    trees = {slug: FLUX, **({f"{slug}-db": DB} if _wants_db(repo.answers) else {})}
    assert set(docs) == set(trees), f"wiring Kustomizations: {sorted(docs)}"
    for name, tree in trees.items():
        spec = docs[name]
        assert spec["path"].removeprefix("./") == tree, spec["path"]
        assert (repo.path / tree / "kustomization.yaml").is_file()
        assert spec["targetNamespace"] == repo.answers["app_namespace"]
        assert spec["sourceRef"]["name"] == slug
        # The gates also allow cluster-versions; this template reads cluster-config alone.
        sources = [entry["name"] for entry in spec["postBuild"]["substituteFrom"]]
        assert sources == ["cluster-config"], f"{name} substitutes from {sources}"
        # The tenant gates require Orphan on the db tree; the app tree goes on removal.
        if tree != DB:
            assert "deletionPolicy" not in spec, f"{name} orphans the app on removal"
    onboarding = _onboarding(repo.path)
    assert "No `postBuild` block" not in onboarding, "the prose contradicts the wiring"


def test_gitlab_wiring_store_names_the_answered_instance(rendered_b, answers_b):
    """The ESO GitLab store names `gitlab_api_url` and the capitalised
    `SecretRef`. Either wrong and the ExternalSecret never syncs, pointing
    nowhere near this file (docs/CONSUMING.md § Secrets).
    """
    provider = _onboarding_store(rendered_b)["spec"]["provider"]["gitlab"]
    assert provider["url"] == answers_b["gitlab_api_url"]
    assert answers_b["gitlab_api_url"] != f"https://{answers_b['git_host']}", (
        "the fixture must answer the two differently, or this gate proves nothing"
    )
    assert "SecretRef" in provider["auth"], f"auth keys: {sorted(provider['auth'])}"


# The reference cluster's platform vault. A tenant render never names it: a
# store or token covering it lets the tenant namespace read every platform item.
CLUSTER_VAULT = "Homelab"

# The rendered files that describe the secrets model to a person or an agent.
SECRETS_MODEL_DOCS = (
    "docs/ONBOARDING.md",
    "docs/ARCHITECTURE.md",
    "README.md",
    "CLAUDE.md",
    ".claude/skills/project-development/SKILL.md",
)


def test_onepassword_store_and_onboarding_name_only_the_tenant_vault(repo):
    """The store, the token and the grant all name the tenant's own vault, and
    no rendered doc names the cluster's vault or a shared-vault model."""
    if repo.answers["secrets_backend"] != "onepassword":
        pytest.skip("the 1Password branch is not rendered")
    vault = repo.answers["onepassword_vault"]
    assert vault == repo.answers["app_namespace"]
    provider = _onboarding_store(repo.path)["spec"]["provider"]["onepassword"]
    assert provider["vaults"] == {vault: 1}, f"store vaults: {provider['vaults']}"
    onboarding = _onboarding(repo.path)
    named = re.findall(r'(?:--vaults?|op vault (?:get|create)) "([^"]*)"', onboarding)
    assert named, "O1 renders no op command scoped to a vault"
    assert set(named) == {vault}, f"op commands name vaults {sorted(set(named))}"
    for command in ("op vault create", "op connect vault grant", "op connect token create"):
        assert command in onboarding, f"O1 lacks `{command}`"
    slug = repo.answers["app_slug"]
    assert f"op connect token create <CLUSTER>-{slug}-eso" in onboarding, (
        "the Connect token name is not cluster-prefixed"
    )
    for rel in SECRETS_MODEL_DOCS:
        text = (repo.path / rel).read_text()
        assert CLUSTER_VAULT not in text, f"{rel} names the cluster vault"
        assert "shared vault" not in text, f"{rel} describes a shared-vault model"


def test_o2b_names_a_single_pull_host_once(tmp_path_factory, answers):
    """With one registry host, O2b says so instead of naming it twice as if
    the credential were keyed to a second, internal name."""
    host = answers["registry_host"]
    render = render_app.render(
        tmp_path_factory.mktemp("render-one-pull-host"),
        dest_name="render-one-pull-host",
        data={"registry_pull_host": host},
    )
    o2b = _onboarding_step(render, "O2b")
    assert "keyed into the same credential" not in o2b, o2b
    assert f"both name `{host}`" in o2b, o2b


def test_an_unanswered_vault_defaults_to_the_namespace(tmp_path_factory):
    """The vault answer is REMOVED so only the default decides, and the
    namespace differs from every other answer so the store can only have taken it."""
    scratch = tmp_path_factory.mktemp("render-vault-default")
    answers_file = render_app.answers_without(scratch, "onepassword_vault")
    assert yaml.safe_load(answers_file.read_text())["secrets_backend"] == "onepassword"
    repo = render_app.render(
        scratch,
        answers=answers_file,
        dest_name="render-vault-default",
        data={"app_namespace": "driftwood"},
    )
    recorded = yaml.safe_load((repo / ".copier-answers.yml").read_text())
    assert recorded["onepassword_vault"] == "driftwood"
    provider = _onboarding_store(repo)["spec"]["provider"]["onepassword"]
    assert provider["vaults"] == {"driftwood": 1}, f"store vaults: {provider['vaults']}"


def test_a_boolean_shaped_vault_name_survives_the_store(tmp_path_factory):
    """`no` is a YAML 1.1 boolean, and Kubernetes parses manifest YAML with a
    1.1 parser. Unquoted, the key becomes `false` and the store names a vault
    that does not exist."""
    render = render_app.render(
        tmp_path_factory.mktemp("render-bool-vault"),
        dest_name="render-bool-vault",
        data={"app_namespace": "no", "onepassword_vault": "no"},
    )
    assert _onboarding_store(render)["spec"]["provider"]["onepassword"]["vaults"] == {"no": 1}


def test_pull_secret_is_wired_into_the_pod(repo):
    """An ExternalSecret with no `imagePullSecrets:` entry is a Secret the
    kubelet never reads — the pull still fails, one layer further down."""
    deployment = yaml.safe_load((repo.path / FLUX / "deployment.yaml").read_text())
    names = [s["name"] for s in deployment["spec"]["template"]["spec"].get("imagePullSecrets", [])]
    assert bool(names) is repo.answers["enable_registry_pull_secret"]


# --------------------------------------------------------------------------
# CI shape
# --------------------------------------------------------------------------

# shape -> (files it must ship, files it must not)
SHAPE_FILES = {
    "gitlab_selfhosted": (
        [
            ".gitlab-ci.yml",
            ".gitlab/secret-detection-ruleset.toml",
            "scripts/check-lib-pins.py",
            "scripts/check-ci-include-job-names.py",
        ],
        [".github/workflows/ci.yml"],
    ),
    # build-image.yml is not listed here: it is gated on `enable_image_build`
    # as well as the shape, so it has its own test below.
    "github": (
        [".github/workflows/ci.yml"],
        [
            ".gitlab-ci.yml",
            ".gitlab/secret-detection-ruleset.toml",
            "scripts/check-lib-pins.py",
            "scripts/check-ci-include-job-names.py",
        ],
    ),
    # Issue and change-request templates are NOT here: they follow the `forge`
    # answer, which test_forge_metadata_follows_the_forge holds.
    "none": (
        [],
        [
            ".gitlab-ci.yml",
            ".github/workflows/ci.yml",
            ".gitlab/secret-detection-ruleset.toml",
            "scripts/semantic-release.py",
            "scripts/check-ci-include-job-names.py",
        ],
    ),
}

# Sentences the tenant-side docs may only carry on a shape where no pipeline runs
# the three manifest gates. On `github` the vendored ci.yml's `manifest-gates`
# job does, so a doc claiming otherwise hides a gate the tenant has.
NO_PIPELINE_GATE_CLAIMS = (
    "No CI job on this shape runs it",
    "the vendored GitHub workflow has no step",
)


def test_ci_shape_keeps_exactly_its_own_files(repo):
    present, absent = SHAPE_FILES[repo.answers["ci_shape"]]
    for relpath in present:
        assert (repo.path / relpath).is_file(), f"{relpath} missing from shape {repo.label}"
    for relpath in absent:
        assert not (repo.path / relpath).exists(), f"{relpath} survived into shape {repo.label}"


GITHUB_WORKFLOW = Path(".github/workflows/ci.yml")

# The gates the GitLab shape runs as netpol-check, scrape-wiring-check and
# kustomization-check. The vendored `manifest-gates` job warns and skips a gate
# the repo does not ship, so a render that stops shipping one goes ungated green.
EXPECTED_GATES = {
    "check-netpol-except-parity.py",
    "check-scrape-wiring.py",
    "check-kustomization.py",
}

# The job calls each gate through its own `run_gate <script> [args...]` helper,
# which prefixes `scripts/` itself.
RUN_GATE = re.compile(r"run_gate ([\w.-]+\.py)")


def _workflow(root: Path) -> dict:
    return yaml.safe_load((root / GITHUB_WORKFLOW).read_text(encoding="utf-8"))


def _check_gates(workflow: dict, root: Path) -> None:
    """Assert the job runs exactly the three gates and the render ships each."""
    names: set[str] = set()
    for step in workflow["jobs"]["manifest-gates"]["steps"]:
        names.update(RUN_GATE.findall(step.get("run") or ""))
    missing = sorted(name for name in names if not (root / "scripts" / name).is_file())
    assert not missing, f"{GITHUB_WORKFLOW} names scripts the render does not ship: {missing}"
    assert names == EXPECTED_GATES, (
        f"{GITHUB_WORKFLOW} runs {sorted(names)}, not the three manifest gates "
        f"{sorted(EXPECTED_GATES)}"
    )


def test_the_manifest_gates_job_runs_exactly_the_three_gates(rendered_b):
    """The job skips a gate the repo does not ship, so the render has to carry
    all three, and a dropped call leaves its invariant ungated on this shape."""
    _check_gates(_workflow(rendered_b), rendered_b)


@pytest.mark.parametrize(
    "replacement", [":", "run_gate check-nothing.py"], ids=["dropped", "missing-script"]
)
def test_a_mutated_gate_call_fails_the_job_check(rendered_b, replacement):
    workflow = copy.deepcopy(_workflow(rendered_b))
    step = next(
        s for s in workflow["jobs"]["manifest-gates"]["steps"] if RUN_GATE.search(s.get("run") or "")
    )
    assert "run_gate check-kustomization.py" in step["run"], "the mutation missed"
    step["run"] = step["run"].replace("run_gate check-kustomization.py", replacement)
    with pytest.raises(AssertionError):
        _check_gates(workflow, rendered_b)


def _pyyaml_pins(root: Path) -> dict[str, set[str]]:
    """Every PyYAML pin this render ships, by source. One CI shape renders, so a
    missing pipeline file contributes nothing rather than failing."""
    pins: dict[str, set[str]] = {}
    if (root / GITHUB_WORKFLOW).is_file():
        pins["github-workflow"] = {_workflow(root)["env"]["PYYAML_VERSION"]}
    pipeline = root / ".gitlab-ci.yml"
    if pipeline.is_file():
        gate = render_app.load_ci(pipeline)[".pyyaml-gate"]
        pins["gitlab-pyyaml-gate"] = {gate["variables"]["PYYAML_VERSION"]}
    precommit = root / ".pre-commit-config.yaml"
    if precommit.is_file():
        config = yaml.safe_load(precommit.read_text(encoding="utf-8"))
        found = {
            dep.split("==", 1)[1]
            for repo in config["repos"]
            for hook in repo.get("hooks") or []
            for dep in hook.get("additional_dependencies") or []
            if dep.startswith("pyyaml==")
        }
        if found:
            pins["pre-commit"] = found
    return pins


def _check_one_pin(roots: list[Path]) -> None:
    pins: dict[str, set[str]] = {}
    for root in roots:
        for source, versions in _pyyaml_pins(root).items():
            pins.setdefault(source, set()).update(versions)
    assert set(pins) == {"github-workflow", "gitlab-pyyaml-gate", "pre-commit"}, (
        f"a PyYAML pin went away — collected {sorted(pins)}"
    )
    versions = set().union(*pins.values())
    assert len(versions) == 1, f"the shapes gate on different PyYAML releases: {pins}"


def test_one_pyyaml_pin_across_the_render(rendered_b, rendered):
    """The vendored workflow's `PYYAML_VERSION` is the library's, and the GitLab
    pipeline's pin and the pre-commit hooks' follow it. Nothing else holds them."""
    _check_one_pin([rendered_b, rendered])


def test_a_drifted_pyyaml_pin_fails_the_pin_check(tmp_path, rendered_b, rendered):
    drifted = tmp_path / "drifted"
    shutil.copytree(rendered_b, drifted)
    target = drifted / GITHUB_WORKFLOW
    shipped = _pyyaml_pins(drifted)["github-workflow"].pop()
    target.write_text(
        target.read_text(encoding="utf-8").replace(shipped, f"{shipped}.post1"),
        encoding="utf-8",
    )
    assert _pyyaml_pins(drifted)["github-workflow"] != {shipped}, "the rewrite missed the pin"
    with pytest.raises(AssertionError):
        _check_one_pin([drifted, rendered])


def test_no_pipeline_gate_wording_stays_off_the_github_shape(repo):
    """The `github` shape runs the three manifest gates, so its docs may not tell the
    tenant nothing does. The `none` branch is the control: the same search finds the
    wording where it belongs, so a failure on `github` means the doc, not the search.
    """
    architecture = (repo.path / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    if repo.answers["ci_shape"] == "github":
        stale = [claim for claim in NO_PIPELINE_GATE_CLAIMS if claim in architecture]
        assert not stale, f"{repo.label} still claims {stale}"
    elif repo.answers["ci_shape"] == "none":
        assert NO_PIPELINE_GATE_CLAIMS[0] in architecture


def test_raising_the_skip_budget_names_the_pipelines_own_copy(repo):
    """`task flux-lint` and the pipeline hold separate skip budgets, so a doc that
    names only `Taskfile.yml` leaves the tenant with a red job and nowhere to look.
    """
    architecture = (repo.path / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    shape = repo.answers["ci_shape"]
    if shape == "gitlab_selfhosted":
        assert "passing `allowed_skips` to the" in architecture, (
            f"{repo.label} tells the tenant to raise ALLOWED_SKIPS without naming the "
            "include input the pipeline reads"
        )
        pipeline = (repo.path / ".gitlab-ci.yml").read_text(encoding="utf-8")
        assert "allowed_skips" not in pipeline, (
            "the flux-lint include now passes allowed_skips, so the doc sentence asking "
            "the tenant to add it is stale"
        )
    elif shape == "github":
        assert "repository variable `ALLOWED_SKIPS`" in architecture, (
            f"{repo.label} does not send the tenant to the repository variable the "
            "vendored flux-lint job reads"
        )
        workflow = (repo.path / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        assert "vars.ALLOWED_SKIPS" in workflow, (
            "the vendored ci.yml does not read vars.ALLOWED_SKIPS; the doc points at "
            "a variable nothing consumes"
        )
    else:
        assert "fails at zero" not in architecture, (
            f"{repo.label} has no pipeline, so a second skip budget is not its problem"
        )


def test_the_pull_registry_answer_reaches_nothing_without_the_credential(repo):
    """`registry_pull_host` renders only inside the pull-credential
    ExternalSecret, which is why copier stops asking for it when that component
    is off."""
    if repo.answers["enable_registry_pull_secret"]:
        pytest.skip("the credential is on, so the answer renders")
    value = repo.answers["registry_pull_host"]
    offenders = [
        str(path.relative_to(repo.path))
        for path, text in _text_files(repo.path)
        if value in text and path.name != ".copier-answers.yml"
    ]
    assert not offenders, f"{value} reached {offenders} with the pull credential off"


def test_forge_metadata_follows_the_forge(repo):
    """A repo with no pipeline still lives on a forge, and that is what decides
    whether it gets GitLab issue and merge-request templates."""
    wanted = repo.answers["forge"] == "gitlab"
    for rel in (
        ".gitlab/issue_templates/Bug.md",
        ".gitlab/issue_templates/Feature.md",
        ".gitlab/merge_request_templates/Default.md",
    ):
        assert (repo.path / rel).is_file() is wanted, rel


def test_the_image_build_answer_governs_both_shapes(repo):
    """`enable_image_build` means the same thing on either forge.

    The GitLab shape gates its build job; the GitHub shape gates the whole
    workflow file, an inert one still firing with `packages: write`.
    """
    builds = repo.answers["enable_image_build"]
    assert (repo.path / "Dockerfile").is_file() is builds
    if repo.answers["ci_shape"] == "github":
        assert (repo.path / ".github/workflows/build-image.yml").is_file() is builds
    elif repo.answers["ci_shape"] == "gitlab_selfhosted":
        ci = (repo.path / ".gitlab-ci.yml").read_text()
        assert ("/ci/build/docker-build.yml" in ci) is builds


# "CI build" is one spelling of the claim; `:<short-sha>` is the OTHER, and the
# one a manifest comment reaches for. Both are gated on the shape everywhere
# they appear, so either surviving into the pipeline-less render is the bug.
_CI_BUILD_CLAIM = re.compile(r"\bCI builds?\b|<short-sha>")


def test_the_pipeline_less_shape_promises_no_ci_build(rendered_none, answers):
    """The pipeline-less render never claims a CI build pushes the image.

    `ci_shape: none` with `enable_image_build: true` is reachable, and the
    prose describing the image tag is gated on the build answer alone.
    """
    assert answers["enable_image_build"], "fixture A must build an image for this to bite"
    offenders = [
        str(path.relative_to(rendered_none))
        for path, text in _text_files(rendered_none)
        if _CI_BUILD_CLAIM.search(text)
    ]
    assert not offenders, f"pipeline-less render claims a CI build in: {offenders}"


def test_manifests_are_identical_across_ci_shapes(rendered, rendered_none):
    """Flux deploys the repo in every shape, so the shape must not reach
    kubernetes/. These two renders differ in ci_shape and nothing else, so any
    difference under kubernetes/ is the pipeline leaking into the deployment."""
    for path in _manifests(rendered):
        other = rendered_none / FLUX / path.name
        assert other.is_file(), f"{path.name} missing from the none-shape render"
        assert path.read_text() == other.read_text(), f"{path.name} differs by CI shape"


# The one string a library `include: ref:` is allowed to be: an input
# interpolates before `include:` resolves, where a variable does not yet exist.
LIB_REF_INPUT = "$[[ inputs.lib_ref ]]"


def test_generated_pipeline_pins_the_library(rendered, answers):
    pipeline = rendered / ".gitlab-ci.yml"
    assert render_app.declared_lib_ref(pipeline) == answers["lib_ref"], (
        "the `spec:` header does not declare the answered tag, so the pins below "
        "read something else"
    )
    ci = render_app.load_ci(pipeline)
    assert ci["variables"]["WEISSSRV_LIB_REF"] == LIB_REF_INPUT
    includes = [i for i in ci["include"] if isinstance(i, dict) and "project" in i]
    assert includes, "the pipeline includes no library templates"
    for include in includes:
        assert include["project"] == answers["lib_project"]
        assert include["ref"] == LIB_REF_INPUT, (
            "every library include reads the declared ref, so the answered tag "
            "reaches the pipeline through exactly one line"
        )


def test_generated_pipeline_cancels_superseded_jobs(rendered):
    """GitLab's conservative default cancels only jobs marked interruptible, so
    without both keys a push to an open change request leaves the previous
    pipeline running. `main` never cancels: its pipeline cuts the tag."""
    ci = render_app.load_ci(rendered / ".gitlab-ci.yml")
    assert ci["default"]["interruptible"] is True
    assert ci["workflow"]["auto_cancel"]["on_new_commit"] == "interruptible"
    main = [
        rule
        for rule in ci["workflow"]["rules"]
        if 'CI_COMMIT_BRANCH == "main"' in str(rule.get("if", ""))
    ]
    assert main, "the pipeline has no main-branch workflow rule"
    assert main[0]["auto_cancel"]["on_new_commit"] == "none"


# Pin names shared by this repo's pipeline and the one it generates.
PIN_SUFFIXES = ("_VERSION", "_SHA256")


def _toolchain_pins(ci: dict) -> dict[str, str]:
    """Every `*_VERSION` / `*_SHA256` variable in a pipeline, wherever it is
    declared: top-level `variables` or any job's, hidden templates included."""
    blocks = [ci.get("variables") or {}]
    blocks += [job.get("variables") or {} for job in ci.values() if isinstance(job, dict)]
    return {
        name: str(value)
        for block in blocks
        for name, value in block.items()
        if name.endswith(PIN_SUFFIXES)
    }


def test_the_two_pipelines_pin_one_toolchain(rendered):
    """This repo lints its own corpus with the tool versions it hands the
    tenant. A pin moved on one side only leaves the two reading different
    toolchains, so the template passes gates the generated repo fails."""
    ours = _toolchain_pins(render_app.load_ci(REPO_ROOT / ".gitlab-ci.yml"))
    theirs = _toolchain_pins(render_app.load_ci(rendered / ".gitlab-ci.yml"))
    shared = sorted(set(ours) & set(theirs))
    assert shared, (
        "the two pipelines share no pin name, so this gate compared nothing: "
        f"ours={sorted(ours)}, generated={sorted(theirs)}"
    )
    drifted = [
        f"{name}: .gitlab-ci.yml pins {ours[name]}, the generated pipeline pins {theirs[name]}"
        for name in shared
        if ours[name] != theirs[name]
    ]
    assert not drifted, "the two pipelines disagree on a pin:\n  " + "\n  ".join(drifted)


def _includes(ci: dict, suffix: str) -> list[dict]:
    """The pipeline's mapping includes whose `file:` ends in `suffix`."""
    return [
        include for include in ci.get("include") or []
        if isinstance(include, dict) and str(include.get("file", "")).endswith(suffix)
    ]


def test_build_job_carries_the_privileged_tag(rendered, answers):
    """The build include carries the answered runner tag and CPU selector."""
    ci = render_app.load_ci(rendered / ".gitlab-ci.yml")
    build = _includes(ci, "docker-build.yml")
    assert len(build) == 1, "the image build must be included exactly once"
    assert build[0]["inputs"]["tags"] == [answers["privileged_runner_tag"]]
    assert build[0]["inputs"]["cpu_selector"] == answers["ci_cpu_selector"]


@pytest.mark.parametrize(
    "render_fixture", ["rendered", "rendered_gitlab_unlike", "rendered_gitlab_no_build"]
)
def test_only_the_image_build_names_a_runner_tag(request, render_fixture):
    """Every library include a tenant renders defaults `tags` to the shared
    tag-less runner, and of those docker-build alone requires one; a tag
    anywhere else moves that job off the shared runner."""
    ci = render_app.load_ci(request.getfixturevalue(render_fixture) / ".gitlab-ci.yml")
    tagged, jobs = render_app.runner_tagged(ci)
    expected = ["/ci/build/docker-build.yml"] if _includes(ci, "docker-build.yml") else []
    assert tagged == expected, tagged
    assert not jobs, f"local jobs set runner tags: {jobs}"


@pytest.mark.parametrize("render_fixture", ["rendered", "rendered_b"])
def test_the_doc_links_job_runs_the_task_s_path_pass(request, render_fixture):
    """The pipeline's doc-link job runs the backticked-path pass `task doc-links`
    runs, or CI passes a doc naming a file that no longer exists."""
    root = request.getfixturevalue(render_fixture)
    task = yaml.safe_load((root / "Taskfile.yml").read_text())["tasks"]["doc-links"]
    assert str(task["env"]["CHECK_DOC_LINKS_PATHS"]) == "1", task["env"]
    if (root / ".gitlab-ci.yml").is_file():
        ci = render_app.load_ci(root / ".gitlab-ci.yml")
        links = _includes(ci, "docs-link-check.yml")
        assert len(links) == 1 and links[0]["inputs"].get("check_paths") is True, links
        return
    workflow = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["lint-docs-links"]["steps"]
    runs = [step for step in steps if "check-doc-links.py" in str(step.get("run", ""))]
    assert runs and str(runs[0].get("env", {}).get("CHECK_DOC_LINKS_PATHS")) == "1", runs


@pytest.mark.parametrize("render_fixture", ["rendered", "rendered_gitlab_unlike"])
def test_the_image_build_runs_after_the_secret_scan(request, render_fixture):
    """The library's docker-build job sets no `needs:`, so stage order is the
    only thing keeping the privileged build off a tree the secret scan has not
    cleared."""
    ci = render_app.load_ci(request.getfixturevalue(render_fixture) / ".gitlab-ci.yml")
    stages = ci["stages"]
    assert stages.index("build") > stages.index("security"), stages
    build = _includes(ci, "docker-build.yml")
    assert "needs" not in (build[0].get("inputs") or {}), (
        "the build passes a `needs:` input, which would let it start before the security stage"
    )


def test_every_lint_include_reruns_on_its_own_targets(rendered):
    """A narrowed `changes:` must still name every path in `targets:`, or the
    job skips the commit it exists to gate."""
    ci = render_app.load_ci(rendered / ".gitlab-ci.yml")
    checked = 0
    for include in ci["include"]:
        if not isinstance(include, dict):
            continue
        inputs = include.get("inputs") or {}
        targets, changes = inputs.get("targets"), inputs.get("changes")
        if not targets or not changes:
            continue
        for target in str(targets).split():
            if target == ".":
                continue
            assert any(str(pattern).startswith(target) for pattern in changes), (
                f"{include['file']} lints {target} but no `changes:` entry covers it"
            )
            checked += 1
    assert checked, "no include declares both targets and changes — this gate examined nothing"


def _resolved(ci: dict, name: str, seen: tuple[str, ...] = ()) -> dict:
    """One job with its `extends:` chain applied, the job's own keys winning.

    GitLab refuses to create a pipeline whose job extends a name the pipeline
    does not define, so an unresolvable chain is no pipeline at all.
    """
    body = dict(ci[name])
    targets = body.pop("extends", [])
    for target in [targets] if isinstance(targets, str) else targets:
        assert target in ci, f"{name} extends {target}, which the pipeline does not define"
        assert target.startswith("."), f"{name} extends {target}, which is not a hidden job"
        assert target not in seen, f"extends cycle at {target}"
        body = _deep_merge(_resolved(ci, target, (*seen, name)), body)
    return body


def _deep_merge(parent: dict, child: dict) -> dict:
    """GitLab's `extends:` merge: hashes merge at every depth, and any other
    value, a list included, is replaced by the child's."""
    merged = dict(parent)
    for key, value in child.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def test_extends_merges_hashes_at_every_depth():
    """A job's own `variables:` adds to its template's, a nested hash merges
    too, and a list is replaced whole."""
    ci = {
        ".base": {
            "variables": {"A": "1", "B": "1"},
            "cache": {"key": "base", "policy": {"when": "always", "pull": True}},
            "script": ["base"],
        },
        "job": {
            "extends": ".base",
            "variables": {"B": "2"},
            "cache": {"policy": {"pull": False}},
            "script": ["job"],
        },
    }
    resolved = _resolved(ci, "job")
    assert resolved["variables"] == {"A": "1", "B": "2"}
    assert resolved["cache"] == {"key": "base", "policy": {"when": "always", "pull": False}}
    assert resolved["script"] == ["job"]


def _job_shell(ci: dict, name: str) -> str:
    """One job's `script:` as a shell program, its resolved `variables:` exported
    first, the way the runner sets them."""
    variables = _resolved(ci, name).get("variables") or {}
    exports = [f"export {key}={shlex.quote(str(value))}" for key, value in variables.items()]
    return "set -e\n" + "\n".join([*exports, *_resolved(ci, name)["script"]])


def test_every_local_job_resolves_its_extends_chain(rendered):
    """A job extending a name the pipeline does not define is not a red job:
    GitLab refuses the pipeline, so a generated repo would have none at all. The
    resolved `before_script` is what makes these gates runnable on the runner."""
    ci = render_app.load_ci(rendered / ".gitlab-ci.yml")
    checked = 0
    for name in render_app.ci_yaml.jobs(ci):
        body = _resolved(ci, name)
        assert body.get("stage") in ci["stages"], (
            f"{name} resolves to stage {body.get('stage')!r}, absent from `stages:`"
        )
        assert body.get("script"), f"{name} resolves to no script"
        install = " ".join(body.get("before_script") or [])
        pinned = (body.get("variables") or {}).get("PYYAML_VERSION")
        for token in " ".join(body["script"]).split():
            if not token.endswith(".py") or "import yaml" not in (rendered / token).read_text():
                continue
            assert pinned, f"{name} runs {token}, which imports yaml, but pins no PyYAML"
            assert f"pyyaml=={pinned}" in install.replace("$PYYAML_VERSION", str(pinned)), (
                f"{name} runs {token}, which imports yaml, but its resolved "
                "before_script installs no PyYAML"
            )
        checked += 1
    assert checked, "the pipeline declares no jobs of its own — this gate examined nothing"


def test_every_local_job_reruns_on_the_paths_it_names(rendered):
    """The sibling of the include gate, for the jobs this pipeline declares
    itself: a narrowed `changes:` must still name every path the job's script
    runs, or the job skips the commit it exists to gate."""
    ci = render_app.load_ci(rendered / ".gitlab-ci.yml")
    checked = 0
    for name in render_app.ci_yaml.jobs(ci):
        body = _resolved(ci, name)
        rules = [rule for rule in body.get("rules") or [] if isinstance(rule, dict)]
        assert any('CI_COMMIT_BRANCH == "main"' in str(rule.get("if", "")) for rule in rules), (
            f"{name} has no main-branch rule, so `changes:` would gate main as well"
        )
        merge = [rule for rule in rules if "merge_request_event" in str(rule.get("if", ""))]
        assert merge, f"{name} has no change-request rule, so it never gates one"
        changes = [str(pattern) for pattern in merge[0].get("changes") or []]
        if not changes:
            continue
        for token in " ".join(body["script"]).split():
            if not token.startswith(("kubernetes/", "scripts/")):
                continue
            # A directory token needs a file under it: `kubernetes/**/*` matches
            # kubernetes/flux/networkpolicy.yaml but not kubernetes/flux.
            covered = any(
                fnmatch(token, pattern) or fnmatch(f"{token}/networkpolicy.yaml", pattern)
                for pattern in changes
            )
            assert covered, f"{name} runs {token} but no `changes:` entry covers it"
            checked += 1
    assert checked, "no job names a path in its script — this gate examined nothing"


def test_secret_detection_carries_the_cpu_selector(rendered, answers):
    """Secret detection passes the answered CPU selector.

    The library defaults it to its own cluster's label domain, so unoverridden
    the scan is unschedulable and sits Pending until the job times out.
    """
    ci = render_app.load_ci(rendered / ".gitlab-ci.yml")
    scan = _includes(ci, "secret-detection.yml")
    assert len(scan) == 1, "secret detection must be included exactly once"
    assert scan[0]["inputs"]["cpu_selector"] == answers["ci_cpu_selector"]


def test_k8s_version_reaches_the_gitlab_shape(rendered, answers):
    """The other targeted gate: the answer must reach both places the GitLab
    shape validates from, so they cannot disagree."""
    ci = render_app.load_ci(rendered / ".gitlab-ci.yml")
    flux_lint = _includes(ci, "flux-lint.yml")
    assert flux_lint[0]["inputs"]["k8s_version"] == answers["k8s_version"]
    taskfile = yaml.safe_load((rendered / "Taskfile.yml").read_text())
    assert taskfile["vars"]["K8S_VERSION"] == answers["k8s_version"]


def test_the_crd_catalog_pin_reaches_the_gitlab_shape(rendered):
    """The include and the Taskfile hold the catalog commit as two literals, so
    only this test stops the job and `task flux-lint` resolving different CR
    schemas — and the include taking the library's own default silently."""
    ci = render_app.load_ci(rendered / ".gitlab-ci.yml")
    flux_lint = _includes(ci, "flux-lint.yml")
    passed = flux_lint[0]["inputs"].get("crd_catalog_ref")
    assert passed, (
        "the flux-lint include passes no crd_catalog_ref, so the job takes the "
        "library default while `task flux-lint` reads Taskfile.yml's pin"
    )
    taskfile = yaml.safe_load((rendered / "Taskfile.yml").read_text())
    assert passed == taskfile["vars"]["CRD_CATALOG_REF"], (
        f"the flux-lint include pins {passed} but Taskfile.yml pins "
        f"{taskfile['vars']['CRD_CATALOG_REF']}"
    )


# Include inputs whose value is a path in the render. Register a new
# path-bearing input here, or nothing holds it to a directory that exists.
PATH_INPUTS = ("targets", "kustomize_path", "cluster_dir")


def test_pipeline_lints_only_paths_that_exist(rendered):
    """ruff exits non-zero on a target path that does not exist, and pytest
    exits 4 on a missing test directory — a generated repo must never ship a job
    pointed at a directory the render did not produce."""
    ci = render_app.load_ci(rendered / ".gitlab-ci.yml")
    checked = []
    for include in ci["include"]:
        if not isinstance(include, dict):
            continue
        inputs = include.get("inputs") or {}
        for key in PATH_INPUTS:
            value = inputs.get(key)
            if not value or value == ".":
                continue
            for target in str(value).split():
                assert (rendered / target).exists(), (
                    f"{include['file']} {key} names missing {target}"
                )
                checked.append(target)
    assert checked, "no include declares a lint target — this gate examined nothing"


# Paths the GitHub workflow hands to a tool as a bare argument, each aborting
# the job when missing. The workflow is vendored and takes no copier answer, so
# it is checked against a real render. Guarded arguments do not belong here.
GITHUB_WORKFLOW_PATH_PATTERNS = (
    r"ruff check[^\n]*--output-format concise ([\w/ .-]+)",
    # flux-lint renders and the manifest gates walk this root unless the variable is set.
    r"MANIFEST_ROOT: \$\{\{ vars\.MANIFEST_ROOT \|\| '([\w/.-]+)' \}\}",
    r"python3 (scripts/[\w.-]+\.py)",
    r"yamllint -c ([\w./-]+)",
    r"gitleaks dir \. --config ([\w./-]+)",
)


def test_github_workflow_names_only_paths_that_exist(rendered_b):
    """Every one of these tools exits non-zero on a path that is not there, so a
    workflow pointed at something the render did not produce fails every run of
    a shape whose whole purpose is to gate."""
    workflow = (rendered_b / ".github" / "workflows" / "ci.yml").read_text()
    checked = []
    for pattern in GITHUB_WORKFLOW_PATH_PATTERNS:
        matches = re.findall(pattern, workflow)
        assert matches, f"no workflow line matched {pattern!r} — the gate moved or went away"
        for match in matches:
            for target in match.split():
                assert (rendered_b / target).exists(), f"ci.yml names missing {target}"
                checked.append(target)
    assert len(checked) >= len(GITHUB_WORKFLOW_PATH_PATTERNS)
    builds = re.findall(r"^\s*kustomize build (.+?) >", workflow, re.MULTILINE)
    assert builds and set(builds) == {'"$MANIFEST_ROOT"'}, f"kustomize build targets: {builds}"


def test_the_tenant_gitleaks_config_extends_the_default_rules(repo):
    """All three shapes read this file — GitLab Secret Detection, the vendored
    GitHub job and the pre-commit hook. Without `[extend] useDefault` its own
    rules are the whole ruleset, and it has none."""
    config = tomllib.loads((repo.path / ".gitleaks.toml").read_text(encoding="utf-8"))
    assert (config.get("extend") or {}).get("useDefault") is True, (
        "the tenant's .gitleaks.toml does not extend the default rules — "
        "every secret scan in the generated repo greens on any tree"
    )


# --------------------------------------------------------------------------
# The generated repo passes its own gates
# --------------------------------------------------------------------------


# Hint for the binaries no pip install provides. The validate-rendered-app job
# fetches them with sha256 verification and runs these same gates, so the
# test job skips them instead of failing.
_TOOLCHAIN_HINT = "Install it in the test job the way validate-rendered-app does."


def _run(repo_path: Path, *command: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=repo_path, capture_output=True, text=True, env=env)


def _stub_bin(bindir: Path, stubs: dict[str, str]) -> dict[str, str]:
    """An env whose PATH leads with `bindir`, holding one executable shell stub
    per name in `stubs`, each running its body."""
    bindir.mkdir(parents=True)
    for name, body in stubs.items():
        (bindir / name).write_text(f"#!/bin/sh\n{body}")
        (bindir / name).chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    return env


def _offline_kubeconform(bindir: Path) -> dict[str, str]:
    """An env whose kubeconform prints a clean summary, so a job script that runs
    flux-lint.sh over the real `kustomize build` needs no schema download."""
    return _stub_bin(bindir, {"kubeconform": f"printf '%s\\n' '{CLEAN_SUMMARY}'\n"})


def test_generated_repo_passes_yamllint(repo):
    require_tool(
        "yamllint",
        "generated-repo yamllint",
        "pip install yamllint in the test job.",
        ci_optional=True,
    )
    result = _run(
        repo.path, "yamllint", "--strict", "-c", "lint/yamllint-relaxed.yml", "."
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_generated_repo_passes_its_doc_link_check(repo, tmp_path):
    """Run the doc-link check the way the tenant's pipeline does, over tracked
    Markdown. The non-git fallback would skip AGENTS.md and the forge
    templates. The copy keeps the shared render free of a .git of its own.
    """
    tree = tmp_path / "tracked"
    shutil.copytree(repo.path, tree)
    for command in (("git", "init", "-q"), ("git", "add", "-A")):
        assert _run(tree, *command).returncode == 0, f"{command} failed"
    result = _run(tree, "python3", "scripts/check-doc-links.py")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "AGENTS.md" in _run(tree, "git", "ls-files", "--", "*.md").stdout, (
        "the tracked scan saw no AGENTS.md — this gate reads only docs/"
    )


def test_generated_pipeline_passes_its_own_pin_gate(rendered, answers):
    """The vendored gate the generated repo runs in CI, run here on the tree it
    was rendered into — so a drifted include fails on the template change that
    caused it."""
    result = _run(
        rendered, "python3", "scripts/check-lib-pins.py", "--project", answers["lib_project"]
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_generated_manifests_build(repo):
    require_tool("kustomize", "generated-manifest build", _TOOLCHAIN_HINT, ci_optional=True)
    result = _run(repo.path, "kustomize", "build", FLUX)
    assert result.returncode == 0, result.stderr
    kinds = [d["kind"] for d in yaml.safe_load_all(result.stdout) if d]
    assert "Deployment" in kinds and "Service" in kinds


def test_generated_manifests_validate(repo):
    for tool in ("kustomize", "kubeconform"):
        require_tool(tool, "generated-manifest validation", _TOOLCHAIN_HINT, ci_optional=True)
    if not os.environ.get("WEISSSRV_SCHEMA_NETWORK"):
        pytest.skip(
            "set WEISSSRV_SCHEMA_NETWORK=1 to fetch CRD schemas; "
            "validate-rendered-app runs this gate in CI"
        )
    built = _run(repo.path, "kustomize", "build", FLUX)
    assert built.returncode == 0, built.stderr
    # Read from the render, so this test and `task flux-lint` cannot resolve
    # different schema sets.
    catalog_ref = yaml.safe_load((repo.path / "Taskfile.yml").read_text())["vars"][
        "CRD_CATALOG_REF"
    ]
    catalog = validate_render.CATALOG_TEMPLATE.format(ref=catalog_ref)
    result = subprocess.run(
        [
            "kubeconform", "-strict", "-ignore-missing-schemas",
            "-kubernetes-version", repo.answers["k8s_version"],
            "-schema-location", "default", "-schema-location", catalog, "-summary",
        ],
        input=built.stdout,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # kubeconform exits 0 for a resource it skipped for want of a schema, so a
    # moved catalog path would leave this validating the built-in kinds alone.
    assert validate_render._kubeconform_skips(result.stdout) == [], result.stdout


def test_forced_pull_secret_under_no_backend_renders_nothing(tmp_path_factory):
    """A forced pull-secret answer under `secrets_backend: none` renders nothing.

    `when:` gates only the prompt, so `--data` can smuggle the answer past it;
    every render site is backend-guarded as well.
    """
    repo = render_app.render(
        tmp_path_factory.mktemp("render-forced-pull"),
        data={
            "secrets_backend": "none",
            "enable_registry_pull_secret": "true",
        },
    )
    flux = repo / "kubernetes" / "flux"
    assert not (flux / "externalsecret-registry.yaml").exists()
    assert "externalsecret-registry" not in (flux / "kustomization.yaml").read_text()
    assert "imagePullSecrets" not in (flux / "deployment.yaml").read_text()


@pytest.mark.parametrize(
    "fixture, shape",
    [
        (render_app.ANSWERS, "gitlab_selfhosted"),
        (render_app.ANSWERS_B, "github"),
    ],
    ids=["gitlab-shape", "github-shape"],
)
def test_unasked_pull_secret_defaults_on_with_a_backend_and_an_image_build(
    tmp_path_factory, fixture, shape
):
    """The computed pull-secret default resolves true on BOTH forge shapes when
    a backend and an image build are answered — it carries no ci_shape term.
    The answer is REMOVED rather than set, so only the default decides.
    """
    scratch = tmp_path_factory.mktemp(f"render-default-pull-on-{shape}")
    answers = yaml.safe_load(fixture.read_text())
    del answers["enable_registry_pull_secret"]
    # Both terms of the default are present, so the default itself is what is
    # under test. Fixture B answers enable_image_build false, so this arm turns
    # it on; the shape is what the parametrization varies.
    answers["enable_image_build"] = True
    assert answers["ci_shape"] == shape
    assert answers["secrets_backend"] != "none"
    answers_file = scratch / "answers-default-pull-on.yml"
    answers_file.write_text(yaml.safe_dump(answers))

    repo = render_app.render(
        scratch, answers=answers_file, dest_name=f"render-default-pull-on-{shape}"
    )

    flux = repo / FLUX
    recorded = yaml.safe_load((repo / ".copier-answers.yml").read_text())
    assert recorded["enable_registry_pull_secret"] is True, (
        "the computed default is what a `copier update` replays; a false here "
        "means the tenant's own answers file disagrees with their manifests"
    )

    external_secret = yaml.safe_load((flux / "externalsecret-registry.yaml").read_text())
    assert external_secret["kind"] == "ExternalSecret"
    assert external_secret["spec"]["target"]["template"]["type"] == "kubernetes.io/dockerconfigjson"
    assert "externalsecret-registry.yaml" in _kustomization(repo)["resources"]

    # The remoteRef shape is per-backend, and the gitlab arm renders nowhere
    # else in this suite.
    refs = [entry["remoteRef"] for entry in external_secret["spec"]["data"]]
    assert refs, "the pull credential reads no remote key"
    if answers["secrets_backend"] == "onepassword":
        slug = answers["app_slug"]
        assert all(ref["key"].startswith(f"{slug}: ") for ref in refs), refs
        assert all(ref.get("property") for ref in refs), refs
    else:
        assert {ref["key"] for ref in refs} == {
            "REGISTRY_PULL_TOKEN",
            "REGISTRY_PULL_USERNAME",
        }, refs
        assert all("property" not in ref for ref in refs), refs

    deployment = yaml.safe_load((flux / "deployment.yaml").read_text())
    pull_secrets = [
        s["name"] for s in deployment["spec"]["template"]["spec"]["imagePullSecrets"]
    ]
    assert pull_secrets == [external_secret["spec"]["target"]["name"]], (
        "the Deployment must name the Secret this ExternalSecret renders — a "
        "credential the pod never references is a Secret that syncs and does "
        "nothing"
    )


def test_the_registry_defaults_follow_the_forge_not_the_shape(tmp_path_factory):
    """`forge` says where the repo lives, so a GitHub-hosted repo with no
    pipeline is offered GHCR, not a self-hosted GitLab registry path. Both
    answers are REMOVED so only the defaults decide."""
    scratch = tmp_path_factory.mktemp("render-registry-default")
    answers = yaml.safe_load(render_app.ANSWERS.read_text())
    for key in ("registry_host", "registry_pull_host"):
        del answers[key]
    answers_file = scratch / "answers-registry-default.yml"
    answers_file.write_text(yaml.safe_dump(answers))

    repo = render_app.render(
        scratch,
        answers=answers_file,
        dest_name="render-registry-default",
        data={"ci_shape": "none", "forge": "github"},
    )
    recorded = yaml.safe_load((repo / ".copier-answers.yml").read_text())
    assert recorded["registry_host"] == "ghcr.io"
    assert recorded["registry_pull_host"] == "ghcr.io"


def test_unasked_pull_secret_defaults_off_without_a_backend(tmp_path_factory):
    """Unasked under no backend, the pull-secret default must compute `false`.

    The answer is removed so the default decides. On `true` copier goes on to ask
    registry_pull_host, whose `when:` has no backend guard, and records a dead pull host.
    """
    scratch = tmp_path_factory.mktemp("render-default-pull")
    answers = yaml.safe_load(render_app.ANSWERS.read_text())
    del answers["enable_registry_pull_secret"]
    answers["secrets_backend"] = "none"
    # The other term must be the one that turns it ON, or a missing backend
    # term would pass here.
    assert answers["enable_image_build"] is True
    answers_file = scratch / "answers-default-pull.yml"
    answers_file.write_text(yaml.safe_dump(answers))

    repo = render_app.render(scratch, answers=answers_file, dest_name="render-default-pull")

    flux = repo / FLUX
    assert not (flux / "externalsecret-registry.yaml").exists()
    assert "imagePullSecrets" not in (flux / "deployment.yaml").read_text()
    recorded = yaml.safe_load((repo / ".copier-answers.yml").read_text())
    assert "registry_pull_host" not in recorded


def test_rendered_markdown_has_no_blank_run(repo):
    """Three consecutive newlines are what an untrimmed `{% if %}` leaves
    behind, and the render is the only place that shows."""
    for path in sorted(repo.path.rglob("*.md")):
        assert "\n\n\n" not in path.read_text(encoding="utf-8"), (
            f"{path.relative_to(repo.path)} has a blank run — an untrimmed jinja block"
        )


# O2, O2b, O2d, O2e and O6b follow the secret backend, the pull credential, the
# CI shape, the image build and the database; every other step is unconditional,
# and a missing one leaves the operator a gap they cannot see.
ONBOARDING_STEPS = tuple(
    f"### O{n} " for n in ("1", "2c", "3", "4", "5", "6", "7", "8", "9", "10")
)


@pytest.mark.parametrize("step", ONBOARDING_STEPS)
def test_onboarding_ships_every_step(repo, step):
    onboarding = _onboarding(repo.path)
    assert step in onboarding, f"docs/ONBOARDING.md is missing {step.strip()}"


def test_onboarding_ships_the_secret_steps_with_a_backend(repo):
    onboarding = _onboarding(repo.path)
    wanted = repo.answers["secrets_backend"] != "none"
    assert ("### O2 " in onboarding) is wanted


def test_codeowners_warns_only_where_the_forge_needs_it(repo):
    """GitHub drops a bare organisation name as an unknown owner; GitLab does
    not, and the note would be noise there. The warning follows `forge`, which
    decides who parses the file."""
    warned = "@org/team" in (repo.path / "CODEOWNERS").read_text()
    assert warned is (repo.answers["forge"] == "github")


def _answer_set(answers_file: str, data: dict) -> tuple:
    """One render, as (answers file, its --data overrides) with values as CI
    spells them: copier takes every --data as a string."""
    return (answers_file, frozenset(_copier_data(data).items()))


def _render_validate_lines() -> list[tuple[str, dict[str, str], bool]]:
    """One entry per validate_render.py line: (answers file, its --data
    overrides, whether --lib-path rides with it)."""
    job = render_app.load_ci(REPO_ROOT / ".gitlab-ci.yml")["validate-rendered-app"]
    lines = []
    for command in job["script"]:
        if "tests/validate_render.py" not in command:
            continue
        tokens = shlex.split(command)
        answers_file = render_app.ANSWERS.name
        data: dict[str, str] = {}
        for index, token in enumerate(tokens):
            if token == "--answers":
                answers_file = Path(tokens[index + 1]).name
            elif token == "--data":
                key, _, value = tokens[index + 1].partition("=")
                data[key] = value
        lines.append((answers_file, data, "--lib-path" in tokens))
    return lines


def _render_validate_invocations() -> list[tuple]:
    return [_answer_set(answers_file, data) for answers_file, data, _ in _render_validate_lines()]


def test_every_pipeline_render_gets_the_include_contract_gate():
    """validate_render runs the include-contract gate only under --lib-path, and
    the gate returns [] for a render with no pipeline — so a GitLab-shape line
    without the flag skips it rather than failing."""
    lines = _render_validate_lines()
    assert lines, "validate-rendered-app runs no validate_render.py invocation"
    unguarded = []
    for answers_file, data, has_lib_path in lines:
        answers = yaml.safe_load((REPO_ROOT / "tests" / answers_file).read_text())
        if data.get("ci_shape", answers["ci_shape"]) != "gitlab_selfhosted":
            continue
        if not has_lib_path:
            unguarded.append(f"{answers_file} {sorted(data.items())}")
    assert not unguarded, (
        "these validate-rendered-app lines render a GitLab pipeline with no --lib-path, "
        "so the include-contract gate silently skips:\n  " + "\n  ".join(unguarded)
    )


def test_every_render_overrides_a_question_copier_declares():
    """copier drops an unknown --data key without a word, so a typo renders the
    base fixture and every assertion in that entry agrees with itself."""
    questions = set(QUESTION_NAMES)
    invocations = _render_validate_invocations()
    assert questions, "copier.yml declared no questions"
    assert invocations, "validate-rendered-app runs no validate_render.py invocation"
    unknown = {key for _, _, overrides in RENDERS.values() for key in overrides} - questions
    for _, data in invocations:
        unknown |= {key for key, _ in data} - questions
    assert not unknown, (
        f"not copier questions: {sorted(unknown)} — copier ignores an unknown "
        "--data key silently, so that render is just the base fixture"
    )


def test_every_render_reaches_the_real_toolchain():
    """tests/validate_render.py is the only place the rendered tree meets
    yamllint, kustomize and kubeconform, and it takes ONE render per invocation,
    so every entry in RENDERS needs a line rendering ITS answers."""
    covered = set(_render_validate_invocations())
    missing = []
    for label, (_, answers_fixture, overrides) in RENDERS.items():
        wanted = _answer_set(_ANSWER_PATHS[answers_fixture].name, overrides)
        if wanted not in covered:
            missing.append(f"{label}: {wanted[0]} {sorted(wanted[1])}")
    assert not missing, (
        "validate-rendered-app renders no tree with these answers:\n  " + "\n  ".join(missing)
    )


def test_the_shipped_agent_permissions_are_announced_and_deny_the_main_push(repo):
    """A tenant gets a permission allowlist it never asked for, so the rendered
    AGENTS.md names it, and the deny list keeps the rule the generated CLAUDE.md
    states as hard: no push to the default branch."""
    settings = repo.path / ".claude/settings.json"
    assert settings.is_file(), ".claude/settings.json does not render on this shape"
    permissions = json.loads(settings.read_text(encoding="utf-8"))["permissions"]
    assert any("main" in entry for entry in permissions["deny"]), (
        "no deny entry mentions main — a default-branch push is only `ask`ed"
    )
    agents = (repo.path / "AGENTS.md").read_text(encoding="utf-8")
    assert ".claude/settings.json" in agents, (
        "AGENTS.md does not mention the settings file the render ships"
    )


def test_the_shipped_deny_list_leaves_feature_branch_pushes_to_ask(repo):
    """A deny entry matching a feature-branch push turns the `ask` on `git push`
    into a refusal for every branch. A `Bash(prefix:*)` rule matches its prefix,
    `*` included, at a word boundary."""
    deny = json.loads((repo.path / ".claude/settings.json").read_text())["permissions"]["deny"]
    prefixes = [e[len("Bash(") : -len(":*)")] for e in deny if e.startswith("Bash(git push")]

    def denied(command: str) -> bool:
        return any(fnmatchcase(command, p) or fnmatchcase(command, p + " *") for p in prefixes)

    for command in (
        "git push origin HEAD",
        "git push origin HEAD:feat/x",
        "git push -u origin feat/x",
        "git push origin main-fix",
        "git push origin feat/x:refs/heads/maintenance",
    ):
        assert not denied(command), f"{command!r} is denied"
    for command in (
        "git push origin main",
        "git push origin HEAD:main",
        "git push origin feat/x:refs/heads/main",
    ):
        assert denied(command), f"{command!r} is not denied"


_NOT_PUSHED = "does NOT push to"


@pytest.fixture(scope="session")
def rendered_github_ghcr(tmp_path_factory) -> Path:
    """Fixture B building and pulling its image on ghcr.io, one host answered twice."""
    return render_app.render(
        tmp_path_factory.mktemp("render-github-ghcr"),
        answers=render_app.ANSWERS_B,
        dest_name="render-github-ghcr",
        data={
            "enable_image_build": "true",
            "enable_registry_pull_secret": "true",
            "registry_host": "ghcr.io",
            "registry_pull_host": "ghcr.io",
        },
    )


def test_a_non_ghcr_answer_is_flagged_on_the_github_shape(
    rendered_image_github, rendered, rendered_github_ghcr
):
    """The vendored build workflow publishes from its own literals, so an
    answered registry the GitHub shape cannot honour has to be said out loud —
    and only there. Both terms of the condition get a negative case."""
    flagged = _onboarding(rendered_image_github)
    assert _NOT_PUSHED in flagged

    gitlab = _onboarding(rendered)
    assert _NOT_PUSHED not in gitlab, "a GitLab tenant has no GitHub workflow to warn about"

    onboarding = _onboarding(rendered_github_ghcr)
    assert _NOT_PUSHED not in onboarding, "ghcr.io IS what the vendored workflow pushes to"

    # docs/VERSIONING.md tells the tenant where the build lands, so it may not
    # name a registry the vendored workflow never pushes to.
    versioning = (rendered_image_github / "docs" / "VERSIONING.md").read_text(encoding="utf-8")
    registry = yaml.safe_load(render_app.ANSWERS_B.read_text())["registry_host"]
    assert "ghcr.io/<owner>/<repo>" in versioning
    assert f"{registry}/" not in versioning, "the GitHub build pushes to GHCR, not this registry"


def test_the_pull_credential_steps_follow_the_forge(
    rendered, rendered_github_ghcr, tmp_path_factory
):
    """O2b's credential steps name an object the forge has. A GitHub-hosted
    tenant on a self-hosted registry gets neither the GHCR wording nor GitLab's
    deploy token."""
    shared = {"enable_image_build": "true", "enable_registry_pull_secret": "true"}
    other = render_app.render(
        tmp_path_factory.mktemp("render-pull-other"),
        answers=render_app.ANSWERS_B,
        dest_name="render-pull-other",
        data=shared,
    )
    steps = _onboarding(other)
    assert "### O2b " in steps
    assert "gitlab+deploy-token" not in steps, "a GitHub tenant has no GitLab deploy tokens"
    assert "read:packages" not in steps, "that scope belongs to ghcr.io, not this registry"

    assert "read:packages" in _onboarding(rendered_github_ghcr)
    # The same host answered twice deduplicates to one auths entry.
    registry = rendered_github_ghcr / FLUX / "externalsecret-registry.yaml"
    assert set(_auths(registry)) == {"ghcr.io"}

    gitlab = _onboarding(rendered)
    assert "read_registry" in gitlab and "gitlab+deploy-token" in gitlab
    assert "read:packages" not in gitlab, "that scope belongs to ghcr.io"


def _auths(path: Path, values: tuple[str, str] = ("robot", "plain-token")) -> dict:
    """The pull credential's dockerconfigjson, with the three Go actions evaluated the
    way ESO's v2 engine would. Real values prove the Jinja loop balances its own braces
    and commas, and that JSON metacharacters in a fetched value survive the quoting.
    """
    document = yaml.safe_load(path.read_text())
    body = document["spec"]["target"]["template"]["data"][".dockerconfigjson"]
    username, token = values
    auth = base64.b64encode(f"{username}:{token}".encode()).decode()
    substitutions = {
        r"\{\{ \.username \| toJson \}\}": json.dumps(username),
        r"\{\{ \.token \| toJson \}\}": json.dumps(token),
        r'\{\{ printf "%s:%s" \.username \.token \| b64enc \}\}': auth,
    }
    for pattern, replacement in substitutions.items():
        body, count = re.subn(pattern, lambda _match, value=replacement: value, body)
        assert count, f"the template does not contain the action {pattern}"
    assert "{{" not in body, f"an unevaluated Go action is left in {body!r}"
    return json.loads(body)["auths"]


def test_the_pull_credential_survives_a_token_with_json_metacharacters(repo):
    """The values are fetched, not authored here: a token carrying a quote or a backslash
    must not break out of the hand-built JSON. A broken one surfaces as ImagePullBackOff
    with the ExternalSecret still reporting Ready."""
    if not (
        repo.answers["enable_registry_pull_secret"] and repo.answers["secrets_backend"] != "none"
    ):
        pytest.skip("this render ships no pull credential")
    username, token = 'robot$ci"er', 'p"a\\ss\tword'
    auths = _auths(repo.path / FLUX / "externalsecret-registry.yaml", (username, token))
    assert set(auths) == {repo.answers["registry_host"], repo.answers["registry_pull_host"]}
    for entry in auths.values():
        assert entry["username"] == username
        assert entry["password"] == token
        assert base64.b64decode(entry["auth"]).decode() == f"{username}:{token}"


def test_the_unanswered_pull_host_follows_the_answered_registry(tmp_path_factory):
    """`registry_pull_host` defaults to registry_host, not to the forge: a
    GitHub-hosted repo pulling from another registry gets one auths entry and
    no GHCR credential walkthrough."""
    scratch = tmp_path_factory.mktemp("render-pull-host-default")
    answers = yaml.safe_load(render_app.ANSWERS_B.read_text())
    assert answers["forge"] == "github" and answers["registry_host"] != "ghcr.io", (
        "this case needs the GitHub forge on a registry that is not GHCR, or a "
        "pull host derived from the forge would pass by accident"
    )
    del answers["registry_pull_host"]
    answers_file = scratch / "answers-pull-host-default.yml"
    answers_file.write_text(yaml.safe_dump(answers))

    render = render_app.render(
        scratch,
        answers=answers_file,
        dest_name="render-pull-host-default",
        data={"enable_registry_pull_secret": "true"},
    )
    recorded = yaml.safe_load((render / ".copier-answers.yml").read_text())
    assert recorded["registry_pull_host"] == answers["registry_host"]
    assert set(_auths(render / FLUX / "externalsecret-registry.yaml")) == {
        answers["registry_host"]
    }
    onboarding = _onboarding(render)
    assert "read:packages" not in onboarding, (
        "the GHCR walkthrough must not render for an image that lives elsewhere"
    )


def test_onboarding_ships_the_pull_credential_step_with_the_component(repo):
    onboarding = _onboarding(repo.path)
    wanted = repo.answers["enable_registry_pull_secret"] and (
        repo.answers["secrets_backend"] != "none"
    )
    assert ("### O2b " in onboarding) is bool(wanted)


def test_the_github_shape_names_the_k8s_version_the_workflow_hardcodes(rendered_b, answers_b):
    """The vendored workflows carry the library's own `env.K8S_VERSION` literal
    and cannot take an answer, so the divergence has to be said out loud."""
    onboarding = _onboarding(rendered_b)
    assert "K8S_VERSION" in onboarding
    assert answers_b["k8s_version"] in onboarding


def _local_hooks(repo: Repo) -> list[dict]:
    hooks = yaml.safe_load((repo.path / ".pre-commit-config.yaml").read_text())
    return [h for r in hooks["repos"] if r["repo"] == "local" for h in r["hooks"]]


def _local_hook_ids(repo: Repo) -> list[str]:
    return [hook["id"] for hook in _local_hooks(repo)]


@pytest.mark.parametrize(
    "hook_id",
    ["netpol-except-parity", "scrape-wiring", "kustomization", "doc-links", "comment-length"],
)
def test_every_local_gate_has_a_pre_commit_hook(repo, hook_id):
    """On `github` and `none` there is no CI job for these, so the hook is the
    only automatic runner a tenant has."""
    assert hook_id in _local_hook_ids(repo), (
        f"no pre-commit hook runs {hook_id}, so on a pipeline-less repo nothing "
        "automatic does"
    )


def _hook_paths(hook: dict) -> list[str]:
    """The repo paths a hook's entry names, read through an `sh -c` wrapper and
    with a closing `$(...)` parenthesis stripped."""
    tokens = shlex.split(hook["entry"])
    if tokens[:2] == ["sh", "-c"]:
        tokens = shlex.split(tokens[2])
    paths = [token.rstrip(")") for token in tokens]
    return [token for token in paths if token.startswith(("scripts/", "kubernetes/"))]


def _hook_targets(repo: Repo, token: str) -> list[Path]:
    """The files or directories one entry path names, a glob expanded."""
    if "*" in token:
        return sorted(repo.path.glob(token))
    target = repo.path / token
    return [target] if target.exists() else []


def _hook_scanned_paths(repo: Repo, hook: dict) -> list[str]:
    """The render-relative paths a hook's `files:` filter has to match for
    pre-commit to invoke it: every Markdown file for doc-links, and the trees the
    entry names for the rest."""
    if hook["id"] == "doc-links":
        return [str(path.relative_to(repo.path)) for path in sorted(repo.path.rglob("*.md"))]
    scanned: list[str] = []
    for token in _hook_paths(hook):
        for target in _hook_targets(repo, token):
            found = [target] if target.is_file() else sorted(target.rglob("*.yaml"))
            scanned += [str(path.relative_to(repo.path)) for path in found]
    return scanned


def test_every_local_hook_runs_a_path_the_render_ships(repo):
    """The hook id being right proves nothing about what it runs, and on
    `github` and `none` the hooks are the only automatic runner. A `files:` that
    matches none of those paths is a hook pre-commit never invokes."""
    for hook in _local_hooks(repo):
        for token in _hook_paths(hook):
            assert _hook_targets(repo, token), (
                f"pre-commit hook {hook['id']} runs {token}, which the render "
                "does not ship"
            )
        if "files" not in hook:
            continue
        scanned = _hook_scanned_paths(repo, hook)
        assert scanned, f"hook {hook['id']} scans nothing this render ships"
        pattern = re.compile(hook["files"])
        assert any(pattern.search(path) for path in scanned), (
            f"hook {hook['id']} files: {hook['files']!r} matches none of the paths it "
            "runs over, so pre-commit never invokes it"
        )


def test_the_doc_links_hook_runs_the_task_s_path_pass(repo):
    """The hook carries the task's whole environment: without the path pass it
    passes a commit naming a missing file, and an ignore list that differs
    lets the task and the hook disagree on the same tree."""
    task = yaml.safe_load((repo.path / "Taskfile.yml").read_text())["tasks"]["doc-links"]
    task_env = {key: str(value) for key, value in task["env"].items()}
    hook = next(h for h in _local_hooks(repo) if h["id"] == "doc-links")
    tokens = shlex.split(hook["entry"])
    assert tokens[0] == "env", f"doc-links hook sets no environment: {hook['entry']}"
    hook_env = dict(t.split("=", 1) for t in tokens[1:] if "=" in t)
    assert hook_env == task_env
    # A doc breaks when a file it cites is renamed, so no Markdown-only filter.
    assert hook.get("always_run") is True and "files" not in hook, (
        "the doc-links hook runs the path pass but skips a commit that touches no Markdown"
    )


def test_the_hook_filter_check_can_fail(repo):
    """Negative control for the filter check above: a pattern matching nothing
    must match nothing, or that check greens on any `files:` value."""
    never = re.compile("^zzz-never-matches/")
    for hook in _local_hooks(repo):
        scanned = _hook_scanned_paths(repo, hook)
        assert scanned, f"hook {hook['id']} scans nothing this render ships"
        assert not any(never.search(path) for path in scanned), hook["id"]


def test_the_egress_fence_gate_ships_to_the_tenant(repo):
    """The tenant is who edits networkpolicy.yaml, so the gate has to be in
    their repo, not only in this template's render checks."""
    gate = repo.path / "scripts" / "check-netpol-except-parity.py"
    assert gate.is_file(), "the generated repo has no egress-fence gate"
    source = REPO_ROOT / "template" / "scripts" / gate.name
    assert gate.read_bytes() == source.read_bytes()
    taskfile = yaml.safe_load((repo.path / "Taskfile.yml").read_text())
    assert "netpol" in taskfile["tasks"]
    assert {"task": "netpol"} in taskfile["tasks"]["lint"]["cmds"], (
        "the netpol task exists but `task lint` does not run it"
    )
    local = _local_hook_ids(repo)
    assert "netpol-except-parity" in local, (
        "no pre-commit hook runs the fence, so on a pipeline-less repo nothing "
        "automatic does"
    )
    if repo.answers["ci_shape"] == "gitlab_selfhosted":
        ci = render_app.load_ci(repo.path / ".gitlab-ci.yml")
        assert "netpol-check" in ci, "the GitLab pipeline runs no egress-fence gate"


def test_the_rendered_manifests_pass_the_shipped_egress_gate(repo):
    """The gate the tenant runs, run here over the tree this template renders,
    so a manifest change that reds every generated repo fails on the change."""
    result = _run(repo.path, "python3", "scripts/check-netpol-except-parity.py", FLUX)
    assert result.returncode == 0, result.stdout + result.stderr


def _flux_lint_script(repo: Repo, **overrides: str) -> str:
    """The rendered flux-lint task as a runnable shell script: go-task's `{{.VAR}}`
    references are substituted from the Taskfile's `vars:` to a fixed point, because
    one var's value references another's. `overrides` stands in for a tenant's edit."""
    taskfile = yaml.safe_load((repo.path / "Taskfile.yml").read_text())
    taskfile["vars"].update(overrides)
    # `set -e`: go-task stops at the first failing cmd, and an unset errexit would
    # report only the last cmd's status once a render ships more than one.
    script = "set -e\n" + "\n".join(taskfile["tasks"]["flux-lint"]["cmds"])
    for _ in range(10):
        before = script
        for name, value in taskfile["vars"].items():
            script = script.replace("{{." + name + "}}", str(value))
        if script == before:
            break
    # go-task's literal-escape form, so the script matches what it hands sh.
    script = re.sub(r"\{\{`([^`]*)`\}\}", r"\1", script)
    leftover = [name for name in taskfile["vars"] if "{{." + name + "}}" in script]
    assert not leftover, f"unresolved go-task vars in the script under test: {leftover}"
    return script


# kubeconform's own wording when it is handed a FILE, which is what the gate
# does; a change to it must fail the tests rather than degrade the sed parse.
CLEAN_SUMMARY = (
    "Summary: 6 resources found in 1 file - Valid: 6, Invalid: 0, Errors: 0, Skipped: 0"
)
SKIPPED_SUMMARY = (
    "Summary: 6 resources found in 1 file - Valid: 3, Invalid: 0, Errors: 0, Skipped: 3"
)


INVALID_SUMMARY = (
    "Summary: 6 resources found in 1 file - Valid: 5, Invalid: 1, Errors: 0, Skipped: 0"
)
NO_SKIPPED_SUMMARY = "Summary: 6 resources found in 1 file - Valid: 6, Invalid: 0, Errors: 0"


def _stubbed_path(
    bindir: Path, render: str, summary: str, *, kubeconform_rc: int = 0
) -> dict[str, str]:
    """An env whose kustomize emits `render` and whose kubeconform prints
    `summary` and exits `kubeconform_rc`, so the rendered gate runs where
    neither binary is installed."""
    return _stub_bin(
        bindir,
        {
            "kustomize": f"printf '%s' '{render}'\n",
            "kubeconform": f"printf '%s\\n' '{summary}'\nexit {kubeconform_rc}\n",
        },
    )


@pytest.mark.parametrize(
    "render, summary, kubeconform_rc, overrides, expected_rc, message",
    [
        # kubeconform exits 0 on empty input, so the tenant's own gate has to notice.
        ("", CLEAN_SUMMARY, 0, {}, 1, "rendered no document"),
        # kubeconform also exits 0 on a resource it skipped for want of a schema.
        ("kind: Service\n", SKIPPED_SUMMARY, 0, {}, 1, "against no schema"),
        # A failing kubeconform reds the task and carries its own output.
        ("kind: Service\n", INVALID_SUMMARY, 1, {}, 1, "Invalid: 1"),
        # A budget the comparison cannot read is false inside an `if`, so it needs a guard.
        ("kind: Service\n", SKIPPED_SUMMARY, 0, {"ALLOWED_SKIPS": "two"}, 1, "non-negative integer"),
        # A summary the sed parse cannot read must red, not compare against "".
        ("kind: Service\n", NO_SKIPPED_SUMMARY, 0, {}, 1, "against no schema"),
        # The success path, the Skipped parse and the budget compare, needs its own run.
        ("kind: Service\n", CLEAN_SUMMARY, 0, {}, 0, "Skipped: 0"),
        # The tenant Kustomization substitutes strictly from cluster-config alone.
        ("kind: Service\nmetadata:\n  name: ${app_name}\n", CLEAN_SUMMARY, 0, {}, 1,
         "outside cluster-config's cluster_* set"),
        # A cluster_* key and a `$${...}` escape are what that substitution accepts.
        ("kind: Service\nmetadata:\n  name: ${cluster_domain}-$${app}\n", CLEAN_SUMMARY,
         0, {}, 0, "Skipped: 0"),
        # Flux's envsubst eats a positional `${1}` silently in-cluster.
        ("kind: Service\nmetadata:\n  name: ${1}\n", CLEAN_SUMMARY, 0, {}, 1,
         "envsubst mishandles"),
    ],
    ids=["empty", "skipped", "invalid", "bad-budget", "no-skip-count", "clean",
         "foreign-key", "cluster-key-and-escape", "malformed-ref"],
)
def test_the_manifest_gate(
    repo, tmp_path, render, summary, kubeconform_rc, overrides, expected_rc, message
):
    """The rendered flux-lint task, run against stubbed kustomize and kubeconform."""
    script = _flux_lint_script(repo, **overrides)
    assert "scripts/flux-lint.sh" in script, "the task does not run the vendored gate"
    env = _stubbed_path(tmp_path / "bin", render, summary, kubeconform_rc=kubeconform_rc)
    result = subprocess.run(
        ["sh", "-c", script], cwd=repo.path, capture_output=True, text=True, env=env
    )
    output = result.stdout if expected_rc == 0 else result.stdout + result.stderr
    assert (result.returncode != 0) == bool(expected_rc), result.stdout + result.stderr
    assert message in output, result.stdout + result.stderr


def test_the_docs_say_where_the_substitution_key_scan_runs(repo):
    """flux-lint.sh refuses a `${...}` key outside cluster_*, and the rendered
    docs say so; on the GitHub shape they also say the workflow job skips it,
    which holds only while the vendored ci.yml runs neither flux-lint.sh nor the scan."""
    doc = " ".join((repo.path / "docs" / "ARCHITECTURE.md").read_text().split())
    assert "`task flux-lint` also fails on a malformed `${`" in doc, "key-scan sentence names no subject"
    assert "outside cluster-config's `cluster_*` set" in doc, "flux-lint bullet omits the key scan"
    assert "The flux-lint gate fails any `${...}`" in doc, "platform contract omits the key scan"
    skip_note = "The `flux-lint` workflow job does not run this scan."
    if repo.answers["ci_shape"] != "github":
        assert skip_note not in doc
        return
    workflow = yaml.safe_load((repo.path / ".github" / "workflows" / "ci.yml").read_text())
    runs = [str(step.get("run", "")) for step in workflow["jobs"]["flux-lint"]["steps"]]
    runs_scan = any("flux-lint.sh" in run or "'^cluster_'" in run for run in runs)
    assert (skip_note in doc) != runs_scan, (
        "the GitHub shape's docs disagree with whether ci.yml runs scripts/flux-lint.sh"
    )


def test_the_flux_trees_substitute_only_cluster_keys(repo, tmp_path):
    """The tenant Kustomization substitutes strictly from cluster-config, so any
    other `${...}` key, or a malformed `${`, stops the whole tenant reconcile.
    The rendered flux-lint task runs that scan over every tree's real build."""
    require_tool("kustomize", "substitution-key scan", _TOOLCHAIN_HINT, ci_optional=True)
    env = _offline_kubeconform(tmp_path / "offline" / "bin")
    result = subprocess.run(
        ["sh", "-c", _flux_lint_script(repo)],
        cwd=repo.path, capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _gate_scripts(repo: Repo, task: str, job: str) -> list[str]:
    """Every shipped form of one local gate: the Taskfile task, and on the GitLab
    shape the pipeline job running the same check."""
    taskfile = yaml.safe_load((repo.path / "Taskfile.yml").read_text())
    assert {"task": task} in taskfile["tasks"]["lint"]["cmds"], (
        f"the {task} task exists but `task lint` does not run it"
    )
    # `set -e`: go-task and a GitLab job both stop at the first failing line, so
    # the database render's second cmd cannot mask the first one's failure here.
    scripts = ["set -e\n" + "\n".join(taskfile["tasks"][task]["cmds"])]
    if repo.answers["ci_shape"] == "gitlab_selfhosted":
        ci = render_app.load_ci(repo.path / ".gitlab-ci.yml")
        assert job in ci, f"the GitLab pipeline runs no {task} gate"
        scripts.append(_job_shell(ci, job))
    return scripts


def _gate_runs(repo: Repo, tmp_path: Path, name: str, task: str, job: str):
    """(work, script) for every shipped form of the gate, each on its own copy
    of the render."""
    for index, script in enumerate(_gate_scripts(repo, task, job)):
        work = tmp_path / f"{name}-{index}"
        shutil.copytree(repo.path, work)
        yield work, script


def test_the_kustomization_gate_rejects_an_unlisted_yml_manifest(repo, tmp_path):
    """A manifest kustomize never builds is never applied, and nothing else in a
    generated repo notices. Every shipped form passes the render, then reds."""
    for work, script in _gate_runs(
        repo, tmp_path, "kustomization", "kustomization", "kustomization-check"
    ):
        result = _run(work, "sh", "-c", script)
        assert result.returncode == 0, result.stdout + result.stderr
        (work / FLUX / "configmap.yml").write_text(
            "---\napiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: unlisted\n"
        )
        result = _run(work, "sh", "-c", script)
        assert result.returncode != 0, "an unlisted .yml manifest passed the gate"
        assert "not listed" in result.stdout + result.stderr


def test_the_scrape_gate_accepts_the_render(repo):
    """The shipped pair agrees, so this is the positive control the two
    negative cases below need to mean anything."""
    for script in _gate_scripts(repo, "scrape-wiring", "scrape-wiring-check"):
        result = _run(repo.path, "sh", "-c", script)
        assert result.returncode == 0, result.stdout + result.stderr


def test_the_scrape_gate_rejects_a_stale_port(repo, tmp_path):
    """A monitor scraping a port the allow does not name is `up == 0` and a
    page in the OPERATOR's Alertmanager, with nothing red in this repo."""
    if not repo.answers["enable_servicemonitor"]:
        pytest.skip("this render ships no ServiceMonitor")
    port = repo.answers["app_port"]
    for work, script in _gate_runs(
        repo, tmp_path, "stale-port", "scrape-wiring", "scrape-wiring-check"
    ):
        policies = (work / FLUX / "networkpolicy.yaml").read_text()
        assert f"port: {port}" in policies, "the scrape allow does not name the app port"
        (work / FLUX / "networkpolicy.yaml").write_text(
            policies.replace(f"port: {port}", f"port: {port + 1}")
        )
        result = _run(work, "sh", "-c", script)
        assert result.returncode != 0, "a scrape port no policy admits passed the gate"
        assert str(port) in result.stdout + result.stderr


def test_the_scrape_gate_accepts_an_inferred_policy_type(repo, tmp_path):
    """An allow with no `policyTypes` is inferred Ingress and admits the scrape,
    so red-lining it sends the tenant after a defect that is not there."""
    if not repo.answers["enable_servicemonitor"]:
        pytest.skip("this render ships no ServiceMonitor")
    for work, script in _gate_runs(
        repo, tmp_path, "no-policy-types", "scrape-wiring", "scrape-wiring-check"
    ):
        policies = work / FLUX / "networkpolicy.yaml"
        documents = [d for d in yaml.safe_load_all(policies.read_text()) if d]
        allow = next(
            d for d in documents if d["metadata"]["name"] == "allow-scrape-from-observability"
        )
        del allow["spec"]["policyTypes"]
        policies.write_text(yaml.safe_dump_all(documents))
        result = _run(work, "sh", "-c", script)
        assert result.returncode == 0, result.stdout + result.stderr


def test_the_tenant_docs_name_the_egress_gate(repo):
    """The gate is only a gate if the person editing the policy knows it exists
    and where it runs on this repo's CI shape."""
    architecture = (repo.path / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "check-netpol-except-parity.py" in architecture
    if repo.answers["ci_shape"] == "github":
        workflow = repo.path / ".github" / "workflows" / "ci.yml"
        assert workflow.is_file(), "the workflow the doc points the tenant at is missing"
        assert "manifest-gates" in architecture, "the job running the gate is unnamed"


def test_the_platform_contract_names_what_the_manifests_bind_to(repo):
    """docs/ARCHITECTURE.md's Platform contract is the operator's list, so an
    object the manifests name and it does not is a silent prerequisite."""
    architecture = (repo.path / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    routes = _manifests(repo.path, "ingressroute*")
    certificates = _manifests(repo.path, "certificate*")
    assert routes and certificates, "the glob matched no route or no certificate"
    expected = {"external-dns", "allowCrossNamespace"}
    for path in routes:
        route = yaml.safe_load(path.read_text())
        expected |= set(route["spec"]["entryPoints"])
        for rule in route["spec"]["routes"]:
            expected |= {m["name"] for m in rule.get("middlewares") or []}
    for path in certificates:
        expected.add(yaml.safe_load(path.read_text())["spec"]["issuerRef"]["name"])
    metrics = _alert_metrics(repo.path)
    # Pin the set: a pattern that stops matching leaves the exporter the alerts
    # depend on unchecked, and an empty set passes the contract vacuously.
    assert metrics == {"kube_deployment_status_replicas_available"}, (
        f"the metrics the alerts select changed: {sorted(metrics)}"
    )
    expected |= metrics
    # The wiring block's dependsOn names a platform stage. A cluster without
    # that name leaves the tenant Kustomization retrying with nothing applied.
    wiring = _onboarding_wiring(repo.path)
    # A dependency on a Kustomization defined here (the database) is not one.
    own = {d["metadata"]["name"] for d in wiring if d.get("kind") == "Kustomization"}
    for document in wiring:
        for dependency in (document.get("spec") or {}).get("dependsOn") or []:
            if dependency["name"] not in own:
                expected.add(dependency["name"])
    # A policy peer ANDs its namespace and pod labels, so both values are a
    # prerequisite: the DNS peer names kube-system AND k8s-app: kube-dns, and a
    # cluster labelling CoreDNS otherwise loses name resolution silently.
    peers = set()
    for path in _manifests(repo.path, "networkpolicy*"):
        for policy in yaml.safe_load_all(path.read_text()):
            spec = (policy or {}).get("spec") or {}
            for rule in (spec.get("ingress") or []) + (spec.get("egress") or []):
                for peer in (rule.get("from") or []) + (rule.get("to") or []):
                    for key in ("namespaceSelector", "podSelector"):
                        peers |= set((peer.get(key) or {}).get("matchLabels", {}).values())
    assert peers, "the policies name no label-selected peer"
    expected |= peers
    if _wants_db(repo.answers):
        # The db tree's cross-namespace peers (the CNPG operator, the scraper)
        # and the platform objects its manifests and alert depend on. A peer
        # with no namespaceSelector is this namespace's own pod, not a prerequisite.
        db_peers = set()
        for path in sorted((repo.path / DB).glob("networkpolicy*.yaml")):
            for policy in yaml.safe_load_all(path.read_text()):
                spec = (policy or {}).get("spec") or {}
                for rule in (spec.get("ingress") or []) + (spec.get("egress") or []):
                    for peer in (rule.get("from") or []) + (rule.get("to") or []):
                        if "namespaceSelector" not in peer:
                            continue
                        for key in ("namespaceSelector", "podSelector"):
                            db_peers |= set((peer.get(key) or {}).get("matchLabels", {}).values())
        assert {"cnpg-system", "cloudnative-pg"} <= db_peers, sorted(db_peers)
        expected |= db_peers | DB_PLATFORM_CONTRACT
    missing = sorted(name for name in expected if name not in architecture)
    assert not missing, f"the Platform contract does not name {missing}"


# What the database component needs from the cluster beyond its policy peers:
# the CRD groups and kinds it applies, the class its claim names, and the
# Prometheus input without which the PodMonitor is never selected.
DB_PLATFORM_CONTRACT = {
    "postgresql.cnpg.io",
    "barmancloud.cnpg.io",
    "PodMonitor",
    "tenant-local",
    "podMonitorSelectorNilUsesHelmValues: false",
}


def test_the_consuming_contract_names_the_database_prerequisites():
    """docs/CONSUMING.md § What your cluster must provide is read before the
    database answer is given, so it names the same prerequisites."""
    consuming = (REPO_ROOT / "docs" / "CONSUMING.md").read_text(encoding="utf-8")
    section = consuming.split("## What your cluster must provide")[1].split("\n## ")[0]
    expected = DB_PLATFORM_CONTRACT | {"cnpg-system", "app.kubernetes.io/name: cloudnative-pg"}
    missing = sorted(name for name in expected if name not in section)
    assert not missing, f"CONSUMING.md's cluster contract does not name {missing}"


def _alert_metrics(root: Path) -> set[str]:
    """Metric names the shipped alert expressions select, whatever exporter
    ships them. The lookahead needs a brace, so label keys and `absent(` are
    not names."""
    rules = yaml.safe_load((root / FLUX / "prometheusrule.yaml").read_text())
    names = set()
    for group in rules["spec"]["groups"]:
        for rule in group["rules"]:
            names |= set(re.findall(r"\b[a-z_][a-z0-9_]*(?=\s*\{)", rule["expr"]))
    return names


# A registry on neither the forge nor GHCR. It must be a host neither fixture
# answers, or the assertion would pass on a fixture literal.
OFF_FORGE_REGISTRY = "harbor.tidewrack.example"


@pytest.fixture(scope="session")
def rendered_off_forge_registry(tmp_path_factory, answers) -> Path:
    """Fixture A pointed at a registry the forge does not host."""
    assert OFF_FORGE_REGISTRY != answers["registry_host"]
    return render_app.render(
        tmp_path_factory.mktemp("render-off-forge-registry"),
        dest_name="render-off-forge-registry",
        data={"registry_host": OFF_FORGE_REGISTRY, "registry_pull_host": OFF_FORGE_REGISTRY},
    )


def test_the_pull_credential_step_follows_the_registry(rendered_off_forge_registry):
    """The credential is a property of the registry, not of the forge: a GitLab
    tenant on Harbor has no deploy token to create."""
    onboarding = _onboarding(rendered_off_forge_registry)
    assert "gitlab+deploy-token" not in onboarding, "a Harbor tenant is sent to a GitLab token"
    assert "robot account, deploy token or pull-scoped token" in onboarding


# The build job's literal push target, quoted so deleting the warning that
# carries it fails here rather than passing silently.
NOT_PROJECT_REGISTRY = "$CI_REGISTRY_IMAGE:<short-sha>"


def test_the_off_forge_registry_warning_names_the_project_registry(
    rendered_off_forge_registry, rendered
):
    """The GitLab build job pushes the project registry, not the answered host.

    The on-forge render pins the subdomain arm: fixture A's registry is a
    subdomain of its `git_host`, so inverting that test starts warning everyone.
    """
    off_forge = _onboarding(rendered_off_forge_registry)
    assert NOT_PROJECT_REGISTRY in off_forge, "the off-forge registry is not flagged"
    on_forge = _onboarding(rendered)
    assert NOT_PROJECT_REGISTRY not in on_forge, (
        "a registry hosted on the forge's own domain is flagged as off-forge"
    )


def test_the_secret_step_names_the_backend_s_own_key(repo):
    """Tenant checklist step 3 tells the tenant which object to create. The two backends spell
    the name differently, and a GitLab variable cannot even hold `api-key`."""
    if repo.answers["secrets_backend"] == "none":
        pytest.skip("no secrets backend, so no secret step")
    onboarding = _onboarding(repo.path)
    external = (repo.path / FLUX / "externalsecret.yaml").read_text()
    wanted = "api-key" if repo.answers["secrets_backend"] == "onepassword" else "APP_API_KEY"
    assert wanted in external, f"the ExternalSecret does not read {wanted}"
    assert wanted in onboarding, f"step 3 does not name {wanted}"


def test_the_vpa_is_documented_only_where_it_ships(repo):
    """A doc step naming a manifest the answers did not render is an
    instruction the reader cannot carry out."""
    wanted = repo.answers["enable_vpa"]
    onboarding = _onboarding(repo.path)
    assert ("vpa.yaml" in onboarding) is wanted
    architecture = (repo.path / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert ("VPA" in architecture) is wanted
    if not wanted:
        for path in _manifests(repo.path, recursive=True):
            assert "vpa.yaml" not in path.read_text(encoding="utf-8"), (
                f"{path.name} cross-references vpa.yaml, which these answers did not render"
            )


def test_the_vpa_cap_matches_the_container_limit(repo):
    """A maxAllowed above the container's limits.memory recommends a request the
    kubelet rejects, and anything but RequestsOnly lets the VPA rewrite the
    limit it is capped by. A comment in either file cannot catch an edit."""
    if not repo.answers["enable_vpa"]:
        pytest.skip("no VPA on this render")
    vpa = yaml.safe_load((repo.path / FLUX / "vpa.yaml").read_text())
    policies = vpa["spec"]["resourcePolicy"]["containerPolicies"]
    policy = next((p for p in policies if p["containerName"] == "*"), None)
    assert policy is not None, "vpa.yaml has no wildcard containerPolicy"
    deployment = yaml.safe_load((repo.path / FLUX / "deployment.yaml").read_text())
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    limit = container["resources"]["limits"]["memory"]
    assert policy["maxAllowed"]["memory"] == limit, (
        f"vpa.yaml caps maxAllowed.memory at {policy['maxAllowed']['memory']} while "
        f"deployment.yaml sets limits.memory to {limit}; move both together"
    )
    assert policy["controlledValues"] == "RequestsOnly", (
        "vpa.yaml must keep controlledValues: RequestsOnly, or the VPA moves the "
        "deployment.yaml limits.memory its maxAllowed is pinned to"
    )


DB = "kubernetes/db"


def _wants_db(answers: dict) -> bool:
    return answers.get("database") == "postgres" and answers.get("secrets_backend") == "onepassword"


def _skip_without_db(answers: dict) -> None:
    if not _wants_db(answers):
        pytest.skip("database component not rendered")


def test_the_database_component_renders_only_when_asked(repo):
    assert (repo.path / DB).is_dir() is _wants_db(repo.answers)


def test_the_database_cluster_keeps_its_safeguards(repo):
    _skip_without_db(repo.answers)
    cluster = yaml.safe_load((repo.path / DB / "cluster.yaml").read_text())
    spec = cluster["spec"]
    assert spec["instances"] == 1
    assert spec["enablePDB"] is False
    assert cluster["metadata"]["annotations"]["kustomize.toolkit.fluxcd.io/prune"] == "disabled"
    assert spec["storage"] == {"storageClass": "tenant-local", "size": repo.answers["db_storage_size"]}
    assert spec["plugins"][0]["parameters"]["barmanObjectName"] == f"{repo.answers['app_slug']}-backups"


@pytest.mark.parametrize("name", ["cluster.yaml", "objectstore.yaml", "pvc-pg-dump.yaml"])
def test_the_database_stateful_objects_are_never_pruned(repo, name):
    """Removing the manifest or the db Kustomization must not delete the Cluster,
    the WAL archive, or the claim fronting the retained dumps."""
    _skip_without_db(repo.answers)
    doc = yaml.safe_load((repo.path / DB / name).read_text())
    assert doc["metadata"].get("annotations", {}).get("kustomize.toolkit.fluxcd.io/prune") == "disabled"


def test_the_database_tree_lists_every_file(repo):
    _skip_without_db(repo.answers)
    listed = set(yaml.safe_load((repo.path / DB / "kustomization.yaml").read_text())["resources"])
    on_disk = {p.name for p in (repo.path / DB).glob("*.yaml")} - {"kustomization.yaml"}
    assert listed == on_disk


def test_the_role_secrets_follow_the_library_contract(repo):
    _skip_without_db(repo.answers)
    docs = list(yaml.safe_load_all((repo.path / DB / "externalsecret-roles.yaml").read_text()))
    keys = {"username", "user", "password", "host", "port", "dbname", "sslmode", "uri"}
    assert len(docs) == 3
    for doc in docs:
        assert set(doc["spec"]["target"]["template"]["data"]) == keys
        assert "{{ .password }}" in doc["spec"]["target"]["template"]["data"]["password"]


def test_the_role_secrets_read_the_answered_item(repo):
    _skip_without_db(repo.answers)
    docs = list(yaml.safe_load_all((repo.path / DB / "externalsecret-roles.yaml").read_text()))
    item = f"{repo.answers['app_slug']}: {repo.answers['db_roles_item']}"
    for doc in docs:
        assert {ref["remoteRef"]["key"] for ref in doc["spec"]["data"]} == {item}


# The template directory that renders kubernetes/db, found by its rendered name
# so the test does not repeat the conditional-path expression.
DB_TEMPLATE_DIR = next(
    path for path in (REPO_ROOT / "template" / "kubernetes").iterdir()
    if path.is_dir() and path.name.endswith("db{% endif %}")
)


@pytest.mark.parametrize(
    "manifest,answer",
    [("externalsecret-roles.yaml", "db_roles_item"), ("externalsecret-backups.yaml", "db_backup_item")],
)
def test_an_item_title_with_a_quote_still_renders_valid_yaml(answers, manifest, answer):
    """The validator refuses a quote in an item title; the manifest stays valid
    YAML without it, so a title that slips past still names the right item."""
    title = 'Database "Production" \\ \'eu\''
    env = jinja2.Environment(  # noqa: S701 - rendering our own template, not HTML
        keep_trailing_newline=True, undefined=jinja2.StrictUndefined
    )
    source = (DB_TEMPLATE_DIR / f"{manifest}.jinja").read_text()
    text = env.from_string(source).render(**{**answers, answer: title})
    keys = {
        ref["remoteRef"]["key"]
        for doc in yaml.safe_load_all(text)
        for ref in doc["spec"]["data"]
    }
    assert keys == {f"{answers['app_slug']}: {title}"}


def test_the_dump_is_a_db_component_selected_by_its_instance(repo):
    """The dump carries component db (the spec's component set) and its own
    instance label, which its egress policy selects so the instance pods stay out."""
    _skip_without_db(repo.answers)
    slug = repo.answers["app_slug"]
    expected = {
        "app.kubernetes.io/name": slug,
        "app.kubernetes.io/component": "db",
        "app.kubernetes.io/instance": f"{slug}-pg-dump",
    }
    cronjob = yaml.safe_load((repo.path / DB / "cronjob-pg-dump.yaml").read_text())
    pvc = yaml.safe_load((repo.path / DB / "pvc-pg-dump.yaml").read_text())
    assert cronjob["metadata"]["labels"] == expected
    assert cronjob["spec"]["jobTemplate"]["spec"]["template"]["metadata"]["labels"] == expected
    assert pvc["metadata"]["labels"] == expected
    policies = {
        doc["metadata"]["name"]: doc
        for doc in yaml.safe_load_all((repo.path / DB / "networkpolicy.yaml").read_text())
    }
    assert policies["backup-allow-egress"]["spec"]["podSelector"] == {
        "matchLabels": {"app.kubernetes.io/instance": f"{slug}-pg-dump"}
    }


def test_every_static_claim_names_a_volume_the_namespace_owns(repo):
    """Tenant admission accepts a storageClassName "" claim only when its
    volumeName starts with the namespace, and O6b names that same volume."""
    prefix = f"{repo.answers['app_namespace']}-"
    static = {}
    for path in sorted((repo.path / "kubernetes").rglob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text()):
            if isinstance(doc, dict) and doc.get("kind") == "PersistentVolumeClaim":
                spec = doc.get("spec") or {}
                if spec.get("storageClassName") == "":
                    static[doc["metadata"]["name"]] = spec.get("volumeName") or ""
    for claim, volume in static.items():
        assert volume.startswith(prefix), (claim, volume)
    if _wants_db(repo.answers):
        slug, namespace = repo.answers["app_slug"], repo.answers["app_namespace"]
        assert static == {f"{slug}-pg-dump": f"{namespace}-pg-dump"}
        text = " ".join(_onboarding(repo.path).split())
        assert (
            f"NFS PersistentVolume `{namespace}-pg-dump` pre-bound to "
            f"`{namespace}/{slug}-pg-dump`"
        ) in text


def test_the_onboarding_wiring_reconciles_the_database_first(repo):
    text = _onboarding(repo.path)
    assert ("path: ./kubernetes/db" in text) is _wants_db(repo.answers)
    assert (f"    - name: {repo.answers['app_slug']}-db\n" in text) is _wants_db(repo.answers)
    assert ("### O6b" in text) is _wants_db(repo.answers)


def test_the_docs_call_the_app_stateless_only_without_a_database(repo):
    """A tenant told the app is stateless skips the backup and restore duties
    the database component brings."""
    for doc in ("ONBOARDING.md", "ARCHITECTURE.md"):
        text = (repo.path / "docs" / doc).read_text()
        assert ("stateless" in text) is not _wants_db(repo.answers), doc
        if _wants_db(repo.answers):
            assert "kubernetes/db" in text and "O6b" in text, doc
        assert "mounts no volume" not in " ".join(text.split()), doc
    architecture = (REPO_ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    storage = architecture.split("**It does not ship storage**")[1].split("\n- ")[0]
    assert "database: postgres" in storage, "the template map still calls every render stateless"


def _rule(root: Path, path: str, alert: str) -> dict:
    rules = yaml.safe_load((root / path).read_text())
    found = [r for g in rules["spec"]["groups"] for r in g["rules"] if r.get("alert") == alert]
    assert len(found) == 1, f"{alert} in {path}: {found}"
    return found[0]


_GITLAB_A = "https://git.ericsweiss.com/eric/recipe-box/-/blob/main/docs/RUNBOOKS.md"
_GITHUB_A = "https://git.ericsweiss.com/eric/recipe-box/blob/main/docs/RUNBOOKS.md"
_GITLAB_B = "https://forge.brinemoor.test/seaworks/tidepool/-/blob/main/docs/RUNBOOKS.md"
_GITHUB_B = "https://forge.brinemoor.test/seaworks/tidepool/blob/main/docs/RUNBOOKS.md"

# Each render's runbooks page as its forge serves the file, written out so a
# wrong path shape for one forge arm fails. None: forge `other` has no known
# file URL shape, so every alert links its answered runbook URL directly.
RUNBOOKS_PAGES = {
    "shaped": _GITLAB_A,
    "unlike": _GITHUB_B,
    "none-shape": _GITLAB_A,
    "no-secrets": _GITLAB_A,
    "github-none": _GITHUB_A,
    "github-image": _GITHUB_B,
    "forge-other": None,
    "gitlab-unlike": _GITLAB_B,
    "gitlab-no-build": _GITLAB_A,
    "unlike-full": _GITHUB_B,
    "unlike-db": _GITHUB_B,
    "private": _GITLAB_A,
}


def test_every_alert_links_its_own_runbook_section(repo):
    """Each alert's runbook_url is a section of this repo's docs/RUNBOOKS.md
    on a GitLab or GitHub forge, the page carries one heading per shipped alert,
    so no anchor dangles, and the page links the platform runbooks."""
    text = (repo.path / "docs" / "RUNBOOKS.md").read_text()
    headings = re.findall(r"^## (\S+)$", text, re.M)
    alerts = {}
    for path in sorted((repo.path / "kubernetes").rglob("prometheusrule.yaml")):
        for group in yaml.safe_load(path.read_text())["spec"]["groups"]:
            for rule in group["rules"]:
                alerts[rule["alert"]] = rule["annotations"]["runbook_url"]
    assert sorted(alerts) == sorted(headings), f"alerts {sorted(alerts)}, sections {headings}"
    page = RUNBOOKS_PAGES[repo.label]
    for alert, url in alerts.items():
        fallback = "db_runbook_url" if alert == "DatabaseAbsent" else "runbook_url"
        want = f"{page}#{alert.lower()}" if page else repo.answers[fallback]
        assert url == want, (alert, url)
    assert ("DatabaseAbsent" in alerts) is _wants_db(repo.answers)
    assert f"]({repo.answers['runbook_url']})" in text
    if _wants_db(repo.answers):
        database = text.split("## DatabaseAbsent")[1]
        assert f"]({repo.answers['db_runbook_url']})" in database


def test_database_absent_follows_any_instance_serial(repo):
    """CNPG re-creates a lost instance under the next serial (<slug>-db-2), so a
    pinned pod name pages for ever on a healthy database."""
    _skip_without_db(repo.answers)
    slug, namespace = repo.answers["app_slug"], repo.answers["app_namespace"]
    rule = _rule(repo.path, f"{DB}/prometheusrule.yaml", "DatabaseAbsent")
    assert rule["expr"] == (
        f'absent(cnpg_collector_up{{namespace="{namespace}", pod=~"{slug}-db-[0-9]+"}})'
    )
    assert "15m" in rule["annotations"]["description"]


def test_the_role_uri_encodes_a_space_as_percent_20(repo):
    """urlquery is query escaping: a space becomes '+', which libpq reads in
    URI userinfo as a literal plus."""
    _skip_without_db(repo.answers)
    docs = list(yaml.safe_load_all((repo.path / DB / "externalsecret-roles.yaml").read_text()))
    for doc in docs:
        uri = doc["spec"]["target"]["template"]["data"]["uri"]
        assert '{{ .password | urlquery | replace "+" "%20" }}' in uri, uri
    onboarding = _onboarding(repo.path)
    assert "without spaces" in onboarding


def _selects(selector: dict, labels: dict) -> bool:
    """Whether a LabelSelector matches a pod carrying `labels`."""
    if any(labels.get(k) != v for k, v in (selector.get("matchLabels") or {}).items()):
        return False
    for expression in selector.get("matchExpressions") or []:
        key, operator, values = expression["key"], expression["operator"], expression.get("values")
        present = key in labels
        if operator == "In" and not (present and labels[key] in values):
            return False
        if operator == "NotIn" and present and labels[key] in values:
            return False
        if operator == "Exists" and not present:
            return False
        if operator == "DoesNotExist" and present:
            return False
    return True


def _db_pods(slug: str) -> dict[str, dict]:
    """The labels the dump and instance pods carry, the app's name label among them."""
    return {
        "dump": {
            "app.kubernetes.io/name": slug,
            "app.kubernetes.io/component": "db",
            "app.kubernetes.io/instance": f"{slug}-pg-dump",
        },
        "instance": {
            "app.kubernetes.io/name": slug,
            "app.kubernetes.io/component": "db",
            "cnpg.io/cluster": f"{slug}-db",
        },
    }


def test_the_scrape_allow_opens_no_database_port(repo):
    """Copier rejects a db pod port as app_port, so the scrape allow's port never
    names one even if its component exclusion were dropped; db-allow-ingress
    names those ports."""
    _skip_without_db(repo.answers)
    db_docs = yaml.safe_load_all((repo.path / DB / "networkpolicy.yaml").read_text())
    db_ingress = next(d for d in db_docs if d and d["metadata"]["name"] == "db-allow-ingress")
    db_ports = {p["port"] for rule in db_ingress["spec"]["ingress"] for p in rule["ports"]}
    assert db_ports == {5432, 8000, 9187}, f"the db pods' ports changed: {sorted(db_ports)}"
    scrape_ports = set()
    for path in _manifests(repo.path, "networkpolicy*"):
        for policy in yaml.safe_load_all(path.read_text()):
            if (policy or {}).get("metadata", {}).get("name") == "allow-scrape-from-observability":
                scrape_ports |= {
                    p["port"] for rule in policy["spec"]["ingress"] for p in rule["ports"]
                }
    app_port = int(repo.answers["app_port"])
    assert app_port not in db_ports, f"the fixture answers app_port {app_port}, a db port"
    expected = {app_port} if repo.answers["enable_servicemonitor"] else set()
    assert scrape_ports == expected, scrape_ports
    assert not scrape_ports & db_ports, f"the scrape allow opens db port(s) {scrape_ports & db_ports}"


def test_no_app_policy_selects_the_database_pods(repo):
    """The instance and dump pods carry the app's name label; an app allow that
    selects them gives the credential-holding dump public egress and opens the
    instances to Traefik and the scraper."""
    _skip_without_db(repo.answers)
    slug = repo.answers["app_slug"]
    db_pods = _db_pods(slug)
    app_pod = {"app.kubernetes.io/name": slug}
    allows = []
    for path in _manifests(repo.path, "networkpolicy*"):
        for policy in yaml.safe_load_all(path.read_text()):
            spec = (policy or {}).get("spec") or {}
            name = (policy or {}).get("metadata", {}).get("name")
            if spec.get("ingress") or spec.get("egress"):
                allows.append((name, spec["podSelector"]))
    assert allows, "kubernetes/flux carries no allow policy"
    for name, selector in allows:
        assert _selects(selector, app_pod), f"{name} does not select the app's pods"
        for pod, labels in db_pods.items():
            assert not _selects(selector, labels), f"{name} selects the database {pod} pod"


def test_app_db_egress_does_not_select_the_database_pods(repo):
    """The instance and dump pods carry the app's name label, so a name-only
    selector hands them the app's egress to Postgres as well."""
    _skip_without_db(repo.answers)
    slug = repo.answers["app_slug"]
    docs = yaml.safe_load_all((repo.path / DB / "networkpolicy.yaml").read_text())
    policy = next(d for d in docs if d and d["metadata"]["name"] == "app-allow-egress-db")
    selector = policy["spec"]["podSelector"]
    assert _selects(selector, {"app.kubernetes.io/name": slug}), "the selector does not select the app's pods"
    for pod, labels in _db_pods(slug).items():
        assert not _selects(selector, labels), f"app-allow-egress-db selects the database {pod} pod"


def test_architecture_names_what_selects_the_database_pods(repo):
    """The database paragraph names each app policy that excludes the db pods,
    the scrape allow among them when the render ships it."""
    _skip_without_db(repo.answers)
    architecture = (repo.path / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    paragraph = architecture.split("The instance and dump pods carry")[1].split("\n\n")[0]
    assert "The app's NetworkPolicies" not in paragraph, "overclaims every app policy"
    wide = [line for line in paragraph.splitlines() if len(line) > 80]
    assert not wide, f"db paragraph renders past 80 columns: {wide}"
    for path in _manifests(repo.path, "networkpolicy*"):
        for policy in yaml.safe_load_all(path.read_text()):
            spec = (policy or {}).get("spec") or {}
            if spec.get("ingress") or spec.get("egress"):
                name = policy["metadata"]["name"]
                assert f"`{name}`" in paragraph, f"{name} is unnamed in the db paragraph"
    scrape_named = "`allow-scrape-from-observability`" in paragraph
    assert scrape_named is bool(repo.answers["enable_servicemonitor"])


def test_the_wiring_quota_counts_the_database_claim(repo):
    """tenant-local does not enforce a claim's size, so the quota is the cap."""
    quotas = [d for d in _onboarding_wiring(repo.path) if d.get("kind") == "ResourceQuota"]
    assert len(quotas) == 1
    hard = quotas[0]["spec"]["hard"]
    storage_key = "tenant-local.storageclass.storage.k8s.io/requests.storage"
    if _wants_db(repo.answers):
        size = int(repo.answers["db_storage_size"].removesuffix("Gi"))
        assert hard["persistentvolumeclaims"] == "4"
        assert hard[storage_key] == f"{size * 2}Gi"
    else:
        # A tenant that ships no claim caps both keys at zero.
        assert hard["persistentvolumeclaims"] == "0"
        assert hard[storage_key] == "0"


def _wiring_named(wiring: list[dict], kind: str, name: str) -> dict:
    found = [d for d in wiring if d.get("kind") == kind and d["metadata"]["name"] == name]
    assert len(found) == 1, f"the wiring carries {len(found)} {kind} {name}"
    return found[0]


def test_removal_keeps_the_namespace_it_guards(repo):
    """The tenant gates hold the prune guards; Removal must not promise what a
    pruned Namespace would undo."""
    removal = _onboarding_step(repo.path, "Removal")
    assert "PVCs survive" not in removal, "Removal promises what the cascade would undo"
    assert "does not prune the Namespace" in " ".join(removal.split())


def test_the_wiring_grants_exactly_the_routed_hostnames(repo):
    """Both admission policies read the grant: a route or certificate naming a
    host outside it is refused. The tenant gates hold the bindings' shape."""
    namespace = repo.answers["app_namespace"]
    grant = _wiring_named(_onboarding_wiring(repo.path), "ConfigMap", f"tenant-routing-{namespace}")
    granted = {h.strip() for h in grant["data"]["hosts"].splitlines() if h.strip()}
    docs = _docs(repo.path)
    routed = {
        host
        for d in docs
        if d["kind"] == "IngressRoute"
        for r in d["spec"]["routes"]
        for host in re.findall(r"Host\(`([^`]+)`\)", r["match"])
    } | {host for d in docs if d["kind"] == "Certificate" for host in d["spec"]["dnsNames"]}
    assert routed, "the render routes no hostname, so this check proves nothing"
    assert granted == routed, f"granted {sorted(granted)}, routed {sorted(routed)}"
    assert not any("*" in h for h in granted), "a wildcard grant refuses every write"


def test_the_wiring_reader_is_read_only(repo):
    """The tenant's kubeconfig identity reads its namespace and its own Flux
    objects, never Secrets and never a write."""
    slug, namespace = repo.answers["app_slug"], repo.answers["app_namespace"]
    wiring = _onboarding_wiring(repo.path)
    account = _wiring_named(wiring, "ServiceAccount", f"{slug}-reader")
    assert account["metadata"]["namespace"] == namespace
    subject = [{"kind": "ServiceAccount", "name": f"{slug}-reader", "namespace": namespace}]
    view = _wiring_named(wiring, "RoleBinding", f"{slug}-reader-view")
    assert view["metadata"]["namespace"] == namespace
    assert view["roleRef"]["kind"] == "ClusterRole" and view["roleRef"]["name"] == "view"
    assert view["subjects"] == subject
    role = _wiring_named(wiring, "Role", f"{slug}-reader")
    assert role["metadata"]["namespace"] == "flux-system"
    kustomizations = {
        d["metadata"]["name"] for d in wiring if d.get("kind") == "Kustomization"
    }
    named = set()
    for rule in role["rules"]:
        assert set(rule["verbs"]) <= {"get", "list", "watch"}, rule["verbs"]
        assert rule.get("resourceNames"), "an unnamed rule reads every tenant's objects"
        named |= set(rule["resourceNames"])
    assert kustomizations <= named, "the reader cannot see one of its Kustomizations"
    binding = _wiring_named(wiring, "RoleBinding", f"{slug}-reader")
    assert binding["metadata"]["namespace"] == "flux-system"
    assert binding["roleRef"] == {
        "kind": "Role", "name": f"{slug}-reader", "apiGroup": "rbac.authorization.k8s.io"
    }
    assert binding["subjects"] == subject
    onboarding = _onboarding(repo.path)
    assert f"create token {slug}-reader" in onboarding


def test_the_bootstrap_secrets_reach_the_unmanaged_secrets_allowlist(repo):
    """Every hand-made Secret the wiring needs has no controller owner, so the
    cluster's unmanaged-secrets gate fails the wiring change without its entry."""
    slug, namespace = repo.answers["app_slug"], repo.answers["app_namespace"]
    wiring = _onboarding_wiring(repo.path)
    source = _wiring_named(wiring, "GitRepository", slug)
    assert source["spec"]["secretRef"] == {"name": f"{slug}-git-auth"}
    onboarding = _onboarding(repo.path)
    assert f"flux-system/{slug}-git-auth" in onboarding
    assert f"flux create secret git {slug}-git-auth" in onboarding
    backend = repo.answers["secrets_backend"]
    if backend != "none":
        name = "onepassword-connect-token" if backend == "onepassword" else "gitlab-api-token"
        assert f'"{namespace}/{name}": "tenant ESO bootstrap"' in onboarding


def test_the_build_runner_step_ships_with_the_tagged_build(repo):
    """The tagged build job pends forever without a runner carrying the tag, so
    the operator step that provides it renders exactly when the job does."""
    onboarding = _onboarding(repo.path)
    built = repo.answers["ci_shape"] == "gitlab_selfhosted" and repo.answers["enable_image_build"]
    assert ("### O2e " in onboarding) == built
    if built:
        step = _onboarding_step(repo.path, "O2e")
        assert f"`{repo.answers['privileged_runner_tag']}`" in step, step
        # The runner is enabled on this project alone, never group- or instance-wide.
        prose = " ".join(step.split())
        lowered = prose.lower()
        assert "on this project alone, never on the" in lowered, step
        assert f"never on the `{repo.answers['git_namespace']}` group or the instance" in prose, step
        # runner_type alone cannot show which projects share the runner.
        assert "`runner_type`" in step and "`project_type`" in step and "`projects`" in step, step
        # An unlocked project runner lets any maintainer here enable it elsewhere.
        assert "`locked` must be `true`" in lowered, step
        # The shared runner's capacity also serves this project.
        assert "`concurrent`" in step and "resourcequota" in lowered, step


def test_the_skill_names_the_answered_build_runner_tag(repo):
    """The project skill tells an agent which runner the build job needs; the
    gitlab-unlike render answers a tag fixture A never uses."""
    skill = (repo.path / ".claude" / "skills" / "project-development" / "SKILL.md").read_text()
    prose = " ".join(skill.split())
    tagged = f"job is tagged `{repo.answers['privileged_runner_tag']}`"
    built = repo.answers["ci_shape"] == "gitlab_selfhosted" and repo.answers["enable_image_build"]
    assert (tagged in prose) == built, f"{repo.label}: tagged line present={tagged in prose}"


def test_the_library_job_token_step_follows_the_gitlab_shape(repo):
    """Only the GitLab pipeline clones the library with its job token."""
    onboarding = _onboarding(repo.path)
    wanted = repo.answers["ci_shape"] == "gitlab_selfhosted"
    assert ("### O2d " in onboarding) is wanted
    if wanted:
        o2d = _onboarding_step(repo.path, "O2d")
        assert repo.answers["lib_project"] in o2d
        assert f"{repo.answers['git_namespace']}/{repo.answers['app_slug']}" in o2d


def test_o6b_registers_the_dump_with_the_backup_freshness_check(repo):
    """A dump nobody watches stops silently; the cluster's freshness alert only
    knows the apps it is told about."""
    _skip_without_db(repo.answers)
    slug = repo.answers["app_slug"]
    o6b = _onboarding_step(repo.path, "O6b")
    assert "nas_storage_backup_artifact_apps" in o6b
    assert f'{{name: {slug}, pattern: "{slug}-*.sql.gz"}}' in o6b
    assert f'absent(backup_artifact_last_mtime_seconds{{app="{slug}"}})' in o6b
    assert "check-backup-artifact-apps" in o6b


def test_the_dump_runs_the_server_image(repo):
    """pg_dump must never be older than the server it dumps."""
    _skip_without_db(repo.answers)
    cluster = yaml.safe_load((repo.path / DB / "cluster.yaml").read_text())
    cronjob = yaml.safe_load((repo.path / DB / "cronjob-pg-dump.yaml").read_text())
    containers = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
    assert [c["image"] for c in containers] == [cluster["spec"]["imageName"]]


def test_the_app_mounts_its_database_secret_as_files(repo):
    deployment = yaml.safe_load((repo.path / FLUX / "deployment.yaml").read_text())
    pod = deployment["spec"]["template"]["spec"]
    secrets = {v["secret"]["secretName"] for v in pod["volumes"] if "secret" in v}
    expected = {f"{repo.answers['app_slug']}-db-app", f"{repo.answers['app_slug']}-db-ca"}
    probe = pod["containers"][0]["readinessProbe"]["httpGet"]["path"]
    if _wants_db(repo.answers):
        assert expected <= secrets
        slug = repo.answers["app_slug"]
        by_name = {v["name"]: v for v in pod["volumes"]}
        assert by_name["db"]["secret"] == {"secretName": f"{slug}-db-app"}
        assert by_name["db-ca"]["secret"]["items"] == [{"key": "ca.crt", "path": "ca.crt"}]
        assert {s for s in secrets if s.startswith(f"{slug}-db-")} == expected
        mounts = pod["containers"][0]["volumeMounts"]
        assert not any("subPath" in m for m in mounts)
        assert {m["name"]: m["mountPath"] for m in mounts if m["name"].startswith("db")} == {
            "db": "/var/run/secrets/db",
            "db-ca": "/var/run/secrets/db-ca",
        }
        assert probe == "/readyz"
        assert pod["containers"][0]["livenessProbe"]["httpGet"]["path"] == "/"
    else:
        assert not expected & secrets
        assert probe == "/"


def test_the_database_gates_ship_with_the_database(repo):
    for gate in ("check-cnpg-clusters.py", "check-pvc-storageclass.py"):
        assert (repo.path / "scripts" / gate).is_file() is _wants_db(repo.answers)


def _drop_prune_annotation(cluster: dict) -> None:
    del cluster["metadata"]["annotations"]["kustomize.toolkit.fluxcd.io/prune"]


def _drop_storage_class(cluster: dict) -> None:
    del cluster["spec"]["storage"]["storageClass"]


def _patch_out_prune_annotation(work: Path) -> None:
    """Strip the prune guard with a Kustomize patch and leave cluster.yaml whole:
    only a gate reading the built output sees what Flux applies."""
    path = work / DB / "kustomization.yaml"
    document = yaml.safe_load(path.read_text())
    remove = [{"op": "remove", "path": "/metadata/annotations/kustomize.toolkit.fluxcd.io~1prune"}]
    document.setdefault("patches", []).append(
        {"target": {"kind": "Cluster"}, "patch": yaml.safe_dump(remove)}
    )
    path.write_text(yaml.safe_dump(document, sort_keys=False))


def _mutate_cluster(work: Path, mutate) -> None:
    cluster_path = work / DB / "cluster.yaml"
    cluster = yaml.safe_load(cluster_path.read_text())
    mutate(cluster)
    cluster_path.write_text(yaml.safe_dump(cluster))


def test_the_database_gates_run_green_and_reject_a_dropped_safeguard_or_class(repo, tmp_path):
    """Both shipped forms (the `database` task, the db-manifest-check job) pass the
    render; check-cnpg-clusters fails on a prune annotation dropped in the source
    or by a patch, check-pvc-storageclass on a dropped class."""
    _skip_without_db(repo.answers)
    require_tool("kustomize", "database gates", _TOOLCHAIN_HINT, ci_optional=True)
    mutations = (
        ("prune", lambda work: _mutate_cluster(work, _drop_prune_annotation), "safeguard"),
        ("class", lambda work: _mutate_cluster(work, _drop_storage_class), None),
        ("patch", _patch_out_prune_annotation, "safeguard"),
    )
    env = _offline_kubeconform(tmp_path / "offline" / "bin")
    for label, mutate, header in mutations:
        for work, script in _gate_runs(repo, tmp_path, f"db-{label}", "database", "db-manifest-check"):
            result = _run(work, "sh", "-c", script, env=env)
            assert result.returncode == 0, result.stdout + result.stderr
            mutate(work)
            result = _run(work, "sh", "-c", script, env=env)
            assert result.returncode != 0, f"the {label} mutation passed the gate"
            if header:
                assert header in result.stderr, result.stdout + result.stderr


def test_validate_render_runs_the_database_gates_over_the_built_tree(repo, tmp_path, monkeypatch):
    """A patch in kubernetes/db/kustomization.yaml changes what Flux applies and
    leaves every source file intact, so the gates must read `kustomize build`."""
    _skip_without_db(repo.answers)
    require_tool("kustomize", "validate_render database gates", _TOOLCHAIN_HINT, ci_optional=True)
    monkeypatch.setattr(validate_render, "_kubeconform", lambda *args, **kwargs: None)
    work = tmp_path / "validate-db"
    shutil.copytree(repo.path, work)
    runner = validate_render.Runner(work)
    validate_render._validate_db(runner, work, "unused", repo.answers["k8s_version"])
    assert runner.failures == []
    _patch_out_prune_annotation(work)
    runner = validate_render.Runner(work)
    validate_render._validate_db(runner, work, "unused", repo.answers["k8s_version"])
    assert "cnpg clusters" in runner.failures, runner.failures


def test_validate_render_reads_the_dump_time_zone_from_the_build(repo, tmp_path, monkeypatch):
    """A patch can replace the dump CronJob's zone and leave the source file
    valid, so the timezone gate must read `kustomize build`."""
    _skip_without_db(repo.answers)
    require_tool("kustomize", "validate_render dump timezone", _TOOLCHAIN_HINT, ci_optional=True)
    monkeypatch.setattr(validate_render, "_kubeconform", lambda *args, **kwargs: None)
    work = tmp_path / "validate-db-tz"
    shutil.copytree(repo.path, work)
    path = work / DB / "kustomization.yaml"
    document = yaml.safe_load(path.read_text())
    replace = [{"op": "replace", "path": "/spec/timeZone", "value": "America/Los_Angelos"}]
    document.setdefault("patches", []).append(
        {"target": {"kind": "CronJob"}, "patch": yaml.safe_dump(replace)}
    )
    path.write_text(yaml.safe_dump(document, sort_keys=False))
    runner = validate_render.Runner(work)
    validate_render._validate_db(runner, work, "unused", repo.answers["k8s_version"])
    assert runner.failures == ["dump timezone"], runner.failures


PRUNE = "kustomize.toolkit.fluxcd.io/prune"


def _pvc_gate_over_the_build(tree: Path) -> subprocess.CompletedProcess:
    """The rendered check-pvc-storageclass over `kustomize build kubernetes/db`."""
    built = _run(tree, "kustomize", "build", DB)
    assert built.returncode == 0, built.stderr
    return subprocess.run(
        [sys.executable, "scripts/check-pvc-storageclass.py"],
        cwd=tree, input=built.stdout, capture_output=True, text=True,
    )


def test_the_rendered_comment_gate_passes_a_database_render(repo):
    """kubernetes/db and the database arms carry comments no other render has,
    and the tenant's comment-length job runs this gate over the whole tree."""
    _skip_without_db(repo.answers)
    assert (repo.path / DB).is_dir(), "the database render ships no kubernetes/db"
    result = _run(repo.path, sys.executable, "scripts/check-comment-length.py", ".")
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_pvc_gate_rejects_a_static_claim_patched_out_of_the_prune_guard(repo, tmp_path):
    """The vendored gate holds a volumeName claim to the prune guard on the built
    tree, so a kustomize patch that strips the annotation fails it."""
    _skip_without_db(repo.answers)
    require_tool("kustomize", "database built-tree checks", _TOOLCHAIN_HINT, ci_optional=True)
    result = _pvc_gate_over_the_build(repo.path)
    assert result.returncode == 0, result.stdout + result.stderr
    work = tmp_path / "prune-pvc"
    shutil.copytree(repo.path, work)
    path = work / DB / "kustomization.yaml"
    document = yaml.safe_load(path.read_text())
    remove = [{"op": "remove", "path": "/metadata/annotations/kustomize.toolkit.fluxcd.io~1prune"}]
    document.setdefault("patches", []).append(
        {"target": {"kind": "PersistentVolumeClaim"}, "patch": yaml.safe_dump(remove)}
    )
    path.write_text(yaml.safe_dump(document, sort_keys=False))
    result = _pvc_gate_over_the_build(work)
    assert result.returncode == 1, result.stdout + result.stderr
    output = result.stdout + result.stderr
    assert f"{repo.answers['app_slug']}-pg-dump" in output and PRUNE in output, output


@pytest.mark.parametrize("keyword", ["true", "null", "off"])
def test_a_yaml_keyword_db_name_still_renders_a_string(answers, keyword):
    """The validator refuses a YAML keyword as db_name; the manifests still carry
    a string without it, so initdb and the role Secrets name the database."""
    env = jinja2.Environment(  # noqa: S701 - rendering our own template, not HTML
        keep_trailing_newline=True, undefined=jinja2.StrictUndefined
    )
    values = {**answers, "db_name": keyword}
    cluster = yaml.safe_load(
        env.from_string((DB_TEMPLATE_DIR / "cluster.yaml.jinja").read_text()).render(**values)
    )
    assert cluster["spec"]["bootstrap"]["initdb"]["database"] == keyword
    roles = (DB_TEMPLATE_DIR / "externalsecret-roles.yaml.jinja").read_text()
    for doc in yaml.safe_load_all(env.from_string(roles).render(**values)):
        assert doc["spec"]["target"]["template"]["data"]["dbname"] == keyword


def test_the_gitlab_db_job_validates_the_db_tree_with_kubeconform(repo, tmp_path):
    """The flux-lint include builds kubernetes/flux only, so db-manifest-check
    is the pipeline's one schema check of kubernetes/db, on the Taskfile's pins."""
    _skip_without_db(repo.answers)
    if repo.answers["ci_shape"] != "gitlab_selfhosted":
        pytest.skip("no GitLab pipeline rendered")
    ci = render_app.load_ci(repo.path / ".gitlab-ci.yml")
    body = _resolved(ci, "db-manifest-check")
    script = [" ".join(str(line).split()) for line in body["script"]]
    lint = [
        index for index, line in enumerate(script)
        if line.startswith("bash scripts/flux-lint.sh --mode simple")
        and "--kustomize-path kubernetes/db" in line
    ]
    assert lint, f"db-manifest-check runs no kubeconform over kubernetes/db: {script}"
    gates = [index for index, line in enumerate(script) if line.startswith("python3 ")]
    assert lint[0] < min(gates), "kubeconform must run before the python gates"
    fetch = [line for line in body["before_script"] if "scripts/ci-fetch-tools.py" in line]
    assert fetch and {"kustomize", "kubeconform"} <= set(fetch[0].split()), body["before_script"]
    taskvars = yaml.safe_load((repo.path / "Taskfile.yml").read_text())["vars"]
    for key in ("ALLOWED_SKIPS", "K8S_VERSION", "CRD_CATALOG_REF"):
        assert str(body["variables"][key]) == str(taskvars[key]), key
    env = _stubbed_path(tmp_path / "db-invalid" / "bin", "kind: Service\n", INVALID_SUMMARY,
                        kubeconform_rc=1)
    result = subprocess.run(
        ["sh", "-c", _job_shell(ci, "db-manifest-check")],
        cwd=repo.path, capture_output=True, text=True, env=env,
    )
    assert result.returncode != 0, "db-manifest-check passed an invalid kubernetes/db resource"
    assert "Invalid: 1" in result.stdout + result.stderr


def test_the_pdb_leaves_the_database_pods_out(repo):
    """An integer minAvailable counts every pod the selector matches: a database
    pod in it lets a drain evict every app replica, and blocks the instance's
    own eviction while the app is unready."""
    _skip_without_db(repo.answers)
    pdbs = _manifests(repo.path, "pdb")
    if not pdbs:
        pytest.skip("no PodDisruptionBudget rendered")
    slug = repo.answers["app_slug"]
    selector = yaml.safe_load(pdbs[0].read_text())["spec"]["selector"]
    assert _selects(selector, {"app.kubernetes.io/name": slug}), "the PDB lost the app pods"
    for pod, labels in _db_pods(slug).items():
        assert not _selects(selector, labels), f"the PDB counts the database {pod} pod"


def test_the_database_gates_have_a_pre_commit_hook(repo, tmp_path):
    """On `github` and `none` the hook is the only automatic runner of the
    kubernetes/db gates; it passes the render and reds a dropped safeguard."""
    _skip_without_db(repo.answers)
    hooks = {hook["id"]: hook for hook in _local_hooks(repo)}
    assert "database" in hooks, "no pre-commit hook runs the kubernetes/db gates"
    if not shutil.which("python"):
        pytest.skip("no `python` on PATH to stand in for the hook venv's")
    require_tool("kustomize", "database pre-commit hook", _TOOLCHAIN_HINT)
    work = tmp_path / "hook"
    shutil.copytree(repo.path, work)
    command = shlex.split(hooks["database"]["entry"])
    result = _run(work, *command)
    assert result.returncode == 0, result.stdout + result.stderr
    _mutate_cluster(work, _drop_prune_annotation)
    result = _run(work, *command)
    assert result.returncode != 0, "the database hook passed a Cluster with no prune guard"
    shutil.copy(repo.path / DB / "cluster.yaml", work / DB / "cluster.yaml")
    assert _run(work, *command).returncode == 0, "restoring cluster.yaml left the hook red"
    _patch_out_prune_annotation(work)
    result = _run(work, *command)
    assert result.returncode != 0, "the database hook passed a patch stripping the prune guard"


def test_the_lint_tasks_run_their_database_arm(repo, tmp_path):
    """Under `set -e` every red above comes from the kubernetes/flux cmd, so the
    kubernetes/db cmds need their own assertion and a red of their own."""
    _skip_without_db(repo.answers)
    tasks = yaml.safe_load((repo.path / "Taskfile.yml").read_text())["tasks"]
    flux_lint = [" ".join(cmd.split()) for cmd in tasks["flux-lint"]["cmds"]]
    assert any("--kustomize-path kubernetes/db" in cmd for cmd in flux_lint), flux_lint
    assert "python3 scripts/check-netpol-except-parity.py kubernetes/db" in tasks["netpol"]["cmds"]
    assert (
        "python3 scripts/check-kustomization.py kubernetes/db" in tasks["kustomization"]["cmds"]
    )
    require_tool("kustomize", "database lint tasks", _TOOLCHAIN_HINT)
    env = _offline_kubeconform(tmp_path / "offline" / "bin")
    for work, script in _gate_runs(repo, tmp_path, "db-unlisted", "kustomization", "db-manifest-check"):
        (work / DB / "configmap.yaml").write_text(
            "---\napiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: unlisted\n"
        )
        result = _run(work, "sh", "-c", script, env=env)
        assert result.returncode != 0, "an unlisted kubernetes/db manifest passed the gate"
        assert "not listed" in result.stdout + result.stderr


def test_generated_db_manifests_build(repo):
    """kubernetes/db is built by its own Kustomization, so a tree kustomize cannot
    build fails only here and in validate_render."""
    _skip_without_db(repo.answers)
    require_tool("kustomize", "generated-manifest build", _TOOLCHAIN_HINT, ci_optional=True)
    result = _run(repo.path, "kustomize", "build", DB)
    assert result.returncode == 0, result.stderr
    kinds = {d["kind"] for d in yaml.safe_load_all(result.stdout) if d}
    expected = {
        "Cluster", "ObjectStore", "ScheduledBackup", "CronJob",
        "PodMonitor", "PrometheusRule", "NetworkPolicy", "ExternalSecret",
    }
    assert expected <= kinds, sorted(expected - kinds)
