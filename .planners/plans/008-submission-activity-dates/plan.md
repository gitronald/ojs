---
id: 8
slug: submission-activity-dates
status: draft
branch:
created: 2026-09-25T08:59:28-07:00
concluded:
pr:
---

# Add the last-activity date to the submissions table

## Plan

### Problem

The normalized `submissions` table has no timestamp for editorial activity.
Moving a submission from Submission to Review in the OJS workflow leaves
`lastModified` unchanged, since that field tracks metadata edits. The only
timestamp that moves is `dateLastActivity`, which the raw API dump already
carries and `ojs api fetch` already uses as its sync watermark
(`ojs/api/sync.py`). `normalize.py` never copies it into the table, so a
downstream consumer that wants to date a stage change has to parse the raw JSON
itself.

The API also returns `reviewRounds` (`round`, `stageId`, `statusId`,
`status`, e.g. "Waiting for reviewers to be assigned.") and a `stages` list
flagging the active stage. Neither carries a date, but together they say what
the latest activity was.

### Investigate first

1. **What moves `dateLastActivity`.** On a test submission, record the value
   before and after each action: a stage change, a review round created, a
   reviewer assigned, a discussion (query) opened, a file uploaded, a metadata
   edit. Confirm which of these bump it and whether `lastModified` moves on
   any of them.
2. **Timezone.** Establish whether the API reports `dateLastActivity` (and
   `dateSubmitted` / `lastModified`) in UTC or the server's local time, and
   document the answer in the column description.
3. **Better sources.** Check whether any endpoint the token can read exposes a
   dated stage change directly: editorial decisions, the event log, or the
   `reviewRounds` objects on a full (non-summary) submission fetch. If one
   does, prefer it and note the endpoint here.

### Change

- Add `date_last_activity` (`pl.Datetime("us")`, source `dateLastActivity`)
  to `schemas.Submissions`, after `last_modified`, and populate it in
  `normalize.py`.
- If step 3 finds nothing better, also add `review_round` and
  `review_round_status` (the latest round's number and status label, from
  `reviewRounds`), so a consumer can tell *which* activity the date probably
  belongs to.
- Document in the column description that `date_last_activity` is the
  **latest** activity, not the stage change: a consumer that needs the
  stage-change date must capture it the first time it sees the new stage,
  because the next action overwrites it.
- Tests: a fixture submission with and without `dateLastActivity` and
  `reviewRounds`; assert the dtype and the null handling.
- Regenerate `table_schemas.csv` via `ojs api schema` and note the new
  columns in `CHANGELOG.md` under `[Unreleased]`.
