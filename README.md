# weisssrv-app-template

A **copier template** for services that deploy to a weisssrv-shaped homelab k3s
cluster. Answer a dozen questions and you get a repository with, on day one:

- a hardened, non-root **Deployment + Service**,
- a **public HTTPS route** that provisions its own DNS record and certificate,
- **secret wiring** through External Secrets (1Password, GitLab CI/CD variables,
  or none),
- **default-deny NetworkPolicies**, down/stale **alerts** and a **VPA**,
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
pipx install copier
copier copy https://git.ericsweiss.com/eric/weisssrv-app-template my-service
```

Then `cd my-service`, `git init`, commit, and follow the generated README.
Later, `copier update` replays your answers against a newer template tag, so a
fix made here arrives as a reviewable diff rather than a re-fork.

---

## The answers, in one look

Identity — `app_slug`, `app_namespace`, `app_port`, `replica_count`,
`copyright_holder` — plus four seams:

| Seam | Answers | What changes |
|---|---|---|
| **Cluster** | `external_domain`, `internal_domain`, `node_label_domain`, `internal_vip`, `registry_host`, `registry_pull_host`, `runbook_url` | which cluster the repo targets. The ones that name a site have no default — an unanswered domain, VIP or runbook fails its validator rather than resolving to another cluster's |
| **Forge / CI** | `ci_shape`, `change_request`, `enable_image_build`, `git_host`, `git_namespace`, `privileged_runner_tag`, `ci_cpu_selector`, `k8s_version`, `lib_ref`, `lib_project` | which pipeline exists, and what it pins |
| **Secrets** | `secrets_backend`, `onepassword_vault`, `secret_item` | the ExternalSecret's store and reference shape — or no secret surface at all |
| **Components** | `enable_servicemonitor`, `enable_internal_ingress`, `enable_hpa`, `enable_registry_pull_secret`, `enable_sso` | one manifest each, wired into `kustomization.yaml` |

Every one is described in [`docs/CONSUMING.md`](docs/CONSUMING.md), which is the
reference for generating and updating a repo. The pipeline choice has its own
page: [`docs/CI-SHAPES.md`](docs/CI-SHAPES.md).

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
python3 tests/validate_render.py   # the real toolchain over fixture A
python3 tests/validate_render.py --answers tests/answers-unlike.yml   # and B
python3 tests/validate_render.py --data ci_shape=none                 # and the third shape
python3 tests/validate_render.py --data secrets_backend=none --data enable_registry_pull_secret=false
python3 tests/validate_render.py --lib-path ../weisssrv-lib   # + vendored copies
```

`tests/validate_render.py` renders ONE answer set per invocation and puts
yamllint, `kustomize build`, kubeconform, ruff and the generated repo's own
doc-link and library-pin gates over the result. CI's `render-validate` job runs
every invocation above — four answer sets, with `--lib-path` folded into the
first — which is what the local loop has to repeat: a
template change that produces an invalid repo fails there rather than in
someone's cluster, and a value copied from the reference cluster fails on the
contrast render rather than passing both.

`--lib-path` adds the two gates that cannot run from a render alone. The
library's `check-vendored-copies.py` over this repository compares every
byte-identical copy (the root helpers, the same helpers under
`template/scripts/`, the three GitHub workflows) and every declared fork against
a real checkout. The **include contract** gate reads the generated pipeline
against the library templates it pins: every `inputs:` key must exist in the
template's `spec.inputs`, and every job's resolved stage must be in the
rendered `stages:` — the two failures GitLab reports only when a tenant pushes.
CI passes both a clone at `copier.yml`'s `lib_ref` default, so the tag the gates
read is the tag a generated repo inherits.

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
