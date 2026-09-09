"""Schema class for the normalized OJS review table (website CSV export).

Single source of truth for the reviews pipeline: column names, polars dtypes,
the raw CSV source headers, and the docs exported to ``table_schemas.csv``.
"""

import polars as pl

from ojs.schema import Column, Table

__all__ = ["Reviews", "REVIEW_TABLES"]


class Reviews(Table):
    name = "reviews"
    description = "Peer review assignments and outcomes"

    submission_id = Column(
        pl.Int64, "Foreign key linking to submissions table", "Submission ID"
    )
    round = Column(pl.Int64, "Review round number", "Round")
    username = Column(pl.String, "Reviewer username", "Reviewer")
    given_name = Column(pl.String, "Reviewer's first/given name", "Given Name")
    family_name = Column(pl.String, "Reviewer's last/family name", "Family Name")
    orcid_id = Column(
        pl.String, "ORCID identifier (persistent digital identifier)", "ORCID iD"
    )
    country = Column(pl.String, "Reviewer's country", "Country")
    affiliation = Column(pl.String, "Institutional affiliation", "Affiliation")
    email = Column(pl.String, "Reviewer's email address", "Email")
    date_assigned = Column(
        pl.Datetime("us"), "Date reviewer was assigned", "Date Assigned"
    )
    date_notified = Column(
        pl.Datetime("us"), "Date reviewer was notified", "Date Notified"
    )
    date_confirmed = Column(
        pl.Datetime("us"), "Date reviewer confirmed the assignment", "Date Confirmed"
    )
    date_completed = Column(
        pl.Datetime("us"), "Date review was completed", "Date Completed"
    )
    date_acknowledged = Column(
        pl.Datetime("us"), "Date review was acknowledged by editor", "Date Acknowledged"
    )
    date_reminded = Column(
        pl.Datetime("us"), "Date reviewer was sent a reminder", "Date Reminded"
    )
    response_due_date = Column(
        pl.Datetime("us"),
        "Deadline for reviewer to accept/decline",
        "Response Due Date",
    )
    response_overdue_days = Column(
        pl.Int64, "Days past response deadline", "Response Overdue Days"
    )
    review_due_date = Column(
        pl.Datetime("us"), "Deadline for review completion", "Review Due Date"
    )
    review_overdue_days = Column(
        pl.Int64, "Days past review deadline", "Review Overdue Days"
    )
    declined = Column(
        pl.Boolean, "Whether reviewer declined the assignment", "Declined"
    )
    recommendation = Column(
        pl.String,
        "Reviewer's recommendation (Accept, Revisions Required, etc.)",
        "Recommendation",
    )
    comments = Column(
        pl.String,
        "Reviewer's comments on the submission (HTML)",
        "Comments On Submission",
    )

    # Mapped/documented but dropped from the normalized output: always empty or
    # constant in the export (in_output=false in table_schemas.csv).
    stage = Column(
        pl.String, "Workflow stage; dropped from output.", "Stage", dropped=True
    )
    reviewing_interests = Column(
        pl.String,
        "Reviewer's reviewing interests; dropped from output.",
        "Reviewing interests",
        dropped=True,
    )
    unconsidered = Column(
        pl.String,
        "Unconsidered flag; dropped from output.",
        "Unconsidered",
        dropped=True,
    )
    submission_title = Column(
        pl.String,
        "Submission title (redundant with submissions table); dropped from output.",
        "Submission Title",
        dropped=True,
    )


REVIEW_TABLES: tuple[type[Table], ...] = (Reviews,)
