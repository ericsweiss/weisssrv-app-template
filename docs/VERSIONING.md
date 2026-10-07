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
`manifest-gates.yml` is this template's own and has no library counterpart.

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

`template/scripts/check-kustomization.py` and
`template/scripts/check-scrape-wiring.py` are template-local at the pinned
`lib_ref`, so they are absent from the manifest. The library's
`scripts/check-scrape-netpol.py` checks the same invariant at namespace level
over a cluster corpus, and the copy here resolves it per port.

### Pending at the next library bump

Each entry is deleted by the pin bump that satisfies it.

- `scripts/check-netpol-except-parity.py` imports `gate_common` from its own
  directory in the library's current tree. Re-vendor
  `template/scripts/gate_common.py` alongside it and register it. Without it,
  every generated repo's `task netpol` and `netpol-check` fail with
  ImportError. This repo keeps no root copy of the egress fence, so it needs no
  root `gate_common.py`.
- `template/scripts/check-kustomization.py` and
  `template/scripts/check-scrape-wiring.py` have library twins,
  `scripts/check-kustomization.py` and `scripts/check-scrape-wiring.py`, in the
  offer list from the next release. At the bump either re-vendor both and
  register them under `vendored:` against the `template/scripts/` consumers, or
  register them under `forked:` with a `reason:` and a `reconciled_sha256`.
  Nothing flags them today: `tests/test_copier_config.py`'s `TEMPLATE_OWNED`
  set exempts both. The same change passes `--scan template/scripts=scripts`
  and `--scan scripts=scripts` in `tests/validate_render.py`'s
  `check_registered_copies`, drops the `TEMPLATE_OWNED` allowlist, and rewrites
  the matching claims in docs/ARCHITECTURE.md, docs/CONSUMING.md and CLAUDE.md.
- `.gitattributes` and `template/.gitattributes` are not registered: the
  library's `lint/gitattributes` is not in its offer list at the pinned
  `lib_ref`. Register both halves in the change that moves the pin to a release
  carrying that path.
- `scripts/check-netpol-except-parity.py`'s docstring cites
  `examples/netpol-except.example.yaml` for the `--config` schema, and neither
  this repo nor a generated one carries it. Vendor it into `examples/` and
  `template/examples/`, so the tenant who needs a different fence has the file
  the gate names.
- The `flux-lint` include in the rendered `.gitlab-ci.yml` gains
  `crd_catalog_ref`, passed the sha `Taskfile.yml`'s `CRD_CATALOG_REF` already
  pins, so the pipeline and `task flux-lint` resolve one catalog. Consider
  `expected_skipped_file` in the same change, and rewrite the two
  docs/CI-SHAPES.md claims it invalidates: that the library's job takes no such
  input at the pinned `lib_ref`, and that neither pipeline shape's kubeconform
  run fails on a skipped schema.
- The library's `flux-lint` simple arm gains an empty-build guard and a
  skipped-schema guard, so the rendered `Taskfile.yml` drops its shell
  re-implementation of both.
- The library's `ci.yml` lints with `yamllint -c lint/yamllint-relaxed.yml .`
  from the next release, and the library offers the profile at
  `lint/yamllint-relaxed.yml` for that path only. This template renders it to
  the tenant root as `.yamllint`, so re-vendoring the workflow alone ships a
  `-c` path no tenant has. At the bump move `template/.yamllint` to
  `template/lint/yamllint-relaxed.yml`, repoint the forked consumer path in
  `scripts/vendored-manifest.yml`, and move the local references with it:
  `template/Taskfile.yml.jinja`'s `yaml-lint` command,
  `template/.pre-commit-config.yaml`'s yamllint hook args, the rendered GitLab
  pipeline's `yaml-lint` `config:` input and the `changes:` list beside it, and
  the `.dockerignore` entry. A rendered file moves, so the release is MAJOR.
- The library's `ci.yml` gains a `manifest-gates` job that runs
  `check-netpol-except-parity.py`, `check-scrape-wiring.py` and
  `check-kustomization.py` over `$MANIFEST_ROOT`, which defaults to the
  rendered tenant's `kubernetes/flux`. Re-vendoring it makes every GitHub-shape
  tenant run the three gates twice. At the bump either drop the template's own
  `manifest-gates.yml`, or keep it and say here why the duplication is wanted.
  Dropping it removes a rendered file, so MAJOR, and it carries
  `tests/test_github_shape.py`, the parity rows in docs/CI-SHAPES.md § Job
  parity and the design note in § Why the GitHub workflows are shaped as they
  are. It also closes the release-gating gap docs/CI-SHAPES.md records, because
  the library's job sits inside `ci.yml`, which `release.yml`'s `workflow_run`
  trigger gates. Three claims become false and must be rewritten in the same
  change: this page's "`manifest-gates.yml` is this template's own and has no
  library counterpart", and the two in docs/CI-SHAPES.md that say the library
  ships no GitHub workflow running those gates and that the file is never
  re-vendored.
- `K8S_VERSION` becomes a repository variable: the library's `ci.yml` reads
  `vars.K8S_VERSION` with its own literal as the fallback, and `flux-lint`
  prints a warning annotation on every run while the variable is unset. At the
  bump rewrite `copier.yml`'s `_message_after_copy`, docs/CI-SHAPES.md
  § What the GitHub shape gives up, `template/docs/VERSIONING.md.jinja` and
  `template/docs/ONBOARDING.md.jinja` to tell the tenant to set repository
  variable `K8S_VERSION`, and optionally `MANIFEST_ROOT`, `ALLOWED_SKIPS` and
  `CRD_CATALOG_REF`, instead of editing the vendored literal. Drop the
  "re-apply after every re-vendor" sentence from each. That leaves no sanctioned
  hand edit to a vendored workflow, so the `env.K8S_VERSION` carve-out in
  `template/CLAUDE.md.jinja` goes with it.

## Which library release a template release was validated against

`lib_ref` is an answer, so a generated repo can pin any library tag it likes.
Exactly one pair per template release is proved to work: the one
`render-validate` runs, which clones the library at `copier.yml`'s `lib_ref`
default and runs the real toolchain over the render against it.

| Template release | Rendered and validated against |
|---|---|
| `v0.1.0` | not a copier template yet |
| `v0.1.1` | not a copier template yet |
| `v0.2.0` | weisssrv-lib `v0.7.2` |
| `v0.3.0` | weisssrv-lib `v0.8.0` |
| `v0.4.0` | weisssrv-lib `v0.9.5` |
| `main` (unreleased) | weisssrv-lib `v0.17.1` |

Rules that keep the table meaningful:

- The `lib_ref` default in `copier.yml` and this repository's own `include:`
  refs move together, in one merge request. `scripts/check-lib-pins.py --fix`
  syncs the includes and `render-validate` clones at the default, so the
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
`render-validate`. A template tag is what a generated repo's `copier update`
resolves to, so it must never be cut from a tree that does not render.
