"""Typed, self-documenting table schemas for polars normalization.

A normalized table is declared as a :class:`Table` subclass whose ordered
:class:`Column` class attributes are the single source of truth for the table's
column names, polars dtypes, source mapping (raw CSV header / API field), and
documentation. The class drives renaming, column ordering, dtype enforcement,
and the ``table_schemas.csv`` documentation export -- there is no
serialize-to-CSV-then-reparse round-trip.

Datetime columns must be declared fully-specified as ``pl.Datetime("us")``
(``pl.Schema`` rejects the bare ``pl.Datetime`` class), which also matches what
``str.strptime`` / :func:`ojs.utils.parse_datetime_columns` produce.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import polars as pl

# A polars dtype, in either instance form (``pl.Datetime("us")``) or the
# non-parametric class form (``pl.Int64``); both are accepted by ``pl.Schema``.
DType = pl.DataType | type[pl.DataType]

# Formats OJS emits for temporal fields, mirrored from parse_datetime_columns.
_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_DATE_FORMAT = "%Y-%m-%d"


def _base_type(dtype: DType) -> type[pl.DataType]:
    """Return the dtype's class, whether given an instance or a class."""
    return dtype if isinstance(dtype, type) else type(dtype)


def _dtype_name(dtype: DType) -> str:
    """Clean base name for documentation, e.g. ``Datetime`` not the full repr."""
    return _base_type(dtype).__name__


@dataclass(frozen=True)
class Column:
    """One column of a normalized table.

    ``name`` is filled automatically from the attribute it is bound to on a
    :class:`Table` subclass (via ``__set_name__``), so it need not be repeated.
    ``source`` is the raw CSV header, API field, or wide-column pattern the value
    comes from; ``None`` marks a derived/computed column.
    """

    dtype: DType
    description: str
    source: str | None = None
    name: str = field(default="")
    dropped: bool = False  # documented in the schema, excluded from output

    def __set_name__(self, owner: type, attr_name: str) -> None:
        # frozen -> bypass the immutability guard to record the bound name once.
        if not self.name:
            object.__setattr__(self, "name", attr_name)


class Table:
    """Base for a normalized table schema.

    Subclasses set ``name`` and ``description`` and declare :class:`Column`
    attributes in the desired output order; ``__init_subclass__`` collects them
    into ``columns`` (class-body definition order is preserved).
    """

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    columns: ClassVar[tuple[Column, ...]] = ()

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        cls.columns = tuple(v for v in vars(cls).values() if isinstance(v, Column))
        # A column attribute named `name`/`description` would shadow the table
        # metadata and silently misorder columns -- fail loudly instead. Declare
        # such a column under a different attribute with an explicit `name=`.
        for reserved in ("name", "description"):
            if isinstance(getattr(cls, reserved), Column):
                raise TypeError(
                    f"{cls.__name__}: column attribute '{reserved}' collides with "
                    f"reserved Table metadata; use a different attribute name and "
                    f"pass name='{reserved}' to Column."
                )

    @classmethod
    def names(cls) -> list[str]:
        """Output column names in schema order."""
        return [c.name for c in cls.columns]

    @classmethod
    def polars_schema(cls) -> pl.Schema:
        """The table as a ``pl.Schema`` (name -> dtype, in order)."""
        return pl.Schema({c.name: c.dtype for c in cls.columns})

    @classmethod
    def rename_map(cls) -> dict[str, str]:
        """``source header -> output name`` for columns with a literal source.

        Columns whose ``source`` is ``None`` (derived) or equal to ``name`` are
        omitted. Wide-column patterns (e.g. ``Given Name (Author N)``) are
        included verbatim; use :meth:`field_map` to consume those.
        """
        return {
            c.source: c.name for c in cls.columns if c.source and c.source != c.name
        }

    @classmethod
    def field_map(cls, pattern: str) -> dict[str, str]:
        """``base field -> output name`` for wide numbered columns.

        Strips the `` (<pattern>)`` suffix from each matching ``source`` so the
        unpivot step can rebuild ``<base field> (<entity> <n>)`` per number.
        """
        suffix = f" ({pattern})"
        out: dict[str, str] = {}
        for c in cls.columns:
            if c.source and pattern in c.source:
                base = c.source.replace(suffix, "")
                out.setdefault(base, c.name)
        return out

    @classmethod
    def source_columns(cls) -> list[str]:
        """Literal source headers (no derived columns, no wide patterns)."""
        return [c.source for c in cls.columns if c.source]

    @classmethod
    def dropped_columns(cls) -> list[str]:
        """Names of columns documented in the schema but excluded from output."""
        return [c.name for c in cls.columns if c.dropped]

    @classmethod
    def doc_rows(cls) -> list[dict[str, str]]:
        """Rows for the ``table_schemas.csv`` documentation export.

        ``in_output`` records whether the column appears in the normalized table
        (``false`` for columns that are mapped/documented but dropped by default).
        """
        return [
            {
                "table_name": cls.name,
                "column_name": c.name,
                "data_type": _dtype_name(c.dtype),
                "in_output": "false" if c.dropped else "true",
                "original_column": c.source or "N/A",
                "description": c.description,
                "table_description": cls.description,
            }
            for c in cls.columns
        ]

    @classmethod
    def apply(cls, df: pl.DataFrame) -> pl.DataFrame:
        """Cast ``df`` to the schema's dtypes and column order, loudly.

        The schema is the full raw->normalized mapping; the normalized output is
        the subset of non-``dropped`` columns. So:

        - Columns present in ``df`` but absent from the schema entirely
          (UNEXPECTED) are reported and dropped -- nothing unknown is silently
          discarded.
        - ``dropped`` columns are excluded from the output (documented as
          out-of-output; not reported, since their exclusion is intended).
        - The remaining schema columns present in ``df`` are kept, in schema
          order, each cast to its declared dtype; string columns targeting
          ``Date``/``Datetime`` are parsed with the OJS format (``strict=False``
          so unparseable values become null).
        - Schema columns absent from ``df`` are simply omitted (an expected
          subset), not filled.
        """
        present = df.schema
        schema_names = {c.name for c in cls.columns}

        unexpected = [c for c in df.columns if c not in schema_names]
        if unexpected:
            print(
                f"WARNING [{cls.name}]: dropping {len(unexpected)} column(s) not "
                f"in schema: {unexpected}"
            )

        output = [c for c in cls.columns if not c.dropped and c.name in present]
        exprs = [_cast_expr(c.name, present[c.name], c.dtype) for c in output]
        return df.select(exprs)


def _cast_expr(name: str, current: pl.DataType, target: DType) -> pl.Expr:
    """Expression that brings column ``name`` from ``current`` to ``target``."""
    col = pl.col(name)
    if current == target:
        return col
    if current == pl.String and _base_type(target) is pl.Date:
        return col.str.strptime(pl.Date, _DATE_FORMAT, strict=False)
    if current == pl.String and _base_type(target) is pl.Datetime:
        return col.str.strptime(pl.Datetime, _DATETIME_FORMAT, strict=False)
    return col.cast(target, strict=False)


def write_schema_docs(tables: tuple[type[Table], ...], output_file: Path) -> None:
    """Write ``table_schemas.csv`` documentation for the given table classes."""
    rows = [row for table in tables for row in table.doc_rows()]
    pl.DataFrame(rows).write_csv(output_file)
    print(f"Schema documentation saved to {output_file}")

    print("\nSchema Summary:")
    counts = Counter(row["table_name"] for row in rows)
    for table_name, count in counts.items():
        print(f"  {table_name}: {count} columns")
    print(f"\nTotal: {len(rows)} columns across {len(counts)} tables")
