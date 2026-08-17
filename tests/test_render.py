"""Render the template and assert what must be true of EVERY generated repo.

Four families:

* the render happens at all, in every CI shape, with the answers recorded for
  `copier update`;
* nothing is left unrendered, and no answer from one fixture survives into a
  render from the other (which is what separates "substituted" from "copied");
* the file set matches the answers — an enabled component is wired into the
  kustomization, a disabled one leaves nothing behind;
* the generated repo passes its own gates.

Rendering is session-scoped, so a second parameter costs assertions, not
renders.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

import render_app

REPO_ROOT = render_app.REPO_ROOT
FLUX = "kubernetes/flux"

# A DENYLIST, not an allowlist: the files a render is most likely to carry an
# unsubstituted or hardcoded value in are the extensionless ones (Dockerfile,
# CODEOWNERS), and every suffix allowlist ever written has missed them. Anything
# read_text() decodes is scanned; the decode error is the filter.
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".pyc"}

# The RFC-reserved and private IPv4 space the public-egress rule must except.
# Declared once here and asserted against the render: it is the same set the
# cluster repo's netpol-egress-public component carries and its parity gate
# enforces, and that gate cannot reach a tenant repo.
RESERVED_FULL = [
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "0.0.0.0/8",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "100.64.0.0/10",
    "192.0.0.0/24",
    "192.0.2.0/24",
    "198.51.100.0/24",
    "203.0.113.0/24",
    "198.18.0.0/15",
    "224.0.0.0/4",
    "240.0.0.0/4",
]


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
    """A second render from deliberately unlike answers, every optional
    component off. It is what makes a hardcoded value visible: under the shaped
    fixture a literal carried over from the reference cluster renders
    identically to a correct substitution."""
    return render_app.render(
        tmp_path_factory.mktemp("render-b"),
        answers=render_app.ANSWERS_B,
        dest_name="render-b",
    )


@pytest.fixture(scope="session")
def rendered_none(tmp_path_factory) -> Path:
    """The third CI shape. It is fixture A with two answers overridden rather
    than a third fixture: what it covers is the absence of a pipeline, and
    everything else being equal to A is what makes that visible."""
    return render_app.render(
        tmp_path_factory.mktemp("render-none"),
        dest_name="render-none",
        data={"ci_shape": "none"},
    )


@pytest.fixture(scope="session")
def rendered_no_secrets(tmp_path_factory) -> Path:
    """The third secret backend, which no fixture answers. It renders the
    ABSENCE of the whole secret surface — the ExternalSecret, the Deployment's
    secret env block, the operator's ClusterSecretStore — and every one of those
    is a branch nothing else in the suite reaches.

    `enable_registry_pull_secret` is overridden with it because the pull
    credential is itself an ExternalSecret: copier SKIPS that question under
    `none`, but a value supplied in a data file is still used as the answer, so
    a fixture-driven render has to say what an interactive tenant would have
    been given.
    """
    return render_app.render(
        tmp_path_factory.mktemp("render-no-secrets"),
        dest_name="render-no-secrets",
        data={"secrets_backend": "none", "enable_registry_pull_secret": "false"},
    )


# The library project the gitlab-unlike render answers. It must differ from BOTH
# fixtures: `eric/weisssrv-lib` is copier's default AND the answer in both files,
# so an `include: project:` written as a literal renders correctly everywhere
# else in this suite. It is also the one answer fixture B is allowed to share
# with fixture A (test_the_two_fixtures_answer_differently), which is exactly
# what leaves it unproven without this render.
ALT_LIB_PROJECT = "seaworks/brinemoor-lib"

# Fixture B, moved onto the GitLab shape with the image build on. Overriding
# rather than adding a third answers FILE is the idiom the other derived renders
# use, and it is what keeps every other value unlike fixture A's for free.
GITLAB_UNLIKE_OVERRIDES = {
    "ci_shape": "gitlab_selfhosted",
    "enable_image_build": "true",
    "lib_project": ALT_LIB_PROJECT,
}


@pytest.fixture(scope="session")
def rendered_gitlab_unlike(tmp_path_factory, answers, answers_b) -> Path:
    """The GitLab pipeline, rendered from answers unlike the reference cluster's.

    The anti-hardcode scan runs against `rendered_b`, which is GitHub-shaped and
    therefore ships no `.gitlab-ci.yml` at all — so every reference-cluster
    literal in `.gitlab-ci.yml.jinja` (the runner tag, the k8s version, the CPU
    selector, the library project) survives that scan untouched. This is the
    render in which the pipeline exists AND none of its answers are fixture A's.
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
def rendered_image_github(tmp_path_factory) -> Path:
    """The contrast fixture with the image build ON. `github` +
    `enable_image_build: true` is the combination neither fixture answers, and
    it is the one the build-image workflow's conditional path is gated on — so
    without this render, the branch that SHIPS that workflow goes unrendered
    while the branch that drops it is covered."""
    return render_app.render(
        tmp_path_factory.mktemp("render-image"),
        answers=render_app.ANSWERS_B,
        dest_name="render-image",
        data={"enable_image_build": "true"},
    )


