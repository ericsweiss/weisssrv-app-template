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
# matches are gone.
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
done

# TLS secret names use the domain's first label, not the whole domain.
ext_label="${CLUSTER_EXTERNAL_DOMAIN%%.*}"
int_label="${CLUSTER_INTERNAL_DOMAIN%%.*}"

# Ordered: the most specific literal first, so a longer host is consumed before
# the bare domain inside it can match. The two sentinels park the repository
# URLs out of reach of the domain rules, then come back untouched.
LIB_URL="git.ericsweiss.com/eric/weisssrv-lib"
WS_URL="git.ericsweiss.com/eric/weisssrv"
rules=(
    "s|${LIB_URL}|@@LIB_URL@@|g"
    "s|${WS_URL}|@@WS_URL@@|g"
    "s|registry.git.ericsweiss.com|${CLUSTER_REGISTRY_HOST}|g"
    "s|registry.git.esweiss.com|${CLUSTER_REGISTRY_PULL_HOST}|g"
    "s|esweiss.com/|${CLUSTER_NODE_LABEL_DOMAIN}/|g"
    "s|ericsweiss.com|${CLUSTER_EXTERNAL_DOMAIN}|g"
    "s|esweiss.com|${CLUSTER_INTERNAL_DOMAIN}|g"
    "s|-ericsweiss-tls|-${ext_label}-tls|g"
    "s|-esweiss-tls|-${int_label}-tls|g"
    "s|192.168.0.101|${CLUSTER_INTERNAL_VIP}|g"
    "s|\"infrastructure\"|\"${CLUSTER_PRIVILEGED_RUNNER_TAG}\"|g"
    "s|\`infrastructure\`|\`${CLUSTER_PRIVILEGED_RUNNER_TAG}\`|g"
    "s|@@LIB_URL@@|${LIB_URL}|g"
    "s|@@WS_URL@@|${WS_URL}|g"
)

script=""
for rule in "${rules[@]}"; do
    script+="${rule}
"
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
