# ojs

Tools for working with the Open Journal Systems (OJS) API.

Pulls submissions, publications, reviews, users, and publication view statistics
from an OJS journal's `/api/v1/*` REST API, downloads the attached file artifacts
(manuscripts, revisions, reviewer attachments, and production galleys), and
normalizes the JSON into typed relational tables backed by polars. Incremental
sync re-pulls only what changed since the last run, so routine top-ups stay
cheap. A typed schema layer (`Column`/`Table` classes) is the single source of
truth for normalization and doubles as exportable column documentation. Also
normalizes the OJS dashboard's Articles and Reviews CSV report exports. Ships a
Typer CLI for the common fetch, download, and normalize workflows. Built against
the [OJS 3.3 REST API](https://docs.pkp.sfu.ca/dev/api/ojs/3.3); other versions
are untested and may differ, as the REST API saw breaking changes between 3.2
and 3.3.

## Project Structure

```
ojs/
├── cli.py              # Typer CLI: init, articles, reviews, api (+ schema docs)
├── schema.py           # Typed schema framework: Column/Table, apply(), doc export
├── utils.py            # HTML stripping + localized-field extraction
├── website/            # Manual website CSV-export pipelines
│   ├── articles/       # Wide CSV → submissions, authors, editors, decisions
│   └── reviews/        # Long CSV → reviews
└── api/                # REST pipeline
    ├── client.py       # OJS REST client (httpx, pagination, retry, early-stop)
    ├── files.py        # Submission file artifact downloads (disk layout, manifest)
    ├── normalize.py    # JSON → relational tables (schema-driven)
    ├── schemas.py      # API table schema classes
    ├── sync.py         # Incremental sync: high-water-mark state, raw-JSON upsert
    └── swagger.json    # OJS API reference (snapshot)
```

## Installation

```bash
uv tool install ojs
```

As a project dependency:

```bash
uv add ojs
```

From GitHub instead of PyPI:

```bash
uv tool install git+https://github.com/gitronald/ojs.git
# or, as a dependency: uv add git+https://github.com/gitronald/ojs.git
```

From source (for development):

```bash
git clone https://github.com/gitronald/ojs.git
cd ojs
uv sync
```

## Configuration

The CLI reads from a `.env` file in the current directory. Run `ojs init` to
scaffold one — it prompts for the journal URL and API token, and writes `.env`
with `0600` permissions:

```bash
ojs init
```

**Getting an API key.** In OJS, open your user profile
(`https://example.org/index.php/myjournal/user/profile`), select the **API Key**
tab, check **Enable external applications with the API key to access this
account**, and copy the key — use the **(re)generate** button if one isn't set
yet.

Values can also come from the environment. A user-level config file is loaded as
a fallback for anything not set in the current directory's `.env` (which takes
precedence): `~/.config/ojs/.env` by default, or the file named by
`OJS_CONFIG_PATH`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `OJS_BASE_URL` | (required for `api`) | OJS journal URL (e.g. `https://example.org/index.php/myjournal`) |
| `OJS_API_KEY` | (required for `api`) | OJS API token |
| `OJS_USERNAME` | (required for website `fetch`) | Editorial-manager login username |
| `OJS_PASSWORD` | (required for website `fetch`) | Password for that account |
| `OJS_REVIEWS_REPORT_URL` | (required for `reviews fetch`) | Review Report export URL (absolute, or a path relative to `OJS_BASE_URL`) |
| `OJS_ARTICLES_REPORT_URL` | (required for `articles fetch`) | Articles Report export URL (absolute, or a path relative to `OJS_BASE_URL`) |
| `OJS_DOWNLOADS_DIR` | `data/ojs-website` | Website CSV exports + normalized output |
| `OJS_ARTICLES_DIR` | `$OJS_DOWNLOADS_DIR/articles` | Articles output dir |
| `OJS_REVIEWS_DIR` | `$OJS_DOWNLOADS_DIR/reviews` | Reviews output dir |
| `OJS_API_DIR` | `data/ojs-api` | API JSON dump dir |
| `OJS_FILES_DIR` | `$OJS_API_DIR/files` | Where downloaded submission files land |

