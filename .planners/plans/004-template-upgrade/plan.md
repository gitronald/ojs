---
id: 4
slug: template-upgrade
status: active
branch: feature/template-upgrade
created: 2026-09-06T21:56:35-07:00
concluded:
pr:
---

# Upgrade tooling to the proj-template 0.8 standard

## Plan

Bring this repo's tooling in line with proj-template 0.8.1a0 (its `template/`
directory at that version) using the template's `install-template` upgrade
flow: classify the repo, walk the sync matrix file by file, verify, then PR
into `dev`.

**Classification:** package (hatchling build, an `ojs` console script,
published to PyPI).

**Sync decisions** per template file:

| Template path | Action |
|---|---|
| `pyproject.toml` ruff/pyrefly sections, dev group | already identical; no change |
| `pyproject.toml` sdist `only-include` | merge: add `[tool.hatch.build.targets.sdist]` limiting the sdist to `/ojs`, `/README.md`, `/CHANGELOG.md`, `/LICENSE` |
| `pyproject.toml` pytest `addopts`, `[tool.coverage.*]` | merge: bare `--cov` via `addopts`, `run.source = ["ojs"]`, branch coverage, `fail_under` pinned to the current measured total (86) |
| `.pre-commit-config.yaml` | sync: ruff-pre-commit rev v0.15.15 -> v0.16.1; hooks otherwise identical |
| `.python-version` | identical (3.14) |
| `.gitignore` | identical plus the repo-specific `/data/`; no change |
| `.claude/settings.json` | merge in the template's permission allow/deny/ask lists (the hooks block already matched). Gitignored: applied on disk in the main checkout, not on this branch |
| `.claude/hooks/lint-typecheck.sh` | keep the repo's copy: it is the template's script plus a non-mutating `ruff format --check`, matching the repo's `## Before finishing a task` guidance |
| `.claude/CLAUDE.md` | already at the canonical path; refresh the `Tests` bullet under `## Development` to mention the coverage gate. On disk only (gitignored) |
| `.github/workflows/test.yml` | sync inner config: bump `actions/checkout` to v7.0.1 and `astral-sh/setup-uv` to v9.0.0, drop the redundant `--python` flag (`UV_PYTHON` already pins it), run bare `uv run pytest` (coverage now comes from `addopts`), move `env` after `strategy` to match the template |
| `.github/workflows/publish.yml` | sync: bump checkout, setup-uv, and `pypa/gh-action-pypi-publish` (v1.14.2); the artifact actions are already current |
| `.github/dependabot.yml` | identical; repo toggles verified (alerts on, security updates off) |
| `.planners/` | present |
| `ojs/`, `tests/`, `README.md`, `CHANGELOG.md` | never (one `[Unreleased]` changelog bullet records the tooling change) |

**Deliberate deviation:** the workflows keep commit-SHA pins with version
comments rather than the template's bare version tags. The 0.8.1 release
pinned them on purpose, and Dependabot has been bumping the pinned SHAs in
this repo (PRs #12, #18, #20), so the template's reason for tags (Dependabot
not tracking SHA pins) does not hold here.

**Verification:** `uv sync --all-groups`, `uv run ruff check .`,
`uv run ruff format --check .`, `uv run pyrefly check`,
`uv run pre-commit run --all-files`, `uv run pytest` (coverage gate), and
`uv build` to confirm the trimmed sdist contents.

## Log

- 2026-09-06: Classified the repo as a package and walked the sync matrix.
  Verification is green in the upgrade worktree: `ruff check`,
  `ruff format --check`, `pyrefly check` (0 errors), `pre-commit run
  --all-files`, and `pytest` (123 passed, 86.60% branch coverage against the
  pinned 86 floor). `uv build` confirms the sdist is now the package, README,
  changelog, license, and `pyproject.toml` only; the wheel is unchanged.
- Applied on disk only (gitignored, outside this branch): the template's
  permission lists merged into `.claude/settings.json`, and the `Tests`
  bullet in `.claude/CLAUDE.md` refreshed. The repo's Stop hook script is
  kept as is (it adds a non-mutating `ruff format --check`).
- Dependabot PR #20 proposed the same checkout and setup-uv SHAs and an older
  pypi-publish (v1.14.1); this branch lands v1.14.2, so that PR is superseded.
