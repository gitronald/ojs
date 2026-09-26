"""Tests for the typed schema framework and schema/output parity."""

import logging
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from ojs.schema import Column, Table


# --------------------------------------------------------------------------- #
# Framework mechanics
# --------------------------------------------------------------------------- #
class Sample(Table):
    name = "sample"
    description = "a sample table"

    submission_id = Column(pl.Int64, "primary key", "Submission ID")
    given_name = Column(pl.String, "given", "Given Name (Author N)")
    author_number = Column(pl.Int64, "derived order")  # derived (no source)
    when = Column(pl.Datetime("us"), "a timestamp", "When")
    dropped_col = Column(pl.String, "documented but dropped", "Dropped", dropped=True)


def test_column_name_autobinds_from_attribute():
    assert Sample.submission_id.name == "submission_id"
    assert Sample.given_name.name == "given_name"


def test_columns_collected_in_definition_order():
    assert Sample.names() == [
        "submission_id",
        "given_name",
        "author_number",
        "when",
        "dropped_col",
    ]


def test_polars_schema_includes_fully_specified_datetime():
    schema = Sample.polars_schema()
    assert schema["submission_id"] == pl.Int64
    assert schema["when"] == pl.Datetime("us")


def test_rename_map_excludes_derived_columns():
    rename = Sample.rename_map()
    assert rename["Submission ID"] == "submission_id"
    assert rename["Given Name (Author N)"] == "given_name"
    # derived columns (no source) are not in the rename map
    assert "author_number" not in rename.values() or "author_number" not in rename


def test_field_map_strips_pattern_suffix():
    assert Sample.field_map("Author N") == {"Given Name": "given_name"}


def test_dropped_columns_and_doc_rows():
    assert Sample.dropped_columns() == ["dropped_col"]
    rows = {r["column_name"]: r for r in Sample.doc_rows()}
    assert rows["submission_id"]["in_output"] == "true"
    assert rows["dropped_col"]["in_output"] == "false"
    assert rows["when"]["data_type"] == "Datetime"
    assert rows["submission_id"]["original_column"] == "Submission ID"
    assert rows["author_number"]["original_column"] == "N/A"


def test_reserved_name_collision_raises():
    # A column attribute named `name` collides with Table.name; build the class
    # dynamically so the runtime guard fires without a static type error.
    with pytest.raises(TypeError, match="reserved"):
        type("Bad", (Table,), {"name": Column(pl.String, "oops")})


# --------------------------------------------------------------------------- #
# apply(): cast, order, drop unexpected, exclude dropped, allow subset
# --------------------------------------------------------------------------- #
def test_apply_casts_parses_and_orders():
    raw = pl.DataFrame(
        {
            "when": ["2024-01-02 03:04:05", "bad"],  # String -> Datetime, strict=False
            "submission_id": ["10", "11"],  # String -> Int64
            "given_name": ["A", "B"],
            "author_number": [1, 2],
        }
    )
    out = Sample.apply(raw)
    # order follows the schema (dropped_col absent -> subset), not the input
    assert out.columns == ["submission_id", "given_name", "author_number", "when"]
    assert out.schema["submission_id"] == pl.Int64
    assert out.schema["when"] == pl.Datetime("us")
    assert out["submission_id"].to_list() == [10, 11]
    assert out["when"][1] is None  # unparseable -> null


def test_apply_drops_unexpected_and_excludes_dropped(caplog):
    raw = pl.DataFrame(
        {
            "submission_id": [1],
            "given_name": ["x"],
            "author_number": [1],
            "when": [None],
            "dropped_col": ["present-but-dropped"],
            "surprise": ["unmapped"],
        }
    )
    out = Sample.apply(raw)
    # unexpected column dropped; dropped-flagged column excluded from output
    assert "surprise" not in out.columns
    assert "dropped_col" not in out.columns
    warning = caplog.text
    assert "surprise" in warning  # unexpected column is surfaced
    assert "dropped_col" not in warning  # intended exclusion is not noisy


