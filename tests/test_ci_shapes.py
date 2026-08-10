"""`scripts/select-ci.sh` — the second command a new project runs.

The template ships all three CI shapes and a project keeps exactly one
(docs/CI-SHAPES.md). What must hold: each shape keeps exactly the documented
files; the GitLab issue/MR templates are host metadata and survive every shape;
and `kubernetes/` — which Flux reconciles in all three shapes — is byte-identical
whichever is chosen.

The keep/drop expectations are read back out of docs/CI-SHAPES.md AND out of the
script's own usage text, so prose that stops matching behaviour fails here
instead of misleading someone at setup time.
"""

from __future__ import annotations

import pytest
import yaml

import template_repo as tr

CLAIM_SOURCES = ("docs/CI-SHAPES.md", "scripts/select-ci.sh")

# The paths a shape selection is allowed to remove, as the prose names them
# (the ruleset file lives under .gitlab/ and is covered by the exhaustive
# inventory below, not by the summary claims).
DOCUMENTED_DROPPABLE = frozenset({".gitlab-ci.yml", ".github/workflows"})


def _claims(source: str) -> dict:
    return tr.shape_claims((tr.REPO_ROOT / source).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# What the docs and the script's usage claim
# --------------------------------------------------------------------------


@pytest.mark.parametrize("source", CLAIM_SOURCES)
def test_every_shape_is_documented(source):
    """Guards the parser as much as the prose: a reformatted table that no
    longer yields claims would make the next test vacuous."""
    claims = _claims(source)
    assert set(claims) == set(tr.SHAPES)
    droppable = frozenset().union(*(c.drop for c in claims.values()))
    assert droppable == DOCUMENTED_DROPPABLE
    for shape, claim in claims.items():
        assert claim.keep or claim.drop, f"{source} claims nothing for {shape}"


@pytest.mark.parametrize("source", CLAIM_SOURCES)
def test_shape_matches_what_is_documented(source, shaped):
    shape, root = shaped
    claim = _claims(source)[shape]
    for rel in sorted(claim.keep):
        assert (root / rel).exists(), f"{source} says {shape} keeps {rel}"
    for rel in sorted(claim.drop):
        assert not (root / rel).exists(), f"{source} says {shape} drops {rel}"


# --------------------------------------------------------------------------
# The full inventory
# --------------------------------------------------------------------------


def test_shape_removes_exactly_the_expected_files(shaped, renamed):
    shape, root = shaped
    assert set(tr.files(renamed)) - set(tr.files(root)) == tr.SHAPE_DROPS[shape]


def test_gitlab_host_metadata_survives_every_shape(shaped):
    """Issue and MR templates are GitLab HOST metadata, not CI: they still work
    on a repo that runs no pipeline, so no shape may take them."""
    _, root = shaped
    for rel in tr.HOST_METADATA:
        assert (root / rel).is_file()


def test_emptied_ci_parent_dirs_are_removed(shapes):
    # .github holds only workflows/, so dropping it empties the parent; .gitlab
    # still holds the issue/MR templates and must stay.
    for shape in ("gitlab", "none"):
        assert not (shapes[shape] / ".github").exists()
    assert (shapes["github"] / ".github" / "workflows").is_dir()
    for shape in tr.SHAPES:
        assert (shapes[shape] / ".gitlab").is_dir()


# --------------------------------------------------------------------------
# What must NOT differ between shapes
# --------------------------------------------------------------------------


def test_kubernetes_is_byte_identical_across_shapes(shapes):
    """Flux is what deploys in all three shapes, so the manifests are
    CI-agnostic (docs/CI-SHAPES.md)."""
    manifests = {
        shape: {
            rel: sha
            for rel, sha in tr.digest(root).items()
            if rel.startswith("kubernetes/")
        }
        for shape, root in shapes.items()
    }
    reference = manifests["gitlab"]
    assert reference, "no kubernetes/ manifests in the rendered project"
    for shape, tree in manifests.items():
        assert tree == reference, f"kubernetes/ differs in shape {shape}"


def test_shapes_differ_only_in_ci_paths(shapes):
    digests = {shape: tr.digest(root) for shape, root in shapes.items()}
    reference = digests["gitlab"]
    for shape, tree in digests.items():
        differing = {
            rel
            for rel in set(tree) | set(reference)
            if tree.get(rel) != reference.get(rel)
        }
        assert differing <= tr.CI_PATHS, f"shape {shape} also changed {differing - tr.CI_PATHS}"


# --------------------------------------------------------------------------
# The wrapper's own argument handling
# --------------------------------------------------------------------------


def test_select_ci_is_idempotent(project, script_env):
    tr.select_ci(project, "gitlab", env=script_env)
    after_first = tr.digest(project)
    tr.select_ci(project, "gitlab", env=script_env)
    assert tr.digest(project) == after_first


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param([], id="no-args"),
        pytest.param(["gitlab", "github"], id="two-shapes"),
        pytest.param([""], id="empty-shape"),
        pytest.param(["GitLab"], id="wrong-case"),
        pytest.param(["gitlab-ci"], id="unknown-shape"),
        pytest.param(["../.."], id="traversal"),
        pytest.param(["ci:gitlab"], id="raw-cli-selector"),
    ],
)
def test_select_ci_refuses_bad_input_without_touching_the_tree(
    project, script_env, argv
):
    before = tr.digest(project)
    proc = tr.run_script(project, "select-ci.sh", *argv, env=script_env)
    assert proc.returncode != 0, proc.stdout
    assert tr.digest(project) == before


def test_the_gate_runs_the_cli_version_select_ci_installs():
    """The suite is only evidence about the CLI a project actually gets, so the
    job's library ref and the wrapper's pin move together."""
    ci = yaml.safe_load((tr.REPO_ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8"))
    assert ci["python-tests"]["variables"]["LIB_REF"] == tr.lib_ref("select-ci.sh")


def test_every_library_pin_agrees_with_the_single_source():
    """One reference per repo, across ALL the places this repo pins the library.

    scripts/check-lib-pins.py covers the `include:` entries. It cannot see the
    other two kinds of pin — the python-tests job's LIB_REF and the two wrapper
    defaults — and those are exactly what drifted before: the wrappers once
    fetched a different build of the same CLI than the gate that tested it.
    """
    ci = yaml.safe_load((tr.REPO_ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8"))
    want = ci["variables"]["WEISSSRV_LIB_REF"]

    include_refs = {
        entry["ref"]
        for entry in ci["include"]
        if isinstance(entry, dict) and entry.get("project") == "eric/weisssrv-lib"
    }
    assert include_refs, "no weisssrv-lib includes found — did the block move?"
    assert include_refs == {want}, f"include refs {include_refs} != {want}"

    assert ci["python-tests"]["variables"]["LIB_REF"] == want
    assert tr.lib_ref("select-ci.sh") == want
    assert tr.lib_ref("rename.sh") == want
