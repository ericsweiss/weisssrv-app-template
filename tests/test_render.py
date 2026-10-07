"""Render the template and assert what must be true of every generated repo.

The invariants under test are listed in docs/ARCHITECTURE.md, Invariants the
render tests hold. Rendering is session-scoped.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shlex
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

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


@pytest.fixture(scope="session")
def answers() -> dict:
    return yaml.safe_load(render_app.ANSWERS.read_text())


@pytest.fixture(scope="session")
def answers_b() -> dict:
    return yaml.safe_load(render_app.ANSWERS_B.read_text())


@pytest.fixture(scope="session")
def rendered(tmp_path_factory) -> Path:
    return render_app.render(tmp_path_factory.mktemp("render"))


@pytest.fixture(scope="session")
def rendered_b(tmp_path_factory) -> Path:
    """A second render from unlike answers, every optional component off.

    It is what makes a hardcoded value visible; under the shaped fixture a
    carried-over literal renders identically to a correct substitution.
    """
    return render_app.render(
        tmp_path_factory.mktemp("render-b"),
        answers=render_app.ANSWERS_B,
        dest_name="render-b",
    )


@pytest.fixture(scope="session")
def rendered_none(tmp_path_factory) -> Path:
    """Fixture A with ci_shape overridden: the render that ships no pipeline."""
    return render_app.render(
        tmp_path_factory.mktemp("render-none"),
        dest_name="render-none",
        data={"ci_shape": "none"},
    )


@pytest.fixture(scope="session")
def rendered_no_secrets(tmp_path_factory) -> Path:
    """The `secrets_backend: none` render: no ExternalSecret, no secret env
    block, no ClusterSecretStore. The pull credential is overridden with it
    because it is an ExternalSecret too.
    """
    return render_app.render(
        tmp_path_factory.mktemp("render-no-secrets"),
        dest_name="render-no-secrets",
        data={"secrets_backend": "none", "enable_registry_pull_secret": "false"},
    )


# The library project the gitlab-unlike render answers. It must differ from
# both fixtures, which share copier's default, or an `include: project:`
# written as a literal renders correctly everywhere else in this suite.
ALT_LIB_PROJECT = "seaworks/brinemoor-lib"

# Fixture B, moved onto the GitLab shape with the image build on. Overriding
# rather than adding a third answers FILE is the idiom the other derived renders
# use, and it is what keeps every other value unlike fixture A's for free.
GITLAB_UNLIKE_OVERRIDES = {
    "ci_shape": "gitlab_selfhosted",
    # The GitLab pipeline is only legal on a GitLab forge, and copier.yml's
    # validator says so, so the shape override carries the forge with it.
    "forge": "gitlab",
    "enable_image_build": "true",
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
    return render_app.render(
        tmp_path_factory.mktemp("render-gitlab-unlike"),
        answers=render_app.ANSWERS_B,
        dest_name="render-gitlab-unlike",
        data=GITLAB_UNLIKE_OVERRIDES,
    )


@pytest.fixture(scope="session")
def rendered_gitlab_no_build(tmp_path_factory) -> Path:
    """The GitLab pipeline with the image build off: no `build` stage, no
    docker-build include, no Dockerfile. The combination no fixture answers."""
    return render_app.render(
        tmp_path_factory.mktemp("render-gitlab-no-build"),
        dest_name="render-gitlab-no-build",
        data={"enable_image_build": "false"},
    )


@pytest.fixture(scope="session")
def rendered_github_none(tmp_path_factory) -> Path:
    """No pipeline on a GitHub-hosted repo: the file set the `forge` answer
    changes on its own, with that forge's word for a reviewed change. `enable_hpa`
    rides along so the default HPA-off, VPA-on pair is rendered somewhere."""
    return render_app.render(
        tmp_path_factory.mktemp("render-github-none"),
        dest_name="render-github-none",
        data={
            "ci_shape": "none",
            "forge": "github",
            "change_request": "pull request",
            "enable_hpa": "false",
        },
    )


@pytest.fixture(scope="session")
def rendered_forge_other(tmp_path_factory) -> Path:
    """A forge that is neither GitLab nor GitHub: no forge metadata renders, and
    `change_request` is answered rather than computed. It is also the only render
    with the HPA on, the VPA off and SSO with no LAN hostname."""
    return render_app.render(
        tmp_path_factory.mktemp("render-forge-other"),
        dest_name="render-forge-other",
        data={
            "ci_shape": "none",
            "forge": "other",
            "change_request": "pull request",
            "enable_vpa": "false",
            "enable_internal_ingress": "false",
        },
    )


# Kept out of RENDERS: it proves one filter, not a file set, so it needs no
# render-validate line of its own.
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
        data={"git_namespace": MIXED_CASE_GIT_NAMESPACE, "enable_image_build": "true"},
    )


@pytest.fixture(scope="session")
def rendered_image_github(tmp_path_factory) -> Path:
    """The contrast fixture with the image build on.

    `github` plus `enable_image_build: true` gates the build-image workflow's
    conditional path, and no fixture answers that combination.
    """
    return render_app.render(
        tmp_path_factory.mktemp("render-image"),
        answers=render_app.ANSWERS_B,
        dest_name="render-image",
        data={"enable_image_build": "true"},
    )


# Fixture B with every optional component on. The contrast fixture answers them
# all off, so without this render seven optional manifests and the forward-auth
# middleware are never scanned for a carried-over reference-cluster literal.
UNLIKE_FULL_OVERRIDES = {
    "enable_servicemonitor": "true",
    "enable_internal_ingress": "true",
    "enable_hpa": "true",
    "enable_vpa": "true",
    "enable_registry_pull_secret": "true",
    "enable_image_build": "true",
    "enable_sso": "true",
    "replica_count": "3",
}


@pytest.fixture(scope="session")
def rendered_unlike_full(tmp_path_factory) -> Path:
    """The contrast fixture with every component on: the ServiceMonitor, HPA, VPA, PDB,
    internal route, pull credential and forward-auth middleware all come from answers
    that are not the reference cluster's."""
    return render_app.render(
        tmp_path_factory.mktemp("render-unlike-full"),
        answers=render_app.ANSWERS_B,
        dest_name="render-unlike-full",
        data=UNLIKE_FULL_OVERRIDES,
    )


# The vault the alt-vault render answers. It must differ from both fixtures'
# answers, or the assertion would pass on a hardcoded string.
ALT_VAULT = "Tidewrack"


