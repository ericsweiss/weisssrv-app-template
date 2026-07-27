# CLAUDE.md

> **Before making any change in this repo, invoke the `project-development`
> skill** (`.claude/skills/project-development/SKILL.md`) and follow it.

Guidance for Claude Code (and other agents) working in a **weisssrv cluster
tenant** repo created from `weisssrv-app-template`.

## What this repo is

A single service that deploys to the weisssrv homelab k3s cluster. **Flux owns
all Kubernetes state** under `kubernetes/flux/` — you edit YAML, open a merge
request, and on merge to `main` the operator-side Flux `Kustomization`
reconciles this repo into your namespace. There is no `kubectl apply` /
`helm upgrade` in the normal workflow.

The generic CI (lint / flux-lint / shellcheck / docs-link-check /
secret-detection) is **included from the shared `eric/weisssrv-lib` library** at
a pinned tag in `.gitlab-ci.yml` — do not re-inline those jobs; change behavior
by bumping the library `ref:` or adjusting the `inputs:`. See
[`docs/CONSUMING.md`](docs/CONSUMING.md).

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

- The template ships app-slug and GitLab-group placeholders. Run
  `./scripts/rename.sh <app> <group>` (a thin wrapper over the
  `weisssrv-new-project` CLI's `rename`) once, then `grep -rn changeme- .` to
  catch any leftovers before shipping. The CLI also `prune`s / `wire`s optional
  components structurally — see [`docs/CONSUMING.md`](docs/CONSUMING.md).
- Register every new manifest in `kubernetes/flux/kustomization.yaml`.
- `task lint` (yamllint + kustomize build + kubeconform) mirrors CI — run it
  before opening an MR.
- Version pins: image tags are literal — bump them yourself in an MR (there is
  no hosted dependency bot). The shared CI tool versions (kubeconform, kustomize)
  are owned by the `eric/weisssrv-lib` templates the pipeline includes — bump the
  library `ref:` in `.gitlab-ci.yml` to move them; pre-commit hook revs live in
  `.pre-commit-config.yaml`.
- Follow the shipped manifests as the pattern rather than inventing new shapes.

## Canonical platform docs (authoritative — on git.ericsweiss.com)

Do not copy platform detail into this repo; link to the source:

- Multi-repo onboarding — https://git.ericsweiss.com/eric/weisssrv/-/blob/main/docs/30-multi-repo-onboarding.md
- Flux day-2 operations — https://git.ericsweiss.com/eric/weisssrv/-/blob/main/docs/29-flux-operations.md
- CI/CD runners & network boundaries — https://git.ericsweiss.com/eric/weisssrv/-/blob/main/docs/13-ci-cd.md
- Autoscaling (VPA/HPA) — https://git.ericsweiss.com/eric/weisssrv/-/blob/main/docs/33-autoscaling.md

## Repo-local docs

- [`docs/CONSUMING.md`](docs/CONSUMING.md) — the two instantiation paths, library
  consumption + bumping, optional-enablement toggles, image build, BYO-keys.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — request/secret/ingress flow.
- [`docs/ONBOARDING.md`](docs/ONBOARDING.md) — tenant + operator checklists.
