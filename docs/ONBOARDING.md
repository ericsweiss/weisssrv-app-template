# Onboarding — getting this repo deploying to the cluster

Two roles are involved: the **tenant** (you, building the app) and the
**operator** (whoever runs weisssrv). The tenant fills in workloads; the
operator adds one wiring file to the weisssrv repo. This mirrors
[`docs/30-multi-repo-onboarding.md`](https://git.ericsweiss.com/eric/weisssrv/-/blob/main/docs/30-multi-repo-onboarding.md)
— read that for the authoritative platform detail.

`<slug>` below is your app slug: it is the repo name, the namespace, and the
Flux Kustomization name. Keep it a valid DNS label.

---

## Step 0 — cluster identity

This template is calibrated for **one** cluster: `weisssrv`. Its domains, node
labels, VIP, registry hosts and runner tag are literals, not variables. **A
weisssrv tenant skips this section** — everything already points at the right
place.

If you are deploying into a cluster generated from `weisssrv-cluster-template`,
those literals are collected in one seam. Edit `scripts/cluster-identity.env`
and run the applier once, right after `rename.sh`:

```bash
$EDITOR scripts/cluster-identity.env
./scripts/apply-cluster-identity.sh
git diff                      # always review before committing
```

| Variable | Means | Default |
|---|---|---|
| `CLUSTER_EXTERNAL_DOMAIN` | public hostnames `<app>.<domain>`, and the external-dns target | `ericsweiss.com` |
| `CLUSTER_INTERNAL_DOMAIN` | LAN/tailnet hostnames on the `optional/*-internal` manifests | `esweiss.com` |
| `CLUSTER_NODE_LABEL_DOMAIN` | prefix of the cluster's node labels (`…/nas`, `…/cpu`) | `esweiss.com` |
| `CLUSTER_INTERNAL_VIP` | where the operator points the internal hostname | `192.168.0.101` |
| `CLUSTER_REGISTRY_HOST` | registry the `build-image` job pushes to | `registry.git.ericsweiss.com` |
| `CLUSTER_REGISTRY_PULL_HOST` | registry the **nodes** pull from (differs on weisssrv, to avoid a hairpin) | `registry.git.esweiss.com` |
| `CLUSTER_PRIVILEGED_RUNNER_TAG` | runner tag for the one privileged job | `infrastructure` |

The applier rewrites `kubernetes/`, `.gitlab-ci.yml`, `.github/workflows/` and
`Taskfile.yml`. It leaves `scripts/`, `tests/` and Markdown alone, and never
rewrites the two repository URLs (`…/eric/weisssrv-lib`, `…/eric/weisssrv`) —
those name repositories, not your cluster. The defaults *are* weisssrv's values,
so running it unedited changes nothing, and it is idempotent.

Three things it does **not** cover, because they are choices rather than
substitutions: `kubernetes/flux/networkpolicy.yaml` (the platform namespace
names `traefik`/`observability`, the excluded private CIDRs, and the LAN host
IPs in its two commented opt-in egress rules), the ClusterIssuer name
(`letsencrypt-prod`) in `certificate.yaml`, and the weisssrv runbook URLs in
`prometheusrule.yaml`. Check those by hand.

The library's account of which *mechanisms* are pluggable (secrets backend,
storage, forge) and which are backend-by-design is
[EXTENSIBILITY.md](https://git.ericsweiss.com/eric/weisssrv-lib/-/blob/main/docs/EXTENSIBILITY.md).

---

## Tenant checklist

1. **Fork this project** and clone your new project. (You can instead use
   *New project → Create from template* only if the operator has registered
   this template as a group/instance custom template — otherwise it won't
   appear in the picker.)
2. **Rename**: `./scripts/rename.sh <slug> <gitlab-group>` substitutes the
   app-slug and GitLab-group placeholders across the tree (it wraps the
   `weisssrv-new-project` CLI). Review `git diff`, then `grep -rn 'changeme[-]' .`
   to confirm nothing was missed — the bracket keeps the pattern from matching
   this line, so a clean project gets zero hits. To also drop/enable optional components, use
   the CLI's `prune` and the `optional/` add-ons — see
   [CONSUMING.md](CONSUMING.md).
   Then **pick a CI shape**: `./scripts/select-ci.sh <gitlab|github|none>`
   (default `gitlab`). That decides only what *checks* your changes and where
   the image is built — Flux deploys the repo in all three shapes. Tell the
   operator which you picked: it changes the `GitRepository` in their wiring
   file (step O3). See [CI-SHAPES.md](CI-SHAPES.md).
   If this is **not** the weisssrv cluster, run
   `./scripts/apply-cluster-identity.sh` here too — Step 0 above.
3. **Set the image** in `kubernetes/flux/deployment.yaml`. The shipped value is a
   **placeholder no registry has** (`…/changeme-group/changeme-app:REPLACE-ME`) —
   leave it and the first reconcile `ImagePullBackOff`s. Point it at an upstream
   image, or at one you build: CI builds the repo-root `Dockerfile` **by
   default** (the `build-image` job, on a privileged runner) and pushes
   `$CI_REGISTRY_IMAGE:<short-sha>`, so the usual first edit is that tag. `task
   build` builds the same image locally. The tag is a **literal pin** — bump it
   yourself in an MR (see the README "Keeping image tags current" note). If the
   project registry is private, tell the operator: the namespace needs a pull
   credential (step O2b).
4. **Set the hostname(s)**. Public `<slug>.ericsweiss.com` is ready in
   `ingressroute.yaml`. For an internal `<slug>.esweiss.com` route, uncomment
   **both** `- optional/ingressroute-internal.yaml` and
   `- optional/certificate-internal.yaml` in `kubernetes/flux/kustomization.yaml`,
   and ask the operator for the AdGuard rewrite (step O5).
5. **Wire secrets** (if any) in `externalsecret.yaml` — that is the 1Password
   backend. For the GitLab CI/CD-variable backend instead, uncomment
   `- optional/externalsecret-gitlab.yaml` and **remove** `- externalsecret.yaml`
   from the same list; both create the same Secret, so run exactly one. Match
   what the operator provisions (step O3). If the app needs no secrets, delete
   `externalsecret.yaml`, its line in `kustomization.yaml`, and the secret `env`
   block in `deployment.yaml`.
6. **Point observability** at your metrics endpoint in `servicemonitor.yaml`
   (or delete it if the app exposes none). Adjust the alert expressions in
   `prometheusrule.yaml` if you renamed the Deployment.
7. **Decide about `tests/`** — it gates the *template*, not your app, and skips
   itself once renamed. Deleting it means also removing the
   `/ci/test/python-tests.yml` include and the `python-tests:` override, or the
   job fails on a missing directory; keeping it means the operator must
   allowlist your project (step O9). Both paths are spelled out in
   [CONSUMING.md](CONSUMING.md#removing-the-templates-gate).
8. `task lint`, commit on a branch, open an MR/PR. CI must be green. (In shape
   `none` there is no pipeline — `task lint` and the pre-commit hooks are the
   whole gate.)
9. **Request wiring** from the operator (hand them your slug, group, chosen
   secret backend, your **repo URL and CI shape**, whether you need an internal
   hostname, whether your image registry is private (step O2b), whether you want
   SSO (step O8), and whether you kept `tests/` (step O9)).
10. After the operator merges the wiring, merge your `main`. Verify with
    `task flux:status` / `kubectl get pods -n <slug>` (kubeconfig from operator).

---

## Operator checklist (performed in the weisssrv repo)

The tenant's CI shape (`gitlab` / `github` / `none`) changes exactly one thing
here: the `GitRepository` in step O3 — its URL, and whether it needs a
`secretRef` for a deploy key or PAT. Everything else is identical, because Flux
is the deployer in all three shapes and no CI ever applies to the cluster. The
GitHub commands are in [CI-SHAPES.md](CI-SHAPES.md).

### O1 — Pick a secret backend

Prepare it:

- *1Password (Option C, recommended):* create prefixed items in the Homelab
  vault (title `"<slug>: <Item>"`), and a scoped Connect token (find the
  server ID with `op connect server list`, then `op connect token create
  weisssrv-<slug>-eso --server <ID> --vaults Homelab`).
- *GitLab CI/CD variables:* have the collaborator create a project access
  token (Reporter, `read_api`).

### O2 — Bootstrap the namespace secret

One-time, not Flux-managed:

```bash
kubectl create namespace <slug>
# 1Password backend:
kubectl -n <slug> create secret generic onepassword-connect-token \
  --from-literal=token=<CONNECT_TOKEN>
# or GitLab backend:
kubectl -n <slug> create secret generic gitlab-api-token \
  --from-literal=token=glpat-<TOKEN>
```

### O2b — Registry pull credential (only if the image is private)

A GitLab *project* registry is private by default, so the kubelet cannot pull the
tenant's own build without a credential — and the shipped `build-image` job
pushes exactly there, so most tenants need this.

1. In the **tenant** project, create a deploy token scoped `read_registry`
   (Settings → Repository → Deploy tokens). The token *name* is not a secret; the
   value is.
2. Store the value in the tenant's secret backend under the field
   `kubernetes/flux/optional/externalsecret-registry.yaml` reads.
3. Tell the tenant to enable that manifest: uncomment
   `- optional/externalsecret-registry.yaml` in
   `kubernetes/flux/kustomization.yaml` and add
   `imagePullSecrets: [{name: <slug>-registry}]` to the pod spec.

Nodes pull over the **internal** registry host (AdGuard rewrite → Traefik → the
GitLab VM registry; same backend, no hairpin NAT). The shape mirrors weisssrv's
own `hermes-registry-pull`
([externalsecret.yaml](https://git.ericsweiss.com/eric/weisssrv/-/blob/main/kubernetes/apps/hermes/externalsecret.yaml)).
Skip this step entirely if the tenant runs an upstream image or makes its package
registry public.

### O3 — Add the wiring file

`kubernetes/clusters/weisssrv/tenants/<slug>.yaml` (1Password variant shown; swap the `ClusterSecretStore` for the GitLab
provider if using that backend):
```yaml
---
apiVersion: v1
kind: Namespace
metadata:
  name: <slug>
  labels:
    app.kubernetes.io/managed-by: flux
    fluxcd.io/tenant: <slug>
    # Pod Security Admission — baseline enforced, restricted advised.
    pod-security.kubernetes.io/enforce: baseline
    pod-security.kubernetes.io/warn: restricted
    pod-security.kubernetes.io/audit: restricted
---
apiVersion: external-secrets.io/v1
kind: ClusterSecretStore
metadata:
  name: onepassword-<slug>
spec:
  provider:
    onepassword:
      connectHost: http://onepassword-connect.external-secrets.svc.cluster.local:8080
      vaults:
        Homelab: 1
      auth:
        secretRef:
          connectTokenSecretRef:
            name: onepassword-connect-token
            namespace: <slug>
            key: token
---
apiVersion: source.toolkit.fluxcd.io/v1
kind: GitRepository
metadata:
  name: <slug>
  namespace: flux-system
spec:
  interval: 1m
  # A tenant repo may live anywhere Flux can clone from — this is the only
  # part of the wiring the tenant's CI shape changes. For a GitHub-hosted
  # repo use https://github.com/<owner>/<repo> (public), or the ssh:// form
  # plus `secretRef:` for a private one; exact commands in CI-SHAPES.md
  # ("Operator wiring for a GitHub-hosted tenant").
  url: https://git.ericsweiss.com/<group>/<slug>
  ref:
    branch: main
---
# A namespace-scoped ServiceAccount so kustomize-controller does NOT apply
# tenant manifests with its own cluster-admin. SA lives in flux-system; the
# RoleBindings grant admin only in the tenant namespace.
apiVersion: v1
kind: ServiceAccount
metadata:
  name: <slug>-flux
  namespace: flux-system
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: <slug>-flux-admin
  namespace: <slug>
subjects:
  - kind: ServiceAccount
    name: <slug>-flux
    namespace: flux-system
roleRef:
  kind: ClusterRole
  name: admin
  apiGroup: rbac.authorization.k8s.io
---
# `admin` does NOT aggregate three platform CRD groups this template uses —
# traefik.io (IngressRoute/Middleware), monitoring.coreos.com
# (ServiceMonitor/PrometheusRule) and autoscaling.k8s.io (VPA). It normally
# DOES cover external-secrets.io (ExternalSecret) and cert-manager.io
# (Certificate) via its aggregated *-edit roles — but the shared
# `tenant-crd-editor` ClusterRole covers those two belt-and-suspenders as well
# (so a cluster missing the external-secrets-edit / cert-manager-edit
# aggregated roles still applies them). Bind it (shipped in the weisssrv repo
# at kubernetes/clusters/weisssrv/tenants/tenant-crd-editor.yaml) too, or the
# tenant Kustomization goes NotReady on the first IngressRoute/ServiceMonitor/
# PrometheusRule/VPA. See docs/30.
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: <slug>-flux-crd-editor
  namespace: <slug>
subjects:
  - kind: ServiceAccount
    name: <slug>-flux
    namespace: flux-system
roleRef:
  kind: ClusterRole
  name: tenant-crd-editor
  apiGroup: rbac.authorization.k8s.io
---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: <slug>
  namespace: flux-system
spec:
  interval: 10m
  retryInterval: 1m
  timeout: 10m
  # Wait for the platform CRD chain (sources -> controllers -> configs) so a
  # fresh bootstrap doesn't apply this tenant's ExternalSecret/IngressRoute/
  # etc. before the ESO/cert-manager/Traefik CRDs exist. Mirrors apps.yaml.
  dependsOn:
    - name: infrastructure-configs
  serviceAccountName: <slug>-flux
  sourceRef:
    kind: GitRepository
    name: <slug>
  path: ./kubernetes/flux
  prune: true
  targetNamespace: <slug>
  wait: true
```

### O4 — Register it

In `kubernetes/clusters/weisssrv/tenants/kustomization.yaml` (Kustomize does not
auto-discover):

```yaml
resources:
  - tenant-crd-editor.yaml   # shared ClusterRole (already present)
  - <slug>.yaml              # add this line
```

### O5 — Internal DNS (only if the tenant needs `<slug>.esweiss.com`)

Add an AdGuard rewrite in `ansible/inventories/prod/group_vars/dns.yml`
(`{domain: "<slug>.{{ internal_domain }}", answer: "192.168.0.101"}`) and run
the `adguard_home` role. Public `*.ericsweiss.com` needs nothing here —
external-dns handles it.

### O6 — Persistent storage (only if the tenant needs a zvol-backed DB)

Add the zvol to `hosts.yml` under `k3s-agt-nas-01` (`vm_additional_disks`, next free
`scsi_slot`, `vzdump_backup: false`), run `task k3s:deploy -- --limit
k3s-agt-nas-01`, and hand the tenant the PV/PVC + NAS-pin pattern. Children of
`ssd/appdata` are backed up to `archive` automatically.

### O7 — Reachability probe (optional, recommended for user-facing routes)

The tenant's `PrometheusRule` only alerts on replica availability. To also alert
on end-to-end HTTP/TLS reachability (a broken `IngressRoute`, cert, or DNS
while replicas stay ready), add the public host to the static blackbox target
list in
`kubernetes/infrastructure/observability/exporters/blackbox-exporter.yaml`
(`values.serviceMonitor.targets`) — `module: http_2xx` for an open route or
`http_sso` for an SSO-gated one.

### O8 — SSO objects (only if the tenant runs `wire sso`)

The tenant's forward-auth middleware reference does nothing on its own. Authentik
state is codified in weisssrv's `terraform/authentik`
([docs/40](https://git.ericsweiss.com/eric/weisssrv/-/blob/main/docs/40-authentik-terraform.md))
— edit the `.tf` files, never the Authentik UI. For a **proxy** provider
(forward-auth, the case this template's middleware targets):

| File | Add |
|---|---|
| `providers_proxy.tf` | a `forward_single` provider whose `external_host` is **exactly** the route's hostname, scheme included |
| `applications.tf` | the application bound to that provider |
| `groups.tf` | the access group (`<slug>-users`) |
| `policy_bindings.tf` | the binding that gates the application on that group |
| `outpost.tf` | the provider added to the embedded outpost's provider list |

For an **OIDC** provider (the app does its own login) the set is
`providers_oauth2.tf`, `applications.tf`, `groups.tf`, `policy_bindings.tf`, plus
a `variables.tf` entry for the client secret, the matching `TF_VAR_*` anchor in
weisssrv's `Taskfile.yml`, and the `authentik-drift-plan` job's variable list.

Then `task terraform:authentik-plan`, review, and apply supervised.

Two failure modes worth knowing before you start: a provider that is not on the
outpost's list answers every request with a 404 from the outpost, and an
`external_host` set to the *other* split-horizon hostname breaks the redirect
loop rather than erroring.

### O9 — Job-token allowlist (only while the tenant keeps `tests/`)

The template's `python-tests` job clones `eric/weisssrv-lib` with `$CI_JOB_TOKEN`.
GitLab's job-token allowlist is per-project, so a tenant project that is not on
`eric/weisssrv-lib`'s list gets a 403 in `before_script` and the job fails before
pytest runs. Either add the tenant project (weisssrv-lib → Settings → CI/CD →
Job token permissions → Allowlist), or tell the tenant to delete the gate — see
[CONSUMING.md § Removing the template's gate](CONSUMING.md#removing-the-templates-gate).
The job only tests the scaffold, so deleting it costs the tenant nothing.

### O10 — Branch → MR → merge

Flux reconciles the new tenant on the next cycle.

### Removal

`git rm` the wiring file and its `kustomization.yaml` line → Flux prunes the
Kustomization, GitRepository, ClusterSecretStore, and namespace. Then manually
revoke the Connect token / GitLab PAT and delete the prefixed 1Password items.
PVCs survive (finalizers) and are cleaned up by hand.
