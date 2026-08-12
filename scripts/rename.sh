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

# The library pin lives in ONE place: variables.WEISSSRV_LIB_REF in
# .gitlab-ci.yml. Honour an explicit env override, otherwise read it from there
# so the tag is never restated here. (GitLab cannot interpolate the include:
# refs, so check-lib-pins.py keeps those in step with the same source.)
LIB_REF="${WEISSSRV_LIB_REF:-$(sed -n 's/^  WEISSSRV_LIB_REF: "\(v[0-9][0-9.]*\)".*/\1/p' "$(dirname "$0")/../.gitlab-ci.yml")}"

# shellcheck source=scripts/lib-cli.sh
. "$(dirname "$0")/lib-cli.sh"

run_cli rename "$@"
