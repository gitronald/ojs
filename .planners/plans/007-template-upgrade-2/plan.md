---
id: 7
slug: template-upgrade-2
status: draft
branch:
created: 2026-09-09T11:24:14-07:00
concluded:
pr:
---

# Sync tooling with the current proj-template standard

## Plan

Second pass of the template's `install-template` upgrade flow. Plan 004 brought
the repo to the 0.8.1a0 template; this walks the sync matrix again against the
current `template/` directory and carries the (small) remaining delta forward.

**Classification:** package (hatchling build, an `ojs` console script,
published to PyPI) — unchanged from plan 004.

**Sync decisions** per template file:

| Template path | Action |
|---|---|
| `pyproject.toml` ruff/pyrefly/pytest/coverage sections, sdist `only-include`, urls, scripts | identical apart from repo-specific values; no change |
| `pyproject.toml` dev group | merge: bump the `pytest` floor to `>=9.0.3` |
| `.pre-commit-config.yaml` | merge: ruff-pre-commit rev v0.16.5 -> v0.16.6, but keep the repo's `ruff-check` hook id (the modern name; the template still uses the deprecated `ruff` alias) |
| `.python-version` | identical (3.14) |
| `.gitignore` | template entries all present, plus the repo-specific `/data/`; no change |
| `.github/workflows/test.yml`, `publish.yml` | byte-identical to the template; no change |
| `.github/dependabot.yml` | merge: add the config-location header comment and `target-branch: dev` on both ecosystems (`origin/dev` exists). Inert until it reaches `main` — Dependabot reads config from the default branch |
| Dependabot repo toggles | already correct: alerts on (204), security updates off (`enabled:false`); no change |
| `.claude/hooks/lint-typecheck.sh` | sync: restore the template's comment explaining why the non-mutating `ruff format --check` mirrors CI. Script body already matches. Gitignored: applied on disk in the main checkout |
| `.claude/settings.json` | keep the repo's copy: it is the template's file minus `Bash(git push:*)` in `ask`, a deliberate loosening. Nothing else diverges |
| `.claude/CLAUDE.md` | keep content; fix the stale "Run both checks" wording under `## Before finishing a task` (three commands are listed). `## Development` bullets are current. On disk only (gitignored) |
| `.planners/` | present |
| `ojs/`, `tests/`, `README.md`, `CHANGELOG.md` | never |

**Deliberate deviations carried forward:** SHA-pinned workflow actions (now the
template default too), the repo's `/data/` gitignore entry, the `ruff-check`
hook id, and the `git push` permission loosening.

**Verification:** `uv sync --all-groups`, `uv run ruff check .`,
`uv run ruff format --check .`, `uv run pyrefly check`,
`uv run pre-commit run --all-files`, `uv run pytest`.
