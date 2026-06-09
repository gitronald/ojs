"""Tests for ojs."""

from typing import Any

import httpx
import polars as pl
import pytest

from ojs.api.client import PAGE_SIZE, _paginate, _stats_params
from ojs.api.normalize import (
    _build_email_to_user_id,
    normalize_authors,
    normalize_publication_stats,
    normalize_publications,
    normalize_review_assignments,
    normalize_submissions,
    normalize_views_timeline,
    normalize_views_timeline_totals,
)
from ojs.api.sync import (
    build_submission_modified,
    load_sync_state,
    save_sync_state,
    stats_window_start,
    submission_watermark,
    upsert_json_list,
    write_json,
)
from ojs.utils import strip_html


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload

    @property
    def content(self) -> bytes:
        # Part of the _Response protocol (used by the file downloader); the
        # JSON-path tests never read it.
        return b""


class _FakeClient:
    """Returns staged payloads in order, one per GET call."""

    def __init__(self, payloads):
        self._payloads = payloads
        self.calls = []

    def get(self, url, params=None):
        self.calls.append(dict(params or {}))
        return _FakeResponse(self._payloads[len(self.calls) - 1])


def test_placeholder():
    assert True


# --- HTML stripping / entity decoding ----------------------------------------


def test_strip_html_decodes_entities():
    # Numeric (decimal + hex) and named entities must be DECODED, not deleted.
    df = pl.DataFrame({"x": ["author&#8217;s caf&#233; &amp; &#x2014; &mdash; end"]})
    out = df.with_columns(strip_html(pl.col("x")).alias("x"))["x"][0]
    assert out == "author’s café & — — end"


def test_strip_html_preserves_nulls_and_whitespace():
    df = pl.DataFrame({"x": ["<p>a&nbsp;b</p><p>c</p>", None, ""]})
    vals = df.with_columns(strip_html(pl.col("x")).alias("x"))["x"].to_list()
    # nbsp -> regular space, </p> -> newline, nulls/empty pass through untouched.
    assert vals == ["a b\nc", None, ""]


def test_strip_html_all_null_column_does_not_crash():
    # An all-None column infers the Null dtype; the cast must let it through.
    df = pl.DataFrame({"x": [None, None]})
    assert df.schema["x"] == pl.Null
    out = df.with_columns(strip_html(pl.col("x")).alias("x"))
    assert out["x"].to_list() == [None, None]
    assert out.schema["x"] == pl.String


def test_strip_html_decoded_brackets_not_restripped():
    # Entity-encoded angle brackets decode AFTER tag stripping, so they survive.
    df = pl.DataFrame({"x": ["use &lt;tag&gt; literally"]})
    out = df.with_columns(strip_html(pl.col("x")).alias("x"))["x"][0]
    assert out == "use <tag> literally"


# --- Submissions / authors null-safety ---------------------------------------


def test_normalize_submissions_keywords_null_and_all_none_abstract():
    subs = [{"id": 1, "publications": [{"sectionId": 1, "fullTitle": {"en_US": "T"}}]}]
    # OJS serializes unset multilingual fields as explicit null.
    pubs = [{"_submission_id": 1, "keywords": None, "abstract": None, "authors": []}]
    df = normalize_submissions(subs, pubs)
    assert df.height == 1
    assert df["keywords"][0] is None
    assert df["abstract"][0] is None
    assert df.schema["abstract"] == pl.String


def test_normalize_submissions_keywords_present():
    subs = [{"id": 1, "publications": [{"sectionId": 1, "fullTitle": {"en_US": "T"}}]}]
    pubs = [
        {
            "_submission_id": 1,
            "keywords": {"en_US": ["a", "b"]},
            "abstract": {"en_US": "x"},
            "authors": [],
        }
    ]
    assert normalize_submissions(subs, pubs)["keywords"][0] == "a; b"


def test_normalize_authors_handles_null_seq():
    pubs = [
        {
            "_submission_id": 1,
            "authors": [
                {"familyName": {"en_US": "Beta"}, "seq": 2},
                {"familyName": {"en_US": "NullSeq"}, "seq": None},
                {"familyName": {"en_US": "Missing"}},
                {"familyName": {"en_US": "Alpha"}, "seq": 1},
            ],
        }
    ]
    df = normalize_authors(pubs, [])  # must not raise on seq: null
    # seq null/missing fold to 0 (stable), so they lead, then 1, then 2.
    assert df["family_name"].to_list() == ["NullSeq", "Missing", "Alpha", "Beta"]
    assert df["author_number"].to_list() == [1, 2, 3, 4]


def test_build_email_to_user_id_excludes_duplicate_emails(capsys):
    users = [
        {"id": 1, "email": "dup@x.org"},
        {"id": 2, "email": "dup@x.org"},  # collision -> excluded, not last-wins
        {"id": 3, "email": "unique@x.org"},
    ]
    mapping = _build_email_to_user_id(users)
    assert mapping == {"unique@x.org": 3}
    assert "dup@x.org" in capsys.readouterr().out
    # The same id repeated on one email is not a conflict.
    assert _build_email_to_user_id(
        [{"id": 5, "email": "a@x.org"}, {"id": 5, "email": "a@x.org"}]
    ) == {"a@x.org": 5}


def test_normalize_authors_duplicate_email_yields_null_user_id():
    pubs = [
        {
            "_submission_id": 1,
            "authors": [
                {"familyName": {"en_US": "A"}, "email": "dup@x.org", "seq": 1},
            ],
        }
    ]
    users = [{"id": 1, "email": "dup@x.org"}, {"id": 2, "email": "dup@x.org"}]
    df = normalize_authors(pubs, users)
    # Ambiguous email -> unmatched (null), never a confidently-wrong id.
    assert df["user_id"][0] is None


# OJS serializes empty array fields as explicit JSON null; `.get(key, [])` only
# defaults on a MISSING key, so each of these would crash before the `or []` fix.


def test_normalize_submissions_handles_null_authors():
    subs = [{"id": 1, "publications": [{"sectionId": 1, "fullTitle": {"en_US": "T"}}]}]
    pubs = [{"_submission_id": 1, "authors": None, "keywords": None, "abstract": None}]
    df = normalize_submissions(subs, pubs)  # must not raise on authors: null
    assert df["author_count"][0] is None  # 0 authors -> null


def test_normalize_authors_handles_null_authors_list():
    df = normalize_authors([{"_submission_id": 1, "authors": None}], [])
    assert df.height == 0  # must not raise; no rows


def test_normalize_publications_handles_null_galleys():
    subs = [
        {
            "id": 1,
            "statusLabel": "Published",
            "publications": [
                {
                    "sectionId": 1,
                    "fullTitle": {"en_US": "T"},
                    "datePublished": "2024-01-01",
                }
            ],
        }
    ]
    pubs = [
        {"_submission_id": 1, "id": 10, "status": 3, "galleys": None, "authors": []}
    ]
    subs_df = normalize_submissions(subs, pubs)
    df = normalize_publications(subs_df, pubs)  # must not raise on galleys: null
    assert df["galley_count"][0] == 0


def test_normalize_review_assignments_handles_null_list():
    subs_ext = [
        {"id": 1, "reviewAssignments": [{"id": 5, "round": 1}]},
        {"id": 2, "reviewAssignments": None},  # explicit null must not abort
    ]
    df = normalize_review_assignments(subs_ext)
    assert df["assignment_id"].to_list() == [5]  # the valid sibling is not lost


def test_paginate_envelope_walks_pages():
    payloads = [
        {"items": [{"id": i} for i in range(PAGE_SIZE)], "itemsMax": PAGE_SIZE + 5},
        {
            "items": [{"id": i} for i in range(PAGE_SIZE, PAGE_SIZE + 5)],
            "itemsMax": PAGE_SIZE + 5,
        },
    ]
    client = _FakeClient(payloads)
    items = _paginate(client, "url", {})
    assert len(items) == PAGE_SIZE + 5
    assert client.calls[1]["offset"] == PAGE_SIZE


