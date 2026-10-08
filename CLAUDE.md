# CLAUDE.md

Guidance for Claude Code (and other agents) working in **weisssrv-app-template**,
the copier template that generates tenant repositories for a weisssrv-shaped k3s
cluster.

## What this repo is

Nothing here deploys. `copier.yml` is the answer schema, `template/` is the tree
rendered into a new repository, `tests/` renders it and asserts the invariants.
A change here reaches every generated repo through `copier update`, so the blast
radius of an edit is every tenant, not this repo.

[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) is the map: the three kinds of
file under `template/`, the four seams, and what the render suite holds.

## Hard rules

- **Never push to `main`.** Branch + merge request, even for one-liners.
- **Never commit secrets.** The rendered repos hold `ExternalSecret` references
  only, and so does every fixture and doc here.
- **Never hardcode site identity in `template/`.** Domains, registry hosts, node
  labels, VIPs and runner tags are answers. The contrast fixture exists to catch
  a literal that slipped in, and it will.
- **A component is all-or-nothing.** Adding one means the manifest, its
  conditional path, its line in `kustomization.yaml`, and whatever else it
  implies (a paired Certificate, a NetworkPolicy, an `imagePullSecrets:` entry).
  Add the render assertion in the same change.
- **Never edit a vendored copy.** `scripts/*.py`, `template/scripts/*.py` and
  the `ci.yml`, `release.yml` and `build-image.yml` workflows under
  `template/.github/workflows/` are byte-identical to weisssrv-lib, and
  `scripts/vendored-manifest.yml` here records every copy and every declared
  fork. Fix upstream and re-vendor; a local edit is reverted by the next
  re-vendor and fails the gate. The `manifest-gates.yml` workflow is this
  template's own, deliberately unregistered: fix that one here.
- **`copier.yml` is API.** Renaming or removing a question breaks every
  generated repo's `copier update` — that is a MAJOR
  ([`docs/VERSIONING.md`](docs/VERSIONING.md)).
- **Run the suite before opening the merge request:** `python3 -m pytest tests`.
  `pre-commit install` once, and gitleaks plus the YAML and whitespace hooks run
  on every commit; the render suite and the doc-link and lib-pin gates only run
  under `pytest`.

## Conventions

- A question may only read answers declared **above** it (`default`, `when`,
  `validator`). Copier fills the answer map in question order, so a forward
  reference is undefined interactively and defined in `--data` mode — a check
  that reads as enforcement and enforces nothing. A test holds this.
- A conditional path (`{% if enable_x %}file.yaml{% endif %}.jinja`) naming an
  undeclared answer fails the render: `_envops.undefined: jinja2.StrictUndefined`
  raises and copier prints the offending path. Declare the answer in `copier.yml`
  first; `test_conditional_paths_name_declared_questions` names the path without
  needing a render.
- Both answer fixtures must answer **every** question, including ones their own
  answers make copier skip: `--defaults` otherwise falls back silently.
- Site identity — the app's names AND the cluster's — carries a `placeholder:`
  and no `default:`. A default there is accepted by pressing enter, and the
  resulting repo is green everywhere and wrong at runtime. Derived defaults are
  fine: they compose from an answer already given and name no site of their own.
- A Markdown file under `template/` that links to a **rendered** sibling
  (`CLAUDE.md`, not `CLAUDE.md.jinja`) needs the `.jinja` suffix itself, or this
  repo's own `lint-docs-links` resolves the link here, where the target does not
  exist yet.
- Keep the tenant-facing docs and agent files (under `template/`) as pointers,
  not procedure copies. The generated repo's `CLAUDE.md` is the standing rules;
  its `docs/` carry the detail.

## Comments

Comments state the current constraint and why it exists, in the present tense.
They carry no history: no dates, no merge-request or pipeline numbers, no "this
used to", no incident narration, no site hostnames. Longer rationale belongs in
`docs/` or the nearest README, not in a header above five lines of code.

A comment block runs to three content lines. A block guarding a trap that causes
an outage may open `CRITICAL:` and run to eight. That marker is for outages, not
for emphasis and not for a long file header.

A comment inside a Jinja template is rendered into the tenant's own file, so
write it for the app owner who reads it there — same three-line ceiling, and no
reference to this template's own files unless it says `in the app template`.

## Docs

| Question | Read |
|---|---|
| Generating and updating a repo; every answer, one by one | [`docs/CONSUMING.md`](docs/CONSUMING.md) |
| The three pipeline shapes and their parity | [`docs/CI-SHAPES.md`](docs/CI-SHAPES.md) |
| How the template is put together, and what the tests hold | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| What a tag here means, and what counts as breaking | [`docs/VERSIONING.md`](docs/VERSIONING.md) |
| The CI library's include contract | https://git.ericsweiss.com/eric/weisssrv-lib/-/blob/main/docs/INCLUDE-CONTRACT.md |