def test_apply_allows_subset_without_warning(caplog):
    # missing a schema column (when) is an allowed subset -> no warning, no fill
    raw = pl.DataFrame(
        {"submission_id": [1], "given_name": ["x"], "author_number": [1]}
    )
    out = Sample.apply(raw)
    assert out.columns == ["submission_id", "given_name", "author_number"]
    assert "WARNING" not in caplog.text


# --------------------------------------------------------------------------- #
# Schema/output parity: the classes must match what normalize actually emits.
# --------------------------------------------------------------------------- #
def _api_fixtures() -> dict[str, Any]:
    pub = {
        "_submission_id": 1,
        "id": 100,
        "status": 3,
        "version": 1,
        "subtitle": {"en_US": "Sub"},
        "authorsString": "A. Author",
        "pages": "1-10",
        "seq": 1,
        "galleys": [{"id": 1}],
        "locale": "en_US",
        "licenseUrl": "http://lic",
        "copyrightHolder": {"en_US": "H"},
        "copyrightYear": 2024,
        "abstract": {"en_US": "<p>Abs</p>"},
        "keywords": {"en_US": ["k1", "k2"]},
        "issueId": 7,
        "authors": [
            {
                "seq": 1,
                "givenName": {"en_US": "A"},
                "familyName": {"en_US": "Author"},
                "orcid": "0000",
                "country": "US",
                "affiliation": {"en_US": "U"},
                "email": "a@x.org",
            }
        ],
    }
    sub = {
        "id": 1,
        "statusLabel": "Published",
        "dateSubmitted": "2024-01-01 09:00:00",
        "lastModified": "2024-02-01 10:00:00",
        "stageId": 5,
        "publications": [
            {
                "fullTitle": {"en_US": "Title"},
                "urlPublished": "http://u",
                "pub-id::doi": "10.1/x",
                "datePublished": "2024-03-01",
                "sectionId": 1,
            }
        ],
    }
    return {
        "submissions": [sub],
        "publications": [pub],
        "users": [{"id": 42, "email": "a@x.org"}],
        "subs_ext": [
            {
                "id": 1,
                "reviewAssignments": [
                    {
                        "id": 5,
                        "round": 1,
                        "roundId": 3,
                        "statusId": 4,
                        "status": "complete",
                        "due": "2024-04-01",
                        "responseDue": "2024-03-15",
                    }
                ],
            }
        ],
        "files": [
            {
                "_submission_id": 1,
                "id": 100,
                "fileId": 900,
                "fileStage": 15,
                "genreId": 1,
                "name": {"en_US": "f.pdf"},
                "mimetype": "application/pdf",
                "documentType": "pdf",
                "uploaderUserId": 42,
                "sourceSubmissionFileId": 80,
                "revisions": [{"r": 1}],
                "createdAt": "2024-01-01 00:00:00",
                "updatedAt": "2024-02-01 00:00:00",
                "url": "http://f/900",
                "assocType": 521,
                "assocId": 3,
            }
        ],
        "stats": [
            {
                "publication": {"id": 2},
                "abstractViews": 5,
                "galleyViews": 30,
                "pdfViews": 20,
                "htmlViews": 10,
                "otherViews": 0,
            }
        ],
        "timeline": [
            {
                "_submission_id": 2,
                "date": "2024-01-01",
                "value": 12,
                "kind": "abstract",
                "interval": "day",
            }
        ],
        "totals": [
            {"date": "2024-01", "value": 8, "kind": "abstract", "interval": "month"}
        ],
    }


