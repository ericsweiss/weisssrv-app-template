# Consuming the template & the shared library

This template deploys a service to the weisssrv k3s cluster, and its **CI comes
from a shared library** ([`eric/weisssrv-lib`](https://git.ericsweiss.com/eric/weisssrv-lib))
rather than being hand-rolled per project. This page covers the two ways to
create a project, how the library is consumed and bumped, which jobs that gets
you, the optional components you can toggle on/off, the image-build story, and
the keys you bring.

> **This page describes CI shape `gitlab`** — the default, and the only shape
> that consumes the shared library. The template also ships a GitHub Actions
> shape and a no-pipeline shape; pick one with `./scripts/select-ci.sh
> <gitlab|github|none>` and read [CI-SHAPES.md](CI-SHAPES.md) for the parity
> table and the trade-offs. Everything *below the CI line* — the toggles, the
> secret backends, the manifests — is identical in all three shapes, because
> Flux is the deployer in all three.

For how the running app rides the platform, see [ARCHITECTURE.md](ARCHITECTURE.md);
for the operator/tenant wiring checklists, see [ONBOARDING.md](ONBOARDING.md).

---

## Two ways to create a project

Both start from a copy of this template and end with the `changeme-app` /
`changeme-group` placeholders replaced.

### 1. Plain create-from-template / fork

Fork (or *New project → Create from template*, if the operator registered this
as a custom template), then rename:

```bash
./scripts/rename.sh <app-slug> <gitlab-group>
./scripts/select-ci.sh gitlab        # or: github | none — see CI-SHAPES.md
```

`scripts/rename.sh` is a thin wrapper that delegates to the library CLI's
`rename` command (fetching it on demand with `pipx run` if it is not already on
`PATH`), so there is one tested substitution implementation.
`scripts/select-ci.sh` is the same kind of wrapper over `prune ci:<shape>`,
which keeps one CI shape and deletes the other two's files (see
[CI-SHAPES.md](CI-SHAPES.md)). Nothing else is required — edit the manifests by
hand and delete the components you don't need.

Both wrappers reach `git.ericsweiss.com`; if you cannot, use the **no-CLI
fallback** below.

### 2. The `weisssrv-new-project` CLI (recommended for component choice)

The library ships a `weisssrv-new-project` CLI that goes beyond rename: it can
**prune** components you don't need structurally (editing the kustomization
resource list and the cross-references, not just text), then **verify** the
result. Install it once, at the tag this repo pins:

```bash
# The spec is POSITIONAL — `pipx install --spec …` fails ("unrecognized
# arguments: --spec"; pipx dropped that flag).
pipx install 'git+https://git.ericsweiss.com/eric/weisssrv-lib.git@vX.Y.Z#subdirectory=cli'

weisssrv-new-project rename recipe-box eric/apps
weisssrv-new-project prune  metrics single-replica   # drop what you don't use
weisssrv-new-project verify                           # no placeholders, kustomize builds
weisssrv-new-project prune  ci:gitlab                 # CI shape (./scripts/select-ci.sh wraps this)
```

Run each from the project root (or pass `--root <dir>`). Full command reference:
the library's [`cli/README.md`](https://git.ericsweiss.com/eric/weisssrv-lib/-/blob/main/cli/README.md).

#### Credentials for the fetch

`eric/weisssrv-lib` is an **internal-visibility** GitLab project, so an
anonymous `git+https://` clone gets a 404 — the same reason `.gitlab-ci.yml`'s
`python-tests` job clones it with `$CI_JOB_TOKEN` instead. Pick one:

```bash
# a) SSH, if your key is on the instance (no token to manage):
pipx install 'git+ssh://git@git.ericsweiss.com/eric/weisssrv-lib.git@vX.Y.Z#subdirectory=cli'

# b) HTTPS with a PAT scoped to read_repository:
pipx install 'git+https://oauth2:<PAT>@git.ericsweiss.com/eric/weisssrv-lib.git@vX.Y.Z#subdirectory=cli'

# c) from a local checkout of the library (no network at all):
pip install ./cli
```

