"""Normalization of raw OJS article CSV exports into relational tables.

The schema classes in ``schemas.py`` are the source of truth: they map raw
export headers (literal for submissions; ``(Author N)``/``(Editor N)`` and the
decision patterns for the wide tables) to typed output columns. Each table is
finalized with ``Table.apply`` (dtype cast + column order). The normalized
output may be a subset of the schema (always-empty columns are dropped); raw
columns no table accounts for are surfaced rather than silently dropped.

The wide export repeats its author, editor, and decision blocks once per
entity, and how many it carries depends on the instance's busiest submission:
one export has editors 1-4, the next adds a fifth editor and an eleventh
decision. The indices to unpivot are therefore read off the headers actually
present (:func:`entity_numbers`, :func:`decision_slots`) rather than fixed
here, so no trailing entity is ever dropped for exceeding a cap.
"""

import logging
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from ojs.utils import log_sample
from ojs.website.articles.schemas import Authors, Decisions, Editors, Submissions

logger = logging.getLogger(__name__)

# Header patterns of the numbered blocks. Profile fields carry the entity index,
# "Family Name (Author 3)"; decisions carry both indices, with the two spaces
# OJS emits before the parenthesis: "Editor Decision 2  (Editor 1)" and its
# "Date decided 2  (Editor 1)" companion.
_ENTITY_HEADER = re.compile(r"^(?P<base>.+) \((?P<label>Author|Editor) (?P<n>\d+)\)$")
_DECISION_HEADER = re.compile(
    r"^(?P<kind>Editor Decision|Date decided) (?P<decision>\d+)\s+"
    r"\(Editor (?P<editor>\d+)\)$"
)
_DECISION_KINDS = {"Editor Decision": "decision", "Date decided": "date_decided"}

# The submission columns dropped from output are declared in the schema
# (`dropped=True`); the drop list is derived from there. CONSTANT_COLUMNS adds
# the expected value for the constant column's anomaly check; the rest are
# expected to be always null. Both are surfaced if they carry unexpected data.
CONSTANT_COLUMNS = {"language": "en_US"}
ALWAYS_NULL_COLUMNS = [
    c for c in Submissions.dropped_columns() if c not in CONSTANT_COLUMNS
]


def _entity_index(col: str, field_map: dict[str, str], label: str) -> int | None:
    """The entity index of ``col`` if it is a mapped ``<base> (<label> N)`` header.

    The one place the entity-header rule lives, shared by :func:`entity_numbers`
    and the unmapped-column check so the two cannot drift. A decision header
    also ends in ``(Editor N)`` and would otherwise match with a spurious base,
    so it is excluded up front rather than left to the ``field_map`` lookup.
    """
    if _DECISION_HEADER.match(col):
        return None
    m = _ENTITY_HEADER.match(col)
    if m and m["label"] == label and m["base"] in field_map:
        return int(m["n"])
    return None


def entity_numbers(
    columns: Iterable[str], field_map: dict[str, str], label: str
) -> list[int]:
    """The entity indices a wide export carries for one numbered block.

    Scans ``columns`` for ``<base> (<label> N)`` headers whose base the schema
    maps (``field_map`` from ``Table.field_map``) and returns the distinct ``N``
    in ascending order. Reading the indices off the export is what keeps a
    fifth editor or a sixteenth author from being dropped: there is no cap to
    exceed.
    """
    numbers = {
        n for col in columns if (n := _entity_index(col, field_map, label)) is not None
    }
    return sorted(numbers)


@dataclass(frozen=True)
class DecisionSlot:
    """One editor's numbered decision in the wide export.

    ``columns`` maps the raw headers present for the slot to their clean names
    (``decision``, ``date_decided``); a slot may carry either without the other.
    """

    editor_number: int
    decision_number: int
    columns: dict[str, str]


def decision_slots(columns: Iterable[str]) -> list[DecisionSlot]:
    """Every (editor, decision) slot the export's decision headers describe.

    Ordered by editor then decision number, so the concatenated long table
    comes out in workflow order without a second sort key being invented.
    """
    found: dict[tuple[int, int], dict[str, str]] = defaultdict(dict)
    for col in columns:
        m = _DECISION_HEADER.match(col)
        if m:
            key = (int(m["editor"]), int(m["decision"]))
            found[key][col] = _DECISION_KINDS[m["kind"]]
    return [
        DecisionSlot(editor_number, decision_number, cols)
        for (editor_number, decision_number), cols in sorted(found.items())
    ]


