---
id: 10
slug: user-details
status: done
branch: feature/user-details
created: 2026-10-12T20:01:57-07:00
concluded: 2026-10-12T20:22:41-07:00
pr: https://github.com/gitronald/ojs/pull/47
---

# Add country, affiliation, and registration date to the users table

## Plan

Carried from plan 009 (step 3 and its investigation), which shipped the `users`
and `user_groups` tables from the `/users` summary records.

### Problem

The `/users` list endpoint returns a summary of each account (`id`, `userName`,
`email`, `fullName`, `orcid`, `disabled`, `groups`). It does not return
`country`, `affiliation`, or any date, so the `users` table cannot say where a
reviewer is based or when an account was registered.

### What is already known

From the OJS 3.3 swagger snapshot (`ojs/api/swagger.json`), checked in plan 009:

- `GET /api/v1/users/{userId}` returns the full `User` schema, which carries
  `country`, `affiliation` (multilingual), `dateRegistered`,
  `dateLastRegistered`, and `dateValidated`. There is no `dateLastLogin` field
  in that schema, despite plan 009's wording.
- `/users` takes `roleIds` and `status` filters, so a details fetch can be
  limited to given roles.

### Investigate first

Both need a live OJS install and its credentials:

1. **The single-user endpoint on the install.** Confirm it returns the fields
   above, what `dateLastRegistered` holds in practice, and the cost: one request
   per user, so time a run over a few hundred accounts.
2. **How the admin Users export is reached.** Find the route and method of
   Statistics > Users > Export (a form that selects user groups, so possibly a
   POST rather than the GET that `download_report` sends), and whether its
   columns vary with the install's user groups.

Record both answers in the Log before choosing a path.

### Steps

1. **Add the missing fields** by whichever path the investigation favors:
   - *API details*: an opt-in `ojs api fetch --user-details` that fetches each
     user's full record, incremental (a user already fetched is skipped unless
     `--full`), into a `user_details.json` beside `users.json`, joined into
     `users` as `country`, `affiliation`, and `date_registered`. Optionally
     limited to given role ids (`--user-roles 4096`).
   - *Website export*: an `ojs users fetch` / `norm` / `schema` pipeline beside
     `articles` and `reviews`, reusing `download_report` (extended for a POST if
     needed), with the URL in `OJS_USERS_REPORT_URL`. Its per-group Yes/No
     columns are unpivoted into the existing `user_groups` shape, so both paths
     produce the same tables.
2. **Tests** with synthetic users (`example.com` addresses, invented names).
3. **Docs**: README, the `init` template if a new env key is added, and the
   changelog.

### Decisions

1. Inherited from plan 009: users are PII and the package leaves storage to the
   caller; groups are rows, not columns.

## Log

- **2026-10-12T20:11:36-07:00** — Activated.
  - Branch: `feature/user-details`
  - Base: `dev` at `8497222`
  - Worktree: `.worktrees/user-details`
  - PR: pending
- **2026-10-12T20:13:52-07:00** — Investigation against the live install (bounded read-only probes, approved by the user: about 60 API GETs, one admin login, one export). Outputs stayed in session scratch; nothing with personal data entered the repo.
  - Question 1: `GET /api/v1/users/{id}` returns the full record on this install, including `country` (string), `affiliation` (locale dict), `dateRegistered`, `dateValidated`, and `dateLastLogin` (all `YYYY-MM-DD HH:MM:SS`). The install returns `dateLastLogin`, which the swagger snapshot lacks; the snapshot's `dateLastRegistered` is absent. 55 sequential GETs averaged 0.110 s (max 0.131 s), so a full pass over the 1153 accounts (`status=all`) is about 2 minutes; 771 accounts hold the reviewer role (`roleIds=4096`).
  - Question 2: the Statistics > Users page is a Vue form (`usersReportForm`) that POSTs `userGroupIds[]` to `{base}/stats/users`. The POST only returns a URL: the export itself is `GET {base}/api/v1/users/report?userGroupIds[]=...`, which accepts the API token (no login needed) and returns one CSV. Its columns are `ID, Given Name, Family Name, Email, Phone, Country, Mailing Address, Date registered, Updated`, then `Site Admin` and one Yes/No column per user group, so they vary with the install's groups. It returned 1090 rows against 1153 accounts; the 63-account gap was not explained (not verified whether they are disabled or groupless). It carries no affiliation, ORCID, or last-login date.
  Decision: the API details path. The report lacks affiliation (a goal of this plan) and last login, and misses accounts; the detail fetch costs about 2 minutes for every field.
  Deviation from the plan: no incremental skip of already-fetched users. `dateLastLogin` and `country` change with no recency signal on users, so a skip would leave stale values; each `--user-details` run refetches the selected users instead, and `--user-roles` keeps the count down. `date_last_login` is added beside the three planned columns.
