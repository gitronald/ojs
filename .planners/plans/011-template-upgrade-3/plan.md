---
id: 11
slug: template-upgrade-3
status: active
branch: feature/template-upgrade-3
created: 2026-10-12T20:37:10-07:00
concluded:
pr:
---

# Sync tooling with proj-template 0.11.2

## Plan

Third pass of the template's `install-template` upgrade flow. The repo is
stamped `[tool.proj-template] version = "0.10.0"`; this walks the sync matrix
against the 0.11.2 `template/` directory and carries the delta forward.

**Classification:** package (hatchling build, an `ojs` console script,
published to PyPI) — unchanged from plans 004 and 007.

**Sync decisions** per template file:

| Template path | Action |
|---|---|
| `pyproject.toml` dev group | merge: `ruff>=0.15` -> `ruff>=0.16,<0.17` with the template's comment tying it to the hook rev; refresh the lock so the locked ruff matches the `ruff-pre-commit` rev |
| `pyproject.toml` `[tool.ruff]` `extend-exclude = ["*.md"]` | skip: the repo keeps ruff 0.16's Markdown fence formatting on (owner decision of 2026-09-06, after the 0.16 bump flagged a plan's code block: fix the file, never exclude it) |
| other `pyproject.toml` tooling sections | identical apart from repo-specific values (`source = ["ojs"]`, `fail_under = 86`); no change |
| `pyproject.toml` `[tool.proj-template]` | stamp `0.11.2` last |
| `.pre-commit-config.yaml` | sync: add `default_stages: [pre-commit]`, run `planners-validate` through `uv run`, add the `planners-index` post-merge hook. The repo already had the `ruff-check` id and the v0.16.6 rev |
| `.gitattributes` | identical (`.planners/README.md merge=union`, added in plan 007) |
| `.python-version` | identical (3.14) |
| `.gitignore` | template entries all present, plus the repo-specific `/data/`; no change |
| `.github/workflows/test.yml`, `publish.yml` | sync: `astral-sh/setup-uv` v10.0.1 -> v10.2.0 (SHA pin + comment) |
| `.github/dependabot.yml` | identical |
| Dependabot repo toggles | already correct: alerts on (204), security updates off (`enabled:false`) |
| `.claude/hooks/lint-typecheck.sh` | replace (template-owned plumbing): adds the uv-workspace root check. `.claude/` is gitignored, so applied on disk in the main checkout |
| `.claude/settings.json` | identical |
| `.claude/settings.local.json` | skip: absent here, and the shared permission baseline lives in the user-level settings rather than per-repo copies |
| `.claude/CLAUDE.md` `## Development` | no change: Install/Tests/Linting/Type checking match; the CI bullet is a repo-specific expansion of the template's wording |
| `ojs/`, `tests/`, `README.md`, `CHANGELOG.md` | never |

**Hooks:** install both hook types from the main checkout
(`pre-commit install --hook-type pre-commit --hook-type post-merge`) so the new
`planners-index` hook actually fires; only `pre-commit` is installed today.

**Verification:** `uv sync --all-groups`, `uv run ruff check .`,
`uv run ruff format --check .`, `uv run pyrefly check`,
`uv run pre-commit run --all-files`, `uv run pytest`.

## Log

- **2026-10-12T20:37:28-07:00** — Activated.
  - Branch: `feature/template-upgrade-3`
  - Base: `dev` at `bc160fa`
  - Worktree: `.worktrees/template-upgrade-3`
  - PR: pending
- **2026-10-12T20:38:30-07:00** — Applied the 0.11.2 sync and verified it green.
  - Pre-commit config synced to the template (`default_stages`, `uv run planners validate`, `planners-index` post-merge hook); the repo already had the `ruff-check` id.
  - `ruff` dev pin moved to `>=0.16,<0.17`; refreshing the lock resolved ruff 0.16.10, so the `ruff-pre-commit` rev moved v0.16.6 -> v0.16.10 to keep the commit hook and CI on one version (the template still ships v0.16.6).
  - `setup-uv` v10.0.1 -> v10.2.0 in `test.yml` and `publish.yml`; both now match the template byte for byte.
  - Skipped `extend-exclude = ["*.md"]`: the repo keeps Markdown fence formatting on by owner decision. Skipped `.claude/settings.local.json`: the permission baseline lives in user-level settings.
  - On disk only (gitignored `.claude/`): replaced `.claude/hooks/lint-typecheck.sh` with the template's workspace-aware copy. Installed both hook types from the main checkout; `INSTALL_PYTHON` in `pre-commit` and `post-merge` points at the main checkout's venv.
  - Stamped `[tool.proj-template] version = "0.11.2"`.
  - Verification: `ruff check`, `ruff format --check`, `pyrefly check` (0 errors), `pre-commit run --all-files`, and `pytest` (246 passed, 95.02% coverage against the 86 floor) all green.
