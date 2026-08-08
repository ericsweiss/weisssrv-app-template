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
LIB_REF="${WEISSSRV_LIB_REF:-v0.3.1}"
LIB_SPEC="git+https://git.ericsweiss.com/eric/weisssrv-lib.git@${LIB_REF}#subdirectory=cli"

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

if command -v weisssrv-new-project >/dev/null 2>&1; then
    exec weisssrv-new-project prune "ci:$1"
elif command -v pipx >/dev/null 2>&1; then
    exec pipx run --spec "$LIB_SPEC" weisssrv-new-project prune "ci:$1"
else
    echo "error: the weisssrv-new-project CLI is required but was not found." >&2
    echo "Install it (from a weisssrv-lib checkout, or straight from git):" >&2
    echo "  pipx install --spec '${LIB_SPEC}' weisssrv-new-project" >&2
    echo "  # or, from a local library checkout: pip install ./cli" >&2
    echo "then re-run:  weisssrv-new-project prune ci:$1" >&2
    exit 1
fi