@pytest.fixture(scope="session")
def rendered_alt_vault(tmp_path_factory, answers, answers_b) -> Path:
    """Fixture A with a different 1Password vault. The contrast fixture uses the
    GitLab backend, so this is the only render in which the 1Password wiring
    branch is produced from an answer that is not the reference cluster's."""
    answered = {answers["onepassword_vault"], answers_b["onepassword_vault"]}
    assert ALT_VAULT not in answered, (
        f"ALT_VAULT now coincides with a fixture answer ({sorted(answered)}) — "
        "pick one neither fixture uses"
    )
    return render_app.render(
        tmp_path_factory.mktemp("render-vault"),
        dest_name="render-vault",
        data={"onepassword_vault": ALT_VAULT},
    )


@dataclass(frozen=True)
class Repo:
    """One rendered repository plus the answers that produced it."""

    label: str
    path: Path
    answers: dict


# label -> (render fixture, answer fixture, the answers that render overrode).
# A derived render answers everything else like its base fixture, so the
# overrides keep the assertions and the tree on the same answer set.
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
    # The GitLab shape from unlike answers. Copier takes `--data` as strings,
    # so the booleans are restated here as the values the render resolved.
    "gitlab-unlike": (
        "rendered_gitlab_unlike",
        "answers_b",
        {**GITLAB_UNLIKE_OVERRIDES, "enable_image_build": True},
    ),
    "gitlab-no-build": ("rendered_gitlab_no_build", "answers", {"enable_image_build": False}),
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
    # The library is the same upstream in both on purpose (a fixture pinned
    # elsewhere would exercise includes no real tenant gets), and the two
    # all-optional-components-off answers coincide by construction.
    assert shared <= {"lib_ref", "lib_project"}, (
        "answers that must differ between the fixtures now coincide: "
        + ", ".join(sorted(shared))
    )


# Answers whose fixture-A value cannot serve as evidence in a cross-render diff.
# Keep this list SHORT: an entry here is coverage given up, so each names the
# targeted gate that replaces it.
CROSS_RENDER_EXEMPT = {
    "privileged_runner_tag": (
        "'infrastructure' is also the name of the platform Flux Kustomization the "
        "operator wiring dependsOn — test_build_job_carries_the_privileged_tag is "
        "the targeted gate"
    ),
    "k8s_version": (
        "the GitHub shape's workflows are vendored byte-identically from the "
        "library and carry its own literal — test_k8s_version_reaches_the_gitlab_"
        "shape is the targeted gate, and docs/CI-SHAPES.md records the limitation"
    ),
    "secret_item": "'App Secrets' is ordinary prose in the docs the render ships",
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
def _vendored_render_paths() -> frozenset[str]:
    """Every `vendored:` copy under template/, as a path relative to a render."""
    document = yaml.safe_load(
        (REPO_ROOT / "scripts" / "vendored-manifest.yml").read_text(encoding="utf-8")
    )
    paths = set()
    for entry in document.get("vendored") or []:
        consumer = entry if isinstance(entry, str) else entry["consumer"]
        if not consumer.startswith("template/"):
            continue
        bare = re.sub(r"\{%.*?%\}", "", consumer[len("template/") :])
        paths.add("/".join(part for part in bare.split("/") if part))
    return frozenset(paths)


VENDORED_FILES = _vendored_render_paths()


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
    """The vendored exemption is per file, not per directory: a value dropped
    into the template-owned workflow beside the vendored ones is still found."""
    root = tmp_path / "render"
    shutil.copytree(request.getfixturevalue("rendered_b"), root)
    target = root / ".github" / "workflows" / "manifest-gates.yml"
    target.write_text(f"{target.read_text()}# {answers['app_slug']}\n")
    leaks = _fixture_a_leaks(root, answers, request.getfixturevalue("answers_b"))
    assert [leak for leak in leaks if "manifest-gates.yml" in leak], leaks


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
    renders. `privileged_runner_tag` and `k8s_version` are un-exempted here,
    their CROSS_RENDER_EXEMPT collisions being outside the pipeline.
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
    ".cursor/rules/project-development.mdc",
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
            assert alert["annotations"]["runbook_url"] == repo.answers["runbook_url"]
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
    assert f"{slug}.{repo.answers['external_domain']}" in served
    if repo.answers["enable_internal_ingress"]:
        assert f"{slug}.{repo.answers['internal_domain']}" in served


def test_hostnames_come_from_the_answers(repo):
    slug, external = repo.answers["app_slug"], repo.answers["external_domain"]
    route = yaml.safe_load((repo.path / FLUX / "ingressroute.yaml").read_text())
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
    route = yaml.safe_load((repo.path / FLUX / "ingressroute.yaml").read_text())
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
        assert expected in naming, f"{expected} no longer names the image repository"

    assert MIXED_CASE_GIT_NAMESPACE in (root / "CODEOWNERS").read_text()
    assert MIXED_CASE_GIT_NAMESPACE in (root / "docs" / "ONBOARDING.md").read_text()


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


def _onboarding_wiring(root: Path) -> list[dict]:
    """The documents of the operator wiring file, out of its Markdown fence.

    Nothing else parses them, so the file the operator applies by hand is
    validated only here.
    """
    fenced = (root / "docs" / "ONBOARDING.md").read_text().split("```yaml")[1].split("```")[0]
    return [d for d in yaml.safe_load_all(fenced) if isinstance(d, dict)]


def _onboarding_store(root: Path) -> dict:
    stores = [d for d in _onboarding_wiring(root) if d.get("kind") == "ClusterSecretStore"]
    assert len(stores) == 1, "the wiring file must carry exactly one store"
    return stores[0]


def test_wiring_store_scopes_itself_to_the_tenant(repo):
    if repo.answers["secrets_backend"] == "none":
        # Assert on the DOCUMENTS, never on the prose: the `none` branch of step
        # O1 tells the operator to skip "the `ClusterSecretStore` in O3", so a
        # substring check fails on the very render it was written for.
        kinds = [d["kind"] for d in _onboarding_wiring(repo.path)]
        assert "ClusterSecretStore" not in kinds, (
            "a store the tenant has nothing to read from, and one more "
            "cluster-scoped object for the operator to keep scoped"
        )
        assert "externalsecret.yaml" not in _kustomization(repo.path)["resources"]
        return
    store = _onboarding_store(repo.path)
    assert store["spec"]["conditions"] == [{"namespaces": [repo.answers["app_namespace"]]}], (
        "a ClusterSecretStore without `conditions` is readable by every "
        "namespace in the cluster"
    )


def test_the_wiring_kustomization_points_at_this_repo(repo):
    """The wiring Kustomization is the whole link between cluster and repo, and
    kubeconform validates its schema, not its path or target namespace."""
    docs = [d for d in _onboarding_wiring(repo.path) if d.get("kind") == "Kustomization"]
    assert len(docs) == 1, "the wiring file must carry exactly one Kustomization"
    spec, slug = docs[0]["spec"], repo.answers["app_slug"]
    assert spec["path"].removeprefix("./") == FLUX, spec["path"]
    assert (repo.path / FLUX / "kustomization.yaml").is_file()
    assert spec["targetNamespace"] == repo.answers["app_namespace"]
    assert spec["sourceRef"]["name"] == slug
    assert spec["serviceAccountName"] == f"{slug}-flux"
    assert spec["prune"] is True


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


def test_onepassword_wiring_store_names_the_answered_vault(rendered_alt_vault):
    """The 1Password store names the answered vault.

    The shaped fixture answers the reference cluster's vault, so only this
    render, which changes that one answer, tells substitution from a literal.
    """
    provider = _onboarding_store(rendered_alt_vault)["spec"]["provider"]["onepassword"]
    assert provider["vaults"] == {ALT_VAULT: 1}
    assert "Homelab" not in (rendered_alt_vault / "docs" / "ONBOARDING.md").read_text()


def test_a_boolean_shaped_vault_name_survives_the_store(tmp_path_factory):
    """`No` is a YAML 1.1 boolean, and Kubernetes parses manifest YAML with a
    1.1 parser. Unquoted, the key becomes `false` and the store names a vault
    that does not exist."""
    render = render_app.render(
        tmp_path_factory.mktemp("render-bool-vault"),
        dest_name="render-bool-vault",
        data={"onepassword_vault": "No"},
    )
    assert _onboarding_store(render)["spec"]["provider"]["onepassword"]["vaults"] == {"No": 1}


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
        [".gitlab-ci.yml", ".gitlab/secret-detection-ruleset.toml", "scripts/check-lib-pins.py"],
        [".github/workflows/ci.yml"],
    ),
    # build-image.yml is not listed here: it is gated on `enable_image_build`
    # as well as the shape, so it has its own test below.
    "github": (
        [".github/workflows/ci.yml", ".github/workflows/manifest-gates.yml"],
        [".gitlab-ci.yml", ".gitlab/secret-detection-ruleset.toml", "scripts/check-lib-pins.py"],
    ),
    # Issue and change-request templates are NOT here: they follow the `forge`
    # answer, which test_forge_metadata_follows_the_forge holds.
    "none": (
        [],
        [
            ".gitlab-ci.yml",
            ".github/workflows/ci.yml",
            ".github/workflows/manifest-gates.yml",
            ".gitlab/secret-detection-ruleset.toml",
            "scripts/semantic-release.py",
        ],
    ),
}