def test_paginate_bare_array_single_page():
    # Some endpoints (e.g. /stats/publications) return a bare array.
    payloads = [[{"id": 1}, {"id": 2}, {"id": 3}]]
    client = _FakeClient(payloads)
    items = _paginate(client, "url", {})
    assert items == [{"id": 1}, {"id": 2}, {"id": 3}]
    assert len(client.calls) == 1  # short page stops pagination


def test_paginate_error_object_surfaces_payload():
    # A degraded endpoint can return 200 with an error object lacking `items`;
    # that must raise a descriptive error (with the URL) rather than a bare
    # KeyError that hides the real problem.
    payloads = [{"error": "api.403.unauthorized", "errorMessage": "Forbidden"}]
    client = _FakeClient(payloads)
    with pytest.raises(RuntimeError) as exc:
        _paginate(client, "http://base/api/v1/users", {})
    assert "http://base/api/v1/users" in str(exc.value)
    assert "api.403.unauthorized" in str(exc.value)


def test_request_with_retry_redacts_api_token_in_error():
    from ojs.api.client import _redact_token, _request_with_retry

    # The query value is scrubbed in isolation...
    assert (
        _redact_token("GET http://h/x?apiToken=SECRET&count=2")
        == "GET http://h/x?apiToken=[REDACTED]&count=2"
    )

    # ...so a propagating HTTP error (the token is sent as a query param) never
    # carries the key in its message, only [REDACTED].
    request = httpx.Request("GET", "http://base/api/v1/x?apiToken=SECRET123&count=100")
    response = httpx.Response(500, request=request)

    class _C:
        def get(self, url, params=None):
            raise httpx.HTTPStatusError(
                f"Server error '500 Internal Server Error' for url '{request.url}'",
                request=request,
                response=response,
            )

    with pytest.raises(httpx.HTTPStatusError) as exc:
        _request_with_retry(_C(), "http://base/api/v1/x", {"apiToken": "SECRET123"})
    assert "SECRET123" not in str(exc.value)
    assert "[REDACTED]" in str(exc.value)
    # The status stays inspectable, so 403/404 skip handling is unaffected.
    assert exc.value.response.status_code == 500


def test_fetch_view_timelines_tags_points(monkeypatch):
    from ojs.api import client as client_mod

    class _CtxClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, params=None):
            # url ends with /{submission_id}/{kind}; echo both back so the test
            # can confirm each point is routed and tagged correctly.
            parts = url.rstrip("/").split("/")
            kind, sub_id = parts[-1], int(parts[-2])
            return _FakeResponse(
                [{"date": "2024-01-01", "value": sub_id, "label": kind}]
            )

    monkeypatch.setattr(client_mod.httpx, "Client", _CtxClient)
    points = client_mod.fetch_view_timelines(
        "http://base", "key", [2, 7], interval="day"
    )

    assert len(points) == 4  # 2 submissions x (abstract + galley)
    tagged = {(p["_submission_id"], p["kind"]) for p in points}
    assert tagged == {(2, "abstract"), (2, "galley"), (7, "abstract"), (7, "galley")}
    # value echoes the submission id, confirming correct per-submission routing.
    assert all(p["value"] == p["_submission_id"] for p in points)
    # Every point records the granularity it was fetched at.
    assert all(p["interval"] == "day" for p in points)


def test_fetch_view_timeline_totals_tags_kind(monkeypatch):
    from ojs.api import client as client_mod

    class _CtxClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, params=None):
            # url ends with /{kind}; echo it back so the test can confirm each
            # point is routed to the right journal-wide endpoint and tagged.
            kind = url.rstrip("/").split("/")[-1]
            value = 1 if kind == "abstract" else 99
            return _FakeResponse([{"date": "2024-01", "value": value, "label": kind}])

    monkeypatch.setattr(client_mod.httpx, "Client", _CtxClient)
    points = client_mod.fetch_view_timeline_totals(
        "http://base", "key", interval="month"
    )

    assert len(points) == 2  # one point each for abstract + galley
    tagged = {p["kind"]: p["value"] for p in points}
    # Distinct endpoints yield distinct values -- the whole point of the
    # aggregate fetch (per-submission endpoints collapse to identical values).
    assert tagged == {"abstract": 1, "galley": 99}
    # Every point records the granularity it was fetched at.
    assert all(p["interval"] == "month" for p in points)


def test_normalize_views_timeline_totals_long_format():
    # Mixed day + month points exercise the interval-aware sort: day and month
    # series stay in separate blocks rather than interleaving by date.
    timeline = [
        {"kind": "galley", "date": "2024-02", "value": 4, "interval": "month"},
        {"kind": "abstract", "date": "2024-02", "value": 12, "interval": "month"},
        {"kind": "abstract", "date": "2024-01", "value": 8, "interval": "month"},
        {"kind": "abstract", "date": "2024-01-15", "value": 3, "interval": "day"},
    ]
    df = normalize_views_timeline_totals(timeline)

    assert df.columns == ["date", "interval", "views", "kind"]
    # Sorted by (interval, kind, date): the lone "day" point leads, then the
    # "month" block ordered by kind then date.
    assert df["interval"].to_list() == ["day", "month", "month", "month"]
    assert df["kind"].to_list() == ["abstract", "abstract", "abstract", "galley"]
    assert df["date"].to_list() == ["2024-01-15", "2024-01", "2024-02", "2024-02"]
    assert df["views"].to_list() == [3, 8, 12, 4]


def test_normalize_views_timeline_totals_empty():
    df = normalize_views_timeline_totals(None)
    assert df.height == 0
    assert df.columns == ["date", "interval", "views", "kind"]


def test_stats_params_drops_unset_filters():
    params = _stats_params("token")
    assert params == {"apiToken": "token"}


def test_stats_params_includes_set_filters():
    params = _stats_params(
        "token",
        date_start="2024-01-01",
        date_end="2024-12-31",
        submission_ids=[3, 1, 2],
    )
    assert params == {
        "apiToken": "token",
        "dateStart": "2024-01-01",
        "dateEnd": "2024-12-31",
        "submissionIds": "3,1,2",
    }


def test_normalize_publication_stats_maps_fields():
    stats = [
        {
            "abstractViews": 50,
            "galleyViews": 30,
            "pdfViews": 20,
            "htmlViews": 10,
            "otherViews": 0,
            "publication": {"id": 7, "fullTitle": {"en_US": "Second"}},
        },
        {
            "abstractViews": 5,
            "galleyViews": 3,
            "pdfViews": 2,
            "htmlViews": 1,
            "otherViews": 0,
            "publication": {"id": 2, "fullTitle": {"en_US": "First"}},
        },
    ]
    df = normalize_publication_stats(stats)

    assert df.columns == [
        "submission_id",
        "abstract_views",
        "galley_views",
        "pdf_views",
        "html_views",
        "other_views",
    ]
    # Sorted by submission_id ascending.
    assert df["submission_id"].to_list() == [2, 7]
    assert df["abstract_views"].to_list() == [5, 50]
    assert df.schema["submission_id"] == pl.Int64


def test_normalize_publication_stats_empty():
    df = normalize_publication_stats([])
    assert df.height == 0
    assert df.schema["abstract_views"] == pl.Int64
    assert "submission_id" in df.columns


def test_normalize_publication_stats_drops_null_publication():
    stats = [
        {"abstractViews": 5, "publication": {"id": 2}},
        {"abstractViews": 9, "publication": None},
        {"abstractViews": 1, "publication": {}},
    ]
    df = normalize_publication_stats(stats)
    # Rows without a joinable submission_id are dropped.
    assert df["submission_id"].to_list() == [2]


def test_normalize_views_timeline_long_format():
    timeline = [
        {"_submission_id": 7, "kind": "abstract", "date": "2024-01-02", "value": 8},
        {"_submission_id": 2, "kind": "galley", "date": "2024-01-01", "value": 4},
        {"_submission_id": 2, "kind": "abstract", "date": "2024-01-01", "value": 12},
    ]
    df = normalize_views_timeline(timeline)

    assert df.columns == ["submission_id", "date", "interval", "views", "kind"]
    # Sorted by (submission_id, interval, kind, date).
    assert df["submission_id"].to_list() == [2, 2, 7]
    assert df["kind"].to_list() == ["abstract", "galley", "abstract"]
    assert df["views"].to_list() == [12, 4, 8]
    assert df.schema["submission_id"] == pl.Int64


