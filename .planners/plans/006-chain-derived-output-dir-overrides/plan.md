---
id: 6
slug: chain-derived-output-dir-overrides
status: done
branch: feature/chain-derived-output-dir-overrides
created: 2026-09-08T21:38:23-07:00
concluded: 2026-09-08T21:49:50-07:00
pr: https://github.com/gitronald/ojs/pull/33
---

# Chain derived output-directory overrides through the run entry points

## Plan

### Problem

`ojs.paths` resolves five directories, three of which are *derived* — their
default is built from another directory rather than being a literal constant:

| Helper | Default when nothing is passed |
| --- | --- |
| `downloads_dir` | `OJS_DOWNLOADS_DIR`, else `data/ojs-website` |
| `api_dir` | `OJS_API_DIR`, else `data/ojs-api` |
| `articles_dir` | `OJS_ARTICLES_DIR`, else `downloads_dir() / "articles"` |
| `reviews_dir` | `OJS_REVIEWS_DIR`, else `downloads_dir() / "reviews"` |
| `files_dir` | `OJS_FILES_DIR`, else `api_dir() / "files"` |

The derived three call their parent helper with **no argument**, so the parent
falls back to the environment even when the caller has already overridden it.
Overrides therefore do not chain:

```python
from ojs.website import run as website

# Reads exports from ./custom-downloads, but writes the normalized tables to
# `data/ojs-website/articles` (or $OJS_ARTICLES_DIR) -- not ./custom-downloads/articles.
website.run_norm("articles", downloads_dir="custom-downloads")
```

`ojs.api.run.run_download` has the same shape: passing `api_dir` without
`dest_dir` reads the JSON dumps from the override but lands the artifacts under
the *default* `api_dir() / "files"`.

Scope of the bug: **library callers only**. `ojs.cli` calls every helper with no
arguments, so no CLI behavior is affected, and the entry-point tests always pass
both the parent and the derived directory, which is why the gap went unnoticed
through the v0.9.0 review. Introduced in v0.9.0 alongside the new directory
override arguments (before that, directories came only from the environment, so
there was nothing to chain).

### Decision to make

Two viable fixes; pick one and apply it consistently.

**Option A — teach the helpers about their parent.** Give each derived helper a
keyword argument for the parent it derives from:

```python
def articles_dir(
    override: Path | str | None = None, *, downloads: Path | str | None = None
) -> Path:
    if override is not None:
        return Path(override)
    return Path(
        os.environ.get("OJS_ARTICLES_DIR", downloads_dir(downloads) / "articles")
    )
```

`files_dir` takes `api: Path | str | None`. Callers that resolved a parent pass
it down; callers that did not are unchanged.

- Keeps resolution wholly inside `ojs.paths`, which is where the three-step rule
  is documented, so the run modules stay declarative.
- Widens the public signature of three helpers (additive and keyword-only, so no
  existing call breaks).

**Option B — thread the resolved parent from the run entry points.** Leave
`ojs.paths` alone and have `run_norm` / `run_download` build the derived default
themselves from the parent they already resolved, falling back to the helper only
when no parent override was given.

- No public signature change.
- Duplicates the env-var-then-default precedence in the run modules, and the
  duplication has to be repeated for each new derived directory — the kind of
  drift `ojs.paths` exists to prevent.

**Recommendation: Option A.** It fixes the precedence at its source (the helper
that owns the derivation) rather than at each call site, and the argument
addition is backward compatible.