## CLI Commands

`norm` reads the typed schema classes directly — no separate step is required.
`schema` exports a `table_schemas.csv` documenting each table's columns, dtypes,
source mapping, and whether each column appears in the normalized output
(`in_output`).

### API

Fetch raw JSON from the REST API, download file artifacts, and normalize into
relational tables.

```bash
ojs api fetch               # fetch raw JSON from the OJS REST API
ojs api download            # download submission file artifacts (PDFs, etc.)
ojs api norm                # normalize API JSON into relational tables
ojs api schema              # export table_schemas.csv docs
```

### Articles

Fetch and normalize the OJS dashboard's Articles Report CSV export.

```bash
ojs articles fetch          # download the latest articles export from the website
ojs articles norm           # normalize the most recent articles export
ojs articles schema         # export table_schemas.csv docs
```

### Reviews

Fetch and normalize the OJS dashboard's Review Report CSV export.

```bash
ojs reviews fetch           # download the latest reviews export from the website
ojs reviews norm            # normalize the most recent reviews export
ojs reviews schema          # export table_schemas.csv docs
```

### Website report fetch

The Articles and Review reports are OJS dashboard exports (Statistics > Tools),
not REST endpoints, so `fetch` authenticates differently from the `api` commands:
it logs in with `OJS_USERNAME` / `OJS_PASSWORD` to establish a session, then
downloads the report at `OJS_REVIEWS_REPORT_URL` / `OJS_ARTICLES_REPORT_URL`. The
CSV lands in `OJS_DOWNLOADS_DIR` as `reviews-<YYYYMMDD>.csv` /
`articles-<YYYYMMDD>.csv`, where the matching `norm` command picks up the newest
file. The report URLs are instance-specific — set them in `.env` (see
`.env.example`).

### Article view stats

`ojs api fetch` also pulls publication view stats from the OJS `/stats/publications/*`
endpoints (skip with `--no-stats`). The API only exposes aggregated counts — the
finest granularity is **daily** (there are no per-event timestamps).

| Flag | Default | Purpose |
| --- | --- | --- |
| `--stats / --no-stats` | on | Toggle stats collection (e.g. when the API key lacks stats access) |
| `--stats-interval` | `day` | Timeline granularity: `day` or `month` |
| `--stats-since` | (none) | `dateStart` filter (`YYYY-MM-DD`) |
| `--stats-until` | (none) | `dateEnd` filter (`YYYY-MM-DD`) |

`ojs api norm` then writes three extra tables:

- `publication_stats` — one row per published submission with abstract, all-galley, PDF, HTML, and other view totals.
- `views_timeline` — long format (`submission_id`, `date`, `interval`, `views`, `kind`) with a per-submission abstract and galley series. `interval` records the granularity (`day` or `month`) a point was fetched at, so a file mixing both stays separable — filter on it rather than summing across intervals.
- `views_timeline_totals` — long format (`date`, `interval`, `views`, `kind`) with the journal-wide abstract and galley series, from the aggregate `/stats/publications/{abstract,galley}` endpoints (the data behind the OJS statistics-page graph). Use this for journal-wide totals rather than summing `views_timeline`.

If the API key lacks stats access, `fetch` prints a warning and skips the stats files, and `norm` simply omits the two tables.

### Submission files

OJS attaches the actual file artifacts (manuscripts, revisions, reviewer
attachments, production galleys) to each submission. `ojs api download` fetches
their metadata and then downloads the binaries.

```bash
ojs api fetch --files                 # also dump file metadata -> submission_files.json
ojs api download                      # download all files for all submissions
ojs api download -s 123 -s 456        # only these submissions (repeatable)
ojs api download --type galleys       # only published galley files
ojs api download --type review        # only review files / revisions / attachments
ojs api download --file-stage 4 --file-stage 15   # raw fileStage ids
ojs api download --no-revisions       # current files only, skip prior revisions
```