# Sentences the tenant-side docs may only carry on a shape where no pipeline
# runs the three manifest gates. On `github` the `manifest-gates` workflow does,
# so a doc that still claims otherwise sends the tenant looking for a gate it has.
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


def test_generated_pipeline_pins_the_library(rendered, answers):
    ci = render_app.load_ci(rendered / ".gitlab-ci.yml")
    assert ci["variables"]["WEISSSRV_LIB_REF"] == answers["lib_ref"]
    includes = [i for i in ci["include"] if isinstance(i, dict) and "project" in i]
    assert includes, "the pipeline includes no library templates"
    for include in includes:
        assert include["project"] == answers["lib_project"]
        assert include["ref"] == answers["lib_ref"], (
            "GitLab cannot interpolate a variable into `include: ref:`, so every "
            "entry repeats the tag — and every one must match the single source"
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


def test_build_job_carries_the_privileged_tag(rendered, answers):
    """The targeted gate CROSS_RENDER_EXEMPT gives up the blanket scan for."""
    ci = render_app.load_ci(rendered / ".gitlab-ci.yml")
    build = [
        i for i in ci["include"]
        if isinstance(i, dict) and str(i.get("file", "")).endswith("docker-build.yml")
    ]
    assert len(build) == 1, "the image build must be included exactly once"
    assert build[0]["inputs"]["tags"] == [answers["privileged_runner_tag"]]
    assert build[0]["inputs"]["cpu_selector"] == answers["ci_cpu_selector"]


@pytest.mark.parametrize("render_fixture", ["rendered", "rendered_gitlab_unlike"])
def test_the_image_build_runs_after_the_secret_scan(request, render_fixture):
    """The library's docker-build job sets no `needs:`, so stage order is the
    only thing keeping the privileged build off a tree the secret scan has not
    cleared."""
    ci = render_app.load_ci(request.getfixturevalue(render_fixture) / ".gitlab-ci.yml")
    stages = ci["stages"]
    assert stages.index("build") > stages.index("security"), stages
    build = [
        i for i in ci["include"]
        if isinstance(i, dict) and str(i.get("file", "")).endswith("docker-build.yml")
    ]
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


# Top-level pipeline keys that configure the pipeline rather than declare a job.
PIPELINE_RESERVED_KEYS = frozenset(
    {
        "after_script",
        "before_script",
        "cache",
        "default",
        "image",
        "include",
        "services",
        "stages",
        "variables",
        "workflow",
    }
)


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
        body = {**_resolved(ci, target, (*seen, name)), **body}
    return body


def _pipeline_jobs(ci: dict) -> list[str]:
    """The jobs the pipeline declares itself, hidden templates excluded."""
    return [
        name
        for name, body in ci.items()
        if isinstance(body, dict)
        and not name.startswith(".")
        and name not in PIPELINE_RESERVED_KEYS
    ]


def test_every_local_job_resolves_its_extends_chain(rendered):
    """A job extending a name the pipeline does not define is not a red job:
    GitLab refuses the pipeline, so a generated repo would have none at all. The
    resolved `before_script` is what makes these gates runnable on the runner."""
    ci = render_app.load_ci(rendered / ".gitlab-ci.yml")
    checked = 0
    for name in _pipeline_jobs(ci):
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
    for name in _pipeline_jobs(ci):
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
    scan = [
        i for i in ci["include"]
        if isinstance(i, dict) and str(i.get("file", "")).endswith("secret-detection.yml")
    ]
    assert len(scan) == 1, "secret detection must be included exactly once"
    assert scan[0]["inputs"]["cpu_selector"] == answers["ci_cpu_selector"]


def test_k8s_version_reaches_the_gitlab_shape(rendered, answers):
    """The other targeted gate: the answer must reach both places the GitLab
    shape validates from, so they cannot disagree."""
    ci = render_app.load_ci(rendered / ".gitlab-ci.yml")
    flux_lint = [
        i for i in ci["include"]
        if isinstance(i, dict) and str(i.get("file", "")).endswith("flux-lint.yml")
    ]
    assert flux_lint[0]["inputs"]["k8s_version"] == answers["k8s_version"]
    taskfile = yaml.safe_load((rendered / "Taskfile.yml").read_text())
    assert taskfile["vars"]["K8S_VERSION"] == answers["k8s_version"]


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
    r"kustomize build ([\w/.-]+)",
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


# Hint for the binaries no pip install provides. The render-validate job
# fetches them with sha256 verification and runs these same gates, so the
# test job skips them instead of failing.
_TOOLCHAIN_HINT = "Install it in the test job the way render-validate does."


def _run(repo_path: Path, *command: str) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=repo_path, capture_output=True, text=True)


