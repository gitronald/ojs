"""OJS REST API client with pagination and publication detail fetching."""

import re
import time
from typing import Any, Protocol

import httpx

PAGE_SIZE = 100
MAX_RETRIES = 3
RETRY_DELAY = 2

# HTTP statuses that mean "this resource isn't available to us" rather than a
# real failure: the key lacks permission (403) or the resource is gone (404).
# Callers skip the affected item and keep going instead of aborting the run.
SKIP_STATUSES = (403, 404)


def _is_unchanged(sub: dict[str, Any], known: dict[int, str]) -> bool:
    """True when `sub`'s `dateLastActivity` matches the last-seen value in `known`.

    The shared early-stop test for the per-submission detail endpoints: a match
    means nothing changed since the previous sync, so the round-trip is skipped.
    A submission absent from `known` (brand new) is never treated as unchanged.
    """
    prior = known.get(sub["id"])
    return prior is not None and prior == sub.get("dateLastActivity")


class _Response(Protocol):
    """The subset of `httpx.Response` the client helpers rely on."""

    def raise_for_status(self) -> object: ...
    def json(self) -> Any: ...

    # Raw bytes, used by the file downloader (`files.py`) to write artifacts.
    @property
    def content(self) -> bytes: ...


class _HttpClient(Protocol):
    """A GET-capable client, satisfied by `httpx.Client` and test doubles alike.

    The helpers below only need `.get(url, params=...)`, so typing against this
    structural contract (rather than the concrete `httpx.Client`) lets tests pass
    lightweight fakes without subclassing the real client.
    """

    def get(self, url: str, *, params: dict[str, Any] | None = ...) -> _Response: ...


def _http_client() -> httpx.Client:
    """An httpx client configured the way every fetch in this module wants it.

    Centralizes the redirect-following and timeout settings so the JSON helpers
    and the binary file downloader (``files.py``) share one definition.
    """
    return httpx.Client(follow_redirects=True, timeout=30)


def _redact_token(text: str) -> str:
    """Redact the ``apiToken`` query value so the API key never reaches logs.

    The key is sent as a query parameter, so httpx echoes the full URL --
    ``?apiToken=<secret>`` and all -- in ``HTTPStatusError`` messages. Scrubbing
    the value keeps an unexpected HTTP error from printing the key to the console
    or a CI log.
    """
    return re.sub(r"(apiToken=)[^&\s'\")]+", r"\1[REDACTED]", text)


def _request_with_retry(
    client: _HttpClient, url: str, params: dict[str, Any]
) -> _Response:
    """Make a GET request with retry on transient failures."""
    for attempt in range(MAX_RETRIES):
        try:
            response = client.get(url, params=params)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as e:
            # An HTTP status error carries the request URL -- with the apiToken
            # query value -- in its message. Re-raise a status-equivalent error
            # with the token redacted so it can't leak to the console/logs;
            # callers still branch on ``e.response.status_code`` for 403/404 skips.
            raise httpx.HTTPStatusError(
                _redact_token(str(e)), request=e.request, response=e.response
            ) from None
        except httpx.TransportError as e:
            # TransportError covers connect/read/pool timeouts and protocol
            # errors -- the transient failures worth retrying. HTTP status
            # errors (4xx/5xx) are not transport errors and propagate.
            if attempt == MAX_RETRIES - 1:
                raise
            delay = RETRY_DELAY * (attempt + 1)
            cls = e.__class__.__name__
            print(
                f"    Retry {attempt + 1}/{MAX_RETRIES} after {cls}, "
                f"waiting {delay}s..."
            )
            time.sleep(delay)
    raise RuntimeError("unreachable")


def _unwrap_items(data: Any, url: str) -> tuple[list[dict[str, Any]], int | None]:
    """Split an OJS list response into ``(page, total)``.

    Accepts the two documented shapes -- a bare array (``total`` ``None``) and the
    ``{items, itemsMax}`` envelope (``itemsMax`` read with ``.get`` so an envelope
    missing only the count still paginates by short-page detection). Any other 200
    JSON (e.g. an OJS error object that slipped past ``raise_for_status``) raises a
    ``RuntimeError`` surfacing the URL and payload instead of a bare ``KeyError``.
    """
    if isinstance(data, list):
        return data, None
    if isinstance(data, dict) and "items" in data:
        return data["items"], data.get("itemsMax")
    raise RuntimeError(f"Unexpected response shape from {url}: {repr(data)[:300]}")


