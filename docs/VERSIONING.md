# Versioning — the template's own tags

A generated repo versions its **service** (its own `docs/VERSIONING.md` covers
that). This page is about the tags on *this* repository, which are what
`copier update` resolves to and what `.copier-answers.yml` records as `_commit`.
Untagged, copier falls back to the template's HEAD, so every update would pull
unreleased work.

Tags are cut by the `release` stage in [`.gitlab-ci.yml`](../.gitlab-ci.yml)
from the conventional commits merged to `main`, via the vendored
`scripts/semantic-release.py`.

| commit subject | bump |
|---|---|
| `feat:` | MINOR |
| `fix:` / `perf:` / `refactor:` | PATCH |
| any `type!:`, or a `BREAKING CHANGE:` trailer | MAJOR — MINOR while 0.x |
| `docs:` `ci:` `build:` `test:` `chore:` `style:` `revert:` | none — listed in the notes, never releases on its own |

The bump comes from the commit **subject**, so a breaking change must be written
`feat!:` (or carry a `BREAKING CHANGE:` trailer) or it ships as a patch and
nobody is warned.

## The public API of a template

Two things, and both are load-bearing on `copier update`:

1. **The answer set in `copier.yml`.** Every question is recorded in each
   generated repo and replayed on update. Renaming or removing one breaks every
   repo generated from this template; adding one with a sensible default does
   not.
2. **The rendered file layout.** A generated repo inherits those paths and then
   edits them in place, so moving or renaming a file makes `copier update` land
   as a delete-plus-create rather than a diff — and any local edit goes with it.
   Resource names inside the manifests are the same class: renaming a Deployment
   is a rename in every derived cluster.

Not API: comments, docs wording, the placeholder `Dockerfile`'s contents, the
test suite.

`copier.yml` requires copier 9.15.0 or newer, the release that implements the
`_envops.undefined` setting this template renders with. An older client fails
`copier copy` and `copier update` with a version error.

### Answer-set changes since the last release

Every question removed, renamed, or given a different default since the newest
tag is listed here, with what a repo on the previous release sees. The test
suite fails a change that is not listed.

- `change_request`, `git_host`, `registry_host` — the default keys on `forge`
  rather than on `ci_shape`. A repo whose two answers agree resolves the same
  value; one that picked GitHub Actions on a GitLab forge resolves the forge's
  spelling and host.
- `registry_pull_host` — the default is `registry_host`, so a repo that never
  answered it stops pulling from a separate internal mirror.

A recorded answer always wins over the default, so a repo that answered the
question keeps its value through `copier update`.

### Rendered-path changes since the last release

- `.yamllint` moves to `lint/yamllint-relaxed.yml`, beside the other lint
  profiles and at the path the library offers the file under. `copier update`
  carries the move; tooling that names `.yamllint` by hand reads the new path.

## MAJOR / MINOR / PATCH

| Level | Meaning here |
|---|---|
| **MAJOR** | A generated repo cannot take the change by running `copier update` and reviewing the diff. A question renamed or removed; a rendered file moved or renamed; a validator tightened to refuse an answer a repo already recorded, which copier raises on before it renders anything; a raised `_min_copier_version`, which copier refuses on before rendering; a default that alters live behaviour on unchanged answers — a changed namespace, a NetworkPolicy that starts denying traffic it allowed, a Deployment field that forces a restart. |
| **MINOR** | New capability an existing repo can adopt or ignore. A new question with a default that reproduces today's render; a new optional component (default off); a new Taskfile task; a library `ref:` bump inside the library's own back-compatible range. |
| **PATCH** | A fix that changes no path, no resource name and no resolved behaviour: a corrected probe path, a typo, docs, comments. |

While this repository is **0.x** a breaking change bumps MINOR rather than
cutting 1.0.0 (`major_on_zero` stays false). The notes still lead with a
**Breaking changes** section.

**A new answer whose default changes the render is MAJOR, not MINOR.** The
question is only half the change; the other half is what every existing repo's
next `copier update` does with it.

### The library ref is part of the contract

The `lib_ref` answer's DEFAULT is what a fresh render pins, and the generated
GitLab pipeline includes the library at exactly it. Moving that default changes
what gates every newly generated repo, so a library MAJOR bump — a renamed
template input, a changed default that alters a resolved job — is a MAJOR here.

The GitHub shape's `ci.yml`, `release.yml` and `build-image.yml` are vendored
byte-identically from the same library; re-vendor them in the same merge request
that moves `lib_ref`'s default, or the two shapes gate on different tools.
`manifest-gates.yml` is this template's own; the library's `ci.yml` carries a
job of the same name, so the two overlap — see Pending below.