| Flag | Default | Purpose |
| --- | --- | --- |
| `--submission-id` / `-s` | all | Limit to these submission ids (repeatable) |
| `--type` | `all` | `all`, `galleys` (published), or `review` |
| `--file-stage` | (none) | Raw `fileStage` id(s); overrides `--type` |
| `--revisions / --no-revisions` | on | Also download prior revisions of each file |
| `--fetch / --no-fetch` | on | Refresh file metadata first (off: use stored JSON) |

Files are laid out under `OJS_FILES_DIR` as
`<submission_id>/<stage>/<fileId>_<name>`. A manifest (`manifest.json`) records
every artifact by its immutable physical `fileId`, so reruns skip files already
on disk — new uploads and revisions are downloaded incrementally.

**Rounds and revisions.** OJS tracks two distinct axes. A file's *stage*
(`fileStage`) says where in the workflow it lives; review files additionally
carry an `assocId` naming the **review round** they belong to. Separately, each
file's `revisions[]` holds prior uploads of that same logical file. `ojs api
norm` writes a `submission_files` table with one row per current file, including
`file_stage_label`, `review_round_id` (joins to `review_assignments.round_id`),
and `revision_count`. Downloads cover the current file plus every revision, each
keyed by its own `fileId`.

Downloading files requires an API token with permission to view them; the API
returns `403` for files the key cannot access.

### Incremental fetch

By default `ojs api fetch` does a full cold pull. For routine top-ups, `--incremental` fetches only what changed since the last successful sync and merges it into the existing JSON dumps, so `ojs api norm` stays a stateless re-derivation from the complete files.

| Flag | Purpose |
| --- | --- |
| `--incremental` / `-i` | Fetch only records changed since the last sync, merging into the JSON dumps |
| `--since YYYY-MM-DD` | Override the stored watermark (implies `--incremental`) |
| `--full` | Force a complete pull and reset the sync state |

How it works:

- A high-water mark lives in `data/ojs-api/sync_state.json` (the last sync time, plus each submission's `dateLastActivity`). It advances only after a run fully succeeds, so a failed fetch never skips records on the next run.
- Submissions and extended submissions are pulled newest-first by `dateLastActivity` and stop early at the watermark. Publication details are skipped for submissions whose `dateLastActivity` is unchanged — the biggest saving, since that endpoint costs one request per submission.
- A one-day overlap buffer re-pulls the boundary on each run; merges are idempotent (upsert by id), so the overlap is harmless.
- View stats: `publication_stats` (cumulative totals) is always pulled in full, while the daily `views_timeline` is re-pulled over a rolling window and merged by `(submission_id, interval, date, kind)`, refreshing recent buckets without dropping history.
- Users are always pulled in full — the API exposes no recency sort for users.

The OJS API has no server-side "modified since" filter, so incremental cannot detect upstream deletions; run `ojs api fetch --full` periodically to reconcile.

## Security & privacy

- The API token lives in `.env` (the `init` prompt hides input), alongside `OJS_PASSWORD` for the website `fetch` commands. `.env` is gitignored and written `0600` — keep it out of version control and out of shared locations.
- The API JSON dumps contain personal data pulled from OJS: `users.json` holds user records **including email addresses**, and the author/submission tables carry author names, emails, and ORCIDs. These files are written with the process umask (typically `0644`, i.e. world-readable). On a shared or multi-user host, run with a restrictive umask (e.g. `umask 077`) or point `OJS_API_DIR` at a private directory so other local users can't read them.
- The fetched website reports (`reviews-*.csv`, `articles-*.csv`) and their normalized tables likewise carry reviewer and author names, emails, and ORCIDs. They write under `OJS_DOWNLOADS_DIR` (default `data/ojs-website`, under the gitignored `data/`) — apply the same umask/private-directory care as for the API dumps.
