"""OJS CLI - normalize Open Journal Systems data exports.

A thin shell over the importable entry points in :mod:`ojs.api.run` and
:mod:`ojs.website.run`: each command declares its options, calls the matching
``run_*`` function, and translates an :class:`~ojs.errors.OjsError` into the exit
code and message the command has always produced. The pipelines themselves live
in the library, so they can be driven in-process without this module.
"""

import logging
import os
import sys
from enum import StrEnum
from pathlib import Path

import typer
from dotenv import load_dotenv

from ojs import paths
from ojs.errors import OjsError, OptionError

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


class _StdoutProxy:
    """A write target that resolves ``sys.stdout`` on every call.

    ``logging.StreamHandler`` binds its stream once, at construction. Writing
    through this proxy instead keeps command output visible to anything that
    swaps the stream after this module is imported: Typer's ``CliRunner``,
    ``contextlib.redirect_stdout``, a capturing test harness.
    """

    def write(self, text: str) -> int:
        return sys.stdout.write(text)

    def flush(self) -> None:
        sys.stdout.flush()


_logging_configured = False


def configure_logging() -> None:
    """Route the library's progress logs to stdout, unadorned.

    Command output is the library's log records printed verbatim (no level name,
    no timestamp), which is why messages that need to read as warnings carry
    their own ``WARNING`` prefix. Called at import so the console script prints;
    a library caller never runs it and stays quiet.
    """
    global _logging_configured
    if _logging_configured:
        return
    logger = logging.getLogger("ojs")
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.StreamHandler(_StdoutProxy()))
    _logging_configured = True


configure_logging()

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


def _fail(error: OjsError) -> typer.Exit | typer.BadParameter:
    """Turn a library error into the CLI failure it has always been reported as.

    An :class:`~ojs.errors.OptionError` is a usage problem, so it becomes click's
    ``BadParameter`` (usage message, exit 2); everything else prints
    ``Error: <message>`` and exits 1.
    """
    if isinstance(error, OptionError):
        return typer.BadParameter(str(error))
    print(f"Error: {error}")
    return typer.Exit(1)


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

    # Website report-fetch config (optional; only the `reviews`/`articles fetch`
    # commands need it). Scaffolded from the environment when set, else left blank
    # for the user to fill in -- not prompted, so `init` stays non-interactive-safe
    # (an empty-stdin prompt would abort). The report URLs are instance-specific.
    username = os.environ.get("OJS_USERNAME", "")
    password = os.environ.get("OJS_PASSWORD", "")
    reviews_report_url = os.environ.get("OJS_REVIEWS_REPORT_URL", "")
    articles_report_url = os.environ.get("OJS_ARTICLES_REPORT_URL", "")

    lines = [
        f"OJS_BASE_URL={_env_quote(base_url)}",
        f"OJS_API_KEY={_env_quote(api_key)}",
        f"OJS_USERNAME={_env_quote(username)}",
        f"OJS_PASSWORD={_env_quote(password)}",
        f"OJS_REVIEWS_REPORT_URL={_env_quote(reviews_report_url)}",
        f"OJS_ARTICLES_REPORT_URL={_env_quote(articles_report_url)}",
        "",
    ]
    env_path.write_text("\n".join(lines))
    # The file holds secrets (OJS_API_KEY, OJS_PASSWORD); keep it owner-only
    # regardless of umask so it is not world-readable on a shared host. chmod (not
    # a 0600 open) also re-tightens an existing .env when regenerating with
    # --force. (No-op on Windows, where POSIX mode bits don't apply -- rely on
    # directory/ACL access control there.)
    env_path.chmod(0o600)
    print(f"Wrote {env_path}")


@articles_app.command("schema")
def articles_schema() -> None:
    """Generate schema documentation for article tables."""
    from ojs.schema import write_schema_docs
    from ojs.website.articles.schemas import ARTICLE_TABLES

    out_dir = paths.articles_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    print("Creating schema documentation for article tables...")
    write_schema_docs(ARTICLE_TABLES, out_dir / "table_schemas.csv")


@articles_app.command("norm")
def articles_norm() -> None:
    """Normalize the most recent articles CSV export into relational tables."""
    from ojs.website import run

    try:
        run.run_norm("articles")
    except OjsError as e:
        raise _fail(e) from e


@articles_app.command("fetch")
def articles_fetch() -> None:
    """Download the latest Articles Report CSV from the OJS website."""
    from ojs.website import run

    try:
        run.run_report_fetch("articles")
    except OjsError as e:
        raise _fail(e) from e


@reviews_app.command("schema")
def reviews_schema() -> None:
    """Generate schema documentation for review tables."""
    from ojs.schema import write_schema_docs
    from ojs.website.reviews.schemas import REVIEW_TABLES

    out_dir = paths.reviews_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    print("Creating schema documentation for review tables...")
    write_schema_docs(REVIEW_TABLES, out_dir / "table_schemas.csv")


@reviews_app.command("norm")
def reviews_norm() -> None:
    """Normalize the most recent reviews CSV export."""
    from ojs.website import run

    try:
        run.run_norm("reviews")
    except OjsError as e:
        raise _fail(e) from e


@reviews_app.command("fetch")
def reviews_fetch() -> None:
    """Download the latest Review Report CSV from the OJS website."""
    from ojs.website import run

    try:
        run.run_report_fetch("reviews")
    except OjsError as e:
        raise _fail(e) from e


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
    from ojs.api import run

    try:
        run.run_fetch(
            stats=stats,
            files=files,
            stats_interval=stats_interval.value,
            stats_since=stats_since,
            stats_until=stats_until,
            incremental=incremental,
            since=since,
            full=full,
        )
    except OjsError as e:
        raise _fail(e) from e


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
    from ojs.api import run

    try:
        run.run_download(
            submission_ids=submission_id,
            file_type=file_type.value,
            file_stages=file_stage,
            revisions=revisions,
            fetch=fetch,
        )
    except OjsError as e:
        raise _fail(e) from e


@api_app.command("norm")
def api_norm() -> None:
    """Normalize API JSON data into relational tables."""
    from ojs.api import run

    try:
        run.run_norm()
    except OjsError as e:
        raise _fail(e) from e


@api_app.command("schema")
def api_schema() -> None:
    """Generate schema documentation for the normalized API tables."""
    from ojs.api.schemas import API_TABLES
    from ojs.schema import write_schema_docs

    out_dir = paths.api_dir() / "normalized"
    out_dir.mkdir(parents=True, exist_ok=True)
    print("Creating schema documentation for API tables...")
    write_schema_docs(API_TABLES, out_dir / "table_schemas.csv")
