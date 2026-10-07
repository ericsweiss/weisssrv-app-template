"""Schema-level invariants of copier.yml itself.

These run without a render, so a malformed question set fails in milliseconds
rather than after three renders. The render invariants are test_render.py.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib

import jinja2
import pytest
import yaml

import render_app

REPO_ROOT = render_app.REPO_ROOT
CONFIG = yaml.safe_load((REPO_ROOT / "copier.yml").read_text())
QUESTIONS = {k: v for k, v in CONFIG.items() if not k.startswith("_")}

# The tenant's own identity. A default here is the template's identity carried
# into somebody else's repository: a deployment named after the template, or a
# LICENSE with the template author's name on the tenant's work.
NO_DEFAULT = ("app_slug", "git_namespace", "copyright_holder")

# Cluster facts, undefaulted for the same reason: a defaults-only render would
# otherwise lint, build and reconcile while publishing hostnames in someone
# else's zone and pulling from someone else's registry.
CLUSTER_NO_DEFAULT = ("external_domain", "internal_domain", "internal_vip", "runbook_url")

# The rest of the cluster block, which composes from the answers above rather
# than naming a site: derived is safe precisely because the literal is gone.
CLUSTER_DERIVED = ("node_label_domain", "registry_host", "registry_pull_host")

# Enums with a fixed implementation set. Answering outside it must be
# impossible, not merely undocumented.
ENUMS = ("ci_shape", "secrets_backend", "forge")


def test_template_mechanics():
    assert CONFIG["_subdirectory"] == "template"
    assert CONFIG["_templates_suffix"] == ".jinja"
    assert CONFIG["_answers_file"] == ".copier-answers.yml"
    # A misspelled answer name must fail the render rather than substitute an
    # empty string into a tenant's file.
    assert CONFIG["_envops"]["undefined"] == "jinja2.StrictUndefined"
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
    """A render answering only the app name fails the site-identity validators."""
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
    choices = QUESTIONS[name].get("choices")
    assert choices, f"{name} must declare its choices"
    default, values = QUESTIONS[name].get("default"), set(choices.values())
    if "{%" in str(default) or "{{" in str(default):
        # `forge`'s default reads `ci_shape`, so resolve it on each shape rather
        # than comparing the expression literally.
        resolved = {
            jinja2.Template(str(default)).render(ci_shape=shape)
            for shape in QUESTIONS["ci_shape"]["choices"].values()
        }
        assert resolved <= values, f"{name} defaults outside its choices: {sorted(resolved - values)}"
    else:
        assert default in values


def test_every_question_is_documented():
    """A question nobody can look up is a prompt the operator has to guess at."""
    docs = "\n".join(
        p.read_text() for p in [REPO_ROOT / "README.md", *(REPO_ROOT / "docs").glob("*.md")]
    )
    missing = [name for name in QUESTIONS if name not in docs]
    assert not missing, "questions named in no operator doc: " + ", ".join(sorted(missing))


def test_the_answer_reference_names_every_question():
    """Every question appears in docs/CONSUMING.md, The answers."""
    text = (REPO_ROOT / "docs" / "CONSUMING.md").read_text(encoding="utf-8")
    section = text.split("## The answers", 1)[-1].split("\n## ", 1)[0]
    named = {cell.strip().strip("`") for line in section.splitlines() for cell in line.split("|")}
    assert len(named) > len(QUESTIONS), "the answer section did not parse — the guard has no subject"
    missing = sorted(set(QUESTIONS) - named)
    assert not missing, "docs/CONSUMING.md § The answers omits: " + ", ".join(missing)


def test_the_platform_contract_names_every_alert_metric():
    """docs/CONSUMING.md is source, not a render, so the render-side contract
    test cannot cover it. An operator who provisions scraping for some of the
    metrics gets alerts that never fire."""
    rule = (
        REPO_ROOT / "template" / "kubernetes" / "flux" / "prometheusrule.yaml.jinja"
    ).read_text(encoding="utf-8")
    metrics = set(re.findall(r"\b(?:kube|certmanager)_\w+", rule))
    assert metrics, "no alert metrics found — the guard has no subject"
    text = (REPO_ROOT / "docs" / "CONSUMING.md").read_text(encoding="utf-8")
    missing = sorted(m for m in metrics if m not in text)
    assert not missing, "docs/CONSUMING.md names no scrape source for: " + ", ".join(missing)


def test_answer_fixtures_cover_every_question():
    """`copier copy --defaults` silently falls back for an unanswered question,
    so a question added later would stop being exercised without failing. A
    computed question is left out: a fixture value would hide its expression."""
    asked = {
        name
        for name, question in QUESTIONS.items()
        if not (isinstance(question, dict) and question.get("when") is False)
    }
    for path in (render_app.ANSWERS, render_app.ANSWERS_B):
        answers = yaml.safe_load(path.read_text())
        missing = asked - set(answers)
        extra = set(answers) - asked
        assert not missing, f"{path.name} does not answer: {sorted(missing)}"
        assert not extra, f"{path.name} answers questions that do not exist: {sorted(extra)}"


def test_answer_fixtures_pin_the_default_library_tag():
    """copier takes a --data-file answer over the question default, so a fixture
    that pins its own tag renders a pipeline the gates never read."""
    want = CONFIG["lib_ref"]["default"]
    for path in (render_app.ANSWERS, render_app.ANSWERS_B):
        got = yaml.safe_load(path.read_text())["lib_ref"]
        assert got == want, f"{path.name} pins lib_ref {got}, not copier.yml's default {want}"


def test_library_pin_is_the_same_in_the_pipeline_and_the_answer_default():
    """This repo's own `include:` refs and what a fresh render pins move
    together: render-validate clones at the answer default, so a partial bump
    lints the template with one library and hands tenants another."""
    ci = render_app.load_ci(REPO_ROOT / ".gitlab-ci.yml")
    pinned = ci["variables"]["WEISSSRV_LIB_REF"]
    want = CONFIG["lib_ref"]["default"]
    assert pinned, "variables.WEISSSRV_LIB_REF is unset — this gate examined nothing"
    assert pinned == want, f".gitlab-ci.yml pins {pinned} but copier.yml lib_ref default is {want}"


def _version(tag: str) -> tuple:
    parts = tag.lstrip("v").split(".")
    return tuple(int(part) for part in parts if part.isdigit())


def test_every_template_release_has_a_validated_pair_row():
    """Nothing at tag time writes the row, so a release cut without relabelling
    the `main` row leaves the pair unrecorded. The NEWEST tag is exempt: the
    `main` row is its pair until the following change relabels it."""
    tags = subprocess.run(
        ["git", "tag", "-l", "v*"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert tags.returncode == 0, (
        "git could not list tags, so this gate verified nothing: " + tags.stderr.strip()
    )
    if not tags.stdout.strip():
        if os.environ.get("CI"):
            pytest.fail("no tags in this checkout, so the validated-pair table went unchecked")
        pytest.skip("no tags in this checkout (shallow clone)")
    table = (REPO_ROOT / "docs" / "VERSIONING.md").read_text(encoding="utf-8")
    rows = {
        cell.strip().strip("`")
        for line in table.splitlines()
        if line.strip().startswith("|")
        for cell in [line.split("|")[1]]
    }
    released = sorted(tags.stdout.split(), key=_version)
    missing = [tag for tag in released[:-1] if tag not in rows]
    assert not missing, (
        "docs/VERSIONING.md's validated-pair table has no row for: " + ", ".join(missing)
    )


def _default_of(question) -> object:
    """A question's default, whatever shorthand declares it."""
    return question.get("default") if isinstance(question, dict) else question