def test_generated_repo_passes_yamllint(repo):
    require_tool(
        "yamllint",
        "generated-repo yamllint",
        "pip install yamllint in the test job.",
        ci_optional=True,
    )
    result = _run(repo.path, "yamllint", "--strict", "-c", ".yamllint", ".")
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
        "the tracked scan saw no AGENTS.md — this gate fell back to docs/ again"
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
            "render-validate runs this gate in CI"
        )
    built = _run(repo.path, "kustomize", "build", FLUX)
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


# O2 and O2b are conditional on the secret backend and the pull credential;
# every other step is unconditional, and a missing one leaves the operator a gap
# they cannot see.
ONBOARDING_STEPS = tuple(f"### O{n} " for n in (1, 3, 4, 5, 6, 7, 8, 9))


@pytest.mark.parametrize("step", ONBOARDING_STEPS)
def test_onboarding_ships_every_step(repo, step):
    onboarding = (repo.path / "docs" / "ONBOARDING.md").read_text(encoding="utf-8")
    assert step in onboarding, f"docs/ONBOARDING.md is missing {step.strip()}"


def test_onboarding_ships_the_secret_steps_with_a_backend(repo):
    onboarding = (repo.path / "docs" / "ONBOARDING.md").read_text(encoding="utf-8")
    wanted = repo.answers["secrets_backend"] != "none"
    assert ("### O2 " in onboarding) is wanted


def test_codeowners_warns_only_where_the_forge_needs_it(repo):
    """GitHub drops a bare organisation name as an unknown owner; GitLab does
    not, and the note would be noise there. The warning follows `forge`, which
    decides who parses the file."""
    warned = "@org/team" in (repo.path / "CODEOWNERS").read_text()
    assert warned is (repo.answers["forge"] == "github")


def test_the_image_repository_is_lowercase(repo):
    """A registry repository name may not contain uppercase, so a mixed-case
    answer renders a reference the kubelet refuses to pull. The TAG may carry
    any case: the shipped one is the `:REPLACE-ME` placeholder."""
    deployment = (repo.path / FLUX / "deployment.yaml").read_text()
    lines = [line for line in deployment.splitlines() if line.strip().startswith("image:")]
    assert lines, "the Deployment names no image"
    repository = lines[0].split("image:", 1)[1].strip().rsplit(":", 1)[0]
    assert repository == repository.lower(), f"the image repository is not lowercase: {repository}"


# answers fixture name -> the file render-validate names with --answers.
_ANSWERS_FILES = {"answers": render_app.ANSWERS.name, "answers_b": render_app.ANSWERS_B.name}


def _answer_set(answers_file: str, data: dict) -> tuple:
    """One render, as (answers file, its --data overrides) with values as CI
    spells them: copier takes every --data as a string."""
    normalised = {
        key: str(value).lower() if isinstance(value, bool) else str(value)
        for key, value in data.items()
    }
    return (answers_file, frozenset(normalised.items()))


# The answers file each render-validate line names, by basename.
_ANSWERS_PATHS = {
    render_app.ANSWERS.name: render_app.ANSWERS,
    render_app.ANSWERS_B.name: render_app.ANSWERS_B,
}


def _render_validate_lines() -> list[tuple[str, dict[str, str], bool]]:
    """One entry per validate_render.py line: (answers file, its --data
    overrides, whether --lib-path rides with it)."""
    job = render_app.load_ci(REPO_ROOT / ".gitlab-ci.yml")["render-validate"]
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
    assert lines, "render-validate runs no validate_render.py invocation"
    unguarded = []
    for answers_file, data, has_lib_path in lines:
        answers = yaml.safe_load(_ANSWERS_PATHS[answers_file].read_text())
        if data.get("ci_shape", answers["ci_shape"]) != "gitlab_selfhosted":
            continue
        if not has_lib_path:
            unguarded.append(f"{answers_file} {sorted(data.items())}")
    assert not unguarded, (
        "these render-validate lines render a GitLab pipeline with no --lib-path, "
        "so the include-contract gate silently skips:\n  " + "\n  ".join(unguarded)
    )


def _copier_questions() -> set[str]:
    document = yaml.safe_load((REPO_ROOT / "copier.yml").read_text())
    return {key for key in document if not key.startswith("_")}


def test_every_render_overrides_a_question_copier_declares():
    """copier drops an unknown --data key without a word, so a typo renders the
    base fixture and every assertion in that entry agrees with itself."""
    questions = _copier_questions()
    invocations = _render_validate_invocations()
    assert questions, "copier.yml declared no questions"
    assert invocations, "render-validate runs no validate_render.py invocation"
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
        wanted = _answer_set(_ANSWERS_FILES[answers_fixture], overrides)
        if wanted not in covered:
            missing.append(f"{label}: {wanted[0]} {sorted(wanted[1])}")
    assert not missing, (
        "render-validate renders no tree with these answers:\n  " + "\n  ".join(missing)
    )


