"""Errors raised deliberately by the ojs pipelines.

Library code must not decide how a failure is reported, so nothing below the CLI
raises ``typer.Exit``. The run entry points raise these instead, and
:mod:`ojs.cli` maps them back onto the exit codes and messages the commands have
always produced:

- :class:`OptionError` -> ``typer.BadParameter`` (click's usage error, exit 2),
  because a contradictory or malformed argument is a usage problem.
- every other :class:`OjsError` -> ``Error: <message>`` on stdout, exit 1.

An embedding caller catches :class:`OjsError` (or a specific subclass) and
decides for itself.
"""

__all__ = [
    "ConfigError",
    "HttpError",
    "MissingDataError",
    "OjsError",
    "OptionError",
]


class OjsError(Exception):
    """Base class for every error the ojs pipelines raise deliberately."""


class ConfigError(OjsError):
    """Required configuration is absent or unusable.

    Raised when a value that has no sensible default -- ``OJS_BASE_URL``,
    ``OJS_API_KEY``, the website report credentials -- was neither passed as an
    argument nor found in the environment.
    """


class OptionError(OjsError):
    """Arguments are contradictory or malformed.

    Distinct from :class:`ConfigError` so the CLI can keep reporting these as
    click usage errors (exit 2) while configuration failures stay exit 1.
    """


class MissingDataError(OjsError):
    """An input file the pipeline needs is not on disk."""


class HttpError(OjsError):
    """An HTTP request to OJS failed after any retries were exhausted.

    Wraps the underlying ``httpx`` error so the CLI reports it as
    ``Error: <message>`` instead of a traceback. ``status_code`` is the HTTP
    status, or ``None`` for a transport failure (timeout, refused connection), so
    callers can still branch on it (e.g. skipping 403/404 per item). The message
    never carries the API token.
    """

    def __init__(
        self, message: str, *, status_code: int | None = None, reason: str = ""
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason
