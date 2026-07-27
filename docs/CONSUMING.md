# Consuming the template & the shared library

This template deploys a service to the weisssrv k3s cluster, and its **CI comes
from a shared library** ([`eric/weisssrv-lib`](https://git.ericsweiss.com/eric/weisssrv-lib))
rather than being hand-rolled per project. This page covers the two ways to
create a project, how the library is consumed and bumped, the optional
components you can toggle on/off, the image-build story, and the keys you bring.

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
`rename` command (installing it on demand via `pipx` if needed), so there is one
tested substitution implementation. `scripts/select-ci.sh` keeps one CI shape
and deletes the other two's files; it is repo-local because the library CLI does
not model CI shapes yet (see [CI-SHAPES.md](CI-SHAPES.md)). Nothing else is
required — edit the manifests by hand and delete the components you don't need.

### 2. The `weisssrv-new-project` CLI (recommended for component choice)

The library ships a `weisssrv-new-project` CLI that goes beyond rename: it can
**prune** components you don't need and **wire** opt-in ones structurally
(editing the kustomization resource list and cross-references, not just text),
then **verify** the result. Install it once, straight from the library:

```bash
pipx install --spec 'git+https://git.ericsweiss.com/eric/weisssrv-lib.git@v0.1.1#subdirectory=cli' weisssrv-new-project
# or, from a local library checkout:  pip install ./cli

weisssrv-new-project rename recipe-box eric/apps
weisssrv-new-project prune  metrics single-replica   # drop what you don't use
weisssrv-new-project wire   hpa                       # enable an opt-in
weisssrv-new-project verify                           # no placeholders, kustomize builds
./scripts/select-ci.sh gitlab                         # CI shape (not yet a CLI command)
```

Run each from the project root (or pass `--root <dir>`). Full command reference:
the library's [`cli/README.md`](https://git.ericsweiss.com/eric/weisssrv-lib/-/blob/main/cli/README.md).

---

## How the library is consumed

Every generic lint/validate/security job is pulled in `.gitlab-ci.yml` via
`include: project:` at a **pinned release tag**:

```yaml
include:
  - project: eric/weisssrv-lib
    ref: v0.1.1
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
  reviewed change: edit every `ref:` in `.gitlab-ci.yml` in one MR (`scripts/
  rename.sh` reads `WEISSSRV_LIB_REF` for the same pin). See the library's
  [VERSIONING.md](https://git.ericsweiss.com/eric/weisssrv-lib/-/blob/main/docs/VERSIONING.md)
  and, for each template's inputs, its
  [INCLUDE-CONTRACT.md](https://git.ericsweiss.com/eric/weisssrv-lib/-/blob/main/docs/INCLUDE-CONTRACT.md).
- **Tool versions live in the library.** kubeconform / kustomize (and their
  sha256 pins) are the library flux-lint template's input defaults — this repo no
  longer re-pins them, so there is no drift to reconcile. Bump the library `ref:`
  to move them.

The jobs the pipeline includes: `yaml-lint`, `flux-lint` (simple/`substitute:
false` mode — literal image pins, no cluster-versions ConfigMap; `k8s_version`
is pinned here since it's the cluster's k8s minor, a per-consumer fact),
`shellcheck`, `docs-link-check`, `secret_detection`, and `build-image` (the
service image — on the privileged runner; see below). `pr-agent-review` stays
defined locally (the library ships no pr-agent template) as a BYO-keys job.

`scripts/check-doc-links.py` is a **vendored copy** of the library's stdlib-only
link checker — the `docs-link-check` job runs it from this path. Re-vendor it
(copy from the pinned library tag) when you bump the library `ref:`.

---

## Optional-enablement toggles

Everything under `kubernetes/flux/` is a starting point. The table shows the
default, and how to toggle each — via the CLI (structural, preferred) or by
hand. The CLI validates the whole request before touching a file, so a typo
never half-mutates the repo.

| Component | Default | Turn off / on |
|---|---|---|
| **CI shape** (`.gitlab-ci.yml` / `.github/workflows/`) | `gitlab` | `./scripts/select-ci.sh <gitlab\|github\|none>` — not a CLI feature yet; see [CI-SHAPES.md](CI-SHAPES.md) |
| **Public IngressRoute** (`*.ericsweiss.com`) | on | `prune external-ingress` (wire the internal route first) |
| **Internal IngressRoute** (`*.esweiss.com`) | off (commented) | `wire internal-ingress` + operator AdGuard rewrite |
| **Authentik SSO** forward-auth middleware | off (commented) | `wire sso` + operator provisions the Authentik objects |
| **HPA** (`hpa.yaml`) | off (commented, not in kustomization) | `wire hpa` (drops `replicas`, makes the VPA memory-only) |
| **PodDisruptionBudget** (`pdb.yaml`) | on | `prune pdb`, or `prune single-replica` (also sets `replicas: 1`) |
| **ServiceMonitor + scrape policy** | on | `prune metrics` (drops servicemonitor + the observability-scrape NetworkPolicy) |
| **ExternalSecret** (secrets) | on (1Password backend) | `prune secrets` (drops the manifest + the deployment secret env block) |
| **Image build** (`Dockerfile` + `build-image` CI job) | on (active) | `prune image-build` deletes Dockerfile/.dockerignore; for a no-build (upstream-image) project also remove the build include from `.gitlab-ci.yml`, or delete `.github/workflows/build-image.yml` in the `github` shape (it already no-ops without a Dockerfile) |
| **Any single manifest** | — | `prune manifest:<file>` (deletes it + its kustomization entry) |
| **Plain (non-k8s) repo** | — | delete `kubernetes/flux/` entirely; keep only the lint/secret-detection CI |

By hand, the same toggles are: uncomment the shipped-commented block (internal
route, SSO middleware, HPA), delete the manifest and its `kustomization.yaml`
line, or edit `replicas`. `verify` (or `task lint`) confirms the result still
builds.

**Secrets backend.** `externalsecret.yaml` ships the 1Password variant active
and the GitLab CI/CD-variable variant commented. Pick the one the operator
provisions your `ClusterSecretStore` for (see [ONBOARDING.md](ONBOARDING.md));
`prune secrets` removes the whole surface if your app needs none.

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

---

## Bring your own keys

No secret values live in this repo. Depending on which components you enable,
bring the following as **masked CI/CD variables** (Settings → CI/CD → Variables)
or operator-provisioned cluster objects. Everything here is optional — an absent
key just means the dependent job/component isn't active.

| Key / credential | Needed for | Notes |
|---|---|---|
| `OP_SERVICE_ACCOUNT_TOKEN` **or** GitLab CI/CD variables | the ExternalSecret backend | Which one depends on the `ClusterSecretStore` the operator provisions (`onepassword-<slug>` vs `gitlab-<slug>`). 1Password item keys are prefixed `"<slug>: <Item>"`. |
| `AI_REVIEW_OPENAI_KEY` + `GITLAB_REVIEW_TOKEN` | `pr-agent-review` (AI code review) | Both masked. Absent them the job isn't created (e.g. fork MRs). 1Password users source these from their vault when SETTING the variables — the shared runner can't run the op CLI at job time. |
| `$CI_REGISTRY` / `$CI_REGISTRY_USER` / `$CI_REGISTRY_PASSWORD` | the (active) CI image build, shape `gitlab` | GitLab **built-ins** — nothing to set. Registry is `registry.git.ericsweiss.com/<group>/<app>`. |
| `GITHUB_TOKEN` (+ `packages: write`) | the CI image build, shape `github` | A GitHub **built-in** — nothing to set. Registry is `ghcr.io/<owner>/<repo>`. A **private** GHCR package additionally needs an `imagePullSecret` in your namespace; making the package public avoids that. |
| A **privileged runner** | the (active) CI image build, shape `gitlab` | Tagged `infrastructure` by default. The shared runner is non-privileged; DinD needs `--privileged`. Retag to your own privileged runner, or (rare) remove the build include for a no-build project. GitHub-hosted runners ship Docker, so shape `github` needs nothing here. |
| Authentik provider/application objects | SSO (`wire sso`) | Operator-provisioned (codified in weisssrv's `terraform/authentik`); the middleware only references them. |
| A read-only `kubeconfig` / cluster agent | local `task flux:status` / `secrets:check` | Operator-provided. Never used to deploy — Flux owns that; these are read-only checks. |

Deploys themselves need **no** key from you: on merge to `main` the operator-side
Flux `Kustomization` reconciles this repo (git is the source of truth).
