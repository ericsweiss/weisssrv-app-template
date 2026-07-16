#!/usr/bin/env bash
# One-shot rename after forking this template.
#
# Replaces the placeholder tokens `changeme-app` and `changeme-group`
# throughout the tracked tree with your app slug and GitLab group.
#
#   ./scripts/rename.sh <app-slug> <gitlab-group>
#
# Example:
#   ./scripts/rename.sh recipe-box eric
#
# The app slug is also your Kubernetes namespace and your Flux Kustomization
# name, so keep it a valid DNS label (lowercase letters, digits, hyphens).
set -euo pipefail

app="${1:-}"
group="${2:-}"

if [ -z "$app" ] || [ -z "$group" ]; then
    echo "usage: $0 <app-slug> <gitlab-group>" >&2
    exit 2
fi

if ! printf '%s' "$app" | grep -Eq '^[a-z0-9]([-a-z0-9]*[a-z0-9])?$'; then
    echo "error: app slug '$app' must be a valid DNS label (lowercase, digits, hyphens)" >&2
    exit 2
fi

cd "$(dirname "$0")/.."

# Operate only on git-tracked files, skipping this script itself so it stays
# reusable. NUL-delimited to survive any odd filenames.
git ls-files -z \
    | grep -zv '^scripts/rename\.sh$' \
    | while IFS= read -r -d '' f; do
        if grep -Iq 'changeme-app\|changeme-group' "$f"; then
            # In-place edit that works on both GNU and BSD sed.
            tmp="$(mktemp)"
            sed -e "s/changeme-app/${app}/g" -e "s/changeme-group/${group}/g" "$f" >"$tmp"
            cat "$tmp" >"$f"
            rm -f "$tmp"
            echo "updated $f"
        fi
    done

echo
echo "Done. Review the diff (git diff), then:"
echo "  - set the container image in kubernetes/flux/deployment.yaml"
echo "  - update README.md / CODEOWNERS for your project"
echo "  - request operator wiring (see docs/ONBOARDING.md)"
