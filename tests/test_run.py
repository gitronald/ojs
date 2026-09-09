"""Tests for the importable run entry points, path resolution, and logging.

The CLI tests in ``test_ojs.py`` / ``test_reports.py`` stay the compatibility
check -- they exercise the same pipelines through ``CliRunner`` and must keep
passing untouched. These call the library directly, which is what the extraction
made possible.
"""

import json
import logging
from pathlib import Path

import pytest

from ojs import paths
from ojs.api import run as api_run
from ojs.errors import ConfigError, MissingDataError, OptionError
from ojs.website import run as web_run

# --- Path resolution ----------------------------------------------------------

# (resolver, env var, default relative to the other defaults)
PATH_CASES = [
    (paths.downloads_dir, "OJS_DOWNLOADS_DIR", "data/ojs-website"),
    (paths.articles_dir, "OJS_ARTICLES_DIR", "data/ojs-website/articles"),
    (paths.reviews_dir, "OJS_REVIEWS_DIR", "data/ojs-website/reviews"),
    (paths.api_dir, "OJS_API_DIR", "data/ojs-api"),
    (paths.files_dir, "OJS_FILES_DIR", "data/ojs-api/files"),
]

PATH_ENV_VARS = [var for _, var, _ in PATH_CASES]


@pytest.fixture
def clean_path_env(monkeypatch):
    """Unset every directory env var so the built-in defaults are observable."""
    for var in PATH_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.mark.parametrize(("resolve", "env_var", "default"), PATH_CASES)
def test_path_default(clean_path_env, resolve, env_var, default):
    assert resolve() == Path(default)


@pytest.mark.parametrize(("resolve", "env_var", "default"), PATH_CASES)
def test_path_env_override(clean_path_env, monkeypatch, resolve, env_var, default):
    monkeypatch.setenv(env_var, "/from/env")
    assert resolve() == Path("/from/env")


@pytest.mark.parametrize(("resolve", "env_var", "default"), PATH_CASES)
def test_path_explicit_argument_wins(
    clean_path_env, monkeypatch, resolve, env_var, default
):
    # An explicit argument beats the environment, so an embedding caller can
    # place output without mutating os.environ.
    monkeypatch.setenv(env_var, "/from/env")
    assert resolve("/explicit") == Path("/explicit")


def test_nested_defaults_follow_their_parent(clean_path_env, monkeypatch):
    # articles/reviews default *under* OJS_DOWNLOADS_DIR and files under
    # OJS_API_DIR, so moving the parent moves them too.
    monkeypatch.setenv("OJS_DOWNLOADS_DIR", "/w")
    monkeypatch.setenv("OJS_API_DIR", "/a")
    assert paths.articles_dir() == Path("/w/articles")
    assert paths.reviews_dir() == Path("/w/reviews")
    assert paths.files_dir() == Path("/a/files")


# --- Library logging ----------------------------------------------------------


def test_library_is_quiet_without_a_handler(tmp_path, capsys):
    """A library caller gets no console output unless it asks for one.

    Importing ``ojs.cli`` (as most tests do) attaches a stdout handler to the
    ``ojs`` logger, so the handlers are swapped out here for the NullHandler the
    package installs on its own.
    """
    from ojs.schema import write_schema_docs
    from ojs.website.reviews.schemas import REVIEW_TABLES

    ojs_logger = logging.getLogger("ojs")
    child = logging.getLogger("ojs.schema")
    child_enabled_before = child.isEnabledFor(logging.INFO)
    saved_handlers, saved_level = ojs_logger.handlers[:], ojs_logger.level
    ojs_logger.handlers = [logging.NullHandler()]
    ojs_logger.setLevel(logging.NOTSET)
    try:
        write_schema_docs(REVIEW_TABLES, tmp_path / "table_schemas.csv")
    finally:
        ojs_logger.handlers = saved_handlers
        # setLevel, not `.level = ...`: only setLevel clears the per-logger
        # effective-level cache. A bare assignment leaves every `ojs.*` child
        # logger holding the `isEnabledFor(INFO) -> False` answer it cached
        # while this test had the parent at NOTSET, which would silently
        # swallow INFO records in any later test in the session.
        ojs_logger.setLevel(saved_level)

    assert capsys.readouterr().out == ""
    assert (tmp_path / "table_schemas.csv").exists()  # the work still happened
    # Probing the library must leave the rest of the session where it found it:
    # the child logger answers about INFO exactly as it did before.
    assert child.isEnabledFor(logging.INFO) == child_enabled_before


