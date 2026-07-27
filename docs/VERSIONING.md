# Versioning & release tags

This repository wears two hats, and they version differently:

1. **As a scaffold** — the thing you clone or fork to start a tenant app. Its
   releases are `vMAJOR.MINOR.PATCH` tags on *this* repository, cut by the
   `release` stage in [`.gitlab-ci.yml`](../.gitlab-ci.yml).
2. **As your project** — once you have run
   `./scripts/rename.sh <app-slug> <gitlab-group>`, the same pipeline keeps
   running in *your* repository and starts tagging *your* service. That is the
   more common case, and it is covered at the bottom of this page.

Both are driven by the same job and the same conventional-commit rules. What
differs is what a bump *means*, because the two have different public APIs.

## The public API of a scaffold

A library's API is its functions. A scaffold's API is **the file layout it hands
you**, because a derived project inherits those paths and then edits them in
place. Concretely:

- **`kubernetes/flux/` and its `kustomization.yaml` resource list** — the
  manifests Flux reconciles, and the names inside them (Deployment, Service,
  namespace, the `-internal` IngressRoute/Certificate suffix convention).
  Renaming a manifest or a resource is a rename in every derived project's live
  cluster.
- **The placeholder tokens `changeme-app` / `changeme-group`** — what
  `scripts/rename.sh` substitutes. They are also what the shared library's
  `weisssrv-new-project` CLI keys off, so changing one breaks a tool in another
  repository.
- **The CI-shape file set** — `.gitlab-ci.yml`, `.github/workflows/`,
  `.gitlab/secret-detection-ruleset.toml`. `weisssrv-new-project prune
  ci:<shape>` deletes them by a **fixed table** of paths held in the library, so
  moving one silently turns the selector into a no-op.
- **The prune/verify surface generally** — the opt-in manifests
  (`hpa.yaml`), the `Dockerfile` / `.dockerignore` pair that `prune image-build`
  targets, and the `allow-scrape-from-observability` NetworkPolicy document that
  `prune metrics` removes. The library's `cli/tests/test_template_contract.py`
  asserts every one of these against a checkout of this repository — a change
  here fails *the library's* pipeline.
- **`Taskfile.yml` task names** — documented procedures name them.

Not API: comments, docs, the placeholder `Dockerfile`'s contents, the wording of
anything under `docs/`.

## MAJOR / MINOR / PATCH

| Level | Meaning for this scaffold |
|---|---|
| **MAJOR** | A derived project cannot take the change by copying files across. A manifest or resource renamed, moved or deleted; a placeholder token changed; a CI-shape path moved (the prune table in the library no longer reaches it); a default that alters live behaviour on an unchanged answer — a changed namespace, a `NetworkPolicy` that starts denying traffic it allowed, a Deployment field that forces a restart or a PVC re-bind. |
| **MINOR** | New capability that an existing derived project can adopt or ignore. A new manifest (shipped opt-in, i.e. commented out of `kustomization.yaml`); a new Taskfile task; a new CI job or a new library include; a bumped library `ref:` that stays within the library's own back-compatible range. |
| **PATCH** | A fix that changes no path, no resource name and no resolved behaviour: a corrected probe path, a typo, docs, comments. |

While this repository is **0.x**, a breaking change bumps MINOR rather than
cutting 1.0.0 (semver's pre-1.0 allowance; the release job's `major_on_zero`
input stays `false`). The notes still lead with a **Breaking changes** section.

### The library ref is part of the contract

Every `include:` in `.gitlab-ci.yml` pins a `eric/weisssrv-lib` release tag, and
the GitHub-shape workflows vendor the same tool versions and sha256s by hand.
Moving those pins changes what gates a derived project, so:

- a library **MINOR/PATCH** bump is a MINOR here;
- a library **MAJOR** bump — a renamed template input, a changed default that
  alters a resolved job — is a MAJOR here, because a derived project that
  copies the new `.gitlab-ci.yml` over its own inherits the break.

The refs are **not uniform today**: the generic jobs are on `v0.1.1` and the
release include is on `v0.2.0`, the first library tag that ships
`ci/release/semantic-release.yml`. Bringing the rest up to `v0.2.0` is its own
MR — it moves tool pins and the secret-detection `allow_failure` default.

