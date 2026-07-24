---
name: project-development
description: >-
  Develop and ship a service in this repo onto the weisssrv k3s cluster. Use
  when working on the app, its Kubernetes manifests under kubernetes/flux, its
  CI, secrets, routing, or observability. Covers the local loop, Flux debugging,
  the secret/routing conventions, container registry auth, and links to the
  canonical weisssrv docs.
---

# Developing a weisssrv tenant service

This repo is a **tenant** of the weisssrv homelab cluster. Flux (GitOps) owns
everything under `kubernetes/flux/`: you edit YAML, open an MR, and on merge to
`main` the operator-side Flux `Kustomization` reconciles this repo into your
namespace. There is no `kubectl apply` / `helm upgrade` in the normal flow.

## Golden rules

- **Never `kubectl apply` or `helm upgrade` to deploy.** Flux reverts drift.
  Preview locally with `kustomize build kubernetes/flux`; deploy by merging.
- **Never push to `main`.** Branch → MR → merge. CI runs on the MR.
- **Never commit secrets.** Secret *values* live in 1Password (or GitLab CI/CD
  variables); this repo only holds `ExternalSecret` manifests that reference
  them. See `kubernetes/flux/externalsecret.yaml`.
- **Stay inside your one namespace.** A tenant owns exactly one namespace; the
  Flux service account is RBAC-scoped to it. Manifests targeting another
  namespace fail to apply.
- The tree ships app-slug and GitLab-group placeholders. Run
  `./scripts/rename.sh <app> <group>` (a wrapper over the `weisssrv-new-project`
  CLI's `rename`; the CLI also `prune`s / `wire`s optional components — see
  `docs/CONSUMING.md`), or `grep -rn changeme- .` for leftovers before shipping.

## Local loop

```bash
task lint                 # yamllint + kustomize build + kubeconform
task render               # see exactly what Flux will apply
task build                # docker build (needs a Dockerfile)
```

CI mirrors `task lint`. Fix lint locally before opening the MR.

## Deploy + verify

Merging to `main` triggers reconcile (a ~1-minute git poll is the fallback).
With a kubeconfig from the operator on `KUBECONFIG` (read-only checks only):

```bash
task flux:status          # flux get kustomization <slug>
task flux:reconcile       # force an immediate reconcile
task secrets:check        # ExternalSecret sync state in your namespace
kubectl get pods -n <slug>
```

If a change didn't take: check the Kustomization is `Ready`, then the workload.
Common causes — a failing `kustomize build` (CI would have caught it), an
`ExternalSecret` stuck because the store isn't wired yet, or an image tag that
doesn't exist in the registry.

## Conventions (already wired in `kubernetes/flux/`)

- **Routing.** Public `*.ericsweiss.com` is self-serve: the `IngressRoute` has
  the `external-dns.alpha.kubernetes.io/target: ericsweiss.com` annotation and
  external-dns creates the proxied Cloudflare record automatically. Internal
  `*.esweiss.com` is NOT auto-provisioned — it needs the operator to add an
  AdGuard rewrite (a weisssrv MR). Platform middlewares (`hsts-header`,
  `lan-tailscale-only`) are referenced cross-namespace from `traefik`.
- **TLS.** A per-host `Certificate` (ClusterIssuer `letsencrypt-prod`) issues
  into your namespace; Traefik reads the secret only from the route's namespace.
- **Secrets.** `ExternalSecret` → `ClusterSecretStore` (`onepassword-<slug>` or
  `gitlab-<slug>`, created operator-side). For 1Password, `remoteRef.key` is the
  prefixed item title `"<slug>: <Item>"` and `remoteRef.property` is the field.
- **Observability.** Ship a `ServiceMonitor` (auto-discovered cluster-wide) and
  a `PrometheusRule` for down/stale alerts. Container logs go to Loki via Alloy
  automatically — no config needed.
- **Autoscaling.** VPA (`Initial`) right-sizes by default; `hpa.yaml` is an
  opt-in HPA + PDB. If you enable the HPA, drop `replicas` from the Deployment
  and make the VPA memory-only.
- **Scheduling / hardening.** Pods run non-root with a read-only root FS and
  soft NAS-avoidance. To pin to the storage node for zvol-backed data, use a
  required hostname affinity plus the `esweiss.com/nas` toleration.

## Container registry

`registry.git.ericsweiss.com/<group>/<app>` is your image registry. Point
`deployment.yaml`'s `image:` at a tag there (literal tag; bump it yourself in an
MR — there is no hosted dependency bot) or at any upstream image.

A placeholder `Dockerfile` ships. The shared runner is non-privileged and can't
run Docker-in-Docker, so the CI image build is **opt-in**: uncomment the
library's `ci/build/docker-build.yml` include in `.gitlab-ci.yml` and retag it to
a privileged runner. By default, build locally with `task build` and push (log in
with a project deploy token or a PAT), then set `image:`. For an upstream image,
`weisssrv-new-project prune image-build` drops the Dockerfile. See
`docs/CONSUMING.md`.

## Runner limits

CI runs on the shared, non-privileged `k8s-deploy` runner: internet egress only,
no LAN/tailnet, no SSH, no Docker-in-Docker, and every job runs as a non-root
UID. The generic jobs are included from `eric/weisssrv-lib` with `tags: []` so
they land here. Lint, kubeconform, secret scanning, and registry push all work;
**building container images needs a PRIVILEGED runner** (the opt-in build job —
retag it, or build locally with `task build`), as does anything needing LAN
access or privileged Docker.

## Shared library + consumption (this repo)

- `docs/CONSUMING.md` — the two instantiation paths, library consumption +
  bumping, optional-enablement toggles, the image build, and BYO-keys.
- Library include contract (each CI template's inputs):
  https://git.ericsweiss.com/eric/weisssrv-lib/-/blob/main/docs/INCLUDE-CONTRACT.md
- Library versioning / tag pinning:
  https://git.ericsweiss.com/eric/weisssrv-lib/-/blob/main/docs/VERSIONING.md

## Canonical weisssrv docs (authoritative — read these for platform detail)

- Multi-repo onboarding (the operator wiring you depend on):
  https://git.ericsweiss.com/eric/weisssrv/-/blob/main/docs/30-multi-repo-onboarding.md
- Flux day-2 operations (reconcile, suspend, secret rotation, debugging):
  https://git.ericsweiss.com/eric/weisssrv/-/blob/main/docs/29-flux-operations.md
- CI/CD runners and network boundaries:
  https://git.ericsweiss.com/eric/weisssrv/-/blob/main/docs/13-ci-cd.md
- Autoscaling (VPA/HPA tiers):
  https://git.ericsweiss.com/eric/weisssrv/-/blob/main/docs/33-autoscaling.md
