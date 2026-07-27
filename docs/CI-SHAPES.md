# CI shapes — pick one at setup

This template ships **three** deployment/CI shapes and expects you to keep
exactly one. They differ only in *what checks your changes* and *where the image
is built*. They do **not** differ in how the app deploys: **Flux pulls this repo
and reconciles it in all three shapes**, so `kubernetes/flux/` is byte-identical
whichever you choose.

| Shape | Repo lives on | Checks run by | Pick it when |
|---|---|---|---|
| **A — `gitlab`** (default) | self-hosted GitLab (`git.ericsweiss.com`) | `.gitlab-ci.yml`, including `eric/weisssrv-lib` templates at a pinned tag | you have an account on the operator's GitLab |
| **B — `github`** | github.com | `.github/workflows/` (GitHub Actions) | your repo lives on GitHub and you want the same gates |
| **C — `none`** | anywhere | nothing — local `task lint` / pre-commit only | you just want Flux to pull and deploy; no pipeline |

**The default is `gitlab`.** Change nothing and you get shape A — but still run
the selector below, because leaving the other shape's files in place means a
GitHub mirror with Actions enabled would run a second, duplicate set of gates.

---

## Selecting a shape (the one command)

Run this once, right after `./scripts/rename.sh <app-slug> <gitlab-group>`:

```bash
./scripts/select-ci.sh gitlab    # A — keep .gitlab-ci.yml, drop .github/workflows/
./scripts/select-ci.sh github    # B — keep .github/workflows/, drop .gitlab-ci.yml
./scripts/select-ci.sh none      # C — drop both; Flux-only
```

Then `git status`, `git add -A`, commit. Re-running with the same shape is a
no-op, and the script never touches `kubernetes/flux/`.

`.gitlab/issue_templates/` and `.gitlab/merge_request_templates/` are **host**
metadata, not CI, so the selector leaves them alone — they still work on a
GitLab repo that runs no pipeline. `rm -rf .gitlab` by hand if this project has
no GitLab side at all; GitHub's equivalents would be `.github/ISSUE_TEMPLATE/`
and `.github/pull_request_template.md`, which this template does not ship.

> **What the script does.** `scripts/select-ci.sh` is a thin wrapper over the
> shared library's `weisssrv-new-project prune ci:<shape>`, the same way
> `scripts/rename.sh` wraps `rename`. Call the CLI directly if you prefer, or
> combine both steps in one call:
>
> ```bash
> weisssrv-new-project rename <app> <group> --ci <shape>
> ```
>
> The shape is never joined onto a filesystem path — it is only ever a key into
> a fixed table of the paths each shape owns — so a crafted value deletes
> nothing.

---

## Shape A — `gitlab` (default, unchanged)

Everything in [CONSUMING.md](CONSUMING.md) applies as written: the generic
lint/validate/security jobs come from `eric/weisssrv-lib` at a pinned `ref:`,
they run tag-less on the operator's shared non-privileged `k8s-deploy` runner,
and `build-image` runs tagged `infrastructure` on the privileged runner.

Nothing about shape A changed when B and C were added. If you are on the
operator's GitLab, stop reading here and follow [CONSUMING.md](CONSUMING.md).

---

## Shape B — `github`

Two workflows: `.github/workflows/ci.yml` (the gates) and
`.github/workflows/build-image.yml` (the image).

### Job-for-job parity

Every tool version is pinned to the same value the GitLab library template
uses, and every downloaded binary is sha256-verified against the same digest —
so both shapes gate on byte-identical tools.

| GitLab job | GitHub job | Parity |
|---|---|---|
| `yaml-lint` | `yaml-lint` | **Exact.** yamllint 1.38.0, `-c .yamllint`, whole tree. |
| `flux-lint` (simple mode) | `flux-lint` | **Exact.** kustomize 5.4.3 + kubeconform 0.6.7 (same sha256s), `-strict -ignore-missing-schemas -kubernetes-version 1.36.0` with the datreeio CRDs-catalog schema location. |
| `shellcheck` | `shellcheck` | **Exact.** shellcheck 0.10.0, `--severity=warning --exclude=SC1091,SC2034`, over `scripts/*.sh`. |
| `docs-link-check` | `docs-link-check` | **Exact.** the same vendored `scripts/check-doc-links.py`. |
| `secret_detection` | `secret-detection` | **Same detector, different wrapper** — see below. |
| `build-image` | `build-image` | **Different registry + push policy** — see below. |
| `pr-agent-review` | — | **Not ported.** |

