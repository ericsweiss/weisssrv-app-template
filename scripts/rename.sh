#!/usr/bin/env bash
# Thin wrapper: delegates to the weisssrv-new-project CLI's `rename` command.
#
#   ./scripts/rename.sh <app-slug> <gitlab-group>
#
# Examples:
#   ./scripts/rename.sh recipe-box eric          # top-level group
#   ./scripts/rename.sh recipe-box eric/apps     # nested subgroup
#
# The app slug is also your Kubernetes namespace and Flux Kustomization name, so
# keep it a valid DNS label (lowercase letters, digits, hyphens). The group is
# your GitLab namespace path and may be nested (contain slashes). The CLI
# validates both before touching anything.
#
# For prune / wire / verify, call the CLI directly — see docs/CONSUMING.md.
set -euo pipefail

# Pin the library version the CLI is fetched from (override with the env var to
# track a newer tag). Keep this in step with the ref: in .gitlab-ci.yml.
LIB_REF="${WEISSSRV_LIB_REF:-v0.6.0}"

# shellcheck source=scripts/lib-cli.sh
. "$(dirname "$0")/lib-cli.sh"

run_cli rename "$@"
