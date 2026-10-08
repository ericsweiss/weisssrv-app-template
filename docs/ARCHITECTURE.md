# Architecture — what this template is, and where its seams are

This repository is a **copier template**: `copier.yml` is the answer schema,
`template/` is the tree that gets rendered, `tests/` renders it and asserts what
must be true of every generated repo. Nothing here deploys; the repos it
generates are deployed by Flux.

```
copier.yml     the answer schema — the template's API
template/      the tenant repo, with .jinja on every templated file
tests/         two answer fixtures, the renders derived from them, the invariants
scripts/       this repo's own vendored library helpers
docs/          what you are reading
```

## The three kinds of file under `template/`

1. **Static** — copied verbatim (`.editorconfig`, the vendored Python helpers,
   the GitHub workflows). No answer reaches them.
2. **Templated** — `.jinja` suffix, stripped on render (`deployment.yaml.jinja`
   → `deployment.yaml`). Answers substitute into the content.
3. **Conditional** — the path itself carries a Jinja expression, e.g.
   `{% raw %}{% if enable_hpa %}hpa.yaml{% endif %}{% endraw %}.jinja`. Copier
   renders each path segment and **skips any file whose segment renders empty**,
   which is how a component is present or absent rather than present and
   commented out.

A conditional path whose expression names an undeclared answer fails the
render: `_envops.undefined: jinja2.StrictUndefined` raises and copier prints the
offending path. `test_conditional_paths_name_declared_questions` scans every
path against the declared question set anyway, so the offender is named without
paying for a render.

## The seams

Four axes, each an answer, each with a render test that proves it reached the
output rather than being hardcoded:

| Seam | Where it lands |
|---|---|
| **Cluster identity** | hostnames, TLS secret names, node affinity, image path, alert runbooks |
| **Forge / CI** | which pipeline exists at all, what it includes, and the forge's own vocabulary |
| **Secrets** | the ExternalSecret's store and `remoteRef` shape, the Deployment's secret `env` block, the operator's `ClusterSecretStore` |
| **Components** | one manifest each, plus its line in `kustomization.yaml` |

