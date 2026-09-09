"""Importable entry points for the OJS API pipelines.

:mod:`ojs.cli` is a thin shell over this module: each ``ojs api ...`` command
declares its options, calls the matching ``run_*`` function, and translates an
:class:`~ojs.errors.OjsError` into an exit code. An embedding caller drives the
same pipelines in-process and gets a result object and exceptions instead of
printed lines and a process exit.

Every entry point takes keyword-only arguments that default to ``None``, meaning
"read the environment" -- the same defaults the CLI has always used. Progress
goes to the ``ojs`` logger rather than stdout, so a library caller's console is
quiet unless it attaches a handler.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx

from ojs import paths
from ojs.api import client
from ojs.api import files as api_files
from ojs.api.normalize import normalize_api
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
from ojs.errors import ConfigError, MissingDataError, OptionError

__all__ = [
    "DatasetCount",
    "DownloadResult",
    "FetchResult",
    "NormResult",
    "run_download",
    "run_fetch",
    "run_norm",
]

logger = logging.getLogger(__name__)

# The JSON dumps `api norm` requires; a missing one means `api fetch` never ran.
REQUIRED_NORM_FILES = ("submissions", "publications", "_submissions", "users")

# The JSON dumps `api norm` folds in when present. Each name is both the dump's
# filename stem and the `normalize_api` keyword argument it feeds, so `run_norm`
# loads them by iterating this tuple -- adding a dump here is the whole change.
OPTIONAL_NORM_FILES = (
    "publication_stats",
    "views_timeline",
    "views_timeline_totals",
    "submission_files",
)

# Timeline granularities the OJS stats endpoints accept. The CLI constrains
# `--stats-interval` with its own enum; this is the library-side guard, so a
# direct caller gets an OptionError instead of an HTTP error from the server.
STATS_INTERVALS = ("day", "month")


@dataclass(frozen=True)
class DatasetCount:
    """How many records a fetch pulled, and how many the dump holds afterwards.

    On a full pull the two are equal (the dump is overwritten). On an incremental
    pull ``fetched`` is the changed slice and ``total`` is the merged dump.
    """

    fetched: int
    total: int


@dataclass(frozen=True)
class FetchResult:
    """What a :func:`run_fetch` run pulled and where it landed."""

    out_dir: Path
    incremental: bool
    submissions: DatasetCount
    submissions_extended: DatasetCount
    publications: DatasetCount
    users: DatasetCount
    submission_files: DatasetCount | None = None
    publication_stats: DatasetCount | None = None
    views_timeline: DatasetCount | None = None
    views_timeline_totals: DatasetCount | None = None
    # False when stats were disabled, or when the API refused them (403/404) and
    # the run carried the prior stats watermark forward instead of advancing it.
    stats_ok: bool = False
    sync_state_written: bool = False
    tracked_submissions: int | None = None


@dataclass(frozen=True)
class DownloadResult:
    """What a :func:`run_download` run selected, fetched, and could not fetch."""

    files_dir: Path
    selected: int
    downloaded: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class NormResult:
    """Where :func:`run_norm` wrote, and the row count of each table."""

    out_dir: Path
    rows: dict[str, int]

    @property
    def tables(self) -> list[str]:
        """The names of the tables written, in output order."""
        return list(self.rows)


def _require_credentials(base_url: str | None, api_key: str | None) -> tuple[str, str]:
    """Resolve the API credentials from the arguments, then the environment."""
    base_url = base_url or os.environ.get("OJS_BASE_URL")
    api_key = api_key or os.environ.get("OJS_API_KEY")
    if not base_url or not api_key:
        raise ConfigError("OJS_BASE_URL and OJS_API_KEY must be set (run `ojs init`).")
    return base_url, api_key


def run_fetch(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    out_dir: Path | str | None = None,
    stats: bool = True,
    files: bool = False,
    stats_interval: str = "day",
    stats_since: str | None = None,
    stats_until: str | None = None,
    incremental: bool = False,
    since: str | None = None,
    full: bool = False,
) -> FetchResult:
    """Fetch data from the OJS API and save raw JSON.

    Default is a full pull. ``incremental`` (or ``since``) fetches only changed
    records and merges them into the existing JSON dumps; ``full`` forces a
    complete pull and resets the sync state.

    Raises:
        ConfigError: neither the arguments nor the environment supply
            ``base_url``/``api_key``.
        OptionError: ``full`` combined with ``incremental``/``since``, a date
            argument that is not ``YYYY-MM-DD``, or a ``stats_interval`` outside
            :data:`STATS_INTERVALS`.
    """
    resolved_base_url, resolved_api_key = _require_credentials(base_url, api_key)

    if full and (incremental or since):
        raise OptionError("--full cannot be combined with --incremental/--since")
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
                raise OptionError(f"{flag} must be YYYY-MM-DD") from e

    # The CLI's enum rejects a bad interval before the call, so this guard only
    # ever fires for a direct caller -- who gets a typed error here instead of
    # the API's rejection surfacing as a raw HTTP error mid-fetch.
    if stats and stats_interval not in STATS_INTERVALS:
        choices = ", ".join(STATS_INTERVALS)
        raise OptionError(
            f"unknown stats interval {stats_interval!r}; expected one of: {choices}"
        )

    api_out_dir = paths.api_dir(out_dir)
    api_out_dir.mkdir(parents=True, exist_ok=True)
    state_path = api_out_dir / SYNC_STATE_FILENAME

    # Resolve the incremental watermark: `since` overrides the stored mark;
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
        logger.info(
            "No prior sync watermark; doing a full pull to establish a baseline."
        )

    def _save(
        path: Path,
        items: list[dict[str, Any]],
        key: str | KeyFn,
        *,
        incr_label: str,
        full_label: str,
    ) -> tuple[list[dict[str, Any]], DatasetCount]:
        """Merge (incremental) or overwrite (full) `items` at `path`, and report."""
        if do_incremental:
            merged = merge_write_json(path, items, key)
            logger.info(f"Merged {len(items)} {incr_label} ({len(merged)} total)")
            return merged, DatasetCount(len(items), len(merged))
        write_json(path, items)
        logger.info(f"Saved {len(items)} {full_label}")
        return items, DatasetCount(len(items), len(items))

    submissions = client.fetch_submissions(
        resolved_base_url, resolved_api_key, since=sub_since if do_incremental else None
    )
    merged_subs, submissions_count = _save(
        api_out_dir / "submissions.json",
        submissions,
        "id",
        incr_label="changed submissions",
        full_label="submissions",
    )

    submissions_ext = client.fetch_submissions_extended(
        resolved_base_url, resolved_api_key, since=sub_since if do_incremental else None
    )
    _, submissions_ext_count = _save(
        api_out_dir / "_submissions.json",
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
    publications = client.fetch_all_publications(
        resolved_base_url, resolved_api_key, submissions, known=known
    )
    _, publications_count = _save(
        api_out_dir / "publications.json",
        publications,
        "_submission_id",
        incr_label="changed publications",
        full_label="publications",
    )

    files_count: DatasetCount | None = None
    if files:
        # Reuse the same skip-unchanged map as publications: a submission whose
        # dateLastActivity is unchanged keeps its already-stored file records.
        submission_files = client.fetch_all_submission_files(
            resolved_base_url, resolved_api_key, submissions, known=known
        )
        _, files_count = _save(
            api_out_dir / "submission_files.json",
            submission_files,
            "id",
            incr_label="changed file records",
            full_label="submission file records",
        )

    # Users have no recency sort in the API, so they are always pulled in full.
    users = client.fetch_users(resolved_base_url, resolved_api_key)
    write_json(api_out_dir / "users.json", users)
    logger.info(f"Saved {len(users)} users")

    # Tracks whether the stats block fully succeeded, so the rolling stats window
    # only advances when stats were actually written (see watermark block below).
    stats_ok = False
    pub_stats_count: DatasetCount | None = None
    timeline_count: DatasetCount | None = None
    totals_count: DatasetCount | None = None
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
            pub_stats = client.fetch_publication_stats(
                resolved_base_url, resolved_api_key
            )
            # Only published submissions appear in the stats response; reuse
            # their ids so per-submission timeline calls skip unpublished work.
            stat_submission_ids = [
                sid
                for record in pub_stats
                if (sid := (record.get("publication") or {}).get("id")) is not None
            ]
            timeline = client.fetch_view_timelines(
                resolved_base_url,
                resolved_api_key,
                stat_submission_ids,
                interval=stats_interval,
                date_start=timeline_since,
                date_end=stats_until,
            )
            # Journal-wide aggregate (the statistics-page graph). Abstract and
            # galley come from distinct endpoints, so these series differ even
            # when the per-submission endpoints collapse to identical values.
            timeline_totals = client.fetch_view_timeline_totals(
                resolved_base_url,
                resolved_api_key,
                interval=stats_interval,
                date_start=timeline_since,
                date_end=stats_until,
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code in client.SKIP_STATUSES:
                logger.info(
                    f"  Skipping stats: {e.response.status_code} "
                    f"{e.response.reason_phrase} (API key may lack stats access)"
                )
            else:
                raise
        else:
            # publication_stats are cumulative totals -- always overwritten in full.
            write_json(api_out_dir / "publication_stats.json", pub_stats)
            logger.info(f"Saved {len(pub_stats)} publication stat records")
            pub_stats_count = DatasetCount(len(pub_stats), len(pub_stats))
            # Keys use .get() (a missing field yields a None component, not a
            # KeyError that would strand the watermark) and include `interval`, so
            # switching the stats interval partitions day vs month series into
            # disjoint keyspaces instead of merging them into a double-counted pile.
            _, timeline_count = _save(
                api_out_dir / "views_timeline.json",
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
            _, totals_count = _save(
                api_out_dir / "views_timeline_totals.json",
                timeline_totals,
                lambda p: (p.get("interval"), p.get("date"), p.get("kind")),
                incr_label="timeline-total points",
                full_label="timeline-total points",
            )
            stats_ok = True

    # Advance sync state only after every fetch above succeeded, so a failed run
    # never moves the watermark. `full` rewrites (resets) it from scratch.
    tracked: int | None = None
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
        logger.info(f"Updated sync state ({tracked} submissions tracked)")

    logger.info(f"\nAll API data saved to {api_out_dir}/")

    return FetchResult(
        out_dir=api_out_dir,
        incremental=do_incremental,
        submissions=submissions_count,
        submissions_extended=submissions_ext_count,
        publications=publications_count,
        users=DatasetCount(len(users), len(users)),
        submission_files=files_count,
        publication_stats=pub_stats_count,
        views_timeline=timeline_count,
        views_timeline_totals=totals_count,
        stats_ok=stats_ok,
        sync_state_written=incremental or full,
        tracked_submissions=tracked,
    )


def run_download(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    api_dir: Path | str | None = None,
    dest_dir: Path | str | None = None,
    submission_ids: list[int] | None = None,
    file_type: str = "all",
    file_stages: list[int] | None = None,
    revisions: bool = True,
    fetch: bool = True,
) -> DownloadResult:
    """Download submission file artifacts (PDFs, etc.) from the OJS API.

    Fetches file metadata for the targeted submissions (merging into
    ``submission_files.json``), then downloads the binary artifacts into
    ``dest_dir``, laid out as ``<submission_id>/<stage>/<fileId>_<name>``. A
    manifest records every downloaded file by its immutable ``fileId``, so reruns
    skip artifacts already on disk -- new uploads and revisions are picked up
    incrementally.

    ``api_dir`` is the JSON dump directory this reads (the same one
    :func:`run_fetch` writes and :func:`run_norm` reads); ``dest_dir`` is where
    the artifacts land. The two are named for what they hold rather than both
    being ``out_dir``, so redirecting the downloads is unambiguous.

    Raises:
        ConfigError: neither the arguments nor the environment supply
            ``base_url``/``api_key``.
        OptionError: ``file_type`` is not one of the known stage groups.
        MissingDataError: a JSON dump the run needs is not on disk.
    """
    resolved_base_url, resolved_api_key = _require_credentials(base_url, api_key)

    if file_stages:
        stages = file_stages
    elif file_type in api_files.STAGE_GROUPS:
        stages = api_files.STAGE_GROUPS[file_type]
    else:
        choices = ", ".join(sorted(api_files.STAGE_GROUPS))
        raise OptionError(
            f"unknown file type {file_type!r}; expected one of: {choices}"
        )

    api_out_dir = paths.api_dir(api_dir)
    files_json = api_out_dir / "submission_files.json"

    # Resolve the target submissions. Explicit ids are fetched as given; with no
    # ids we operate over every submission recorded by `api fetch`.
    if submission_ids:
        target_ids = set(submission_ids)
        target_subs: list[dict[str, Any]] = [
            {"id": sid, "dateLastActivity": None} for sid in submission_ids
        ]
    else:
        subs_path = api_out_dir / "submissions.json"
        if not subs_path.exists():
            raise MissingDataError(f"{subs_path} not found. Run 'ojs api fetch' first.")
        target_subs = json.loads(subs_path.read_text())
        target_ids = {s["id"] for s in target_subs}

    # Choose the candidate file records. With `fetch` we pull current metadata
    # for the target submissions (already scoped to them, and to `stages`
    # server-side) and merge it to disk; otherwise we reuse the stored dump,
    # narrowed to the target submissions.
    if fetch:
        candidates = client.fetch_all_submission_files(
            resolved_base_url, resolved_api_key, target_subs, file_stages=stages
        )
        merge_write_json(files_json, candidates, "id")
    else:
        if not files_json.exists():
            raise MissingDataError(f"{files_json} not found. Run with --fetch first.")
        stored = json.loads(files_json.read_text())
        # Distinguish "no files for this id" from "this id was never fetched":
        # an explicit id absent from the stored dump has no metadata to use.
        if submission_ids:
            known_ids = {f.get("_submission_id") for f in stored}
            missing = target_ids - known_ids
            if missing == target_ids:
                raise MissingDataError(
                    f"no stored file metadata for submission id(s) "
                    f"{sorted(target_ids)} in {files_json}. "
                    "Run with --fetch to retrieve it first."
                )
            if missing:
                logger.warning(
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
    logger.info(f"{len(to_download)} file records selected for download")

    artifacts_dir = paths.files_dir(dest_dir)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = artifacts_dir / "manifest.json"
    manifest: list[dict[str, Any]] = (
        json.loads(manifest_path.read_text()) if manifest_path.exists() else []
    )
    downloaded = {api_files.manifest_key(r): r for r in manifest}

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
        manifest = upsert_json_list(manifest, [record], api_files.manifest_key)
        write_json(manifest_path, manifest)

    new_records, failed_records = api_files.download_files(
        to_download,
        api_key=resolved_api_key,
        dest_dir=artifacts_dir,
        downloaded=downloaded,
        include_revisions=revisions,
        on_record=_persist,
    )

    # Persist the files the API would not serve (403/404), keyed by fileId like
    # the manifest, so the skip history survives the run instead of scrolling by.
    if failed_records:
        skipped_path = artifacts_dir / "skipped.json"
        prior_skips = (
            json.loads(skipped_path.read_text()) if skipped_path.exists() else []
        )
        merged_skips = upsert_json_list(
            prior_skips, failed_records, api_files.manifest_key
        )
        write_json(skipped_path, merged_skips)
        logger.info(f"Logged {len(failed_records)} skipped files to {skipped_path}")

    logger.info(f"\nDownloaded {len(new_records)} new files to {artifacts_dir}/")

    return DownloadResult(
        files_dir=artifacts_dir,
        selected=len(to_download),
        downloaded=new_records,
        failed=failed_records,
    )


def run_norm(
    *,
    api_dir: Path | str | None = None,
    out_dir: Path | str | None = None,
) -> NormResult:
    """Normalize the raw API JSON dumps into relational tables.

    Reads the dumps written by :func:`run_fetch` from ``api_dir`` and writes one
    CSV per table into ``out_dir`` (default: ``<api_dir>/normalized``).

    Raises:
        MissingDataError: naming the first required dump that is absent.
    """
    source_dir = paths.api_dir(api_dir)
    output_dir = Path(out_dir) if out_dir is not None else source_dir / "normalized"

    required: dict[str, list[dict[str, Any]]] = {}
    for name in REQUIRED_NORM_FILES:
        path = source_dir / f"{name}.json"
        if not path.exists():
            raise MissingDataError(f"{path} not found. Run 'ojs api fetch' first.")
        required[name] = json.loads(path.read_text())

    def _load_optional(name: str) -> list[dict[str, Any]] | None:
        path = source_dir / f"{name}.json"
        return json.loads(path.read_text()) if path.exists() else None

    optional = {name: _load_optional(name) for name in OPTIONAL_NORM_FILES}

    tables = normalize_api(
        required["submissions"],
        required["publications"],
        required["_submissions"],
        required["users"],
        output_dir,
        **optional,
    )
    return NormResult(
        out_dir=output_dir, rows={name: df.height for name, df in tables.items()}
    )