def test_api_schemas_match_normalized_output():
    from ojs.api import normalize as N
    from ojs.api import schemas as S

    fx = _api_fixtures()
    subs = N.normalize_submissions(fx["submissions"], fx["publications"])
    cases = [
        (subs, S.Submissions),
        (N.normalize_publications(subs, fx["publications"]), S.Publications),
        (N.normalize_authors(fx["publications"], fx["users"]), S.Authors),
        (N.normalize_review_assignments(fx["subs_ext"]), S.ReviewAssignments),
        (N.normalize_submission_files(fx["files"]), S.SubmissionFiles),
        (N.normalize_publication_stats(fx["stats"]), S.PublicationStats),
        (N.normalize_views_timeline(fx["timeline"]), S.ViewsTimeline),
        (N.normalize_views_timeline_totals(fx["totals"]), S.ViewsTimelineTotals),
    ]
    for df, table in cases:
        assert dict(df.schema) == dict(table.polars_schema()), table.name


def test_api_empty_inputs_yield_typed_frames():
    from ojs.api import normalize as N
    from ojs.api import schemas as S

    assert dict(N.normalize_submissions([], []).schema) == dict(
        S.Submissions.polars_schema()
    )
    assert dict(N.normalize_authors([], []).schema) == dict(S.Authors.polars_schema())
    assert dict(N.normalize_submission_files(None).schema) == dict(
        S.SubmissionFiles.polars_schema()
    )
    assert dict(N.normalize_publication_stats(None).schema) == dict(
        S.PublicationStats.polars_schema()
    )


def _write_articles_csv(path) -> None:
    author_fields = {
        "Given Name (Author 1)": "Ada",
        "Family Name (Author 1)": "Lovelace",
        "ORCID iD (Author 1)": "0000-1",
        "Country (Author 1)": "GB",
        "Affiliation (Author 1)": "Analytical Engine",
        "Email (Author 1)": "ada@x.org",
        "Homepage URL (Author 1)": "http://ada",
        "Bio Statement (e.g., department and rank) (Author 1)": "Mathematician",
    }
    editor_fields = {
        "Given Name (Editor 1)": "Charles",
        "Family Name (Editor 1)": "Babbage",
        "ORCID iD (Editor 1)": "0000-2",
        "Email (Editor 1)": "charles@x.org",
    }
    row = {
        "Submission ID": 1,
        "Title": "On Computing",
        "Abstract": "<p>An abstract</p>",
        "Section title": "Peer-reviewed Articles",
        "Language": "en_US",
        "Coverage": None,  # always-empty -> dropped
        "Keywords": "computing",
        "Status": "Published",
        "URL": "http://u",
        "DOI": "10.1/x",
        "Date submitted": "2024-01-01 09:00:00",
        "Last modified": "2024-02-01 10:00:00",
        "Mystery Column": "unmapped",  # unexpected -> surfaced & dropped
        **author_fields,
        **editor_fields,
        "Editor Decision 1  (Editor 1)": "Accept",
        "Date decided 1  (Editor 1)": "2024-02-15 12:00:00",
    }
    pl.DataFrame([row]).write_csv(path)


def test_article_normalize_matches_schema(tmp_path, caplog):
    from ojs.website.articles import normalize as A
    from ojs.website.articles.schemas import Authors, Decisions, Editors, Submissions

    raw = tmp_path / "articles-2024.csv"
    _write_articles_csv(raw)
    out = tmp_path / "out"
    A.normalize(raw, out)

    def expected(table):
        return {c.name: c.dtype for c in table.columns if not c.dropped}

    subs = pl.read_csv(out / "submissions.csv", try_parse_dates=False)
    # dropped/unmapped columns are absent from the normalized output
    assert "language" not in subs.columns
    assert "coverage" not in subs.columns
    assert "Mystery Column" not in subs.columns
    assert subs.columns == [c.name for c in Submissions.columns if not c.dropped]

    authors = pl.read_csv(out / "authors.csv")
    assert authors.columns == [c.name for c in Authors.columns if not c.dropped]
    assert authors["family_name"].to_list() == ["Lovelace"]

    editors = pl.read_csv(out / "editors.csv")
    assert editors.columns == [c.name for c in Editors.columns]

    decisions = pl.read_csv(out / "decisions.csv")
    assert decisions.columns == [c.name for c in Decisions.columns]
    assert decisions["decision"].to_list() == ["Accept"]

    # the unmapped raw column is surfaced, not silently dropped
    assert "Mystery Column" in caplog.text