A configured git credential helper covers (b) without putting the token in the
URL. `vX.Y.Z` above is the tag your repo pins — `variables.WEISSSRV_LIB_REF` in
`.gitlab-ci.yml`, the single source. The `WEISSSRV_LIB_REF` environment variable
overrides the tag the wrappers fetch, which is how you test an unreleased ref.

#### No-CLI fallback

If you have no path to the library at all, the scaffold is still usable — the
CLI's value is validation and the structural `prune` edits, not the rename
itself:

```bash
# 1. Rename. Both placeholders are literal tokens in file CONTENT only — no
#    filename carries one — so a plain substitution over the tracked tree is
#    exactly what the CLI does. (BSD/macOS sed: use `sed -i ''`.)
git ls-files -z | xargs -0 sed -i \
  -e 's/changeme-app/<app-slug>/g' -e 's/changeme-group/<gitlab-group>/g'

# 2. Pick a CI shape by deleting the other shapes' files:
#      gitlab -> rm -rf .github/workflows
#      github -> rm -f  .gitlab-ci.yml .gitlab/secret-detection-ruleset.toml
#      none   -> both of the above
rm -rf .github/workflows

# 3. Check.
grep -rn 'changeme[-]' .        # must be empty
kustomize build kubernetes/flux >/dev/null
```

