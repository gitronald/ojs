---
id: 7
slug: template-upgrade-2
status: done
branch: feature/template-upgrade-2
created: 2026-09-09T11:24:14-07:00
concluded: 2026-09-09T13:04:38-07:00
pr: https://github.com/gitronald/ojs/pull/35
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
| `.github/workflows/test.yml` | sync: `astral-sh/setup-uv` v9.0.0 -> v10.0.1, and restate the matrix interpreter as `--python` on `uv sync` alongside the job-level `UV_PYTHON`, with the template's comment recording that the redundancy is deliberate |
| `.github/workflows/publish.yml` | sync: `astral-sh/setup-uv` v9.0.0 -> v10.0.1 |
| `.github/dependabot.yml` | merge: adopt the template's trimmed header (guide link plus the default-branch caveat) and `target-branch: dev` on both ecosystems (`origin/dev` exists). Inert until it reaches `main` — Dependabot reads config from the default branch |
| Dependabot repo toggles | already correct: alerts on (204), security updates off (`enabled:false`); no change |
| `.planners/` | present; reconcile the repo against planners 0.6.1 via `planners install --check` (see below) |
| `ojs/`, `tests/`, `README.md`, `CHANGELOG.md` | never |

**Deliberate deviations carried forward:** SHA-pinned workflow actions (now the
template default too), the repo's `/data/` gitignore entry, and the `ruff-check`
hook id.

**Verification:** `uv sync --all-groups`, `uv run ruff check .`,
`uv run ruff format --check .`, `uv run pyrefly check`,
`uv run pre-commit run --all-files`, `uv run pytest`.

## Log

- 2026-09-09: Walked the sync matrix against the current `template/`. Several
  rows were already identical — `.python-version`, `.gitignore`, and every
  `pyproject.toml` tooling section matched, `.planners/` is present, and the
  Dependabot repo toggles were already alerts-on / security-updates-off.
  Applied: ruff-pre-commit v0.16.5 -> v0.16.6, `pytest>=9.0.2` -> `>=9.0.3`
  (with the lock refreshed), and the `dependabot.yml` header plus
  `target-branch: dev` on both ecosystems.
- 2026-09-09: Divergence decisions. Kept the repo's `ruff-check` hook id over
  the template's deprecated `ruff` alias — the repo is ahead there, so only the
  `rev` was carried forward.
- 2026-09-09: Re-read `template/` after it moved on mid-upgrade and picked up
  the newer rows: `astral-sh/setup-uv` v9.0.0 -> v10.0.1 in both workflows
  (v10 only disables `enable-cache: auto` for `pull_request_target`,
  `workflow_run`, and `release`, none of which either workflow triggers on), the
  deliberate `--python ${{ matrix.python-version }}` restatement on `uv sync` in
  `test.yml`, and the trimmed `dependabot.yml` header that leaves the rationale
  in the template's automation guide.
- 2026-09-09: Brought the repo's planners wiring up to 0.6.1.
  `planners install --check` reported `gitattr: missing` with the holder, rule,
  and pre-commit hook already `ok`, so the only gap was the index merge
  attribute: `.gitattributes` now carries `.planners/README.md merge=union`, so
  a local merge resolves the generated index instead of conflicting on it
  (GitHub does not apply the attribute, so a PR can still report a conflict
  there — merge the base in locally and regenerate rather than hand-editing).
  Nothing to clean up: this repo has no legacy planners artifacts — it was
  created on the `.planners/` layout, so there is no `docs/plans/`, `TODO.md`,
  `.claude/rules/plan-files.md`, or per-repo holder, and the
  `planners-validate` hook block already matches the generated form byte for
  byte. `planners index .` produces no diff and `planners validate` passes on
  all 8 plans.
- 2026-09-09: Verification green in the worktree — `ruff check`,
  `ruff format --check`, `pyrefly check` (0 errors),
  `pre-commit run --all-files`, and `pytest` (182 passed, 91.98% coverage
  against the pinned 86 floor).
- 2026-09-09: PR #35 merged into `dev` as `cdaba8d` with all four CI cells
  (3.11-3.14) green; worktree removed.

## Retrospective

Re-reading `template/` before each row, rather than trusting the read taken at
the start, was what made this pass correct: the template shipped 0.8.2 -> 0.8.5
while the branch was open, so the first sync of `dependabot.yml` landed comment
text the template had already replaced, and the `setup-uv` v10 bump and the
`--python` restatement on `uv sync` would have been missed entirely. Cheap to
re-check, expensive to discover after a merge — worth doing on any upgrade that
spans more than one sitting.

The sync matrix also found less than it looked like it would. Most rows were
already identical, and the real value was in the two places the repo was
*ahead* of the template (the `ruff-check` hook id) or deliberately different —
which is exactly the class the skill says to ask about rather than overwrite.
Diffing every managed file, including the ones expected to match, is what
surfaced that; a presence check would have called the whole matrix done.