def test_reviews_normalize_matches_schema(tmp_path, caplog):
    from ojs.website.reviews import normalize as R
    from ojs.website.reviews.schemas import Reviews

    raw = tmp_path / "reviews-2024.csv"
    pl.DataFrame(
        [
            {
                "Submission ID": 1,
                "Round": 1,
                "Reviewer": "rev1",
                "Given Name": "Rae",
                "Family Name": "Viewer",
                "ORCID iD": "0000-9",
                "Country": "US",
                "Affiliation": "Uni",
                "Email": "rae@x.org",
                "Date Assigned": "2024-01-01 00:00:00",
                "Date Notified": "2024-01-02 00:00:00",
                "Date Confirmed": "2024-01-03 00:00:00",
                "Date Completed": "2024-01-10 00:00:00",
                "Date Acknowledged": "2024-01-11 00:00:00",
                "Date Reminded": "2024-01-05 00:00:00",
                "Response Due Date": "2024-01-04 00:00:00",
                "Response Overdue Days": 0,
                "Review Due Date": "2024-01-09 00:00:00",
                "Review Overdue Days": 0,
                "Declined": "No",
                "Recommendation": "Accept",
                "Comments On Submission": "<p>Good</p>",
                "Stage": "review",
                "Reviewing interests": "ml",  # known dropped
                "Mystery": "unmapped",  # unexpected
            }
        ]
    ).write_csv(raw)
    out = tmp_path / "out"
    R.normalize_reviews(raw, out)

    reviews = pl.read_csv(out / "reviews.csv")
    assert reviews.columns == [c.name for c in Reviews.columns if not c.dropped]
    assert "stage" not in reviews.columns  # documented-dropped
    assert reviews["declined"].to_list() == [False]  # Yes/No -> bool
    assert "Mystery" in caplog.text  # unmapped surfaced


def test_write_schema_docs_emits_in_output_flag(tmp_path):
    from ojs.schema import write_schema_docs
    from ojs.website.articles.schemas import ARTICLE_TABLES

    out = tmp_path / "table_schemas.csv"
    write_schema_docs(ARTICLE_TABLES, out)
    doc = pl.read_csv(out)
    assert set(doc["table_name"].unique()) == {
        "submissions",
        "authors",
        "editors",
        "decisions",
    }
    # dropped columns are documented with in_output=false
    dropped = doc.filter(pl.col("in_output") == "false")
    assert "coverage" in dropped["column_name"].to_list()


def _write_wide_articles_csv(path: Path) -> None:
    """An export wider than any fixed cap: 16 authors, 5 editors, decision 11."""
    row: dict[str, object] = {
        "Submission ID": 7,
        "Title": "Many Hands",
        "Status": "Published",
        "Date submitted": "2024-01-01 09:00:00",
    }
    for n in range(1, 17):
        row[f"Given Name (Author {n})"] = f"A{n}"
        row[f"Family Name (Author {n})"] = f"Author{n}"
    for n in range(1, 6):
        row[f"Given Name (Editor {n})"] = f"E{n}"
        row[f"Family Name (Editor {n})"] = f"Editor{n}"
        row[f"Email (Editor {n})"] = f"e{n}@x.org"
    # Decisions land where the export puts them, not at the low indices only:
    # editor 1's eleventh decision and editor 5's first and tenth.
    row["Editor Decision 11  (Editor 1)"] = "Accept"
    row["Date decided 11  (Editor 1)"] = "2024-03-01 12:00:00"
    row["Editor Decision 1  (Editor 5)"] = "Send to Review"
    row["Date decided 1  (Editor 5)"] = "2024-02-01 12:00:00"
    row["Editor Decision 10  (Editor 5)"] = "Decline"
    # A date column with no decision partner is claimed but yields no row.
    row["Date decided 4  (Editor 2)"] = "2024-02-10 12:00:00"
    pl.DataFrame([row]).write_csv(path)


