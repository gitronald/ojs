---
id: 9
slug: users-table
status: implemented
branch: feature/users-table
created: 2026-10-12T19:45:40-07:00
concluded:
pr: https://github.com/gitronald/ojs/pull/46
---

# Add a users table

## Plan

### Problem

`ojs api fetch` already downloads every user account (`users.json`, from
`/api/v1/users`), but `ojs api norm` uses it only to match authors to accounts
by email. Nothing writes a users table, so a consumer that wants to know who
holds a role (every account in the Reviewer group, say), or to link an address
to an OJS user id, has to parse the raw JSON itself or export the Users report
from the OJS admin by hand.

The list endpoint returns a summary of each user: `id`, `userName`, `email`,
`fullName`, `orcid`, `disabled`, and `groups` (each with `id`, `name`,
`abbrev`, `roleId`). It does not return `country`, `affiliation`, or
`dateRegistered`. The admin's Users export (Statistics > Users > Export) does
carry country and registration date, with one Yes/No column per user group.

### Investigate first

1. **What the single-user endpoint returns.** Check whether
   `GET /api/v1/users/{id}` returns the full user (`country`, `affiliation`,
   `dateRegistered`, `dateLastLogin`) on an OJS 3.3 install, and what it costs:
   one request per user, so time a run over a few hundred accounts.
2. **How the Users export is reached.** Find the route and method of the admin
   export (it is a form that selects user groups, so it may be a POST rather than
   the GET that `download_report` sends), and whether its columns vary with the
   install's user groups.

Record both answers in the Log before choosing the fetch path in step 3.

### Steps

1. **Normalize the users the API already fetches.** Add two tables to
   `ojs api norm`, declared in `ojs/api/schemas.py` like the others:
   - `users`: `user_id`, `username`, `email`, `full_name`, `orcid`, `disabled`.
   - `user_groups`: one row per user and group, `user_id`, `group_id`,
     `group_name`, `group_abbrev`, `role_id`. Groups are install-specific, so
     they are rows, not columns.
2. **Export their schema docs** through `ojs api schema`, as for the other API
   tables.
3. **Add the fields the summary lacks**, by whichever path the investigation
   favors:
   - *API details*: an opt-in `ojs api fetch --user-details` that fetches each
     user's full record, incremental (a user already fetched is skipped unless
     `--full`), into a `user_details.json` beside `users.json`, joined into
     `users` as `country`, `affiliation`, `date_registered`, and
     `date_last_login`. Optionally limited to given role ids
     (`--user-roles 4096`) to keep the request count down.
   - *Website export*: an `ojs users fetch` / `norm` / `schema` pipeline beside
     `articles` and `reviews`, reusing `download_report` (extended for a POST if
     step 2 of the investigation needs it), with the URL in
     `OJS_USERS_REPORT_URL`. Its per-group Yes/No columns are unpivoted into the
     same `user_groups` shape, so the two paths produce the same tables.
4. **Tests** with synthetic users (`example.com` addresses, invented names):
   the normalize step, the group unpivot, and the incremental detail fetch or the
   export parse.
5. **Docs**: README (the table list and the new flag or commands), the
   `init` template if a new env key is added, and the changelog.

### Decisions

1. **Users are PII.** The tables carry names and email addresses. As with the
   existing `authors` table, the package writes them and leaves where they are
   stored to the caller; the README says so.
2. **Groups are rows.** An install renames, adds, and removes user groups, so a
   fixed column per role would break across installs.
3. **The summary tables ship first.** Step 1 needs no new request and is useful
   on its own; step 3 can land in a later release if the investigation shows it
   is larger than expected.

### Out of scope

- Writing to users or groups (the package is read-only against OJS).
- Matching users to reviews beyond the existing `reviews` table's columns.

## Log

- **2026-10-12T19:58:48-07:00** — Activated.
  - Branch: `feature/users-table`
  - Base: `dev` at `6b8c302`
  - Worktree: `.worktrees/users-table`
  - PR: pending
- **2026-10-12T20:01:49-07:00** — PR opened: https://github.com/gitronald/ojs/pull/46
- **2026-10-12T20:02:21-07:00** — Investigation (from the swagger snapshot only; nothing was run against a live install).
  - Question 1, partly answered: `GET /api/v1/users/{userId}` returns the full `User` schema, with `country`, `affiliation`, `dateRegistered`, `dateLastRegistered`, and `dateValidated`. It has no `dateLastLogin`. The cost per user was not timed: that needs a live install.
  - Question 2, not answered: the route of the admin Users export needs admin access to a live install.
  - New finding: `/users` defaults to `status=active`, so `users.json` never held disabled accounts, and a `disabled` column would always have read false. `fetch_users` now sends `status=all`.
  Decision (per decision 3): steps 1, 2, 4, and 5 ship here; step 3 and both investigation questions moved to plan 010.
  Built: `Users` and `UserGroups` schemas, `normalize_users` / `normalize_user_groups` wired into `normalize_api` (always written, empty with headers when no users), group names localized with any-locale fallback. `ojs api schema` documents both through `API_TABLES`. README and changelog updated. 6 new tests and 2 updated; 238 pass.
- **2026-10-12T20:02:37-07:00** — Implemented: 6 commits ahead of `dev`.