# The vault the alt-vault render answers. It must differ from BOTH fixtures'
# answers: it is the only evidence that the rendered wiring came from the answer
# rather than from a literal, so a value one of them also answers would make the
# assertion pass on a hardcoded string.
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
# overrides are what keeps the assertions and the tree talking about the same
# answer set.
RENDERS = {
    "shaped": ("rendered", "answers", {}),
    "unlike": ("rendered_b", "answers_b", {}),
    "none-shape": ("rendered_none", "answers", {"ci_shape": "none"}),
    "no-secrets": (
        "rendered_no_secrets",
        "answers",
        {"secrets_backend": "none", "enable_registry_pull_secret": False},
    ),
    "github-image": ("rendered_image_github", "answers_b", {"enable_image_build": True}),
    # The GitLab shape from unlike answers. Copier takes `--data` as strings,
    # so the booleans and the computed `change_request` are restated here as the
    # values the render actually resolved.
    "gitlab-unlike": (
        "rendered_gitlab_unlike",
        "answers_b",
        {
            **GITLAB_UNLIKE_OVERRIDES,
            "enable_image_build": True,
            "change_request": "merge request",
        },
    ),
}


@pytest.fixture(scope="session", params=list(RENDERS))
def repo(request) -> Repo:
    render_fixture, answers_fixture, overrides = RENDERS[request.param]
    answers = {**request.getfixturevalue(answers_fixture), **overrides}
    return Repo(request.param, request.getfixturevalue(render_fixture), answers)


def _text_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix in BINARY_SUFFIXES:
            continue
        try:
            yield path, path.read_text()
        except (UnicodeDecodeError, OSError):
            continue


def _kustomization(root: Path) -> dict:
    return yaml.safe_load((root / FLUX / "kustomization.yaml").read_text())


def _docs(root: Path) -> list[dict]:
    docs: list[dict] = []
    for path in sorted((root / FLUX).glob("*.yaml")):
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
    """A `{% ... %}` block in the output means a templated file was not given
    the .jinja suffix; a `{{ <question> }}` is an answer that reached the output
    unsubstituted — most often written inside a `{% raw %}` block that another
    templating language (go-task, Helm, Prometheus) needed."""
    names = "|".join(sorted(repo.answers))
    leak = re.compile(r"\{\{-?\s*(" + names + r")\s*[|}-]")
    offenders = []
    for path, text in _text_files(repo.path):
        # The vendored library scripts are Python, not template sources.
        if path.relative_to(repo.path).parts[0] == "scripts":
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if "{%" in line or leak.search(line):
                offenders.append(f"{path.relative_to(repo.path)}:{lineno} {line.strip()[:70]}")
    assert not offenders, "unrendered Jinja survived the render:\n  " + "\n  ".join(offenders)


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
    "change_request": (
        "the vendored GitHub workflows narrate what the GitLab job does and "
        "legitimately say 'merge request' — test_forge_vocabulary_matches_the_shape "
        "is the targeted gate, and it skips the vendored copies"
    ),
}


# A line naming one of the family's own repositories legitimately carries their
# path wherever the generated repo lives: they are upstreams, not site identity.
UPSTREAM_REPOS = ("weisssrv-lib", "weisssrv-app-template", "weisssrv-cluster-template")