def _answer_set_changes(previous: dict, current: dict) -> list[str]:
    """Answer-set changes a generated repo cannot absorb by reviewing a diff: a
    question gone (a rename reads as one), or a default that resolves anew."""
    changes = [f"{name} (removed or renamed)" for name in sorted(set(previous) - set(current))]
    changes += [
        f"{name} (default changed)"
        for name in sorted(set(previous) & set(current))
        if _default_of(previous[name]) != _default_of(current[name])
    ]
    return changes


def _questions_at(ref: str) -> dict:
    """copier.yml's question set at a git ref."""
    result = subprocess.run(
        ["git", "show", f"{ref}:copier.yml"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"git could not read copier.yml at {ref}, so this gate verified nothing: "
        + result.stderr.strip()
    )
    config = yaml.safe_load(result.stdout)
    return {k: v for k, v in config.items() if not k.startswith("_")}


def test_every_answer_set_change_since_the_last_release_is_recorded():
    """The answer set is the template's public API: copier replays it on every
    update, so a question gone or a default resolving anew is work the operator
    does by hand. docs/VERSIONING.md is where they read about it."""
    tags = subprocess.run(
        ["git", "tag", "-l", "v*"], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    assert tags.returncode == 0, (
        "git could not list tags, so this gate verified nothing: " + tags.stderr.strip()
    )
    if not tags.stdout.strip():
        if os.environ.get("CI"):
            pytest.fail("no tags in this checkout, so the answer-set diff went unchecked")
        pytest.skip("no tags in this checkout (shallow clone)")
    newest = max(tags.stdout.split(), key=_version)
    changes = _answer_set_changes(_questions_at(newest), QUESTIONS)
    versioning = (REPO_ROOT / "docs" / "VERSIONING.md").read_text(encoding="utf-8")
    unrecorded = [change for change in changes if change.split()[0] not in versioning]
    assert not unrecorded, (
        f"these answers changed since {newest} and docs/VERSIONING.md names none of them — "
        "add each under Answer-set changes since the last release: " + ", ".join(unrecorded)
    )


def test_the_answer_set_diff_notices_a_rename_and_a_flipped_default():
    """The gate above passes vacuously whenever the answer set is unchanged, so
    the diff itself is exercised on a mutated pair."""
    previous = {"old_name": {"default": "x"}, "enable_thing": {"type": "bool", "default": False}}
    current = {"new_name": {"default": "x"}, "enable_thing": {"type": "bool", "default": True}}
    changes = _answer_set_changes(previous, current)
    assert "old_name (removed or renamed)" in changes
    assert "enable_thing (default changed)" in changes
    assert _answer_set_changes(previous, previous) == []


def _referenced_questions(text: str) -> set[str]:
    return {name for name in QUESTIONS if re.search(rf"\b{re.escape(name)}\b", text)}


def test_no_question_reads_an_answer_asked_later():
    """A validator, default or `when:` reads only answers asked before it.

    The answer map fills in question order, so a forward reference is undefined
    interactively and defined in `--data` mode.
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
    """Render a validator the way copier does: non-empty result means rejected.

    `regex_search` comes from copier's extra filter set and is supplied here.
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
        # A YAML 1.1 boolean spelling. The store quotes the key, so the name is
        # carried rather than turning into `false`.
        ("No", False),
        ("Yes", False),
    ],
)
def test_onepassword_vault_rejects_names_the_store_cannot_carry(vault, rejected):
    message = _validator_message("onepassword_vault", onepassword_vault=vault)
    assert bool(message) is rejected, message or "accepted, but should not be"


def test_git_host_defaults_from_the_forge():
    """`forge: github` with no pipeline is a supported pair, and the default a
    tenant accepts becomes the clone URL in their operator's wiring file."""
    default = QUESTIONS["git_host"]["default"]
    render = jinja2.Template(default).render
    assert render(forge="github", external_domain="brinemoor.test") == "github.com"
    assert render(forge="gitlab", external_domain="brinemoor.test") == "git.brinemoor.test"
    assert render(forge="other", external_domain="brinemoor.test") == "git.brinemoor.test"


def test_change_request_is_computed_per_forge():
    """No render answers it on gitlab or github, so the expression behind the
    vocabulary in every generated doc is proven here. The `other` arm is the
    prefill a tenant on a third forge accepts, so it names no forge's object."""
    question = QUESTIONS["change_request"]
    default = jinja2.Template(question["default"]).render
    assert default(forge="github") == "pull request"
    assert default(forge="gitlab") == "merge request"
    assert default(forge="other") == "change request"
    asked = jinja2.Template(question["when"]).render
    assert asked(forge="other") == "True"
    assert asked(forge="gitlab") == "False"
    assert asked(forge="github") == "False"


@pytest.mark.parametrize("ref,rejected", [("v0.7.4", False), ("main", True), ("0.6.2", True)])
def test_lib_ref_takes_release_tags_only(ref, rejected):
    assert bool(_validator_message("lib_ref", lib_ref=ref)) is rejected