## Vendored copies

Some files here are copies of library files rather than local work.
`scripts/vendored-manifest.yml` records them, the library publishes the offer
list in `scripts/vendorable-paths.yml`, and its `check-vendored-copies.py` does
the comparison. Every `lib:` path must appear in that offer list at the pinned
ref.

The manifest records two relationships. A `vendored` entry must stay
byte-identical, and drift in either direction fails. A `forked` entry must stay
different, needs a `reason:`, and when it sets `reconciled_sha256` the check
also fails if the library side moved since the fork was last reconciled. An
entry is a bare string when both repos use the same path, or a mapping with
`lib:` and `consumer:` when the paths differ.

This is a copier template with `_subdirectory: template`, so a copy that
renders into a tenant lives under `template/` and its jinja directory
conditionals are part of the literal path. Copies this template runs on
itself stay unprefixed. Both sets are registered, because they are separate
files that drift separately.

A `forked` entry's `reason:` must name a difference the file actually contains,
so the next re-vendor reconciles against the real divergence.

Every gate under `template/scripts/` is a registered library copy. The
library's `scripts/check-scrape-netpol.py` checks the scrape invariant at
namespace level over a cluster corpus; `check-scrape-wiring.py`, the copy a
tenant gets, resolves it per port.

### Pending at the next library bump

Each entry is deleted by the change that satisfies it. All three are unblocked
at the pinned `lib_ref` and waiting on their own change.

- The `flux-lint` include in the rendered `.gitlab-ci.yml` does not pass
  `crd_catalog_ref`, so the pipeline takes the library's default while
  `task flux-lint` takes `Taskfile.yml`'s `CRD_CATALOG_REF`. They agree today
  because both pin the same commit, with nothing holding them equal. Pass the
  input, and consider `expected_skipped_file` in the same change.
- The library's `flux-lint` simple arm carries the empty-build guard, the
  skipped-schema guard and the integer check on the skip budget, and the
  rendered `Taskfile.yml` implements the same three in shell because a CI
  template cannot serve a local task. Give the library a script form both can
  call, or the two drift and only `tests/test_render.py` notices.
- The vendored `ci.yml` carries a `manifest-gates` job running the same three
  gates as this template's own `manifest-gates.yml`, over `$MANIFEST_ROOT`,
  which defaults to the rendered tenant's `kubernetes/flux`. Every GitHub-shape
  tenant therefore runs them twice. Drop the template's own workflow, or keep it
  and say here why the duplication is wanted. Dropping it removes a rendered
  file, so MAJOR, and it carries `tests/test_github_shape.py`, the parity rows
  in docs/CI-SHAPES.md § Job parity and the design note in § Why the GitHub
  workflows are shaped as they are. The vendored job also passes no
  `--namespace`, which the four template-owned callers do, so a ServiceMonitor
  scoped with `matchNames` reds it while they pass.

## Which library release a template release was validated against

`lib_ref` is an answer, so a generated repo can pin any library tag it likes.
Exactly one pair per template release is proved to work: the one
`validate-rendered-app` runs, which clones the library at `copier.yml`'s `lib_ref`
default and runs the real toolchain over the render against it.

| Template release | Rendered and validated against |
|---|---|
| `v0.1.0` | not a copier template yet |
| `v0.1.1` | not a copier template yet |
| `v0.2.0` | weisssrv-lib `v0.7.2` |
| `v0.3.0` | weisssrv-lib `v0.8.0` |
| `v0.4.0` | weisssrv-lib `v0.9.5` |
| `main` (unreleased) | weisssrv-lib `v0.18.1` |

Rules that keep the table meaningful:

- The `lib_ref` default in `copier.yml` and this repository's own `include:`
  refs move together, in one merge request. `scripts/check-lib-pins.py --fix`
  syncs the includes and `validate-rendered-app` clones at the default, so the
  default is the tag actually proved. The test suite compares the two.
- Add the row in that same merge request, labelled `main` until the tag exists,
  then relabel it when the release is cut. The release notes are generated from
  commit subjects and carry no pin, so this table is the only place the pair is
  written down.
- **Other pairs are untested, not unsupported.** A repo on an older template
  release answering a newer `lib_ref` is a combination nothing here exercised.
  The library's own [VERSIONING.md](https://git.ericsweiss.com/eric/weisssrv-lib/-/blob/main/docs/VERSIONING.md)
  is what says whether that bump is allowed to break it.

## Releasing

`release` is the LAST stage on purpose: the semantic-release job sets no
`needs:`, so stage ordering gates the tag on every job above it — including
`validate-rendered-app`. A template tag is what a generated repo's `copier update`
resolves to, so it must never be cut from a tree that does not render.