def test_render_b_carries_no_fixture_a_values(rendered_b, answers, answers_b):
    """No answer from fixture A may appear anywhere in a render from fixture B.

    A hardcoded literal is invisible in the shaped render, because its value
    happens to equal the correct one. Rendering a second, unlike answer set and
    diffing against the first is what separates 'substituted' from 'copied'.

    Matched on word boundaries: a short answer is otherwise a substring of
    ordinary English (`eric` inside `generic`), and a scan that cries wolf gets
    exempted into uselessness.
    """
    leaks = []
    for key, value in answers.items():
        if key in CROSS_RENDER_EXEMPT or not isinstance(value, str):
            continue
        if value == answers_b.get(key) or len(value) < 4:
            continue
        needle = re.compile(rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])")
        for path, text in _text_files(rendered_b):
            if path.relative_to(rendered_b).parts[0] == "scripts":
                continue  # vendored library helpers, not rendered output
            for lineno, line in enumerate(text.splitlines(), 1):
                # copier's own bookkeeping keys (_src_path, _commit) record where
                # the render came from, which under pytest is a scratch path
                # containing the caller's username.
                if path.name == ".copier-answers.yml" and line.startswith("_"):
                    continue
                if needle.search(line) and not any(repo in line for repo in UPSTREAM_REPOS):
                    leaks.append(f"{path.relative_to(rendered_b)}:{lineno} {key}={value}")
    assert not leaks, (
        "fixture A's answers appear in a render from fixture B — those values "
        "are hardcoded, not substituted:\n  " + "\n  ".join(sorted(set(leaks)))
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


# Answers the gitlab-unlike render CANNOT answer differently, so their agreement
# with fixture A is not evidence of anything. `change_request` is computed from
# ci_shape (`when: false`), and this render is on the GitLab shape by
# construction; `ci_shape` is the override itself.
PIPELINE_UNPROVABLE = ("ci_shape", "change_request")


def test_the_pipeline_carries_no_fixture_a_values(
    rendered_gitlab_unlike, answers, answers_b
):
    """The anti-hardcode gate, applied where it was blind.

    `test_render_b_carries_no_fixture_a_values` cannot see `.gitlab-ci.yml.jinja`:
    fixture B is GitHub-shaped and the conversion drops the pipeline entirely, so
    a reference-cluster literal written into it — `eric/weisssrv-lib`,
    `esweiss.com/cpu=modern`, the k8s version, the runner tag — renders no output
    for that scan to catch, and every OTHER render answers those values exactly
    as fixture A does. Here the pipeline exists and every answer is unlike.

    Note what this render also un-exempts: `privileged_runner_tag` and
    `k8s_version` are in CROSS_RENDER_EXEMPT because of collisions elsewhere in
    the tree (a Flux Kustomization named `infrastructure`, the vendored GitHub
    workflows). Neither collision exists inside the pipeline, so both are held
    to the full standard here — which is the coverage those exemptions gave up.
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


# Byte-identical library copies. They cannot take an answer by construction, and
# the GitHub workflows deliberately narrate the GitLab job's behaviour — so
# "merge request" there is a description of another forge, not an instruction.
VENDORED_ROOTS = ("scripts", ".github")


def test_forge_vocabulary_matches_the_shape(rendered_b):
    """A GitHub tenant has no merge requests.

    The conversion gates `.gitlab/`'s templates out of the GitHub shape, but the
    prose is the part a reader follows: a standing rule in CLAUDE.md, or the
    skill an agent is told to read first, naming an object the forge does not
    have is an instruction that cannot be carried out.
    """
    offenders = []
    for path, text in _text_files(rendered_b):
        relative = path.relative_to(rendered_b)
        if relative.parts[0] in VENDORED_ROOTS:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if "merge request" in line.lower():
                offenders.append(f"{relative}:{lineno} {line.strip()[:70]}")
    assert not offenders, (
        "GitLab vocabulary in a GitHub-shape render — substitute "
        "{{ change_request }}:\n  " + "\n  ".join(offenders)
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
    on_disk = {p.name for p in (repo.path / FLUX).glob("*.yaml")} - {"kustomization.yaml"}
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
        vpa = yaml.safe_load((repo.path / FLUX / "vpa.yaml").read_text())
        controlled = vpa["spec"]["resourcePolicy"]["containerPolicies"][0]["controlledResources"]
        assert controlled == ["memory"], "the VPA must not drive CPU while an HPA does"
    else:
        assert deployment["spec"]["replicas"] == repo.answers["replica_count"]


def test_scrape_policy_ships_with_the_servicemonitor(repo):
    """The ServiceMonitor without the NetworkPolicy scrapes into a default-deny
    namespace and reports `up == 0`; the policy without the ServiceMonitor opens
    a port nothing uses."""
    policies = [d["metadata"]["name"] for d in _docs(repo.path) if d["kind"] == "NetworkPolicy"]
    assert ("allow-scrape-from-observability" in policies) is repo.answers[
        "enable_servicemonitor"
    ]


def test_public_egress_excepts_the_whole_reserved_set(repo):
    """The except list is a standards constant, and the platform namespaces use
    the same one. A short list is not a syntax error — it silently lets the app
    reach loopback, cloud-metadata or the LAN, and no render gate but this one
    would notice."""
    policies = {d["metadata"]["name"]: d for d in _docs(repo.path) if d["kind"] == "NetworkPolicy"}
    egress = policies["allow-egress-public"]["spec"]["egress"]
    public = [
        entry["ipBlock"]
        for rule in egress
        for entry in rule.get("to", [])
        if "ipBlock" in entry and entry["ipBlock"]["cidr"] == "0.0.0.0/0"
    ]
    assert len(public) == 1, "exactly one rule may open 0.0.0.0/0"
    assert sorted(public[0]["except"]) == sorted(RESERVED_FULL)


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
    """Tenant PrometheusRules are evaluated cluster-wide, so an unscoped
    selector fires on any namespace's like-named Deployment — and an unscoped
    `absent()` returns nothing the moment one exists, silently disarming the
    alert it was written for."""
    rule = yaml.safe_load((repo.path / FLUX / "prometheusrule.yaml").read_text())
    namespace = repo.answers["app_namespace"]
    for group in rule["spec"]["groups"]:
        for alert in group["rules"]:
            assert f'namespace="{namespace}"' in alert["expr"], (
                f"{alert['alert']} does not scope its selector to the namespace"
            )
            assert alert["annotations"]["runbook_url"] == repo.answers["runbook_url"]


def test_routes_and_certificates_pair_up(repo):
    """Each IngressRoute must name a TLS Secret some Certificate issues; a route
    whose Secret never exists serves the platform's default certificate."""
    docs = _docs(repo.path)
    issued = {d["spec"]["secretName"] for d in docs if d["kind"] == "Certificate"}
    used = {d["spec"]["tls"]["secretName"] for d in docs if d["kind"] == "IngressRoute"}
    assert used <= issued, f"routes reference unissued TLS secrets: {sorted(used - issued)}"
    assert issued == used, f"certificates nothing uses: {sorted(issued - used)}"


def test_hostnames_come_from_the_answers(repo):
    slug, external = repo.answers["app_slug"], repo.answers["external_domain"]
    route = yaml.safe_load((repo.path / FLUX / "ingressroute.yaml").read_text())
    assert f"Host(`{slug}.{external}`)" in route["spec"]["routes"][0]["match"]
    assert route["metadata"]["annotations"]["external-dns.alpha.kubernetes.io/target"] == external


def test_sso_middleware_matches_the_answer(repo):
    route = yaml.safe_load((repo.path / FLUX / "ingressroute.yaml").read_text())
    middlewares = [m["name"] for m in route["spec"]["routes"][0]["middlewares"]]
    assert ("authentik-auth" in middlewares) is repo.answers["enable_sso"]


def test_image_names_the_answered_registry(repo):
    deployment = yaml.safe_load((repo.path / FLUX / "deployment.yaml").read_text())
    image = deployment["spec"]["template"]["spec"]["containers"][0]["image"]
    expected = (
        f"{repo.answers['registry_host']}/{repo.answers['git_namespace']}/"
        f"{repo.answers['app_slug']}"
    )
    assert image.startswith(expected + ":"), image


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

    They live in a fence, so no `kustomize build` or kubeconform run in this
    repo ever parses them — the one file the operator applies by hand is the one
    nothing validates unless a test does.
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


def test_gitlab_wiring_store_names_the_answered_instance(rendered_b, answers_b):
    """Two ESO-GitLab-provider details a paste-ready file cannot get wrong.

    `url` is optional and DEFAULTS to https://gitlab.com, so omitting it sends a
    self-hosted token to the wrong instance; and this provider's auth field is
    the capitalised `SecretRef`, so a lower-cased key is pruned as unknown and
    the store is left with no credential at all. Both failures surface as an
    ExternalSecret that never syncs, pointing nowhere near this file.

    The url comes from `gitlab_api_url`, NOT `git_host`: the CI/CD variables
    need not live on the forge the code does, and under `ci_shape: github`
    git_host is github.com, which is not a GitLab instance at all. The fixture
    answers the two differently so a reintroduced git_host would fail here.
    """
    provider = _onboarding_store(rendered_b)["spec"]["provider"]["gitlab"]
    assert provider["url"] == answers_b["gitlab_api_url"]
    assert answers_b["gitlab_api_url"] != f"https://{answers_b['git_host']}", (
        "the fixture must answer the two differently, or this gate proves nothing"
    )
    assert "SecretRef" in provider["auth"], f"auth keys: {sorted(provider['auth'])}"


def test_onepassword_wiring_store_names_the_answered_vault(rendered_alt_vault):
    """The contrast fixture answers `secrets_backend: gitlab`, so the 1Password
    branch renders only under the shaped fixture — where the reference cluster's
    vault name is indistinguishable from a correct substitution. This render is
    the shaped fixture with that one answer changed."""
    provider = _onboarding_store(rendered_alt_vault)["spec"]["provider"]["onepassword"]
    assert provider["vaults"] == {ALT_VAULT: 1}
    assert "Homelab" not in (rendered_alt_vault / "docs" / "ONBOARDING.md").read_text()


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
        [".github/workflows/ci.yml"],
        [".gitlab-ci.yml", ".gitlab/secret-detection-ruleset.toml", "scripts/check-lib-pins.py"],
    ),
    "none": (
        # GitLab issue/merge-request templates are forge metadata, not CI, so a
        # pipeline-less repo on GitLab keeps them.
        [".gitlab/issue_templates/Bug.md"],
        [".gitlab-ci.yml", ".github/workflows/ci.yml", "scripts/semantic-release.py"],
    ),
}