def test_normalize_views_timeline_drops_zeros_preserving_sum():
    # The OJS stats endpoint pads every day with value 0; those rows are dropped.
    timeline: list[dict[str, Any]] = [
        {"_submission_id": 2, "kind": "abstract", "date": "2024-01-01", "value": 0},
        {"_submission_id": 2, "kind": "abstract", "date": "2024-01-02", "value": 5},
        {"_submission_id": 2, "kind": "abstract", "date": "2024-01-03", "value": 0},
        {"_submission_id": 2, "kind": "galley", "date": "2024-01-01", "value": 3},
        {"_submission_id": 7, "kind": "abstract", "date": "2024-01-01", "value": 0},
    ]
    df = normalize_views_timeline(timeline)

    # Only the non-zero days survive...
    assert df.height == 2
    assert bool((df["views"] > 0).all())
    # ...but the total view count is identical to the raw input (zeros add nothing).
    assert df["views"].sum() == sum(p["value"] for p in timeline) == 8
    # Per-kind sums are preserved too.
    assert df.filter(pl.col("kind") == "abstract")["views"].sum() == 5
    assert df.filter(pl.col("kind") == "galley")["views"].sum() == 3


def test_normalize_views_timeline_totals_sum_matches_raw():
    # Totals are NOT zero-filtered (they back a continuous graph), so the
    # normalized sum must equal the raw aggregate input exactly.
    timeline: list[dict[str, Any]] = [
        {"kind": "abstract", "date": "2024-01-01", "value": 0},
        {"kind": "abstract", "date": "2024-01-02", "value": 9},
        {"kind": "galley", "date": "2024-01-01", "value": 4},
    ]
    df = normalize_views_timeline_totals(timeline)
    assert df.height == 3
    assert df["views"].sum() == sum(p["value"] for p in timeline) == 13


def test_normalize_views_timeline_month_interval_chronological():
    # Zero-padded YYYY-MM months sort chronologically as strings.
    timeline = [
        {"_submission_id": 2, "kind": "abstract", "date": "2024-10", "value": 1},
        {"_submission_id": 2, "kind": "abstract", "date": "2024-02", "value": 2},
        {"_submission_id": 2, "kind": "abstract", "date": "2024-01", "value": 3},
    ]
    df = normalize_views_timeline(timeline)
    assert df["date"].to_list() == ["2024-01", "2024-02", "2024-10"]


def test_normalize_views_timeline_empty():
    df = normalize_views_timeline(None)
    assert df.height == 0
    assert df.columns == ["submission_id", "date", "interval", "views", "kind"]
    assert df.schema["views"] == pl.Int64


def test_normalize_views_timeline_keeps_interval():
    # Day and month points that share (submission_id, date-ish, kind) stay
    # distinguishable by `interval` so consumers can filter rather than sum both.
    timeline = [
        {
            "_submission_id": 2,
            "interval": "day",
            "date": "2024-01-01",
            "kind": "abstract",
            "value": 5,
        },
        {
            "_submission_id": 2,
            "interval": "month",
            "date": "2024-01",
            "kind": "abstract",
            "value": 5,
        },
    ]
    df = normalize_views_timeline(timeline)
    assert "interval" in df.columns
    assert set(df["interval"].to_list()) == {"day", "month"}


# --- Incremental sync state ---------------------------------------------------


def test_load_sync_state_missing_returns_empty(tmp_path):
    state = load_sync_state(tmp_path / "absent.json")
    assert state == {
        "last_sync": None,
        "stats_last_sync": None,
        "submission_modified": {},
    }


def test_load_sync_state_corrupt_returns_empty(tmp_path):
    path = tmp_path / "sync_state.json"
    path.write_text("{not valid json")
    assert load_sync_state(path) == {
        "last_sync": None,
        "stats_last_sync": None,
        "submission_modified": {},
    }


def test_load_sync_state_backfills_stats_last_sync(tmp_path):
    # A legacy state file written before stats_last_sync existed loads cleanly.
    path = tmp_path / "sync_state.json"
    path.write_text(
        '{"last_sync": "2026-01-01T00:00:00-07:00", "submission_modified": {}}'
    )
    assert load_sync_state(path)["stats_last_sync"] is None


def test_save_then_load_roundtrip(tmp_path):
    path = tmp_path / "nested" / "sync_state.json"  # parent created on save
    state = {
        "last_sync": "2026-05-28T13:09:38-07:00",
        "stats_last_sync": "2026-05-20T00:00:00-07:00",
        "submission_modified": {"1": "2024-06-10 12:00:00"},
    }
    save_sync_state(path, state)
    assert load_sync_state(path) == state


def test_write_json_atomic_replaces_existing(tmp_path):
    path = tmp_path / "data.json"
    write_json(path, {"a": 1})
    write_json(path, {"a": 2})
    import json

    assert json.loads(path.read_text()) == {"a": 2}
    # The atomic rename leaves no stray temp files behind.
    assert list(tmp_path.iterdir()) == [path]


def test_write_json_failure_leaves_prior_file_intact(tmp_path):
    path = tmp_path / "data.json"
    write_json(path, {"a": 1})
    # A non-serializable payload fails before the file is touched; the prior
    # complete file must survive and no temp file may be left behind.
    with pytest.raises(TypeError):
        write_json(path, {"bad": object()})
    import json

    assert json.loads(path.read_text()) == {"a": 1}
    assert list(tmp_path.iterdir()) == [path]


def test_write_json_cleans_temp_on_replace_failure(tmp_path, monkeypatch):
    import json
    import os

    path = tmp_path / "data.json"
    write_json(path, {"a": 1})

    def boom(src, dst):
        raise RuntimeError("interrupted")

    # Simulate the process dying at the atomic rename: the prior complete file
    # must survive and the temp file must be cleaned up.
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(RuntimeError):
        write_json(path, {"a": 2})
    assert json.loads(path.read_text()) == {"a": 1}
    assert list(tmp_path.iterdir()) == [path]


def test_write_json_respects_umask_permissions(tmp_path):
    import os
    import stat
    import sys

    if sys.platform == "win32":  # pragma: no cover - POSIX permission semantics
        return
    path = tmp_path / "data.json"
    write_json(path, {"a": 1})
    cur = os.umask(0o022)
    os.umask(cur)
    # mkstemp makes the temp 0600; the write must restore the umask-derived mode
    # (typically 0644) rather than silently shipping owner-only dumps.
    assert stat.S_IMODE(path.stat().st_mode) == 0o666 & ~cur


def test_build_submission_modified_stringifies_ids():
    subs = [
        {"id": 7, "dateLastActivity": "2024-06-15 08:30:00"},
        {"id": 2, "dateLastActivity": None},  # missing timestamp kept as None
    ]
    assert build_submission_modified(subs) == {
        "7": "2024-06-15 08:30:00",
        "2": None,
    }


def test_submission_watermark_max_minus_buffer():
    state = {
        "submission_modified": {
            "1": "2024-06-10 12:00:00",
            "2": "2024-06-15 08:30:00",  # newest
            "3": None,  # nulls ignored, do not break max()
        }
    }
    # Default overlap buffer is one day, in the API's `Y-m-d H:i:s` format.
    assert submission_watermark(state) == "2024-06-14 08:30:00"


def test_submission_watermark_empty_returns_none():
    assert submission_watermark({"submission_modified": {}}) is None
    assert submission_watermark({"submission_modified": {"1": None}}) is None


def test_stats_window_start_from_last_sync_minus_buffer():
    # Legacy state with no stats_last_sync falls back to last_sync.
    state = {"last_sync": "2026-05-28T13:09:38-07:00"}
    # Wall-clock date minus the one-day overlap buffer, date-only for dateStart.
    assert stats_window_start(state) == "2026-05-27"


def test_stats_window_start_prefers_stats_last_sync():
    state = {
        "last_sync": "2026-05-28T13:09:38-07:00",
        "stats_last_sync": "2026-05-20T00:00:00-07:00",
    }
    # Anchored on the stats-specific mark (minus the one-day buffer), not last_sync.
    assert stats_window_start(state) == "2026-05-19"