@pytest.mark.parametrize("version,rejected", [("1.36.0", False), ("1.36", True), ("", True)])
def test_k8s_version_must_be_full(version, rejected):
    """kubeconform's -kubernetes-version takes X.Y.Z; a bare minor is rejected
    at run time, several minutes into the first pipeline."""
    assert bool(_validator_message("k8s_version", k8s_version=version)) is rejected


# One accept and one reject per validator no test above exercises. These
# questions all carry a default, so no other test reaches them. `context`
# supplies only the answers the validator itself reads.
VALIDATOR_CASES = [
    ("app_namespace", "recipe-box", None, {}),
    ("app_namespace", "Recipe_Box", "not a DNS label", {}),
    (
        "app_namespace",
        "flux-system",
        "a platform namespace: the tenant ServiceAccount is not scoped to it",
        {},
    ),
    (
        "app_namespace",
        "kube-future-system",
        "a platform namespace: the tenant ServiceAccount is not scoped to it",
        {},
    ),
    ("replica_count", 2, None, {}),
    ("replica_count", 0, "a Deployment scaled to zero serves nothing the route points at", {}),
    ("change_request", "change proposal", None, {}),
    (
        "change_request",
        "   ",
        "blank leaves every generated doc with no word for a reviewed change",
        {},
    ),
    ("git_host", "git.example.com", None, {}),
    ("git_host", "https://git.example.com", "a scheme is not a hostname", {}),
    ("privileged_runner_tag", "infrastructure", None, {}),
    (
        "privileged_runner_tag",
        "   ",
        "blank lands the build on the shared non-privileged runner, where DinD cannot start",
        {},
    ),
    (
        "ci_cpu_selector",
        "lan.example.com/cpu=modern",
        None,
        {"node_label_domain": "lan.example.com"},
    ),
    (
        "ci_cpu_selector",
        "",
        "the pipeline always passes the input, and an empty value fails the runner's "
        "allowlist regex at pod creation rather than skipping the pin",
        {"node_label_domain": "lan.example.com"},
    ),
    (
        "ci_cpu_selector",
        "zone=fast",
        "the runner's node_selector_overwrite_allowed regex refuses it at pod creation",
        {"node_label_domain": "lan.example.com"},
    ),
    (
        "ci_cpu_selector",
        "lan.example.com/cpu=fast",
        "only modern and legacy are allowed values",
        {"node_label_domain": "lan.example.com"},
    ),
    ("secret_item", "App Secrets", None, {}),
    ("secret_item", "   ", "blank is half of the item title every remoteRef resolves", {}),
    ('secret_item', 'App "Secrets"', "a double quote breaks the quoted YAML scalar", {}),
    ("gitlab_api_url", "https://gitlab.example.com", None, {}),
    ("gitlab_api_url", "http://gitlab.example.com", "must use https", {}),
    ("gitlab_api_url", "https://gitlab.example.com/api/v4", "a path is not a base URL", {}),
    (
        "gitlab_api_url",
        "https://github.com",
        "github.com is not a GitLab instance, and the store reads the GitLab API",
        {},
    ),
    ("lib_project", "eric/weisssrv-lib", None, {}),
    ("lib_project", "weisssrv-lib", "no namespace segment", {}),
    ("copyright_holder", "Homelab Operator", None, {}),
    ("copyright_holder", "   ", "blank is the name the shipped LICENSE carries", {}),
    ("external_domain", "example.com", None, {}),
    (
        "external_domain",
        "Example.COM",
        "the zone is spelled into Host() rules and certificate names, which are lowercase",
        {},
    ),
    ("git_namespace", "homelab/apps", None, {}),
    ("git_namespace", "/homelab", "a leading slash doubles the separator in every clone URL", {}),
    # A GitHub organisation is commonly mixed-case, and it is the clone URL and
    # the CODEOWNERS handle as answered; only the image path lowercases it.
    ("git_namespace", "Homelab", None, {}),
    ("git_namespace", "home lab", "a space is not legal in a forge path segment", {}),
    ("forge", "gitlab", None, {"ci_shape": "gitlab_selfhosted"}),
    (
        "forge",
        "github",
        "forge must be gitlab",
        {"ci_shape": "gitlab_selfhosted"},
        "so forge must be gitlab",
    ),
    ("forge", "github", None, {"ci_shape": "github"}),
    (
        "forge",
        "gitlab",
        "forge must be github",
        {"ci_shape": "github"},
        "so forge must be github",
    ),
    ("internal_vip", "192.168.1.101", None, {}),
    (
        "internal_vip",
        "vip.example.com",
        "the operator's DNS step needs the address the internal hostname points at, "
        "and a hostname there sends them to add a rewrite with no target",
        {},
    ),
    ("node_label_domain", "lan.example.com", None, {"internal_domain": "lan.example.com"}),
    (
        "node_label_domain",
        "lan",
        "a label prefix is a domain: a single segment matches no node label the cluster sets",
        {"internal_domain": "lan.example.com"},
    ),
    ("registry_host", "registry.git.example.com", None, {}),
    ("registry_host", "ghcr.io", None, {}),
    (
        "registry_host",
        "registry.git.example.com/group",
        "the path belongs to the image reference, not the host the pipeline logs into",
        {},
    ),
    ("registry_pull_host", "registry.git.lan.example.com", None, {}),
    (
        "registry_pull_host",
        "https://registry.git.lan.example.com",
        "a scheme is not a hostname — the pull reference is host/path:tag",
        {},
    ),
    (
        "runbook_url",
        "https://git.example.com/homelab/cluster/-/blob/main/docs/flux-operations.md",
        None,
        {},
    ),
    ("runbook_url", "git.example.com/docs", "no scheme, so the alert annotation is not a link", {}),
    (
        "runbook_url",
        "https://git.example.com/it's",
        "a single quote closes the quoted YAML scalar it renders into on every alert",
        {},
    ),
]


