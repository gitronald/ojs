---
id: 10
slug: user-details
status: active
branch: feature/user-details
created: 2026-10-12T20:01:57-07:00
concluded:
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