def test_stats_window_start_none_without_last_sync():
    assert stats_window_start({"last_sync": None}) is None
    assert stats_window_start({}) is None


# --- Raw-JSON upsert (Option A merge) ----------------------------------------


def test_upsert_json_list_by_id_replaces_in_place_and_appends():
    existing = [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}]
    new = [{"id": 2, "v": "B"}, {"id": 3, "v": "c"}]
    merged = upsert_json_list(existing, new, "id")
    # id 2 replaced in its original slot; id 3 appended; order preserved.
    assert merged == [{"id": 1, "v": "a"}, {"id": 2, "v": "B"}, {"id": 3, "v": "c"}]


def test_upsert_json_list_empty_existing_is_new():
    new = [{"id": 1}, {"id": 2}]
    assert upsert_json_list([], new, "id") == new


def test_upsert_json_list_idempotent():
    existing = [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}]
    assert upsert_json_list(existing, existing, "id") == existing


def test_upsert_json_list_composite_callable_key():
    # Generic composite callable-key upsert (views_timeline itself now keys by the
    # interval-aware 4-tuple -- see test_views_timeline_interval_key_separates...).
    def key(p):
        return (p["_submission_id"], p["date"], p["kind"])

    existing = [
        {"_submission_id": 2, "date": "2024-01-01", "kind": "abstract", "value": 5},
        {"_submission_id": 2, "date": "2024-01-02", "kind": "abstract", "value": 7},
    ]
    new = [
        # same (sub, date, kind) -> replaces the stale count
        {"_submission_id": 2, "date": "2024-01-02", "kind": "abstract", "value": 9},
        # new bucket -> appended
        {"_submission_id": 2, "date": "2024-01-02", "kind": "galley", "value": 1},
    ]
    merged = upsert_json_list(existing, new, key)
    assert [p["value"] for p in merged] == [5, 9, 1]
    assert len(merged) == 3


def test_views_timeline_interval_key_separates_granularities():
    # The interval-aware key is idempotent within a granularity but keeps day and
    # month series in disjoint keyspaces, so switching --stats-interval no longer
    # double-counts.
    def key(p):
        return (
            p.get("_submission_id"),
            p.get("interval"),
            p.get("date"),
            p.get("kind"),
        )

    day = {
        "_submission_id": 2,
        "interval": "day",
        "date": "2024-01-15",
        "kind": "abstract",
        "value": 5,
    }
    # Re-pulling the same interval replaces in place (no duplicate).
    merged = upsert_json_list([day], [{**day, "value": 9}], key)
    assert [p["value"] for p in merged] == [9]
    # A month point is a distinct key -> appended, not collapsed onto the day one.
    month = {
        "_submission_id": 2,
        "interval": "month",
        "date": "2024-01",
        "kind": "abstract",
        "value": 40,
    }
    merged2 = upsert_json_list(merged, [month], key)
    assert len(merged2) == 2


# --- Early-stop pagination (incremental) -------------------------------------


def test_paginate_since_stops_within_page():
    # DESC by dateLastActivity; the first record at/below `since` ends the walk.
    payloads = [
        {
            "items": [
                {"id": 1, "dateLastActivity": "2024-07-01 00:00:00"},
                {"id": 2, "dateLastActivity": "2024-05-01 00:00:00"},  # <= since
                {"id": 3, "dateLastActivity": "2024-04-01 00:00:00"},
            ],
            "itemsMax": 3,
        }
    ]
    client = _FakeClient(payloads)
    items = _paginate(client, "url", {}, since="2024-06-01 00:00:00")
    assert [i["id"] for i in items] == [1]
    assert len(client.calls) == 1  # stopped mid-page, no further requests


def test_paginate_since_crosses_pages_then_stops():
    page1 = {
        "items": [
            {"id": i, "dateLastActivity": "2024-12-31 00:00:00"}
            for i in range(PAGE_SIZE)
        ],
        "itemsMax": PAGE_SIZE + 1,
    }
    page2 = {
        "items": [{"id": PAGE_SIZE, "dateLastActivity": "2024-01-01 00:00:00"}],
        "itemsMax": PAGE_SIZE + 1,
    }
    client = _FakeClient([page1, page2])
    items = _paginate(client, "url", {}, since="2024-06-01 00:00:00")
    # Full first page kept, then page 2's older record stops the walk.
    assert len(items) == PAGE_SIZE
    assert client.calls[1]["offset"] == PAGE_SIZE
    assert len(client.calls) == 2


def test_paginate_since_keeps_items_missing_timestamp():
    payloads = [
        {
            "items": [
                {"id": 1, "dateLastActivity": "2024-07-01 00:00:00"},
                {"id": 2},  # no timestamp -> kept (err toward refetch)
                {"id": 3, "dateLastActivity": "2024-05-01 00:00:00"},  # stop
            ],
            "itemsMax": 3,
        }
    ]
    client = _FakeClient(payloads)
    items = _paginate(client, "url", {}, since="2024-06-01 00:00:00")
    assert [i["id"] for i in items] == [1, 2]


def test_fetch_submissions_orders_desc_only_when_since(monkeypatch):
    from ojs.api import client as client_mod

    captured = []

    class _RecordingClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, params=None):
            captured.append(dict(params or {}))
            return _FakeResponse({"items": [], "itemsMax": 0})

    monkeypatch.setattr(client_mod.httpx, "Client", _RecordingClient)

    client_mod.fetch_submissions("http://base", "key")
    assert "orderBy" not in captured[-1]

    captured.clear()
    client_mod.fetch_submissions("http://base", "key", since="2024-06-01 00:00:00")
    assert captured[-1]["orderBy"] == "dateLastActivity"
    assert captured[-1]["orderDirection"] == "DESC"


# --- Skip-unchanged publication details --------------------------------------


class _RecordingPubClient:
    """Records the publication-detail URLs it is asked to GET."""

    fetched: list[str] = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, params=None):
        type(self).fetched.append(url)
        return _FakeResponse({"id": 99})


def test_fetch_all_publications_skips_unchanged(monkeypatch):
    from ojs.api import client as client_mod

    _RecordingPubClient.fetched = []
    monkeypatch.setattr(client_mod.httpx, "Client", _RecordingPubClient)

    submissions = [
        {
            "id": 1,
            "dateLastActivity": "2024-06-15 00:00:00",
            "publications": [{"id": 11}],
        },
        {
            "id": 2,
            "dateLastActivity": "2024-07-01 00:00:00",
            "publications": [{"id": 22}],
        },
        {
            "id": 3,
            "dateLastActivity": "2024-07-02 00:00:00",
            "publications": [{"id": 33}],
        },
    ]
    # sub 1 unchanged (matches), sub 2 changed, sub 3 brand-new (absent).
    known = {1: "2024-06-15 00:00:00", 2: "2024-06-01 00:00:00"}
    pubs = client_mod.fetch_all_publications(
        "http://base", "key", submissions, known=known
    )

    assert {p["_submission_id"] for p in pubs} == {2, 3}
    fetched = _RecordingPubClient.fetched
    assert all("/publications/11" not in u for u in fetched)  # skipped
    assert any("/publications/22" in u for u in fetched)
    assert any("/publications/33" in u for u in fetched)


def test_fetch_all_publications_without_known_fetches_all(monkeypatch):
    from ojs.api import client as client_mod

    _RecordingPubClient.fetched = []
    monkeypatch.setattr(client_mod.httpx, "Client", _RecordingPubClient)

    submissions = [
        {
            "id": 1,
            "dateLastActivity": "2024-06-15 00:00:00",
            "publications": [{"id": 11}],
        },
        {
            "id": 2,
            "dateLastActivity": "2024-07-01 00:00:00",
            "publications": [{"id": 22}],
        },
    ]
    pubs = client_mod.fetch_all_publications("http://base", "key", submissions)
    assert {p["_submission_id"] for p in pubs} == {1, 2}
    assert len(_RecordingPubClient.fetched) == 2