## Only the GitLab shape releases

The release stage lives in `.gitlab-ci.yml`, so it belongs to **CI shape A
(`gitlab`)** only — see [CI-SHAPES.md](CI-SHAPES.md).

- **Shape `github`** — `prune ci:github` deletes `.gitlab-ci.yml`, and with it
  the release job. There is **no ported workflow**: the vendored
  `scripts/semantic-release.py` talks to the GitLab Releases API
  (`$CI_API_V4_URL/projects/:id/releases`, `JOB-TOKEN`) and has no GitHub mode,
  so a `.github/workflows/release.yml` would mean either forking the script —
  which destroys the byte-identical-to-the-library property that makes it
  trustworthy — or taking a marketplace-action dependency, which nothing else in
  this template does. Tag by hand (`git tag -a vX.Y.Z && git push --tags`, then
  a GitHub Release), or ask for GitHub support in the library's script. This is
  another entry on the shape-B ledger in
  [CI-SHAPES.md](CI-SHAPES.md#what-a-githubcom-repo-genuinely-loses).
- **Shape `none`** — no pipeline, so no releases. `scripts/semantic-release.py`
  is left behind unused, exactly as `scripts/check-doc-links.py` is; delete it
  if it bothers you.

## Releases are cut automatically (conventional commits)

Merging to `main` runs the vendored
[`scripts/semantic-release.py`](../scripts/semantic-release.py) through the
library's `ci/release/semantic-release.yml`: it reads the conventional commits
since the last tag, decides the bump, and creates the tag **and** the GitLab
Release with generated notes in one Releases-API call.

| commit subject | bump |
|---|---|
| `feat:` | MINOR |
| `fix:` / `perf:` / `refactor:` | PATCH |
| any `type!:`, or a `BREAKING CHANGE:` trailer | MAJOR — MINOR while 0.x |
| `docs:` `ci:` `build:` `test:` `chore:` `style:` `revert:` | none — listed in the notes, never releases on its own |

The bump comes from the commit **subject**, so a breaking change must be written
`feat!:` (or carry a `BREAKING CHANGE:` trailer) or it ships as a patch and
nobody is warned. No releasable commit means no release and exit 0, so
re-running on an already-released commit is a no-op.

`$CI_JOB_TOKEN` is enough — the Releases API creates the tag from `ref`. If your
project protects `v*` tags, pass a PAT reference through the template's
`release_token` input with `token_header: PRIVATE-TOKEN`.

`scripts/semantic-release.py` is **vendored** from weisssrv-lib and is meant to
stay byte-identical to the library's copy at the ref the include pins. Nothing
in this repository checks that automatically, so re-copy it deliberately in the
same MR that bumps the ref.

## After `rename.sh`: releasing your own service

Once the placeholders are gone this pipeline is your project's, and the tags it
cuts are your **service's** versions, not the scaffold's. Two things change:

- **Your API is your app**, not this file layout. Read the table above with
  "what a derived project inherits" replaced by "what your users and your
  cluster depend on": your HTTP routes, your config keys, your database schema,
  the environment variables your Deployment reads.
- **The image tag is what deploys, not the git tag.** The `build-image` job
  pushes `$CI_REGISTRY_IMAGE:<short-sha>` (and `:latest` on `main`), and
  `kubernetes/flux/deployment.yaml` points at a tag you choose — see
  [CONSUMING.md](CONSUMING.md). A release tag is a *label on the commit*, a
  human-readable marker for the notes and for rollback; it does not by itself
  change what is running. Point `image:` at the digest or the `:<short-sha>` the
  released commit built.

If you would rather not version the project at all, delete the `release` stage
and its include; everything else in the pipeline is independent of it.

## Related

- [CI-SHAPES.md](CI-SHAPES.md) — the three shapes, and what shape B gives up
- [CONSUMING.md](CONSUMING.md) — library consumption, component toggles, the image build
- [ONBOARDING.md](ONBOARDING.md) — tenant + operator wiring
- [weisssrv-lib VERSIONING.md](https://git.ericsweiss.com/eric/weisssrv-lib/-/blob/main/docs/VERSIONING.md)
  — the library's own tags, which every `ref:` here pins
