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
  `template/.github/workflows/*.yml` are byte-identical to weisssrv-lib, which
  owns the registry (`scripts/vendored-paths.yml` there). Fix upstream and
  re-vendor; a local edit is reverted by the next re-vendor and fails the gate.
- **`copier.yml` is API.** Renaming or removing a question breaks every
  generated repo's `copier update` — that is a MAJOR
  ([`docs/VERSIONING.md`](docs/VERSIONING.md)).
- **Run the suite before opening the merge request:** `python3 -m pytest tests`.

## Conventions

- A question may only read answers declared **above** it (`default`, `when`,
  `validator`). Copier fills the answer map in question order, so a forward
  reference is undefined interactively and defined in `--data` mode — a check
  that reads as enforcement and enforces nothing. A test holds this.
- A conditional path (`{% if enable_x %}file.yaml{% endif %}.jinja`) with a typo
  does not fail — the file is silently skipped. Add the answer to `copier.yml`
  first; the path scan checks the rest.
- Both answer fixtures must answer **every** question, including ones their own
  answers make copier skip: `--defaults` otherwise falls back silently.
- Site identity — the app's names AND the cluster's — carries a `placeholder:`
  and no `default:`. A default there is accepted by pressing enter, and the
  resulting repo is green everywhere and wrong at runtime. Derived defaults are
  fine: they compose from an answer already given and name no site of their own.
- A Markdown file under `template/` that links to a **rendered** sibling
  (`CLAUDE.md`, not `CLAUDE.md.jinja`) needs the `.jinja` suffix itself, or this
  repo's own `docs-link-check` resolves the link here, where the target does not
  exist yet.
- Keep the tenant-facing docs and agent files (under `template/`) as pointers,
  not procedure copies. The generated repo's `CLAUDE.md` is the standing rules;
  its `docs/` carry the detail.

## Docs

| Question | Read |
|---|---|
| Generating and updating a repo; every answer, one by one | [`docs/CONSUMING.md`](docs/CONSUMING.md) |
| The three pipeline shapes and their parity | [`docs/CI-SHAPES.md`](docs/CI-SHAPES.md) |
| How the template is put together, and what the tests hold | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| What a tag here means, and what counts as breaking | [`docs/VERSIONING.md`](docs/VERSIONING.md) |
| The CI library's include contract | https://git.ericsweiss.com/eric/weisssrv-lib/-/blob/main/docs/INCLUDE-CONTRACT.md |
