"""Tests for the website report-fetch module and the `fetch` CLI commands."""

import httpx
import pytest

from ojs.website import reports
from ojs.website.reports import (
    ReportAuthError,
    extract_csrf_token,
    fetch_report,
    login,
    resolve_report_url,
)

# A realistic OJS login page: a hidden CSRF token plus username and password
# fields. `_is_login_html` keys off the two form fields; `extract_csrf_token`
# off the hidden input.
LOGIN_HTML = """<!DOCTYPE html>
<html><body>
<form method="post" action="https://ojs.example.org/index.php/j/login/signIn">
  <input type="hidden" name="csrfToken" value="TOK123">
  <input type="text" name="username" value="">
  <input type="password" name="password" value="">
  <button type="submit">Login</button>
</form>
</body></html>"""

# The post-login landing page (no login form) -- a successful sign-in.
DASHBOARD_HTML = "<!DOCTYPE html><html><body>Editorial dashboard</body></html>"

REPORT_CSV = "Submission ID,Round,Reviewer\n1,1,alice\n2,1,bob\n"


class _Resp:
    """Minimal stand-in for httpx.Response (text/content/raise_for_status)."""

    def __init__(
        self, text: str = "", *, content: bytes | None = None, status: int = 200
    ):
        self._text = text
        self._content = content if content is not None else text.encode()
        self._status = status

    @property
    def text(self) -> str:
        return self._text

    @property
    def content(self) -> bytes:
        return self._content

    def raise_for_status(self) -> object:
        if self._status >= 400:
            request = httpx.Request("GET", "http://x")
            response = httpx.Response(self._status, request=request)
            raise httpx.HTTPStatusError("err", request=request, response=response)
        return self


class _FlowClient:
    """Fakes the whole login+fetch flow on one client (as httpx would).

    Records the GET urls and POST payloads so tests can assert what was sent, and
    returns the login page for the `/login` GET, `post_resp` for the sign-in POST,
    and `report_resp` for the report GET -- exercising that a single client carries
    the session from login into the report download.
    """

    def __init__(self, *, login_html=LOGIN_HTML, post_resp=None, report_resp=None):
        self.login_html = login_html
        self.post_resp = post_resp if post_resp is not None else _Resp(DASHBOARD_HTML)
        self.report_resp = report_resp if report_resp is not None else _Resp(REPORT_CSV)
        self.gets: list[str] = []
        self.posts: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url):
        self.gets.append(url)
        if url.endswith("/login"):
            return _Resp(self.login_html)
        return self.report_resp

    def post(self, url, *, data):
        self.posts.append({"url": url, "data": dict(data)})
        return self.post_resp


# --- resolve_report_url -------------------------------------------------------


def test_resolve_report_url_absolute_passthrough():
    url = "https://ojs.example.org/index.php/j/management/tools/report"
    assert resolve_report_url("https://ojs.example.org/index.php/j", url) == url


def test_resolve_report_url_joins_relative_path():
    assert (
        resolve_report_url("https://host/index.php/j/", "management/tools/report")
        == "https://host/index.php/j/management/tools/report"
    )


# --- extract_csrf_token -------------------------------------------------------


def test_extract_csrf_token_hidden_input():
    assert extract_csrf_token(LOGIN_HTML) == "TOK123"


def test_extract_csrf_token_value_before_name():
    html = '<input value="ABC" type="hidden" name="csrfToken">'
    assert extract_csrf_token(html) == "ABC"


def test_extract_csrf_token_js_variable():
    html = "<script>var x = {csrfToken: 'JS_TOKEN', other: 1};</script>"
    assert extract_csrf_token(html) == "JS_TOKEN"


def test_extract_csrf_token_absent_returns_none():
    assert extract_csrf_token("<html><body>no token here</body></html>") is None


# --- login --------------------------------------------------------------------


def test_login_posts_credentials_and_csrf():
    client = _FlowClient()
    login(client, "https://host/index.php/j", "editor", "s3cret")

    # GET the login page, then POST to the sign-in route on the same client.
    assert client.gets == ["https://host/index.php/j/login"]
    assert client.posts[0]["url"] == "https://host/index.php/j/login/signIn"
    sent = client.posts[0]["data"]
    assert sent["username"] == "editor"
    assert sent["password"] == "s3cret"
    # The CSRF token scraped from the login page is echoed back.
    assert sent["csrfToken"] == "TOK123"


def test_login_omits_csrf_when_absent():
    client = _FlowClient(login_html="<html><input name='username'></html>")
    login(client, "https://host/index.php/j", "editor", "pw")
    assert "csrfToken" not in client.posts[0]["data"]


def test_login_bad_credentials_raises():
    # OJS re-renders the login form (HTTP 200) on bad credentials.
    client = _FlowClient(post_resp=_Resp(LOGIN_HTML))
    with pytest.raises(ReportAuthError) as exc:
        login(client, "https://host/index.php/j", "editor", "wrong")
    assert "OJS_USERNAME" in str(exc.value)


# --- fetch_report -------------------------------------------------------------


class _SingleGet:
    """A client whose GET always returns one staged response."""

    def __init__(self, resp):
        self.resp = resp

    def get(self, url):
        return self.resp

    def post(self, url, *, data):  # pragma: no cover - not used here
        raise AssertionError("post not expected")


def test_fetch_report_returns_csv_bytes():
    content = fetch_report(_SingleGet(_Resp(REPORT_CSV)), "http://host/report")
    assert content == REPORT_CSV.encode()