def _paginate(
    client: _HttpClient,
    url: str,
    params: dict[str, Any],
    *,
    since: str | None = None,
    since_key: str = "dateLastActivity",
) -> list[dict[str, Any]]:
    """Fetch all pages from a paginated OJS API endpoint.

    Most list endpoints return an `{items, itemsMax}` envelope. Some (the swagger
    snapshot documents `/stats/publications` this way) return a bare array; that
    is handled too -- a short page ends pagination, a full page advances offset.

    When `since` is set the caller must also request newest-first ordering on
    `since_key` (`orderBy=...&orderDirection=DESC`). Pagination then early-stops:
    the first item at or below `since` ends the walk, since DESC order guarantees
    every later record is older. Items missing `since_key` are kept (err toward
    refetch). With `since` None the behavior is a plain full pull.
    """
    all_items: list[dict[str, Any]] = []
    offset = 0

    while True:
        params["count"] = PAGE_SIZE
        params["offset"] = offset
        response = _request_with_retry(client, url, params)
        data = response.json()

        page, total = _unwrap_items(data, url)

        reached_since = False
        if since is not None:
            kept = []
            for item in page:
                ts = item.get(since_key)
                if ts is not None and ts <= since:
                    reached_since = True
                    break
                kept.append(item)
            all_items.extend(kept)
        else:
            all_items.extend(page)

        if total is None:
            print(f"  Fetched {len(all_items)}")
        else:
            print(f"  Fetched {len(all_items)}/{total}")

        if reached_since:
            break
        if total is None:
            if len(page) < PAGE_SIZE:
                break
        elif len(all_items) >= total or not page:
            break
        offset += PAGE_SIZE

    return all_items


def _submission_list_params(api_key: str, since: str | None) -> dict[str, Any]:
    """Query params for a submission list call, ordered for early-stop when incremental.

    With `since` set, page newest-first by `dateLastActivity` so `_paginate` can
    stop at the watermark. Both `/submissions` and `/_submissions` accept this
    ordering.
    """
    params = {"apiToken": api_key}
    if since is not None:
        params["orderBy"] = "dateLastActivity"
        params["orderDirection"] = "DESC"
    return params


def fetch_submissions(
    base_url: str, api_key: str, *, since: str | None = None
) -> list[dict[str, Any]]:
    """Fetch submissions from /submissions.

    With `since` set, returns only submissions whose `dateLastActivity` is newer
    than the watermark (incremental delta); with `since` None, a full pull.
    """
    print("Fetching submissions...")
    with _http_client() as client:
        url = f"{base_url}/api/v1/submissions"
        return _paginate(
            client, url, _submission_list_params(api_key, since), since=since
        )


def fetch_submissions_extended(
    base_url: str, api_key: str, *, since: str | None = None
) -> list[dict[str, Any]]:
    """Fetch submissions from /_submissions (includes review data).

    Honors `since` for an incremental delta, the same way as `fetch_submissions`.
    """
    print("Fetching extended submissions (with review data)...")
    with _http_client() as client:
        url = f"{base_url}/api/v1/_submissions"
        return _paginate(
            client, url, _submission_list_params(api_key, since), since=since
        )


def _fetch_publication(
    client: httpx.Client,
    base_url: str,
    api_key: str,
    submission_id: int,
    publication_id: int,
) -> dict[str, Any]:
    """Fetch a publication's detail on an open client, tagged with submission id."""
    url = f"{base_url}/api/v1/submissions/{submission_id}/publications/{publication_id}"
    response = _request_with_retry(client, url, {"apiToken": api_key})
    pub = response.json()
    pub["_submission_id"] = submission_id
    return pub


def fetch_publication(
    base_url: str, api_key: str, submission_id: int, publication_id: int
) -> dict[str, Any]:
    """Fetch a single submission's full publication detail (one-off request)."""
    with _http_client() as client:
        return _fetch_publication(
            client, base_url, api_key, submission_id, publication_id
        )