# --- api run_norm -------------------------------------------------------------


def _write_api_dumps(api_dir: Path, *, optional: bool = True) -> None:
    api_dir.mkdir(parents=True, exist_ok=True)
    dumps: dict[str, list[dict]] = {
        "submissions": [
            {
                "id": 1,
                "statusLabel": "Published",
                "publications": [{"sectionId": 1, "fullTitle": {"en_US": "T"}}],
            }
        ],
        "publications": [
            {
                "_submission_id": 1,
                "id": 10,
                "status": 3,
                "galleys": [],
                "keywords": None,
                "abstract": None,
                "authors": [{"familyName": {"en_US": "Doe"}, "seq": 1}],
            }
        ],
        "_submissions": [{"id": 1, "reviewAssignments": [{"id": 5, "round": 1}]}],
        "users": [{"id": 100, "email": "a@example.org"}],
    }
    if optional:
        dumps["publication_stats"] = [
            {"abstractViews": 5, "publication": {"id": 1}},
        ]
    for name, payload in dumps.items():
        (api_dir / f"{name}.json").write_text(json.dumps(payload))


def test_api_run_norm_returns_tables_and_row_counts(tmp_path):
    import polars as pl

    api_dir = tmp_path / "api"
    _write_api_dumps(api_dir)

    result = api_run.run_norm(api_dir=api_dir)

    assert result.out_dir == api_dir / "normalized"
    # The four required tables, plus publication_stats because its dump exists.
    assert result.tables == [
        "submissions",
        "publications",
        "authors",
        "review_assignments",
        "publication_stats",
    ]
    assert result.rows["submissions"] == 1
    assert result.rows["authors"] == 1
    # Row counts describe what was written, so they match the CSVs on disk.
    for name, rows in result.rows.items():
        assert pl.read_csv(result.out_dir / f"{name}.csv").height == rows


def test_api_run_norm_omits_absent_optional_tables(tmp_path):
    api_dir = tmp_path / "api"
    _write_api_dumps(api_dir, optional=False)
    result = api_run.run_norm(api_dir=api_dir)
    assert "publication_stats" not in result.tables


def test_api_run_norm_honors_explicit_out_dir(tmp_path):
    api_dir = tmp_path / "api"
    _write_api_dumps(api_dir)
    out = tmp_path / "elsewhere"
    result = api_run.run_norm(api_dir=api_dir, out_dir=out)
    assert result.out_dir == out
    assert (out / "submissions.csv").exists()


def test_api_run_norm_names_the_first_missing_input(tmp_path):
    api_dir = tmp_path / "api"
    _write_api_dumps(api_dir)
    (api_dir / "publications.json").unlink()

    with pytest.raises(MissingDataError) as exc:
        api_run.run_norm(api_dir=api_dir)
    # The message names the missing file, not just "something is missing".
    assert "publications.json" in str(exc.value)
    assert "ojs api fetch" in str(exc.value)


# --- api run_fetch ------------------------------------------------------------


@pytest.fixture
def fake_client(monkeypatch):
    """Stub the API client with a mutable in-memory 'server'."""
    from ojs.api import client as client_mod

    server: dict[int, dict] = {
        1: {
            "id": 1,
            "dateLastActivity": "2024-01-01 00:00:00",
            "publications": [{"id": 11}],
        },
        2: {
            "id": 2,
            "dateLastActivity": "2024-06-01 00:00:00",
            "publications": [{"id": 22}],
        },
    }

    def changed(since):
        subs = list(server.values())
        if since is not None:  # emulate the early-stop DESC filtering
            subs = [s for s in subs if s["dateLastActivity"] > since]
        return subs

    monkeypatch.setattr(
        client_mod, "fetch_submissions", lambda *a, since=None, **k: changed(since)
    )
    monkeypatch.setattr(
        client_mod,
        "fetch_submissions_extended",
        lambda *a, since=None, **k: [
            {"id": s["id"], "reviewAssignments": []} for s in changed(since)
        ],
    )
    monkeypatch.setattr(
        client_mod,
        "fetch_all_publications",
        lambda base, key, submissions, **k: [
            {"id": s["publications"][0]["id"], "_submission_id": s["id"]}
            for s in submissions
        ],
    )
    monkeypatch.setattr(
        client_mod, "fetch_users", lambda *a, **k: [{"id": 100, "email": "a@x.org"}]
    )
    return server