# --- CLI fetch wiring (incremental end-to-end) -------------------------------


def _patch_api_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OJS_BASE_URL", "http://base")
    monkeypatch.setenv("OJS_API_KEY", "key")
    monkeypatch.setenv("OJS_API_DIR", str(tmp_path))


def test_api_fetch_incremental_merges_and_tracks_state(tmp_path, monkeypatch):
    import json

    from typer.testing import CliRunner

    from ojs import cli
    from ojs.api import client as client_mod

    _patch_api_env(tmp_path, monkeypatch)

    # Mutable fake "server" dataset, keyed by id with a dateLastActivity.
    server = {
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
    calls = {"since": [], "known": []}

    def fake_submissions(base_url, api_key, *, since=None):
        calls["since"].append(since)
        subs = list(server.values())
        if since is not None:  # emulate early-stop DESC filtering
            subs = [s for s in subs if s["dateLastActivity"] > since]
        return subs

    def fake_ext(base_url, api_key, *, since=None):
        subs = list(server.values())
        if since is not None:
            subs = [s for s in subs if s["dateLastActivity"] > since]
        return [{"id": s["id"], "reviewAssignments": []} for s in subs]

    def fake_pubs(base_url, api_key, submissions, *, known=None):
        calls["known"].append(known)
        return [
            {"id": s["publications"][0]["id"], "_submission_id": s["id"], "status": 1}
            for s in submissions
        ]

    def fake_users(base_url, api_key):
        return [{"id": 100, "email": "a@example.com"}]

    monkeypatch.setattr(client_mod, "fetch_submissions", fake_submissions)
    monkeypatch.setattr(client_mod, "fetch_submissions_extended", fake_ext)
    monkeypatch.setattr(client_mod, "fetch_all_publications", fake_pubs)
    monkeypatch.setattr(client_mod, "fetch_users", fake_users)

    runner = CliRunner()

    # First incremental run: no prior state -> full baseline (since None).
    r1 = runner.invoke(cli.app, ["api", "fetch", "--no-stats", "--incremental"])
    assert r1.exit_code == 0, r1.output
    assert calls["since"][-1] is None
    state = json.loads((tmp_path / "sync_state.json").read_text())
    assert set(state["submission_modified"]) == {"1", "2"}
    assert state["last_sync"]

    # Upstream: sub 2 edited, sub 3 added.
    server[2]["dateLastActivity"] = "2024-07-01 00:00:00"
    server[3] = {
        "id": 3,
        "dateLastActivity": "2024-08-01 00:00:00",
        "publications": [{"id": 33}],
    }

    # Second incremental run: watermark present -> since set, delta merged.
    r2 = runner.invoke(cli.app, ["api", "fetch", "--no-stats", "--incremental"])
    assert r2.exit_code == 0, r2.output
    assert calls["since"][-1] is not None  # used the stored watermark
    # known passed to publication fetch is the prior watermark (subs 1 and 2).
    assert calls["known"][-1] == {1: "2024-01-01 00:00:00", 2: "2024-06-01 00:00:00"}

    merged = json.loads((tmp_path / "submissions.json").read_text())
    by_id = {s["id"]: s for s in merged}
    assert sorted(by_id) == [1, 2, 3]  # 1 preserved, 2 updated in place, 3 appended
    assert by_id[2]["dateLastActivity"] == "2024-07-01 00:00:00"
    # State now tracks all three submissions.
    state2 = json.loads((tmp_path / "sync_state.json").read_text())
    assert set(state2["submission_modified"]) == {"1", "2", "3"}


def test_api_fetch_default_does_not_write_state(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from ojs import cli
    from ojs.api import client as client_mod

    _patch_api_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        client_mod,
        "fetch_submissions",
        lambda *a, **k: [{"id": 1, "dateLastActivity": "2024-01-01 00:00:00"}],
    )
    monkeypatch.setattr(client_mod, "fetch_submissions_extended", lambda *a, **k: [])
    monkeypatch.setattr(client_mod, "fetch_all_publications", lambda *a, **k: [])
    monkeypatch.setattr(client_mod, "fetch_users", lambda *a, **k: [])

    r = CliRunner().invoke(cli.app, ["api", "fetch", "--no-stats"])
    assert r.exit_code == 0, r.output
    # Default full pull is unchanged behavior: no sync state is written.
    assert not (tmp_path / "sync_state.json").exists()


def test_api_fetch_full_and_incremental_conflict(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from ojs import cli

    _patch_api_env(tmp_path, monkeypatch)
    r = CliRunner().invoke(cli.app, ["api", "fetch", "--full", "--incremental"])
    assert r.exit_code != 0


def test_api_fetch_stats_since_does_not_window_publication_stats(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from ojs import cli
    from ojs.api import client as client_mod

    _patch_api_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        client_mod,
        "fetch_submissions",
        lambda *a, **k: [
            {"id": 1, "dateLastActivity": "2024-01-01 00:00:00", "publications": []}
        ],
    )
    monkeypatch.setattr(client_mod, "fetch_submissions_extended", lambda *a, **k: [])
    monkeypatch.setattr(client_mod, "fetch_all_publications", lambda *a, **k: [])
    monkeypatch.setattr(client_mod, "fetch_users", lambda *a, **k: [])

    captured: dict[str, Any] = {}

    def fake_pub_stats(base_url, api_key, *, date_start=None, date_end=None, **k):
        captured["date_start"] = date_start
        captured["date_end"] = date_end
        return [{"publication": {"id": 1}, "abstractViews": 5}]

    timeline_kwargs: dict[str, Any] = {}

    def fake_timelines(base_url, api_key, ids, **k):
        timeline_kwargs.update(k)
        return []

    monkeypatch.setattr(client_mod, "fetch_publication_stats", fake_pub_stats)
    monkeypatch.setattr(client_mod, "fetch_view_timelines", fake_timelines)
    monkeypatch.setattr(client_mod, "fetch_view_timeline_totals", lambda *a, **k: [])

    r = CliRunner().invoke(cli.app, ["api", "fetch", "--stats-since", "2024-06-01"])
    assert r.exit_code == 0, r.output
    # Cumulative publication stats are pulled in full -- --stats-since is ignored.
    assert captured == {"date_start": None, "date_end": None}
    # ...but the per-period timeline IS windowed by --stats-since.
    assert timeline_kwargs["date_start"] == "2024-06-01"


def test_api_fetch_skipped_stats_preserves_stats_window(tmp_path, monkeypatch):
    import json

    from typer.testing import CliRunner

    from ojs import cli
    from ojs.api import client as client_mod

    _patch_api_env(tmp_path, monkeypatch)
    # Prior state with a stats window anchor in the past.
    prior = "2026-01-01T00:00:00-07:00"
    (tmp_path / "sync_state.json").write_text(
        json.dumps(
            {
                "last_sync": prior,
                "stats_last_sync": prior,
                "submission_modified": {"1": "2024-01-01 00:00:00"},
            }
        )
    )

    monkeypatch.setattr(
        client_mod,
        "fetch_submissions",
        lambda *a, **k: [
            {"id": 1, "dateLastActivity": "2024-01-01 00:00:00", "publications": []}
        ],
    )
    monkeypatch.setattr(client_mod, "fetch_submissions_extended", lambda *a, **k: [])
    monkeypatch.setattr(client_mod, "fetch_all_publications", lambda *a, **k: [])
    monkeypatch.setattr(client_mod, "fetch_users", lambda *a, **k: [])

    def forbidden_stats(*a, **k):
        request = httpx.Request("GET", "http://base/api/v1/stats/publications")
        response = httpx.Response(403, request=request)
        raise httpx.HTTPStatusError("forbidden", request=request, response=response)

    monkeypatch.setattr(client_mod, "fetch_publication_stats", forbidden_stats)

    r = CliRunner().invoke(cli.app, ["api", "fetch", "--incremental"])
    assert r.exit_code == 0, r.output
    state = json.loads((tmp_path / "sync_state.json").read_text())
    # The run advanced last_sync, but the stats window anchor is preserved so the
    # days the 403 skipped are re-pulled next run rather than lost.
    assert state["last_sync"] != prior
    assert state["stats_last_sync"] == prior


def test_api_fetch_timeline_point_missing_date_does_not_abort(tmp_path, monkeypatch):
    import json

    from typer.testing import CliRunner

    from ojs import cli
    from ojs.api import client as client_mod

    _patch_api_env(tmp_path, monkeypatch)
    # Prior watermark so the incremental merge (which invokes the upsert key) runs.
    (tmp_path / "sync_state.json").write_text(
        json.dumps(
            {
                "last_sync": "2026-01-01T00:00:00-07:00",
                "stats_last_sync": "2026-01-01T00:00:00-07:00",
                "submission_modified": {"1": "2024-01-01 00:00:00"},
            }
        )
    )

    monkeypatch.setattr(
        client_mod,
        "fetch_submissions",
        lambda *a, **k: [
            {"id": 1, "dateLastActivity": "2024-06-01 00:00:00", "publications": []}
        ],
    )
    monkeypatch.setattr(client_mod, "fetch_submissions_extended", lambda *a, **k: [])
    monkeypatch.setattr(client_mod, "fetch_all_publications", lambda *a, **k: [])
    monkeypatch.setattr(client_mod, "fetch_users", lambda *a, **k: [])
    monkeypatch.setattr(
        client_mod,
        "fetch_publication_stats",
        lambda *a, **k: [{"publication": {"id": 1}, "abstractViews": 5}],
    )
    # A timeline point missing 'date' must not raise KeyError in the upsert key.
    monkeypatch.setattr(
        client_mod,
        "fetch_view_timelines",
        lambda *a, **k: [
            {"_submission_id": 1, "kind": "abstract", "value": 3, "interval": "day"}
        ],
    )
    monkeypatch.setattr(client_mod, "fetch_view_timeline_totals", lambda *a, **k: [])

    r = CliRunner().invoke(cli.app, ["api", "fetch", "--incremental"])
    assert r.exit_code == 0, r.output
    points = json.loads((tmp_path / "views_timeline.json").read_text())
    assert points and points[0]["value"] == 3


# --- Submission files: fetch, flatten, download, normalize -------------------


def test_stage_label_and_review_round_id():
    from ojs.api.files import review_round_id, stage_label

    assert stage_label(4) == "review_file"
    assert stage_label(11) == "production_ready"
    assert stage_label(999) == "stage_999"
    assert stage_label(None) == "stage_unknown"

    # assocType 521 == ASSOC_TYPE_REVIEW_ROUND -> assocId is the round id.
    assert review_round_id({"assocType": 521, "assocId": 7}) == 7
    # Any other assocType is not a review-round association.
    assert review_round_id({"assocType": 515, "assocId": 7}) is None
    assert review_round_id({}) is None


def test_download_targets_includes_revisions_and_dedupes():
    from ojs.api.files import download_targets

    file = {
        "_submission_id": 5,
        "id": 100,
        "fileId": 900,
        "fileStage": 4,
        "assocType": 521,
        "assocId": 3,
        "name": {"en_US": "paper draft.pdf"},
        "url": "http://base/files/900/download",
        "revisions": [
            {"fileId": 900, "url": "http://base/files/900/download"},  # dup of current
            {"fileId": 880, "url": "http://base/files/880/download"},
        ],
    }
    targets = list(download_targets(file))
    # 900 (current) + 880 (prior); the duplicate 900 revision is dropped.
    assert [t["file_id"] for t in targets] == [900, 880]
    assert targets[0]["stage"] == "review_file"
    assert targets[0]["review_round_id"] == 3
    # Filenames are prefixed by fileId and sanitized.
    assert targets[0]["filename"] == "900_paper_draft.pdf"


def test_download_targets_skips_missing_url_or_id_and_can_exclude_revisions():
    from ojs.api.files import download_targets

    file = {
        "_submission_id": 5,
        "id": 100,
        "fileId": 900,
        "fileStage": 2,
        "name": "x.pdf",
        "url": None,  # current has no URL -> nothing to fetch
        "revisions": [{"fileId": 880, "url": "http://base/880"}],
    }
    # Revisions excluded and current has no url -> no targets.
    assert list(download_targets(file, include_revisions=False)) == []
    # Revisions included -> only the revision with a url.
    targets = list(download_targets(file, include_revisions=True))
    assert [t["file_id"] for t in targets] == [880]


class _RecordingFileClient:
    """Records the file-list URLs and params it is asked to GET."""

    calls: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, params=None):
        type(self).calls.append({"url": url, "params": dict(params or {})})
        # The live endpoint returns the standard {items, itemsMax} envelope.
        return _FakeResponse({"items": [{"id": 1, "fileId": 2}], "itemsMax": 1})


def test_fetch_all_submission_files_skips_unchanged(monkeypatch):
    from ojs.api import client as client_mod

    _RecordingFileClient.calls = []
    monkeypatch.setattr(client_mod.httpx, "Client", _RecordingFileClient)

    submissions = [
        {"id": 1, "dateLastActivity": "2024-06-15 00:00:00"},
        {"id": 2, "dateLastActivity": "2024-07-01 00:00:00"},
    ]
    known = {1: "2024-06-15 00:00:00"}  # sub 1 unchanged
    files = client_mod.fetch_all_submission_files(
        "http://base", "key", submissions, known=known, file_stages=[4, 15]
    )

    urls = [c["url"] for c in _RecordingFileClient.calls]
    assert all("/submissions/1/files" not in u for u in urls)  # skipped
    assert any("/submissions/2/files" in u for u in urls)
    # Each returned record is tagged with its submission id.
    assert all(f["_submission_id"] == 2 for f in files)
    # Stage filter is forwarded as a comma-separated array param (style=form,
    # explode=false), matching the OJS spec.
    assert _RecordingFileClient.calls[0]["params"]["fileStages"] == "4,15"


def test_fetch_submission_files_unwraps_envelope_and_bare_array():
    from ojs.api import client as client_mod

    class _Client:
        def __init__(self, payload):
            self._payload = payload

        def get(self, url, params=None):
            return _FakeResponse(self._payload)

    # The live endpoint returns the standard {items, itemsMax} envelope.
    env = _Client({"items": [{"fileId": 9}, {"fileId": 10}], "itemsMax": 2})
    files = client_mod._fetch_submission_files(
        env, "http://base", "key", 42, file_stages=None, review_round_ids=None
    )
    assert [f["fileId"] for f in files] == [9, 10]
    assert all(f["_submission_id"] == 42 for f in files)

    # Defensive: a bare array (older swagger snapshots) still works.
    bare = _Client([{"fileId": 11}])
    files = client_mod._fetch_submission_files(
        bare, "http://base", "key", 7, file_stages=None, review_round_ids=None
    )
    assert files == [{"fileId": 11, "_submission_id": 7}]


class _DownloadResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass

    def json(self):  # pragma: no cover - not used by the downloader
        return None


class _DownloadClient:
    """Returns byte payloads keyed by URL and records what it fetched."""

    def __init__(self):
        self.fetched: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, params=None):
        self.fetched.append(url)
        return _DownloadResponse(b"PDFDATA:" + url.encode())


