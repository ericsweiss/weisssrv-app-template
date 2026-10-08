# weisssrv-app-template

A **copier template** for services that deploy to a weisssrv-shaped homelab k3s
cluster. Answer the questions and you get a repository with, on day one:

- a hardened, non-root **Deployment + Service**,
- a **public HTTPS route** that provisions its own DNS record and certificate,
- **secret wiring** through External Secrets (1Password, GitLab CI/CD variables,
  or none),
- **default-deny NetworkPolicies**, down/stale **alerts**, and optional
  autoscaling (a **VPA** by default, an **HPA** on request, or both),
- a **pipeline** in the shape you pick — self-hosted GitLab (jobs included from
  [`eric/weisssrv-lib`](https://git.ericsweiss.com/eric/weisssrv-lib) at a pinned
  tag), GitHub Actions, or none at all,
- and an **operator wiring page rendered with your repo's own names**, so the
  file your operator has to add is already written.

Flux (GitOps) does the deploying in every shape: you edit YAML, open a merge
request (a pull request on GitHub — the generated prose uses the word your forge
does), and on merge to `main` the cluster reconciles the repo into your
namespace. The pipeline never deploys — there is no `kubectl apply` in the
normal flow.

```bash
pipx install 'copier>=9.15.0'   # or: uv tool install 'copier>=9.15.0'
copier copy https://git.ericsweiss.com/eric/weisssrv-app-template my-service
```

Then `cd my-service`, `git init`, commit, and follow the generated README.
Later, `copier update` replays your answers against a newer template tag, so a
fix made here arrives as a reviewable diff rather than a re-fork.

---

## The answers, in one look

The app's own identity, plus four seams:

| Seam | What changes |
|---|---|
| **Cluster** | which cluster the repo targets. The answers that name a site have no default — an unanswered domain, VIP or runbook fails its validator rather than resolving to another cluster's |
| **Forge / CI** | which pipeline exists, and what it pins |
| **Secrets** | the ExternalSecret's store and reference shape — or no secret surface at all |
| **Components** | one manifest each, wired into `kustomization.yaml` |

Every answer is listed and described in
[`docs/CONSUMING.md`](docs/CONSUMING.md), the one place the full set lives and
the reference for generating and updating a repo. The pipeline choice has its
own page: [`docs/CI-SHAPES.md`](docs/CI-SHAPES.md).

**Components are all-or-nothing.** An enabled one renders its manifest *and* its
kustomization entry; a disabled one leaves nothing behind. There is no
`optional/` directory of switched-off files and no commented resource list — the
class of bug where a component is half-enabled cannot occur, and the render
tests assert it.

## Layout

```
copier.yml     the answer schema — the template's API (docs/VERSIONING.md)
template/      the tenant repo; .jinja files are rendered, conditional paths appear or vanish
tests/         two answer fixtures, the renders derived from them, and the invariants they must satisfy
scripts/       this repo's own vendored library helpers
docs/          CONSUMING, CI-SHAPES, ARCHITECTURE, VERSIONING
```

[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) explains how the template is put
together — the three kinds of file, the seams, and what the render suite holds.

## Working on the template

```bash
python3 -m pytest tests            # schema + the renders and their invariants
python3 tests/render_app.py --out /tmp/render   # eyeball a render
WEISSSRV_SCHEMA_NETWORK=1 python3 -m pytest tests   # also fetch CRD schemas
WEISSSRV_LIB_PATH=../weisssrv-lib python3 -m pytest tests   # + this repo's own include contract
```

The schema fetch is opt-in so a plain `pytest` run touches no network. It is a
local duplicate of what `validate-rendered-app` already enforces:
`tests/validate_render.py` is the schema gate of record, and it fails on a
skipped or empty render. The `WEISSSRV_LIB_PATH` checkout must be at
`.gitlab-ci.yml`'s `WEISSSRV_LIB_REF`.
`tests/validate_render.py`'s own flags are in
[`docs/ARCHITECTURE.md` § Running the real toolchain](docs/ARCHITECTURE.md), and
the `validate-rendered-app` script block in `.gitlab-ci.yml` is the full answer-set
matrix.

Changes ship by merge request; releases are cut from conventional commits
([`docs/VERSIONING.md`](docs/VERSIONING.md)).

## Related

- [`eric/weisssrv-lib`](https://git.ericsweiss.com/eric/weisssrv-lib) — the CI
  job templates every generated pipeline includes, and the source of the
  vendored helpers.
- [`eric/weisssrv-cluster-template`](https://git.ericsweiss.com/eric/weisssrv-cluster-template)
  — generates the CLUSTER a repo from this template deploys into.

## License

[MIT](LICENSE).