**Secret detection.** GitLab runs its managed Secret-Detection analyzer, which
is gitleaks underneath, loading `.gitleaks.toml` through
`.gitlab/secret-detection-ruleset.toml`. The workflow runs the same gitleaks
(8.30.1, the version pinned in `.pre-commit-config.yaml`) directly against the
same `.gitleaks.toml`: the working tree always, plus the commit range a pull
request adds. Two deliberate differences:

- **Findings block the job on GitHub.** The GitLab include here is still on the
  library's pre-`v0.2.0` `allow_failure: true` default, i.e. findings only
  *warn*. Pass `inputs: { allow_failure: false }` on the `secret-detection.yml`
  include to make shape A block the same way.
- You get gitleaks' own output and exit code — not GitLab's vulnerability
  report, merge-request security widget, or security dashboard.

**Image build.** Shape B pushes to **GHCR** (`ghcr.io/<owner>/<repo>`)
authenticated with the built-in `GITHUB_TOKEN`, the way shape A uses the
`$CI_REGISTRY_*` built-ins — no key to bring either way. Two differences:

- A **pull request builds but does not push**: a fork's `GITHUB_TOKEN` is
  read-only, so a push would fail there. On merge to `main` the workflow pushes
  `:<short-sha>` and `:latest`. The GitLab job pushes `:<short-sha>` on merge
  requests too. (`:latest` is default-branch-only in both — privileged jobs
  consume that tag, so unreviewed code must never populate it.)
- No privileged-runner problem to solve. GitHub-hosted runners ship Docker, so
  unlike the tenant `k8s-deploy` runner the build needs no special tag. Delete
  `build-image.yml` (or run `weisssrv-new-project prune image-build`) for a
  project that runs an upstream image; the workflow already no-ops when there
  is no `Dockerfile`.

### What a github.com repo genuinely loses

