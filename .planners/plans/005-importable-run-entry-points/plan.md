---
id: 5
slug: importable-run-entry-points
status: done
branch: feature/importable-run-entry-points
created: 2026-09-08T14:15:19-07:00
concluded: 2026-09-08T18:23:15-07:00
pr: https://github.com/gitronald/ojs/pull/31
---

# Extract importable run entry points from the CLI commands

## Plan

### Goal

Make the `fetch` and `norm` pipelines callable as ordinary Python functions, so
the package can be driven in-process instead of only through its console script.

Right now `ojs/cli.py` is 749 lines and holds the orchestration itself, not just
argument parsing. `api_fetch` runs to roughly 245 lines (`cli.py:299-544`),
`api_download` another 150 (`545-696`), `api_norm` 43 (`698-740`). The importable
surface stops one level below that: `normalize_api` (`api/normalize.py:413`), the
`fetch_*` client calls, and the `sync.py` watermark helpers are all reusable, but
nothing composes them. A caller embedding this package in a larger workflow has to
`subprocess` the console script, which loses exceptions, return values, and any
control over output.

The pipeline logic should live in the library and the CLI should be a thin shell
over it — the usual layering, and the same split `normalize_api` already models
one level down.

### Scope

In scope:
- `ojs/api/run.py` with `run_fetch`, `run_download`, and `run_norm`.
- `ojs/website/run.py` with `run_report_fetch` and `run_norm` for the articles and
  reviews CSV pipelines.
- A public `ojs/paths.py` holding the directory resolution now private to the CLI.
- A typed error to replace `typer.Exit` in library code.
- Rewriting the CLI commands as thin wrappers, with identical behavior and output.
- Tests calling the new functions directly.

Out of scope:
- Any change to fetch semantics, incremental sync, normalization, or the schema
  classes. This is a pure refactor — same requests, same files, same tables.
- Async or concurrent fetching.
- A programmatic config object replacing environment variables. The env vars stay
  the source of defaults; the functions just also accept explicit arguments.

### Design

**1. Entry points take explicit arguments and fall back to the environment.**

```python
def run_fetch(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    out_dir: Path | None = None,
    stats: bool = True,
    files: bool = False,
    stats_interval: str = "day",
    stats_since: str | None = None,
    stats_until: str | None = None,
    incremental: bool = False,
    since: str | None = None,
    full: bool = False,
) -> FetchResult: ...
```

`None` means "read the environment" (`OJS_BASE_URL`, `OJS_API_KEY`,
`OJS_API_DIR`), preserving today's behavior for the CLI while letting a caller
pass values directly. Keyword-only, so the long option list stays readable and can
grow without breaking positional callers.

**2. Return a result instead of printing it.** `FetchResult` carries the counts the
command currently prints — submissions, publications, users, stat records,
timeline points, whether the stats pull succeeded, whether the run was incremental
— plus the output directory. `run_norm` returns the table names and row counts.
The CLI formats these into the same lines it prints today; callers get data.

**3. Replace `print()` with `logging`.** The command bodies print progress
throughout (`Fetched 250/354 publications`, `Merged N ...`). In a library that
should be `logger.info` on a `ojs` logger with a `NullHandler`, and the CLI
attaches a plain stdout handler at import. This keeps CLI output byte-identical
while making a library caller's console quiet by default.

Alternative considered: an `on_progress` callback parameter. More flexible, but it
threads a parameter through every helper and callers mostly want either "log it"
or "silence it" — `logging` gives both with no signature churn. Confirm at
implementation time.

**4. Raise, don't exit.** Library code must not call `typer.Exit`. Add
`ojs.errors.OjsError` with `ConfigError` (missing `OJS_BASE_URL` / `OJS_API_KEY`,
conflicting `--full` with `--incremental`) and `MissingDataError` (a `norm` run
with no JSON to read, or no CSV export matching the glob) as subclasses. Each CLI
command wraps its call in `try/except OjsError`, prints the message, and raises
`typer.Exit(1)` — so exit codes and messages are unchanged.