def test_the_cursorrules_pointer_restates_no_rules(repo):
    """One loud copy of the invariants, in the `.mdc` Cursor loads. The legacy
    `.cursorrules` points at it."""
    rules = (repo.path / ".cursorrules").read_text(encoding="utf-8")
    assert "project-development.mdc" in rules
    assert not [line for line in rules.splitlines() if line.startswith("- ")], (
        ".cursorrules carries rule bullets again — they belong in the .mdc only"
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


_NOT_PUSHED = "does NOT push to"


def test_a_non_ghcr_answer_is_flagged_on_the_github_shape(
    rendered_image_github, rendered, tmp_path_factory
):
    """The vendored build workflow publishes from its own literals, so an
    answered registry the GitHub shape cannot honour has to be said out loud —
    and only there. Both terms of the condition get a negative case."""
    flagged = (rendered_image_github / "docs" / "ONBOARDING.md").read_text(encoding="utf-8")
    assert _NOT_PUSHED in flagged

    gitlab = (rendered / "docs" / "ONBOARDING.md").read_text(encoding="utf-8")
    assert _NOT_PUSHED not in gitlab, "a GitLab tenant has no GitHub workflow to warn about"

    ghcr = render_app.render(
        tmp_path_factory.mktemp("render-ghcr"),
        answers=render_app.ANSWERS_B,
        dest_name="render-ghcr",
        data={"enable_image_build": "true", "registry_host": "ghcr.io"},
    )
    onboarding = (ghcr / "docs" / "ONBOARDING.md").read_text(encoding="utf-8")
    assert _NOT_PUSHED not in onboarding, "ghcr.io IS what the vendored workflow pushes to"

    # docs/VERSIONING.md tells the tenant where the build lands, so it may not
    # name a registry the vendored workflow never pushes to.
    versioning = (rendered_image_github / "docs" / "VERSIONING.md").read_text(encoding="utf-8")
    registry = yaml.safe_load(render_app.ANSWERS_B.read_text())["registry_host"]
    assert "ghcr.io/<owner>/<repo>" in versioning
    assert f"{registry}/" not in versioning, "the GitHub build pushes to GHCR, not this registry"


def test_the_pull_credential_steps_follow_the_forge(rendered, tmp_path_factory):
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
    steps = (other / "docs" / "ONBOARDING.md").read_text(encoding="utf-8")
    assert "### O2b " in steps
    assert "gitlab+deploy-token" not in steps, "a GitHub tenant has no GitLab deploy tokens"
    assert "read:packages" not in steps, "that scope belongs to ghcr.io, not this registry"

    ghcr = render_app.render(
        tmp_path_factory.mktemp("render-pull-ghcr"),
        answers=render_app.ANSWERS_B,
        dest_name="render-pull-ghcr",
        data={**shared, "registry_host": "ghcr.io", "registry_pull_host": "ghcr.io"},
    )
    assert "read:packages" in (ghcr / "docs" / "ONBOARDING.md").read_text(encoding="utf-8")

    gitlab = (rendered / "docs" / "ONBOARDING.md").read_text(encoding="utf-8")
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
        assert count, f"the template no longer contains the action {pattern}"
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


def test_the_pull_credential_is_valid_dockerconfigjson(repo):
    """The body is assembled by a Jinja loop that closes the JSON itself, and
    a malformed one is ImagePullBackOff on first reconcile with nothing red."""
    if not (
        repo.answers["enable_registry_pull_secret"] and repo.answers["secrets_backend"] != "none"
    ):
        pytest.skip("this render ships no pull credential")
    auths = _auths(repo.path / FLUX / "externalsecret-registry.yaml")
    assert set(auths) == {repo.answers["registry_host"], repo.answers["registry_pull_host"]}
    for entry in auths.values():
        assert {"username", "password", "auth"} <= set(entry)


def test_one_registry_host_yields_one_auths_entry(tmp_path_factory):
    """Answering the same host twice must deduplicate, not emit it twice."""
    render = render_app.render(
        tmp_path_factory.mktemp("render-pull-one-host"),
        answers=render_app.ANSWERS_B,
        dest_name="render-pull-one-host",
        data={
            "enable_image_build": "true",
            "enable_registry_pull_secret": "true",
            "registry_host": "ghcr.io",
            "registry_pull_host": "ghcr.io",
        },
    )
    assert set(_auths(render / FLUX / "externalsecret-registry.yaml")) == {"ghcr.io"}


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
    onboarding = (render / "docs" / "ONBOARDING.md").read_text(encoding="utf-8")
    assert "read:packages" not in onboarding, (
        "the GHCR walkthrough must not render for an image that lives elsewhere"
    )


def test_onboarding_ships_the_pull_credential_step_with_the_component(repo):
    onboarding = (repo.path / "docs" / "ONBOARDING.md").read_text(encoding="utf-8")
    wanted = repo.answers["enable_registry_pull_secret"] and (
        repo.answers["secrets_backend"] != "none"
    )
    assert ("### O2b " in onboarding) is bool(wanted)


def test_the_github_shape_names_the_k8s_version_the_workflow_hardcodes(rendered_b, answers_b):
    """The vendored workflows carry the library's own `env.K8S_VERSION` literal
    and cannot take an answer, so the divergence has to be said out loud."""
    onboarding = (rendered_b / "docs" / "ONBOARDING.md").read_text(encoding="utf-8")
    assert "K8S_VERSION" in onboarding
    assert answers_b["k8s_version"] in onboarding


def _local_hooks(repo: Repo) -> list[dict]:
    hooks = yaml.safe_load((repo.path / ".pre-commit-config.yaml").read_text())
    return [h for r in hooks["repos"] if r["repo"] == "local" for h in r["hooks"]]


def _local_hook_ids(repo: Repo) -> list[str]:
    return [hook["id"] for hook in _local_hooks(repo)]


@pytest.mark.parametrize(
    "hook_id", ["netpol-except-parity", "scrape-wiring", "kustomization", "doc-links"]
)
def test_every_local_gate_has_a_pre_commit_hook(repo, hook_id):
    """On `github` and `none` there is no CI job for these, so the hook is the
    only automatic runner a tenant has."""
    assert hook_id in _local_hook_ids(repo), (
        f"no pre-commit hook runs {hook_id}, so on a pipeline-less repo nothing "
        "automatic does"
    )


def _hook_scanned_paths(repo: Repo, hook: dict) -> list[str]:
    """The render-relative paths a hook's `files:` filter has to match for
    pre-commit to invoke it: every Markdown file for doc-links, and the trees the
    entry names for the rest."""
    if hook["id"] == "doc-links":
        return [str(path.relative_to(repo.path)) for path in sorted(repo.path.rglob("*.md"))]
    scanned: list[str] = []
    for token in hook["entry"].split():
        if not token.startswith(("scripts/", "kubernetes/")):
            continue
        target = repo.path / token
        found = [target] if target.is_file() else sorted(target.rglob("*.yaml"))
        scanned += [str(path.relative_to(repo.path)) for path in found]
    return scanned


def test_every_local_hook_runs_a_path_the_render_ships(repo):
    """The hook id being right proves nothing about what it runs, and on
    `github` and `none` the hooks are the only automatic runner. A `files:` that
    matches none of those paths is a hook pre-commit never invokes."""
    for hook in _local_hooks(repo):
        for token in hook["entry"].split():
            if token.startswith(("scripts/", "kubernetes/")):
                assert (repo.path / token).exists(), (
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


def _flux_lint_script(repo: Repo) -> str:
    """The rendered flux-lint task as a runnable shell script: go-task's own
    `{{.VAR}}` references are substituted from the Taskfile's `vars:`, to a
    fixed point, because one var's value references another's."""
    taskfile = yaml.safe_load((repo.path / "Taskfile.yml").read_text())
    script = "\n".join(taskfile["tasks"]["flux-lint"]["cmds"])
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
    bindir.mkdir(parents=True)
    (bindir / "kustomize").write_text(f"#!/bin/sh\nprintf '%s' '{render}'\n")
    (bindir / "kubeconform").write_text(
        f"#!/bin/sh\nprintf '%s\\n' '{summary}'\nexit {kubeconform_rc}\n"
    )
    for name in ("kustomize", "kubeconform"):
        (bindir / name).chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    return env


def test_the_manifest_gate_rejects_an_empty_build(repo, tmp_path):
    """kubeconform exits 0 on empty input, so the tenant's own gate has to
    notice. The guard is RUN here; a disarmed `exit 1` fails this test."""
    script = _flux_lint_script(repo)
    env = _stubbed_path(tmp_path / "empty" / "bin", "", CLEAN_SUMMARY)
    result = subprocess.run(
        ["sh", "-c", script], cwd=repo.path, capture_output=True, text=True, env=env
    )
    assert result.returncode != 0, "flux-lint reported a build with no resources as clean"
    assert "produced no resources" in result.stderr, result.stdout + result.stderr


def test_the_manifest_gate_rejects_a_skipped_schema(repo, tmp_path):
    """kubeconform also exits 0 on a resource it skipped for want of a schema,
    which in a tenant repo is most of them. A budget over ALLOWED_SKIPS reds."""
    script = _flux_lint_script(repo)
    assert "against no schema" in script, "flux-lint no longer reports a skipped resource"
    env = _stubbed_path(tmp_path / "skips" / "bin", "kind: Service\n", SKIPPED_SUMMARY)
    result = subprocess.run(
        ["sh", "-c", script], cwd=repo.path, capture_output=True, text=True, env=env
    )
    assert result.returncode != 0, "flux-lint reported 3 unvalidated resources as clean"
    assert "against no schema" in result.stderr, result.stdout + result.stderr


def test_the_manifest_gate_reports_an_invalid_resource(repo, tmp_path):
    """A non-zero kubeconform must red the task and carry its own output, not
    swallow it into the command substitution."""
    script = _flux_lint_script(repo)
    env = _stubbed_path(
        tmp_path / "invalid" / "bin", "kind: Service\n", INVALID_SUMMARY, kubeconform_rc=1
    )
    result = subprocess.run(
        ["sh", "-c", script], cwd=repo.path, capture_output=True, text=True, env=env
    )
    assert result.returncode != 0, "flux-lint reported an invalid resource as clean"
    assert "Invalid: 1" in result.stderr, result.stdout + result.stderr


def test_the_manifest_gate_rejects_a_summary_without_a_skip_count(repo, tmp_path):
    """A summary the sed parse cannot read leaves the skip budget compared
    against an empty string, which must red rather than pass."""
    script = _flux_lint_script(repo)
    env = _stubbed_path(tmp_path / "no-skips" / "bin", "kind: Service\n", NO_SKIPPED_SUMMARY)
    result = subprocess.run(
        ["sh", "-c", script], cwd=repo.path, capture_output=True, text=True, env=env
    )
    assert result.returncode != 0, "flux-lint reported an unparsable summary as clean"
    assert "against no schema" in result.stderr, result.stdout + result.stderr


def test_the_manifest_gate_passes_a_clean_render(repo, tmp_path):
    """Both negative controls also pass on a permanently-broken gate, so the
    success path — the Skipped parse and the budget compare — needs its own."""
    script = _flux_lint_script(repo)
    env = _stubbed_path(tmp_path / "clean" / "bin", "kind: Service\n", CLEAN_SUMMARY)
    result = subprocess.run(
        ["sh", "-c", script], cwd=repo.path, capture_output=True, text=True, env=env
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Skipped: 0" in result.stdout, result.stdout + result.stderr


def _gate_scripts(repo: Repo, task: str, job: str) -> list[str]:
    """Every shipped form of one local gate: the Taskfile task, and on the GitLab
    shape the pipeline job running the same check."""
    taskfile = yaml.safe_load((repo.path / "Taskfile.yml").read_text())
    assert {"task": task} in taskfile["tasks"]["lint"]["cmds"], (
        f"the {task} task exists but `task lint` does not run it"
    )
    scripts = ["\n".join(taskfile["tasks"][task]["cmds"])]
    if repo.answers["ci_shape"] == "gitlab_selfhosted":
        ci = render_app.load_ci(repo.path / ".gitlab-ci.yml")
        assert job in ci, f"the GitLab pipeline runs no {task} gate"
        scripts.append("\n".join(_resolved(ci, job)["script"]))
    return scripts


def _gate_runs(repo: Repo, tmp_path: Path, name: str, task: str, job: str):
    """(work, script) for every shipped form of the gate, each on its own copy
    of the render."""
    for index, script in enumerate(_gate_scripts(repo, task, job)):
        work = tmp_path / f"{name}-{index}"
        shutil.copytree(repo.path, work)
        yield work, script


def _kustomization_scripts(repo: Repo) -> list[str]:
    return _gate_scripts(repo, "kustomization", "kustomization-check")


def _kustomization_runs(repo: Repo, tmp_path: Path, name: str):
    return _gate_runs(repo, tmp_path, name, "kustomization", "kustomization-check")


def test_the_kustomization_gate_rejects_an_empty_list(repo, tmp_path):
    """kustomize exits 0 on an emptied `resources:` and the cluster-side
    Kustomization then prunes the namespace. The guard is RUN here."""
    for work, script in _kustomization_runs(repo, tmp_path, "empty"):
        kustomization = work / FLUX / "kustomization.yaml"
        document = yaml.safe_load(kustomization.read_text())
        document["resources"] = []
        kustomization.write_text(yaml.safe_dump(document))
        result = _run(work, "sh", "-c", script)
        assert result.returncode != 0, "an emptied resource list passed the gate"
        assert "lists no resources" in result.stdout + result.stderr


def test_the_kustomization_gate_rejects_an_unlisted_manifest(repo, tmp_path):
    """A manifest kustomize never builds is never applied, and nothing else in
    a generated repo notices."""
    for work, script in _kustomization_runs(repo, tmp_path, "unlisted"):
        (work / FLUX / "configmap.yaml").write_text(
            "---\napiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: unlisted\n"
        )
        result = _run(work, "sh", "-c", script)
        assert result.returncode != 0, "an unlisted manifest passed the gate"
        assert "not listed" in result.stdout + result.stderr


def test_the_kustomization_gate_rejects_an_unlisted_yml_manifest(repo, tmp_path):
    """kustomize takes `resources: [foo.yml]` too, so a `.yml` left unlisted is
    the same inert manifest with a different suffix."""
    for work, script in _kustomization_runs(repo, tmp_path, "unlisted-yml"):
        (work / FLUX / "configmap.yml").write_text(
            "---\napiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: unlisted\n"
        )
        result = _run(work, "sh", "-c", script)
        assert result.returncode != 0, "an unlisted .yml manifest passed the gate"
        assert "not listed" in result.stdout + result.stderr


def test_the_kustomization_gate_rejects_a_listed_path_that_does_not_exist(repo, tmp_path):
    """A `resources:` entry with no file fails `kustomize build`, so the gate
    that runs first must say so rather than report the tree as clean."""
    for work, script in _kustomization_runs(repo, tmp_path, "ghost"):
        kustomization = work / FLUX / "kustomization.yaml"
        document = yaml.safe_load(kustomization.read_text())
        document["resources"].append("ghost.yaml")
        kustomization.write_text(yaml.safe_dump(document))
        result = _run(work, "sh", "-c", script)
        assert result.returncode != 0, "a listed path with no file passed the gate"
        assert "do not exist" in result.stdout + result.stderr


def test_the_kustomization_gate_accepts_a_generator_referenced_file(repo, tmp_path):
    """A configMapGenerator source is built by kustomize without being listed
    in `resources:`; reporting it as unlisted is a false failure."""
    for work, script in _kustomization_runs(repo, tmp_path, "generator"):
        (work / FLUX / "settings.yaml").write_text("key: value\n")
        kustomization = work / FLUX / "kustomization.yaml"
        document = yaml.safe_load(kustomization.read_text())
        document["configMapGenerator"] = [
            {"name": f"{repo.answers['app_slug']}-config", "files": ["settings.yaml"]}
        ]
        kustomization.write_text(yaml.safe_dump(document))
        result = _run(work, "sh", "-c", script)
        assert result.returncode == 0, result.stdout + result.stderr


def test_the_kustomization_gate_accepts_a_listed_directory(repo, tmp_path):
    """A listed directory is built whole, so the manifests inside it are not
    unlisted — the recursive scan must not report them."""
    for work, script in _kustomization_runs(repo, tmp_path, "subdir"):
        extras = work / FLUX / "extras"
        extras.mkdir()
        (extras / "configmap.yaml").write_text(
            "---\napiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: extra\n"
        )
        (extras / "kustomization.yaml").write_text(
            "---\nresources:\n  - configmap.yaml\n"
        )
        kustomization = work / FLUX / "kustomization.yaml"
        document = yaml.safe_load(kustomization.read_text())
        document["resources"].append("extras")
        kustomization.write_text(yaml.safe_dump(document))
        result = _run(work, "sh", "-c", script)
        assert result.returncode == 0, result.stdout + result.stderr


def test_the_kustomization_gate_accepts_a_dot_slash_entry(repo, tmp_path):
    """kustomize takes `./foo.yaml`; reporting the same file as unlisted is a
    false failure on a tree that builds."""
    for work, script in _kustomization_runs(repo, tmp_path, "dot-slash"):
        kustomization = work / FLUX / "kustomization.yaml"
        document = yaml.safe_load(kustomization.read_text())
        document["resources"] = [f"./{n}" for n in document["resources"]]
        kustomization.write_text(yaml.safe_dump(document))
        result = _run(work, "sh", "-c", script)
        assert result.returncode == 0, result.stdout + result.stderr


def test_the_kustomization_gate_accepts_a_trailing_slash_directory(repo, tmp_path):
    """A listed directory written `extras/` covers its children the same way."""
    for work, script in _kustomization_runs(repo, tmp_path, "trailing-slash"):
        extras = work / FLUX / "extras"
        extras.mkdir()
        (extras / "configmap.yaml").write_text(
            "---\napiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: extra\n"
        )
        (extras / "kustomization.yaml").write_text("---\nresources:\n  - configmap.yaml\n")
        kustomization = work / FLUX / "kustomization.yaml"
        document = yaml.safe_load(kustomization.read_text())
        document["resources"].append("extras/")
        kustomization.write_text(yaml.safe_dump(document))
        result = _run(work, "sh", "-c", script)
        assert result.returncode == 0, result.stdout + result.stderr


def _scrape_wiring_scripts(repo: Repo) -> list[str]:
    return _gate_scripts(repo, "scrape-wiring", "scrape-wiring-check")


def _scrape_wiring_runs(repo: Repo, tmp_path: Path, name: str):
    return _gate_runs(repo, tmp_path, name, "scrape-wiring", "scrape-wiring-check")


def test_the_scrape_gate_accepts_the_render(repo):
    """The shipped pair agrees, so this is the positive control the two
    negative cases below need to mean anything."""
    for script in _scrape_wiring_scripts(repo):
        result = _run(repo.path, "sh", "-c", script)
        assert result.returncode == 0, result.stdout + result.stderr


def test_the_scrape_gate_rejects_a_stale_port(repo, tmp_path):
    """A monitor scraping a port the allow does not name is `up == 0` and a
    page in the OPERATOR's Alertmanager, with nothing red in this repo."""
    if not repo.answers["enable_servicemonitor"]:
        pytest.skip("this render ships no ServiceMonitor")
    port = repo.answers["app_port"]
    for work, script in _scrape_wiring_runs(repo, tmp_path, "stale-port"):
        policies = (work / FLUX / "networkpolicy.yaml").read_text()
        assert f"port: {port}" in policies, "the scrape allow no longer names the app port"
        (work / FLUX / "networkpolicy.yaml").write_text(
            policies.replace(f"port: {port}", f"port: {port + 1}")
        )
        result = _run(work, "sh", "-c", script)
        assert result.returncode != 0, "a scrape port no policy admits passed the gate"
        assert str(port) in result.stdout + result.stderr


def test_the_scrape_gate_rejects_a_deleted_allow(repo, tmp_path):
    """Dropping the scrape allow while editing egress rules is the other half
    of the same break."""
    if not repo.answers["enable_servicemonitor"]:
        pytest.skip("this render ships no ServiceMonitor")
    for work, script in _scrape_wiring_runs(repo, tmp_path, "no-allow"):
        policies = list(yaml.safe_load_all((work / FLUX / "networkpolicy.yaml").read_text()))
        kept = [
            d for d in policies if d and d["metadata"]["name"] != "allow-scrape-from-observability"
        ]
        assert len(kept) < len(policies), "the scrape allow was not found to delete"
        (work / FLUX / "networkpolicy.yaml").write_text(yaml.safe_dump_all(kept))
        result = _run(work, "sh", "-c", script)
        assert result.returncode != 0, "a deleted scrape allow passed the gate"


def _scrape_policy(work: Path) -> tuple[list[dict], dict]:
    """The rendered policies and the scrape allow among them."""
    documents = [d for d in yaml.safe_load_all((work / FLUX / "networkpolicy.yaml").read_text()) if d]
    allow = next(d for d in documents if d["metadata"]["name"] == "allow-scrape-from-observability")
    return documents, allow


def _rewrite_policies(work: Path, documents: list[dict]) -> None:
    (work / FLUX / "networkpolicy.yaml").write_text(yaml.safe_dump_all(documents))


@pytest.mark.parametrize("spelling", ["named-port", "no-ports", "no-policy-types"])
def test_the_scrape_gate_accepts_the_legal_policy_spellings(repo, tmp_path, spelling):
    """A named port, an empty `ports:` and an inferred `policyTypes` all admit
    the scrape in the cluster, so red-lining them sends the tenant after a
    defect that is not there."""
    if not repo.answers["enable_servicemonitor"]:
        pytest.skip("this render ships no ServiceMonitor")
    for work, script in _scrape_wiring_runs(repo, tmp_path, f"spelling-{spelling}"):
        documents, allow = _scrape_policy(work)
        rule = allow["spec"]["ingress"][0]
        if spelling == "named-port":
            rule["ports"] = [{"protocol": "TCP", "port": "http"}]
        elif spelling == "no-ports":
            rule["ports"] = []
        else:
            del allow["spec"]["policyTypes"]
        _rewrite_policies(work, documents)
        result = _run(work, "sh", "-c", script)
        assert result.returncode == 0, result.stdout + result.stderr


def test_the_scrape_gate_rejects_a_udp_only_allow(repo, tmp_path):
    """A NetworkPolicy port entry defaults to TCP; an explicit UDP on the right
    number admits nothing the scrape uses."""
    if not repo.answers["enable_servicemonitor"]:
        pytest.skip("this render ships no ServiceMonitor")
    for work, script in _scrape_wiring_runs(repo, tmp_path, "udp-allow"):
        documents, allow = _scrape_policy(work)
        allow["spec"]["ingress"][0]["ports"][0]["protocol"] = "UDP"
        _rewrite_policies(work, documents)
        result = _run(work, "sh", "-c", script)
        assert result.returncode != 0, "a UDP-only allow passed the gate"


def _pod_monitor(repo: Repo) -> dict:
    slug = repo.answers["app_slug"]
    return {
        "apiVersion": "monitoring.coreos.com/v1",
        "kind": "PodMonitor",
        "metadata": {"name": f"{slug}-pods"},
        "spec": {
            "selector": {"matchLabels": {"app.kubernetes.io/name": slug}},
            "podMetricsEndpoints": [{"port": "http", "path": "/metrics"}],
        },
    }


def test_the_scrape_gate_reads_a_pod_monitor(repo, tmp_path):
    """A PodMonitor declares `podMetricsEndpoints`, not `endpoints`. Reading the
    wrong key reports a green verdict having checked nothing."""
    if not repo.answers["enable_servicemonitor"]:
        pytest.skip("this render ships no scrape allow to match")
    for work, script in _scrape_wiring_runs(repo, tmp_path, "pod-monitor"):
        (work / FLUX / "podmonitor.yaml").write_text(yaml.safe_dump(_pod_monitor(repo)))
        result = _run(work, "sh", "-c", script)
        assert result.returncode == 0, result.stdout + result.stderr

        monitor = _pod_monitor(repo)
        monitor["spec"]["podMetricsEndpoints"] = [
            {"targetPort": repo.answers["app_port"] + 1, "path": "/metrics"}
        ]
        (work / FLUX / "podmonitor.yaml").write_text(yaml.safe_dump(monitor))
        result = _run(work, "sh", "-c", script)
        assert result.returncode != 0, "a PodMonitor port no policy admits passed the gate"
        assert monitor["metadata"]["name"] in result.stdout + result.stderr


def test_the_tenant_docs_name_the_egress_gate(repo):
    """The gate is only a gate if the person editing the policy knows it exists
    and where it runs on this repo's CI shape."""
    architecture = (repo.path / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "check-netpol-except-parity.py" in architecture
    if repo.answers["ci_shape"] == "github":
        workflow = repo.path / ".github" / "workflows" / "manifest-gates.yml"
        assert workflow.is_file(), "the workflow the doc points the tenant at is missing"
        assert "manifest-gates" in architecture, "the workflow running the gate is unnamed"


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
    for document in _onboarding_wiring(repo.path):
        for dependency in (document.get("spec") or {}).get("dependsOn") or []:
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
    missing = sorted(name for name in expected if name not in architecture)
    assert not missing, f"the Platform contract does not name {missing}"


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
    onboarding = (rendered_off_forge_registry / "docs" / "ONBOARDING.md").read_text(
        encoding="utf-8"
    )
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
    off_forge = (rendered_off_forge_registry / "docs" / "ONBOARDING.md").read_text(
        encoding="utf-8"
    )
    assert NOT_PROJECT_REGISTRY in off_forge, "the off-forge registry is not flagged"
    on_forge = (rendered / "docs" / "ONBOARDING.md").read_text(encoding="utf-8")
    assert NOT_PROJECT_REGISTRY not in on_forge, (
        "a registry hosted on the forge's own domain is flagged as off-forge"
    )


def test_the_secret_step_names_the_backend_s_own_key(repo):
    """Step 3 tells the tenant which object to create. The two backends spell
    the name differently, and a GitLab variable cannot even hold `api-key`."""
    if repo.answers["secrets_backend"] == "none":
        return
    onboarding = (repo.path / "docs" / "ONBOARDING.md").read_text(encoding="utf-8")
    external = (repo.path / FLUX / "externalsecret.yaml").read_text()
    wanted = "api-key" if repo.answers["secrets_backend"] == "onepassword" else "APP_API_KEY"
    assert wanted in external, f"the ExternalSecret no longer reads {wanted}"
    assert wanted in onboarding, f"step 3 does not name {wanted}"


def test_the_vpa_is_documented_only_where_it_ships(repo):
    """A doc step naming a manifest the answers did not render is an
    instruction the reader cannot carry out."""
    wanted = repo.answers["enable_vpa"]
    onboarding = (repo.path / "docs" / "ONBOARDING.md").read_text(encoding="utf-8")
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
        return
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
