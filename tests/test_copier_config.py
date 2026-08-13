"""Schema-level invariants of copier.yml itself.

These run without a render, so a malformed question set fails in milliseconds
rather than after three renders. The render invariants are test_render.py.
"""

from __future__ import annotations

import re
import subprocess
import sys

import jinja2
import pytest
import yaml

import render_app

REPO_ROOT = render_app.REPO_ROOT
CONFIG = yaml.safe_load((REPO_ROOT / "copier.yml").read_text())
QUESTIONS = {k: v for k, v in CONFIG.items() if not k.startswith("_")}

# The tenant's own identity — what the service is called, where it lives, and
# who owns its code. A default here is the TEMPLATE's identity carried into
# somebody else's repository: a deployment named after the template, or a
# LICENSE with the template author's name on the tenant's work.
NO_DEFAULT = ("app_slug", "git_namespace", "copyright_holder")

# Facts about the CLUSTER, and the reason they must not be defaulted either: a
# defaults-only render is otherwise a repo that renders, lints, builds,
# validates and reconciles — while publishing hostnames in someone else's zone
# and pulling from someone else's registry. Every failure is at runtime.
CLUSTER_NO_DEFAULT = ("external_domain", "internal_domain", "internal_vip", "runbook_url")

# The rest of the cluster block, which composes from the answers above rather
# than naming a site: derived is safe precisely because the literal is gone.
CLUSTER_DERIVED = ("node_label_domain", "registry_host", "registry_pull_host")

# Enums with a fixed implementation set. Answering outside it must be
# impossible, not merely undocumented.
ENUMS = ("ci_shape", "secrets_backend")


def test_template_mechanics():
    assert CONFIG["_subdirectory"] == "template"
    assert CONFIG["_templates_suffix"] == ".jinja"
    assert CONFIG["_answers_file"] == ".copier-answers.yml"
    # copier's built-in exclude list is REPLACED by _exclude, so the built-ins
    # have to be repeated; forgetting .git ships the template's history.
    for pattern in ("copier.yml", ".git", "__pycache__"):
        assert pattern in CONFIG["_exclude"], f"{pattern} must stay excluded"


@pytest.mark.parametrize("name", NO_DEFAULT)
def test_app_identity_has_no_default(name):
    question = QUESTIONS[name]
    assert "default" not in question, (
        f"{name} identifies the TENANT, so it must have no default — a "
        "defaults-only render would otherwise carry the template's own identity "
        "into the generated repository."
    )
    assert question.get("placeholder"), f"{name} needs a placeholder to show the shape"


@pytest.mark.parametrize("name", CLUSTER_NO_DEFAULT)
def test_cluster_identity_has_no_default(name):
    question = QUESTIONS[name]
    assert "default" not in question, (
        f"{name} is a fact about the cluster, so it must have no default — "
        "pressing enter would otherwise accept the reference cluster's value "
        "and the repo would deploy, greenly, against the wrong cluster."
    )
    assert question.get("placeholder"), f"{name} needs a placeholder to show the shape"


@pytest.mark.parametrize("name", CLUSTER_DERIVED)
def test_derived_cluster_defaults_compose_from_an_answer(name):
    """A default here is allowed only because it carries no site of its own: it
    is a SHAPE around an answer already given, so it moves with that answer."""
    assert "{{" in QUESTIONS[name]["default"], (
        f"{name} defaults to a literal — either derive it from an answered "
        f"domain or move it to {CLUSTER_NO_DEFAULT}'s placeholder-only set."
    )


