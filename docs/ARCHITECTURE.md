# Architecture — how a tenant app rides the weisssrv platform

This repo contributes **one namespace** of workloads to the weisssrv k3s
cluster. The platform (Traefik, cert-manager, external-dns, External Secrets
Operator, Prometheus/Loki, MetalLB) is shared; your repo supplies the app and
its per-namespace CRs. Flux reconciles this repo's `kubernetes/flux/` directory
into your namespace.

## Request path (public route)

```mermaid
flowchart LR
  user([User]) -->|"changeme-app.ericsweiss.com"| cf["Cloudflare proxy"]
  cf -->|":443"| edge["Home router<br/>port-forward"]
  edge --> lb["MetalLB VIP<br/>.100 public / .101 internal"]
  lb --> traefik["Traefik IngressRoute<br/>+ hsts-header"]
  traefik --> svc["Service :8080"]
  svc --> pod["App pod<br/>non-root, read-only rootfs"]
```

- **Public** `*.ericsweiss.com`: the `IngressRoute` carries
  `external-dns.alpha.kubernetes.io/target: ericsweiss.com`; external-dns
  creates the proxied Cloudflare CNAME automatically. No operator action.
- **Internal** `*.esweiss.com` (opt-in): reached only on the LAN/tailnet via the
  `.101` VIP and the `lan-tailscale-only` middleware. The internal DNS name is
  **not** auto-provisioned — the operator adds an AdGuard rewrite. Activate the
  route by uncommenting `- optional/ingressroute-internal.yaml` and
  `- optional/certificate-internal.yaml` in `kubernetes/flux/kustomization.yaml`
  (see [CONSUMING.md](CONSUMING.md)).
- **TLS**: a per-host `Certificate` (ClusterIssuer `letsencrypt-prod`, DNS-01
  over Cloudflare) issues a secret into your namespace; Traefik terminates TLS
  from that secret. Per-host, not wildcard, to stay under Let's Encrypt's
  duplicate-certificate rate limit.

## Secret flow

```mermaid
flowchart LR
  subgraph backend["Secret backend"]
    op["1Password Homelab vault<br/>(prefixed items)"]
    gl["GitLab CI/CD variables"]
  end
  op --> css["ClusterSecretStore<br/>onepassword-slug / gitlab-slug<br/>(operator-owned)"]
  gl --> css
  css --> es["ExternalSecret<br/>(in your namespace)"]
  es --> sec[("Kubernetes Secret")]
  sec -->|secretKeyRef| pod["App pod"]
```

- Secret **values** never enter git. This repo holds only the `ExternalSecret`
  manifest, which references the operator-provisioned `ClusterSecretStore`.
- 1Password items are **prefixed with your slug** in the shared Homelab vault
  (`remoteRef.key: "<slug>: <Item>"`, `remoteRef.property: <field>`), which
  isolates them from other tenants. GitLab CI/CD variables are the alternative
  backend for collaborators without vault access.

## Storage & scheduling

- The template app is **stateless** and soft-avoids the storage node. For
  persistent, zvol-backed data (e.g. a database), the operator provisions a zvol
  on `pve-nas-01` and a matching PV, and the pod is **pinned** to the NAS node
  with a required hostname affinity plus a toleration for
  `esweiss.com/nas=true:PreferNoSchedule`. NFS-backed config volumes mount **by
  hostname** `pve-nas-01.esweiss.com` with `xprtsec=tls`. Persistent storage is
  an operator-assisted step — see ONBOARDING.md.

## Autoscaling & resilience

- **VPA** (`updateMode: Initial`) right-sizes the pod on natural restarts.
- **PDB** (`pdb.yaml`, always on) holds `minAvailable: 1`, so `kured`'s
  coordinated node-reboot drains can't evict both default replicas at once — a
  replica stays up across drains without opting into anything.
- **HPA** is opt-in (`optional/hpa.yaml`). Enabling it means uncommenting its
  line in `kubernetes/flux/kustomization.yaml`, dropping the static `replicas`,
  and making the VPA memory-only so the two never fight CPU (`minReplicas: 2`
  keeps the PDB satisfiable) — see [CONSUMING.md](CONSUMING.md).

## Observability

- **Metrics**: a `ServiceMonitor` is discovered cluster-wide by the platform
  Prometheus; the `NetworkPolicy` allows scrape from the `observability`
  namespace.
- **Alerts**: a `PrometheusRule` (down/stale) is likewise discovered
  cluster-wide and routed by the platform Alertmanager.
- **Reachability**: those alerts read kube-state metrics — they cover *replica
  availability*, not end-to-end HTTP/TLS reachability. A broken `IngressRoute`,
  a failed certificate, or a DNS problem would not fire them while the pod stays
  ready. The platform's blackbox exporter uses a **static, operator-managed**
  target list (there is no `Probe` CRD discovery), so reachability alerting for
  a public route is an **optional operator-added blackbox target** — see
  ONBOARDING.md.
- **Logs**: container stdout/stderr ships to Loki via the Alloy DaemonSet
  automatically — nothing to configure.

## What the operator owns vs. what you own

| Owned by the operator (weisssrv repo) | Owned by you (this repo) |
|---|---|
| The `Namespace` (with PSA labels) | Everything under `kubernetes/flux/` |
| The `GitRepository` + `Kustomization` wiring | Workload, Service, routing CRs |
| The `ClusterSecretStore` + bootstrap secret | `ExternalSecret` references |
| Internal `*.esweiss.com` AdGuard rewrites | Public routing (external-dns) |
| Platform middlewares, ClusterIssuer, VIPs | Per-host `Certificate` |
| Persistent storage (zvol/PV) provisioning | PVC references (if any) |