@pytest.fixture
def fake_stats(monkeypatch):
    """Stub the three stats endpoints with one published submission's series."""
    from ojs.api import client as client_mod

    monkeypatch.setattr(
        client_mod,
        "fetch_publication_stats",
        lambda *a, **k: [{"abstractViews": 7, "publication": {"id": 11}}],
    )
    monkeypatch.setattr(
        client_mod,
        "fetch_view_timelines",
        lambda *a, interval="day", **k: [
            {"_submission_id": 11, "interval": interval, "date": d, "kind": "abstract"}
            for d in ("2024-01-01", "2024-01-02")
        ],
    )
    monkeypatch.setattr(
        client_mod,
        "fetch_view_timeline_totals",
        lambda *a, interval="day", **k: [
            {"interval": interval, "date": "2024-01-01", "kind": "abstract"}
        ],
    )


def test_run_fetch_stats_success_populates_counts_and_dumps(
    tmp_path, fake_client, fake_stats
):
    result = api_run.run_fetch(
        base_url="http://base", api_key="key", out_dir=tmp_path, stats=True
    )

    # The whole stats block succeeded, so every stats dataset reports a count.
    assert result.stats_ok is True
    assert result.publication_stats == api_run.DatasetCount(1, 1)
    assert result.views_timeline == api_run.DatasetCount(2, 2)
    assert result.views_timeline_totals == api_run.DatasetCount(1, 1)
    # The counts describe what landed on disk.
    for name, count in (
        ("publication_stats", 1),
        ("views_timeline", 2),
        ("views_timeline_totals", 1),
    ):
        assert len(json.loads((tmp_path / f"{name}.json").read_text())) == count


def test_run_fetch_stats_success_advances_the_stats_watermark(
    tmp_path, fake_client, fake_stats
):
    api_run.run_fetch(
        base_url="http://base",
        api_key="key",
        out_dir=tmp_path,
        stats=True,
        incremental=True,
    )
    state = json.loads((tmp_path / "sync_state.json").read_text())
    # A successful stats pull moves the window anchor forward with the run,
    # rather than carrying the prior (here absent) mark.
    assert state["stats_last_sync"] == state["last_sync"]


def test_run_fetch_full_writes_and_resets_sync_state(tmp_path, fake_client):
    # Seed a stale state naming a submission the server no longer reports.
    (tmp_path).mkdir(parents=True, exist_ok=True)
    (tmp_path / "sync_state.json").write_text(
        json.dumps(
            {"last_sync": "2020-01-01T00:00:00", "submission_modified": {"99": 1}}
        )
    )

    result = api_run.run_fetch(
        base_url="http://base",
        api_key="key",
        out_dir=tmp_path,
        stats=False,
        full=True,
    )

    assert result.incremental is False  # --full is always a complete pull
    assert result.sync_state_written is True
    assert result.tracked_submissions == 2
    state = json.loads((tmp_path / "sync_state.json").read_text())
    # Rewritten from scratch: the stale id is gone, not merged forward.
    assert set(state["submission_modified"]) == {"1", "2"}


def test_run_fetch_files_pulls_and_counts_submission_files(
    tmp_path, fake_client, monkeypatch
):
    from ojs.api import client as client_mod

    monkeypatch.setattr(
        client_mod,
        "fetch_all_submission_files",
        lambda base, key, submissions, **k: [
            {"id": 900 + s["id"], "_submission_id": s["id"]} for s in submissions
        ],
    )

    result = api_run.run_fetch(
        base_url="http://base",
        api_key="key",
        out_dir=tmp_path,
        stats=False,
        files=True,
    )

    assert result.submission_files == api_run.DatasetCount(2, 2)
    stored = json.loads((tmp_path / "submission_files.json").read_text())
    assert sorted(f["id"] for f in stored) == [901, 902]