**5. Promote the path helpers.** `_downloads_dir`, `_articles_dir`, `_reviews_dir`,
`_api_dir`, and `_files_dir` (`cli.py:73-92`) resolve output directories from env
vars and are private to the CLI module, so a library caller cannot reach them
without importing a private name. Move them to `ojs/paths.py` as public functions
with the same behavior, and have both the CLI and the run functions use them. This
also gives an embedding caller a supported way to ask where the package writes,
rather than hardcoding a matching path on its own side and hoping the defaults
never diverge.

**6. Website commands get the same treatment, smaller.** `articles_norm` and
`reviews_norm` (`cli.py:216-229`, `246-259`) each glob for the newest matching
export, error if there is none, and call their normalizer. That glob-and-pick step
is real logic living in a command body; it moves to `run_norm` in
`ojs/website/run.py`, parameterized by which report it handles. `_report_fetch`
(`cli.py:156`) is already a shared helper — it moves as-is and becomes public.

### Compatibility

This is a refactor, not a feature. The acceptance bar is that nothing observable
changes:

- Every command keeps its name, options, defaults, printed output, and exit codes.
- No change to file layout, JSON dumps, sync state, or normalized tables.
- The existing CLI tests pass untouched. If a test needs editing, the refactor
  changed behavior and the change is wrong.

`ojs/cli.py` should end up well under half its current length, with each command
reduced to option declarations plus a call and a result-formatting block.

### Tests

The extraction is itself the main test win — `fetch` and `norm` currently can only
be exercised through `CliRunner`. Add direct-call coverage:

- `run_norm` on a fixture directory, asserting the returned table names and row
  counts, and that `MissingDataError` names the first missing input.
- `run_fetch` against the existing fake client, for both the full and incremental
  paths, asserting `FetchResult` counts and that the sync state is written.
- `ConfigError` when `base_url` or `api_key` is neither passed nor in the
  environment, and when mutually exclusive flags are combined.
- `paths.py` resolution: default, env-var override, and explicit argument, for
  each of the five directories.
- One test that the `ojs` logger emits no output without a handler attached, so
  the library stays quiet by default.

Keep the CLI tests as-is; they now double as the compatibility check.

### Docs

- README: a short **Using ojs as a library** section showing `run_fetch` /
  `run_norm`, next to the existing CLI usage.
- CHANGELOG `[Unreleased]`: the new modules and public functions, noting that CLI
  behavior is unchanged.
- Version: additive public API with no breakage, so a minor bump.

## Log

