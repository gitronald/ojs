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
