"""Normalization of raw OJS review CSV exports.

The ``Reviews`` schema class maps raw export headers to typed output columns.
Raw headers are renamed via the schema, value transforms are applied, and
``Reviews.apply`` casts/orders to the schema -- documenting-but-dropping the
known constant columns and surfacing any raw column the schema does not cover.
"""

from pathlib import Path

import polars as pl

from ojs.utils import strip_html
from ojs.website.reviews.schemas import Reviews


def normalize_reviews(input_file: Path, output_dir: Path) -> None:
    """Run the reviews normalization pipeline."""
    output_dir.mkdir(exist_ok=True, parents=True)

    print(f"Loading raw data from {input_file}")
    df = pl.read_csv(input_file, encoding="utf-8-lossy")
    print(f"Raw data shape: {df.shape[0]} rows, {df.shape[1]} columns")

    # Map raw headers to output names via the schema.
    rename = {
        src: name for src, name in Reviews.rename_map().items() if src in df.columns
    }
    df = df.rename(rename)

    # Value transforms before the schema-driven cast.
    if "comments" in df.columns:
        df = df.with_columns(strip_html(pl.col("comments")).alias("comments"))
    if "declined" in df.columns:
        df = df.with_columns((pl.col("declined") == "Yes").alias("declined"))

    # apply() parses datetimes, enforces dtypes, orders columns, excludes the
    # documented dropped columns, and surfaces any raw column not in the schema.
    df = Reviews.apply(df)

    output_file = output_dir / "reviews.csv"
    df.write_csv(output_file)
    print(f"\nReviews: {df.shape[0]} rows, {df.shape[1]} columns")
    print(f"Saved to {output_file}")

    if df.shape[0] > 0:
        core_cols = ["submission_id", "round", "username", "recommendation", "declined"]
        available = [c for c in core_cols if c in df.columns]
        print("\nSample from reviews table:")
        print(df.select(available).head(3))