def test_download_files_lays_out_skips_and_records(monkeypatch, tmp_path):
    from ojs.api import files as files_mod

    client = _DownloadClient()
    monkeypatch.setattr(files_mod, "_http_client", lambda: client)

    files = [
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
    records, failed = files_mod.download_files(
        files, api_key="key", dest_dir=tmp_path, downloaded={}
    )

    assert failed == []
    assert len(records) == 1
    rec = records[0]
    assert rec["file_id"] == 900
    assert rec["path"] == "5/production_ready/900_final.pdf"
    dest = tmp_path / rec["path"]
    assert dest.read_bytes() == b"PDFDATA:http://base/files/900"
    assert client.fetched == ["http://base/files/900"]

    # Second run with the file already in the manifest and on disk -> skipped.
    again_records, again_failed = files_mod.download_files(
        files,
        api_key="key",
        dest_dir=tmp_path,
        downloaded={900: rec},
    )
    assert again_records == []
    assert again_failed == []
    assert client.fetched == ["http://base/files/900"]  # no new fetch


class _ForbiddenForClient:
    """Raises HTTPStatusError(status) for one URL substring, else returns bytes."""

    def __init__(self, deny_substr, status):
        self.deny_substr = deny_substr
        self.status = status
        self.fetched: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, params=None):
        self.fetched.append(url)
        if self.deny_substr in url:
            request = httpx.Request("GET", url)
            response = httpx.Response(self.status, request=request)
            raise httpx.HTTPStatusError("denied", request=request, response=response)
        return _DownloadResponse(b"PDFDATA:" + url.encode())


