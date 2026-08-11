"""`scripts/apply-cluster-identity.sh` — retargeting the scaffold at a cluster.

The script's header promises the run is idempotent. Sentinels buy that for ONE
pass (no rule can see another's output); they do not buy it for the second pass,
which sees the values the first pass wrote. An identity whose own values contain
a literal the script matches is therefore rewritten AGAIN on a re-run — and for
the TLS-secret rules that collapses the external and internal Secrets onto one
name, which is the collision the first-label guard exists to prevent.

So: the shipped identity stays a no-op, a permitted identity is stable under
re-runs, and an identity that would move on the second run is refused before
anything is written.
"""

from __future__ import annotations

import os

import template_repo as tr

SECRET_NAME_FILES = (
    "kubernetes/flux/certificate.yaml",
    "kubernetes/flux/optional/certificate-internal.yaml",
)

# First labels differ, and no value contains a literal the script matches.
PERMITTED = """\
CLUSTER_EXTERNAL_DOMAIN="example.com"
CLUSTER_INTERNAL_DOMAIN="lan.example.org"
CLUSTER_NODE_LABEL_DOMAIN="example.com"
CLUSTER_INTERNAL_VIP="10.10.0.101"
CLUSTER_REGISTRY_HOST="registry.example.com"
CLUSTER_REGISTRY_PULL_HOST="registry.lan.example.org"
CLUSTER_PRIVILEGED_RUNNER_TAG="infra"
"""

# `esweiss.io`'s first label is the scaffold's INTERNAL one, so the external
# Secret this run writes (`-esweiss-tls`) is the literal the internal rule
# matches: a second run renames it to `-example-tls` and both Certificates end
# up naming one Secret.
COLLAPSING = """\
CLUSTER_EXTERNAL_DOMAIN="esweiss.io"
CLUSTER_INTERNAL_DOMAIN="example.lan"
CLUSTER_NODE_LABEL_DOMAIN="example.lan"
CLUSTER_INTERNAL_VIP="10.10.0.101"
CLUSTER_REGISTRY_HOST="registry.git.esweiss.io"
CLUSTER_REGISTRY_PULL_HOST="registry.git.example.lan"
CLUSTER_PRIVILEGED_RUNNER_TAG="infra"
"""


def _project(tmp_path, identity: str | None = None):
    root = tr.copy_template(tmp_path / "project")
    if identity is not None:
        (root / "identity.env").write_text(identity, encoding="utf-8")
    return root


def _apply(root, *args):
    return tr.run_script(
        root, "apply-cluster-identity.sh", *args, env=dict(os.environ)
    )


def _secret_names(root):
    return [
        line.split("secretName:")[1].strip()
        for rel in SECRET_NAME_FILES
        for line in (root / rel).read_text(encoding="utf-8").splitlines()
        if "secretName:" in line
    ]


def test_the_shipped_identity_is_a_no_op(tmp_path):
    """The defaults ARE weisssrv's values, so an existing tenant is unchanged —
    and the new guard must not refuse them for containing their own literals."""
    root = _project(tmp_path)
    before = tr.digest(root)
    proc = _apply(root)
    assert proc.returncode == 0, proc.stderr
    assert "nothing to change" in proc.stdout
    assert tr.digest(root) == before


def test_a_permitted_identity_is_stable_across_reruns(tmp_path):
    """Run once, run again: the second run has nothing left to do, and the two
    TLS Secrets still have distinct names."""
    root = _project(tmp_path, PERMITTED)
    first = _apply(root, "identity.env")
    assert first.returncode == 0, first.stderr
    assert "retargeted" in first.stdout
    names = _secret_names(root)
    assert len(set(names)) == 2, names

    after_first = tr.digest(root)
    second = _apply(root, "identity.env")
    assert second.returncode == 0, second.stderr
    assert "nothing to change" in second.stdout
    assert tr.digest(root) == after_first


def test_an_identity_that_would_move_on_a_rerun_is_refused(tmp_path):
    """Refused BEFORE anything is written — a half-retargeted tree whose next
    run collapses the Secrets is worse than no run at all."""
    root = _project(tmp_path, COLLAPSING)
    before = tr.digest(root)
    proc = _apply(root, "identity.env")
    assert proc.returncode == 1
    assert "not safe to re-apply" in proc.stderr
    # The message names the collision, not just the fact of one.
    assert "-ericsweiss-tls" in proc.stderr
    assert "-esweiss-tls" in proc.stderr
    assert tr.digest(root) == before
