#!/usr/bin/env bash
# Retarget this scaffold at a different cluster.
#
#   ./scripts/apply-cluster-identity.sh [identity-file]
#
# Reads scripts/cluster-identity.env (or the file given) and rewrites the
# cluster-level facts the scaffold hardcodes — domains, node-label prefix,
# internal VIP, registry hosts, forge URL, privileged-runner tag — across the
# deployable surface: kubernetes/, .gitlab-ci.yml, .github/workflows/ and
# Taskfile.yml. scripts/, tests/ and Markdown are left alone: the wrappers pin
# the weisssrv-lib location (which is not cluster identity) and the docs are
# prose to edit by hand.
#
# The shipped defaults ARE weisssrv's values, so a run with an unedited
# identity file changes nothing. Run it once after scripts/rename.sh; it is
# idempotent, and re-running after a change is a no-op because the literals it
# matches are gone. An identity whose OWN values contain one of those literals
# would break that promise on the second run, so it is refused up front (see the
# idempotence guard below) rather than written and discovered later.
#
# Two links are deliberately NOT rewritten: the weisssrv-lib project URL and the
# weisssrv runbook URL. Both name repositories, not this cluster.
set -euo pipefail

cd "$(dirname "$0")/.."
IDENTITY="${1:-scripts/cluster-identity.env}"
[ -f "$IDENTITY" ] || { echo "error: no identity file at $IDENTITY" >&2; exit 1; }
# shellcheck disable=SC1090  # path is an argument by design
. "./$IDENTITY"

for var in CLUSTER_EXTERNAL_DOMAIN CLUSTER_INTERNAL_DOMAIN \
    CLUSTER_NODE_LABEL_DOMAIN CLUSTER_INTERNAL_VIP CLUSTER_REGISTRY_HOST \
    CLUSTER_REGISTRY_PULL_HOST CLUSTER_PRIVILEGED_RUNNER_TAG; do
    [ -n "${!var:-}" ] || { echo "error: $var is unset or empty in $IDENTITY" >&2; exit 1; }
    case "${!var}" in *'|'*) echo "error: $var must not contain '|'" >&2; exit 1 ;; esac
    # `@@` is the sentinel marker below; a value containing one would be
    # rewritten by its own restore pass.
    case "${!var}" in *'@@'*) echo "error: $var must not contain '@@'" >&2; exit 1 ;; esac
done

# The external/internal split is load-bearing: the two Certificates and the two
# IngressRoutes are told apart ONLY by domain, and the two TLS Secrets only by
# the domain's first label. Collapse either and the pair collides — two
# cert-manager Certificates contending for one Secret (duplicate Let's Encrypt
# orders against the 5/week limit), or two IngressRoutes claiming one Host().
if [ "$CLUSTER_EXTERNAL_DOMAIN" = "$CLUSTER_INTERNAL_DOMAIN" ]; then
    echo "error: CLUSTER_EXTERNAL_DOMAIN and CLUSTER_INTERNAL_DOMAIN are both" \
         "'$CLUSTER_EXTERNAL_DOMAIN'; the external and internal IngressRoutes" \
         "would claim the same Host()" >&2
    exit 1
fi

# TLS secret names use the domain's first label, not the whole domain.
ext_label="${CLUSTER_EXTERNAL_DOMAIN%%.*}"
int_label="${CLUSTER_INTERNAL_DOMAIN%%.*}"
if [ "$ext_label" = "$int_label" ]; then
    echo "error: CLUSTER_EXTERNAL_DOMAIN ($CLUSTER_EXTERNAL_DOMAIN) and" \
         "CLUSTER_INTERNAL_DOMAIN ($CLUSTER_INTERNAL_DOMAIN) share the first" \
         "label '$ext_label'; both TLS Secrets would be named <app>-$ext_label-tls." \
         "Use domains whose first labels differ." >&2
    exit 1
fi

# The rules run as ONE sed script, in order, over each file — so a rule's output
# is still on the line when every later rule runs. Ordering alone does not make
# that safe: it decides which literal is CONSUMED first, not whether the
# replacement is re-matched. `-ericsweiss-tls` -> `-esweiss-tls` (external
# domain `esweiss.io`) is then hit by the `-esweiss-tls` rule, and both
# Certificates end up naming ONE Secret — the exact collision the first-label
# guard above rejects. The same holds for `esweiss.com/` -> a node-label domain
# that still contains `esweiss.com`, and for either registry host.
#
# So EVERY rule emits a sentinel instead of its value, and a second pass swaps
# the sentinels back. No rule can see another's output, which makes the pass
# order-independent and idempotent. Sentinels are `@@NAME@@`: no dot, no slash,
# nothing any pattern here matches, and rejected in the inputs above.
LIB_URL="git\.ericsweiss\.com/eric/weisssrv-lib"
WS_URL="git\.ericsweiss\.com/eric/weisssrv"
rules=(
    # Park: literal -> sentinel. Most specific first, so a longer host is
    # consumed before the bare domain inside it can match.
    "s|${LIB_URL}|@@LIB_URL@@|g"
    "s|${WS_URL}|@@WS_URL@@|g"
    "s|registry\.git\.ericsweiss\.com|@@REGISTRY_HOST@@|g"
    "s|registry\.git\.esweiss\.com|@@REGISTRY_PULL_HOST@@|g"
    "s|esweiss\.com/|@@NODE_LABEL_DOMAIN@@/|g"
    "s|ericsweiss\.com|@@EXTERNAL_DOMAIN@@|g"
    "s|esweiss\.com|@@INTERNAL_DOMAIN@@|g"
    "s|-ericsweiss-tls|-@@EXTERNAL_LABEL@@-tls|g"
    "s|-esweiss-tls|-@@INTERNAL_LABEL@@-tls|g"
    "s|192\.168\.0\.101|@@INTERNAL_VIP@@|g"
    "s|\"infrastructure\"|\"@@RUNNER_TAG@@\"|g"
    "s|\`infrastructure\`|\`@@RUNNER_TAG@@\`|g"
    # Restore: sentinel -> value. Order is irrelevant — the sentinels are
    # distinct and no value may contain one.
    "s|@@LIB_URL@@|${LIB_URL}|g"
    "s|@@WS_URL@@|${WS_URL}|g"
    "s|@@REGISTRY_HOST@@|${CLUSTER_REGISTRY_HOST}|g"
    "s|@@REGISTRY_PULL_HOST@@|${CLUSTER_REGISTRY_PULL_HOST}|g"
    "s|@@NODE_LABEL_DOMAIN@@|${CLUSTER_NODE_LABEL_DOMAIN}|g"
    "s|@@EXTERNAL_DOMAIN@@|${CLUSTER_EXTERNAL_DOMAIN}|g"
    "s|@@INTERNAL_DOMAIN@@|${CLUSTER_INTERNAL_DOMAIN}|g"
    "s|@@EXTERNAL_LABEL@@|${ext_label}|g"
    "s|@@INTERNAL_LABEL@@|${int_label}|g"
    "s|@@INTERNAL_VIP@@|${CLUSTER_INTERNAL_VIP}|g"
    "s|@@RUNNER_TAG@@|${CLUSTER_PRIVILEGED_RUNNER_TAG}|g"
)

script=""
park_rules=()
restore_script=""
for rule in "${rules[@]}"; do
    script+="${rule}
"
    case "$rule" in
        "s|@@"*) restore_script+="${rule}
" ;;
        *) park_rules+=("$rule") ;;
    esac
done

# Sentinels make ONE pass order-independent. They do not make a SECOND pass a
# no-op: the second run sees the values this run wrote, and rewrites any of them
# that still contains a literal the park pass consumes. With
# CLUSTER_EXTERNAL_DOMAIN=esweiss.io the external Secret becomes
# `-esweiss-tls` — which is exactly the literal the INTERNAL label rule parks —
# so a second run renames it to `-<internal label>-tls` and the two
# Certificates collide on one Secret. That is the failure the first-label guard
# above rejects, arriving one run later.
#
# So refuse the identity instead of silently writing a tree that only survives
# being retargeted once. The test IS the property: take what each rule writes
# (its parked replacement with the restore pass applied) and feed it back
# through the whole script. Anything that comes out different is a value the
# next run would rewrite. Running the real script means rule ORDER and the
# sentinels are honoured, so the deliberately-preserved forge URLs — which do
# contain `ericsweiss.com` but are parked by an earlier, longer rule — are not
# false positives. Everything is derived from `rules`, so a rule added later is
# covered with no second list to keep in step.
for rule in "${park_rules[@]}"; do
    body="${rule#s|}"
    body="${body%|g}"
    literal="${body%%|*}"
    written="$(printf '%s' "${body#*|}" | sed -e "$restore_script")"
    rewritten="$(printf '%s' "$written" | sed -e "$script")"
    if [ "$rewritten" = "$written" ]; then
        continue
    fi
    echo "error: this identity is not safe to re-apply." \
         "'${literal}' becomes '${written}', which this same script rewrites" \
         "again — to '${rewritten}'. The second run would not be a no-op, and" \
         "for the TLS-secret rules it collapses the external and internal" \
         "Secrets onto one name (two Certificates contending for one Secret)." \
         "Nothing has been written. Pick values that do not contain the" \
         "literals this script matches — most often a domain whose first label" \
         "is one of the scaffold's own." >&2
    exit 1
done

changed=0
while IFS= read -r path; do
    case "$path" in
        scripts/* | tests/* | *.md) continue ;;
        kubernetes/* | .gitlab-ci.yml | .github/workflows/* | Taskfile.yml) ;;
        *) continue ;;
    esac
    [ -f "$path" ] || continue   # `git ls-files` still lists a staged deletion
    before="$(cat "$path")"
    after="$(printf '%s' "$before" | sed -e "$script")"
    if [ "$before" != "$after" ]; then
        printf '%s\n' "$after" > "$path"
        echo "updated $path"
        changed=$((changed + 1))
    fi
done < <(git ls-files)

if [ "$changed" -eq 0 ]; then
    echo "cluster identity already applied — nothing to change"
else
    echo "$changed file(s) retargeted; review \`git diff\` before committing"
fi
