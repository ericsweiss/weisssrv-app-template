# CI shapes

`ci_shape` is the one answer that changes which files a generated repo gets
outside `kubernetes/`. Three values:

| `ci_shape` | Pipeline | Ships |
|---|---|---|
| `gitlab_selfhosted` (default) | self-hosted GitLab, jobs included from `eric/weisssrv-lib` at a pinned tag | `.gitlab-ci.yml`, `.gitlab/`, `scripts/check-lib-pins.py`, `scripts/semantic-release.py` |
| `github` | GitHub Actions, workflows vendored from the same library | `.github/workflows/{ci,release}.yml` — plus `build-image.yml` when `enable_image_build` is on — and `scripts/semantic-release.py` |
| `none` | none | neither; `task lint` and the pre-commit hooks are the whole gate |

**Flux deploys the repo in all three.** The shape decides what *checks* a change
and where the image is built — never what applies to the cluster. Nothing under
`kubernetes/` varies by shape, and the render suite asserts that the manifests
are byte-identical across two renders that differ only in `ci_shape`.

The GitLab issue and merge-request templates are forge metadata rather than CI,
so shape `none` keeps them (a repo without a pipeline may still live on GitLab);
shape `github` does not.

## Job parity

| Gate | `gitlab_selfhosted` | `github` | `none` |
|---|---|---|---|
| yamllint | `yaml-lint` (library template) | `yaml-lint` job | `task yaml-lint` |
| `kustomize build` + kubeconform | `flux-lint` (library template) | `flux-lint` job | `task flux-lint` |
| ruff | `python-lint` (library template) | `python-lint` job | `task python-lint` |
| Markdown link check | `docs-link-check` (library template) | `docs-link-check` job | `task doc-links` |
| Secret scanning | GitLab Secret Detection (gitleaks under the hood), findings block | gitleaks directly, same `.gitleaks.toml`, findings block | pre-commit gitleaks hook |
| Library pin gate | `lib-pin-check`, plus `task lib-pins` locally | n/a — no includes to pin | n/a |
| Image build | `build-image` on a privileged runner | `build-image.yml` to GHCR, push-only | `task build` locally |
| Release | `semantic-release` (library template) | `release.yml` (vendored) | by hand |
| AI review | `pr-agent-review`, created only when both keys are set | n/a | n/a |

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
  re-vendor of the three files.
- **`k8s_version`.** The vendored `ci.yml` carries the library's own literal
  (`K8S_VERSION`), because a byte-identical copy cannot take a copier answer. A
  cluster on a different Kubernetes minor edits that one line by hand; the
  GitLab shape takes the answer in both places it validates from.
- **A privileged runner is not needed.** GitHub-hosted runners ship Docker,
  which is why only the GitLab shape asks for `privileged_runner_tag`.

The image build also differs deliberately: on GitHub it is **push-only**, with
the pull-request Dockerfile gate living in `ci.yml` (build, no push, under
`contents: read`). A `pull_request` run executes the pull request's own copy of
the workflow, so a workflow holding `packages: write` must never run on one.

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