- **2026-10-12T20:16:07-07:00** — PR opened: https://github.com/gitronald/ojs/pull/47
- **2026-10-12T20:16:15-07:00** — Built the API details path.
  - `ojs.api.client.fetch_user_details` (one GET per user on one client; 403/404 logged and skipped, other errors raise).
  - `run_fetch(user_details=, user_roles=)` and `ojs api fetch --user-details --user-roles <id>` (repeatable). Role filtering uses the `groups` already in `users.json`, so it costs no extra request; `--user-roles` without `--user-details` is an `OptionError`. `FetchResult.user_details` reports the count.
  - `user_details.json` joins into `users` as `country`, `affiliation` (en_US, else any locale), `date_registered`, `date_last_login` (Datetime). The columns are always present and null without details, so the table's shape does not depend on the flag.
  - 7 new tests (245 pass); README section and changelog entry.
  - Not verified: no end-to-end `ojs api fetch --user-details` run against the live install, since a full fetch was outside the approved probe budget. The parser was written against the field shapes the probe recorded.
- **2026-10-12T20:16:18-07:00** — Implemented: 5 commits ahead of `dev`.
- **2026-10-12T20:23:13-07:00** — Review gate: `/code-review` at medium on PR #47, summary posted to the PR. Commit 8f5fa14 carries the fixes.
  - Review follow-up: 11 candidates raised, 4 confirmed and fixed. (1) A stale `user_details.json` was joined with no age signal: records are now stamped `_fetched_at`, surfaced as `users.details_fetched_at`. (2) A `--user-roles` run overwrote the dump and discarded other accounts' details: records now merge by user id (regression test `test_run_fetch_user_details_merges_and_keeps_unselected`). (3) 403/404 skips were silent: a `WARNING` with the count (asserted in the fetch test). (4) Role 17 named two ways: now "section editor" in both. Conscious no-ops: string `roleId`, detail record without `id`, non-standard date formats, `--user-roles` matching no group, inline role filter (all below confidence 30 or not reproducible).
  - This reverses the earlier "rewrite every run" choice: every selected user is still refetched, but the dump is merged rather than replaced.
  - Check gate before merge: ruff check, ruff format --check, pyrefly, and pytest (246 passed).
  - Still not verified: no end-to-end `ojs api fetch --user-details` run against the live install.

## Retrospective

- The probes overturned two premises before any code was written: the swagger snapshot's field list (the live install returns `dateLastLogin`, not `dateLastRegistered`) and the export's mechanics (the admin POST only hands back a GET on `/api/v1/users/report`, which takes the API token). A live check beat reasoning from the snapshot.
- The website-export path lost on completeness (no affiliation or last login, 63 accounts missing), not cost; the per-user fetch is about 2 minutes, so the plan's worry about request counts did not hold.
- My first answer to staleness, rewriting the dump every run, traded one problem for another: a role-limited run wiped everyone else's details. Merging by id with a fetch stamp fixed both, and the review caught it, not the tests I wrote.
- Printing distinct values per column for "shape only" leaked a real value from a sparse column into session output. A probe meant to avoid personal data should print counts only, never values, whatever the cardinality.
