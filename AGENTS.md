# AGENTS.md

Agent instructions for this repository live in [CLAUDE.md](CLAUDE.md) — read it
first. A task-oriented guide is in the `project-development` skill
(`.claude/skills/project-development/SKILL.md`). Do not duplicate their content
here.

CI comes in three shapes and this repo keeps exactly one — self-hosted GitLab
(`.gitlab-ci.yml`, including the shared `eric/weisssrv-lib` library at a pinned
tag), GitHub Actions (`.github/workflows/`), or none at all (Flux-only). Check
which files are present before touching CI: [docs/CI-SHAPES.md](docs/CI-SHAPES.md).
The `weisssrv-new-project` CLI scaffolds the rest — see
[docs/CONSUMING.md](docs/CONSUMING.md).
