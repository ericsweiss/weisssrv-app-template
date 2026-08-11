#!/usr/bin/env bash
# Thin wrapper around the weisssrv-new-project CLI's `prune ci:<shape>` command.
#
#   ./scripts/select-ci.sh gitlab   # self-hosted GitLab CI (the DEFAULT shape)
#   ./scripts/select-ci.sh github   # GitHub Actions
#   ./scripts/select-ci.sh none     # no pipeline at all — Flux-only deployment
#
# The template ships ALL THREE so it stays one repo rather than three forks;
# a project runs exactly one. Run this once, right after
# `./scripts/rename.sh <app> <group>`, then review `git diff` / `git status`
# and commit. Re-running with the same shape is a no-op.
#
# `weisssrv-new-project rename <app> <group> --ci <shape>` does the rename and
# the selection in one call, which is the shorter path for a new project.
#
# What each shape keeps, and what a github.com repo gives up versus self-hosted
# GitLab, is in docs/CI-SHAPES.md. Nothing under kubernetes/flux/ is touched —
# the manifests are CI-agnostic and identical in all three shapes, because Flux
# is what deploys them in all three. `.gitlab/{issue,merge_request}_templates/`
# also survive every shape: they are GitLab HOST metadata, not CI.
set -euo pipefail

# Pin the library version the CLI is fetched from (override with the env var to
# track a newer tag). Keep this in step with the ref: in .gitlab-ci.yml.
LIB_REF="${WEISSSRV_LIB_REF:-v0.6.0}"

# shellcheck source=scripts/lib-cli.sh
. "$(dirname "$0")/lib-cli.sh"

usage() {
    cat >&2 <<'EOF'
usage: ./scripts/select-ci.sh <gitlab|github|none>

  gitlab  keep .gitlab-ci.yml + .gitlab/         (drop .github/workflows/)
  github  keep .github/workflows/                (drop .gitlab-ci.yml and the
                                                  GitLab Secret-Detection ruleset)
  none    keep neither — Flux pulls and deploys this repo with no pipeline

See docs/CI-SHAPES.md.
EOF
    exit 2
}

[ "$#" -eq 1 ] || usage
case "$1" in
    gitlab | github | none) ;;
    *) usage ;;
esac

run_cli prune "ci:$1"
