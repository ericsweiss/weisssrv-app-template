"""Schema-level invariants of copier.yml itself.

These run without a render, so a malformed question set fails in milliseconds
rather than after three renders. The render invariants are test_render.py.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib

import jinja2
import pytest
import yaml
from jinja2.sandbox import SandboxedEnvironment

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
CLUSTER_NO_DEFAULT = (
    "external_domain",
    "internal_domain",
    "internal_vip",
    "runbook_url",
    # The database's cluster facts: B2 bucket names are global, so a guessed one
    # names a bucket nobody created, and the API server nodes are the site's own.
    "db_backup_bucket",
    "db_apiserver_cidrs",
    "db_runbook_url",
    # The build runner is the one the operator enabled on this project: a site fact.
    "privileged_runner_tag",
)

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


def test_the_answer_reference_names_every_question():
    """Every question has a row in docs/CONSUMING.md § The answers: a question
    nobody can look up is a prompt the operator has to guess at."""
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
    """The ref this repo's own pipeline declares and what a fresh render pins
    move together: validate-rendered-app clones at the pipeline pin, so a partial
    bump lints the template with one library and hands tenants another."""
    pinned = render_app.declared_lib_ref(REPO_ROOT / ".gitlab-ci.yml")
    want = CONFIG["lib_ref"]["default"]
    assert pinned, "spec.inputs.lib_ref.default is unset — this gate examined nothing"
    assert pinned == want, (
        f".gitlab-ci.yml declares {pinned} but copier.yml lib_ref default is {want}"
    )


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
        for field in ("default", "validator", "when"):
            value = question.get(field)
            if not isinstance(value, str):
                continue
            for referenced in _referenced_questions(value) & later:
                offenders.append(f"{name}.{field} reads {referenced}, asked later")
    assert not offenders, "\n  ".join(["forward references in copier.yml:"] + offenders)


def _validator_message(name: str, **context) -> str:
    """Render a validator the way copier does: non-empty result means rejected.

    The environment is copier's: this file's `_envops`, its `_jinja_extensions`,
    and the Ansible filter set copier loads by default for `regex_search`.
    """
    envops = dict(CONFIG["_envops"])
    assert envops.pop("undefined") == "jinja2.StrictUndefined"
    extensions = ["jinja2_ansible_filters.AnsibleCoreFiltersExtension"]
    extensions += list(CONFIG.get("_jinja_extensions") or [])
    env = SandboxedEnvironment(  # noqa: S701 - our own config
        extensions=extensions, undefined=jinja2.StrictUndefined, **envops
    )
    return env.from_string(QUESTIONS[name]["validator"]).render(**context).strip()


def test_copier_rejects_a_db_port_as_app_port(tmp_path):
    """The database validator is wired on a choice question, end to end."""
    src = render_app.copy_source(tmp_path)
    result = subprocess.run(
        [
            sys.executable, "-m", "copier", "copy", "--defaults", "--trust",
            "--data-file", str(render_app.ANSWERS),
            "--data", "database=postgres",
            "--data", "app_port=8000",
            str(src), str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, "app_port 8000 was accepted with a database"
    assert "question 'database'" in result.stdout + result.stderr, (
        "the render failed for some other reason:\n" + result.stdout + result.stderr
    )
    assert not (tmp_path / "out" / "kubernetes").exists(), "a rejected render wrote manifests"


def test_no_prompt_describes_a_shared_vault():
    """Each tenant reads its own vault. A store reading a shared vault lets each
    tenant namespace read every item in it, so no prompt may offer one."""
    helps = {name: str(q.get("help", "")) for name, q in QUESTIONS.items() if isinstance(q, dict)}
    shared = sorted(name for name, text in helps.items() if "shared vault" in text)
    assert not shared, f"help text describes a shared vault: {shared}"
    choices = QUESTIONS["secrets_backend"]["choices"]
    assert not [label for label in choices if "shared vault" in label], choices


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


def test_the_answer_reference_row_matches_the_computed_words():
    """docs/CONSUMING.md is the only place an operator reads the vocabulary
    before answering, and the three words are an expression nobody else holds
    against the doc."""
    default = jinja2.Template(QUESTIONS["change_request"]["default"]).render
    text = (REPO_ROOT / "docs" / "CONSUMING.md").read_text(encoding="utf-8")
    row = next(
        line for line in text.splitlines() if line.startswith("| `change_request` |")
    )
    missing = [
        default(forge=forge) for forge in ("github", "gitlab", "other")
        if default(forge=forge) not in row
    ]
    assert not missing, "the change_request row names no word for: " + ", ".join(missing)


# The ports the CloudNativePG instance pods listen on: PostgreSQL, the instance
# manager's status endpoint, and the metrics exporter.
DB_POD_PORTS = (5432, 8000, 9187)

# At least one accept and one reject per validator. `context` supplies only the
# answers the validator itself reads; a fifth element is a substring the
# rejection message must contain.
VALIDATOR_CASES = [
    ("app_slug", "recipe-box", None, {}),
    ("app_slug", "Recipe_Box", "not a DNS label", {}),
    ("app_slug", "9lives", "a DNS label starts with a letter", {}),
    ("app_slug", "trailing-", "a DNS label ends with a letter or digit", {}),
    ("app_port", 8080, None, {}),
    ("app_port", 80, "UID 65532 with every capability dropped cannot bind it", {}),
    ("app_port", 70000, "no TCP port", {}),
    ("database", "postgres", None, {"app_port": 8080}),
    *[
        ("database", "postgres", "the app's allows would reach a db port", {"app_port": port},
         f"app_port {port}")
        for port in DB_POD_PORTS
    ],
    *[("database", "none", None, {"app_port": port}) for port in DB_POD_PORTS],
    ("internal_domain", "esweiss.com", None, {"external_domain": "ericsweiss.com"}),
    (
        "internal_domain",
        "example.com",
        "both IngressRoutes claim one Host()",
        {"external_domain": "example.com"},
    ),
    (
        "internal_domain",
        "esweiss.io",
        "a shared first label puts two Certificates on one TLS Secret",
        {"external_domain": "esweiss.com"},
    ),
    ("onepassword_vault", "recipes-ns", None, {"app_namespace": "recipes-ns"}),
    (
        "onepassword_vault",
        "recipe-box",
        "the tenant-wiring gate accepts only the namespace's vault",
        {"app_namespace": "recipes-ns"},
    ),
    (
        "onepassword_vault",
        "Homelab",
        "the tenant-wiring gate accepts only the namespace's vault",
        {"app_namespace": "recipes-ns"},
    ),
    ("onepassword_vault", "", "an empty vault names no store", {"app_namespace": "recipes-ns"}),
    ("lib_ref", "v0.7.4", None, {}),
    ("lib_ref", "main", "a branch is never a valid pin", {}),
    ("lib_ref", "0.6.2", "a release tag carries the v", {}),
    ("k8s_version", "1.36.0", None, {}),
    ("k8s_version", "1.36", "kubeconform's -kubernetes-version takes X.Y.Z", {}),
    ("k8s_version", "", "kubeconform's -kubernetes-version takes X.Y.Z", {}),
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
    ("enable_internal_ingress", False, None, {"enable_public_ingress": True}),
    ("enable_sso", True, None, {"enable_public_ingress": True}),
    ("enable_sso", False, None, {"enable_public_ingress": False}),
    (
        "enable_sso",
        True,
        "forward-auth on a route that does not exist",
        {"enable_public_ingress": False},
    ),
    ("enable_internal_ingress", True, None, {"enable_public_ingress": False}),
    (
        "enable_internal_ingress",
        False,
        "an app with neither ingress has no route at all",
        {"enable_public_ingress": False},
    ),
    ("git_host", "https://git.example.com", "a scheme is not a hostname", {}),
    ("privileged_runner_tag", "tenant-build", None, {}),
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
    ("runbook_url", "git.example.com/docs", "no scheme, so the runbooks page cannot link it", {}),
    (
        "runbook_url",
        "https://git.example.com/it's",
        "a single quote closes the quoted YAML scalar it renders into on forge other",
        {},
    ),
    ("db_name", "nai", None, {}),
    ("db_name", "not-an-insider", "a hyphen is not legal in an unquoted identifier", {}),
    ("db_name", "true", "renders as a YAML boolean, not the database name", {}),
    ("db_name", "yes", "renders as a YAML boolean, not the database name", {}),
    ("db_name", "null", "renders as a YAML null, so initdb gets no database name", {}),
    ("db_name", "off", "a YAML 1.1 boolean to the parsers that still read 1.1", {}),
    ("db_name", "true_store", None, {}),
    ("db_name", "postgres", "CNPG cannot create a built-in database as the app's", {}, "built-in"),
    ("db_name", "template0", "CNPG cannot create a built-in database as the app's", {}, "built-in"),
    ("db_name", "template1", "CNPG cannot create a built-in database as the app's", {}, "built-in"),
    ("db_name", "postgres_app", None, {}),
    ("db_role_prefix", "nai", None, {}),
    ("db_role_prefix", "Nai", "roles are created lowercase and the uri names them unquoted", {}),
    ("db_role_prefix", "pg_nai", "PostgreSQL reserves role names starting with pg_", {}, "pg_"),
    ("db_role_prefix", "nai_pg", None, {}),
    ("db_storage_size", "20Gi", None, {}),
    ("db_storage_size", "20G", "a decimal G size is not the Gi quantity the storage quota counts", {}),
    ("db_backup_endpoint", "https://s3.us-west-004.backblazeb2.com", None, {}),
    ("db_backup_endpoint", "s3.us-west-004.backblazeb2.com", "no scheme, so Barman reads a bucket path", {}),
    ("db_backup_endpoint", "https://s3.us-west-004.backblazeb2.com:443", None, {}),
    ("db_backup_endpoint", "https://store.example:9000", "db-allow-egress opens only port 443", {}),
    ("db_backup_endpoint", "https://store.example/bucket", "a path is not part of an S3 endpoint", {}),
    ("db_backup_endpoint", "https://minio.storage.svc", "db-allow-egress opens 443 to public addresses only", {}, "public object store"),
    ("db_backup_endpoint", "https://minio.storage.svc.cluster.local", "db-allow-egress opens 443 to public addresses only", {}, "public object store"),
    ("db_backup_endpoint", "https://nas.local", "db-allow-egress opens 443 to public addresses only", {}, "public object store"),
    ("db_backup_endpoint", "https://s3.corp.internal", "db-allow-egress opens 443 to public addresses only", {}, "public object store"),
    ("db_backup_endpoint", "https://localhost", "db-allow-egress opens 443 to public addresses only", {}, "public object store"),
    ("db_backup_endpoint", "https://minio.localhost", "db-allow-egress opens 443 to public addresses only", {}, "public object store"),
    ("db_backup_endpoint", "https://10.0.10.5", "db-allow-egress opens 443 to public addresses only", {}, "public object store"),
    ("db_backup_endpoint", "https://203.0.113.7:443", "db-allow-egress opens 443 to public addresses only", {}, "public object store"),
    ("db_backup_endpoint", "https://s3.eu-central-003.backblazeb2.com", None, {}),
    ("db_apiserver_cidrs", "192.168.1.222/32,192.168.1.223/32", None, {}),
    ("db_apiserver_cidrs", "192.168.1.222", "a bare address is not a CIDR the ipBlock accepts", {}),
    (
        "db_apiserver_cidrs",
        "192.168.1.222/32,999.999.999.999/32",
        "an octet over 255 fails NetworkPolicy admission",
        {},
    ),
    (
        "db_apiserver_cidrs",
        "192.168.1.222/32,10.0.0.0/8",
        "a /8 opens 6443 egress to a whole network, not the API server nodes",
        {},
    ),
    ("db_apiserver_cidrs", "192.168.1.222/99", "a prefix over 32 is no IPv4 CIDR", {}),
    ("db_backup_item", "CNPG Backup", None, {}),
    ("db_backup_item", 'Backup "Prod"', "a double quote closes the quoted remoteRef key", {}),
    ("db_backup_item", "Backup\\Prod", "a backslash starts an escape in the quoted key", {}),
    ("db_backup_item", " CNPG Backup", "1Password matches the title exactly, padding included", {}),
    ("db_roles_item", "Database", None, {}),
    ("db_roles_item", 'Database "Production"', "a double quote closes the quoted remoteRef key", {}),
    ("db_roles_item", "Data\tbase", "a control character is no title anyone typed", {}),
    ("db_roles_item", "Database ", "1Password matches the title exactly, padding included", {}),
    ("db_backup_bucket", "weisssrv-cnpg-recipe-box", None, {}),
    ("db_backup_bucket", "Recipe_Box", "renders unquoted into s3://<bucket>/, and S3 names are lowercase", {}),
    ("db_backup_bucket", "s3://box-cnpg", "a scheme is not a bucket name", {}),
    ("db_dump_timezone", "America/Los_Angeles", None, {}),
    ("db_dump_timezone", "Etc/GMT+8", None, {}),
    ("db_dump_timezone", "Pacific Time", "renders unquoted into timeZone:, and the CronJob is refused at apply", {}),
    (
        "db_runbook_url",
        "https://git.example.com/homelab/cluster/-/blob/main/docs/postgres.md#postgres-down",
        None,
        {},
    ),
    ("db_runbook_url", "git.example.com/docs", "no scheme, so the runbooks page cannot link it", {}),
    (
        "db_runbook_url",
        "https://git.example.com/it's",
        "a single quote closes the quoted YAML scalar DatabaseAbsent renders it into",
        {},
    ),
]


@pytest.mark.parametrize(
    "case",
    VALIDATOR_CASES,
    ids=[f"{case[0]}-{case[1]}" for case in VALIDATOR_CASES],
)
def test_validator_accepts_and_rejects(case):
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


def test_every_validator_is_exercised():
    """Every validator declared in copier.yml has a test case."""
    declared = {
        name
        for name, question in QUESTIONS.items()
        if isinstance(question, dict) and "validator" in question
    }
    covered = {case[0] for case in VALIDATOR_CASES}
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


def test_validate_rendered_app_clones_the_library_at_the_pipeline_pin():
    """The rendered-app gates read the release the includes pin, not a second
    derivation of it."""
    ci = render_app.load_ci(REPO_ROOT / ".gitlab-ci.yml")
    install = "\n".join(ci["validate-rendered-app"]["before_script"])
    assert '--branch "$WEISSSRV_LIB_REF"' in install, (
        "validate-rendered-app does not clone the library at variables.WEISSSRV_LIB_REF"
    )
    assert "copier.yml" not in install, (
        "validate-rendered-app re-derives the library ref from copier.yml"
    )


def test_no_include_or_job_names_a_runner_tag():
    """Every library include this pipeline uses defaults `tags` to `[]`, so this
    repo's own jobs run on the shared tag-less runner and a tag here only moves
    one off it."""
    includes, jobs = render_app.runner_tagged(render_app.load_ci(REPO_ROOT / ".gitlab-ci.yml"))
    assert not includes, f"library includes pass runner tags: {includes}"
    assert not jobs, f"jobs set runner tags: {jobs}"


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


@pytest.mark.parametrize(
    "dropped,data,expected",
    [
        (None, ["--data", "app_slug=Not A Label"], None),
        # The build never falls through to a runner the tenant did not name.
        ("privileged_runner_tag", [], 'Question "privileged_runner_tag" is required'),
    ],
    ids=["bad-app-slug", "no-build-runner-tag"],
)
def test_copier_rejects_an_invalid_answer(tmp_path, dropped, data, expected):
    """End-to-end proof that a validator is wired, not just well-written: a
    copier run with a bad answer must exit non-zero and write nothing."""
    src = render_app.copy_source(tmp_path)
    answers = render_app.answers_without(tmp_path, dropped) if dropped else render_app.ANSWERS
    result = subprocess.run(
        [
            sys.executable, "-m", "copier", "copy", "--defaults", "--trust",
            "--data-file", str(answers), *data,
            str(src), str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, "an invalid answer was accepted"
    output = result.stdout + result.stderr
    assert expected is None or expected in output, "rejected for another reason:\n" + output
    assert not (tmp_path / "out" / "kubernetes").exists(), "a rejected render wrote manifests"


@pytest.mark.parametrize(
    "data",
    [
        {"ci_shape": "github", "forge": "github"},
        {"enable_image_build": "false"},
    ],
    ids=["github-shape", "gitlab-no-build"],
)
def test_a_render_that_never_asks_the_runner_tag_needs_no_answer(tmp_path, data):
    """Every file naming privileged_runner_tag sits behind the question's own
    `when:`, so a render that skips the question renders without an answer."""
    dest = render_app.render(
        tmp_path, answers=render_app.answers_without(tmp_path, "privileged_runner_tag"), data=data
    )
    assert (dest / "kubernetes").is_dir()


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


def test_docs_quote_no_library_tag():
    """The library tag has one owner, copier.yml, which the pin gate holds to
    the pipeline header. A tag quoted in a doc is a copy nothing keeps current."""
    quoted = [
        f"{path.relative_to(REPO_ROOT)}:{lineno} {tag}"
        for path in [REPO_ROOT / "README.md", *sorted((REPO_ROOT / "docs").glob("*.md"))]
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        for tag in _TAG_LITERAL.findall(line)
    ]
    assert not quoted, "docs quote a library tag; name copier.yml's default instead:\n  " + "\n  ".join(quoted)


# --------------------------------------------------------------------------
# The vendored-copy manifest
# --------------------------------------------------------------------------

MANIFEST = REPO_ROOT / "scripts" / "vendored-manifest.yml"


def _missing_consumers(document: dict, root) -> list[str]:
    paths = render_app.consumer_paths(document=document)
    return [path for path in paths if not (root / path).exists()]


def test_every_vendored_consumer_path_exists():
    """A conditional path renamed in template/ leaves the manifest naming a file
    that is gone, and the copy it registered is then gated by nothing."""
    document = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    paths = render_app.consumer_paths(document=document)
    assert len(paths) >= 20, f"the manifest registers only {len(paths)} copies"
    assert not _missing_consumers(document, REPO_ROOT), (
        "vendored-manifest.yml names paths that do not exist: "
        f"{_missing_consumers(document, REPO_ROOT)}"
    )


# The scrape gate refuses a ServiceMonitor's `matchNames` without `--namespace`,
# so a template-owned caller that omits the answer reds on a tenant's own
# manifest. The vendored workflow takes no copier answer and derives it instead.
SCRAPE_INVOCATION = re.compile(r"python3?\s+scripts/check-scrape-wiring\.py")


def test_every_template_owned_scrape_caller_passes_the_namespace():
    """The task, the pre-commit hook and the GitLab pipeline run the same gate,
    and the answer is the only thing that tells it which namespace this tree
    deploys into."""
    document = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    # Only the byte-identical copies: a declared fork is edited here.
    vendored = {
        entry if isinstance(entry, str) else entry["consumer"]
        for entry in document.get("vendored") or []
    }
    callers = {}
    for path in sorted((REPO_ROOT / "template").rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        relpath = path.relative_to(REPO_ROOT).as_posix()
        if relpath in vendored:
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if SCRAPE_INVOCATION.search(body):
            callers[relpath] = body
    assert len(callers) >= 3, f"the scan found only {sorted(callers)}"
    missing = sorted(
        relpath
        for relpath, body in callers.items()
        if "--namespace {{ app_namespace }}" not in body
    )
    assert not missing, "scrape-gate callers passing no namespace: " + ", ".join(missing)


# Gates this template would own outright. Every gate it ships is a registered
# library copy, so the set is empty; the negative test below holds it honest.
TEMPLATE_OWNED: set[str] = set()


def _script_paths(root) -> set[str]:
    """Every gate under scripts/ and template/scripts/, repo-relative."""
    return {
        path.relative_to(root).as_posix()
        for directory in ("scripts", "template/scripts")
        for path in (root / directory).iterdir()
        # The conditional copies carry `{% endif %}` after the suffix.
        if path.is_file() and (".py" in path.name or ".sh" in path.name)
    }


def _unclassified(document: dict, found: set[str]) -> list[str]:
    return sorted(found - set(render_app.consumer_paths(document=document)) - TEMPLATE_OWNED)


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


# The platform namespaces of a weisssrv-shaped cluster, which the app_namespace
# validator refuses.
PLATFORM_NAMESPACES = (
    "default",
    "kube-system",
    "kube-public",
    "kube-node-lease",
    "flux-system",
    "traefik",
    "observability",
    "authentik",
    "external-secrets",
    "cert-manager",
    "metallb-system",
    "external-dns",
    "cnpg-system",
    "local-path-storage",
    "tailscale",
    "vpa-system",
    "reloader",
    "nvidia-device-plugin",
    "prometheus-operator-crds",
    "cloudflare-ddns",
    "gitlab-runner",
    "gitlab-runner-privileged",
    "gitlab-runner-reaper",
    "registry-cache",
    "vm-ingress",
    "tailnet-dns",
)


@pytest.mark.parametrize("namespace", PLATFORM_NAMESPACES)
def test_app_namespace_refuses_every_platform_namespace(namespace):
    """The wiring relabels its namespace and binds the tenant ServiceAccount to
    admin there, so a platform namespace answered here hands a tenant the
    platform's own workloads."""
    assert "platform namespace" in _validator_message("app_namespace", app_namespace=namespace)


@pytest.mark.parametrize(
    "namespace",
    ["reloader-notes", "my-traefik", "tailscale-dash", "my-kube-tools", "docs-gitlab-runner"],
)
def test_app_namespace_matches_platform_names_exactly(namespace):
    """Only the exact names and the two prefixes are refused, so a tenant name
    that merely contains a platform word stays available."""
    assert not _validator_message("app_namespace", app_namespace=namespace)
