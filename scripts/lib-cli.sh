#!/usr/bin/env bash
# Shared resolver for the weisssrv-new-project CLI. Sourced by rename.sh and
# select-ci.sh so both fetch the SAME build: the wrappers pin the library tag,
# and a PATH-installed CLI from a different tag would silently do different work.
#
#   LIB_REF   the pinned tag (both wrappers default it from $WEISSSRV_LIB_REF)
#   run_cli   exec the CLI with the given arguments
#
# A PATH-installed CLI is used as-is, but its `--version` (which mirrors the
# library tag) is compared against the pin first and a mismatch is reported —
# otherwise a stale CLI does different work with nothing to show for it. A
# checkout-run CLI reports "0+source" and is accepted silently; that is the
# development path the template's own test suite uses.

LIB_SPEC="git+https://git.ericsweiss.com/eric/weisssrv-lib.git@${LIB_REF}#subdirectory=cli"

run_cli() {
    if command -v weisssrv-new-project >/dev/null 2>&1; then
        local installed
        installed="$(weisssrv-new-project --version 2>/dev/null | awk '{print $NF}')"
        if [ -n "$installed" ] && [ "$installed" != "0+source" ] \
            && [ "v${installed}" != "$LIB_REF" ]; then
            echo "warning: weisssrv-new-project on PATH is ${installed}, but this" >&2
            echo "         template pins ${LIB_REF}. Reinstall from the pin to be sure:" >&2
            echo "           pipx install --force '${LIB_SPEC}'" >&2
        fi
        exec weisssrv-new-project "$@"
    fi

    if command -v pipx >/dev/null 2>&1; then
        exec pipx run --spec "$LIB_SPEC" weisssrv-new-project "$@"
    fi

    echo "error: the weisssrv-new-project CLI is required but was not found." >&2
    echo "Install it (from a weisssrv-lib checkout, or straight from git):" >&2
    echo "  pipx install '${LIB_SPEC}'" >&2
    echo "  # or, from a local library checkout: pip install ./cli" >&2
    echo "then re-run:  weisssrv-new-project $*" >&2
    exit 1
}
