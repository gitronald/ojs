---
id: 7
slug: template-upgrade-2
status: active
branch: feature/template-upgrade-2
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

## Log

- 2026-09-09: Walked the sync matrix against the current `template/`. Most rows
  were already identical — `test.yml` and `publish.yml` are byte-for-byte the
  template's, `.python-version`, `.gitignore`, and every `pyproject.toml`
  tooling section matched, `.planners/` is present, and the Dependabot repo
  toggles were already alerts-on / security-updates-off. Applied: ruff-pre-commit
  v0.16.5 -> v0.16.6, `pytest>=9.0.2` -> `>=9.0.3` (with the lock refreshed),
  and the `dependabot.yml` header comment plus `target-branch: dev` on both
  ecosystems.
- 2026-09-09: Divergence decisions. Kept the repo's `ruff-check` hook id over
  the template's deprecated `ruff` alias — the repo is ahead there, so only the
  `rev` was carried forward. Kept `.claude/settings.json` as-is: its sole
  difference from the template is dropping `Bash(git push:*)` from `ask`, a
  deliberate loosening, and nothing else in the file diverged.
- 2026-09-09: Gitignored `.claude/` payload applied on disk in the main
  checkout, not on this branch: `hooks/lint-typecheck.sh` regained the
  template's comment explaining why the non-mutating `ruff format --check`
  mirrors CI (script body was already current), and `CLAUDE.md`'s
  `## Before finishing a task` intro was corrected from "Run both checks" to
  "Run all three checks" (it lists three commands). The `## Development`
  bullets were already current.
- 2026-09-09: Verification green in the worktree — `ruff check`,
  `ruff format --check`, `pyrefly check` (0 errors),
  `pre-commit run --all-files`, and `pytest` (182 passed, 91.98% coverage
  against the pinned 86 floor).