def test_fetch_report_strips_bom_before_sniff():
    # A BOM-led CSV is still recognized as CSV (not mistaken for HTML).
    bom_csv = "﻿Submission ID,Round\n1,1\n"
    content = fetch_report(_SingleGet(_Resp(bom_csv)), "http://host/report")
    assert content == bom_csv.encode()


def test_fetch_report_login_page_raises():
    with pytest.raises(ReportAuthError) as exc:
        fetch_report(_SingleGet(_Resp(LOGIN_HTML)), "http://host/report")
    assert "login page" in str(exc.value)


def test_fetch_report_generic_html_raises():
    html = "<!DOCTYPE html><html><body>Server error</body></html>"
    with pytest.raises(ReportAuthError) as exc:
        fetch_report(_SingleGet(_Resp(html)), "http://host/report")
    assert "HTML" in str(exc.value)


# --- download_report ----------------------------------------------------------


def test_download_report_writes_csv_atomically(tmp_path, monkeypatch):
    client = _FlowClient()
    monkeypatch.setattr(reports.httpx, "Client", lambda **kwargs: client)

    dest = tmp_path / "reviews-20260101.csv"
    result = reports.download_report(
        base_url="https://host/index.php/j",
        username="editor",
        password="pw",
        report_url="https://host/index.php/j/report",
        dest=dest,
    )
    assert result == dest
    assert dest.read_text() == REPORT_CSV
    # The report GET happened after login on the same client.
    assert client.gets[-1] == "https://host/index.php/j/report"
    # Atomic write leaves no stray temp file behind.
    assert [p.name for p in tmp_path.iterdir()] == ["reviews-20260101.csv"]


# --- CLI: reviews fetch / articles fetch --------------------------------------


def _patch_website_env(tmp_path, monkeypatch, *, report_var, url):
    monkeypatch.setenv("OJS_BASE_URL", "https://host/index.php/j")
    monkeypatch.setenv("OJS_USERNAME", "editor")
    monkeypatch.setenv("OJS_PASSWORD", "pw")
    monkeypatch.setenv("OJS_DOWNLOADS_DIR", str(tmp_path))
    monkeypatch.setenv(report_var, url)


def test_reviews_fetch_writes_dated_csv_then_norm_consumes_it(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from ojs import cli

    _patch_website_env(
        tmp_path, monkeypatch, report_var="OJS_REVIEWS_REPORT_URL", url="report/reviews"
    )
    client = _FlowClient()
    monkeypatch.setattr(reports.httpx, "Client", lambda **kwargs: client)

    r = CliRunner().invoke(cli.app, ["reviews", "fetch"])
    assert r.exit_code == 0, r.output

    # A date-stamped reviews-*.csv landed in OJS_DOWNLOADS_DIR with the fetched body.
    matches = list(tmp_path.glob("reviews-*.csv"))
    assert len(matches) == 1
    assert matches[0].read_text() == REPORT_CSV
    # The relative report URL was resolved against OJS_BASE_URL.
    assert client.gets[-1] == "https://host/index.php/j/report/reviews"

    # The freshly fetched export feeds `reviews norm` unchanged.
    r2 = CliRunner().invoke(cli.app, ["reviews", "norm"])
    assert r2.exit_code == 0, r2.output
    assert (tmp_path / "reviews" / "reviews.csv").exists()


def test_articles_fetch_writes_dated_csv(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from ojs import cli

    _patch_website_env(
        tmp_path,
        monkeypatch,
        report_var="OJS_ARTICLES_REPORT_URL",
        url="https://host/index.php/j/report/articles",
    )
    client = _FlowClient(report_resp=_Resp("Submission ID,Title\n1,Paper\n"))
    monkeypatch.setattr(reports.httpx, "Client", lambda **kwargs: client)

    r = CliRunner().invoke(cli.app, ["articles", "fetch"])
    assert r.exit_code == 0, r.output
    assert len(list(tmp_path.glob("articles-*.csv"))) == 1


def test_reviews_fetch_missing_config_exits_nonzero(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from ojs import cli

    monkeypatch.setenv("OJS_BASE_URL", "https://host/index.php/j")
    monkeypatch.setenv("OJS_USERNAME", "editor")
    monkeypatch.setenv("OJS_DOWNLOADS_DIR", str(tmp_path))
    monkeypatch.delenv("OJS_PASSWORD", raising=False)
    monkeypatch.delenv("OJS_REVIEWS_REPORT_URL", raising=False)

    r = CliRunner().invoke(cli.app, ["reviews", "fetch"])
    assert r.exit_code == 1
    # The error names exactly what is missing.
    assert "OJS_PASSWORD" in r.output
    assert "OJS_REVIEWS_REPORT_URL" in r.output


def test_reviews_fetch_surfaces_auth_error(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from ojs import cli

    _patch_website_env(
        tmp_path, monkeypatch, report_var="OJS_REVIEWS_REPORT_URL", url="report/reviews"
    )
    # Bad credentials: the sign-in POST returns the login form again.
    client = _FlowClient(post_resp=_Resp(LOGIN_HTML))
    monkeypatch.setattr(reports.httpx, "Client", lambda **kwargs: client)

    r = CliRunner().invoke(cli.app, ["reviews", "fetch"])
    assert r.exit_code == 1
    assert "Login failed" in r.output
    # No file was written on failure.
    assert list(tmp_path.glob("reviews-*.csv")) == []
