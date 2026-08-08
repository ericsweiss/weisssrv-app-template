# Onboarding — getting this repo deploying to the cluster

Two roles are involved: the **tenant** (you, building the app) and the
**operator** (whoever runs weisssrv). The tenant fills in workloads; the
operator adds one wiring file to the weisssrv repo. This mirrors
[`docs/30-multi-repo-onboarding.md`](https://git.ericsweiss.com/eric/weisssrv/-/blob/main/docs/30-multi-repo-onboarding.md)
— read that for the authoritative platform detail.

`<slug>` below is your app slug: it is the repo name, the namespace, and the
Flux Kustomization name. Keep it a valid DNS label.

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
   the CLI's `prune` / `wire` — see [CONSUMING.md](CONSUMING.md).
   Then **pick a CI shape**: `./scripts/select-ci.sh <gitlab|github|none>`
   (default `gitlab`). That decides only what *checks* your changes and where
   the image is built — Flux deploys the repo in all three shapes. Tell the
   operator which you picked: it changes the `GitRepository` in their wiring
   file (step O3). See [CI-SHAPES.md](CI-SHAPES.md).
3. **Set the image** in `kubernetes/flux/deployment.yaml` — an upstream image,
   or one you build (locally with `task build`, or via the opt-in CI build-image
   job on a privileged runner) and push to
   `registry.git.ericsweiss.com/<group>/<slug>`. The tag is a **literal pin** —
   bump it yourself in an MR (see the README "Keeping image tags current" note).
4. **Set the hostname(s)**. Public `<slug>.ericsweiss.com` is ready in
   `ingressroute.yaml`. For an internal `<slug>.esweiss.com` route, uncomment
   the internal `IngressRoute` (in `ingressroute.yaml`) and the internal
   `Certificate` (in `certificate.yaml`), and ask the operator for the AdGuard
   rewrite (step O5).
5. **Wire secrets** (if any) in `externalsecret.yaml` — pick the 1Password or
   GitLab backend to match what the operator provisions (step O3). If the app
   needs none, delete `externalsecret.yaml`, its line in `kustomization.yaml`,
   and the secret `env` block in `deployment.yaml`.
6. **Point observability** at your metrics endpoint in `servicemonitor.yaml`
   (or delete it if the app exposes none). Adjust the alert expressions in
   `prometheusrule.yaml` if you renamed the Deployment.
7. `task lint`, commit on a branch, open an MR/PR. CI must be green. (In shape
   `none` there is no pipeline — `task lint` and the pre-commit hooks are the
   whole gate.)
8. **Request wiring** from the operator (hand them your slug, group, chosen
   secret backend, your **repo URL and CI shape**, and whether you need an
   internal hostname).
9. After the operator merges the wiring, merge your `main`. Verify with
   `task flux:status` / `kubectl get pods -n <slug>` (kubeconfig from operator).

---

## Operator checklist (performed in the weisssrv repo)

The tenant's CI shape (`gitlab` / `github` / `none`) changes exactly one thing
here: the `GitRepository` in step O3 — its URL, and whether it needs a
`secretRef` for a deploy key or PAT. Everything else is identical, because Flux
is the deployer in all three shapes and no CI ever applies to the cluster. The
GitHub commands are in [CI-SHAPES.md](CI-SHAPES.md).

1. **O1 — Pick a secret backend** and prepare it:
   - *1Password (Option C, recommended):* create prefixed items in the Homelab
     vault (title `"<slug>: <Item>"`), and a scoped Connect token (find the
     server ID with `op connect server list`, then `op connect token create
     weisssrv-<slug>-eso --server <ID> --vaults Homelab`).
   - *GitLab CI/CD variables:* have the collaborator create a project access
     token (Reporter, `read_api`).
2. **O2 — Bootstrap the namespace secret** (one-time, not Flux-managed):
   ```bash
   kubectl create namespace <slug>
   # 1Password backend:
   kubectl -n <slug> create secret generic onepassword-connect-token \
     --from-literal=token=<CONNECT_TOKEN>
   # or GitLab backend:
   kubectl -n <slug> create secret generic gitlab-api-token \
     --from-literal=token=glpat-<TOKEN>
   ```
3. **O3 — Add the wiring file** `kubernetes/clusters/weisssrv/tenants/<slug>.yaml`
   (1Password variant shown; swap the `ClusterSecretStore` for the GitLab
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
4. **O4 — Register it** in `kubernetes/clusters/weisssrv/tenants/kustomization.yaml`
   (Kustomize does not auto-discover):
   ```yaml
   resources:
     - tenant-crd-editor.yaml   # shared ClusterRole (already present)
     - <slug>.yaml              # add this line
   ```
5. **O5 — Internal DNS (only if the tenant needs `<slug>.esweiss.com`)**: add an
   AdGuard rewrite in `ansible/inventories/prod/group_vars/dns.yml`
   (`{domain: "<slug>.{{ internal_domain }}", answer: "192.168.0.101"}`) and run
   the `adguard_home` role. Public `*.ericsweiss.com` needs nothing here —
   external-dns handles it.
6. **O6 — Persistent storage (only if the tenant needs a zvol-backed DB)**: add the
   zvol to `hosts.yml` under `k3s-agt-nas-01` (`vm_additional_disks`, next free
   `scsi_slot`, `vzdump_backup: false`), run `task k3s:deploy -- --limit
   k3s-agt-nas-01`, and hand the tenant the PV/PVC + NAS-pin pattern. Children of
   `ssd/appdata` are backed up to `archive` automatically.
7. **O7 — Reachability probe (optional, recommended for user-facing routes)**: the
   tenant's `PrometheusRule` only alerts on replica availability. To also alert
   on end-to-end HTTP/TLS reachability (a broken `IngressRoute`, cert, or DNS
   while replicas stay ready), add the public host to the static blackbox target
   list in
   `kubernetes/infrastructure/observability/exporters/blackbox-exporter.yaml`
   (`values.serviceMonitor.targets`) — `module: http_2xx` for an open route or
   `http_sso` for an SSO-gated one.
8. **O8 — Branch → MR → merge.** Flux reconciles the new tenant on the next cycle.

### Removal

`git rm` the wiring file and its `kustomization.yaml` line → Flux prunes the
Kustomization, GitRepository, ClusterSecretStore, and namespace. Then manually
revoke the Connect token / GitLab PAT and delete the prefixed 1Password items.
PVCs survive (finalizers) and are cleaned up by hand.