def test_download_files_skips_forbidden_and_continues(monkeypatch, tmp_path):
    from ojs.api import files as files_mod

    client = _ForbiddenForClient("/901", httpx.codes.FORBIDDEN)
    monkeypatch.setattr(files_mod, "_http_client", lambda: client)

    files = [
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
            "fileStage": 4,
            "name": "denied.pdf",
            "url": "http://base/files/901",
            "revisions": [],
        },
    ]
    records, failed = files_mod.download_files(
        files, api_key="key", dest_dir=tmp_path, downloaded={}
    )

    # The 403 file is skipped; the accessible file still downloads.
    assert [r["file_id"] for r in records] == [900]
    assert (tmp_path / "5/production_ready/900_ok.pdf").exists()
    assert not (tmp_path / "5/review_file/901_denied.pdf").exists()
    # Both were attempted -- the run did not abort on the 403.
    assert client.fetched == ["http://base/files/900", "http://base/files/901"]
    # The denied file is recorded with its status for the skip log.
    assert [(r["file_id"], r["status"]) for r in failed] == [(901, 403)]


def test_download_files_refetches_renamed_file(monkeypatch, tmp_path):
    from ojs.api import files as files_mod

    client = _DownloadClient()
    monkeypatch.setattr(files_mod, "_http_client", lambda: client)

    file_v1 = {
        "_submission_id": 5,
        "id": 100,
        "fileId": 900,
        "fileStage": 11,
        "name": "final.pdf",
        "url": "http://base/files/900",
        "revisions": [],
    }
    records, _ = files_mod.download_files(
        [file_v1], api_key="key", dest_dir=tmp_path, downloaded={}
    )
    rec = records[0]
    assert rec["path"] == "5/production_ready/900_final.pdf"
    downloaded = {900: rec}

    # Same name, already on disk -> skipped (no over-fetch).
    again, _ = files_mod.download_files(
        [file_v1], api_key="key", dest_dir=tmp_path, downloaded=downloaded
    )
    assert again == []

    # Renamed file (same fileId, new resolved dest) -> re-fetched to the new path,
    # and the now-orphaned prior artifact is deleted (no silent accumulation).
    file_v2 = {**file_v1, "name": "revised.pdf"}
    renamed, _ = files_mod.download_files(
        [file_v2], api_key="key", dest_dir=tmp_path, downloaded=downloaded
    )
    assert [r["path"] for r in renamed] == ["5/production_ready/900_revised.pdf"]
    assert (tmp_path / "5/production_ready/900_revised.pdf").exists()
    assert not (tmp_path / "5/production_ready/900_final.pdf").exists()


def test_download_files_persists_records_before_propagating_error(
    monkeypatch, tmp_path
):
    from ojs.api import files as files_mod

    # 500 is NOT a SKIP_STATUS, so it propagates out of download_files mid-batch.
    client = _ForbiddenForClient("/901", httpx.codes.INTERNAL_SERVER_ERROR)
    monkeypatch.setattr(files_mod, "_http_client", lambda: client)

    persisted: list[dict[str, Any]] = []
    files = [
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
            "fileStage": 4,
            "name": "boom.pdf",
            "url": "http://base/files/901",
            "revisions": [],
        },
    ]
    with pytest.raises(httpx.HTTPStatusError):
        files_mod.download_files(
            files,
            api_key="key",
            dest_dir=tmp_path,
            downloaded={},
            on_record=persisted.append,
        )
    # The first file's record was flushed before the second file's 500 propagated.
    assert [r["file_id"] for r in persisted] == [900]
    assert (tmp_path / "5/production_ready/900_ok.pdf").exists()


def test_api_download_cli_flushes_manifest_per_file_on_error(tmp_path, monkeypatch):
    # End-to-end: the `api download` CLI wires `_persist` as on_record, so a
    # mid-batch failure leaves manifest.json holding the already-written file
    # (atomically, with no temp file). The library-level test above stubs
    # on_record; this exercises the real CLI _persist -> upsert -> write_json path.
    import json

    from typer.testing import CliRunner

    from ojs import cli
    from ojs.api import files as files_mod

    _patch_api_env(tmp_path, monkeypatch)
    files_dir = tmp_path / "files"
    monkeypatch.setenv("OJS_FILES_DIR", str(files_dir))

    (tmp_path / "submission_files.json").write_text(
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
                    "name": "boom.pdf",
                    "url": "http://base/files/901",
                    "revisions": [],
                },
            ]
        )
    )
    # 500 is not a SKIP_STATUS, so it propagates out of the CLI command.
    client = _ForbiddenForClient("/901", httpx.codes.INTERNAL_SERVER_ERROR)
    monkeypatch.setattr(files_mod, "_http_client", lambda: client)

    r = CliRunner().invoke(cli.app, ["api", "download", "--no-fetch", "-s", "5"])
    assert r.exit_code != 0  # the 500 propagated out of the run

    manifest = json.loads((files_dir / "manifest.json").read_text())
    assert [rec["file_id"] for rec in manifest] == [900]
    assert (files_dir / "5/production_ready/900_ok.pdf").exists()
    # The atomic manifest write left no stray temp file behind.
    assert [p.name for p in files_dir.iterdir() if p.name.startswith(".")] == []


def test_download_files_orphan_cleanup_refuses_path_escape(monkeypatch, tmp_path):
    # A tampered/corrupt manifest `path` that escapes dest_dir must NOT turn the
    # rename-orphan cleanup into arbitrary file deletion outside the tree.
    from ojs.api import files as files_mod

    client = _DownloadClient()
    monkeypatch.setattr(files_mod, "_http_client", lambda: client)

    dest_dir = tmp_path / "files"
    dest_dir.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("do not delete")

    file_v2 = {
        "_submission_id": 5,
        "id": 100,
        "fileId": 900,
        "fileStage": 11,
        "name": "revised.pdf",
        "url": "http://base/files/900",
        "revisions": [],
    }
    downloaded = {900: {"file_id": 900, "path": "../outside.txt"}}
    records, _ = files_mod.download_files(
        [file_v2], api_key="key", dest_dir=dest_dir, downloaded=downloaded
    )
    assert [r["path"] for r in records] == ["5/production_ready/900_revised.pdf"]
    # Cleanup refused the traversal path -- the outside file survives.
    assert outside.exists() and outside.read_text() == "do not delete"


def test_download_files_refuses_write_outside_dest(monkeypatch, tmp_path):
    # A corrupt submission_id with path traversal must be refused before any
    # fetch or write, so a write can't escape dest_dir.
    from ojs.api import files as files_mod

    client = _DownloadClient()
    monkeypatch.setattr(files_mod, "_http_client", lambda: client)

    dest_dir = tmp_path / "files"
    dest_dir.mkdir()
    evil = {
        "_submission_id": "../../escape",
        "id": 100,
        "fileId": 900,
        "fileStage": 11,
        "name": "x.pdf",
        "url": "http://base/files/900",
        "revisions": [],
    }
    with pytest.raises(ValueError):
        files_mod.download_files(
            [evil], api_key="key", dest_dir=dest_dir, downloaded={}
        )
    assert client.fetched == []  # refused before any network/write happened


