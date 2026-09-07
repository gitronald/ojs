"""Normalize OJS API JSON data into relational tables."""

from pathlib import Path
from typing import Any

import polars as pl

from ojs.api import schemas
from ojs.utils import localized, strip_html

# sectionId -> section title mapping (derived from CSV cross-reference)
SECTION_MAP = {
    1: "Peer-reviewed Articles",
    2: "Commentary",
    3: "Letter from the Editor",
    4: "Peer-reviewed Research Notes",
    6: "Special Issue: Digital Intersectionality and Marginalization in Majority World",
}


def _author_seq_key(author: dict[str, Any]) -> float:
    """Sort key for an author's ``seq``, coercing null/missing/non-numeric to 0.

    OJS serializes an unset ``seq`` as JSON ``null``; sorting ``None`` against an
    int raises ``TypeError``, so non-numeric values fold to 0 (the sort is stable,
    so equal keys keep input order).
    """
    seq = author.get("seq")
    return seq if isinstance(seq, (int, float)) else 0


def normalize_submissions(
    submissions: list[dict[str, Any]], publications: list[dict[str, Any]]
) -> pl.DataFrame:
    """Build submissions table from API submissions and publication details."""
    # Index publications by submission_id
    pub_by_sub = {p["_submission_id"]: p for p in publications}

    rows: list[dict[str, Any]] = []
    for sub in submissions:
        pub_summary = sub["publications"][0] if sub.get("publications") else {}
        pub_full = pub_by_sub.get(sub["id"], {})

        # Abstract from full publication (HTML -> plain text)
        abstract = localized(pub_full.get("abstract"))

        # Keywords from full publication. `or {}` guards both a missing key and
        # an explicit JSON null (OJS serializes unset multilingual fields as null).
        keywords_list = (pub_full.get("keywords") or {}).get("en_US", [])
        keywords = "; ".join(keywords_list) if keywords_list else None

        # Authors from full publication (`or []` guards an explicit JSON null).
        authors = pub_full.get("authors") or []
        author_count = len(authors)

        # First author info
        first_author = authors[0] if authors else {}
        author_name = localized(first_author.get("familyName"))
        author_email = first_author.get("email")

        # Section title from sectionId
        section_id = pub_summary.get("sectionId")
        if section_id is None:
            submission_type = "Unknown (None)"
        else:
            submission_type = SECTION_MAP.get(section_id, f"Unknown ({section_id})")

        rows.append(
            {
                "submission_id": sub["id"],
                "author_name": author_name,
                "author_email": author_email,
                "author_count": author_count if author_count > 0 else None,
                "title": localized(pub_summary.get("fullTitle")),
                "abstract": abstract,
                "submission_type": submission_type,
                "keywords": keywords,
                "status": sub.get("statusLabel", None),
                "url": pub_summary.get("urlPublished", None),
                "doi": pub_summary.get("pub-id::doi", None),
                "date_submitted": sub.get("dateSubmitted", None),
                "last_modified": sub.get("lastModified", None),
                # API-only fields (not available in CSV export)
                "date_published": pub_summary.get("datePublished", None),
                "stage_id": sub.get("stageId", None),
                "issue_id": pub_full.get("issueId", None),
            }
        )

    if not rows:
        return pl.DataFrame(schema=schemas.Submissions.polars_schema())

    df = pl.DataFrame(rows)
    df = df.with_columns(strip_html(pl.col("abstract")).alias("abstract"))

    # Schema-driven cast parses date_submitted/last_modified (Datetime) and
    # date_published (Date), and enforces the integer columns' dtypes.
    df = schemas.Submissions.apply(df)
    return df.sort("submission_id")


def _build_email_to_user_id(users: list[dict[str, Any]]) -> dict[str, int]:
    """Build an email -> user_id map, excluding ambiguous duplicate emails.

    A plain dict comprehension would silently keep the last id for a repeated
    email, misattributing every author with that email to one account. Instead,
    collect the ids per email and drop any email mapped by more than one distinct
    id (warning on the conflict), so an author with an ambiguous email gets a null
    ``user_id`` (honestly unmatched) rather than a confidently-wrong one.
    """
    ids_by_email: dict[str, set[int]] = {}
    for u in users:
        email = u.get("email")
        if email:
            ids_by_email.setdefault(email, set()).add(u["id"])

    mapping: dict[str, int] = {}
    for email, ids in ids_by_email.items():
        if len(ids) == 1:
            mapping[email] = next(iter(ids))
        else:
            print(
                f"  Warning: email {email!r} maps to multiple user ids "
                f"{sorted(ids)}; leaving authors with this email unmatched"
            )
    return mapping