@pytest.mark.parametrize(
    "case",
    VALIDATOR_CASES,
    ids=[f"{case[0]}-{case[1]}" for case in VALIDATOR_CASES],
)
def test_validator_accepts_and_rejects(case):
    """A case may carry a fifth element: a substring the message must contain,
    which is how a validator with several arms proves it took the right one."""
    name, answer, why, context = case[:4]
    expected = case[4] if len(case) > 4 else None
    message = _validator_message(name, **{name: answer}, **context)
    if why is None:
        assert not message, f"{name}={answer!r} was rejected: {message}"
    else:
        assert message, f"{name}={answer!r} was accepted — {why}"
        if expected:
            assert expected in message, (
                f"{name}={answer!r} was rejected by the wrong arm: {message}"
            )


# Validators with a test of their own above, which the table deliberately does
# not duplicate — the value is in the reasoning those tests carry, not in a
# second accept/reject pair.
DEDICATED_VALIDATOR_TESTS = {
    "app_slug": "test_app_slug_must_be_a_dns_label",
    "app_port": "test_app_port_rejects_ports_the_pod_cannot_bind",
    "internal_domain": "test_internal_domain_rejects_a_colliding_pair",
    "onepassword_vault": "test_onepassword_vault_rejects_names_the_store_cannot_carry",
    "lib_ref": "test_lib_ref_takes_release_tags_only",
    "k8s_version": "test_k8s_version_must_be_full",
}