An open sub-question either way: when a caller passes `downloads_dir` explicitly
but `OJS_ARTICLES_DIR` is *also* set, which wins? The proposal above keeps the
env var ahead of the derived parent (matching the current documented "argument ->
env var -> default" order, since the parent only feeds the *default* leg).
Confirm that reading is intended and state it in the docstrings, because the
alternative — an explicit parent argument outranking the child's env var — is
also defensible and would be a behavior change worth calling out.

### Decisions taken

Confirmed at implementation time, superseding the recommendation above on the
sub-question:

- **Option A**, as recommended: the parent keyword goes on the helper that owns
  the derivation.
- **The parent argument outranks the child's env var** — the *alternative*
  reading of the sub-question, not the one proposed above. Rationale: the point
  of "argument beats environment" is that a programmatic caller is not silently
  reconfigured by ambient environment, and keeping the child env var ahead would
  only half-fix the bug — `run_norm("articles", downloads_dir=X)` would still
  write somewhere unexpected whenever `OJS_ARTICLES_DIR` happened to be set in
  the caller's `.env`, which is the very surprise this plan calls a bug. The
  resulting rule is one rule rather than two: anything the caller passes beats
  anything in the environment. The child env vars still apply whenever no
  directory argument reaches the helper — every CLI invocation, since the CLI
  resolves directories from the environment only.

### Implementation order

1. Add the parent keyword to `articles_dir`, `reviews_dir`, and `files_dir` in
   `ojs/paths.py`; document the precedence decided above in each docstring and in
   the module docstring's statement of the three-step rule.
2. Pass the resolved parent through at the three call sites: the `out_dir`
   lookups in `ojs.website.run.run_norm` (via `ReportSpec.out_dir`, whose
   `Callable` type will need the extra keyword) and `paths.files_dir(dest_dir)` in
   `ojs.api.run.run_download`.
3. Tests in `tests/test_run.py` for the override-one-not-the-other case, which no
   test currently covers:
   - `run_norm(report, downloads_dir=X)` with no `out_dir` writes under `X`, for
     both `articles` and `reviews`.
   - `run_download(api_dir=X)` with no `dest_dir` writes artifacts under `X`.
   - Whichever precedence step 1 settles on, with the child's env var set and the
     parent passed explicitly, so the resolution order is pinned by a test rather
     than only by a docstring.
   - Unit tests in `tests/test_ojs.py` for the helpers themselves, covering the
     parent argument with and without the child env var set.
4. Update the README's "Using ojs as a library" bullet on argument-versus-
   environment precedence, which currently says only that a location argument
   bypasses the environment — it should say how a parent override reaches the
   directories derived from it.
5. Changelog entry under `[Unreleased]`, described as a fix (a library caller's
   override was silently ignored) and noting the additive keyword arguments.

### Out of scope

- Exposing directory overrides as CLI options. The CLI reads directories from the
  environment only, and that is deliberate — this plan does not change it.
- Renaming or consolidating the `OJS_*_DIR` env vars, or changing any default
  path. The precedence bug is fixable without touching either.

## Log

- Applied Option A with parent-argument-wins precedence (see *Decisions taken*).
- `ReportSpec.out_dir` could not keep its `Callable[[Path | str | None], Path]`
  annotation: a `Callable` alias cannot express a keyword-only parameter, and
  that parameter is the whole point of the change. Replaced it with an
  `OutDirResolver` `Protocol` in `ojs/website/run.py`, exported alongside
  `ReportSpec`.
- Both call sites pass the **raw** parent argument, not the parent they already
  resolved. Passing the resolved path would mean the child's env var could never
  win, since the parent is never `None` after resolution — the child env var
  would be dead code rather than the no-argument fallback it is meant to be.
- Step 3 placed the helper unit tests in `tests/test_ojs.py`; they went into
  `tests/test_run.py` instead, which is where every existing `ojs.paths` test
  lives (`PATH_CASES`, `clean_path_env`, `test_nested_defaults_follow_their_parent`).
  The new `DERIVED_PATH_CASES` table sits directly below them and reuses the
  same fixture.
- `ojs.api.run.run_norm` needed no change: it already derives its default output
  directory from the `source_dir` it resolved, rather than calling a helper with
  no argument. The bug was confined to the two call sites the plan names.
- Full suite: 182 passed, 92% coverage (86% floor). `ruff check`,
  `ruff format --check`, and `pyrefly check` all clean.

### Review follow-up (2026-09-08)

Reviewed at level `medium` on PR #33; no actionable findings, so no code changed
after the review. Four cleanup candidates were raised and all four rejected on
verification — recorded here because three of them are things a later reader is
likely to re-propose:

- The "pass the raw argument, not the resolved one" comments at both call sites
  were flagged as restating the docstrings. Rejected: the docstrings state the
  user-visible precedence, the comments guard the implementation trap. They stay.
- Routing through `downloads_dir(downloads)` / `api_dir(api)` in the parent
  branch was flagged as needless indirection, since only the parent's override
  branch is reachable there. Rejected: that is Option A — the derived default
  stays expressed in the parent's own resolution rule. Inlining `Path(...)`
  would be a regression against the decision above, not a simplification.
- The unused `env_var` parameter in
  `test_website_run_norm_downloads_override_reaches_the_tables` was flagged as
  dead. Rejected: `WEBSITE_REPORT_CASES` is shared with the sibling test that
  does assert on it, and pytest requires every parametrized name in the
  signature; splitting the table to drop the parameter would cost more than it
  saves.

The correctness finder returned nothing: the precedence rule is applied
consistently across all three helpers and both call sites, and no existing caller
reaches the new keyword-only parameters positionally.

## Retrospective

- The plan's recommendation on the open sub-question was overturned during
  implementation, and that was the right call. Writing the sub-question down as
  an explicit *decision to make* — rather than settling it in the proposal — is
  what made the reversal cheap: the alternative was already argued on the page,
  so choosing it took a paragraph rather than a rethink.
- The deciding argument only became visible once the bug was stated concretely.
  Keeping `OJS_ARTICLES_DIR` ahead of an explicit parent would have half-fixed
  the bug — `run_norm("articles", downloads_dir=X)` would still surprise a caller
  whose `.env` happened to set the child var. A rule with one clause (arguments
  beat the environment) beat a rule with two.
- The type annotation was the one thing the plan did not anticipate. A
  `Callable[...]` alias cannot express a keyword-only parameter, so adding the
  argument forced a `Protocol`. Worth remembering: a `Callable` field in a spec
  dataclass is a soft constraint on how its callees may later evolve.
- Both call sites had to pass the **raw** argument rather than the parent they
  had already resolved. This is the sharp edge of the design and the reason the
  inline comments exist — the resolved value is never `None`, so substituting it
  silently converts the child's env var from a fallback into dead code. A
  regression test pins each precedence leg at both entry points.
- The plan's step 3 named the wrong test file. Checking where the existing
  `ojs.paths` tests actually live (`tests/test_run.py`, next to `PATH_CASES` and
  `clean_path_env`) took one grep and let the new cases reuse the fixture rather
  than rebuild it.
