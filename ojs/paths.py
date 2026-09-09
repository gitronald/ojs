"""Output directory resolution, shared by the CLI and the run entry points.

Every directory follows the same three-step rule: an explicit argument wins, then
the environment variable, then the built-in default. Keeping this public means an
embedding caller can ask the package where it writes instead of hardcoding a
matching path on its own side and hoping the defaults never diverge.

Three of the directories are *derived* -- their default is built from another
directory rather than a literal constant (``articles``/``reviews`` from
``downloads``, ``files`` from ``api``). Each takes a keyword argument naming the
parent it derives from, so a caller that overrode the parent can chain that
override down. A parent passed this way counts as an explicit argument and so
still outranks the child's own environment variable: anything the caller passes
beats anything in the environment, and the child env var applies whenever no
argument reaches the helper at all (every CLI invocation, since the CLI resolves
directories from the environment only).
"""

import os
from pathlib import Path

__all__ = [
    "DEFAULT_API_DIR",
    "DEFAULT_DOWNLOADS_DIR",
    "api_dir",
    "articles_dir",
    "downloads_dir",
    "files_dir",
    "reviews_dir",
]

DEFAULT_DOWNLOADS_DIR = "data/ojs-website"
DEFAULT_API_DIR = "data/ojs-api"


def downloads_dir(override: Path | str | None = None) -> Path:
    """Where the website CSV exports are fetched to and normalized from."""
    if override is not None:
        return Path(override)
    return Path(os.environ.get("OJS_DOWNLOADS_DIR", DEFAULT_DOWNLOADS_DIR))


def articles_dir(
    override: Path | str | None = None, *, downloads: Path | str | None = None
) -> Path:
    """Where the normalized articles tables are written.

    ``downloads`` is the downloads directory this derives from when no
    ``override`` is given; passing it is what lets a caller's downloads override
    reach the articles directory. Being an explicit argument, it outranks
    ``OJS_ARTICLES_DIR``, which applies only when neither argument is given.
    """
    if override is not None:
        return Path(override)
    if downloads is not None:
        return downloads_dir(downloads) / "articles"
    return Path(os.environ.get("OJS_ARTICLES_DIR", downloads_dir() / "articles"))


def reviews_dir(
    override: Path | str | None = None, *, downloads: Path | str | None = None
) -> Path:
    """Where the normalized reviews table is written.

    ``downloads`` is the downloads directory this derives from when no
    ``override`` is given; passing it is what lets a caller's downloads override
    reach the reviews directory. Being an explicit argument, it outranks
    ``OJS_REVIEWS_DIR``, which applies only when neither argument is given.
    """
    if override is not None:
        return Path(override)
    if downloads is not None:
        return downloads_dir(downloads) / "reviews"
    return Path(os.environ.get("OJS_REVIEWS_DIR", downloads_dir() / "reviews"))


def api_dir(override: Path | str | None = None) -> Path:
    """Where the raw API JSON dumps and sync state live."""
    if override is not None:
        return Path(override)
    return Path(os.environ.get("OJS_API_DIR", DEFAULT_API_DIR))


def files_dir(
    override: Path | str | None = None, *, api: Path | str | None = None
) -> Path:
    """Where downloaded submission file artifacts land.

    ``api`` is the API directory this derives from when no ``override`` is given;
    passing it is what lets a caller's API-directory override reach the artifact
    directory. Being an explicit argument, it outranks ``OJS_FILES_DIR``, which
    applies only when neither argument is given.
    """
    if override is not None:
        return Path(override)
    if api is not None:
        return api_dir(api) / "files"
    return Path(os.environ.get("OJS_FILES_DIR", api_dir() / "files"))