def test_every_validator_is_exercised():
    """Every validator declared in copier.yml has a test case."""
    declared = {
        name
        for name, question in QUESTIONS.items()
        if isinstance(question, dict) and "validator" in question
    }
    covered = {case[0] for case in VALIDATOR_CASES} | set(DEDICATED_VALIDATOR_TESTS)
    assert not declared - covered, (
        "copier.yml validators no test exercises: " + ", ".join(sorted(declared - covered))
    )
    assert not covered - declared, (
        "these names are listed as covered but declare no validator: "
        + ", ".join(sorted(covered - declared))
    )


def test_copier_pin_is_the_same_in_both_places_this_pipeline_installs_it():
    """`variables.COPIER_VERSION` and the `pip_packages:` literal must agree.

    `include: inputs:` resolves before job variables exist, so the python-tests
    entry repeats the pin and a drift renders the two jobs under two copiers.
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


PYTHON_TESTS_INCLUDE = "/ci/test/python-tests.yml"

# template/ holds a tenant's own files, gated by the rendered pipeline and the
# rendered Taskfile; this repo's suites all live outside it.
SUITE_ROOT_SKIP = {"template", "__pycache__", ".git", ".tmp", ".pytest_cache", ".ruff_cache"}


def test_python_tests_collects_every_suite_directory():
    """A suite outside the `test_dir` input never runs in CI.

    `include: inputs:` resolve before any job exists, so a new directory of
    test_*.py is silently dropped rather than failing the pipeline.
    """
    ci = render_app.load_ci(REPO_ROOT / ".gitlab-ci.yml")
    include = next(
        (
            entry for entry in ci["include"]
            if isinstance(entry, dict) and entry.get("file") == PYTHON_TESTS_INCLUDE
        ),
        None,
    )
    assert include, f"the pipeline includes no {PYTHON_TESTS_INCLUDE}"
    test_dir = (include.get("inputs") or {}).get("test_dir")
    assert test_dir, (
        f"{PYTHON_TESTS_INCLUDE} is included without a test_dir input, so the job "
        "falls back to the library default"
    )
    collected = sorted(set(str(test_dir).split()))
    shipped = sorted({
        path.relative_to(REPO_ROOT).parts[0]
        for path in REPO_ROOT.rglob("test_*.py")
        if not SUITE_ROOT_SKIP & set(path.relative_to(REPO_ROOT).parts)
    })
    assert shipped, "this repo ships no test_*.py at all — the gate examined nothing"
    assert collected == shipped, (
        f"python-tests collects {collected} but suites live in {shipped} — "
        "a suite outside test_dir never runs"
    )


COPIER_FLOOR_DOCS = ("README.md", "docs/CONSUMING.md", "docs/VERSIONING.md")


def test_install_hints_name_the_copier_floor_copier_yml_enforces():
    """`_min_copier_version` refuses an older client before writing anything, so
    an install hint without the floor, or behind it, sends a stranger into that
    refusal."""
    floor = CONFIG["_min_copier_version"]
    for name in COPIER_FLOOR_DOCS:
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        found = set(re.findall(r"copier(?:>=| )([0-9]+\.[0-9]+\.[0-9]+)", text))
        assert found, f"{name} names no copier floor — it must state {floor}"
        assert found == {floor}, f"{name} names copier {sorted(found)}, not {floor}"


def test_python_tests_clones_the_library_the_contract_gate_reads():
    """Without the checkout, test_this_repos_pipeline_satisfies_the_include_contract
    fails the job: `require_env` stops rather than skips once $CI is set."""
    ci = render_app.load_ci(REPO_ROOT / ".gitlab-ci.yml")
    path = (ci["variables"] or {}).get("WEISSSRV_LIB_PATH")
    assert path, "variables.WEISSSRV_LIB_PATH is unset, so pytest has no library to read"
    include = next(
        (
            entry for entry in ci["include"]
            if isinstance(entry, dict) and entry.get("file") == PYTHON_TESTS_INCLUDE
        ),
        None,
    )
    assert include, f"the pipeline includes no {PYTHON_TESTS_INCLUDE}"
    setup = str((include.get("inputs") or {}).get("setup_command", ""))
    assert "git clone" in setup and "$WEISSSRV_LIB_PATH" in setup, (
        "the python-tests setup_command does not clone the library into "
        "$WEISSSRV_LIB_PATH — the include-contract gate has nothing to read"
    )
    assert "$WEISSSRV_LIB_REF" in setup, (
        "the clone does not pin variables.WEISSSRV_LIB_REF, so the gate reads "
        "a different release than the includes pin"
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
    assert set(QUESTIONS["forge"]["choices"].values()) == {"gitlab", "github", "other"}


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


def test_copier_rejects_an_undeclared_answer_name(tmp_path):
    """`_envops.undefined: jinja2.StrictUndefined`, end to end. Without it a
    misspelled name renders as the empty string, so a manifest ships a blank
    value or a `{% if %}` silently takes its false arm."""
    src = render_app.copy_source(tmp_path)
    (src / "template" / "probe.txt.jinja").write_text("{{ not_a_declared_answer }}\n")
    result = subprocess.run(
        [
            sys.executable, "-m", "copier", "copy", "--defaults", "--trust",
            "--data-file", str(render_app.ANSWERS),
            str(src), str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, "an undeclared answer name rendered anyway"
    assert "not_a_declared_answer" in result.stdout + result.stderr, (
        "the render failed for some other reason:\n" + result.stdout + result.stderr
    )


def test_conditional_paths_name_declared_questions():
    """Every `{% if %}` in a source path reads a declared question.

    StrictUndefined turns a typo into a render error; this test names the
    offending path instead, and runs without a render.
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