Step 2's file list is the same fixed table the CLI holds; keeping the two in step
is one of the reasons the CLI exists — re-read [CI-SHAPES.md](CI-SHAPES.md) if
you take this path. Toggling optional components by hand is described under
[the toggle table](#optional-enablement-toggles).

---

## How the library is consumed

Every generic lint/validate/security job is pulled in `.gitlab-ci.yml` via
`include: project:` at a **pinned release tag**:

```yaml
include:
  - project: eric/weisssrv-lib
    ref: vX.Y.Z            # == variables.WEISSSRV_LIB_REF; check-lib-pins keeps it in step
    file: /ci/lint/yaml-lint.yml
    inputs:
      tags: []            # the shared non-privileged tenant runner
      config: "-c .yamllint"
      targets: "."
```

- The library is **internal** visibility, so any authenticated instance user
  (hence every tenant repo `eric` owns) can resolve the include. `include:
  project:` resolves with the pipeline creator's read access at pipeline-creation
  time — a fork MR by a non-member would fail include resolution.
- **Pin a tag, never a branch.** A floating `main` ref would auto-propagate a
  library change into your pipeline with no review. Bumping is a deliberate,
  reviewed change: edit every `ref:` in `.gitlab-ci.yml` in one MR. See the
  library's
  [VERSIONING.md](https://git.ericsweiss.com/eric/weisssrv-lib/-/blob/main/docs/VERSIONING.md)
  and, for each template's inputs, its
  [INCLUDE-CONTRACT.md](https://git.ericsweiss.com/eric/weisssrv-lib/-/blob/main/docs/INCLUDE-CONTRACT.md).
- **Every include pins the same tag**, and so do the two `scripts/` wrappers
  (`LIB_REF`, overridable with `$WEISSSRV_LIB_REF`) and the `python-tests` job's
  clone. `.gitlab-ci.yml`'s `variables.WEISSSRV_LIB_REF` is the single source;
  `scripts/check-lib-pins.py` fails the pipeline on an include that drifts from
  it or pins a branch, and `--fix` rewrites them. The wrappers are outside what
  that gate sees — `tests/` ties them to the same value.
- **Tool versions live in the library.** kubeconform / kustomize (and their
  sha256 pins) are the library flux-lint template's input defaults — this repo no
  longer re-pins them, so there is no drift to reconcile. Bump the library `ref:`
  to move them. `k8s_version` is the exception: it is the *cluster's* Kubernetes
  minor, a per-consumer fact, so it is pinned here.

### The jobs the pipeline includes

| Job | Library template | Notes |
|---|---|---|
| `yaml-lint` | `/ci/lint/yaml-lint.yml` | whole tree against the repo-local `.yamllint` |
| `flux-lint` | `/ci/validate/flux-lint.yml` | `substitute: false` — literal image pins, no cluster-versions ConfigMap |
| `flux-lint-optional` | `/ci/validate/flux-lint.yml` | the same gate over `kubernetes/flux/optional/`, so a switched-off add-on cannot rot |
| `shellcheck` | `/ci/lint/shellcheck.yml` | `scripts/*.sh` (no Ansible tree to walk) |
| `docs-link-check` | `/ci/lint/docs-link-check.yml` | vendored `scripts/check-doc-links.py`, every tracked `*.md` |
| `python-tests` | `/ci/test/python-tests.yml` | the **template's own** gate (`tests/`) — see [Removing the template's gate](#removing-the-templates-gate) |
| `secret_detection` | `/ci/security/secret-detection.yml` | GitLab Secret Detection (gitleaks); findings **block** |
| `build-image` | `/ci/build/docker-build.yml` | your service image, on the privileged runner — see below |
| `semantic-release` | `/ci/release/semantic-release.yml` | last stage; tags `main` from its conventional commits ([VERSIONING.md](VERSIONING.md)) |
| `pr-agent-review` | `/ci/review/pr-agent.yml` | optional AI review, BYO keys; the job is not created unless both are set |

### Two jobs you can add

Neither ships, because most tenants want neither, and an inert commented-out job
is a maintenance surface with no gate on it. Both are a paste away.

**Terraform fmt + validate**, if the repo grows a `terraform/` directory. Add an
include for `/ci/validate/terraform.yml` at the same tag as everything else
(`variables.WEISSSRV_LIB_REF`) with `inputs: { tags: [], changes:
["terraform/**/*"] }`, and a `validate` stage to hold it. It runs on the shared
runner, which has no LAN — so `plan`/`apply` would additionally need a remote
HTTP state backend and internet-reachable providers.

**A manual reconcile poke.** Flux already reconciles on merge; this only
fast-forwards the poll, and the receiver must be reachable from the internet
because the shared runner has no LAN access:

```yaml
trigger-reconcile:
  stage: ai-review
  image: curlimages/curl:8.11.1
  needs: []
  script:
    - curl -fsS -X POST "$FLUX_WEBHOOK_URL"
  rules:
    - if: '$CI_COMMIT_BRANCH == "main" && $FLUX_WEBHOOK_URL'
      when: manual
      allow_failure: true
```

### Vendored scripts

`scripts/check-doc-links.py`, `scripts/check-lib-pins.py` and
`scripts/semantic-release.py` are **vendored copies** of the library's
stdlib-only tools — the library's job templates run them from *these* paths, so
the copy in this repo is the one that executes. Re-vendor all three (copy from
the library tag you are moving to) in the same MR that bumps the `ref:`.

`ruff.toml` at the repo root is vendored the same way (from the library's
`lint/ruff.toml`), so `python-lint` reports the same findings here as it does in
the library — re-copy it on a bump too.

All three scripts are checked while `tests/` is still here:
`tests/test_vendored_scripts.py` byte-compares every non-local file in
`scripts/` against the library checkout at the pinned ref, and `python-tests`
sets `WEISSSRV_REQUIRE_CLI=1` so a missing checkout fails the job instead of
skipping green. Delete `tests/` (below) and that gate goes with it — from then
on re-vendoring is unenforced, which is why it belongs in the bump MR rather
than a follow-up.

### Removing the template's gate

`python-tests` runs `tests/`, which exercises **this template's** wrappers — that
no placeholder survives `rename`, and that each CI shape keeps exactly what
[CI-SHAPES.md](CI-SHAPES.md) says it keeps. It is not your app's test suite. In a
generated project the suite skips itself, and you are meant to delete it:

```bash
rm -rf tests/
```

Deleting `tests/` alone leaves two jobs that fail — `pytest` exits 4 on a
missing directory, and `ruff` errors on a target path that does not exist.
Remove **all four** pieces together:

1. `rm -rf tests/`
2. the `/ci/test/python-tests.yml` entry in the `include:` block
3. the `python-tests:` variables override near the bottom of `.gitlab-ci.yml`
4. `tests` from the `python-lint` include's `targets:` — and from the same list
   in `Taskfile.yml`'s `python-lint` task, plus `.github/workflows/ci.yml`'s
   `python-lint` step in shape `github`

Keeping `tests/` instead is fine; the suite is a no-op once renamed. Shape
`github` never ported `python-tests`, so only step 4 applies there.

### Cluster identity

This template is calibrated for **one** cluster: eric's `weisssrv`. The domains,
the node-label prefix, the internal VIP, the registry hosts and the
privileged-runner tag are literals in the manifests and in `.gitlab-ci.yml`
rather than variables — which is what lets `kustomize build kubernetes/flux`
run with no inputs.

They are collected in **one seam**: `scripts/cluster-identity.env`. Edit it and
run `./scripts/apply-cluster-identity.sh` once, right after `rename.sh`, to
retarget the deployable surface (`kubernetes/`, `.gitlab-ci.yml`,
`.github/workflows/`, `Taskfile.yml`). The shipped defaults *are* weisssrv's
values, so a run without edits changes nothing and a weisssrv tenant never needs
it. Markdown is deliberately out of scope — the docs are prose to fix by hand.
See [ONBOARDING.md § Step 0](ONBOARDING.md#step-0--cluster-identity).

The library's own account of which mechanisms are pluggable and which are
backend-by-design is
[EXTENSIBILITY.md](https://git.ericsweiss.com/eric/weisssrv-lib/-/blob/main/docs/EXTENSIBILITY.md).

---

## Optional-enablement toggles

Everything under `kubernetes/flux/` is a starting point.

### Switching an add-on ON

The opt-in manifests are **real files** in `kubernetes/flux/optional/`, listed in
`kubernetes/flux/kustomization.yaml` as commented-out lines. Flux never builds
`optional/` — it reconciles `kubernetes/flux/` — so a manifest sitting there is
inert. Enabling one is uncommenting its line:

```yaml
resources:
  ...
  # - optional/hpa.yaml                       # horizontal autoscaling
  # - optional/ingressroute-internal.yaml     # internal (LAN/tailnet) route
  # - optional/certificate-internal.yaml      # cert for that internal route
  # - optional/externalsecret-registry.yaml   # private-registry pull secret
  # - optional/externalsecret-gitlab.yaml     # REPLACES externalsecret.yaml
```

Each file's header carries the rest of its checklist — the HPA also wants
`replicas:` dropped from `deployment.yaml` and the VPA made memory-only; the
internal route wants its certificate enabled alongside and an operator AdGuard
rewrite; the GitLab ExternalSecret *replaces* `externalsecret.yaml` rather than
joining it. They are kept out of the reconciled build but not out of CI: the
`flux-lint-optional` job runs `kustomize build` + kubeconform over
`optional/kustomization.yaml`, so a switched-off manifest cannot rot into
something that fails the day you enable it.

| Add-on | Enable by |
|---|---|
| **HPA** (`optional/hpa.yaml`) | uncommenting the line, then dropping `replicas:` and setting the VPA's `controlledResources: [memory]` |
| **Internal IngressRoute + Certificate** (`optional/*-internal.yaml`) | uncommenting **both** lines, plus operator step [O5](ONBOARDING.md#o5--internal-dns-only-if-the-tenant-needs-slugesweisscom) |
| **Registry pull secret** (`optional/externalsecret-registry.yaml`) | uncommenting the line, adding `imagePullSecrets:` to the pod spec, plus operator step [O2b](ONBOARDING.md#o2b--registry-pull-credential-only-if-the-image-is-private) |
| **GitLab secret backend** (`optional/externalsecret-gitlab.yaml`) | uncommenting the line **and removing** `- externalsecret.yaml` |
| **Authentik SSO** forward-auth middleware | `weisssrv-new-project wire sso`, or uncommenting the two middleware lines in `ingressroute.yaml`; plus operator step [O8](ONBOARDING.md#o8--sso-objects-only-if-the-tenant-runs-wire-sso) |

### Switching something OFF

`prune` does this structurally — it deletes the manifest, drops its
`kustomization.yaml` entry and cleans the cross-references, validating the whole
request before touching a file so a typo never half-mutates the repo.

| Component | Default | Turn off |
|---|---|---|
| **CI shape** (`.gitlab-ci.yml` / `.github/workflows/`) | `gitlab` | `prune ci:<gitlab\|github\|none>`, or the `./scripts/select-ci.sh` wrapper; see [CI-SHAPES.md](CI-SHAPES.md) |
| **PodDisruptionBudget** (`pdb.yaml`) | on | `prune pdb`, or `prune single-replica` (also sets `replicas: 1`) |
| **ServiceMonitor + scrape policy** | on | `prune metrics` (drops the ServiceMonitor and the observability-scrape NetworkPolicy) |
| **ExternalSecret** (secrets) | on (1Password backend) | `prune secrets` (drops the manifest and the Deployment's secret `env` block) |
| **Image build** (`Dockerfile` + `build-image` CI job) | on | `prune image-build` deletes `Dockerfile`/`.dockerignore`; for a no-build (upstream-image) project also remove the build include from `.gitlab-ci.yml`, or delete `.github/workflows/build-image.yml` in the `github` shape (it already no-ops without a Dockerfile) |
| **Any single manifest** | — | `prune manifest:<file>` (deletes it and its kustomization entry) |
| **Plain (non-k8s) repo** | — | delete `kubernetes/flux/` entirely; keep only the lint/secret-detection CI |

By hand, every one of those is "delete the file, delete its `kustomization.yaml`
line, delete whatever referenced it". `verify` (or `task lint`) confirms the
result still builds.

**Secrets backend.** `externalsecret.yaml` ships the 1Password variant active;
`optional/externalsecret-gitlab.yaml` is the CI/CD-variable alternative. They
create the same Secret, so run exactly one. Pick the one the operator provisions
your `ClusterSecretStore` for (see [ONBOARDING.md](ONBOARDING.md)); `prune
secrets` removes the whole surface if your app needs none.

---

## Building the service image

Building the service image is **the main use case, and it is on by default**:
the template ships a repo-root **`Dockerfile`** (a non-root placeholder serving
`:8080`) and an **active `build-image` CI job**. Replace the placeholder with
your app's real build, keeping it non-root (UID 65532) and read-only-rootfs
friendly so it satisfies the namespace's Pod Security Admission baseline.

1. **CI build (default, active)** — the `ci/build/docker-build.yml` include in
   `.gitlab-ci.yml` builds the repo-root Dockerfile on every MR/main and pushes
   `$CI_REGISTRY_IMAGE:<short-sha>` (+ `:latest` on main; `$CI_REGISTRY_*` are
   GitLab built-ins, no key to bring). It **needs a PRIVILEGED runner** —
   Docker-in-Docker cannot run on the shared, non-privileged tenant runner — so
   it is tagged `tags: ["infrastructure"]` (weisssrv's privileged runner). Retag
   it to your **own** privileged runner if you registered one. Point
   `deployment.yaml`'s `image:` at the pushed tag. *(Shape `github`:
   `.github/workflows/build-image.yml` does the equivalent to
   `ghcr.io/<owner>/<repo>` — no privileged runner needed, and a pull request
   builds without pushing. See [CI-SHAPES.md](CI-SHAPES.md).)*
2. **Locally** — `task build` (your workstation's Docker daemon), then push to
   `registry.git.ericsweiss.com/<group>/<app>` (log in with a project deploy
   token or a PAT).
3. **Upstream image (no build)** — for the rare project that builds nothing,
   remove the `build-image` include from `.gitlab-ci.yml` and run
   `weisssrv-new-project prune image-build` to drop the Dockerfile, then point
   `image:` at any existing image.

**Pulling it back.** A GitLab **project** registry is private by default, so the
cluster needs a pull credential in your namespace. That path ships:
`kubernetes/flux/optional/externalsecret-registry.yaml` renders a
`kubernetes.io/dockerconfigjson` Secret from a `read_registry` deploy token —
uncomment its line in `kubernetes/flux/kustomization.yaml` and add
`imagePullSecrets: [{name: <slug>-registry}]` to the pod spec. The operator side
is [ONBOARDING.md § O2b](ONBOARDING.md#o2b--registry-pull-credential-only-if-the-image-is-private).
Making the project's package registry public avoids it entirely, as does
pointing `image:` at a public upstream image.

---

## Bring your own keys

No secret values live in this repo. Depending on which components you enable,
bring the following as **masked CI/CD variables** (Settings → CI/CD → Variables)
or operator-provisioned cluster objects. Everything here is optional — an absent
key just means the dependent job/component isn't active.

| Key / credential | Needed for | Notes |
|---|---|---|
| `OP_SERVICE_ACCOUNT_TOKEN` **or** GitLab CI/CD variables | the ExternalSecret backend | Which one depends on the `ClusterSecretStore` the operator provisions (`onepassword-<slug>` vs `gitlab-<slug>`). 1Password item keys are prefixed `"<slug>: <Item>"`. |
| `OPENAI__KEY` + `GITLAB__PERSONAL_ACCESS_TOKEN` | `pr-agent-review` (AI code review) | Both masked. Absent them the job isn't created (e.g. fork MRs). 1Password users source these from their vault when SETTING the variables — the shared runner can't run the op CLI at job time. |
| `$CI_REGISTRY` / `$CI_REGISTRY_USER` / `$CI_REGISTRY_PASSWORD` | the (active) CI image build, shape `gitlab` | GitLab **built-ins** — nothing to set. Registry is `registry.git.ericsweiss.com/<group>/<app>`. |
| `GITHUB_TOKEN` (+ `packages: write`) | the CI image build, shape `github` | A GitHub **built-in** — nothing to set. Registry is `ghcr.io/<owner>/<repo>`. A **private** GHCR package additionally needs an `imagePullSecret` in your namespace; making the package public avoids that. |
| A **privileged runner** | the (active) CI image build, shape `gitlab` | Tagged `infrastructure` by default. The shared runner is non-privileged; DinD needs `--privileged`. Retag to your own privileged runner, or (rare) remove the build include for a no-build project. GitHub-hosted runners ship Docker, so shape `github` needs nothing here. |
| A **registry pull credential** | pulling a **private** project-registry image | A `dockerconfigjson` Secret in your namespace, from a deploy token with `read_registry`. Operator step [O2b](ONBOARDING.md#o2b--registry-pull-credential-only-if-the-image-is-private); a public package or a public upstream image needs none. |
| **CI/CD job-token allowlist** on `eric/weisssrv-lib` | `python-tests`, while you keep `tests/` | The job clones the internal library with `$CI_JOB_TOKEN`, and GitLab's allowlist is per-project — an un-allowlisted project gets 403 before pytest runs. Ask the operator ([O9](ONBOARDING.md#o9--job-token-allowlist-only-while-the-tenant-keeps-tests)), or [delete the gate](#removing-the-templates-gate). |
| Authentik provider/application objects | SSO (`wire sso`) | Operator-provisioned (codified in weisssrv's `terraform/authentik`); the middleware only references them. Operator step [O8](ONBOARDING.md#o8--sso-objects-only-if-the-tenant-runs-wire-sso). |
| A read-only `kubeconfig` / cluster agent | local `task flux:status` / `secrets:check` | Operator-provided. Never used to deploy — Flux owns that; these are read-only checks. |

Deploys themselves need **no** key from you: on merge to `main` the operator-side
Flux `Kustomization` reconciles this repo (git is the source of truth).