def test_ci_shape_keeps_exactly_its_own_files(repo):
    present, absent = SHAPE_FILES[repo.answers["ci_shape"]]
    for relpath in present:
        assert (repo.path / relpath).is_file(), f"{relpath} missing from shape {repo.label}"
    for relpath in absent:
        assert not (repo.path / relpath).exists(), f"{relpath} survived into shape {repo.label}"


def test_the_image_build_answer_governs_both_shapes(repo):
    """`enable_image_build: false` must mean the same thing on either forge.
    The GitLab shape gates its build job on the answer; the GitHub shape gates
    the whole workflow file, because a workflow present but inert still fires on
    every push to main holding `packages: write`."""
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
    """`ci_shape: none` with `enable_image_build: true` is reachable and close to
    the default answer set. Every prose site that describes where the image tag
    comes from is gated on the build answer, so without this gate they all tell
    the tenant a pipeline they do not have pushes their image."""
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
    for path in sorted((rendered / FLUX).glob("*.yaml")):
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


def test_secret_detection_carries_the_cpu_selector(rendered, answers):
    """gitleaks is pinned to a modern-CPU node by a selector the LIBRARY
    defaults to its own cluster's label domain. Unoverridden, the scan is
    unschedulable here and sits Pending until the job times out — and unlike the
    image build, this job exists in every gitlab_selfhosted repo."""
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


