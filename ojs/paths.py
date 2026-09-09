"""Output directory resolution, shared by the CLI and the run entry points.

Every directory follows the same three-step rule: an explicit argument wins, then
the environment variable, then the built-in default. Keeping this public means an
embedding caller can ask the package where it writes instead of hardcoding a
matching path on its own side and hoping the defaults never diverge.
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


def articles_dir(override: Path | str | None = None) -> Path:
    """Where the normalized articles tables are written."""
    if override is not None:
        return Path(override)
    return Path(os.environ.get("OJS_ARTICLES_DIR", downloads_dir() / "articles"))


def reviews_dir(override: Path | str | None = None) -> Path:
    """Where the normalized reviews table is written."""
    if override is not None:
        return Path(override)
    return Path(os.environ.get("OJS_REVIEWS_DIR", downloads_dir() / "reviews"))


def api_dir(override: Path | str | None = None) -> Path:
    """Where the raw API JSON dumps and sync state live."""
    if override is not None:
        return Path(override)
    return Path(os.environ.get("OJS_API_DIR", DEFAULT_API_DIR))


def files_dir(override: Path | str | None = None) -> Path:
    """Where downloaded submission file artifacts land."""
    if override is not None:
        return Path(override)
    return Path(os.environ.get("OJS_FILES_DIR", api_dir() / "files"))
