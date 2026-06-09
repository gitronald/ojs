"""Shared utilities for OJS data normalization."""

import html
from typing import Any

import polars as pl


def strip_html(col: pl.Expr) -> pl.Expr:
    """Strip HTML tags from a string column, decode HTML entities, clean whitespace.

    Casts to ``String`` first so an all-null column (inferred as the ``Null``
    dtype before the schema cast runs) passes through cleanly instead of raising
    on the string ops. Tags are removed before entities are decoded -- via
    ``html.unescape``, which handles named, decimal, *and* hex entities in one
    pass -- so entity-encoded angle brackets (``&lt;tag&gt;``) survive as literal
    text rather than being re-stripped. ``&nbsp;`` decodes to ``\\xa0``, which is
    normalized back to a regular space to preserve the prior whitespace behavior.
    """
    return (
        col.cast(pl.String)
        .str.replace_all(r"<br\s*/?>", "\n")
        .str.replace_all(r"</p>", "\n")
        .str.replace_all(r"</li>", "\n")
        .str.replace_all(r"<[^>]+>", "")
        .map_elements(html.unescape, return_dtype=pl.String)
        .str.replace_all("\xa0", " ")
        .str.replace_all(r"\r\n", "\n")
        .str.replace_all(r"\n{3,}", "\n\n")
        .str.strip_chars()
    )


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
