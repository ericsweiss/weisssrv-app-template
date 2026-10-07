# CI shapes

`ci_shape` is the one answer that changes which files a generated repo gets
outside `kubernetes/`. Three values:

| `ci_shape` | Pipeline | Ships |
|---|---|---|
| `gitlab_selfhosted` (default) | self-hosted GitLab, jobs included from `eric/weisssrv-lib` at a pinned tag | `.gitlab-ci.yml`, `.gitlab/secret-detection-ruleset.toml`, `scripts/check-lib-pins.py`, `scripts/semantic-release.py` |
| `github` | GitHub Actions, workflows vendored from the same library | `.github/workflows/{ci,manifest-gates,release}.yml` — plus `build-image.yml` when `enable_image_build` is on — and `scripts/semantic-release.py` |
| `none` | none | neither pipeline; `scripts/check-doc-links.py`, `scripts/check-kustomization.py`, `scripts/check-netpol-except-parity.py` and `scripts/check-scrape-wiring.py` still ship, and `task lint` plus the pre-commit hooks are the whole gate |

The `.gitlab/issue_templates/` and `.gitlab/merge_request_templates/` files
follow the `forge` answer rather than `ci_shape`, so a `forge: gitlab` tenant
gets them in every shape.

**Flux deploys the repo in all three.** The shape decides what *checks* a change
and where the image is built — never what applies to the cluster. Nothing under
`kubernetes/` varies by shape, and the render suite asserts that the manifests
are byte-identical across two renders that differ only in `ci_shape`.

The word the docs use for a reviewed change follows the **`forge`** answer
rather than `ci_shape`, for all three values: a repo with no pipeline still
lives somewhere, and that somewhere decides the vocabulary. `forge` defaults
from `ci_shape` and is asked next to it. A `forge: other` tenant is asked for
the word itself, so their answer is recorded and replayed on `copier update`.

The shipped issue and change-request templates exist for `forge: gitlab` only. A
`github` or `other` tenant writes their own. The GitLab merge-request template's
Summary / Changes / Testing-done / Deploy-notes skeleton is worth copying,
including the deploy-notes prompt about new secrets and cluster-side wiring.

## Job parity

| Gate | `gitlab_selfhosted` | `github` | `none` |
|---|---|---|---|
| yamllint | `yaml-lint` (library template) | `yaml-lint` job | `task yaml-lint` |
| `kustomize build` + kubeconform | `flux-lint` (library template) | `flux-lint` job | `task flux-lint` |
| ruff | `python-lint` (library template) | `python-lint` job | `task python-lint` |
| shellcheck | n/a — no GitLab counterpart; a tenant with shell scripts adds the library's `shellcheck` template | `shellcheck` job, skipped when the repo has no `.sh` files | n/a |
| Markdown link check | `docs-link-check` (library template) | `docs-link-check` job | `task doc-links` |
| Secret scanning | GitLab Secret Detection (gitleaks under the hood), findings block | gitleaks directly, same `.gitleaks.toml`, findings block | pre-commit gitleaks hook |
| Library pin gate | `lib-pin-check`, plus `task lib-pins` locally | n/a — no includes to pin | n/a |
| Egress fence (`check-netpol-except-parity.py`) | `netpol-check`, plus `task netpol` locally | `manifest-gates` job, plus `task netpol` locally | `task netpol` |
| Scrape wiring (`check-scrape-wiring.py`) | `scrape-wiring-check`, plus `task scrape-wiring` locally | `manifest-gates` job, plus `task scrape-wiring` locally | `task scrape-wiring` |
| `kustomization.yaml` resource list | `kustomization-check`, plus `task kustomization` locally | `manifest-gates` job, plus `task kustomization` locally | `task kustomization` |
| Image build | `build-image` on a privileged runner | `build-image.yml` to GHCR, push-only | `task build` locally |
| Release | `semantic-release` (library template) | `release.yml` (vendored) | by hand |
| AI review | `pr-agent-review`, created only when both keys are set | n/a | n/a |

Neither pipeline shape's kubeconform run fails on a skipped schema, so a
catalog path that moves leaves the CRs unvalidated while the job still prints
ok. The generated `task flux-lint` fails on a skip count over its
`ALLOWED_SKIPS` budget and on an empty build in every shape, and
`tests/validate_render.py` fails on any skip, so a green `task lint` is
stricter than a green pipeline. `task flux-lint` resolves CR schemas from a
pinned catalog commit; the library's job takes no such input at the pinned
`lib_ref`, so the two can disagree about what has a schema.

Both pipelined shapes drive the **same** vendored `scripts/semantic-release.py`
with `--platform {gitlab,github}`, so there is one implementation to audit
rather than one per forge.

`enable_image_build: false` means the same thing on both forges: the GitLab
shape drops the build job from `.gitlab-ci.yml`, and the GitHub shape drops
`build-image.yml` entirely rather than shipping a workflow that fires on every
push to main holding `packages: write` and does nothing. What survives either
way is `ci.yml`'s `docker-build`, a discarded build under `contents: read` — it
lives in the shared file, so it guards the Dockerfile at runtime instead, and
announces its skip when there is none.

## What the GitHub shape gives up

- **The vulnerability report and MR widget.** GitLab's managed Secret-Detection
  analyzer produces both; invoking gitleaks directly produces neither. Findings
  still fail the job in both.
- **Central tool-version control.** The GitLab shape's tool versions live in the
  library and move when the `ref:` moves. The GitHub workflows are vendored
  copies with literal versions and sha256s — a library bump is a manual
  re-vendor of `ci.yml`, `release.yml`, `build-image.yml` and the vendored
  `scripts/` helpers. `manifest-gates.yml` is this template's own and is never
  re-vendored.
