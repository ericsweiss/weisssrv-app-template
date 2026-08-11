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
  CLI's `rename`; the CLI also `prune`s optional components — see
  `docs/CONSUMING.md`) and `./scripts/select-ci.sh <shape>` (a wrapper over the
  same CLI's `prune ci:<shape>`), then `grep -rn 'changeme[-]' .` for leftovers
  before shipping (the bracket keeps the pattern from matching this line).
- **The cluster identity is hard-wired.** `ericsweiss.com`, `esweiss.com`, the
  `esweiss.com/*` node labels, `registry.git.ericsweiss.com` and the
  `infrastructure` runner tag are literals, not variables. Do not generalise
  them; `docs/ONBOARDING.md` § Step 0 lists every one for a repo being pointed
  at a different cluster.

## Local loop

```bash
task lint                 # yamllint + kustomize/kubeconform + shellcheck + ruff + doc links
task render               # see exactly what Flux will apply
task build                # docker build (needs a Dockerfile)
```

`task lint` mirrors the CI lint stage, including the second kubeconform pass over
`kubernetes/flux/optional/`. Secret detection is pipeline-only; `pre-commit
install` covers it locally. Fix lint before opening the MR.

## `tests/` is the template's gate, not yours

`tests/` exercises the scaffold's own wrappers and skips itself once the repo is
renamed. Deleting it is a **four**-part edit, because two jobs read the path:
pytest exits 4 on a missing directory and ruff errors on a target that does not
exist. Remove together: (1) `tests/`, (2) the `/ci/test/python-tests.yml`
include, (3) the `python-tests:` variables override, and (4) `tests` from the
`python-lint` include's `targets:` — and from the same list in `Taskfile.yml`'s
`python-lint` task and `.github/workflows/ci.yml`'s `python-lint` step. If you
keep it, the operator must allowlist this project on
`eric/weisssrv-lib`'s CI/CD job-token list, since the job clones the internal
library. Both paths: `docs/CONSUMING.md` § Removing the template's gate.

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
- **Autoscaling.** VPA (`Initial`) right-sizes by default and the PDB is always
  on; `optional/hpa.yaml` is the opt-in HPA. Enabling it means uncommenting its
  line in `kubernetes/flux/kustomization.yaml`, dropping `replicas` from the
  Deployment, and making the VPA memory-only.
- **Opt-in add-ons.** `kubernetes/flux/optional/` holds real, CI-validated
  manifests Flux does not build (HPA, internal route + cert, the GitLab secret
  backend, the registry pull secret). Enabling one is uncommenting its line in
  `kubernetes/flux/kustomization.yaml`; each file's header carries the rest.
- **Scheduling / hardening.** Pods run non-root with a read-only root FS and
  soft NAS-avoidance. To pin to the storage node for zvol-backed data, use a
  required hostname affinity plus the `esweiss.com/nas` toleration.

## Container registry

`registry.git.ericsweiss.com/<group>/<app>` is your image registry in shape
`gitlab` (in shape `github` it is `ghcr.io/<owner>/<repo>`, built by
`.github/workflows/build-image.yml`). Point `deployment.yaml`'s `image:` at a tag
there (literal tag; bump it yourself in an MR — there is no hosted dependency
bot) or at any upstream image. The shipped `:REPLACE-ME` tag is a placeholder no
registry has, so the first reconcile fails until you replace it.

A **private** registry — a GitLab project registry by default, or a private GHCR
package — needs an `imagePullSecret` in your namespace: an `ExternalSecret` of
type `kubernetes.io/dockerconfigjson` plus `imagePullSecrets:` on the Deployment.
That is an operator-assisted step (`docs/ONBOARDING.md` § O2b); making the
package public avoids it.

A placeholder `Dockerfile` ships and the CI **builds the service image by
default** (the `build-image` job, the library's `ci/build/docker-build.yml`).
Docker-in-Docker can't run on the shared non-privileged runner, so that job is
tagged `infrastructure` (a privileged runner) — retag it if you register your
own. Replace the placeholder Dockerfile with your app's real build, and point
`image:` at the pushed tag (`$CI_REGISTRY_IMAGE:<short-sha>`). You can also build
locally with `task build`. For an upstream image (no build), remove the build
include and run `weisssrv-new-project prune image-build`. See `docs/CONSUMING.md`.

## CI shape (check this first)

CI ships in three shapes and a project keeps exactly one. Look before you edit:

| Present | Shape | What runs the gates |
|---|---|---|
| `.gitlab-ci.yml` | `gitlab` (default) | self-hosted GitLab, including `eric/weisssrv-lib` templates at a pinned tag |
| `.github/workflows/` | `github` | GitHub Actions — a **vendored** copy of the same gates, same tool versions + sha256s |
| neither | `none` | nothing: `task lint` + pre-commit are the whole gate |

Flux deploys in all three, so `kubernetes/flux/` never varies by shape. Shape is
chosen once at setup with `./scripts/select-ci.sh <shape>`. In shape `github`,
a library bump is a manual re-vendor (there are no reusable Actions workflows in
the library) — call that out in the PR. Full parity table, what a github.com
repo gives up, and the Flux-only operator wiring (deploy key / PAT + `secretRef`)
are in `docs/CI-SHAPES.md`.

## Runner limits (shape `gitlab`)

CI runs on the shared, non-privileged `k8s-deploy` runner: internet egress only,
no LAN/tailnet, no SSH, no Docker-in-Docker, and every job runs as a non-root
UID. The lint/validate/security jobs are included from `eric/weisssrv-lib` with
`tags: []` so they land here. Lint, kubeconform, secret scanning, and registry
push all work; **building container images needs a PRIVILEGED runner** — the
`build-image` job is therefore tagged `infrastructure` (retag it, or build
locally with `task build`), as is anything needing LAN access or privileged
Docker.

## Shared library + consumption (this repo)

- `docs/CI-SHAPES.md` — the three CI shapes, the one-command selector, the
  GitLab↔GitHub job-parity table, and the operator wiring for a GitHub-hosted
  tenant.
- `docs/CONSUMING.md` — the two instantiation paths, library consumption +
  bumping, the included-job table, optional-enablement toggles, the image build,
  and BYO-keys.
- `docs/ONBOARDING.md` — cluster-identity literals, then the tenant and operator
  checklists (secret backend, registry pull credential, DNS, storage, SSO).
- `docs/VERSIONING.md` — what the release job reads from commit subjects, and
  what MAJOR/MINOR/PATCH mean here.
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
