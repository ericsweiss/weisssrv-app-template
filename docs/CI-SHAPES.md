# CI shapes

`ci_shape` is the one answer that changes which files a generated repo gets
outside `kubernetes/`. Three values:

| `ci_shape` | Pipeline | Ships |
|---|---|---|
| `gitlab_selfhosted` (default) | self-hosted GitLab, jobs included from `eric/weisssrv-lib` at a pinned tag | `.gitlab-ci.yml`, `.gitlab/secret-detection-ruleset.toml`, `scripts/check-lib-pins.py` with `scripts/lib-ref-sites.yml`, `scripts/check-ci-include-job-names.py`, `scripts/semantic-release.py` |
| `github` | GitHub Actions, workflows vendored from the same library | `.github/workflows/{ci,release}.yml` — plus `build-image.yml` when `enable_image_build` is on — and `scripts/semantic-release.py` |
| `none` | none | neither pipeline; `scripts/check-comment-length.py`, `scripts/check-doc-links.py`, `scripts/check-kustomization.py`, `scripts/check-netpol-except-parity.py`, `scripts/check-scrape-wiring.py` and `scripts/flux-lint.sh` still ship, and `task lint` plus the pre-commit hooks are the whole gate |

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
| `kustomize build` + kubeconform | `flux-lint` (library template), running the repo's own `scripts/flux-lint.sh` | `flux-lint` job, the same checks inline | `task flux-lint`, the same `scripts/flux-lint.sh` |
| ruff | `python-lint` (library template) | `python-lint` job | `task python-lint` |
| shellcheck | n/a — no GitLab counterpart; a tenant with shell scripts adds the library's `shellcheck` template | `shellcheck` job, skipped when the repo has no `.sh` files | n/a |
| Markdown link check | `lint-docs-links` (library template) | `lint-docs-links` job | `task doc-links` |
| Comment convention (`check-comment-length.py`) | `comment-length` (library template), plus `task comment-length` locally | `comment-length` job, plus `task comment-length` locally | `task comment-length` |
| Secret scanning | GitLab Secret Detection (gitleaks under the hood), findings block | gitleaks directly, same `.gitleaks.toml`, findings block | pre-commit gitleaks hook |
| Library pin gate | `lib-pin-check`, plus `task lib-pins` locally — the pins and `scripts/lib-ref-sites.yml`'s sites against the declared `lib_ref` | n/a — no includes to pin | n/a |
| Optional-need gate (`check-ci-include-job-names.py`) | `ci-job-names-check`, plus `task ci-job-names` locally | n/a — a workflow `needs:` names a job in the same file | n/a |
| Egress fence (`check-netpol-except-parity.py`) | `netpol-check`, plus `task netpol` locally | `ci.yml`'s `manifest-gates` job, plus `task netpol` locally | `task netpol` |
| Scrape wiring (`check-scrape-wiring.py`) | `scrape-wiring-check`, plus `task scrape-wiring` locally | `ci.yml`'s `manifest-gates` job — `--namespace-from-tree`, not `--namespace`, so a monitor scoped with `matchNames` reds it — plus `task scrape-wiring` locally | `task scrape-wiring` |
| `kustomization.yaml` resource list | `kustomization-check`, plus `task kustomization` locally | `ci.yml`'s `manifest-gates` job, plus `task kustomization` locally | `task kustomization` |
| Database tree (`kubernetes/db`, `database: postgres`) | `db-manifest-check` (kustomization list, egress fence, and CNPG safeguards and storage class over the `kustomize build` output) but no kubeconform; run `task database` and `task flux-lint` locally | nothing in CI; run `task database` and `task flux-lint` locally, and the pre-commit `database` hook | `task database` and `task flux-lint`, and the pre-commit `database` hook |
| Image build | `build-image` on a privileged runner | `build-image.yml` to GHCR, push-only | `task build` locally |
| Release | `semantic-release` (library template) | `release.yml` (vendored) | by hand |
| AI review | `pr-agent-review`, created only when both keys are set | n/a | n/a |

Both pipeline shapes fail a kubeconform run whose skip count exceeds their
budget: the GitLab `flux-lint` include defaults `allowed_skips` to 0, and the
vendored `ci.yml` reads repository variable `ALLOWED_SKIPS`. The generated
`task flux-lint` fails on the same budget and on an empty build in every shape,
and `tests/validate_render.py` fails on any skip, so a green `task lint` is
still the strictest of the three. `task flux-lint` resolves CR schemas from the
catalog commit `Taskfile.yml` pins, and the GitLab shape passes that same commit
to the include's `crd_catalog_ref`, so both read one schema set.

The empty-build guard, the skipped-schema guard and the integer check on the
skip budget exist twice: in the library's `flux-lint` simple arm, and in the
rendered `Taskfile.yml`'s shell because a CI template cannot serve a local task.
`tests/test_render.py` is the only thing holding the two in step.

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
  `scripts/` helpers.
- **The manifest gates gate the tag on both pipeline shapes.** On the GitLab
  shape `netpol-check`, `scrape-wiring-check` and `kustomization-check` sit in
  `stage: lint`, ahead of `stage: release`. On GitHub the three run as the
  vendored `ci.yml`'s `manifest-gates` job, and `release.yml` triggers on `ci`
  completing, so a red gate stops the tag there too.
- **`k8s_version`.** The vendored `ci.yml` cannot take a copier answer, so it
  reads repository variable `K8S_VERSION` and falls back to the library's own
  literal. A cluster on a different Kubernetes minor sets that variable;
  `flux-lint` warns on every run while it is unset. `MANIFEST_ROOT`,
  `ALLOWED_SKIPS` and `CRD_CATALOG_REF` work the same way. The generated
  `docs/VERSIONING.md` and `docs/ONBOARDING.md` say so where the tenant reads
  them. The GitLab shape takes the answer in both places it validates from.
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

**`lint-docs-links` image.** The library's default is the full `python:3.14`,
which carries the `git` the tracked-Markdown scan shells out to, and the
checker raises rather than degrading when `git` is missing; no `image` input
restates it. The job name is the library's own default, so no `job_name` input
restates it either.

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

`ci.yml`, `release.yml` and `build-image.yml` are byte-identical library
copies, and they are every workflow this shape ships. Their design lives here
rather than in a banner each tenant carries.

**`manifest-gates`.** The vendored `ci.yml`'s job drives the same three scripts
the GitLab shape runs as `netpol-check`, `scrape-wiring-check` and
`kustomization-check`, over `$MANIFEST_ROOT`, which defaults to the rendered
tenant's `kubernetes/flux`. It derives the scrape gate's namespace with
`--namespace-from-tree` because a byte-identical copy cannot carry a tenant's
answer, and the rendered tree ships no `Namespace` manifest — the platform
operator owns the namespace — so a monitor scoped with `matchNames` reds that
job while the three template-owned callers pass it. The generated
`docs/ARCHITECTURE.md` tells the tenant to scope with `any: false` instead.

**The `kustomization.yaml` resource list.** An emptied list renders nothing,
kustomize and kubeconform both exit 0 on it, and the cluster-side Kustomization
prunes with `prune: true`, so every object in the namespace is deleted, the
PrometheusRule with it, and no alert fires. A manifest present but unlisted is
never built. The GitLab `kustomization-check` job and the GitHub shape's
`manifest-gates` job both run it.

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
