"""Schema classes for the normalized OJS article tables (website CSV export).

These classes are the single source of truth for the article pipeline: column
names, polars dtypes, the wide-CSV source headers (literal for submissions,
``(Author N)``/``(Editor N)`` patterns for the unpivoted tables), and the docs
exported to ``table_schemas.csv``.
"""

import polars as pl

from ojs.schema import Column, Table

__all__ = ["Submissions", "Authors", "Editors", "Decisions", "ARTICLE_TABLES"]


class Submissions(Table):
    name = "submissions"
    description = "Core article submission information"

    submission_id = Column(
        pl.Int64,
        "Unique identifier for journal submission (Primary Key)",
        "Submission ID",
    )
    author_name = Column(
        pl.String, "First author's family name (derived from Author 1)"
    )
    author_email = Column(
        pl.String, "First author's email address (derived from Author 1)"
    )
    author_count = Column(
        pl.Int64, "Total number of authors for this submission (calculated)"
    )
    title = Column(pl.String, "Article title as submitted by authors", "Title")
    abstract = Column(pl.String, "Article abstract text", "Abstract")
    submission_type = Column(
        pl.String, "Journal section classification", "Section title"
    )
    # Mapped/documented but dropped from the normalized output: always empty
    # (or constant) for this journal. `dropped=True` flags them as out-of-output
    # (in_output=false in table_schemas.csv); normalize derives its drop list
    # from this flag and surfaces any unexpected data.
    language = Column(
        pl.String,
        "Language of the article (typically en_US); dropped from output (constant).",
        "Language",
        dropped=True,
    )
    coverage = Column(
        pl.String,
        "Geographic/topical coverage info; dropped from output (always empty).",
        "Coverage",
        dropped=True,
    )
    rights = Column(
        pl.String,
        "Copyright and usage rights information; dropped from output (always empty).",
        "Rights",
        dropped=True,
    )
    source = Column(
        pl.String,
        "Source or origin information; dropped from output (always empty).",
        "Source",
        dropped=True,
    )
    subjects = Column(
        pl.String,
        "Subject classification terms; dropped from output (always empty).",
        "Subjects",
        dropped=True,
    )
    type = Column(
        pl.String,
        "Article type; dropped from output (always empty).",
        "Type",
        dropped=True,
    )
    disciplines = Column(
        pl.String,
        "Academic discipline classifications; dropped from output (always empty).",
        "Disciplines",
        dropped=True,
    )
    keywords = Column(pl.String, "Author-provided keywords for the article", "Keywords")
    supporting_agencies = Column(
        pl.String,
        "Funding/institutional support; dropped from output (always empty).",
        "Supporting Agencies",
        dropped=True,
    )
    status = Column(
        pl.String, "Current publication status (Published, Declined, etc.)", "Status"
    )
    url = Column(pl.String, "Publication URL if published", "URL")
    doi = Column(pl.String, "Digital Object Identifier if assigned", "DOI")
    date_submitted = Column(
        pl.Datetime("us"), "Date and time when article was submitted", "Date submitted"
    )
    last_modified = Column(
        pl.Datetime("us"), "Date and time of last modification", "Last modified"
    )


class Authors(Table):
    name = "authors"
    description = "Author information linked to submissions"

    submission_id = Column(
        pl.Int64, "Foreign key linking to submissions table", "Submission ID"
    )
    author_number = Column(
        pl.Int64, "Sequential author number for this submission (1-15)"
    )
    given_name = Column(pl.String, "Author's first/given name", "Given Name (Author N)")
    family_name = Column(
        pl.String, "Author's last/family name", "Family Name (Author N)"
    )
    orcid_id = Column(
        pl.String,
        "ORCID identifier (persistent digital identifier)",
        "ORCID iD (Author N)",
    )
    country = Column(pl.String, "Author's country of affiliation", "Country (Author N)")
    affiliation = Column(
        pl.String, "Institutional affiliation", "Affiliation (Author N)"
    )
    email = Column(pl.String, "Author's email address", "Email (Author N)")
    homepage_url = Column(
        pl.String,
        "Author's personal or professional website",
        "Homepage URL (Author N)",
    )
    bio_statement = Column(
        pl.String,
        "Biographical info including department and academic rank",
        "Bio Statement (e.g., department and rank) (Author N)",
    )


class Editors(Table):
    name = "editors"
    description = "Editor information for submissions in peer review"

    submission_id = Column(
        pl.Int64, "Foreign key linking to submissions table", "Submission ID"
    )
    editor_number = Column(
        pl.Int64, "Sequential editor number for this submission (1-4)"
    )
    given_name = Column(pl.String, "Editor's first/given name", "Given Name (Editor N)")
    family_name = Column(
        pl.String, "Editor's last/family name", "Family Name (Editor N)"
    )
    orcid_id = Column(
        pl.String,
        "ORCID identifier (persistent digital identifier)",
        "ORCID iD (Editor N)",
    )
    email = Column(pl.String, "Editor's email address", "Email (Editor N)")


class Decisions(Table):
    name = "decisions"
    description = "Editorial workflow decisions and timeline"

    submission_id = Column(
        pl.Int64, "Foreign key linking to submissions table", "Submission ID"
    )
    editor_number = Column(pl.Int64, "Editor number (1-4) making this decision")
    decision_number = Column(
        pl.Int64, "Sequential decision number for this editor (1-9)"
    )
    decision = Column(
        pl.String,
        "Editorial decision text (Send to Review, Accept, etc)",
        "Editor Decision N  (Editor M)",
    )
    date_decided = Column(
        pl.Datetime("us"),
        "Date and time when editorial decision was made",
        "Date decided N  (Editor M)",
    )


ARTICLE_TABLES: tuple[type[Table], ...] = (Submissions, Authors, Editors, Decisions)