def test_run_fetch_rejects_an_unknown_stats_interval(tmp_path):
    # The CLI's enum catches this on its side; a direct caller gets the same
    # typed error instead of the API rejecting the value mid-fetch.
    with pytest.raises(OptionError) as exc:
        api_run.run_fetch(
            base_url="http://base",
            api_key="key",
            out_dir=tmp_path,
            stats_interval="days",
        )
    assert "day" in str(exc.value) and "month" in str(exc.value)
    # Rejected before any fetch, so nothing was written.
    assert not (tmp_path / "submissions.json").exists()


def test_run_fetch_full_pull_counts_and_no_sync_state(tmp_path, fake_client):
    result = api_run.run_fetch(
        base_url="http://base", api_key="key", out_dir=tmp_path, stats=False
    )

    assert result.out_dir == tmp_path
    assert result.incremental is False
    # A full pull overwrites, so fetched == total for every dataset.
    assert result.submissions == api_run.DatasetCount(2, 2)
    assert result.publications == api_run.DatasetCount(2, 2)
    assert result.users == api_run.DatasetCount(1, 1)
    assert result.stats_ok is False
    # Stats were off, and the optional datasets were never pulled.
    assert result.publication_stats is None
    assert result.submission_files is None
    # The default full pull writes no sync state.
    assert result.sync_state_written is False
    assert not (tmp_path / "sync_state.json").exists()


def test_run_fetch_incremental_merges_and_writes_state(tmp_path, fake_client):
    # First run: no watermark yet, so it falls back to a full baseline.
    baseline = api_run.run_fetch(
        base_url="http://base",
        api_key="key",
        out_dir=tmp_path,
        stats=False,
        incremental=True,
    )
    assert baseline.incremental is False  # no watermark -> baseline pull
    assert baseline.sync_state_written is True
    assert baseline.tracked_submissions == 2

    # Upstream: submission 2 edited, submission 3 added.
    fake_client[2]["dateLastActivity"] = "2024-07-01 00:00:00"
    fake_client[3] = {
        "id": 3,
        "dateLastActivity": "2024-08-01 00:00:00",
        "publications": [{"id": 33}],
    }

    result = api_run.run_fetch(
        base_url="http://base",
        api_key="key",
        out_dir=tmp_path,
        stats=False,
        incremental=True,
    )

    assert result.incremental is True
    # Only the two changed submissions were fetched; all three are on disk.
    assert result.submissions == api_run.DatasetCount(2, 3)
    assert result.tracked_submissions == 3
    merged = json.loads((tmp_path / "submissions.json").read_text())
    assert sorted(s["id"] for s in merged) == [1, 2, 3]
    state = json.loads((tmp_path / "sync_state.json").read_text())
    assert set(state["submission_modified"]) == {"1", "2", "3"}


def test_run_fetch_missing_credentials_raises_config_error(tmp_path, monkeypatch):
    monkeypatch.delenv("OJS_BASE_URL", raising=False)
    monkeypatch.delenv("OJS_API_KEY", raising=False)

    with pytest.raises(ConfigError) as exc:
        api_run.run_fetch(out_dir=tmp_path)
    assert "OJS_BASE_URL" in str(exc.value)

    # A base_url alone is still incomplete.
    with pytest.raises(ConfigError):
        api_run.run_fetch(base_url="http://base", out_dir=tmp_path)


def test_run_fetch_reads_credentials_from_the_environment(
    tmp_path, monkeypatch, fake_client
):
    monkeypatch.setenv("OJS_BASE_URL", "http://base")
    monkeypatch.setenv("OJS_API_KEY", "key")
    result = api_run.run_fetch(out_dir=tmp_path, stats=False)
    assert result.submissions.total == 2


def test_run_fetch_rejects_full_with_incremental(tmp_path):
    with pytest.raises(OptionError) as exc:
        api_run.run_fetch(
            base_url="http://base",
            api_key="key",
            out_dir=tmp_path,
            full=True,
            incremental=True,
        )
    assert "--full" in str(exc.value)

    # `since` implies incremental, so it conflicts with --full too.
    with pytest.raises(OptionError):
        api_run.run_fetch(
            base_url="http://base",
            api_key="key",
            out_dir=tmp_path,
            full=True,
            since="2024-01-01",
        )


