---
id: 2
slug: reviews-csv-collection
status: draft
branch:
created: 2026-06-08T22:33:49-07:00
concluded:
pr:
---

# Add reviews.csv website data collection

## Plan

Collect the Review Report (`reviews-*.csv`) directly from the OJS website, so the
reviews ingestion path no longer depends on a manual dashboard CSV export. Today
`ojs reviews norm` consumes a hand-exported `reviews-*.csv` (matched by the fixed
`REVIEWS_GLOB` constant in `ojs/cli.py`); this would automate fetching that export. The website pipeline
under `ojs/website/reviews/` is currently normalize-only (`normalize.py` +
`schemas.py`, no fetch step), so this adds the collection front-end to it.