def test_pipeline_lints_only_paths_that_exist(rendered):
    """ruff exits non-zero on a target path that does not exist, and pytest
    exits 4 on a missing test directory — a generated repo must never ship a job
    pointed at a directory the render did not produce."""
    ci = render_app.load_ci(rendered / ".gitlab-ci.yml")
    checked = []
    for include in ci["include"]:
        if not isinstance(include, dict):
            continue
        targets = (include.get("inputs") or {}).get("targets")
        if not targets or targets == ".":
            continue
        for target in str(targets).split():
            assert (rendered / target).exists(), f"{include['file']} lints missing {target}"
            checked.append(target)
    assert checked, "no include declares a lint target — this gate examined nothing"


# Paths the GitHub workflow hands to a tool as a bare argument, each of which
# aborts the job when it does not exist. The workflow is vendored
# byte-identically from the library and so cannot take a copier answer — which
# is exactly why these need checking against a real render rather than against
# the library's idea of a tenant's layout.
#
# Only unguarded arguments belong here: the shellcheck loop and the Dockerfile
# build test for their targets first and skip, which is a shape answer being
# honoured, not a broken path.
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


# --------------------------------------------------------------------------
# The generated repo passes its own gates
# --------------------------------------------------------------------------


def _run(repo_path: Path, *command: str) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=repo_path, capture_output=True, text=True)


def test_generated_repo_passes_yamllint(repo):
    yamllint = shutil.which("yamllint")
    if not yamllint:
        pytest.skip("yamllint not installed")
    result = _run(repo.path, yamllint, "--strict", "-c", ".yamllint", ".")
    assert result.returncode == 0, result.stdout + result.stderr


