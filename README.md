# weisssrv-app-template

> **Note**: the canonical source for this repository is
> [git.ericsweiss.com](https://git.ericsweiss.com/eric/weisssrv-app-template).
> GitHub is a read-only mirror updated by push mirroring. Issues and merge
> requests go to the GitLab instance.

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
copier copy https://github.com/ericsweiss/weisssrv-app-template.git my-service
```

Then `cd my-service`, `git init`, commit, and follow the generated README.
Later, `copier update` replays your answers against a newer template tag, so a
fix made here arrives as a reviewable diff rather than a re-fork.

---

## Layout

[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) maps the repository, the seams
and what the render suite holds. [`docs/CONSUMING.md`](docs/CONSUMING.md) lists
every answer.

## Working on the template

```bash
pre-commit install                 # once: gitleaks, YAML and whitespace hooks on every commit
python3 -m pytest tests            # schema + the renders and their invariants
python3 tests/render_app.py --out /tmp/render   # eyeball a render
WEISSSRV_SCHEMA_NETWORK=1 python3 -m pytest tests   # also fetch CRD schemas
WEISSSRV_LIB_PATH=../weisssrv-lib python3 -m pytest tests   # + this repo's own include contract
```

The schema gate and its flags are in
[`docs/ARCHITECTURE.md` § Running the real toolchain](docs/ARCHITECTURE.md).

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
