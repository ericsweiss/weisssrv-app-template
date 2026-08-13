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

A conditional path with a typo does not fail the render — the expression
evaluates to an undefined name and the file is silently skipped — so
`test_conditional_paths_name_declared_questions` checks every one against the
declared question set.

## The seams

Four axes, each an answer, each with a render test that proves it reached the
output rather than being hardcoded:

| Seam | Answer | Where it lands |
|---|---|---|
| **Cluster identity** | `external_domain`, `internal_domain`, `node_label_domain`, `internal_vip`, `registry_host`, `registry_pull_host`, `runbook_url` | hostnames, TLS secret names, node affinity, image path, alert runbooks |
| **Forge / CI** | `ci_shape`, `change_request`, `enable_image_build`, `git_host`, `git_namespace`, `privileged_runner_tag`, `ci_cpu_selector`, `k8s_version`, `lib_ref`, `lib_project` | which pipeline exists at all, what it includes, and the forge's own vocabulary |
| **Secrets** | `secrets_backend`, `onepassword_vault`, `secret_item` | the ExternalSecret's store and `remoteRef` shape, the Deployment's secret `env` block, the operator's `ClusterSecretStore` |
| **Components** | `enable_*` | one manifest each, plus its line in `kustomization.yaml` |

The first is why this template can target a cluster generated from
`weisssrv-cluster-template` and not only the reference cluster. The second is
the one that changes the file SET rather than file contents.

## Invariants the render tests hold

These are the properties a hand-edited scaffold kept losing, which is why they
are asserted rather than documented:

- **A component is all-or-nothing.** Its manifest exists exactly when its answer
  is true, and is listed in `kustomization.yaml` exactly then. A manifest Flux
  never builds is inert; a listed resource with no file fails `kustomize build`.
- **Paired components stay paired.** The internal route ships with its
  Certificate; the ServiceMonitor with its scrape NetworkPolicy; the pull-secret
  ExternalSecret with the pod's `imagePullSecrets:`.
- **Two autoscalers never drive one resource.** With `enable_hpa` the Deployment
  ships no `replicas:` and the VPA is memory-only.
- **The PDB tracks the replica count.** `minAvailable: 1` on a single replica
  blocks every voluntary eviction, so it is not rendered there.
- **Alert selectors are namespace-scoped.** Tenant PrometheusRules evaluate
  cluster-wide, so an unscoped `absent()` silently stops firing as soon as any
  namespace has a like-named Deployment.
- **The CI shape does not reach `kubernetes/`.** Two renders differing only in
  `ci_shape` produce byte-identical manifests.
- **Nothing site-specific is hardcoded.** A second render from deliberately
  unlike answers must contain no value from the first.

## Why two answer fixtures

`tests/answers-weisssrv-shaped.yml` answers with the reference cluster's own
values and every optional component ON. It is the primary use case — and blind
to one whole class of defect: a value hardcoded from that cluster renders
identically to a correct substitution.

`tests/answers-unlike.yml` answers differently in every field, with every
optional component OFF and a different CI shape. Diffing render B against
fixture A's answers is what separates *substituted* from *copied*; rendering it
at all is what exercises the "component absent" half of every conditional.

The remaining renders each override ONE answer on top of a fixture, so the
branch under test is the only difference — `tests/test_render.py`'s `RENDERS`
table is the current list. They exist for the branches neither fixture answers:
`ci_shape: none`, the shape that ships no pipeline (and the
manifests-are-shape-independent proof); `secrets_backend: none`, the absence of
the whole secret surface — no ExternalSecret, no secret `env` block, no
`ClusterSecretStore` in the operator's wiring file; `enable_image_build: true`
on the contrast fixture, the only render that ships the GitHub build workflow;
and `onepassword_vault`, which renders the 1Password wiring branch from a vault
neither fixture answers, the contrast fixture being on the GitLab backend.

## The one gate no render can carry

The vendored copies live in this repository, not in a generated one: the helpers
at the root are what this repo runs on itself, the same helpers under
`template/scripts/` and the three GitHub workflows are what it hands to a
tenant, and all of them are byte-identical to weisssrv-lib. That relationship is
recorded in the library (`scripts/vendored-paths.yml`) and checked from here by
its `check-vendored-copies.py`, which `tests/validate_render.py --lib-path`
runs against a checkout at `copier.yml`'s `lib_ref` default.

Registering the copy in the library rather than here is what makes the gate hold
in both directions: a file the library starts or stops shipping — and a
deliberate fork that quietly converges — reaches this gate at the next bump,
with nothing to maintain on this side.

## What this template does not do

- **It does not deploy.** Flux does, from the generated repo, after the operator
  adds the wiring file the generated `docs/ONBOARDING.md` spells out.
- **It does not ship storage.** The generated app is stateless; a PV/PVC pattern
  is cluster-specific and operator-assisted (ONBOARDING step O6).
- **It does not create the cluster.** That is
  [`weisssrv-cluster-template`](https://git.ericsweiss.com/eric/weisssrv-cluster-template),
  which this template's cluster-identity answers are designed to match.