def test_article_normalize_follows_the_export_width(tmp_path, caplog):
    """Indices come from the headers, so nothing past an old cap is dropped."""
    from ojs.website.articles import normalize as A

    raw = tmp_path / "articles-2024.csv"
    _write_wide_articles_csv(raw)
    out = tmp_path / "out"
    with caplog.at_level(logging.WARNING, logger="ojs"):
        tables = A.normalize(raw, out)

    assert "not mapped" not in caplog.text

    authors = tables["authors"]
    assert authors["author_number"].to_list() == list(range(1, 17))
    assert tables["submissions"]["author_count"].to_list() == [16]

    editors = tables["editors"]
    assert editors["editor_number"].to_list() == [1, 2, 3, 4, 5]
    assert editors.filter(pl.col("editor_number") == 5)["email"].to_list() == [
        "e5@x.org"
    ]

    decisions = tables["decisions"]
    assert decisions.select("editor_number", "decision_number").rows() == [
        (1, 11),
        (5, 1),
        (5, 10),
    ]
    assert (
        decisions.filter(pl.col("decision_number") == 10)["date_decided"]
        .is_null()
        .all()
    )


def test_entity_numbers_reads_indices_off_mapped_headers():
    from ojs.website.articles.normalize import entity_numbers
    from ojs.website.articles.schemas import Authors, Editors

    columns = [
        "Submission ID",
        "Family Name (Author 12)",
        "Given Name (Author 3)",
        "Family Name (Editor 5)",
        "Shoe Size (Author 40)",  # a base the schema does not map
        "Editor Decision 2  (Editor 9)",  # not a profile field
    ]
    assert entity_numbers(columns, Authors.field_map("Author N"), "Author") == [3, 12]
    assert entity_numbers(columns, Editors.field_map("Editor N"), "Editor") == [5]


def test_entity_numbers_excludes_decision_headers_up_front():
    """A decision header never counts as an entity, even with a colluding base.

    ``Editor Decision 11  (Editor 1)`` also parses as ``<base> (Editor 1)`` with
    the base ``Editor Decision 11 ``; the exclusion must not depend on that base
    happening to be absent from the schema.
    """
    from ojs.website.articles.normalize import _claimed_columns, entity_numbers

    col = "Editor Decision 11  (Editor 1)"
    colluding = {"Editor Decision 11 ": "decision"}
    assert entity_numbers([col], colluding, "Editor") == []
    assert _claimed_columns([col]) == {col}


def test_decision_slots_pairs_headers_in_workflow_order():
    from ojs.website.articles.normalize import DecisionSlot, decision_slots

    columns = [
        "Date decided 10  (Editor 2)",
        "Editor Decision 1  (Editor 2)",
        "Editor Decision 10  (Editor 2)",
        "Editor Decision 3  (Editor 1)",
        "Family Name (Editor 1)",
    ]
    assert decision_slots(columns) == [
        DecisionSlot(1, 3, {"Editor Decision 3  (Editor 1)": "decision"}),
        DecisionSlot(2, 1, {"Editor Decision 1  (Editor 2)": "decision"}),
        DecisionSlot(
            2,
            10,
            {
                "Date decided 10  (Editor 2)": "date_decided",
                "Editor Decision 10  (Editor 2)": "decision",
            },
        ),
    ]


def test_article_claimed_columns_are_uncapped():
    from ojs.website.articles.normalize import _claimed_columns

    columns = [
        "Title",
        "Email (Author 99)",
        "ORCID iD (Editor 7)",
        "Editor Decision 42  (Editor 7)",
        "Date decided 42  (Editor 7)",
        "Mystery Column",
        "Shoe Size (Author 1)",
    ]
    assert _claimed_columns(columns) == set(columns) - {
        "Mystery Column",
        "Shoe Size (Author 1)",
    }
