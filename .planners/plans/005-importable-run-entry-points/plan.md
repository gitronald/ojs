---
id: 5
slug: importable-run-entry-points
status: draft
branch:
created: 2026-09-08T14:15:19-07:00
concluded:
pr:
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