def test_normalize_submission_files_maps_fields():
    from ojs.api.normalize import normalize_submission_files

    files = [
        {
            "_submission_id": 5,
            "id": 100,
            "fileId": 900,
            "fileStage": 15,
            "assocType": 521,
            "assocId": 3,
            "genreId": 1,
            "name": {"en_US": "revision.pdf"},
            "mimetype": "application/pdf",
            "documentType": "pdf",
            "uploaderUserId": 42,
            "sourceSubmissionFileId": 80,
            "revisions": [{"fileId": 880}],
            "createdAt": "2024-01-01 00:00:00",
            "updatedAt": "2024-02-01 00:00:00",
            "url": "http://base/files/900",
        }
    ]
    df = normalize_submission_files(files)
    assert df.height == 1
    row = df.row(0, named=True)
    assert row["submission_id"] == 5
    assert row["submission_file_id"] == 100
    assert row["file_id"] == 900
    assert row["file_stage_label"] == "review_revision"
    assert row["review_round_id"] == 3
    assert row["revision_count"] == 1
    assert row["name"] == "revision.pdf"


def test_normalize_submission_files_empty():
    from ojs.api.normalize import normalize_submission_files

    df = normalize_submission_files(None)
    assert df.height == 0
    assert "review_round_id" in df.columns


# --- CLI init .env quoting & download selection ------------------------------


def test_env_quote_roundtrips_special_values():
    import io

    from dotenv import dotenv_values

    from ojs.cli import _env_quote

    # A space-delimited '#' (the reported bug), embedded quotes, backslashes, and
    # surrounding whitespace must all read back verbatim through python-dotenv.
    for value in ["abc # note", 'has"quote', "back\\slash", "  spaced  "]:
        line = f"OJS_X={_env_quote(value)}"
        assert dotenv_values(stream=io.StringIO(line))["OJS_X"] == value


def test_init_quotes_env_values_with_inline_comment_marker(tmp_path, monkeypatch):
    from dotenv import dotenv_values
    from typer.testing import CliRunner

    from ojs import cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OJS_API_KEY", "abc # note")
    r = CliRunner().invoke(
        cli.app,
        ["init", "--base-url", "https://ojs.example.org"],
    )
    assert r.exit_code == 0, r.output
    values = dotenv_values(tmp_path / ".env")
    # Without quoting, python-dotenv would truncate the key at " #".
    assert values["OJS_API_KEY"] == "abc # note"
    assert values["OJS_BASE_URL"] == "https://ojs.example.org"


def test_init_env_file_is_owner_only(tmp_path, monkeypatch):
    import stat
    import sys

    from typer.testing import CliRunner

    from ojs import cli

    if sys.platform == "win32":  # pragma: no cover - POSIX permission semantics
        return
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OJS_API_KEY", "secret")
    r = CliRunner().invoke(
        cli.app,
        ["init", "--base-url", "https://ojs.example.org"],
    )
    assert r.exit_code == 0, r.output
    # The .env holds the API key -> owner-only, never world-readable.
    assert stat.S_IMODE((tmp_path / ".env").stat().st_mode) == 0o600


def test_init_force_tightens_existing_world_readable_env(tmp_path, monkeypatch):
    import stat
    import sys

    from typer.testing import CliRunner

    from ojs import cli

    if sys.platform == "win32":  # pragma: no cover - POSIX permission semantics
        return
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OJS_API_KEY", "new")
    env_path = tmp_path / ".env"
    env_path.write_text("OJS_API_KEY=old\n")
    env_path.chmod(0o644)  # a pre-fix, world-readable .env
    r = CliRunner().invoke(cli.app, ["init", "--force", "--base-url", "https://x.org"])
    assert r.exit_code == 0, r.output
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600


def test_init_has_no_api_key_flag(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from ojs import cli

    # The API key is never accepted on argv (it would leak into shell history and
    # the process list); it comes only from OJS_API_KEY or the hidden prompt.
    monkeypatch.chdir(tmp_path)
    rejected = CliRunner().invoke(
        cli.app, ["init", "--base-url", "https://x.org", "--api-key", "x"]
    )
    assert rejected.exit_code != 0
    assert "--api-key" not in CliRunner().invoke(cli.app, ["init", "--help"]).output


def test_init_falls_back_to_env_api_key_without_prompting(tmp_path, monkeypatch):
    from dotenv import dotenv_values
    from typer.testing import CliRunner

    from ojs import cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OJS_API_KEY", "from-env")
    # No --api-key and no stdin: the env value is used instead of prompting.
    r = CliRunner().invoke(cli.app, ["init", "--base-url", "https://x.org"])
    assert r.exit_code == 0, r.output
    assert dotenv_values(tmp_path / ".env")["OJS_API_KEY"] == "from-env"


def test_init_falls_back_to_env_base_url_without_prompting(tmp_path, monkeypatch):
    from dotenv import dotenv_values
    from typer.testing import CliRunner

    from ojs import cli

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OJS_BASE_URL", "https://from-env.org")
    monkeypatch.setenv("OJS_API_KEY", "k")
    # Mirror of the api-key fallback: OJS_BASE_URL from the env skips the prompt.
    r = CliRunner().invoke(cli.app, ["init"])
    assert r.exit_code == 0, r.output
    assert dotenv_values(tmp_path / ".env")["OJS_BASE_URL"] == "https://from-env.org"


def test_init_force_without_api_key_reuses_env_key(tmp_path, monkeypatch):
    from dotenv import dotenv_values
    from typer.testing import CliRunner

    from ojs import cli

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text('OJS_API_KEY="old"\n')
    monkeypatch.setenv("OJS_API_KEY", "from-env")
    # --force regen without --api-key uses the env value (no prompt), per the
    # documented env-before-prompt fallback.
    r = CliRunner().invoke(cli.app, ["init", "--force", "--base-url", "https://x.org"])
    assert r.exit_code == 0, r.output
    assert dotenv_values(tmp_path / ".env")["OJS_API_KEY"] == "from-env"


def test_env_value_with_braces_roundtrips_without_interpolation():
    import io

    from dotenv import dotenv_values

    from ojs.cli import _env_quote

    # cli.py loads .env with interpolate=False, so a value containing ${VAR} reads
    # back verbatim rather than being expanded against the environment.
    line = f"OJS_API_KEY={_env_quote('a${HOME}b')}"
    got = dotenv_values(stream=io.StringIO(line), interpolate=False)["OJS_API_KEY"]
    assert got == "a${HOME}b"


def test_env_example_template_has_no_secret():
    from pathlib import Path

    from dotenv import dotenv_values

    example = Path(__file__).resolve().parent.parent / ".env.example"
    assert example.exists()
    values = dotenv_values(example)
    # Documents the vars but ships no real secret.
    assert "OJS_API_KEY" in values
    assert not values["OJS_API_KEY"]
    assert not values["OJS_BASE_URL"]


def test_api_download_no_fetch_unfetched_id_errors(tmp_path, monkeypatch):
    import json

    from typer.testing import CliRunner

    from ojs import cli

    _patch_api_env(tmp_path, monkeypatch)
    monkeypatch.setenv("OJS_FILES_DIR", str(tmp_path / "files"))
    (tmp_path / "submission_files.json").write_text(
        json.dumps([{"_submission_id": 5, "id": 1, "fileId": 9, "fileStage": 11}])
    )
    # Requested id 999 was never fetched -> a distinct, actionable error (exit 1),
    # not a silent "0 files selected".
    r = CliRunner().invoke(cli.app, ["api", "download", "--no-fetch", "-s", "999"])
    assert r.exit_code == 1
    assert "999" in r.output
    assert "--fetch" in r.output


def test_api_download_no_fetch_partial_missing_warns(tmp_path, monkeypatch):
    import json

    from typer.testing import CliRunner

    from ojs import cli

    _patch_api_env(tmp_path, monkeypatch)
    monkeypatch.setenv("OJS_FILES_DIR", str(tmp_path / "files"))
    (tmp_path / "submission_files.json").write_text(
        json.dumps([{"_submission_id": 5, "id": 1, "fileId": 9, "fileStage": 11}])
    )
    # id 5 is present, id 999 is missing; filtering to a stage no record has means
    # nothing actually downloads (no network), but the run still warns and exits 0.
    r = CliRunner().invoke(
        cli.app,
        ["api", "download", "--no-fetch", "-s", "5", "-s", "999", "--file-stage", "99"],
    )
    assert r.exit_code == 0, r.output
    assert "999" in r.output  # warned about the missing id
