---
id: 3
slug: articles-normalize-column-ranges
status: active
branch: feature/articles-normalize-column-ranges
created: 2026-07-08T18:39:28-07:00
concluded:
pr:
---

# Fix articles normalize silently dropping editor and decision columns

## Plan

> Stub — to be fleshed out and implemented after re-collecting article data.
> Surfaced while verifying [002](../002-reviews-csv-collection/plan.md) against a
> live instance: `ojs articles fetch` pulled the full real Articles Report, and
> `ojs articles norm` then warned that **42 raw columns were unmapped and
> dropped**.

### The problem

`ojs/website/articles/normalize.py` hardcodes the numbered-column ranges it
unpivots:

```python
AUTHOR_RANGE = range(1, 16)  # authors 1-15
EDITOR_RANGE = range(1, 5)  # editors 1-4
DECISION_RANGE = range(1, 10)  # decisions 1-9
```

A real export exceeded two of these: it carried a **5th editor** (`Given Name
(Editor 5)`, its profile fields, and all of that editor's decisions) and
**editor decisions numbered 10 and 11**. Every column past the hardcoded bound is
counted as "unmapped" and silently excluded from `editors.csv` / `decisions.csv`
— genuine editorial data loss. The normalizer *does* warn (it surfaces unmapped
columns rather than dropping them silently at the schema level), but nothing
widens to cover them.

### Direction (to refine)

- Fix at the source: derive the author/editor/decision ranges from the columns
  actually present in the export (scan the `(Author N)` / `(Editor N)` /
  `Editor Decision M (Editor N)` headers) instead of hardcoding caps, so the
  pipeline never drops trailing entities regardless of how many an export has.
- Remake the articles schema accordingly (the `(... N)` field maps and the
  decision headers), keeping it the single source of truth.
- Add fixtures/tests covering an export with more editors and decisions than the
  old caps, to lock in that nothing is dropped.

### Notes

- Reviews normalization is unaffected — it has no numbered-column unpivot.
- Blocked on re-collecting representative article data elsewhere before reworking
  the schema; no code changes until then.

## Log

- 2026-09-25: Unblocked. Two real Articles Report exports were available for
  verification: an older one within the old caps (15 authors, 4 editors,
  decisions 1-9) and a newer one carrying a 5th editor and decisions 10 and 11.
  Running the old normalizer on the newer export reproduced the report exactly:
  42 unmapped columns, editors capped at 4, decisions at 9.
- Implemented the fix at the source in `ojs/website/articles/normalize.py`:
  removed `AUTHOR_RANGE` / `EDITOR_RANGE` / `DECISION_RANGE` and added
  `entity_numbers` (reads `<base> (Author|Editor N)` indices off the headers,
  restricted to bases the schema maps) and `decision_slots` (pairs every
  `Editor Decision D  (Editor E)` with its `Date decided` companion, ordered by
  editor then decision). The unpivot, the first-author `author_count`, the
  decisions extraction, and the unmapped-column check all consume those, so the
  schema's `(... N)` patterns stay the single source of truth with no cap.
- Schema descriptions no longer state the old bounds (`1-15`, `1-4`, `1-9`).
- Verification on the newer export: no unmapped-column warning; two 5th-editor
  rows and two decisions numbered above 9 recovered; every row within the old
  caps byte-identical to the pre-fix output across all four tables. The older
  export normalizes identically before and after.
- Tests: an end-to-end fixture with 16 authors, 5 editors, and decisions 10 and
  11 (including a date-only slot), plus unit tests for `entity_numbers`,
  `decision_slots`, and the uncapped claimed-column set.