_TAG_LITERAL = re.compile(r"\bv\d+\.\d+\.\d+\b")


def _is_historical(path, line: str) -> bool:
    """Lines recording what a past release pinned, not what to pin now.

    A `_commit:` marker, or a validated-pair row whose first cell is a released
    template tag. The `main` row and every other table stay scanned.
    """
    stripped = line.strip()
    if stripped.startswith("_commit:"):
        return True
    if not (stripped.startswith("|") and path.name == "VERSIONING.md"):
        return False
    cells = stripped.split("|")
    return len(cells) > 1 and "main" not in cells[1] and bool(_TAG_LITERAL.search(cells[1]))


def test_docs_quote_only_the_current_library_tag():
    """Every library tag quoted in the docs equals the `lib_ref` default.

    Nothing else ties the docs to that default. `_commit:` markers in
    .copier-answers.yml examples name superseded tags and stay exempt.
    """
    want = CONFIG["lib_ref"]["default"]
    stale = []
    found = 0
    for path in [REPO_ROOT / "README.md", *sorted((REPO_ROOT / "docs").glob("*.md"))]:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _is_historical(path, line):
                continue
            for tag in _TAG_LITERAL.findall(line):
                found += 1
                if tag != want:
                    stale.append(f"{path.relative_to(REPO_ROOT)}:{lineno} {tag}")
    # Non-vacuity: the docs are expected to advertise the default at least
    # once (docs/CONSUMING.md's answer table); zero literals means the scan
    # lost its subject, not that nothing drifted.
    assert found, "no library tag literal found in the docs — the guard has no subject"
    assert not stale, (
        f"docs quote a library tag other than copier.yml's lib_ref default ({want}):\n  "
        + "\n  ".join(stale)
    )


def test_the_unreleased_pair_row_quotes_the_current_library_tag():
    """The validated-pair table is the only place the pair a release ships is
    written down, and a library bump has to relabel the `main` row by hand."""
    want = CONFIG["lib_ref"]["default"]
    rows = [
        line.strip().split("|")
        for line in (REPO_ROOT / "docs" / "VERSIONING.md").read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("|") and "main" in line.strip().split("|")[1]
    ]
    assert len(rows) == 1, (
        f"the validated-pair table has {len(rows)} `main` rows, not one — this gate "
        "no longer knows which row records the unreleased pair"
    )
    assert _TAG_LITERAL.findall(rows[0][2]) == [want], (
        "the `main` validated-pair row does not quote copier.yml's lib_ref default "
        f"({want}): {rows[0][2].strip()}"
    )