Which answers make up each seam is in
[CONSUMING.md](CONSUMING.md#the-answers), the single list.

The first is why this template can target a cluster generated from
`weisssrv-cluster-template` and not only the reference cluster. The second is
the one that changes the file SET rather than file contents.

## Invariants the render tests hold

The render tests assert each of these:

- **A component is all-or-nothing.** Its manifest exists exactly when its answer
  is true, and is listed in `kustomization.yaml` exactly then. A manifest Flux
  never builds is inert; a listed resource with no file fails `kustomize build`.
- **Paired components stay paired.** The internal route ships with its
  Certificate; the ServiceMonitor with its scrape NetworkPolicy; the pull-secret
  ExternalSecret with the pod's `imagePullSecrets:`. The HPA and the VPA name the
  rendered Deployment, and where a PDB ships the HPA holds at least two replicas.
- **Two autoscalers never drive one resource.** With `enable_hpa` the Deployment
  ships no `replicas:`, and the VPA — when `enable_vpa` renders one — is
  memory-only.
- **The PDB tracks the replica count.** `minAvailable: 1` on a single replica
  blocks every voluntary eviction, so it is not rendered there.
- **Alert selectors are namespace-scoped.** Tenant PrometheusRules evaluate
  cluster-wide, so an unscoped `absent()` silently stops firing as soon as any
  namespace has a like-named Deployment.
- **The CI shape does not reach `kubernetes/`.** Two renders differing only in
  `ci_shape` produce byte-identical manifests.
- **Nothing site-specific is hardcoded.** Every render built on the unlike
  fixture must contain no value from the reference one, the all-components-on
  render included, so no optional manifest escapes the scan.
- **A cited path exists in the render.** A doc, a comment or the Taskfile that
  names `docs/X.md`, `scripts/X.py` or `.github/workflows/X.yml` reads to the
  recipient as a file in their own repo, so that file must render. A path that
  lives only here is cited as "in the app template".

## Why two answer fixtures

`tests/answers-weisssrv-shaped.yml` answers with the reference cluster's own
values and every optional component ON. It is the primary use case — and blind
to one whole class of defect: a value hardcoded from that cluster renders
identically to a correct substitution.

`tests/answers-unlike.yml` answers differently in every field, with every
optional component OFF and a different CI shape. Diffing render B against
fixture A's answers is what separates *substituted* from *copied*; rendering it
at all is what exercises the "component absent" half of every conditional.

The remaining renders override the smallest set of answers that reaches the
branch under test. One answer where that is enough, more where a validator
forces a companion: the GitLab pipeline is only legal on a GitLab forge, so
that render carries `forge` with it. They exist for the branches neither
fixture answers, and `tests/test_render.py`'s `RENDERS` table is the current
list.

## Running the real toolchain

The pytest suite asserts structure with no external binaries, so it runs
anywhere. `tests/validate_render.py` runs what a generated repo's own pipeline
runs — yamllint, `kustomize build`, kubeconform, a second kubeconform pass over
`docs/ONBOARDING.md`'s wiring block, ruff, the doc-link checker, the
egress-fence, scrape-wiring and kustomization gates, the library-pin gate and,
with `--lib-path`, the vendored-copy gate — against a
render. That is the only way to learn that a repo this template produces would
fail its own gates.

```
python3 tests/validate_render.py                               # fixture A
python3 tests/validate_render.py --answers tests/answers-unlike.yml
python3 tests/validate_render.py --data ci_shape=none          # one answer overridden
python3 tests/validate_render.py --data secrets_backend=none --data enable_registry_pull_secret=false
python3 tests/validate_render.py --keep /tmp/render            # leave the tree behind
WEISSSRV_KEEP_SCRATCH=1 python3 tests/validate_render.py       # leave the temp dir in place
python3 tests/validate_render.py --lib-path /path/to/lib       # + vendored copies and the include contract
python3 tests/validate_render.py --lib-path ../weisssrv-lib --allow-ref-mismatch # warn, do not fail
```

`--lib-path` needs a checkout AT the pinned ref; `--allow-ref-mismatch` turns
that failure into a printed warning, for validating against an unreleased
library. CI never passes it.

The render is `git init`-ed before the gates run. The doc-link checker reads
`git ls-files`, and an untracked tree would silently narrow it to `docs/` and
two root files.

One render per invocation. Exit 0 when every gate passes, 1 on a failure, and 2
when a required tool is missing, because a validator that quietly skips itself
is not one.

## The one gate no render can carry

The vendored copies live in this repository, not in a generated one: the helpers
at the root are what this repo lints and, where it has the tree for them, runs
on itself; the same helpers under `template/scripts/` and the GitHub workflows
are what it hands to a tenant. Every gate script is a byte-identical library copy, as are the `ci.yml`,
`release.yml` and `build-image.yml` workflows. The `manifest-gates.yml` workflow
is this template's own and is deliberately unregistered. That relationship is
recorded HERE, in `scripts/vendored-manifest.yml`, and checked by the library's
`check-vendored-copies.py`, which `tests/validate_render.py --lib-path` runs
against a checkout at `copier.yml`'s `lib_ref` default.

Listing the fork alongside the copy is what makes the gate hold in both
directions: a deliberate fork that quietly converges fails, and a fork whose
library side moved fails on its `reconciled_sha256` until the change is
absorbed. The library's half is the engine plus its offer list
(`scripts/vendorable-paths.yml`) — a manifest entry naming a path the library
does not offer fails, so this repository cannot grow a dependency on a library
internal no release contract covers.

The **include contract** gate rides with `--lib-path` too: it reads the
generated pipeline against the library templates it pins, so every `inputs:` key
must exist in the template's `spec.inputs` and every job's resolved stage must
appear in the rendered `stages:`. Those are the two failures GitLab reports only
once a tenant pushes, so every invocation that renders a pipeline passes
`--lib-path`.

## Platform contract

The rendered manifests name platform objects directly, so a target cluster must
provide them. The list is in
[CONSUMING.md § What your cluster must provide](CONSUMING.md#what-your-cluster-must-provide),
and the tenant-side copy is rendered from
`template/docs/ARCHITECTURE.md.jinja` — keep the two in agreement. A render test
asserts that the objects the manifests bind to are named in the tenant copy. A cluster
generated by
[`weisssrv-cluster-template`](https://git.ericsweiss.com/eric/weisssrv-cluster-template)
is the reference implementation.

Three gates run over every render here AND ship into the generated repo: the
egress fence (`scripts/check-netpol-except-parity.py`, run there by `task netpol`
and the GitLab shape's `netpol-check` job), the scrape-wiring gate
(`task scrape-wiring` / `scrape-wiring-check`) and the kustomization gate
(`task kustomization` / `kustomization-check`). The GitHub shape runs all three
in its template-owned `manifest-gates` workflow, which has no library
counterpart. The generated `docs/ARCHITECTURE.md` says so where the tenant reads
it.

## What this template does not do

- **It does not deploy.** Flux does, from the generated repo, after the operator
  adds the wiring file the generated `docs/ONBOARDING.md` spells out.
- **It does not ship storage.** The generated app is stateless. Adding a volume
  is an operator step and storage-class-agnostic; ONBOARDING step O6 works the
  reference cluster's zvol flow as the example.
- **It does not create the cluster.** That is
  [`weisssrv-cluster-template`](https://git.ericsweiss.com/eric/weisssrv-cluster-template),
  which this template's cluster-identity answers are designed to match.
