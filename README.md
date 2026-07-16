# weisssrv-project-template

A forkable GitLab project template for services that deploy to the **weisssrv**
homelab k3s cluster. Create from it and you get, on day one:

- a hardened, non-root **Deployment + Service**,
- a **public HTTPS route** (`<app>.ericsweiss.com`) that provisions its own DNS
  and TLS,
- **secret wiring** via External Secrets (1Password or GitLab CI/CD variables),
- **default-deny NetworkPolicies**, a **ServiceMonitor**, down/stale **alerts**,
  and a **VPA** — observability and autoscaling without extra work,
- **CI** that builds/pushes your image, lints, schema-validates the manifests,
  and scans for secrets.

Flux (GitOps) does the deploying: you edit YAML, open a merge request, and on
merge to `main` the cluster reconciles this repo into your namespace. There is
no `kubectl apply` in the normal flow.

> New here? The agent skill in `.claude/skills/project-development/` and
> [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) explain how a tenant app rides
> the platform. Operator/tenant checklists are in
> [`docs/ONBOARDING.md`](docs/ONBOARDING.md).

---

## Quick start

### 1. Create the project

In GitLab: **New project → Create from template → (this template)**. Clone it.

### 2. Rename (the 3 things you set)

```bash
./scripts/rename.sh <app-slug> <gitlab-group>
```

This replaces the single placeholder token `changeme-app` / `changeme-group`
everywhere. The three things you're really setting:

1. **App slug** — also your Kubernetes namespace and Flux Kustomization name.
   Keep it a valid DNS label (`recipe-box`, not `Recipe_Box`).
2. **Public host** — defaults to `<slug>.ericsweiss.com` in
   `kubernetes/flux/ingressroute.yaml` and `certificate.yaml`.
3. **Internal host** (optional) — `<slug>.esweiss.com`; uncomment the internal
   `IngressRoute` and `Certificate` and request the operator DNS step.

### 3. Add your build logic

Point `kubernetes/flux/deployment.yaml`'s `image:` at any image, **or** drop a
`Dockerfile` at the repo root — the CI `build-image` job activates
automatically and pushes to `registry.git.ericsweiss.com/<group>/<slug>`
(tagged by commit SHA on MRs; `:latest` on `main`; `:<tag>` on git tags). Image
tags are **literal pins** — [Renovate](renovate.json) keeps them current
(there's no Flux `${var}` substitution for tenant repos).

The build uses **kaniko** (daemonless, unprivileged) because the shared runner
can't run Docker-in-Docker — see [CI runner](#ci-runner). It runs jobs as a
non-root UID, so kaniko suits Dockerfiles that don't modify root-owned base-image
files; for anything heavier, build/push the image from a privileged environment
(your workstation, GitHub Actions, …) and just set `image:`.

### 4. Ship

```bash
task lint            # yamllint + kustomize build + kubeconform (same as CI)
git switch -c my-change && git commit -am "feat: ..." && git push -u origin my-change
```

Open the MR. On merge, Flux reconciles — assuming the operator has wired your
repo once (below).

---

## How secrets flow

Secret **values** never go in git. This repo ships only an `ExternalSecret`
that references a `ClusterSecretStore` the operator creates for you. Two
backends (match your wiring file — see `docs/ONBOARDING.md`):

| Backend | Use when | `remoteRef.key` |
|---|---|---|
| **1Password** (Option C, recommended) | you have Homelab vault access | prefixed item title `"<slug>: <Item>"` (+ `property: <field>`) |
| **GitLab CI/CD variables** | you're a collaborator without the vault | the CI variable name |

`kubernetes/flux/externalsecret.yaml` ships both variants (1Password active,
GitLab commented). If your app needs no secrets, delete the file and its
references. This matches weisssrv
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

## Autoscaling (by default)

- `vpa.yaml` — VPA `updateMode: Initial` right-sizes the pod on natural
  restarts.
- `hpa.yaml` — opt-in HPA + PodDisruptionBudget. Enable it, drop `replicas` from
  the Deployment, and make the VPA memory-only so the two don't fight over CPU.

---

## The operator-side wiring step (once per repo)

Before your first deploy, the operator adds one file to the weisssrv repo
(`kubernetes/clusters/weisssrv/tenants/<slug>.yaml`) that creates your
namespace, secret store, Flux `GitRepository`, and a namespace-scoped
`Kustomization`. The exact file and both checklists are in
[`docs/ONBOARDING.md`](docs/ONBOARDING.md); the canonical reference is weisssrv
[`docs/30-multi-repo-onboarding.md`](https://git.ericsweiss.com/eric/weisssrv/-/blob/main/docs/30-multi-repo-onboarding.md).

---

## Local development

Requires `task` (go-task), plus `kustomize`, `kubeconform`, and `yamllint` for
linting. `task --list` shows everything.

```bash
task lint            # what CI runs
task render          # print the manifests Flux will apply
task build           # docker build (needs a Dockerfile)
task flux:status     # reconcile state (read-only; needs a kubeconfig)
task flux:reconcile  # force a reconcile
task secrets:check   # ExternalSecret sync state
```

Install the pre-commit hooks (`pre-commit install`) to catch secrets and YAML
issues before you push.

### CI runner

The pipeline is **tag-less**, so it runs on weisssrv's shared, non-privileged
`k8s-deploy` runner. That runner has **internet egress only** — build, lint,
kubeconform, and the container registry all work; there is **no LAN/tailnet
access and no SSH**. Deploys go through Flux, never a CI `kubectl apply`.

Because the runner is non-privileged (no Docker-in-Docker) and runs jobs as a
non-root UID, the `build-image` job uses **kaniko** rather than `docker build`.
Local `task build` still uses your workstation's Docker daemon.

---

## GitHub mirror (optional)

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
  ingressroute.yaml  #   public route (+ commented internal variant)
  certificate.yaml   #   per-host LE cert (+ commented internal)
  externalsecret.yaml#   1Password (active) / GitLab (commented) backends
  networkpolicy.yaml #   default-deny + scoped allows
  servicemonitor.yaml
  prometheusrule.yaml
  vpa.yaml
  hpa.yaml           #   opt-in autoscaling (commented)
  kustomization.yaml
.gitlab-ci.yml       # build -> lint -> validate -> security -> ai-review
Taskfile.yml         # local dev wrappers
scripts/rename.sh    # placeholder replacement
docs/                # ARCHITECTURE.md, ONBOARDING.md
.claude/             # agent settings + project-development skill
```

## License

[MIT](LICENSE).
