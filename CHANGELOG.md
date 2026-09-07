# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

- Sync the dev tooling with the project template: trim the sdist to the package, README, changelog, and license; run `pytest` with branch coverage by default and fail below an 86% floor; bump the ruff pre-commit hook to v0.16.1 and the checkout, setup-uv, and PyPI publish actions in the workflows.

## [0.8.1] - 2026-07-08

- Bump runtime dependencies and dev tooling (polars 1.42.0, typer 0.26.8, ruff 0.15.20, pyrefly 1.1.1, pytest 9.1.1).
- Pin GitHub Actions to commit SHAs in the publish and test workflows, and drop the redundant github-actions cooldown from the Dependabot config.

## [0.8.0] - 2026-07-08

- Add `ojs reviews fetch` and `ojs articles fetch`, which log in to the OJS website with `OJS_USERNAME` / `OJS_PASSWORD` and download the instance-specific report CSVs (`OJS_REVIEWS_REPORT_URL` / `OJS_ARTICLES_REPORT_URL`) as `{name}-<YYYYMMDD>.csv` into `OJS_DOWNLOADS_DIR`, so the `fetch` to `norm` handoff needs no manual export; `ojs init` scaffolds the new keys.
- Breaking: drop `OJS_DATA_DIR` (and the `ojs init --data-dir` flag); output directories now split by source into `OJS_DOWNLOADS_DIR` (default `data/ojs-website`, website exports) and `OJS_API_DIR` (default `data/ojs-api`, API data), with the articles and reviews directories defaulting under `OJS_DOWNLOADS_DIR`.
- Bump `idna` to 3.18 to resolve PYSEC-2026-215.

## [0.7.2] - 2026-06-08

- First public release (history squashed into the initial commit).

## [0.7.1] - 2026-06-08

- `OJS_DOWNLOADS_DIR` now defaults to `data/ojs-website` (was `$OJS_DATA_DIR/website-downloads`), placing website CSV exports beside the API data rather than nested under it.

## [0.7.0] - 2026-06-08

- Add a user-level config fallback (`~/.config/ojs/.env` by default, or the file named by `OJS_CONFIG_PATH`) for values not set in the local `.env`; drop the `ojs init --api-key` flag (the key comes only from `OJS_API_KEY` or the hidden prompt).

## [0.6.0] - 2026-06-08

- Make the CSV export globs fixed package constants (drop the `OJS_ARTICLES_GLOB` / `OJS_REVIEWS_GLOB` env vars); default the data directory to `data/ojs-api` with the API JSON at its root; infer the `submission_files` schema from all rows.

## [0.5.0] - 2026-06-05

- Add an `interval` column to the view-stats timelines so day/month points stay separable; write API JSON dumps atomically; always pull cumulative `publication_stats` in full; redact the API token from HTTP error messages; constrain file writes to the download tree; tolerate JSON `null` in normalization.

## [0.4.0] - 2026-06-05

- Add the typed schema framework (`ojs/schema.py`: `Column`/`Table`) as the runtime source of truth for normalization, with `ojs api schema` exporting `table_schemas.csv`; move the website CSV pipelines under `ojs.website`.

## [0.3.2] - 2026-06-05

- Log unservable files to `skipped.json`; drop zero-view days from the per-submission `views_timeline`; unwrap the `{items, itemsMax}` envelope for submission files.

## [0.3.0] - 2026-06-01

- Add `ojs api download` for submission file artifacts (manifest-tracked, incremental) and a journal-wide `views_timeline_totals` table; normalize empty localized values to `null`.

## [0.2.1] - 2026-05-28

- Bump runtime dependencies and dev tooling (polars, typer, ruff, pyrefly).

## [0.2.0] - 2026-05-28

- Add incremental fetch (`ojs api fetch --incremental`/`--full`, with a sync-state watermark) and publication view stats (`publication_stats`, `views_timeline`).