def _claimed_columns(columns: Iterable[str]) -> set[str]:
    """The raw headers among ``columns`` that the article schema accounts for.

    Literal submission headers, any ``(Author N)``/``(Editor N)`` field the
    schema maps at whatever index the export uses, and every decision/date
    header. Anything else is surfaced by the caller as unmapped.
    """
    literal = set(Submissions.source_columns())
    fields = {
        "Author": Authors.field_map("Author N"),
        "Editor": Editors.field_map("Editor N"),
    }
    return {
        col
        for col in columns
        if col in literal
        or _DECISION_HEADER.match(col)
        or any(
            _entity_index(col, field_map, label) is not None
            for label, field_map in fields.items()
        )
    }


def _unpivot_numbered_columns(
    df: pl.DataFrame,
    field_map: dict[str, str],
    id_col: str,
    number_col: str,
    entity_label: str,
) -> pl.DataFrame:
    """Unpivot wide-format numbered columns into long format using polars concat.

    For each entity index present in the export (see :func:`entity_numbers`),
    selects the matching columns, renames them to clean names, and concatenates
    into a single long-format dataframe.

    Args:
        df: Raw wide-format dataframe.
        field_map: Base field name -> clean column name mapping.
        id_col: Name of the ID column in df (e.g., "Submission ID").
        number_col: Name for the number column (e.g., "author_number").
        entity_label: Label used in column names (e.g., "Author", "Editor").
    """
    frames: list[pl.DataFrame] = []
    for n in entity_numbers(df.columns, field_map, entity_label):
        rename = {id_col: "submission_id"}
        for base_field, clean_name in field_map.items():
            col = f"{base_field} ({entity_label} {n})"
            if col in df.columns:
                rename[col] = clean_name

        # Skip if no entity columns exist for this number
        if len(rename) <= 1:
            continue

        sub = df.select(list(rename.keys())).rename(rename)
        sub = sub.with_columns(pl.lit(n).cast(pl.Int64).alias(number_col))
        frames.append(sub)

    if not frames:
        return pl.DataFrame()

    result = pl.concat(frames, how="diagonal")

    # Filter to rows where at least one non-id field has data
    data_cols = [c for c in result.columns if c not in ("submission_id", number_col)]
    has_data = pl.any_horizontal(
        pl.col(c).is_not_null() & (pl.col(c).cast(pl.String) != "") for c in data_cols
    )
    result = result.filter(has_data)
    result = result.sort("submission_id", number_col)

    return result


def _warn_dropped_anomalies(submissions: pl.DataFrame) -> None:
    """Warn if a dropped-by-default column unexpectedly carries data.

    The columns are excluded from output by ``Submissions.apply`` (they are
    ``dropped=True`` in the schema); this only surfaces the anomaly.
    """
    for col in ALWAYS_NULL_COLUMNS:
        if col in submissions.columns:
            non_null_count = submissions[col].drop_nulls().len()
            if non_null_count > 0:
                logger.warning(
                    f"WARNING: Column '{col}' expected to be always null, but has "
                    f"{non_null_count} non-null values. Dropping anyway."
                )

    for col, expected_value in CONSTANT_COLUMNS.items():
        if col in submissions.columns:
            unique_values = submissions[col].drop_nulls().unique().to_list()
            if len(unique_values) > 1 or (
                len(unique_values) == 1 and unique_values[0] != expected_value
            ):
                logger.warning(
                    f"WARNING: Column '{col}' expected to be always "
                    f"'{expected_value}', but has values: {unique_values}. "
                    "Dropping anyway."
                )