def test_run_fetch_rejects_malformed_dates(tmp_path):
    # Every date filter is validated up front, and the error names the flag the
    # CLI exposes so the message reads the same from either side.
    with pytest.raises(OptionError, match="--since"):
        api_run.run_fetch(
            base_url="http://base", api_key="key", out_dir=tmp_path, since="nope"
        )
    with pytest.raises(OptionError, match="--stats-since"):
        api_run.run_fetch(
            base_url="http://base", api_key="key", out_dir=tmp_path, stats_since="nope"
        )
    with pytest.raises(OptionError, match="--stats-until"):
        api_run.run_fetch(
            base_url="http://base",
            api_key="key",
            out_dir=tmp_path,
            stats_until="2024-13-01",
        )
    # Validation happens before any fetch, so nothing was written.
    assert not (tmp_path / "submissions.json").exists()


# --- api run_download ---------------------------------------------------------


def test_run_download_missing_submissions_dump_raises(tmp_path):
    with pytest.raises(MissingDataError) as exc:
        api_run.run_download(
            base_url="http://base", api_key="key", api_dir=tmp_path, fetch=False
        )
    assert "submissions.json" in str(exc.value)


def test_run_download_rejects_unknown_file_type(tmp_path):
    with pytest.raises(OptionError) as exc:
        api_run.run_download(
            base_url="http://base",
            api_key="key",
            api_dir=tmp_path,
            file_type="nonsense",
        )
    # The message lists what would have worked.
    assert "galleys" in str(exc.value)


def test_run_download_selects_and_records(tmp_path, monkeypatch):
    from ojs.api import files as files_mod

    class _Resp:
        content = b"PDFDATA"

        def raise_for_status(self):
            pass

        def json(self):  # pragma: no cover - the downloader only reads bytes
            return None

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, params=None):
            return _Resp()

    monkeypatch.setattr(files_mod, "_http_client", _Client)

    api_dir = tmp_path / "api"
    api_dir.mkdir()
    (api_dir / "submission_files.json").write_text(
        json.dumps(
            [
                {
                    "_submission_id": 5,
                    "id": 100,
                    "fileId": 900,
                    "fileStage": 11,
                    "name": "final.pdf",
                    "url": "http://base/files/900",
                    "revisions": [],
                }
            ]
        )
    )

    result = api_run.run_download(
        base_url="http://base",
        api_key="key",
        api_dir=api_dir,
        dest_dir=tmp_path / "files",
        submission_ids=[5],
        fetch=False,
    )

    assert result.files_dir == tmp_path / "files"
    assert result.selected == 1
    assert [r["file_id"] for r in result.downloaded] == [900]
    assert result.failed == []
    assert (tmp_path / "files" / "5/production_ready/900_final.pdf").exists()


def test_run_download_records_inaccessible_files(tmp_path, monkeypatch):
    """A 403 file lands in DownloadResult.failed and in skipped.json."""
    import httpx

    from ojs.api import files as files_mod

    class _Resp:
        content = b"PDFDATA"

        def raise_for_status(self):
            pass

    class _Forbidden:
        def raise_for_status(self):
            request = httpx.Request("GET", "http://base/files/901")
            raise httpx.HTTPStatusError(
                "forbidden",
                request=request,
                response=httpx.Response(403, request=request),
            )

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, params=None):
            # The API refuses one of the two files; the run must carry on.
            return _Forbidden() if url.endswith("901") else _Resp()

    monkeypatch.setattr(files_mod, "_http_client", _Client)

    api_dir = tmp_path / "api"
    api_dir.mkdir()
    (api_dir / "submission_files.json").write_text(
        json.dumps(
            [
                {
                    "_submission_id": 5,
                    "id": 100,
                    "fileId": 900,
                    "fileStage": 11,
                    "name": "ok.pdf",
                    "url": "http://base/files/900",
                    "revisions": [],
                },
                {
                    "_submission_id": 5,
                    "id": 101,
                    "fileId": 901,
                    "fileStage": 11,
                    "name": "denied.pdf",
                    "url": "http://base/files/901",
                    "revisions": [],
                },
            ]
        )
    )

    result = api_run.run_download(
        base_url="http://base",
        api_key="key",
        api_dir=api_dir,
        dest_dir=tmp_path / "files",
        submission_ids=[5],
        fetch=False,
    )

    assert result.selected == 2
    assert [r["file_id"] for r in result.downloaded] == [900]
    assert [r["file_id"] for r in result.failed] == [901]
    assert result.failed[0]["status"] == 403
    # The skip history is persisted, so it survives the run instead of
    # scrolling past in the log.
    skipped = json.loads((tmp_path / "files" / "skipped.json").read_text())
    assert [r["file_id"] for r in skipped] == [901]


