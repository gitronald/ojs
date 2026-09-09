"""Importable entry points for the website CSV-export pipelines.

The articles and reviews pipelines are the same two steps against different
reports: log in to the OJS dashboard and download a report CSV, then normalize
the newest matching export on disk. :data:`REPORTS` holds the per-report
differences (filename glob, report-URL env var, output directory, normalizer) so
:func:`run_report_fetch` and :func:`run_norm` stay single implementations.

As in :mod:`ojs.api.run`, ``None`` arguments mean "read the environment" and
progress goes to the ``ojs`` logger rather than stdout.
"""

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

import polars as pl

from ojs import paths
from ojs.errors import ConfigError, MissingDataError, OptionError

__all__ = [
    "REPORTS",
    "NormResult",
    "OutDirResolver",
    "ReportSpec",
    "run_norm",
    "run_report_fetch",
]

logger = logging.getLogger(__name__)


class OutDirResolver(Protocol):
    """The shape of the :mod:`ojs.paths` helpers that derive from ``downloads``.

    A ``Callable`` alias cannot express the keyword-only parent argument, and that
    argument is the whole point here: it is how a caller's downloads override
    reaches the directory derived from it.
    """

    def __call__(
        self,
        override: Path | str | None = None,
        *,
        downloads: Path | str | None = None,
    ) -> Path: ...


@dataclass(frozen=True)
class ReportSpec:
    """Everything that differs between the articles and reviews pipelines."""

    # Filename glob for the export on disk. The naming convention is fixed by the
    # OJS dashboard, so it is internal to the package rather than configurable.
    glob: str
    # Env var holding the instance-specific report URL (it varies by install).
    url_env: str
    out_dir: OutDirResolver
    normalize: Callable[[Path, Path], dict[str, pl.DataFrame]]


@dataclass(frozen=True)
class NormResult:
    """Which export was normalized, where it went, and each table's row count.

    Distinct from :class:`ojs.api.run.NormResult`, which has no ``input_file``
    (the API pipeline normalizes a directory of dumps, not one export). The two
    share a name, so import the modules rather than the classes when a caller
    drives both pipelines.
    """

    input_file: Path
    out_dir: Path
    rows: dict[str, int]

    @property
    def tables(self) -> list[str]:
        """The names of the tables written, in output order."""
        return list(self.rows)


def _normalize_articles(input_file: Path, output_dir: Path) -> dict[str, pl.DataFrame]:
    from ojs.website.articles.normalize import normalize

    return normalize(input_file=input_file, output_dir=output_dir)


def _normalize_reviews(input_file: Path, output_dir: Path) -> dict[str, pl.DataFrame]:
    from ojs.website.reviews.normalize import normalize_reviews

    return normalize_reviews(input_file=input_file, output_dir=output_dir)


REPORTS: dict[str, ReportSpec] = {
    "articles": ReportSpec(
        glob="articles-*.csv",
        url_env="OJS_ARTICLES_REPORT_URL",
        out_dir=paths.articles_dir,
        normalize=_normalize_articles,
    ),
    "reviews": ReportSpec(
        glob="reviews-*.csv",
        url_env="OJS_REVIEWS_REPORT_URL",
        out_dir=paths.reviews_dir,
        normalize=_normalize_reviews,
    ),
}


def _spec(report: str) -> ReportSpec:
    """Look up a report's pipeline configuration, or say what the choices are."""
    try:
        return REPORTS[report]
    except KeyError as e:
        choices = ", ".join(sorted(REPORTS))
        raise OptionError(
            f"unknown report {report!r}; expected one of: {choices}"
        ) from e


def run_report_fetch(
    report: str,
    *,
    base_url: str | None = None,
    username: str | None = None,
    password: str | None = None,
    report_url: str | None = None,
    downloads_dir: Path | str | None = None,
) -> Path:
    """Download a website report CSV via authenticated login.

    Logs in with the editorial-manager credentials, downloads the report URL, and
    writes it as ``{report}-<YYYYMMDD>.csv`` into the downloads directory -- the
    directory :func:`run_norm` globs -- so the fetch -> norm handoff needs no
    manual step. Returns the path written.

    Raises:
        OptionError: ``report`` is not a known report.
        ConfigError: a required credential or the report URL is missing.
        ReportAuthError: login failed or the session was refused.
    """
    from ojs.website.reports import download_report, resolve_report_url

    spec = _spec(report)
    base_url = base_url or os.environ.get("OJS_BASE_URL")
    username = username or os.environ.get("OJS_USERNAME")
    password = password or os.environ.get("OJS_PASSWORD")
    report_url = report_url or os.environ.get(spec.url_env)
    if not base_url or not username or not password or not report_url:
        missing = [
            var
            for var, value in (
                ("OJS_BASE_URL", base_url),
                ("OJS_USERNAME", username),
                ("OJS_PASSWORD", password),
                (spec.url_env, report_url),
            )
            if not value
        ]
        raise ConfigError(
            f"{', '.join(missing)} must be set to fetch the {report} report."
        )

    dest = paths.downloads_dir(downloads_dir) / f"{report}-{date.today():%Y%m%d}.csv"
    resolved = resolve_report_url(base_url, report_url)
    logger.info(
        f"Logging in to OJS as {username} and downloading the {report} report..."
    )
    download_report(
        base_url=base_url,
        username=username,
        password=password,
        report_url=resolved,
        dest=dest,
    )
    logger.info(f"Saved {report} report to {dest}")
    return dest


def run_norm(
    report: str,
    *,
    input_file: Path | str | None = None,
    downloads_dir: Path | str | None = None,
    out_dir: Path | str | None = None,
) -> NormResult:
    """Normalize the most recent matching CSV export into relational tables.

    With no ``input_file``, picks the newest export matching the report's glob in
    the downloads directory -- the date-stamped filenames sort lexicographically,
    so the reverse sort is newest-first.

    With no ``out_dir``, the tables are written under ``downloads_dir`` when that
    was passed, so overriding the downloads directory redirects both ends of the
    pipeline; otherwise the report's own env var (or the default) decides.

    Raises:
        OptionError: ``report`` is not a known report.
        MissingDataError: no export matches the glob (or ``input_file`` is gone).
    """
    spec = _spec(report)
    downloads = paths.downloads_dir(downloads_dir)

    if input_file is not None:
        source = Path(input_file)
        if not source.exists():
            raise MissingDataError(f"{source} not found.")
    else:
        matches = sorted(downloads.glob(spec.glob), reverse=True)
        if not matches:
            raise MissingDataError(
                f"No files matching {spec.glob} found in {downloads}"
            )
        source = matches[0]

    # Pass the raw `downloads_dir` argument, not the resolved `downloads` path:
    # when the caller gave none, the helper must still fall through to the
    # report's own env var rather than to a path derived from the default.
    output_dir = spec.out_dir(out_dir, downloads=downloads_dir)
    tables = spec.normalize(source, output_dir)
    return NormResult(
        input_file=source,
        out_dir=output_dir,
        rows={name: df.height for name, df in tables.items()},
    )