def test_generated_repo_passes_its_doc_link_check(repo, tmp_path):
    """Run it the way the tenant's own pipeline does: over TRACKED Markdown.

    A render is not a git checkout, so the checker would otherwise take its
    non-git fallback (docs/ + README.md + CLAUDE.md) and never open AGENTS.md or
    the forge templates — the files whose broken link would ship green from here
    and fail on the tenant's first pipeline. The copy is so the shared render
    keeps no .git of its own.
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
    kustomize = shutil.which("kustomize")
    if not kustomize:
        pytest.skip("kustomize not installed")
    result = _run(repo.path, kustomize, "build", FLUX)
    assert result.returncode == 0, result.stderr
    kinds = [d["kind"] for d in yaml.safe_load_all(result.stdout) if d]
    assert "Deployment" in kinds and "Service" in kinds


def test_generated_manifests_validate(repo):
    kustomize, kubeconform = shutil.which("kustomize"), shutil.which("kubeconform")
    if not (kustomize and kubeconform):
        pytest.skip("kustomize/kubeconform not installed")
    if not os.environ.get("WEISSSRV_SCHEMA_NETWORK"):
        pytest.skip("set WEISSSRV_SCHEMA_NETWORK=1 to fetch CRD schemas")
    built = _run(repo.path, kustomize, "build", FLUX)
    catalog = (
        "https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/"
        "{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json"
    )
    result = subprocess.run(
        [
            kubeconform, "-strict", "-ignore-missing-schemas",
            "-kubernetes-version", repo.answers["k8s_version"],
            "-schema-location", "default", "-schema-location", catalog, "-summary",
        ],
        input=built.stdout,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_forced_pull_secret_under_no_backend_renders_nothing(tmp_path_factory):
    """Copier's `when:` gates only the prompt: `--data` can smuggle
    enable_registry_pull_secret=true past secrets_backend=none. Every render
    site is backend-guarded too, so the forced value must produce no
    ExternalSecret, no kustomization entry, and no imagePullSecrets."""
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
    """The ON half of the same default, and the half that carries the risk.

    Its sibling below proves the backend term. This proves the default RESOLVES
    at all: a tenant who builds their own image is answering a question whose
    default is computed, never typed, and a default that quietly resolved false
    would ship a Deployment that cannot pull its own image — `ImagePullBackOff`
    on first reconcile, with nothing in the render to point at.

    BOTH shapes, because the default carries no `ci_shape` term and must not
    grow one: a GHCR package is private by default too (its visibility does not
    follow the source repo's), and nothing in the rendered chain is
    forge-shaped — the ExternalSecret keys on the two registry hostnames and
    reads a username+token pair, which is what `docker login ghcr.io` takes.
    A forge term here would default the credential off for exactly the GitHub
    tenant who needs it.

    So the answer is REMOVED rather than set: a data file answers it either way,
    and only its absence leaves the default to decide.
    """
    scratch = tmp_path_factory.mktemp(f"render-default-pull-on-{shape}")
    answers = yaml.safe_load(fixture.read_text())
    del answers["enable_registry_pull_secret"]
    # The default's two terms, both present, so what is under test is the
    # default and not one of them being absent. Fixture B answers
    # enable_image_build false (it covers the no-build paths), so this arm
    # turns it on — the shape is what the parametrization varies.
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

    deployment = yaml.safe_load((flux / "deployment.yaml").read_text())
    pull_secrets = [
        s["name"] for s in deployment["spec"]["template"]["spec"]["imagePullSecrets"]
    ]
    assert pull_secrets == [external_secret["spec"]["target"]["name"]], (
        "the Deployment must name the Secret this ExternalSecret renders — a "
        "credential the pod never references is a Secret that syncs and does "
        "nothing"
    )


def test_unasked_pull_secret_defaults_off_without_a_backend(tmp_path_factory):
    """The interactive `none` shape, which no fixture can reach: copier skips
    the question and computes its DEFAULT, and a data file — every fixture here
    is one — answers it either way. So the answer is removed rather than
    overridden, leaving the default to decide.

    It must decide `false`. On `true` the skill's guidance for getting a pull
    credential (gated on the answer being off) disappears, while every render
    site stays backend-guarded — the tenant is told nothing and gets nothing."""
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
    skill = (repo / ".claude/skills/project-development/SKILL.md").read_text()
    assert "That credential is an `ExternalSecret`, so it needs a `ClusterSecretStore`" in skill
