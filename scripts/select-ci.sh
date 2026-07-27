#!/usr/bin/env bash
# Select this project's CI shape, and prune the two you didn't pick.
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
# What each shape keeps, and what a github.com repo gives up versus self-hosted
# GitLab, is in docs/CI-SHAPES.md. Nothing under kubernetes/flux/ is touched —
# the manifests are CI-agnostic and identical in all three shapes, because Flux
# is what deploys them in all three.
#
# WHY THIS IS A LOCAL SCRIPT: the component toggles (`prune metrics`, `wire
# hpa`, ...) live in the shared library's `weisssrv-new-project` CLI, which
# knows only the kubernetes/flux tree. CI-shape selection is not in it yet.
# When it lands (as `weisssrv-new-project prune ci:<shape>`), this script
# becomes a thin wrapper over that command — exactly what happened to
# scripts/rename.sh — and the interface here does not change.
set -euo pipefail

usage() {
    cat >&2 <<'EOF'
usage: ./scripts/select-ci.sh <gitlab|github|none>

  gitlab  keep .gitlab-ci.yml + .gitlab/         (drop .github/workflows/)
  github  keep .github/workflows/                (drop .gitlab-ci.yml and the
                                                  GitLab Secret-Detection ruleset)
  none    keep neither — Flux pulls and deploys this repo with no pipeline

See docs/CI-SHAPES.md.
EOF
}

# Paths that belong to exactly one shape. `.gitlab/{issue,merge_request}_
# templates/` are deliberately NOT listed: they are GitLab HOST metadata, not
# CI, and stay useful on a GitLab repo (or mirror) that runs no pipeline.
# Delete them by hand if this project has no GitLab side at all.
GITLAB_CI_PATHS=(".gitlab-ci.yml" ".gitlab/secret-detection-ruleset.toml")
GITHUB_CI_PATHS=(".github/workflows")

drop() {
    local removed=0
    for path in "$@"; do
        if [ -e "$path" ]; then
            rm -rf -- "$path"
            echo "  removed $path"
            removed=1
        fi
    done
    [ "$removed" -eq 1 ] || echo "  (nothing to remove — already applied)"
    # Leave no empty parent behind. `rmdir` on a non-empty dir fails harmlessly,
    # so a project that later adds .github/ISSUE_TEMPLATE keeps it.
    for parent in .github .gitlab; do
        [ -d "$parent" ] && rmdir "$parent" 2>/dev/null || true
    done
}

main() {
    if [ "$#" -ne 1 ]; then
        usage
        exit 2
    fi

    case "$1" in
        gitlab)
            echo "CI shape: gitlab — keeping .gitlab-ci.yml"
            drop "${GITHUB_CI_PATHS[@]}"
            ;;
        github)
            echo "CI shape: github — keeping .github/workflows/"
            drop "${GITLAB_CI_PATHS[@]}"
            ;;
        none)
            echo "CI shape: none — Flux-only, no pipeline"
            drop "${GITLAB_CI_PATHS[@]}" "${GITHUB_CI_PATHS[@]}"
            ;;
        -h | --help | help)
            usage
            exit 0
            ;;
        *)
            echo "error: unknown CI shape '$1'" >&2
            usage
            exit 2
            ;;
    esac

    echo
    echo "Next: review 'git status', then 'git add -A' and commit."
    echo "Shape-specific follow-ups are in docs/CI-SHAPES.md."
}

main "$@"