# --- website run --------------------------------------------------------------


def _write_reviews_csv(path: Path) -> None:
    import polars as pl

    pl.DataFrame(
        [
            {
                "Submission ID": 1,
                "Round": 1,
                "Reviewer": "rev1",
                "Declined": "No",
                "Recommendation": "Accept",
                "Comments On Submission": "<p>Good</p>",
            }
        ]
    ).write_csv(path)


def test_website_run_norm_picks_the_newest_export(tmp_path):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    _write_reviews_csv(downloads / "reviews-20240101.csv")
    _write_reviews_csv(downloads / "reviews-20240601.csv")

    result = web_run.run_norm(
        "reviews", downloads_dir=downloads, out_dir=tmp_path / "out"
    )

    # Date-stamped names sort lexicographically, so newest-first is a reverse sort.
    assert result.input_file.name == "reviews-20240601.csv"
    assert result.tables == ["reviews"]
    assert result.rows == {"reviews": 1}
    assert (tmp_path / "out" / "reviews.csv").exists()


def test_website_run_norm_accepts_an_explicit_input_file(tmp_path):
    source = tmp_path / "anywhere.csv"
    _write_reviews_csv(source)
    result = web_run.run_norm("reviews", input_file=source, out_dir=tmp_path / "out")
    assert result.input_file == source


def test_website_run_norm_no_matching_export_raises(tmp_path):
    with pytest.raises(MissingDataError) as exc:
        web_run.run_norm("reviews", downloads_dir=tmp_path, out_dir=tmp_path / "out")
    assert "reviews-*.csv" in str(exc.value)


def test_website_run_rejects_unknown_report(tmp_path):
    with pytest.raises(OptionError) as exc:
        web_run.run_norm("editors", downloads_dir=tmp_path)
    assert "articles" in str(exc.value) and "reviews" in str(exc.value)


def test_website_run_report_fetch_names_every_missing_setting(tmp_path, monkeypatch):
    monkeypatch.delenv("OJS_BASE_URL", raising=False)
    monkeypatch.delenv("OJS_USERNAME", raising=False)
    monkeypatch.delenv("OJS_PASSWORD", raising=False)
    monkeypatch.delenv("OJS_ARTICLES_REPORT_URL", raising=False)

    with pytest.raises(ConfigError) as exc:
        web_run.run_report_fetch("articles", downloads_dir=tmp_path)
    message = str(exc.value)
    for var in (
        "OJS_BASE_URL",
        "OJS_USERNAME",
        "OJS_PASSWORD",
        "OJS_ARTICLES_REPORT_URL",
    ):
        assert var in message


def test_website_run_report_fetch_writes_dated_csv(tmp_path, monkeypatch):
    from datetime import date

    from ojs.website import reports

    written: dict[str, object] = {}

    def fake_download(*, base_url, username, password, report_url, dest):
        written.update(report_url=report_url, dest=dest)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_text("Submission ID\n1\n")

    monkeypatch.setattr(reports, "download_report", fake_download)

    dest = web_run.run_report_fetch(
        "articles",
        base_url="https://host/index.php/j",
        username="editor",
        password="pw",
        report_url="report/articles",
        downloads_dir=tmp_path,
    )

    # Named for the report and stamped with today's date, in the directory
    # `run_norm` globs -- so the fetch -> norm handoff needs no manual step.
    assert dest == tmp_path / f"articles-{date.today():%Y%m%d}.csv"
    assert dest.read_text() == "Submission ID\n1\n"
    # A relative report URL is resolved against the base URL.
    assert written["report_url"] == "https://host/index.php/j/report/articles"