def normalize_authors(
    publications: list[dict[str, Any]], users: list[dict[str, Any]]
) -> pl.DataFrame:
    """Build authors table from full publication details.

    Matches authors to OJS user accounts by email where possible.
    """
    email_to_user_id = _build_email_to_user_id(users)

    rows: list[dict[str, Any]] = []
    for pub in publications:
        sub_id = pub["_submission_id"]
        # Sort by seq for display order, then assign 1-indexed author_number.
        # `or []` guards a null authors list; _author_seq_key folds a null/missing
        # seq to 0 (sorting None vs int crashes).
        raw_authors: list[dict[str, Any]] = pub.get("authors") or []
        authors = sorted(raw_authors, key=_author_seq_key)
        for n, author in enumerate(authors, start=1):
            email = author.get("email") or None
            rows.append(
                {
                    "submission_id": sub_id,
                    "author_number": n,
                    "user_id": email_to_user_id.get(email) if email else None,
                    "given_name": localized(author.get("givenName")),
                    "family_name": localized(author.get("familyName")),
                    "orcid_id": author.get("orcid") or None,
                    "country": author.get("country") or None,
                    "affiliation": localized(author.get("affiliation")),
                    "email": email,
                }
            )

    if not rows:
        return pl.DataFrame(schema=schemas.Authors.polars_schema())

    df = schemas.Authors.apply(pl.DataFrame(rows))
    df = df.sort("submission_id", "author_number")

    matched = df["user_id"].drop_nulls().len()
    print(f"  Matched {matched}/{df.height} authors to user accounts by email")

    return df


def normalize_publications(
    submissions_df: pl.DataFrame, publications: list[dict[str, Any]]
) -> pl.DataFrame:
    """Build publications table: published submissions joined with publication detail.

    Filters to publications with `status == 3` (OJS code for "Published"), which
    matches submissions whose statusLabel is "Published".
    """
    pub_rows: list[dict[str, Any]] = []
    for pub in publications:
        if pub.get("status") != 3:
            continue
        pub_rows.append(
            {
                "submission_id": pub["_submission_id"],
                "publication_id": pub.get("id"),
                "version": pub.get("version"),
                "subtitle": localized(pub.get("subtitle")),
                "authors_string": pub.get("authorsString") or None,
                "pages": pub.get("pages") or None,
                "seq": pub.get("seq"),
                "galley_count": len(pub.get("galleys") or []),
                "locale": pub.get("locale") or None,
                "license_url": pub.get("licenseUrl") or None,
                "copyright_holder": localized(pub.get("copyrightHolder")),
                "copyright_year": pub.get("copyrightYear"),
            }
        )

    schema = {
        "submission_id": pl.Int64,
        "publication_id": pl.Int64,
        "version": pl.Int64,
        "subtitle": pl.String,
        "authors_string": pl.String,
        "pages": pl.String,
        "seq": pl.Int64,
        "galley_count": pl.Int64,
        "locale": pl.String,
        "license_url": pl.String,
        "copyright_holder": pl.String,
        "copyright_year": pl.Int64,
    }

    pub_df = pl.DataFrame(pub_rows, schema=schema)

    sub_cols = [
        "submission_id",
        "title",
        "author_name",
        "author_email",
        "author_count",
        "abstract",
        "submission_type",
        "keywords",
        "doi",
        "url",
        "date_submitted",
        "date_published",
        "issue_id",
    ]
    published = submissions_df.filter(pl.col("status") == "Published").select(sub_cols)

    df = published.join(pub_df, on="submission_id", how="inner")

    # Column order and dtypes come from the schema class (the source of truth).
    df = schemas.Publications.apply(df)
    return df.sort(["date_published", "submission_id"], descending=[True, False])


def normalize_review_assignments(
    submissions_ext: list[dict[str, Any]],
) -> pl.DataFrame:
    """Build review assignments table from /_submissions endpoint data."""
    rows: list[dict[str, Any]] = []
    for sub in submissions_ext:
        for ra in sub.get("reviewAssignments") or []:
            rows.append(
                {
                    "submission_id": sub["id"],
                    "assignment_id": ra.get("id"),
                    "round": ra.get("round"),
                    "round_id": ra.get("roundId"),
                    "status_id": ra.get("statusId"),
                    "status": ra.get("status"),
                    "due": ra.get("due"),
                    "response_due": ra.get("responseDue"),
                }
            )

    if not rows:
        return pl.DataFrame(schema=schemas.ReviewAssignments.polars_schema())

    # Schema-driven cast parses due/response_due (Date) and enforces int dtypes.
    df = schemas.ReviewAssignments.apply(pl.DataFrame(rows))
    return df.sort("submission_id", "round", "assignment_id")


