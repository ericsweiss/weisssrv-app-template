"""The GitHub shape hand-vendors the library's tool pins — this gates them.

`.github/workflows/ci.yml` cannot `include:` a GitLab CI template, so it repeats
the tool versions and sha256s the library's templates declare as input defaults.
The workflow claims both shapes gate on byte-identical tools; without this test
that claim is prose. Reading the defaults out of the library checkout at
`variables.WEISSSRV_LIB_REF` makes a library bump that moves a pin fail here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import template_repo as tr
from test_vendored_scripts import lib_checkout  # noqa: F401  (fixture import)

# env key in .github/workflows/ci.yml -> (library template, input name)
ENV_FROM_LIBRARY = {
    "YAMLLINT_VERSION": ("ci/lint/yaml-lint.yml", "yamllint_version"),
    "KUSTOMIZE_VERSION": ("ci/validate/flux-lint.yml", "kustomize_version"),
    "KUSTOMIZE_SHA256": ("ci/validate/flux-lint.yml", "kustomize_sha256"),
    "KUBECONFORM_VERSION": ("ci/validate/flux-lint.yml", "kubeconform_version"),
    "KUBECONFORM_SHA256": ("ci/validate/flux-lint.yml", "kubeconform_sha256"),
    "RUFF_VERSION": ("ci/lint/python-lint.yml", "ruff_version"),
}

# shellcheck has no version input — the library pins the tool by image tag.
SHELLCHECK_TEMPLATE = "ci/lint/shellcheck.yml"


def _spec_inputs(lib: Path, rel: str) -> dict:
    """The `spec: inputs:` header of a library CI template, with defaults."""
    header = (lib / rel).read_text(encoding="utf-8").split("\n---\n", 1)[0]
    inputs = yaml.safe_load(header)["spec"]["inputs"] or {}
    return {
        name: (decl or {}).get("default") if isinstance(decl, dict) else None
        for name, decl in inputs.items()
    }


def _github_env() -> dict:
    wf = yaml.safe_load(
        (tr.REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    )
    return wf["env"]


@pytest.mark.parametrize("env_key", sorted(ENV_FROM_LIBRARY))
def test_github_env_matches_the_library_default(lib_checkout, env_key):  # noqa: F811
    rel, input_name = ENV_FROM_LIBRARY[env_key]
    want = _spec_inputs(lib_checkout, rel)[input_name]
    assert _github_env()[env_key] == want, (
        f"{env_key} in .github/workflows/ci.yml is stale against "
        f"{rel}'s `{input_name}` default at the pinned library ref"
    )


def test_github_shellcheck_matches_the_library_image_tag(lib_checkout):  # noqa: F811
    image = _spec_inputs(lib_checkout, SHELLCHECK_TEMPLATE)["image"]
    tag = image.rsplit(":", 1)[1].lstrip("v")
    assert _github_env()["SHELLCHECK_VERSION"] == tag
