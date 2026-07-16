# CLAUDE.md

Guidance for Claude Code (and other agents) working in a **weisssrv cluster
tenant** repo created from `weisssrv-project-template`.

## What this repo is

A single service that deploys to the weisssrv homelab k3s cluster. **Flux owns
all Kubernetes state** under `kubernetes/flux/` — you edit YAML, open a merge
request, and on merge to `main` the operator-side Flux `Kustomization`
reconciles this repo into your namespace. There is no `kubectl apply` /
`helm upgrade` in the normal workflow.

A task-oriented walkthrough lives in the `project-development` skill
(`.claude/skills/project-development/SKILL.md`) — read it for the local loop,
Flux debugging, and the secret/routing/observability conventions. This file is
the short standing-rules version.

## Hard rules

- **Never push to `main`.** Every change ships via a feature branch + merge
  request, even one-liners.
- **Never `kubectl apply` / `helm upgrade` to deploy.** Flux reverts drift.
  Preview with `kustomize build kubernetes/flux`; deploy by merging.
- **Never commit secrets.** Values live in 1Password or GitLab CI/CD variables;
  this repo holds only `ExternalSecret` manifests referencing them. For the
  1Password backend, `remoteRef.key` is the prefixed item title `"<slug>:
  <Item>"` and `remoteRef.property` is the field — no `op://`, no item IDs.
- **Stay in your one namespace.** The Flux service account is RBAC-scoped to it;
  manifests targeting another namespace fail to apply.
- **No `latest`/floating image tags, no privileged/root containers.** The
  namespace enforces Pod Security Admission `baseline` (and warns on
  `restricted`); keep `runAsNonRoot`, dropped capabilities, and a read-only root
  filesystem.

## Conventions

- The placeholder token is `changeme-app` / `changeme-group`. Run
  `./scripts/rename.sh <app> <group>` once, and grep for leftovers before
  shipping.
- Register every new manifest in `kubernetes/flux/kustomization.yaml`.
- `task lint` (yamllint + kustomize build + kubeconform) mirrors CI — run it
  before opening an MR.
- Version pins: image tags are literal (Renovate bumps them). Pinned CI tool
  versions live in `.gitlab-ci.yml`; pre-commit hook revs in
  `.pre-commit-config.yaml`.
- Follow the shipped manifests as the pattern rather than inventing new shapes.

## Canonical platform docs (authoritative — on git.ericsweiss.com)

Do not copy platform detail into this repo; link to the source:

- Multi-repo onboarding — https://git.ericsweiss.com/eric/weisssrv/-/blob/main/docs/30-multi-repo-onboarding.md
- Flux day-2 operations — https://git.ericsweiss.com/eric/weisssrv/-/blob/main/docs/29-flux-operations.md
- CI/CD runners & network boundaries — https://git.ericsweiss.com/eric/weisssrv/-/blob/main/docs/13-ci-cd.md
- Autoscaling (VPA/HPA) — https://git.ericsweiss.com/eric/weisssrv/-/blob/main/docs/33-autoscaling.md

## Repo-local docs

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — request/secret/ingress flow.
- [`docs/ONBOARDING.md`](docs/ONBOARDING.md) — tenant + operator checklists.
