"""OJS CLI - normalize Open Journal Systems data exports."""

import json
import os
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import typer
from dotenv import load_dotenv

from ojs.api.sync import (
    SYNC_STATE_FILENAME,
    KeyFn,
    build_submission_modified,
    load_sync_state,
    merge_write_json,
    save_sync_state,
    stats_window_start,
    submission_watermark,
    upsert_json_list,
    write_json,
)
from ojs.schema import write_schema_docs
from ojs.website.articles.normalize import normalize
from ojs.website.articles.schemas import ARTICLE_TABLES
from ojs.website.reviews.normalize import normalize_reviews
from ojs.website.reviews.schemas import REVIEW_TABLES

# interpolate=False so a value containing `${VAR}` is read back verbatim instead
# of being expanded against the environment -- this tool's config (URLs, keys,
# dirs) never needs shell-style expansion. Quote/escape unescaping of `\\`, `\"`,
# and `\n` is independent of interpolation, so quoted values still round-trip.
load_dotenv(interpolate=False)
# Fall back to a user-level config file for any value the CWD `.env` (or the real
# environment) does not set: OJS_CONFIG_PATH if set, else ~/.config/ojs/.env.
# override=False so the CWD `.env` and the environment keep precedence.
config_path = os.environ.get("OJS_CONFIG_PATH") or "~/.config/ojs/.env"
load_dotenv(Path(config_path).expanduser(), override=False, interpolate=False)

app = typer.Typer(help="Normalize Open Journal Systems data exports.")
articles_app = typer.Typer(help="Articles data pipeline.")
reviews_app = typer.Typer(help="Reviews data pipeline.")
api_app = typer.Typer(help="OJS API data pipeline.")
app.add_typer(articles_app, name="articles")
app.add_typer(reviews_app, name="reviews")
app.add_typer(api_app, name="api")


class StatsInterval(StrEnum):
    """Timeline granularity accepted by the OJS stats endpoints."""

    day = "day"
    month = "month"


class FileType(StrEnum):
    """Named bundles of file stages for the `api download --type` option."""

    all = "all"
    galleys = "galleys"
    review = "review"


# Filename globs for the latest website CSV exports. Fixed naming conventions
# from the OJS dashboard (`articles-*.csv` / `reviews-*.csv`) -- internal to the
# package, not worth exposing as configurable env vars.
ARTICLES_GLOB = "articles-*.csv"
REVIEWS_GLOB = "reviews-*.csv"


def _data_dir() -> Path:
    return Path(os.environ.get("OJS_DATA_DIR", "data/ojs-api"))


def _downloads_dir() -> Path:
    return Path(os.environ.get("OJS_DOWNLOADS_DIR", "data/ojs-website"))


def _articles_dir() -> Path:
    return Path(os.environ.get("OJS_ARTICLES_DIR", _data_dir() / "articles"))


def _reviews_dir() -> Path:
    return Path(os.environ.get("OJS_REVIEWS_DIR", _data_dir() / "reviews"))


def _api_dir() -> Path:
    return Path(os.environ.get("OJS_API_DIR", _data_dir()))


def _files_dir() -> Path:
    return Path(os.environ.get("OJS_FILES_DIR", _api_dir() / "files"))


