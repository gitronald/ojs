---
id: 0
slug: submission-file-downloads
status: draft
branch: feature/ojs-file-download-auth
created: 2026-06-08T22:33:49-07:00
concluded:
pr:
---

# Investigate submission file binary download

## Plan

`ojs api download` pulls submission file **metadata** fine but cannot retrieve the
file **bytes** from an OJS 3.3.0.7 instance (REST API spec 3.3) using an API
token. A `--type all` run produces **0 real files and 805 "Access denied" JSON
stubs**, yet reports `Downloaded 808 new files`. This is an exploratory plan:
confirm the diagnosis against the package, decide the misleading-behavior fixes
`ojs` needs regardless, and research whether token- or session-based binary
download is possible before picking a retrieval strategy. **Decision is
deferred** — we lay out the options here.

### The problem

Each file's metadata (`/submissions/{id}/files`) carries two URLs:

- `url` — the OJS **grid handler**
  (`…/$$$call$$$/api/file/file-api/download-file?submissionFileId=…&submissionId=…&stageId=…`),
  the editorial-UI download component, authenticated by **session cookie**.
- `_href` — the REST resource `…/api/v1/submissions/{id}/files/{fileId}`.

### What we've already tried (probed read-only against the live instance)

| Request | Result |
|---|---|
| `url` + `?apiToken=` (what `ojs api download` sends) | `200` JSON `{"status":false,"content":"Access denied."}` (~73 B) |
| `url` + `Authorization: Bearer <token>` | `200` JSON "the current role does not have access to this operation" |
| `_href` + `?apiToken=` (no stageId) | `403` "A workflow stage was not specified." |
| `_href` + `?apiToken=&stageId=N` | `200` **metadata JSON only** (~1 KB) — never the binary |
| `article/download/{submissionId}/{galleyId}` (no auth) | `200` **real PDF** |

Findings:

- This instance authenticates the token via the **`?apiToken=` query param**, not
  the `Authorization: Bearer` header (Bearer returns `403` everywhere — that is
  the *unauthenticated* role, not the token's).
- The grid `download-file` handler does not accept the API token at all.
- The REST file resource returns **metadata JSON, not bytes** — OJS 3.3 appears to
  have no REST endpoint that streams file content.
- **Published galley files are public** via `article/download/{submissionId}/{galleyId}`.
- Owner-confirmed: the same file link downloads in a logged-in browser and fails
  logged-out → the binaries are **session-gated**, matching the grid handler.

### `ojs` fixes needed regardless of retrieval strategy

1. **Detect non-binary / error responses.** `download_files` (`ojs/api/files.py`)
   only treats `403`/`404` as failures (a per-file skip log); on a `200` it writes
   `response.content` straight to disk with no content-type check. Here every
   blocked file returns `200` + `Content-Type: application/json` with an OJS
   `{"status":false,…}` error envelope, so those JSON stubs land as `.pdf`/`.docx`.
   Extend the skip path to treat a JSON/HTML content-type (or an OJS error
   envelope) as a **failure** — log it to `skipped.json` and do not write the file.
2. **Don't report false success.** Count only real artifacts; surface the
   skipped/failed count prominently (`Downloaded 808 new files` with 0 real is
   actively misleading).
3. **Document the limitation** in the README/CHANGELOG: API-token download does
   not work on OJS 3.3 grid-handler installs; a session is required for non-public
   files.

### Retrieval options (decision deferred)

1. **Reuse a logged-in session cookie (recommended start).** The owner pastes
   their session cookie; the downloader sends it on the grid-handler `url`s.
   Covers every stage; no password stored. Caveat: cookies expire (CSRF not
   needed for GET downloads).
2. **Scripted login.** Fetch the login page `csrfToken`, POST `/login/signIn`,
   capture the session cookie, download with it. Fully automatable; handles
   credentials at runtime (never commit them).
3. **Public galleys only.** `article/download/{submissionId}/{galleyId}` (no
   auth) — simple and credential-free, but only covers *published* galleys.
4. **Metadata-only (status quo).** Keep the metadata tables; rely on the external
   canonical file store for the actual documents.

Likely combination: option 3 for published galleys + option 1 for everything
else, with option 2 as a fallback.

### Research to do (web / PKP docs) — pin to OJS 3.3.0.7, API spec 3.3

Behavior differs across 3.2 / 3.3 / 3.4, so verify against the exact version:

- Does OJS 3.3's REST API expose any **file-content download** endpoint, or is the
  grid `download-file` handler the only path?
- Can an **API token's role** be granted grid-handler file-download access, or is
  token auth simply not wired to it in 3.3? (PKP forum, `pkp/pkp-lib` issues.)
- Is there a supported **headless/session** flow (login + cookie, or a CSRF token
  flow) others use to script OJS file downloads?
- Did **OJS 3.4+** add token- or REST-based file download (informs whether an
  upgrade would fix this)?
- Confirm the **public galley URL** pattern for 3.3
  (`article/download/{submissionId}/{galleyId}` vs `/{galleyId}/{fileId}`).

Record findings here before choosing an option.

### Out of scope

Implementing the chosen retrieval strategy. This plan ends when the
misleading-behavior fixes are done, the research is captured, and an option is
chosen; implementation is a follow-up plan on `feature/ojs-file-download-auth`.
