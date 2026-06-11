---
id: 1
slug: verify-modified-date
status: draft
branch:
created: 2026-06-08T22:33:49-07:00
concluded:
pr:
---

# Verify last modified date for review-assignment edits

## Plan

Verify whether OJS's `lastModified` advances when a **review assignment** is
edited. The incremental sync computes its high-water mark from `dateLastActivity`
(`submission_watermark` in `ojs/api/sync.py`); `lastModified` is not used there
today. If `lastModified` reliably advances on review-assignment edits, switching
the watermark to it would cut redundant refetches.

Steps:

- On a sandbox/test submission, edit a review assignment and observe whether
  `lastModified` advances (and how it compares to `dateLastActivity`).
- If it advances reliably, update `submission_watermark` in `ojs/api/sync.py` to
  track `lastModified`; otherwise record the finding and keep `dateLastActivity`.