def normalize_submission_files(files: list[dict[str, Any]] | None) -> pl.DataFrame:
    """Build a submission-files table from `/submissions/{id}/files` records.

    One row per current `SubmissionFile`, carrying the workflow `file_stage`
    (with a readable `file_stage_label`), the `review_round_id` when the file
    belongs to a review round, and `revision_count` for how many prior uploads
    it has. The physical download id is `file_id`; the stable record id is
    `submission_file_id`. Joins to the other tables on `submission_id`, and to
    `review_assignments` on `review_round_id` == `round_id`.
    """
    # Imported here to keep the stage-label/round constants colocated with the
    # download logic that defines them.
    from ojs.api.files import review_round_id, stage_label

    rows: list[dict[str, Any]] = []
    for f in files or []:
        rows.append(
            {
                "submission_id": f.get("_submission_id"),
                "submission_file_id": f.get("id"),
                "file_id": f.get("fileId"),
                "file_stage": f.get("fileStage"),
                "file_stage_label": stage_label(f.get("fileStage")),
                "review_round_id": review_round_id(f),
                "genre_id": f.get("genreId"),
                "name": localized(f.get("name")),
                "mimetype": f.get("mimetype"),
                "document_type": f.get("documentType"),
                "uploader_user_id": f.get("uploaderUserId"),
                "source_submission_file_id": f.get("sourceSubmissionFileId"),
                "revision_count": len(f.get("revisions") or []),
                "created_at": f.get("createdAt"),
                "updated_at": f.get("updatedAt"),
                "url": f.get("url"),
            }
        )

    if not rows:
        return pl.DataFrame(schema=schemas.SubmissionFiles.polars_schema())

    df = schemas.SubmissionFiles.apply(pl.DataFrame(rows, infer_schema_length=None))
    return df.sort(["submission_id", "file_stage", "review_round_id", "file_id"])


def normalize_publication_stats(stats: list[dict[str, Any]] | None) -> pl.DataFrame:
    """Build a publication-stats table from /stats/publications records.

    One row per publication with abstract, all-galley, PDF, HTML, and other view
    totals. `submission_id` comes from the nested `publication.id`, which the OJS
    stats API keys on the submission, so it joins to the other tables. Records
    without a publication id are dropped, since a null key cannot join.
    """
    rows: list[dict[str, Any]] = []
    for record in stats or []:
        publication = record.get("publication") or {}
        rows.append(
            {
                "submission_id": publication.get("id"),
                "abstract_views": record.get("abstractViews"),
                "galley_views": record.get("galleyViews"),
                "pdf_views": record.get("pdfViews"),
                "html_views": record.get("htmlViews"),
                "other_views": record.get("otherViews"),
            }
        )

    if not rows:
        return pl.DataFrame(schema=schemas.PublicationStats.polars_schema())

    df = schemas.PublicationStats.apply(pl.DataFrame(rows))
    # A null submission_id (missing publication id) cannot join; drop it.
    df = df.filter(pl.col("submission_id").is_not_null())
    return df.sort("submission_id")


def normalize_views_timeline(timeline: list[dict[str, Any]] | None) -> pl.DataFrame:
    """Build a long-format, per-submission views timeline.

    Each input point carries `_submission_id`, `kind` (`abstract` or `galley`),
    `date`, `value`, and `interval`. `date` is kept as a string because the
    interval can be a day (`YYYY-MM-DD`) or a month (`YYYY-MM`); `interval`
    records which, so a file holding both granularities stays separable -- group
    or filter on it rather than summing across intervals (that would double-count).

    The OJS stats endpoint pads every day in the journal's range with `value: 0`,
    so the raw series is dominated by zeros (~60% on a multi-year journal). Those
    zero rows are dropped: a missing `(submission_id, interval, date, kind)` means
    zero views. The journal-wide totals stay recoverable by grouping the kept rows
    on `date` and `kind` within an interval (zeros don't affect a sum). Consumers
    needing a gap-free daily axis should reindex/fill on their side.
    """
    rows: list[dict[str, Any]] = []
    for point in timeline or []:
        rows.append(
            {
                "submission_id": point.get("_submission_id"),
                "date": point.get("date"),
                "interval": point.get("interval"),
                "views": point.get("value"),
                "kind": point.get("kind"),
            }
        )

    if not rows:
        return pl.DataFrame(schema=schemas.ViewsTimeline.polars_schema())

    df = schemas.ViewsTimeline.apply(pl.DataFrame(rows))
    return df.filter(pl.col("views") > 0).sort(
        ["submission_id", "interval", "kind", "date"]
    )


