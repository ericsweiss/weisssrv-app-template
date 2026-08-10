#!/usr/bin/env bash
# Thin wrapper around the weisssrv-new-project CLI's `rename` command.
#
# The placeholder-substitution logic that used to live here now ships in the
# shared library's CLI (weisssrv-lib, cli/weisssrv_lib_cli) as `rename`, which
# supersedes this script — one tested implementation, alongside `prune`, `wire`
# and `verify`. This wrapper keeps `./scripts/rename.sh <app> <group>` working
# for the plain "create from template" flow by delegating to that CLI.
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
LIB_REF="${WEISSSRV_LIB_REF:-v0.5.2}"
LIB_SPEC="git+https://git.ericsweiss.com/eric/weisssrv-lib.git@${LIB_REF}#subdirectory=cli"

if command -v weisssrv-new-project >/dev/null 2>&1; then
    exec weisssrv-new-project rename "$@"
elif command -v pipx >/dev/null 2>&1; then
    exec pipx run --spec "$LIB_SPEC" weisssrv-new-project rename "$@"
else
    echo "error: the weisssrv-new-project CLI is required but was not found." >&2
    echo "Install it (from a weisssrv-lib checkout, or straight from git):" >&2
    echo "  pipx install --spec '${LIB_SPEC}' weisssrv-new-project" >&2
    echo "  # or, from a local library checkout: pip install ./cli" >&2
    echo "then re-run:  weisssrv-new-project rename $*" >&2
    exit 1
fi
