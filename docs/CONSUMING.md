# Generating and updating a tenant repo

This is a [copier](https://copier.readthedocs.io) template. `copier copy`
renders it into a new repository; `copier update` replays the recorded answers
against a newer template tag, so a fix made here reaches every generated repo as
a reviewable diff.

```bash
pipx install copier                     # or: uv tool install copier
copier copy https://git.ericsweiss.com/eric/weisssrv-app-template my-service
cd my-service && git init && git add -A && git commit -m "chore: generate from template"
```

Answers land in `.copier-answers.yml`. Later:

```bash
copier update                           # re-render at the newest template tag
copier update --data enable_hpa=true    # flip one answer and re-render
```

`copier update` is a three-way merge: your edits survive, the template's changes
arrive, and a genuine conflict lands as conflict markers to resolve. Commit
before running it.

For the design this template encodes, see [ARCHITECTURE.md](ARCHITECTURE.md);
for the pipeline choice, [CI-SHAPES.md](CI-SHAPES.md).

---

## The answers

**Site identity has no default** — not the app's own names, and not the facts
about the cluster it deploys into. Those questions carry a `placeholder:`
showing the shape and a validator that rejects an empty answer, so a
defaults-only run is refused rather than producing a repo wired to whichever
cluster this template was extracted from. What is defaulted is what composes
from an answer already given (the registry hosts, the node-label prefix) or is
a template-wide choice (the port, the CI shape, the component switches).

A "—" in the Default column below means there is none: the placeholder shown
is an example, not a value you can accept by pressing enter.

### App identity

| Answer | Default | What it drives |
|---|---|---|
| `app_slug` | — | Repository name, Flux Kustomization, every `metadata.name`, the hostname's first label. A DNS label. |
| `app_namespace` | `{{ app_slug }}` | The namespace the operator creates and scopes the Flux service account to. |
| `app_port` | `8080` | The container port, the Service, the IngressRoute backend and both NetworkPolicy ingress rules — one answer, four places. Must be >= 1024: the pod runs as UID 65532 and cannot bind a privileged port. |
| `replica_count` | `2` | Deployment replicas. At 1 no PodDisruptionBudget is generated — `minAvailable: 1` on one replica blocks every voluntary eviction, so a node could never drain. Ignored when `enable_hpa` is on. |
| `copyright_holder` | — | The name on the generated `LICENSE` (MIT), which the generated README links as the repository's own. The year is filled in at render time. This template's licence covers the template, not what it renders, so there is nothing sensible to default to. |

### Cluster identity

| Answer | Default | What it drives |
|---|---|---|
| `external_domain` | — | The public hostname and the external-dns target annotation. |
| `internal_domain` | — | The LAN/tailnet hostname. Must differ from `external_domain` **including its first label** — the two TLS Secrets are named after that label, and a collision puts two Certificates on one Secret. |
| `node_label_domain` | `{{ internal_domain }}` | Node-label prefix (`<prefix>/nas`, `<prefix>/cpu`) for the scheduling affinity and the build's CPU selector. |
| `internal_vip` | — | The address the operator points the internal hostname at. Documentation only; nothing dials it — which is why it is asked further down, with `enable_internal_ingress`, and skipped entirely when that is off. |
| `registry_host` | `registry.git.{{ external_domain }}` (or `ghcr.io`) | Registry in the Deployment's `image:`. |
| `registry_pull_host` | `registry.git.{{ internal_domain }}` (or `ghcr.io`) | The name **nodes** pull from. Set it equal to `registry_host` when there is only one. Both are keyed into the pull credential: the kubelet matches a pull secret against the literal host in the image reference. |
| `runbook_url` | — | The `runbook_url` annotation on every generated alert. Point it at the cluster repo's Flux day-2 operations page; a guessed one is a 404 delivered at 3am. |

### Forge and CI

| Answer | Default | What it drives |
|---|---|---|
| `ci_shape` | `gitlab_selfhosted` | Which pipeline ships — see [CI-SHAPES.md](CI-SHAPES.md). Flux deploys the repo in all three. |
| `change_request` | derived from `ci_shape` | **Never asked.** The forge's word for a reviewed change — "pull request" under `github`, "merge request" otherwise — substituted into every generated doc, agent file and Cursor rule, so the instruction names an object the forge has. |
| `enable_image_build` | `true` | The placeholder `Dockerfile` and the build job. Off for a service that runs an upstream image. |
| `git_host` | `git.{{ external_domain }}` (or `github.com`) | The forge this repo lives on: the library include host, the AI-review URL, the repo URL in the operator's wiring. |
| `git_namespace` | — | The group/user path that owns the repo. Also the middle segment of the image path. |
| `privileged_runner_tag` | `infrastructure` | Runner tag for the one job that needs a privileged runner (the image build); asked only for the GitLab shape with a build. |
| `ci_cpu_selector` | `{{ node_label_domain }}/cpu=modern` | Node selector pinning the CPU-sensitive jobs — secret detection always, the image build when it exists — to a modern CPU. Asked for the whole GitLab shape, because the library defaults it to its own cluster's label domain and a selector no node carries leaves the job Pending. Required, and must match the runner's `node_selector_overwrite_allowed` regex `^[a-z0-9.-]+/cpu=(modern\|legacy)$`: the pipeline always passes the input, and an empty value fails that regex at pod creation rather than skipping the pin. |
| `k8s_version` | `1.36.0` | The Kubernetes minor kubeconform validates against, in the pipeline and `Taskfile.yml`. |

### Secrets

| Answer | Default | What it drives |
|---|---|---|
| `secrets_backend` | `onepassword` | `onepassword`, `gitlab` or `none`. Chooses the ExternalSecret's store and `remoteRef` shape, and whether the Deployment gets a secret `env` block at all. Must match what the operator provisions. |
| `onepassword_vault` | — | 1Password only: the vault the operator's `ClusterSecretStore` reads and the scoped Connect token is issued against. A store naming a vault that does not exist is accepted by the API server and then fails every fetch. |
| `secret_item` | `App Secrets` | 1Password only: the item title, which renders as the prefixed `"<slug>: <item>"`. |
| `gitlab_api_url` | `https://{{ git_host }}` | GitLab only: the instance holding the CI/CD variables. Separate from `git_host` because the variables need not live on the forge the code does — and because `ci_shape: github` defaults `git_host` to github.com, which is not a GitLab instance. The ESO provider's own default is `https://gitlab.com`, so the store always names it explicitly. |

### Optional components

Each renders a manifest **and** its line in `kustomization.yaml`, so an enabled
component is wired and a disabled one leaves nothing behind. There is no
`optional/` directory and no commented resource list to forget.

| Answer | Default | Renders |
|---|---|---|
| `enable_servicemonitor` | `false` | The ServiceMonitor and the matching scrape NetworkPolicy. **Off by default**: a ServiceMonitor pointed at an endpoint that does not exist yet scrapes `up == 0` and raises TargetDown in the *operator's* Alertmanager. Turn it on with the change that adds `/metrics`. |
| `enable_internal_ingress` | `false` | The internal IngressRoute **and** its Certificate, together — one without the other serves from a TLS Secret that never exists. Needs one operator DNS step. |
| `enable_hpa` | `false` | The HPA; the Deployment then ships no `replicas:` and the VPA becomes memory-only, so the two autoscalers never both drive CPU. |
| `enable_registry_pull_secret` | `{{ enable_image_build and secrets_backend != 'none' }}` | The `dockerconfigjson` ExternalSecret **and** the pod's `imagePullSecrets:` entry. Defaulted on for the combination that needs it — both forges publish privately by default (a GitLab project registry; a GHCR package, whose visibility does not follow the source repo's), so a repo that builds its own image cannot be pulled without it. No `ci_shape` term: nothing in the rendered chain is forge-shaped — the ExternalSecret keys on `registry_host`/`registry_pull_host` (both `ghcr.io` under the GitHub shape, deduplicated to one `auths` entry) and reads a username+token pair, which is what `docker login ghcr.io` takes. The backend term is not redundant with the `when:` that skips the question under `secrets_backend: none`: a skipped question still takes its default, and a `true` recorded there is a component no render site will produce. |
| `enable_sso` | `false` | The Authentik forward-auth middleware on the public route. The middleware alone authenticates nothing — the operator's provider/application/outpost objects are what enforce it. |

