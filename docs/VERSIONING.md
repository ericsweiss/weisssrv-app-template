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

## MAJOR / MINOR / PATCH

| Level | Meaning here |
|---|---|
| **MAJOR** | A generated repo cannot take the change by running `copier update` and reviewing the diff. A question renamed or removed; a rendered file moved or renamed; a validator tightened to refuse an answer a repo already recorded, which copier raises on before it renders anything; a default that alters live behaviour on unchanged answers — a changed namespace, a NetworkPolicy that starts denying traffic it allowed, a Deployment field that forces a restart. |
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

The GitHub shape's workflows are vendored byte-identically from the same
library; re-vendor them in the same merge request that moves `lib_ref`'s
default, or the two shapes gate on different tools.

## Releasing

`release` is the LAST stage on purpose: the semantic-release job sets no
`needs:`, so stage ordering gates the tag on every job above it — including
`render-validate`. A template tag is what a generated repo's `copier update`
resolves to, so it must never be cut from a tree that does not render.
