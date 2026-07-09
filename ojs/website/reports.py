"""Authenticated download of OJS dashboard report CSVs (website pipeline).

The Review and Articles reports are OJS report-plugin exports reached through the
editorial dashboard (Statistics > Tools), not the REST API. They authenticate by
a login *session cookie* -- the REST ``apiToken`` is rejected on the editorial web
app (see plan 000) -- so this module logs in with editorial-manager credentials to
establish a session on an ``httpx.Client``, then GETs the report URL on that
session and writes the CSV to disk.

httpx is the only dependency; both ``ojs reviews fetch`` and ``ojs articles fetch``
call :func:`download_report`. The report URL is supplied by config (it varies by
OJS install and version); the login routes are the standard OJS paths derived from
``OJS_BASE_URL``.
"""

import os
import re
import tempfile
from pathlib import Path
from typing import Protocol

import httpx

__all__ = [
    "ReportAuthError",
    "download_report",
    "extract_csrf_token",
    "fetch_report",
    "login",
    "resolve_report_url",
]

# Standard OJS login routes, joined onto OJS_BASE_URL. Only the report URL itself
# is configured explicitly, since that is the part that varies by install.
LOGIN_PATH = "login"
SIGN_IN_PATH = "login/signIn"

# UTF-8 BOM as a str codepoint. Some OJS CSV exports are Excel-friendly and lead
# with a BOM, which httpx leaves as U+FEFF in the decoded text; strip it before
# the CSV-vs-HTML sniff so a BOM never hides the leading '<' of a login page.
_BOM = chr(0xFEFF)


class ReportAuthError(RuntimeError):
    """Login failed, or the session was not accepted for the report download."""


class _Response(Protocol):
    """The subset of ``httpx.Response`` the helpers here rely on."""

    @property
    def text(self) -> str: ...
    @property
    def content(self) -> bytes: ...
    def raise_for_status(self) -> object: ...


class _Client(Protocol):
    """A GET/POST-capable client, satisfied by ``httpx.Client`` and test fakes."""

    def get(self, url: str) -> _Response: ...
    def post(self, url: str, *, data: dict[str, str]) -> _Response: ...


def _join(base_url: str, path: str) -> str:
    """Join an OJS route path onto the base URL with exactly one separator."""
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def resolve_report_url(base_url: str, report_url: str) -> str:
    """Return an absolute report URL, joining a relative path onto ``base_url``.

    The configured report URL may be given either absolute (``https://.../...``)
    or as a path relative to ``OJS_BASE_URL``; both resolve to the same request.
    """
    if report_url.startswith(("http://", "https://")):
        return report_url
    return _join(base_url, report_url)


def extract_csrf_token(html: str) -> str | None:
    """Extract the login form's CSRF token from an OJS login page, if present.

    OJS renders the token as a hidden ``csrfToken`` input and also exposes it as a
    JavaScript ``csrfToken`` variable; either form is accepted. Returns ``None``
    when absent (instances that do not gate sign-in on a CSRF token) -- the token
    is only sent when found, so a missing one is harmless.
    """
    # Hidden input, either attribute order: name=...value=... or value=...name=...
    for pattern in (
        r'name=["\']csrfToken["\'][^>]*?\bvalue=["\']([^"\']+)["\']',
        r'\bvalue=["\']([^"\']+)["\'][^>]*?name=["\']csrfToken["\']',
        # JavaScript variable: csrfToken: '...' or csrfToken = "..."
        r'csrfToken["\']?\s*[:=]\s*["\']([^"\']+)["\']',
    ):
        match = re.search(pattern, html)
        if match:
            return match.group(1)
    return None


def _is_login_html(text: str) -> bool:
    """True when ``text`` is an OJS login form (a username *and* a password field).

    Used to tell a re-rendered login page (auth failure) from a real report body:
    a CSV export never contains form fields, so both markers together are a
    reliable signal that we got the sign-in page back instead of the report.
    """
    lowered = text.lower()
    has_username = 'name="username"' in lowered or "name='username'" in lowered
    has_password = 'type="password"' in lowered or "type='password'" in lowered
    return has_username and has_password


def _looks_like_html(text: str) -> bool:
    """True when the (BOM/whitespace-stripped) body begins as an HTML document."""
    return text.lstrip(_BOM).lstrip().startswith("<")


def login(client: _Client, base_url: str, username: str, password: str) -> None:
    """Establish an authenticated OJS session on ``client`` via the login form.

    GETs the login page to read its CSRF token, then POSTs the credentials to the
    sign-in route on the same client (whose cookie jar retains the session cookie
    across the redirect). Raises :class:`ReportAuthError` when OJS re-renders the
    sign-in form, which is how it signals bad credentials.
    """
    page = client.get(_join(base_url, LOGIN_PATH))
    page.raise_for_status()

    form: dict[str, str] = {"username": username, "password": password, "source": ""}
    csrf = extract_csrf_token(page.text)
    if csrf:
        form["csrfToken"] = csrf

    response = client.post(_join(base_url, SIGN_IN_PATH), data=form)
    response.raise_for_status()

    # A successful sign-in redirects to the dashboard; a failure re-renders the
    # login form (HTTP 200) with an error notice -- detected by its form fields.
    if _is_login_html(response.text):
        raise ReportAuthError(
            "Login failed: OJS returned the sign-in form again. Check "
            "OJS_USERNAME and OJS_PASSWORD."
        )


def fetch_report(client: _Client, report_url: str) -> bytes:
    """GET a report CSV on an authenticated session and return its raw bytes.

    Raises :class:`ReportAuthError` when the response is HTML rather than CSV --
    almost always the login page served because the session was not established or
    has expired -- so an HTML blob never gets written into a ``.csv`` file.
    """
    response = client.get(report_url)
    response.raise_for_status()

    if _looks_like_html(response.text):
        if _is_login_html(response.text):
            raise ReportAuthError(
                f"Report download returned the login page, not CSV ({report_url}); "
                "the session was not established or has expired."
            )
        raise ReportAuthError(f"Report download returned HTML, not CSV ({report_url}).")
    return response.content


def _write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically (temp file + ``os.replace``).

    Mirrors ``ojs.api.sync.write_json``: a crash or full disk mid-write leaves any
    previous file intact rather than a truncated one, and the file gets the
    umask-derived mode rather than mkstemp's owner-only default.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        umask = os.umask(0o022)
        os.umask(umask)
        os.fchmod(fd, 0o666 & ~umask)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def download_report(
    *,
    base_url: str,
    username: str,
    password: str,
    report_url: str,
    dest: Path,
) -> Path:
    """Log in, download the report at ``report_url``, and write it to ``dest``.

    ``report_url`` must already be absolute (see :func:`resolve_report_url`). Opens
    one ``httpx.Client``, so the login session cookie carries into the report GET.
    Returns ``dest``.
    """
    with httpx.Client(follow_redirects=True, timeout=60) as client:
        login(client, base_url, username, password)
        content = fetch_report(client, report_url)
    _write_bytes(dest, content)
    return dest