def extract_submissions_table(df: pl.DataFrame) -> pl.DataFrame:
    """Extract core submission data into the normalized submissions table."""
    source_cols = [c for c in Submissions.source_columns() if c in df.columns]
    submissions = df.select(source_cols).rename(
        {
            src: name
            for src, name in Submissions.rename_map().items()
            if src in source_cols
        }
    )

    # Derived first-author columns and author count, from the wide author block.
    author_name = (
        df["Family Name (Author 1)"]
        if "Family Name (Author 1)" in df.columns
        else pl.Series("author_name", [None] * df.height)
    )
    author_email = (
        df["Email (Author 1)"]
        if "Email (Author 1)" in df.columns
        else pl.Series("author_email", [None] * df.height)
    )
    family_name_cols = [
        f"Family Name (Author {n})"
        for n in entity_numbers(df.columns, Authors.field_map("Author N"), "Author")
        if f"Family Name (Author {n})" in df.columns
    ]
    author_count = df.select(
        pl.sum_horizontal(
            pl.col(c).is_not_null() & (pl.col(c).cast(pl.String) != "")
            for c in family_name_cols
        )
        .cast(pl.Int64)
        .alias("author_count")
    )["author_count"]

    submissions = submissions.with_columns(
        [
            author_name.alias("author_name"),
            author_email.alias("author_email"),
            author_count.alias("author_count"),
        ]
    )

    _warn_dropped_anomalies(submissions)
    return Submissions.apply(submissions)


def extract_authors_table(df: pl.DataFrame) -> pl.DataFrame:
    """Extract author data into the normalized authors table."""
    field_map = Authors.field_map("Author N")
    result = _unpivot_numbered_columns(
        df, field_map, "Submission ID", "author_number", "Author"
    )
    if result.height == 0:
        return pl.DataFrame(schema=Authors.polars_schema())
    return Authors.apply(result)


def extract_editors_table(df: pl.DataFrame) -> pl.DataFrame:
    """Extract editor data into the normalized editors table."""
    field_map = Editors.field_map("Editor N")
    result = _unpivot_numbered_columns(
        df, field_map, "Submission ID", "editor_number", "Editor"
    )
    if result.height == 0:
        return pl.DataFrame(schema=Editors.polars_schema())
    return Editors.apply(result)


def extract_decisions_table(df: pl.DataFrame) -> pl.DataFrame:
    """Extract editorial decisions into the normalized decisions table."""
    frames: list[pl.DataFrame] = []
    for slot in decision_slots(df.columns):
        # A date with no decision header has nothing to filter on; skip it.
        if "decision" not in slot.columns.values():
            continue

        cols = {"Submission ID": "submission_id", **slot.columns}
        sub = df.select(list(cols.keys())).rename(cols)
        sub = sub.with_columns(
            [
                pl.lit(slot.editor_number).cast(pl.Int64).alias("editor_number"),
                pl.lit(slot.decision_number).cast(pl.Int64).alias("decision_number"),
            ]
        )
        frames.append(sub)

    if not frames:
        return pl.DataFrame(schema=Decisions.polars_schema())

    decisions_df = pl.concat(frames, how="diagonal")
    decisions_df = decisions_df.filter(
        pl.col("decision").is_not_null() & (pl.col("decision").cast(pl.String) != "")
    )
    decisions_df = Decisions.apply(decisions_df)
    return decisions_df.sort("submission_id", "editor_number", "decision_number")


def normalize(input_file: Path, output_dir: Path) -> dict[str, pl.DataFrame]:
    """Run the full article normalization pipeline.

    Returns the written tables keyed by name, so a caller gets the result in
    memory rather than having to re-read the CSVs it just wrote.
    """
    output_dir.mkdir(exist_ok=True, parents=True)

    logger.info(f"Loading raw data from {input_file}")
    df = pl.read_csv(input_file, encoding="utf-8-lossy")
    logger.info(f"Raw data shape: {df.shape[0]} rows, {df.shape[1]} columns")

    # No silent dropping: surface raw headers no article table accounts for.
    claimed = _claimed_columns(df.columns)
    unclaimed = [c for c in df.columns if c not in claimed]
    if unclaimed:
        logger.warning(
            f"WARNING: {len(unclaimed)} raw column(s) not mapped by any article "
            f"schema (ignored): {unclaimed}"
        )

    logger.info("\nExtracting normalized tables...")
    tables = {
        "submissions": extract_submissions_table(df),
        "authors": extract_authors_table(df),
        "editors": extract_editors_table(df),
        "decisions": extract_decisions_table(df),
    }

    for name, table_df in tables.items():
        logger.info(f"{name}: {table_df.shape[0]} rows, {table_df.shape[1]} columns")
        output_file = output_dir / f"{name}.csv"
        table_df.write_csv(output_file)
        logger.info(f"Saved {name} to {output_file}")

    logger.info(f"\nNormalization complete! All tables saved to {output_dir}/")

    log_sample(
        logger,
        tables["submissions"],
        "submissions",
        ["submission_id", "title", "status", "date_submitted"],
    )

    return tables
