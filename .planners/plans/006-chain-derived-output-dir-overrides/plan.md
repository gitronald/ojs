---
id: 6
slug: chain-derived-output-dir-overrides
status: draft
branch:
created: 2026-09-08T21:38:23-07:00
concluded:
pr:
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