def test_a_defaults_only_render_is_refused(tmp_path):
    """The property the two lists above exist for, end to end.

    A stranger who answers only the app's own name must not get a working repo
    wired to the reference cluster: with no defaults, copier's empty fallback
    fails the site-identity validators and nothing is written.
    """
    src = render_app.copy_source(tmp_path)
    result = subprocess.run(
        [
            sys.executable, "-m", "copier", "copy", "--defaults", "--trust",
            "--data", "app_slug=stranger-app",
            "--data", "git_namespace=stranger",
            str(src), str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, "a defaults-only render produced a repository"
    assert not (tmp_path / "out" / "kubernetes").exists(), "a rejected render wrote manifests"


@pytest.mark.parametrize("name", ENUMS)
def test_enums_are_enumerated(name):
    assert QUESTIONS[name].get("choices"), f"{name} must declare its choices"
    assert QUESTIONS[name].get("default") in QUESTIONS[name]["choices"].values()


def test_every_question_is_documented():
    """A question nobody can look up is a prompt the operator has to guess at."""
    docs = "\n".join(
        p.read_text() for p in [REPO_ROOT / "README.md", *(REPO_ROOT / "docs").glob("*.md")]
    )
    missing = [name for name in QUESTIONS if name not in docs]
    assert not missing, "questions named in no operator doc: " + ", ".join(sorted(missing))


def test_answer_fixtures_cover_every_question():
    """`copier copy --defaults` silently falls back for an unanswered question,
    so a question added later would stop being exercised without failing."""
    for path in (render_app.ANSWERS, render_app.ANSWERS_B):
        answers = yaml.safe_load(path.read_text())
        missing = set(QUESTIONS) - set(answers)
        extra = set(answers) - set(QUESTIONS)
        assert not missing, f"{path.name} does not answer: {sorted(missing)}"
        assert not extra, f"{path.name} answers questions that do not exist: {sorted(extra)}"


def _referenced_questions(text: str) -> set[str]:
    return {name for name in QUESTIONS if re.search(rf"\b{re.escape(name)}\b", text)}


def test_no_question_reads_an_answer_asked_later():
    """A validator, default or `when:` sees only the answers given SO FAR.

    Copier renders them against the answer map, which fills in question order,
    so a forward reference is undefined interactively — the path the docs
    document — and defined in `--data`/update mode. That is a check which reads
    as enforcement and enforces nothing where it matters.
    """
    order = list(QUESTIONS)
    offenders = []
    for index, name in enumerate(order):
        later = set(order[index + 1:])
        question = QUESTIONS[name]
        for field in ("default", "validator", "when", "help"):
            value = question.get(field)
            if not isinstance(value, str) or field == "help":
                continue
            for referenced in _referenced_questions(value) & later:
                offenders.append(f"{name}.{field} reads {referenced}, asked later")
    assert not offenders, "\n  ".join(["forward references in copier.yml:"] + offenders)


def _validator_message(name: str, **context) -> str:
    """Render a question's validator the way copier does: a non-empty result is
    the rejection message, an empty one means the answer is accepted.

    `regex_search` comes from copier's extra filter set, so it is supplied here
    rather than left to fail as an unknown filter.
    """
    env = jinja2.Environment()  # noqa: S701 - rendering our own config, no user input
    env.filters["regex_search"] = lambda value, pattern: re.search(pattern, str(value))
    return env.from_string(QUESTIONS[name]["validator"]).render(**context).strip()


@pytest.mark.parametrize(
    "internal,external,rejected",
    [
        ("esweiss.com", "ericsweiss.com", False),
        # Identical zones: both IngressRoutes claim one Host().
        ("example.com", "example.com", True),
        # Different zones, same first label: `<slug>-esweiss-tls` twice, so two
        # Certificates contend for one Secret and burn the duplicate-cert limit.
        ("esweiss.io", "esweiss.com", True),
    ],
)
def test_internal_domain_rejects_a_colliding_pair(internal, external, rejected):
    message = _validator_message(
        "internal_domain", internal_domain=internal, external_domain=external
    )
    assert bool(message) is rejected, message or "accepted, but should not be"


@pytest.mark.parametrize(
    "slug,rejected",
    [
        ("recipe-box", False),
        ("Recipe_Box", True),
        ("9lives", True),
        ("trailing-", True),
    ],
)
def test_app_slug_must_be_a_dns_label(slug, rejected):
    assert bool(_validator_message("app_slug", app_slug=slug)) is rejected


@pytest.mark.parametrize("port,rejected", [(8080, False), (80, True), (70000, True)])
def test_app_port_rejects_ports_the_pod_cannot_bind(port, rejected):
    """The pod runs as UID 65532 with allowPrivilegeEscalation disabled, so a
    port under 1024 renders a Deployment that can never come up."""
    assert bool(_validator_message("app_port", app_port=port)) is rejected


@pytest.mark.parametrize(
    "vault,rejected",
    [
        ("Homelab", False),
        ("Cluster Ops", False),
        ("", True),
        # The name is a YAML key in the store's `vaults:` map and a path segment
        # in op:// references, so neither character can be carried through.
        ("Homelab/Apps", True),
        ("Homelab: Apps", True),
    ],
)
def test_onepassword_vault_rejects_names_the_store_cannot_carry(vault, rejected):
    message = _validator_message("onepassword_vault", onepassword_vault=vault)
    assert bool(message) is rejected, message or "accepted, but should not be"


@pytest.mark.parametrize("ref,rejected", [("v0.7.2", False), ("main", True), ("0.6.2", True)])
def test_lib_ref_takes_release_tags_only(ref, rejected):
    assert bool(_validator_message("lib_ref", lib_ref=ref)) is rejected


@pytest.mark.parametrize("version,rejected", [("1.36.0", False), ("1.36", True), ("", True)])
def test_k8s_version_must_be_full(version, rejected):
    """kubeconform's -kubernetes-version takes X.Y.Z; a bare minor is rejected
    at run time, several minutes into the first pipeline."""
    assert bool(_validator_message("k8s_version", k8s_version=version)) is rejected


def test_copier_pin_is_the_same_in_both_places_this_pipeline_installs_it():
    """`variables.COPIER_VERSION` and the `pip_packages:` literal must agree.

    GitLab resolves `include: inputs:` at pipeline-creation time, before job
    variables exist, so the python-tests entry cannot read the variable and
    repeats the pin — the same constraint `include: ref:` has, and the same
    silent failure: the pytest suite would render under one copier and
    render-validate under another, and the disagreement shows up as a render
    difference nobody can reproduce.
    """
    ci = render_app.load_ci(REPO_ROOT / ".gitlab-ci.yml")
    pinned = ci["variables"]["COPIER_VERSION"]
    literals = [
        package
        for include in ci["include"]
        if isinstance(include, dict)
        for package in str((include.get("inputs") or {}).get("pip_packages", "")).split()
        if package.startswith("copier==")
    ]
    assert literals, "no include installs copier — this gate examined nothing"
    for literal in literals:
        assert literal == f"copier=={pinned}", (
            f"{literal} disagrees with variables.COPIER_VERSION ({pinned})"
        )


def test_unimplemented_choices_cannot_be_answered():
    """`choices` is advisory in `--data` mode, so anything the render cannot
    actually produce must be absent from the list rather than merely
    undocumented."""
    assert set(QUESTIONS["ci_shape"]["choices"].values()) == {
        "gitlab_selfhosted",
        "github",
        "none",
    }
    assert set(QUESTIONS["secrets_backend"]["choices"].values()) == {
        "onepassword",
        "gitlab",
        "none",
    }


def test_copier_rejects_an_invalid_answer(tmp_path):
    """End-to-end proof that a validator is wired, not just well-written: a
    copier run with a bad answer must exit non-zero and write nothing."""
    src = render_app.copy_source(tmp_path)
    result = subprocess.run(
        [
            sys.executable, "-m", "copier", "copy", "--defaults", "--trust",
            "--data-file", str(render_app.ANSWERS),
            "--data", "app_slug=Not A Label",
            str(src), str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, "an invalid app_slug was accepted"
    assert not (tmp_path / "out" / "kubernetes").exists(), "a rejected render wrote manifests"


def test_conditional_paths_name_declared_questions():
    """Every `{% if %}` in a source PATH must read a real question.

    A typo there does not fail the render — copier evaluates the expression,
    gets an undefined name, and skips the file, so the component silently
    vanishes from every generated repo.
    """
    names = set(QUESTIONS)
    offenders = []
    for path in (REPO_ROOT / "template").rglob("*"):
        for part in path.relative_to(REPO_ROOT / "template").parts:
            if "{%" not in part:
                continue
            # String literals carry the CHOICE values, not answer names.
            expression = re.sub(r"'[^']*'", "", part.split("%}")[0])
            referenced = set(re.findall(r"\b([a-z_][a-z0-9_]*)\b", expression))
            unknown = referenced - names - {"if", "not", "and", "or", "in", "endif"}
            for token in unknown:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: unknown name {token!r}")
    assert not offenders, "\n  ".join(["conditional paths reference unknown answers:"] + offenders)