**2026-09-08** — Implemented on `feature/importable-run-entry-points`
(PR #31). Four commits: the refactor, direct-call tests, the PR-url plan
update, and docs.

### What landed

- `ojs/errors.py` — `OjsError` with `ConfigError`, `OptionError`, and
  `MissingDataError`. `ReportAuthError` additionally subclasses `OjsError` (still
  a `RuntimeError`), so one `except OjsError` covers every deliberate failure.
- `ojs/paths.py` — the five directory resolvers, each following
  argument -> env var -> default.
- `ojs/api/run.py` — `run_fetch`, `run_download`, `run_norm` plus `FetchResult`,
  `DownloadResult`, `NormResult`, and a `DatasetCount(fetched, total)` value type.
- `ojs/website/run.py` — `run_report_fetch` and `run_norm`, both parameterized by
  a `REPORTS` table holding each report's glob, URL env var, output directory, and
  normalizer, so neither function branches on the report name.
- `ojs/cli.py` cut from 749 to ~380 lines; every command is options plus a call
  plus a `try/except OjsError`.
- The three normalize pipelines now return `dict[str, pl.DataFrame]` so `run_norm`
  reports row counts without re-reading the CSVs it just wrote.

### Decisions taken at implementation time

- **`logging` over an `on_progress` callback**, as the plan's design section
  leaned. Every `print` below `cli.py` became a `logger` call on a
  `logging.getLogger(__name__)` per module; `ojs/__init__.py` installs the
  `NullHandler`. The conversion covers the whole package, not only the command
  bodies -- leaving `client.py` and the normalizers printing would defeat "quiet
  by default" and make the library-silence test meaningless.
- **The CLI handler resolves `sys.stdout` at emit time.**
  `logging.StreamHandler` binds its stream at construction, which would have made
  command output invisible to `CliRunner` (it swaps `sys.stdout` after import) and
  broken every CLI test. A tiny write-proxy fixes it without subclassing
  `Handler` (`typing.override` is 3.12+, and the project supports 3.11).
- **Warning-level messages keep their literal `WARNING` prefix.** The CLI prints
  records bare (no level name), so the prefix is what marks them in output;
  dropping it would have changed what the commands print.
- **`OptionError` split out from `ConfigError`,** which the plan had covering both
  missing config and contradictory flags. Mapping both to one CLI handler would
  have moved the flag-conflict and bad-date exits from click's 2 to 1. The split
  keeps exit codes identical: `OptionError` -> `typer.BadParameter` (exit 2),
  every other `OjsError` -> `Error: <msg>` (exit 1). The plan's own compatibility
  bar outranked its example grouping here.
- **`run.py` imports the client as a module** (`client.fetch_submissions(...)`)
  rather than binding the functions at import, so the existing tests'
  `monkeypatch.setattr(client_mod, ...)` still takes effect. `cli.py` imports the
  run modules inside the command bodies, preserving the deferred-import startup
  cost the previous code was careful about.

### Compatibility check

Beyond the suite, the CLI was diffed against the pre-refactor code in the main
checkout: 18 scenarios (all three `norm`s, all three `schema`s, three `--help`
outputs, and nine error paths spanning exits 0, 1, and 2) run against identical
fixtures with identical env. Output and exit codes are **byte-identical**,
including the polars sample-table renders, the warning ordering, and the
`BadParameter` usage box.

The five library tests that asserted on `capsys` moved to `caplog` -- they assert
the library *emits* the record rather than that something happened to be printing
it. Four of them were passing only by accident of import order (an earlier test
importing `ojs.cli` attached the stdout handler); they are deterministic now. No
CLI test was touched. Suite: 159 passed, coverage 91.2% (floor 86%).

### Follow-up

The version was left at `0.8.4a0`. The public API is additive, so the next stable
release should be a **minor** bump (`0.9.0`) rather than the patch the current
prerelease implies -- run from `main` through the normal release workflow, not
from this branch.

### Review follow-up

**2026-09-08** — Review of PR #31 at high effort: four finders and four
adversarial verifiers over the full diff. 28 candidates, 14 survived
verification. Two commits actioned them.

Actioned (`update: tighten run entry point args, guards, and tests`):

- **`run_download`'s `out_dir` named the input directory**, not the output --
  the reverse of `run_fetch`/`run_norm` in the same module, so a caller
  redirecting downloads would silently redirect the JSON dumps instead and the
  files would still land in the default. Renamed to `api_dir`, matching
  `run_norm`; `dest_dir` remains the artifact destination. The CLI never passed
  it, so nothing observable changed. This was the one finding worth catching
  before release -- a public keyword argument is expensive to rename later.
- **`OPTIONAL_NORM_FILES` was dead.** `run_norm` hardcoded the same four names,
  so editing the mapping to add a dump would have been a silent no-op. It is now
  a tuple that `run_norm` iterates.
- **`stats_interval` was unvalidated in the library.** The CLI's enum guards it
  on that side, so a direct caller passing `"days"` got a raw
  `httpx.HTTPStatusError` mid-fetch instead of the `OptionError` every other
  malformed argument raises. Added `STATS_INTERVALS` and an up-front check.
- **The quiet-library test poisoned logging's effective-level cache.** It
  restored the `ojs` logger's level by attribute assignment; only `setLevel`
  calls `_clear_cache()`. A verifier demonstrated the real `ojs.schema` logger
  left with `_cache == {20: False}` while `ojs.level` was 20 -- any later test
  asserting an INFO record via `caplog` would have silently seen nothing.
  Latent, because every existing `caplog` assertion checks WARNING records.
  Fixed, and the test now asserts the child logger is left as it was found.
- **Four coverage gaps on surface this plan introduced**: the stats success path
  (`stats_ok` and the three stats `DatasetCount`s were only ever asserted
  `None`/`False`), the stats watermark advancing on success, a standalone
  `full=True` run's sync-state reset, `files=True`, and `DownloadResult.failed`
  plus the `skipped.json` persistence block (zero coverage). Six tests added;
  `api/run.py` coverage 91% -> 95%, suite 159 -> 165 passing, total 91.7%.

Actioned (`update: correct library usage docs in readme, changelog`):

- The README and CHANGELOG both claimed *every* entry point is keyword-only and
  defaults to `None`; the two website entry points take a required positional
  `report` with no environment fallback. Both corrected.
- The README's two snippets imported a different `run_norm` under the same name;
  a verifier reproduced the resulting `TypeError` from combining them. The
  website example now imports the module (`website.run_norm`).
- The README offered `logging.basicConfig(...)` as the way to get "the CLI's
  output", but `basicConfig` binds to stderr while the CLI deliberately routes
  through a stdout proxy -- a caller capturing stdout would have got an empty
  file. Replaced with an explicit stdout handler on the `ojs` logger.

Conscious no-ops:

- **CLI flag spellings in library error messages** (`--full cannot be combined
  with --incremental/--since`). Flagged by two finders, rejected on
  verification: the text pre-exists verbatim on `dev`, the tests pin it, and it
  is what keeps CLI output byte-identical -- this plan's own acceptance bar.
  Making the messages library-native means mapping them in `cli.py`, which is a
  behavior change, not a cleanup.
- **Two classes named `NormResult`** (`api.run` and `website.run`). They are
  genuinely different results and module-qualified use is ordinary Python;
  renaming either would be worse. Documented the distinction in the website
  class's docstring instead.
- **Five untested paths verified as pre-existing on `dev`** -- the non-403
  re-raise in the stats block, `run_download`'s default `fetch=True` path and
  its no-explicit-ids branch, `since` without `incremental`, and the
  `stats_last_sync` advance as an internal state field. The refactor moved this
  code without changing it; closing those gaps is separate work.
- **The three `schema` commands still hold their logic in `cli.py`.** Out of
  this plan's stated scope, which named the `fetch`/`norm`/`download` pipelines.

## Retrospective

- **The compatibility bar did the design work.** Holding "nothing observable
  changes" as the acceptance criterion settled the two decisions the plan had
  left open -- `OptionError` split out from `ConfigError` (to keep click's exit
  2), and the dashed flag names kept inside library messages. Both look like
  layering violations in isolation and are correct against the bar. It also
  paid off in review: two finders independently flagged the flag names, and the
  bar is what rejected them.
- **`logging` over an `on_progress` callback was right, and bigger than
  planned.** The plan scoped the conversion to the command bodies; leaving
  `client.py` and the normalizers printing would have made "quiet by default" a
  half-truth and the silence test meaningless. The cost was one real subtlety --
  `StreamHandler` binding its stream at construction, which would have broken
  every `CliRunner` test -- and one latent trap the review caught, the
  effective-level cache. Global logging state is harder to put back than it
  looks; a test that touches it should restore through the API that invalidates
  the cache, not by assignment.
- **The refactor's own new surface is where the coverage debt landed.** Every
  confirmed gap was a field this plan added (`stats_ok`, `DownloadResult.failed`,
  `sync_state_written`), not old logic -- and the verifier that separated those
  from the five pre-existing gaps is what kept the follow-up proportionate.
  Returning results instead of printing them creates assertions someone has to
  write; extracting an entry point is only half the work.
- **Worth repeating next time:** diffing the pre- and post-refactor CLI across
  18 scenarios gave more confidence than the test suite did, and took minutes.
  For a pure refactor with a byte-identical bar, that comparison is the real
  test.
