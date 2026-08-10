# weisssrv-app-template

A forkable GitLab project template for services that deploy to the **weisssrv**
homelab k3s cluster. Create from it and you get, on day one:

- a hardened, non-root **Deployment + Service**,
- a **public HTTPS route** (`<app>.ericsweiss.com`) that provisions its own DNS
  and TLS,
- **secret wiring** via External Secrets (1Password or GitLab CI/CD variables),
- **default-deny NetworkPolicies**, a **ServiceMonitor**, down/stale **alerts**,
  and a **VPA** — observability and autoscaling without extra work,
- **CI** that lints, schema-validates the manifests, and scans for secrets on
  every change — in **three interchangeable shapes** you pick with one command
  at setup: self-hosted **GitLab** (the default, pulling its jobs from the shared
  [`eric/weisssrv-lib`](https://git.ericsweiss.com/eric/weisssrv-lib) library at
  a pinned tag), **GitHub Actions**, or **none at all**. See
  [`docs/CI-SHAPES.md`](docs/CI-SHAPES.md).

Flux (GitOps) does the deploying **in every shape**: you edit YAML, open a merge
request/pull request, and on merge to `main` the cluster reconciles this repo
into your namespace. The pipeline never deploys — there is no `kubectl apply` in
the normal flow, and `kubernetes/flux/` is identical whichever shape you pick. A
placeholder `Dockerfile` ships and CI **builds your service image by default**;
see [step 3](#3-set-your-image).

> New here? [`docs/CI-SHAPES.md`](docs/CI-SHAPES.md) is the first fork in the
> road — GitLab, GitHub, or no pipeline.
> [`docs/CONSUMING.md`](docs/CONSUMING.md) covers the two ways to
> create a project (fork or the `weisssrv-new-project` CLI), the optional
> components you can toggle, the image-build story, and the keys you bring. The
> agent skill in `.claude/skills/project-development/` and
> [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) explain how a tenant app rides
> the platform; operator/tenant checklists are in
> [`docs/ONBOARDING.md`](docs/ONBOARDING.md).
> [`docs/VERSIONING.md`](docs/VERSIONING.md) covers the release tags this
> pipeline cuts — for the scaffold, and then for your own service.

**One cluster, spelled out in literals.** This template targets eric's
`weisssrv` cluster: `ericsweiss.com` / `esweiss.com`, the `esweiss.com/*` node
labels, `registry.git.ericsweiss.com` and the `infrastructure` runner tag are
written into the manifests and `.gitlab-ci.yml` rather than parameterised, which
is what lets `kustomize build` run with no inputs. They are collected in one
seam: edit `scripts/cluster-identity.env` and run
`./scripts/apply-cluster-identity.sh` once to retarget the scaffold at a cluster
generated from `weisssrv-cluster-template`. The defaults are weisssrv's values,
so an existing tenant never runs it. See
[`docs/ONBOARDING.md` § Step 0](docs/ONBOARDING.md#step-0--cluster-identity).

---

## Quick start

### 1. Create the project

In GitLab: **Fork this project** and clone it. (You can use **New project →
Create from template** instead only if the operator registered this as a
group/instance custom template — otherwise it won't show up in the picker.)

### 2. Rename (the 3 things you set) and pick a CI shape

```bash
./scripts/rename.sh <app-slug> <gitlab-group>
./scripts/select-ci.sh gitlab      # or: github | none
```

`scripts/rename.sh` is a thin wrapper over the library's `weisssrv-new-project`
CLI. For dropping components (not just renaming), use the CLI directly — it
`prune`s structurally and then `verify`s the result:

```bash
weisssrv-new-project rename <app-slug> <gitlab-group>
weisssrv-new-project prune metrics single-replica   # optional
weisssrv-new-project verify
```

Either way, `grep -rn 'changeme[-]' .` afterward confirms no placeholders are
left. The bracket is deliberate: the pattern matches a real placeholder but not
this line, so a clean project gets zero hits instead of hits on the very docs
telling you to run the check.

[`docs/CONSUMING.md`](docs/CONSUMING.md) has the CLI install instructions and the
full toggle list. The library is an **internal-visibility** project, so the same
page also carries the credential forms for fetching the CLI — and a no-CLI
fallback if you have no path to it at all.

`scripts/select-ci.sh` keeps one CI shape and deletes the other two's files —
**`gitlab` is the default**, and running it even for `gitlab` matters (it drops
`.github/workflows/`, which a GitHub mirror with Actions enabled would otherwise
run as a duplicate set of gates). The shapes, the job-for-job parity table, and
what a github.com repo gives up are in [`docs/CI-SHAPES.md`](docs/CI-SHAPES.md).

The three things you're really setting:

1. **App slug** — also your Kubernetes namespace and Flux Kustomization name.
   Keep it a valid DNS label (`recipe-box`, not `Recipe_Box`).
2. **Public host** — defaults to `<slug>.ericsweiss.com` in
   `kubernetes/flux/ingressroute.yaml` and `certificate.yaml`.
3. **Internal host** (optional) — `<slug>.esweiss.com`; uncomment
   `- optional/ingressroute-internal.yaml` and
   `- optional/certificate-internal.yaml` in
   `kubernetes/flux/kustomization.yaml`, and request the operator DNS step.

Targeting a different cluster? Edit `scripts/cluster-identity.env` and run
`./scripts/apply-cluster-identity.sh` before you go further
([`docs/ONBOARDING.md` § Step 0](docs/ONBOARDING.md#step-0--cluster-identity)).

### 3. Set your image

**Required, not optional.** `kubernetes/flux/deployment.yaml` ships
`registry.git.ericsweiss.com/changeme-group/changeme-app:REPLACE-ME` — a tag no
registry has. Replace it, or the first reconcile `ImagePullBackOff`s.
The usual value is the `:<short-sha>` the CI build just pushed. Tags are
**literal pins** — there's no Flux `${var}` substitution for tenant repos and no
hosted dependency bot, so bump them yourself (see [Keeping image tags
current](#keeping-image-tags-current)). If the project registry is private, the
namespace also needs a pull credential — operator step
[O2b](docs/ONBOARDING.md#o2b--registry-pull-credential-only-if-the-image-is-private).

A placeholder `Dockerfile` ships as the buildable default — **replace it with
your service's real build**. Three ways an image gets built (full detail in
[`docs/CONSUMING.md`](docs/CONSUMING.md)):

- **CI build (default)** — shape `gitlab`: the `ci/build/docker-build.yml`
  include builds the repo-root Dockerfile on every MR/main and pushes
  `$CI_REGISTRY_IMAGE:<short-sha>` (+ `:latest` on main). It runs on a
  **privileged runner** (Docker-in-Docker), tagged `infrastructure` — retag it
  to your own privileged runner if you have one; see [CI runner](#ci-runner-shape-gitlab).
  Shape `github`: `.github/workflows/build-image.yml` does the same to
  `ghcr.io/<owner>/<repo>`, building on pull requests and pushing on merge
  ([`docs/CI-SHAPES.md`](docs/CI-SHAPES.md)). Shape `none` builds nothing.
- **Locally** — `task build`, then push to
  `registry.git.ericsweiss.com/<group>/<slug>:<tag>` (or your own registry).
- **Upstream image (no build)** — for the rare project that builds nothing,
  remove the build include from `.gitlab-ci.yml` (or delete
  `.github/workflows/build-image.yml`) and run `weisssrv-new-project prune
  image-build` to drop the Dockerfile, then point `image:` at any image.

### 4. Ship

```bash
task lint            # the same four gates the CI lint stage runs
git switch -c my-change && git commit -am "feat: ..." && git push -u origin my-change
```

Open the MR (or PR). On merge, Flux reconciles — assuming the operator has wired
your repo once (below). `task lint` covers CI's whole lint stage (yamllint,
kustomize + kubeconform, shellcheck, Markdown links); secret detection is
pipeline-only, and the pre-commit hooks cover it locally. In shape `none` there
is no pipeline at all, so those two together are the whole gate.

---

## Keeping image tags current

Image tags in `kubernetes/flux/deployment.yaml` are **literal pins**, and there
is **no hosted dependency bot** on `git.ericsweiss.com`. Bump a tag by editing it
on a branch, opening an MR, and merging — mirroring weisssrv's `task
maintenance:check-versions` habit. The shared CI tool versions live in the
`eric/weisssrv-lib` templates the pipeline includes: bump the library `ref:` in
`.gitlab-ci.yml` (and the pre-commit hook revs in `.pre-commit-config.yaml`) the
same deliberate, reviewed way.

---

## How secrets flow

Secret **values** never go in git. This repo ships only an `ExternalSecret`
that references a `ClusterSecretStore` the operator creates for you. Two
backends (match your wiring file — see `docs/ONBOARDING.md`):

| Backend | Use when | `remoteRef.key` |
|---|---|---|
| **1Password** (Option C, recommended) | you have Homelab vault access | prefixed item title `"<slug>: <Item>"` (+ `property: <field>`) |
| **GitLab CI/CD variables** | you're a collaborator without the vault | the CI variable name |

`kubernetes/flux/externalsecret.yaml` is the 1Password variant;
`kubernetes/flux/optional/externalsecret-gitlab.yaml` is the GitLab one. They
create the same Secret, so enable exactly one. If your app needs no secrets,
delete the file and its references. This matches weisssrv
[`docs/30`](https://git.ericsweiss.com/eric/weisssrv/-/blob/main/docs/30-multi-repo-onboarding.md).

---

## How routing works

Public is fully self-serve; internal needs one operator step.

- **Public — `<slug>.ericsweiss.com`** (default, on by default): the
  `IngressRoute` carries `external-dns.alpha.kubernetes.io/target:
  ericsweiss.com`, and external-dns creates the proxied Cloudflare record
  automatically. No Terraform, no operator action.
- **Internal — `<slug>.esweiss.com`** (opt-in): reached only on the LAN/tailnet
  via the `lan-tailscale-only` middleware. external-dns does **not** manage the
  `esweiss.com` zone, so the internal DNS name requires the operator to add an
  AdGuard rewrite (a small weisssrv MR).
- **TLS**: a **per-host** `Certificate` (ClusterIssuer `letsencrypt-prod`)
  issues into your namespace. It is per-host, not wildcard, on purpose: Let's
  Encrypt limits **duplicate certificates to 5 per week**, and the platform
  already issues the shared wildcards — a per-host cert keeps you clear of that
  limit and out of the shared secret.
- **Middlewares** (`hsts-header`, `lan-tailscale-only`) are referenced
  **cross-namespace** from the `traefik` namespace; the cluster enables
  `allowCrossNamespace`, so you don't copy them locally.

---

## Observability (by default)

- `servicemonitor.yaml` — auto-discovered by the platform Prometheus in every
  namespace. Point it at your `/metrics`; the NetworkPolicy already allows the
  scrape from the `observability` namespace.
- `prometheusrule.yaml` — down/stale alerts (read kube-state metrics, so they
  work even before your app has metrics), following the weisssrv
  `for`/`severity`/`runbook` convention.
- **Logs** ship to Loki via the Alloy DaemonSet automatically.

## Autoscaling & resilience (by default)

- `vpa.yaml` — VPA `updateMode: Initial` right-sizes the pod on natural
  restarts.
- `pdb.yaml` — a `minAvailable: 1` PodDisruptionBudget (always on) so a kured
  node-drain can't take both default replicas down at once.
- `optional/hpa.yaml` — opt-in HPA. Uncomment its line in
  `kubernetes/flux/kustomization.yaml`, drop `replicas` from the Deployment, and
  make the VPA memory-only so the two don't fight over CPU (`minReplicas: 2`
  keeps the PDB satisfiable).

---

## The operator-side wiring step (once per repo)

Before your first deploy, the operator adds one file to the weisssrv repo
(`kubernetes/clusters/weisssrv/tenants/<slug>.yaml`) that creates your
namespace, secret store, Flux `GitRepository`, and a namespace-scoped
`Kustomization`. The exact file and both checklists are in
[`docs/ONBOARDING.md`](docs/ONBOARDING.md); the canonical reference is weisssrv
[`docs/30-multi-repo-onboarding.md`](https://git.ericsweiss.com/eric/weisssrv/-/blob/main/docs/30-multi-repo-onboarding.md).

This step is the same in all three CI shapes — Flux is the deployer regardless.
Only the `GitRepository` differs when the repo lives on GitHub (a deploy key or
PAT plus a `secretRef`, if it is private):
[`docs/CI-SHAPES.md`](docs/CI-SHAPES.md#operator-wiring-for-a-github-hosted-tenant).

---

## Local development

Requires `task` (go-task), plus `kustomize`, `kubeconform`, and `yamllint` for
linting. `task --list` shows everything.

```bash
task lint            # yamllint + kustomize/kubeconform + shellcheck + doc links
task render          # print the manifests Flux will apply
task build           # docker build (needs a Dockerfile)
task flux:status     # reconcile state (read-only; needs a kubeconfig)
task flux:reconcile  # force a reconcile
task secrets:check   # ExternalSecret sync state
```

Install the pre-commit hooks (`pre-commit install`) to catch secrets and YAML
issues before you push.

### CI runner (shape `gitlab`)

Shape `github` runs on GitHub-hosted runners (no tags, Docker included, no LAN
access ever); shape `none` runs nothing. The rest of this section is shape
`gitlab` — see [`docs/CI-SHAPES.md`](docs/CI-SHAPES.md) for the comparison.

The pipeline is **tag-less**, so it runs on weisssrv's shared, non-privileged
`k8s-deploy` runner. That runner has **internet egress only** — lint,
kubeconform, secret scanning, and the container registry all work; there is
**no LAN/tailnet access and no SSH**. Deploys go through Flux, never a CI
`kubectl apply`.

The shared runner is non-privileged **and runs every job as a non-root UID**, so
it **can't build container images** (Docker-in-Docker needs `--privileged`). The
`build-image` job is therefore tagged `infrastructure` so it lands on weisssrv's
privileged runner instead — retag it if you register your own. Everything else
(lint, kubeconform, secret scanning, registry push) runs tag-less on the shared
runner. For a project that builds nothing, remove the build include and run
`weisssrv-new-project prune image-build`. See
[`docs/CONSUMING.md`](docs/CONSUMING.md).

---

## GitHub mirror (optional)

This is **not** CI shape `github` — a mirror is a read-only copy of a repo whose
CI still runs on GitLab (shape `gitlab`). If your repo *lives* on GitHub, pick
shape `github` instead ([`docs/CI-SHAPES.md`](docs/CI-SHAPES.md)).

weisssrv keeps a read-only GitHub mirror; you can do the same. It's a **GitLab
push-mirror**, configured in the UI (nothing in this repo):

1. GitHub: create an empty repo; generate a fine-grained PAT (Contents:
   Read/Write, Metadata: Read); disable Actions.
2. GitLab: **Settings → Repository → Mirroring repositories → Add** — direction
   **Push**, the GitHub URL, username + PAT, leave "mirror only protected
   branches" unchecked.

---

## Layout

```
kubernetes/flux/     # what Flux reconciles into your namespace
  deployment.yaml    #   hardened non-root app + probes + resources
  service.yaml
  ingressroute.yaml  #   public route
  certificate.yaml   #   per-host LE cert
  externalsecret.yaml#   secrets, 1Password backend
  networkpolicy.yaml #   default-deny + scoped allows
  servicemonitor.yaml
  prometheusrule.yaml
  vpa.yaml
  pdb.yaml           #   default PodDisruptionBudget (minAvailable: 1)
  kustomization.yaml #   the reconciled resource list; opt-ins are commented lines
  optional/          #   real, CI-validated add-ons Flux does NOT build:
                     #     hpa.yaml, ingressroute-internal.yaml,
                     #     certificate-internal.yaml, externalsecret-gitlab.yaml,
                     #     externalsecret-registry.yaml
Dockerfile           # placeholder service image (task build / CI build, active by default)
.dockerignore
.gitlab-ci.yml       # CI shape A: includes eric/weisssrv-lib templates @ a pinned tag
.github/workflows/   # CI shape B: the same gates as GitHub Actions
  ci.yml             #   yaml-lint, flux-lint, shellcheck, docs-link-check, secrets
  build-image.yml    #   docker build -> ghcr.io/<owner>/<repo>
  release.yml        #   semantic-release via scripts/semantic-release.py --platform github
Taskfile.yml         # local dev wrappers
scripts/rename.sh    # thin wrapper over the weisssrv-new-project CLI
scripts/select-ci.sh # keep one CI shape, drop the other two (run once at setup)
scripts/lib-cli.sh   # shared CLI resolver both wrappers source
scripts/cluster-identity.env        # the cluster this scaffold targets
scripts/apply-cluster-identity.sh   # retarget it at another cluster
scripts/check-doc-links.py  # offline Markdown link checker (docs-link-check job)
scripts/check-lib-pins.py   # every library ref matches WEISSSRV_LIB_REF
scripts/semantic-release.py # vendored release script (both CI shapes; --platform github for B)
docs/                # CI-SHAPES.md, CONSUMING.md, ARCHITECTURE.md, ONBOARDING.md,
                     #   VERSIONING.md
tests/               # the TEMPLATE's own gate (rename + CI-shape selection);
                     #   skips itself once renamed. Deleting it also means
                     #   dropping the python-tests include — docs/CONSUMING.md
.claude/             # agent settings + project-development skill
```

Shape A keeps `.gitlab-ci.yml`, shape B keeps `.github/workflows/`, shape C
keeps neither — `./scripts/select-ci.sh <shape>` does the pruning.

## License

[MIT](LICENSE).