### Library pin (GitLab shape only)

| Answer | Default | What it drives |
|---|---|---|
| `lib_ref` | `v0.13.0` | The tag every `include:` pins. A release tag, never a branch: the generated repo's own `check-lib-pins.py` enforces the shape on the first pipeline. |
| `lib_project` | `eric/weisssrv-lib` | The library's project path. `include: project:` resolves instance-locally, so the library must live on the same GitLab as the repo. |

---

## What the generated repo contains

```
kubernetes/flux/     # everything Flux reconciles into the namespace
docs/                # ONBOARDING (the operator's wiring, rendered), ARCHITECTURE, VERSIONING
scripts/             # vendored library helpers — see below; which ones ship depends on ci_shape
Taskfile.yml         # the local gates and read-only cluster checks
.copier-answers.yml  # the answers, replayed by `copier update`
```

Plus the pipeline for the chosen shape, a placeholder `Dockerfile` and its build
job when `enable_image_build` is on (on the GitHub shape that job is the whole
`build-image.yml` workflow, dropped with the answer so a repo running an
upstream image gets no `packages: write` workflow), and the agent files
(`CLAUDE.md`, `AGENTS.md`, the `project-development` skill, Cursor rules).

## Changing the generated repo by hand

Nothing stops you — it is your repository. Two things to know:

- **Adding a manifest** means adding its line to
  `kubernetes/flux/kustomization.yaml`. A file Flux never builds is inert, and
  inert manifests rot.
- **`copier update` re-renders the files it generated.** An edit inside one of
  them is preserved by the three-way merge, but a change you would rather keep
  forever is better made as an answer here, or upstream in this template.

## The vendored library helpers

`scripts/check-doc-links.py`, `scripts/check-lib-pins.py` and
`scripts/semantic-release.py` are **byte-identical copies** of
[`eric/weisssrv-lib`](https://git.ericsweiss.com/eric/weisssrv-lib)'s
stdlib-only tools: the library's job templates run them from those paths, so the
copy is what executes. All three live here under `template/scripts/`; a
generated repo gets only the ones its shape runs —

| `ci_shape` | Ships |
|---|---|
| `gitlab_selfhosted` | all three |
| `github` | `check-doc-links.py`, `semantic-release.py` (the pin gate has no includes to gate) |
| `none` | `check-doc-links.py` only (`task doc-links` runs it with no pipeline at all) |

This repository owns the copy relationship in `scripts/vendored-manifest.yml`
and the library's `check-vendored-copies.py` gates it — fix a bug upstream and
re-vendor, never in the copy, which the next re-vendor reverts.

The lint profiles are **forks** of library files rather than copies: the same
rules with per-repo paths. They come in two sets, registered separately because
they drift separately —

- at the root, what this template lints **itself** with: `ruff.toml`,
  `.gitleaks.toml`, `.editorconfig` and `.gitlab/secret-detection-ruleset.toml`;
- under `template/`, what a generated repo gets: `template/ruff.toml`,
  `template/.gitleaks.toml`, `template/.editorconfig`,
  `template/.pre-commit-config.yaml` and the rendered
  `.gitlab/secret-detection-ruleset.toml`.

The registry records each as a fork and pins the library-side blob it was last
reconciled against, so an upstream change is surfaced rather than silently
ignored.