1. **The shared library.** `include: project: eric/weisssrv-lib` is GitLab-only,
   and the library ships GitLab CI templates — it has no reusable Actions
   workflows. So `.github/workflows/` is a **vendored copy**, not an include: a
   tool bump or gate change that shape A picks up by moving one `ref:` is a
   manual edit here, in every GitHub project, with nothing to tell you it
   happened. This is the real cost of shape B. Watch the library's
   [INCLUDE-CONTRACT.md](https://git.ericsweiss.com/eric/weisssrv-lib/-/blob/main/docs/INCLUDE-CONTRACT.md)
   and re-vendor deliberately.
2. **Per-job change detection.** GitLab skips a job whose `changes:` paths
   didn't move. Actions filters paths per *workflow*, not per job, and a skipped
   job never satisfies a required status check — so every gate runs on every
   pull request here. They are all fast; this is a cost in runner minutes, not
   in signal.
3. **The GitLab security surface.** No vulnerability report, no MR security
   widget, no security dashboard, no "new findings in this MR" diffing. GitHub's
   own secret scanning and push protection cover some of this — free on public
   repos, GitHub Advanced Security on private ones.
4. **`pr-agent-review`.** Not ported. `pr-agent` supports GitHub; add it as a
   workflow with your own `OPENAI_API_KEY` if you want it. It never blocked
   anything in shape A either.
5. **The in-cluster runner.** The tenant `k8s-deploy` runner has internet egress
   only — no LAN, no tailnet, no SSH — so shape A gives up most of that too, and
   parity is closer than it sounds. What you do lose is the *option*: on GitLab
   the operator can hand you a tagged in-cluster runner for a job that must
   reach the cluster. GitHub-hosted runners cannot, ever; you would have to
   register a self-hosted GitHub runner inside the cluster.
6. **Pulling the image.** Shape A pushes to `registry.git.ericsweiss.com`, which
   sits on the LAN behind the cluster's registry cache and is already trusted.
   GHCR is on the internet: a **private** package needs an `imagePullSecret` in
   your namespace (an `ExternalSecret` of type `kubernetes.io/dockerconfigjson`,
   plus `imagePullSecrets:` on the Deployment). Making the GHCR package public
   avoids that entirely and is the simpler choice for a homelab app.
7. **`CODEOWNERS`.** The shipped file uses `@changeme-group`. On GitHub that
   must be a username or `@org/team`, and it is only enforced if branch
   protection turns on "Require review from Code Owners".

---

## Shape C — `none` (Flux-only)

No pipeline at all. Flux pulls this repo on its `interval` and reconciles
`kubernetes/flux/` into your namespace — exactly as in shapes A and B, because
in all three shapes *Flux* is what deploys. What you give up is the gate: a
manifest that fails `kustomize build` reaches the cluster, and the tenant
`Kustomization` goes `NotReady` instead of the pipeline going red.

Two things to put in its place:

```bash
pre-commit install     # gitleaks + yamllint + YAML syntax on every commit
task lint              # yamllint + kustomize build + kubeconform — what CI would run
```

`.gitleaks.toml`, `.yamllint`, `.pre-commit-config.yaml` and `Taskfile.yml` all
survive shape C, so the local checks are the same checks. There is nothing to
build an image with, so use an upstream image or `task build` and push by hand.

### Operator wiring for a GitHub-hosted tenant

The wiring file in the cluster repo
(`kubernetes/clusters/weisssrv/tenants/<slug>.yaml`) is **unchanged** from
[ONBOARDING.md](ONBOARDING.md) step O3 except for the `GitRepository`. Shape C
is not GitHub-specific — a GitLab repo with no pipeline works identically — but
GitHub is the case that needs credentials spelled out.

**Public GitHub repo — no credential at all:**

```yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: GitRepository
metadata:
  name: <slug>
  namespace: flux-system
spec:
  interval: 1m
  url: https://github.com/<owner>/<repo>
  ref:
    branch: main
```

**Private GitHub repo — deploy key (recommended).** A deploy key is scoped to
one repository, read-only, and does not expire:

```bash
# 1. Create the Secret (NOT Flux-managed — one-time, like the ESO bootstrap
#    secret). The flux CLI generates the keypair, scans known_hosts, and prints
#    the PUBLIC key.
flux create secret git <slug>-git-auth \
  --namespace=flux-system \
  --url=ssh://git@github.com/<owner>/<repo> \
  --ssh-key-algorithm=ecdsa --ssh-ecdsa-curve=p521

# 2. GitHub → the repo → Settings → Deploy keys → Add deploy key.
#    Paste the printed public key. Leave "Allow write access" UNCHECKED —
#    Flux only ever reads.
```

```yaml
spec:
  interval: 1m
  url: ssh://git@github.com/<owner>/<repo>.git   # ssh:// form, .git suffix
  secretRef:
    name: <slug>-git-auth                        # same namespace as the GitRepository
  ref:
    branch: main
```

**Private GitHub repo — fine-grained PAT** (use when egress on port 22 is
blocked). Scope it to the single repository with **Contents: Read-only**:

```bash
flux create secret git <slug>-git-auth \
  --namespace=flux-system \
  --url=https://github.com/<owner>/<repo> \
  --username=git --password=<PAT>
```

```yaml
spec:
  url: https://github.com/<owner>/<repo>
  secretRef:
    name: <slug>-git-auth
```

A PAT expires — put its rotation on the operator's calendar. A deploy key does
not, which is why it is the default recommendation.

Notes that bite people:

- The Secret lives in **`flux-system`**, the `GitRepository`'s own namespace —
  `secretRef` is namespace-local and cannot cross namespaces.
- It is created by hand and **never committed**, like the `op-credentials` /
  `onepassword-connect-token` bootstrap secrets.
- Deployment is **pull-based**: with no CI there is no webhook poke, so the
  `interval` (1m above) is your reconcile latency. Push-based needs a Flux
  `Receiver` reachable from github.com — worth it only for a repo you push to
  often.
- Flux clones over git; GitHub's **REST API rate limits do not apply**, so a 1m
  interval is fine for a handful of tenants.
- Removal is unchanged: delete the wiring file and its `kustomization.yaml`
  line, then revoke the deploy key / PAT by hand.

---

## Related

- [CONSUMING.md](CONSUMING.md) — library consumption, component toggles, image
  build, BYO keys (written for shape A; the toggles apply to all three).
- [ONBOARDING.md](ONBOARDING.md) — tenant + operator checklists, the full wiring
  file.
- [ARCHITECTURE.md](ARCHITECTURE.md) — how a request, a secret, and an image
  flow through the platform.