def _env_quote(value: str) -> str:
    """Quote a value for a .env line so python-dotenv reads it back verbatim.

    Unquoted, python-dotenv strips an inline comment at the first ` #`, so a value
    containing a space-delimited `#` (or surrounding whitespace) would be silently
    truncated. Double-quoting and backslash-escaping `\\` then `"` round-trips
    cleanly through ``dotenv_values``.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


@app.command("init")
def init(
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing .env"),
    base_url: str = typer.Option("", "--base-url", help="OJS_BASE_URL value"),
    data_dir: str = typer.Option(
        "data/ojs-api", "--data-dir", help="OJS_DATA_DIR value"
    ),
) -> None:
    """Scaffold a .env file in the current directory."""
    env_path = Path(".env").resolve()
    if env_path.exists() and not force:
        print(f"{env_path} already exists. Use --force to overwrite.")
        raise typer.Exit(1)

    # Fall back to an already-set environment variable before prompting, so a
    # non-interactive run (CI) can supply values through the environment. The API
    # key has no flag -- passing a secret on argv leaks it into shell history and
    # the process list -- so it comes only from OJS_API_KEY or the hidden prompt.
    if not base_url:
        base_url = os.environ.get("OJS_BASE_URL", "") or typer.prompt(
            "OJS_BASE_URL", default=""
        )
    api_key = os.environ.get("OJS_API_KEY", "") or typer.prompt(
        "OJS_API_KEY", default="", hide_input=True
    )

    lines = [
        f"OJS_BASE_URL={_env_quote(base_url)}",
        f"OJS_API_KEY={_env_quote(api_key)}",
        f"OJS_DATA_DIR={_env_quote(data_dir)}",
        "",
    ]
    env_path.write_text("\n".join(lines))
    # The file holds OJS_API_KEY; keep it owner-only regardless of umask so it is
    # not world-readable on a shared host. chmod (not a 0600 open) also re-tightens
    # an existing .env when regenerating with --force. (No-op on Windows, where
    # POSIX mode bits don't apply -- rely on directory/ACL access control there.)
    env_path.chmod(0o600)
    print(f"Wrote {env_path}")


@articles_app.command("schema")
def articles_schema() -> None:
    """Generate schema documentation for article tables."""
    out_dir = _articles_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    print("Creating schema documentation for article tables...")
    write_schema_docs(ARTICLE_TABLES, out_dir / "table_schemas.csv")


@articles_app.command("norm")
def articles_norm() -> None:
    """Normalize the most recent articles CSV export into relational tables."""
    downloads = _downloads_dir()
    out_dir = _articles_dir()
    pattern = ARTICLES_GLOB

    matches = sorted(downloads.glob(pattern), reverse=True)
    if not matches:
        print(f"Error: No files matching {pattern} found in {downloads}")
        raise typer.Exit(1)

    normalize(input_file=matches[0], output_dir=out_dir)


@reviews_app.command("schema")
def reviews_schema() -> None:
    """Generate schema documentation for review tables."""
    out_dir = _reviews_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    print("Creating schema documentation for review tables...")
    write_schema_docs(REVIEW_TABLES, out_dir / "table_schemas.csv")


@reviews_app.command("norm")
def reviews_norm() -> None:
    """Normalize the most recent reviews CSV export."""
    downloads = _downloads_dir()
    out_dir = _reviews_dir()
    pattern = REVIEWS_GLOB

    matches = sorted(downloads.glob(pattern), reverse=True)
    if not matches:
        print(f"Error: No files matching {pattern} found in {downloads}")
        raise typer.Exit(1)

    normalize_reviews(input_file=matches[0], output_dir=out_dir)


@api_app.command("fetch")
def api_fetch(
    stats: bool = typer.Option(
        True, "--stats/--no-stats", help="Fetch publication view stats."
    ),
    files: bool = typer.Option(
        False,
        "--files/--no-files",
        help="Fetch submission file metadata (one request per submission).",
    ),
    stats_interval: StatsInterval = typer.Option(
        StatsInterval.day, "--stats-interval", help="Timeline granularity."
    ),
    stats_since: str = typer.Option(
        None, "--stats-since", help="dateStart filter for stats (YYYY-MM-DD)."
    ),
    stats_until: str = typer.Option(
        None, "--stats-until", help="dateEnd filter for stats (YYYY-MM-DD)."
    ),
    incremental: bool = typer.Option(
        False,
        "--incremental",
        "-i",
        help="Fetch only records changed since the last sync, merging into the JSON.",
    ),
    since: str = typer.Option(
        None,
        "--since",
        help="Override the stored watermark: refetch changes on/after this date "
        "(YYYY-MM-DD). Implies --incremental.",
    ),
    full: bool = typer.Option(
        False, "--full", help="Force a complete pull and reset incremental sync state."
    ),
) -> None:
    """Fetch data from the OJS API and save raw JSON.

    Default is a full pull. `--incremental` (or `--since`) fetches only changed
    records and merges them into the existing JSON dumps; `--full` forces a
    complete pull and resets the sync state.

    Upgrade note: the view-stats timelines gained an `interval` column (day vs
    month) that is part of the merge key. Timelines written by an older version
    lack it, so the first `--incremental` run after upgrading would leave those
    legacy points alongside the re-fetched ones (their keys differ). Run a
    one-time `ojs api fetch --full` after upgrading to rewrite
    `views_timeline.json` / `views_timeline_totals.json` cleanly.
    """
    import httpx

    from ojs.api.client import (
        SKIP_STATUSES,
        fetch_all_publications,
        fetch_all_submission_files,
        fetch_publication_stats,
        fetch_submissions,
        fetch_submissions_extended,
        fetch_users,
        fetch_view_timeline_totals,
        fetch_view_timelines,
    )

    base_url = os.environ.get("OJS_BASE_URL")
    api_key = os.environ.get("OJS_API_KEY")
    if not base_url or not api_key:
        print("Error: OJS_BASE_URL and OJS_API_KEY must be set (run `ojs init`).")
        raise typer.Exit(1)

    if full and (incremental or since):
        raise typer.BadParameter("--full cannot be combined with --incremental/--since")
    incremental = incremental or since is not None

    # Validate date filters up front so a typo fails fast, before any fetch.
    for flag, value in (
        ("--since", since),
        ("--stats-since", stats_since),
        ("--stats-until", stats_until),
    ):
        if value is not None:
            try:
                date.fromisoformat(value)
            except ValueError as e:
                raise typer.BadParameter(f"{flag} must be YYYY-MM-DD") from e

    out_dir = _api_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    state_path = out_dir / SYNC_STATE_FILENAME

    # Resolve the incremental watermark: `--since` overrides the stored mark;
    # otherwise derive it from the previous sync. No usable mark -> full pull.
    state: dict[str, Any] = (
        load_sync_state(state_path) if incremental else {"submission_modified": {}}
    )
    sub_since = (
        since
        if since is not None
        else (submission_watermark(state) if incremental else None)
    )
    do_incremental = incremental and sub_since is not None
    if incremental and not do_incremental:
        print("No prior sync watermark; doing a full pull to establish a baseline.")

    def _save(
        path: Path,
        items: list[dict[str, Any]],
        key: str | KeyFn,
        *,
        incr_label: str,
        full_label: str,
    ) -> list[dict[str, Any]]:
        """Merge (incremental) or overwrite (full) `items` at `path`, and report."""
        if do_incremental:
            merged = merge_write_json(path, items, key)
            print(f"Merged {len(items)} {incr_label} ({len(merged)} total)")
            return merged
        write_json(path, items)
        print(f"Saved {len(items)} {full_label}")
        return items

    submissions = fetch_submissions(
        base_url, api_key, since=sub_since if do_incremental else None
    )
    merged_subs = _save(
        out_dir / "submissions.json",
        submissions,
        "id",
        incr_label="changed submissions",
        full_label="submissions",
    )

    submissions_ext = fetch_submissions_extended(
        base_url, api_key, since=sub_since if do_incremental else None
    )
    _save(
        out_dir / "_submissions.json",
        submissions_ext,
        "id",
        incr_label="changed extended subs",
        full_label="extended submissions",
    )

    # In incremental mode, skip the detail GET for submissions whose
    # dateLastActivity is unchanged since the watermark stored last run.
    known = (
        {int(k): v for k, v in state.get("submission_modified", {}).items()}
        if do_incremental
        else None
    )
    publications = fetch_all_publications(base_url, api_key, submissions, known=known)
    _save(
        out_dir / "publications.json",
        publications,
        "_submission_id",
        incr_label="changed publications",
        full_label="publications",
    )

    if files:
        # Reuse the same skip-unchanged map as publications: a submission whose
        # dateLastActivity is unchanged keeps its already-stored file records.
        submission_files = fetch_all_submission_files(
            base_url, api_key, submissions, known=known
        )
        _save(
            out_dir / "submission_files.json",
            submission_files,
            "id",
            incr_label="changed file records",
            full_label="submission file records",
        )

    # Users have no recency sort in the API, so they are always pulled in full.
    users = fetch_users(base_url, api_key)
    write_json(out_dir / "users.json", users)
    print(f"Saved {len(users)} users")

    # Tracks whether the stats block fully succeeded, so the rolling stats window
    # only advances when stats were actually written (see watermark block below).
    stats_ok = False
    if stats:
        # publication_stats are cumulative totals, so they are always pulled in
        # full and overwritten -- windowing them would corrupt the totals. The
        # rolling window applies only to the per-day views_timeline, which merges
        # safely by (submission_id, date, kind) and so refreshes recent buckets
        # without dropping history.
        timeline_since = (
            stats_since
            if stats_since is not None
            else (stats_window_start(state) if do_incremental else None)
        )
        try:
            # No date window: /stats/publications returns cumulative totals, so
            # windowing would truncate them (see the comment above). Only the
            # per-period timelines below take the rolling window.
            pub_stats = fetch_publication_stats(base_url, api_key)
            # Only published submissions appear in the stats response; reuse
            # their ids so per-submission timeline calls skip unpublished work.
            stat_submission_ids = [
                sid
                for record in pub_stats
                if (sid := (record.get("publication") or {}).get("id")) is not None
            ]
            timeline = fetch_view_timelines(
                base_url,
                api_key,
                stat_submission_ids,
                interval=stats_interval.value,
                date_start=timeline_since,
                date_end=stats_until,
            )
            # Journal-wide aggregate (the statistics-page graph). Abstract and
            # galley come from distinct endpoints, so these series differ even
            # when the per-submission endpoints collapse to identical values.
            timeline_totals = fetch_view_timeline_totals(
                base_url,
                api_key,
                interval=stats_interval.value,
                date_start=timeline_since,
                date_end=stats_until,
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code in SKIP_STATUSES:
                print(
                    f"  Skipping stats: {e.response.status_code} "
                    f"{e.response.reason_phrase} (API key may lack stats access)"
                )
            else:
                raise
        else:
            # publication_stats are cumulative totals -- always overwritten in full.
            write_json(out_dir / "publication_stats.json", pub_stats)
            print(f"Saved {len(pub_stats)} publication stat records")
            # Keys use .get() (a missing field yields a None component, not a
            # KeyError that would strand the watermark) and include `interval`, so
            # switching --stats-interval partitions day vs month series into
            # disjoint keyspaces instead of merging them into a double-counted pile.
            _save(
                out_dir / "views_timeline.json",
                timeline,
                lambda p: (
                    p.get("_submission_id"),
                    p.get("interval"),
                    p.get("date"),
                    p.get("kind"),
                ),
                incr_label="view-timeline points",
                full_label="view-timeline points",
            )
            _save(
                out_dir / "views_timeline_totals.json",
                timeline_totals,
                lambda p: (p.get("interval"), p.get("date"), p.get("kind")),
                incr_label="timeline-total points",
                full_label="timeline-total points",
            )
            stats_ok = True

    # Advance sync state only after every fetch above succeeded, so a failed run
    # never moves the watermark. `--full` rewrites (resets) it from scratch.
    if incremental or full:
        now = datetime.now().astimezone().isoformat()
        # Advance the stats window anchor only when stats were actually written;
        # otherwise carry the prior mark forward so a skipped/failed/disabled
        # stats run does not lose the days it never pulled.
        new_state = {
            "last_sync": now,
            "stats_last_sync": (
                now if (stats and stats_ok) else state.get("stats_last_sync")
            ),
            "submission_modified": build_submission_modified(merged_subs),
        }
        save_sync_state(state_path, new_state)
        tracked = len(new_state["submission_modified"])
        print(f"Updated sync state ({tracked} submissions tracked)")

    print(f"\nAll API data saved to {out_dir}/")


@api_app.command("download")
def api_download(
    submission_id: list[int] = typer.Option(
        None,
        "--submission-id",
        "-s",
        help="Limit to these submission ids (repeatable). Default: all submissions.",
    ),
    file_type: FileType = typer.Option(
        FileType.all,
        "--type",
        help="Which files to download: all, galleys (published), or review.",
    ),
    file_stage: list[int] = typer.Option(
        None,
        "--file-stage",
        help="Raw fileStage id(s) to download; overrides --type when given.",
    ),
    revisions: bool = typer.Option(
        True,
        "--revisions/--no-revisions",
        help="Also download prior revisions of each file.",
    ),
    fetch: bool = typer.Option(
        True,
        "--fetch/--no-fetch",
        help="Refresh file metadata before downloading (off: use stored JSON).",
    ),
) -> None:
    """Download submission file artifacts (PDFs, etc.) from the OJS API.

    Fetches file metadata for the targeted submissions (merging into
    `submission_files.json`), then downloads the binary artifacts into
    `OJS_FILES_DIR`, laid out as `<submission_id>/<stage>/<fileId>_<name>`. A
    manifest records every downloaded file by its immutable `fileId`, so reruns
    skip artifacts already on disk -- new uploads and revisions are picked up
    incrementally.
    """
    from ojs.api.client import fetch_all_submission_files
    from ojs.api.files import STAGE_GROUPS, download_files, manifest_key

    base_url = os.environ.get("OJS_BASE_URL")
    api_key = os.environ.get("OJS_API_KEY")
    if not base_url or not api_key:
        print("Error: OJS_BASE_URL and OJS_API_KEY must be set (run `ojs init`).")
        raise typer.Exit(1)

    api_dir = _api_dir()
    files_json = api_dir / "submission_files.json"
    stages = file_stage if file_stage else STAGE_GROUPS[file_type.value]

    # Resolve the target submissions. Explicit ids are fetched as given; with no
    # ids we operate over every submission recorded by `api fetch`.
    if submission_id:
        target_ids = set(submission_id)
        target_subs: list[dict[str, Any]] = [
            {"id": sid, "dateLastActivity": None} for sid in submission_id
        ]
    else:
        subs_path = api_dir / "submissions.json"
        if not subs_path.exists():
            print(f"Error: {subs_path} not found. Run 'ojs api fetch' first.")
            raise typer.Exit(1)
        target_subs = json.loads(subs_path.read_text())
        target_ids = {s["id"] for s in target_subs}

    # Choose the candidate file records. With --fetch we pull current metadata
    # for the target submissions (already scoped to them, and to `stages`
    # server-side) and merge it to disk; otherwise we reuse the stored dump,
    # narrowed to the target submissions.
    if fetch:
        candidates = fetch_all_submission_files(
            base_url, api_key, target_subs, file_stages=stages
        )
        merge_write_json(files_json, candidates, "id")
    else:
        if not files_json.exists():
            print(f"Error: {files_json} not found. Run with --fetch first.")
            raise typer.Exit(1)
        stored = json.loads(files_json.read_text())
        # Distinguish "no files for this id" from "this id was never fetched":
        # an explicit -s id absent from the stored dump has no metadata to use.
        if submission_id:
            known_ids = {f.get("_submission_id") for f in stored}
            missing = target_ids - known_ids
            if missing == target_ids:
                print(
                    f"Error: no stored file metadata for submission id(s) "
                    f"{sorted(target_ids)} in {files_json}. "
                    "Run with --fetch to retrieve it first."
                )
                raise typer.Exit(1)
            if missing:
                print(
                    f"Warning: no stored file metadata for submission id(s) "
                    f"{sorted(missing)}; run with --fetch to include them. "
                    "Processing the rest."
                )
        candidates = [f for f in stored if f.get("_submission_id") in target_ids]

    # Apply the stage filter -- a no-op on the fetch path (the server already
    # filtered), but needed when reusing the stored dump.
    stage_set = set(stages) if stages else None
    to_download = [
        f for f in candidates if stage_set is None or f.get("fileStage") in stage_set
    ]
    print(f"{len(to_download)} file records selected for download")

    files_dir = _files_dir()
    files_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = files_dir / "manifest.json"
    manifest: list[dict[str, Any]] = (
        json.loads(manifest_path.read_text()) if manifest_path.exists() else []
    )
    downloaded = {manifest_key(r): r for r in manifest}

    def _persist(record: dict[str, Any]) -> None:
        # Flush each record as soon as its bytes hit disk. Merging against the
        # in-memory manifest (the same snapshot that built the skip map above)
        # keeps the write consistent with the skip decisions; an atomic write
        # means an interrupted run leaves a complete manifest of what landed.
        # This re-serializes the whole manifest per file (the durability cost of
        # per-file flushing), which is dwarfed by the network transfer of the file
        # itself; if batches ever grow large enough for that to matter, flush every
        # K records instead of every one.
        nonlocal manifest
        manifest = upsert_json_list(manifest, [record], manifest_key)
        write_json(manifest_path, manifest)

    new_records, failed_records = download_files(
        to_download,
        api_key=api_key,
        dest_dir=files_dir,
        downloaded=downloaded,
        include_revisions=revisions,
        on_record=_persist,
    )

    # Persist the files the API would not serve (403/404), keyed by fileId like
    # the manifest, so the skip history survives the run instead of scrolling by.
    if failed_records:
        skipped_path = files_dir / "skipped.json"
        prior_skips = (
            json.loads(skipped_path.read_text()) if skipped_path.exists() else []
        )
        merged_skips = upsert_json_list(prior_skips, failed_records, manifest_key)
        write_json(skipped_path, merged_skips)
        print(f"Logged {len(failed_records)} skipped files to {skipped_path}")

    print(f"\nDownloaded {len(new_records)} new files to {files_dir}/")


@api_app.command("norm")
def api_norm() -> None:
    """Normalize API JSON data into relational tables."""
    from ojs.api.normalize import normalize_api

    api_dir = _api_dir()
    files = {
        "submissions": api_dir / "submissions.json",
        "publications": api_dir / "publications.json",
        "_submissions": api_dir / "_submissions.json",
        "users": api_dir / "users.json",
    }

    for path in files.values():
        if not path.exists():
            print(f"Error: {path} not found. Run 'ojs api fetch' first.")
            raise typer.Exit(1)

    submissions = json.loads(files["submissions"].read_text())
    publications = json.loads(files["publications"].read_text())
    submissions_ext = json.loads(files["_submissions"].read_text())
    users = json.loads(files["users"].read_text())

    def _load_optional(path: Path) -> list[dict[str, Any]] | None:
        return json.loads(path.read_text()) if path.exists() else None

    publication_stats = _load_optional(api_dir / "publication_stats.json")
    views_timeline = _load_optional(api_dir / "views_timeline.json")
    views_timeline_totals = _load_optional(api_dir / "views_timeline_totals.json")
    submission_files = _load_optional(api_dir / "submission_files.json")

    normalize_api(
        submissions,
        publications,
        submissions_ext,
        users,
        api_dir / "normalized",
        publication_stats=publication_stats,
        views_timeline=views_timeline,
        views_timeline_totals=views_timeline_totals,
        submission_files=submission_files,
    )


@api_app.command("schema")
def api_schema() -> None:
    """Generate schema documentation for the normalized API tables."""
    from ojs.api.schemas import API_TABLES

    out_dir = _api_dir() / "normalized"
    out_dir.mkdir(parents=True, exist_ok=True)
    print("Creating schema documentation for API tables...")
    write_schema_docs(API_TABLES, out_dir / "table_schemas.csv")