- **The manifest gates do not gate the tag.** On the GitLab shape
  `netpol-check`, `scrape-wiring-check` and `kustomization-check` sit in
  `stage: lint`, ahead of `stage: release`. On GitHub they run as the sibling
  `manifest-gates.yml` workflow while `release.yml` triggers on `ci` completing,
  and `release.yml`'s own header says anything that must gate the release
  belongs inside `ci.yml` as a job. A red manifest gate does not stop a tag, so
  branch protection is what keeps one out of `main`. The library's next release
  ships a `manifest-gates` job inside `ci.yml`; the entry in
  docs/VERSIONING.md § Pending at the next library bump closes this gap and
  deletes this bullet.
- **`k8s_version`.** The vendored `ci.yml` carries the library's own literal
  (`K8S_VERSION`), because a byte-identical copy cannot take a copier answer. A
  cluster on a different Kubernetes minor edits that one line by hand, and
  re-applies the edit in the same pull request as every re-vendor: the copy
  overwrites it, and no gate in a tenant repo notices. The generated
  `docs/VERSIONING.md` says so where the tenant reads it. The GitLab shape
  takes the answer in both places it validates from.
- **`registry_host`.** The vendored build workflow publishes to
  `ghcr.io/<owner>/<repo>` from its own literals, so a GitHub tenant answering
  another registry gets an image reference nothing pushed to. Answer `ghcr.io`,
  or swap the workflow's login step and its `REGISTRY`/`IMAGE_NAME`, which makes
  the copy no longer byte-identical — record that as a deliberate fork.
- **A privileged runner is not needed.** GitHub-hosted runners ship Docker,
  which is why only the GitLab shape asks for `privileged_runner_tag`.

The image build also differs deliberately: on GitHub it is **push-only**, and a
pull request never runs `build-image.yml` at all. The Dockerfile gate lives in
`ci.yml` instead, which builds without pushing under `contents: read`. A
`pull_request` run executes the pull request's own copy of the workflow, so a
workflow holding `packages: write` must never run on one.

## Why the GitLab tenant pipeline is shaped as it is

The rendered `.gitlab-ci.yml` keeps its comments to the trap each block guards.
The reasoning behind them lives here.

**`docs-link-check` image.** The library already defaults to the full
`python:3.11`, which carries the `git` the tracked-Markdown scan shells out to.
The `python:3.13` input is an interpreter bump, not a capability fix, and the
checker raises rather than degrading when `git` is missing.

**`cpu_selector` on secret detection and the image build.** gitleaks needs
POPCNT/SSE4.2 and SIGILLs on older CPUs, so both jobs always emit a CPU node
selector. The runner validates the override against a
`<node-label-domain>/cpu=(modern|legacy)` allowlist, so an empty value is not a
no-pin escape — a runner with no such rule ignores the value instead.

**`semantic-release`.** `major_on_zero` stays false: while the tag is 0.x a
`feat!:` bumps the minor.

**`pr-agent`.** The library defaults `openai_key` and `gitlab_token` to
`$OPENAI__KEY` and `$GITLAB__PERSONAL_ACCESS_TOKEN`, so a fork merge request has
no AI review by design.

## Why the GitHub workflows are shaped as they are

`ci.yml`, `release.yml` and `build-image.yml` are byte-identical library copies;
`manifest-gates.yml` is this template's own. Their design lives here rather than
in a banner each tenant carries.

**`manifest-gates`.** The library ships `check-netpol-except-parity.py` and no
GitHub workflow that runs it; `check-scrape-wiring.py` and
`check-kustomization.py` are this template's own. This template therefore ships
the job. It holds `contents: read`, runs on pull requests, pushes to main and
`workflow_dispatch`, and drives the same three scripts the GitLab shape runs as
`netpol-check`, `scrape-wiring-check` and `kustomization-check`.

**The `kustomization.yaml` resource list.** An emptied list renders nothing,
kustomize and kubeconform both exit 0 on it, and the cluster-side Kustomization
prunes with `prune: true`, so every object in the namespace is deleted, the
PrometheusRule with it, and no alert fires. A manifest present but unlisted is
never built. The `manifest-gates` workflow and the GitLab `kustomization-check`
job both run it.

**Release trust.** `release.yml` runs on `ci` completing and holds
`contents: write`, so three conditions must all hold: the `ci` run succeeded,
its event was a push, and its head repository is this repository. A fork pull
request's `ci` run also matches `branches: [main]`, and these conditions are
what keep it out. There is deliberately no `workflow_dispatch`: a dispatch runs
the workflow definition from the selected ref, so a branch that deletes those
conditions would run its own code with write access. Re-running the previous
run replays the original payload, which is the recovery path.

**`release.yml`'s concurrency is job-scoped.** Workflow-scoped concurrency
would let a later run whose conditions skip it evict a queued run that would
have released. The other three workflows are workflow-scoped and cancel in
progress, which is what you want for a gate.

## Operator wiring for a GitHub-hosted tenant

The wiring file is the same in every shape — Flux is the deployer regardless.
Only the `GitRepository` differs: use `https://github.com/<owner>/<repo>` for a
public repo, or the `ssh://` form plus a `secretRef` naming a deploy-key secret
for a private one. The rendered `docs/ONBOARDING.md` in the generated repo
carries the whole file with that repo's names already substituted.

## Changing shape later

`copier update --data ci_shape=github` re-renders: the new shape's files appear
and the old shape's are removed by the update's own diff. Review it as you would
any merge request — a repository that has diverged far from the template will
have conflicts to resolve.
