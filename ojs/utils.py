"""Shared utilities for OJS data normalization."""

import html
import logging
from typing import Any

import polars as pl


def _html_to_text(col: pl.Expr) -> pl.Expr:
    """Turn line-breaking tags into newlines, drop other tags, decode entities.

    Casts to ``String`` first so an all-null column (inferred as the ``Null``
    dtype before the schema cast runs) passes through cleanly instead of raising
    on the string ops. Tags are removed before entities are decoded -- via
    ``html.unescape``, which handles named, decimal, *and* hex entities in one
    pass -- so entity-encoded angle brackets (``&lt;tag&gt;``) survive as literal
    text rather than being re-stripped. ``&nbsp;`` decodes to ``\\xa0``, which is
    normalized back to a regular space, and ``\\r\\n`` or a lone ``\\r`` becomes
    ``\\n``. Line-breaking tags match in any case and with attributes: ``<br>``
    in any form, and the closing ``</p>``, ``</li>``, and ``</div>``.
    """
    return (
        col.cast(pl.String)
        .str.replace_all(r"(?i)<br\b[^>]*>|</(?:p|li|div)\s*>", "\n")
        .str.replace_all(r"<[^>]+>", "")
        .map_elements(html.unescape, return_dtype=pl.String)
        .str.replace_all("\xa0", " ")
        .str.replace_all(r"\r\n?", "\n")
    )


def strip_html(col: pl.Expr) -> pl.Expr:
    """Strip HTML tags from a string column, decode HTML entities, clean whitespace.

    Runs of three or more newlines collapse to a paragraph break, and the
    result is trimmed. See ``_html_to_text`` for the tag and entity handling.
    """
    return _html_to_text(col).str.replace_all(r"\n{3,}", "\n\n").str.strip_chars()


def strip_html_lines(col: pl.Expr) -> pl.Expr:
    """Strip HTML like ``strip_html`` but keep one line per line of input.

    For free text an author typed line by line (e.g. reviewer recommendations):
    line-breaking tags and line breaks become single newlines, each line is trimmed,
    blank lines are dropped, and a value with no text left becomes null.
    """
    # Each newline and the whitespace around it (blank lines included) becomes
    # one newline, which trims every line and drops blank ones in one pass.
    text = _html_to_text(col).str.replace_all(r"\s*\n\s*", "\n").str.strip_chars()
    return pl.when(text != "").then(text)


def localized(
    value: dict[str, Any] | str | None,
    locale: str = "en_US",
    *,
    fallback_any: bool = False,
) -> str | None:
    """Extract a locale-keyed string from an OJS multilingual field.

    With ``fallback_any`` set, fall back to any non-empty localized value when the
    requested ``locale`` is missing or empty -- used for filename resolution where
    a usable name matters more than the locale.
    """
    if isinstance(value, dict):
        primary = value.get(locale)
        if fallback_any:
            return primary or next((v for v in value.values() if v), None)
        return primary or None
    return value or None


def log_sample(
    logger: logging.Logger, df: pl.DataFrame, label: str, columns: list[str]
) -> None:
    """Log the first three rows of ``df``'s ``columns`` that exist, if any rows.

    The closing preview every ``norm`` command prints after writing its tables.
    """
    if df.height == 0:
        return
    available = [c for c in columns if c in df.columns]
    logger.info(f"\nSample from {label} table:")
    logger.info(df.select(available).head(3))
