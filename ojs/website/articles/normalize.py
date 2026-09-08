"""Normalization of raw OJS article CSV exports into relational tables.

The schema classes in ``schemas.py`` are the source of truth: they map raw
export headers (literal for submissions; ``(Author N)``/``(Editor N)`` and the
decision patterns for the wide tables) to typed output columns. Each table is
finalized with ``Table.apply`` (dtype cast + column order). The normalized
output may be a subset of the schema (always-empty columns are dropped); raw
columns no table accounts for are surfaced rather than silently dropped.
"""

import logging
from pathlib import Path

import polars as pl

from ojs.website.articles.schemas import Authors, Decisions, Editors, Submissions

logger = logging.getLogger(__name__)

# Numbered-column ranges in the wide export (author/editor/decision indices).
AUTHOR_RANGE = range(1, 16)
EDITOR_RANGE = range(1, 5)
DECISION_RANGE = range(1, 10)

# The submission columns dropped from output are declared in the schema
# (`dropped=True`); the drop list is derived from there. CONSTANT_COLUMNS adds
# the expected value for the constant column's anomaly check; the rest are
# expected to be always null. Both are surfaced if they carry unexpected data.
CONSTANT_COLUMNS = {"language": "en_US"}
ALWAYS_NULL_COLUMNS = [
    c for c in Submissions.dropped_columns() if c not in CONSTANT_COLUMNS
]


def _decision_columns(decision_num: int, editor_num: int) -> tuple[str, str]:
    """Raw (decision, date) header pair for a given decision/editor number."""
    return (
        f"Editor Decision {decision_num}  (Editor {editor_num})",
        f"Date decided {decision_num}  (Editor {editor_num})",
    )


def _claimed_columns() -> set[str]:
    """Every raw header the article schema accounts for (literal + numbered)."""
    claimed: set[str] = set(Submissions.source_columns())
    for base in Authors.field_map("Author N"):
        claimed |= {f"{base} (Author {n})" for n in AUTHOR_RANGE}
    for base in Editors.field_map("Editor N"):
        claimed |= {f"{base} (Editor {n})" for n in EDITOR_RANGE}
    for decision_num in DECISION_RANGE:
        for editor_num in EDITOR_RANGE:
            claimed.update(_decision_columns(decision_num, editor_num))
    return claimed


def _unpivot_numbered_columns(
    df: pl.DataFrame,
    field_map: dict[str, str],
    id_col: str,
    number_col: str,
    number_range: range,
    entity_label: str,
) -> pl.DataFrame:
    """Unpivot wide-format numbered columns into long format using polars concat.

    For each number in the range, selects the matching columns, renames them to
    clean names, and concatenates into a single long-format dataframe.

    Args:
        df: Raw wide-format dataframe.
        field_map: Base field name -> clean column name mapping.
        id_col: Name of the ID column in df (e.g., "Submission ID").
        number_col: Name for the number column (e.g., "author_number").
        number_range: Range of numbers to iterate (e.g., range(1, 16)).
        entity_label: Label used in column names (e.g., "Author", "Editor").
    """
    frames: list[pl.DataFrame] = []
    for n in number_range:
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
        for n in AUTHOR_RANGE
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
        df, field_map, "Submission ID", "author_number", AUTHOR_RANGE, "Author"
    )
    if result.height == 0:
        return pl.DataFrame(schema=Authors.polars_schema())
    return Authors.apply(result)


def extract_editors_table(df: pl.DataFrame) -> pl.DataFrame:
    """Extract editor data into the normalized editors table."""
    field_map = Editors.field_map("Editor N")
    result = _unpivot_numbered_columns(
        df, field_map, "Submission ID", "editor_number", EDITOR_RANGE, "Editor"
    )
    if result.height == 0:
        return pl.DataFrame(schema=Editors.polars_schema())
    return Editors.apply(result)


def extract_decisions_table(df: pl.DataFrame) -> pl.DataFrame:
    """Extract editorial decisions into the normalized decisions table."""
    frames: list[pl.DataFrame] = []
    for editor_num in EDITOR_RANGE:
        for decision_num in DECISION_RANGE:
            decision_col, date_col = _decision_columns(decision_num, editor_num)
            if decision_col not in df.columns:
                continue

            cols = {"Submission ID": "submission_id", decision_col: "decision"}
            if date_col in df.columns:
                cols[date_col] = "date_decided"

            sub = df.select(list(cols.keys())).rename(cols)
            sub = sub.with_columns(
                [
                    pl.lit(editor_num).cast(pl.Int64).alias("editor_number"),
                    pl.lit(decision_num).cast(pl.Int64).alias("decision_number"),
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
    claimed = _claimed_columns()
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

    submissions = tables["submissions"]
    if submissions.shape[0] > 0:
        core_cols = ["submission_id", "title", "status", "date_submitted"]
        available = [c for c in core_cols if c in submissions.columns]
        logger.info("\nSample from submissions table:")
        logger.info(submissions.select(available).head(3))

    return tables
