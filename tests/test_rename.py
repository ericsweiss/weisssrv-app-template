"""`scripts/rename.sh` — the first command a new project runs.

Three families:

* the placeholder tokens are all gone afterwards — in file CONTENTS and in the
  paths, since the wrapped CLI substitutes text only;
* the values land where the manifests actually consume them;
* a bad slug or group is refused BEFORE anything is written, so a typo cannot
  leave a half-renamed tree.
"""

from __future__ import annotations

import pytest
import yaml

import template_repo as tr
from template_repo import APP, APP_TOKEN, GROUP, GROUP_TOKEN

TOKENS = (APP_TOKEN, GROUP_TOKEN)


def _docs(root, rel):
    return [
        doc
        for doc in yaml.safe_load_all((root / rel).read_text(encoding="utf-8"))
        if isinstance(doc, dict)
    ]


# --------------------------------------------------------------------------
# Substitution
# --------------------------------------------------------------------------


def test_the_template_still_ships_both_tokens(pristine):
    """Without this, every 'no token survives' assertion below passes on a tree
    that simply has no placeholders left to find."""
    carriers = {
        token: sorted(rel for rel, text in tr.text_files(pristine) if token in text)
        for token in TOKENS
    }
    for token, rels in carriers.items():
        assert rels, f"no file in the template carries {token}"
    assert "kubernetes/flux/deployment.yaml" in carriers[APP_TOKEN]
    assert "kubernetes/flux/deployment.yaml" in carriers[GROUP_TOKEN]


def test_rename_leaves_no_token_in_any_file(renamed):
    leftovers = [
        f"{rel}:{n}"
        for rel, text in tr.text_files(renamed)
        for n, line in enumerate(text.splitlines(), 1)
        if any(token in line for token in TOKENS)
    ]
    assert leftovers == []


def test_rename_leaves_no_token_in_any_path(renamed):
    """The CLI rewrites file CONTENTS only. A placeholder in a file or directory
    name would survive silently, so the tree names are checked separately."""
    assert [
        rel
        for rel in tr.files(renamed)
        if any(token in rel for token in TOKENS)
    ] == []


def test_no_other_changeme_placeholder_survives(renamed):
    """Any NEW placeholder token added to the template must be one rename
    substitutes. The only `changeme` left afterwards is the documented
    `grep -rn changeme- .` check telling you to look for leftovers."""
    stragglers = [
        f"{rel}:{n}: {line.strip()}"
        for rel, text in tr.text_files(renamed)
        for n, line in enumerate(text.splitlines(), 1)
        if "changeme" in line and "grep" not in line
    ]
    assert stragglers == []


def test_rename_writes_the_values_the_manifests_consume(renamed):
    deployment = _docs(renamed, "kubernetes/flux/deployment.yaml")[0]
    assert deployment["metadata"]["name"] == APP
    assert deployment["metadata"]["labels"]["app.kubernetes.io/name"] == APP
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    # One line carrying BOTH tokens, and the group is the nested form.
    assert container["image"] == f"registry.git.ericsweiss.com/{GROUP}/{APP}:0.1.0"
    assert container["env"][0]["valueFrom"]["secretKeyRef"]["name"] == f"{APP}-secrets"

    route = _docs(renamed, "kubernetes/flux/ingressroute.yaml")[0]
    assert route["metadata"]["name"] == APP
    assert route["spec"]["routes"][0]["match"] == f"Host(`{APP}.ericsweiss.com`)"
    assert route["spec"]["tls"]["secretName"] == f"{APP}-ericsweiss-tls"

    assert f"* @{GROUP}" in (renamed / "CODEOWNERS").read_text(encoding="utf-8")
    assert f"APP: {APP}" in (renamed / "Taskfile.yml").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "app,group",
    [
        ("recipe-box", "eric"),  # top-level group
        ("recipe-box", "eric/apps"),  # nested: a slash in the replacement
        ("app2", "eric.dev/sub_group-1"),  # dots, underscores, hyphens
    ],
)
def test_rename_accepts_substitution_hostile_values(project, script_env, app, group):
    tr.rename(project, app, group, env=script_env)
    assert [rel for rel, text in tr.text_files(project) if any(t in text for t in TOKENS)] == []
    image = _docs(project, "kubernetes/flux/deployment.yaml")[0]["spec"]["template"][
        "spec"
    ]["containers"][0]["image"]
    assert image == f"registry.git.ericsweiss.com/{group}/{app}:0.1.0"


def test_rename_is_idempotent(project, script_env):
    tr.rename(project, env=script_env)
    after_first = tr.digest(project)
    second = tr.rename(project, env=script_env)
    assert tr.digest(project) == after_first
    assert "no changes" in second.stdout


# --------------------------------------------------------------------------
# Refusal
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param([], id="no-args"),
        pytest.param([APP], id="slug-only"),
        pytest.param([APP, GROUP, "extra"], id="too-many-args"),
        pytest.param(["", "eric"], id="empty-slug"),
        pytest.param(["recipe/box", "eric"], id="slug-with-slash"),
        pytest.param(["recipe box", "eric"], id="slug-with-space"),
        pytest.param(["Recipe_Box", "eric"], id="slug-not-a-dns-label"),
        pytest.param(["-recipe", "eric"], id="slug-leading-hyphen"),
        pytest.param(["a" * 64, "eric"], id="slug-over-63-chars"),
        pytest.param([APP, ""], id="empty-group"),
        pytest.param([APP, "/eric"], id="group-leading-slash"),
        pytest.param([APP, "eric/"], id="group-trailing-slash"),
        pytest.param([APP, "eric apps"], id="group-with-space"),
        pytest.param([APP, "../eric"], id="group-traversal"),
    ],
)
def test_rename_refuses_invalid_input_without_touching_the_tree(
    project, script_env, argv
):
    before = tr.digest(project)
    proc = tr.run_script(project, "rename.sh", *argv, env=script_env)
    assert proc.returncode != 0, proc.stdout
    assert tr.digest(project) == before