# --------------------------------------------------------------------------
# The vendored-copy manifest
# --------------------------------------------------------------------------

MANIFEST = REPO_ROOT / "scripts" / "vendored-manifest.yml"


def _consumer_paths(document: dict) -> list[str]:
    """Every consumer path the manifest names, in either entry form."""
    paths = []
    for section in ("vendored", "forked"):
        for entry in document.get(section) or []:
            paths.append(entry if isinstance(entry, str) else entry["consumer"])
    return paths


def _missing_consumers(document: dict, root) -> list[str]:
    return [path for path in _consumer_paths(document) if not (root / path).exists()]


def test_every_vendored_consumer_path_exists():
    """A conditional path renamed in template/ leaves the manifest naming a file
    that is gone, and the copy it registered is then gated by nothing."""
    document = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    paths = _consumer_paths(document)
    assert len(paths) >= 20, f"the manifest registers only {len(paths)} copies"
    assert not _missing_consumers(document, REPO_ROOT), (
        "vendored-manifest.yml names paths that do not exist: "
        f"{_missing_consumers(document, REPO_ROOT)}"
    )


# Gates this template owns outright, so no library copy claims them.
TEMPLATE_OWNED = {
    "template/scripts/check-kustomization.py",
    "template/scripts/check-scrape-wiring.py",
}


def _script_paths(root) -> set[str]:
    """Every Python gate under scripts/ and template/scripts/, repo-relative."""
    return {
        path.relative_to(root).as_posix()
        for directory in ("scripts", "template/scripts")
        for path in (root / directory).iterdir()
        # The conditional copies carry `{% endif %}` after the suffix.
        if path.is_file() and ".py" in path.name
    }


def _unclassified(document: dict, found: set[str]) -> list[str]:
    return sorted(found - set(_consumer_paths(document)) - TEMPLATE_OWNED)


def test_every_script_is_classified():
    """A library copy dropped in without a manifest entry is byte-compared by
    nothing, and a local edit to it survives every gate."""
    document = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    unclassified = _unclassified(document, _script_paths(REPO_ROOT))
    assert not unclassified, (
        "scripts are neither registered in vendored-manifest.yml nor declared "
        f"template-owned: {unclassified}"
    )


def test_an_unregistered_script_is_reported():
    """The negative case: a copy in neither set must be named."""
    document = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    found = _script_paths(REPO_ROOT) | {"scripts/new-gate.py"}
    assert _unclassified(document, found) == ["scripts/new-gate.py"]


def test_a_missing_consumer_path_is_reported():
    """The negative case: the helper must name the entry it could not resolve."""
    bogus = {"vendored": ["scripts/no-such-gate.py"], "forked": []}
    assert _missing_consumers(bogus, REPO_ROOT) == ["scripts/no-such-gate.py"]


# --------------------------------------------------------------------------
# The gitleaks configs extend the default ruleset
# --------------------------------------------------------------------------

GITLEAKS_CONFIGS = (REPO_ROOT / ".gitleaks.toml", REPO_ROOT / "template" / ".gitleaks.toml")


def _extends_default_rules(text: str) -> bool:
    return (tomllib.loads(text).get("extend") or {}).get("useDefault") is True


@pytest.mark.parametrize("path", GITLEAKS_CONFIGS, ids=lambda p: str(p.parent.name))
def test_gitleaks_extends_the_default_rules(path):
    """Without `[extend] useDefault` the file's own rules are the whole ruleset,
    and it has none: every secret scan then greens on any tree."""
    assert _extends_default_rules(path.read_text(encoding="utf-8")), (
        f"{path}: [extend] useDefault is not true — secret detection is disabled"
    )


def test_a_gitleaks_config_without_extend_is_reported():
    """The negative case: the helper must reject the disarmed file."""
    assert not _extends_default_rules('title = "x"\n[[allowlists]]\ndescription = "y"\n')