def fetch_all_publications(
    base_url: str,
    api_key: str,
    submissions: list[dict[str, Any]],
    *,
    known: dict[int, str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch full publication details for the given submissions.

    `known` maps submission_id -> last-seen `dateLastActivity`. When a
    submission's current `dateLastActivity` matches its `known` value, the detail
    GET is skipped as unchanged -- the biggest incremental saving, since this
    endpoint costs one HTTP round-trip per submission. Brand-new submissions
    (absent from `known`) are always fetched.
    """
    known = known or {}
    print(f"Fetching full publication details for {len(submissions)} submissions...")
    publications = []
    skipped = 0

    with _http_client() as client:
        for i, sub in enumerate(submissions):
            pubs = sub.get("publications", [])
            if not pubs:
                continue

            if _is_unchanged(sub, known):
                skipped += 1
                continue

            pub = _fetch_publication(
                client, base_url, api_key, sub["id"], pubs[0]["id"]
            )
            publications.append(pub)

            if (i + 1) % 50 == 0:
                print(f"  Fetched {i + 1}/{len(submissions)} publications")

    if skipped:
        print(f"  Skipped {skipped} unchanged publications")
    print(f"  Fetched {len(publications)}/{len(submissions)} publications")
    return publications


def _submission_files_url(base_url: str, submission_id: int) -> str:
    return f"{base_url}/api/v1/submissions/{submission_id}/files"


def _file_query_params(
    api_key: str,
    *,
    file_stages: list[int] | None,
    review_round_ids: list[int] | None,
) -> dict[str, Any]:
    """Query params for the files list endpoint, dropping unset filters.

    `fileStages` and `reviewRoundIds` are `style=form, explode=false` array
    params in the OJS spec, i.e. comma-separated single values (`fileStages=4,15`)
    -- the same encoding `_stats_params` uses for `submissionIds`.
    """
    params: dict[str, Any] = {"apiToken": api_key}
    if file_stages:
        params["fileStages"] = ",".join(str(s) for s in file_stages)
    if review_round_ids:
        params["reviewRoundIds"] = ",".join(str(r) for r in review_round_ids)
    return params


def _fetch_submission_files(
    client: _HttpClient,
    base_url: str,
    api_key: str,
    submission_id: int,
    *,
    file_stages: list[int] | None,
    review_round_ids: list[int] | None,
) -> list[dict[str, Any]]:
    """Fetch one submission's files on an open client, tagging each with its id.

    The endpoint returns the standard `{items, itemsMax}` envelope (older swagger
    snapshots document a bare array; both are accepted). It is scoped to a single
    submission, which is never expected to carry a full page of files, so this
    does not paginate.
    """
    url = _submission_files_url(base_url, submission_id)
    params = _file_query_params(
        api_key, file_stages=file_stages, review_round_ids=review_round_ids
    )
    payload = _request_with_retry(client, url, params).json()
    files, _ = _unwrap_items(payload, url)
    for f in files:
        f["_submission_id"] = submission_id
    return files


def fetch_submission_files(
    base_url: str,
    api_key: str,
    submission_id: int,
    *,
    file_stages: list[int] | None = None,
    review_round_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Fetch the file metadata for a single submission (one-off request)."""
    with _http_client() as client:
        return _fetch_submission_files(
            client,
            base_url,
            api_key,
            submission_id,
            file_stages=file_stages,
            review_round_ids=review_round_ids,
        )


def fetch_all_submission_files(
    base_url: str,
    api_key: str,
    submissions: list[dict[str, Any]],
    *,
    known: dict[int, str] | None = None,
    file_stages: list[int] | None = None,
    review_round_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Fetch file metadata for the given submissions, one request each.

    `known` maps submission_id -> last-seen `dateLastActivity`. A submission
    whose current `dateLastActivity` matches its `known` value is skipped as
    unchanged -- the same incremental saving used by `fetch_all_publications`,
    since this endpoint costs one HTTP round-trip per submission. New
    submissions (absent from `known`) are always fetched.
    """
    known = known or {}
    print(f"Fetching submission files for {len(submissions)} submissions...")
    files: list[dict[str, Any]] = []
    skipped = 0

    with _http_client() as client:
        for i, sub in enumerate(submissions):
            if _is_unchanged(sub, known):
                skipped += 1
                continue

            files.extend(
                _fetch_submission_files(
                    client,
                    base_url,
                    api_key,
                    sub["id"],
                    file_stages=file_stages,
                    review_round_ids=review_round_ids,
                )
            )

            if (i + 1) % 50 == 0:
                print(f"  Fetched files for {i + 1}/{len(submissions)} submissions")

    if skipped:
        print(f"  Skipped {skipped} unchanged submissions")
    print(f"  Fetched {len(files)} file records")
    return files


def fetch_users(base_url: str, api_key: str) -> list[dict[str, Any]]:
    """Fetch all users from /users endpoint."""
    print("Fetching users...")
    with _http_client() as client:
        url = f"{base_url}/api/v1/users"
        return _paginate(client, url, {"apiToken": api_key})


def _stats_params(
    api_key: str,
    *,
    date_start: str | None = None,
    date_end: str | None = None,
    submission_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Build a query-param dict for the /stats endpoints, dropping unset filters."""
    params = {"apiToken": api_key}
    if date_start:
        params["dateStart"] = date_start
    if date_end:
        params["dateEnd"] = date_end
    if submission_ids:
        # OJS expects a comma-separated list (style=form, explode=false).
        params["submissionIds"] = ",".join(str(s) for s in submission_ids)
    return params


def fetch_publication_stats(
    base_url: str,
    api_key: str,
    *,
    date_start: str | None = None,
    date_end: str | None = None,
    submission_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Fetch per-publication view totals from /stats/publications.

    Returns one record per publication with abstract, all-galley, PDF, HTML, and
    other view counts. `_paginate` tolerates both the `{items, itemsMax}`
    envelope and a bare-array response from this endpoint.
    """
    print("Fetching publication view stats...")
    params = _stats_params(
        api_key,
        date_start=date_start,
        date_end=date_end,
        submission_ids=submission_ids,
    )
    with _http_client() as client:
        url = f"{base_url}/api/v1/stats/publications"
        return _paginate(client, url, params)


def _request_timeline(
    client: httpx.Client,
    base_url: str,
    api_key: str,
    submission_id: int,
    kind: str,
    *,
    interval: str,
    date_start: str | None,
    date_end: str | None,
) -> list[dict[str, Any]]:
    """Fetch one publication's abstract- or galley-view timeline.

    Returns a flat list of `{date, label, value}` points for the requested
    submission. These per-publication endpoints are not paginated.
    """
    params = _stats_params(api_key, date_start=date_start, date_end=date_end)
    params["timelineInterval"] = interval
    url = f"{base_url}/api/v1/stats/publications/{submission_id}/{kind}"
    response = _request_with_retry(client, url, params)
    return response.json()


def fetch_view_timelines(
    base_url: str,
    api_key: str,
    submission_ids: list[int],
    *,
    interval: str = "day",
    date_start: str | None = None,
    date_end: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch per-submission abstract and galley view timelines.

    Makes one request per submission per view type and returns a flat list of
    timeline points, each tagged with `_submission_id`, `kind`, and `interval`
    (the granularity it was fetched at), ready for normalization into a long
    table. Defaults to daily resolution, the finest granularity the OJS stats API
    exposes.
    """
    total = len(submission_ids)
    print(f"Fetching view timelines for {total} submissions ({interval})...")
    points = []

    with _http_client() as client:
        for i, submission_id in enumerate(submission_ids):
            for kind in ("abstract", "galley"):
                series = _request_timeline(
                    client,
                    base_url,
                    api_key,
                    submission_id,
                    kind,
                    interval=interval,
                    date_start=date_start,
                    date_end=date_end,
                )
                for point in series:
                    points.append(
                        {
                            **point,
                            "_submission_id": submission_id,
                            "kind": kind,
                            "interval": interval,
                        }
                    )

            if (i + 1) % 50 == 0:
                print(f"  Fetched {i + 1}/{total} submission timelines")

    # Final tally, unless the in-loop print just emitted it.
    if total and total % 50 != 0:
        print(f"  Fetched {total}/{total} submission timelines")
    return points


def fetch_view_timeline_totals(
    base_url: str,
    api_key: str,
    *,
    interval: str = "day",
    date_start: str | None = None,
    date_end: str | None = None,
    submission_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Fetch journal-wide abstract and galley view timelines.

    Hits the aggregate `/stats/publications/abstract` and
    `/stats/publications/galley` endpoints (no submission id) -- the same data
    the OJS statistics page graphs by month over the full span. Returns a flat
    list of timeline points, each tagged with `kind` (`abstract` or `galley`)
    and `interval` (the granularity), ready to normalize into a journal-wide
    totals table.
    """
    print(f"Fetching journal-wide view timeline totals ({interval})...")
    params = _stats_params(
        api_key,
        date_start=date_start,
        date_end=date_end,
        submission_ids=submission_ids,
    )
    params["timelineInterval"] = interval
    points = []
    with _http_client() as client:
        for kind in ("abstract", "galley"):
            url = f"{base_url}/api/v1/stats/publications/{kind}"
            series = _request_with_retry(client, url, params).json()
            for point in series:
                points.append({**point, "kind": kind, "interval": interval})
    return points