def normalize_views_timeline_totals(
    timeline: list[dict[str, Any]] | None,
) -> pl.DataFrame:
    """Build a long-format, journal-wide views timeline.

    Each input point carries `kind` (`abstract` or `galley`), `date`, `value`,
    and `interval` -- the aggregate counts across all publications, matching the
    OJS statistics-page graph. `date` is kept as a string because the interval
    can be a day (`YYYY-MM-DD`) or a month (`YYYY-MM`); `interval` records which,
    so a file mixing granularities stays separable rather than double-counted.
    """
    rows: list[dict[str, Any]] = []
    for point in timeline or []:
        rows.append(
            {
                "date": point.get("date"),
                "interval": point.get("interval"),
                "views": point.get("value"),
                "kind": point.get("kind"),
            }
        )

    if not rows:
        return pl.DataFrame(schema=schemas.ViewsTimelineTotals.polars_schema())

    df = schemas.ViewsTimelineTotals.apply(pl.DataFrame(rows))
    return df.sort(["interval", "kind", "date"])


def normalize_api(
    submissions: list[dict[str, Any]],
    publications: list[dict[str, Any]],
    submissions_ext: list[dict[str, Any]],
    users: list[dict[str, Any]],
    output_dir: Path,
    publication_stats: list[dict[str, Any]] | None = None,
    views_timeline: list[dict[str, Any]] | None = None,
    views_timeline_totals: list[dict[str, Any]] | None = None,
    submission_files: list[dict[str, Any]] | None = None,
) -> None:
    """Run the full API normalization pipeline."""
    output_dir.mkdir(exist_ok=True, parents=True)

    print("Normalizing API data...")

    subs_df = normalize_submissions(submissions, publications)
    print(f"Submissions: {subs_df.shape[0]} rows, {subs_df.shape[1]} columns")

    pubs_df = normalize_publications(subs_df, publications)
    print(f"Publications: {pubs_df.shape[0]} rows, {pubs_df.shape[1]} columns")

    authors_df = normalize_authors(publications, users)
    print(f"Authors: {authors_df.shape[0]} rows, {authors_df.shape[1]} columns")

    reviews_df = normalize_review_assignments(submissions_ext)
    print(
        f"Review assignments: {reviews_df.shape[0]} rows, {reviews_df.shape[1]} columns"
    )

    tables = {
        "submissions": subs_df,
        "publications": pubs_df,
        "authors": authors_df,
        "review_assignments": reviews_df,
    }

    if submission_files is not None:
        files_df = normalize_submission_files(submission_files)
        rows, cols = files_df.shape
        print(f"Submission files: {rows} rows, {cols} columns")
        tables["submission_files"] = files_df

    if publication_stats is not None:
        stats_df = normalize_publication_stats(publication_stats)
        print(
            f"Publication stats: {stats_df.shape[0]} rows, {stats_df.shape[1]} columns"
        )
        tables["publication_stats"] = stats_df

    if views_timeline is not None:
        timeline_df = normalize_views_timeline(views_timeline)
        rows, cols = timeline_df.shape
        print(f"Views timeline: {rows} rows, {cols} columns")
        tables["views_timeline"] = timeline_df

    if views_timeline_totals is not None:
        totals_df = normalize_views_timeline_totals(views_timeline_totals)
        rows, cols = totals_df.shape
        print(f"Views timeline totals: {rows} rows, {cols} columns")
        tables["views_timeline_totals"] = totals_df

    for name, df in tables.items():
        output_file = output_dir / f"{name}.csv"
        df.write_csv(output_file)
        print(f"Saved {name} to {output_file}")

    print(f"\nNormalization complete! All tables saved to {output_dir}/")

    # Show sample
    if subs_df.shape[0] > 0:
        core_cols = ["submission_id", "title", "status", "date_submitted"]
        available = [c for c in core_cols if c in subs_df.columns]
        print("\nSample from submissions:")
        print(subs_df.select(available).head(3))
