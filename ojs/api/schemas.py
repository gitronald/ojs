"""Schema classes for the normalized OJS API tables.

Single source of truth for the API pipeline's relational outputs. Columns,
dtypes, and order are taken from the proven `ojs.api.normalize` outputs (the
four stats/files tables are also test-covered); the ``source`` field records the
originating API JSON field (or ``None`` for derived columns) and descriptions
draw on ``swagger.json``.

``Submissions``, ``Publications``, ``Authors``, ``ReviewAssignments``,
``Users``, and ``UserGroups`` are emitted unconditionally; the remaining four
tables are emitted only when their optional JSON dumps are present.
"""

import polars as pl

from ojs.schema import Column, Table

__all__ = [
    "Submissions",
    "Publications",
    "Authors",
    "ReviewAssignments",
    "Users",
    "UserGroups",
    "SubmissionFiles",
    "PublicationStats",
    "ViewsTimeline",
    "ViewsTimelineTotals",
    "API_TABLES",
]


class Submissions(Table):
    name = "submissions"
    description = (
        "One row per submission, with first-author and publication summary detail"
    )

    submission_id = Column(pl.Int64, "Submission identifier (primary key).", "id")
    author_name = Column(
        pl.String,
        "Family name of the first author.",
        "publications[].authors[0].familyName",
    )
    author_email = Column(
        pl.String, "Email of the first author.", "publications[].authors[0].email"
    )
    author_count = Column(
        pl.Int64, "Number of authors on the publication; null if zero."
    )
    title = Column(pl.String, "Full submission title.", "publications[0].fullTitle")
    abstract = Column(
        pl.String, "Plain-text abstract (HTML stripped).", "publications[].abstract"
    )
    submission_type = Column(
        pl.String, "Section title mapped from sectionId.", "publications[0].sectionId"
    )
    keywords = Column(
        pl.String, "Semicolon-joined en_US keywords.", "publications[].keywords"
    )
    status = Column(pl.String, "Human-readable submission status label.", "statusLabel")
    url = Column(
        pl.String, "Published URL of the submission.", "publications[0].urlPublished"
    )
    doi = Column(pl.String, "DOI of the publication.", "publications[0].pub-id::doi")
    date_submitted = Column(
        pl.Datetime("us"),
        "Datetime the submission was submitted. Naive server-local time (the "
        "API reports no offset).",
        "dateSubmitted",
    )
    last_modified = Column(
        pl.Datetime("us"),
        "Datetime the submission or one of its associated objects was last "
        "modified. Naive server-local time (the API reports no offset).",
        "lastModified",
    )
    date_last_activity = Column(
        pl.Datetime("us"),
        "Datetime the last activity was recorded on the submission (the field "
        "the OJS API orders and syncs by; it moves on a stage change, unlike "
        "last_modified). Naive server-local time. This is the latest activity, "
        "not the "
        "stage-change date: the next action overwrites it, so a consumer that "
        "needs the date a stage was entered must capture this value the first "
        "time it sees the new stage_id.",
        "dateLastActivity",
    )
    date_published = Column(
        pl.Date, "Publication date (date-only).", "publications[0].datePublished"
    )
    stage_id = Column(pl.Int64, "Current editorial workflow stage id.", "stageId")
    review_round = Column(
        pl.Int64,
        "Number of the latest review round opened (highest review stage, then "
        "highest round within it); null when no round has been opened. From "
        "the extended submissions endpoint.",
        "_submissions[].reviewRounds[].round",
    )
    review_round_status = Column(
        pl.String,
        "Human-readable status of the latest review round, e.g. 'Waiting for "
        "reviewers to be assigned.'; null when no round has been opened.",
        "_submissions[].reviewRounds[].status",
    )
    issue_id = Column(
        pl.Int64, "Issue id the publication belongs to.", "publications[].issueId"
    )


class Publications(Table):
    name = "publications"
    description = "One row per published publication, joined with submission detail"

    submission_id = Column(
        pl.Int64, "Submission identifier (join key).", "_submission_id"
    )
    publication_id = Column(pl.Int64, "Publication identifier.", "id")
    date_published = Column(pl.Date, "Publication date (date-only), from submissions.")
    title = Column(pl.String, "Full title, from submissions.")
    subtitle = Column(pl.String, "Publication subtitle.", "subtitle")
    authors_string = Column(
        pl.String, "Pre-formatted author list string.", "authorsString"
    )
    author_count = Column(pl.Int64, "Number of authors, from submissions.")
    doi = Column(pl.String, "DOI, from submissions.")
    url = Column(pl.String, "Published URL, from submissions.")
    issue_id = Column(pl.Int64, "Issue id, from submissions.")
    submission_type = Column(pl.String, "Section title, from submissions.")
    pages = Column(pl.String, "Page range string.", "pages")
    seq = Column(pl.Int64, "Sequence/order of the publication within its issue.", "seq")
    version = Column(pl.Int64, "Publication version number.", "version")
    locale = Column(pl.String, "Primary locale of the publication.", "locale")
    license_url = Column(pl.String, "License URL for the publication.", "licenseUrl")
    copyright_holder = Column(pl.String, "Copyright holder name.", "copyrightHolder")
    copyright_year = Column(pl.Int64, "Copyright year.", "copyrightYear")
    galley_count = Column(
        pl.Int64, "Number of galleys attached to the publication.", "galleys"
    )
    keywords = Column(pl.String, "Semicolon-joined keywords, from submissions.")
    abstract = Column(pl.String, "Plain-text abstract, from submissions.")
    author_name = Column(pl.String, "First author family name, from submissions.")
    author_email = Column(pl.String, "First author email, from submissions.")
    date_submitted = Column(pl.Datetime("us"), "Datetime submitted, from submissions.")


class Authors(Table):
    name = "authors"
    description = (
        "One row per author per publication, matched to user accounts by email"
    )

    submission_id = Column(
        pl.Int64, "Submission the author belongs to.", "_submission_id"
    )
    author_number = Column(
        pl.Int64, "Author's display order within the publication (1-indexed)."
    )
    user_id = Column(
        pl.Int64, "Matched OJS user account id by email; null if unmatched."
    )
    given_name = Column(pl.String, "Author given name.", "givenName")
    family_name = Column(pl.String, "Author family name.", "familyName")
    orcid_id = Column(pl.String, "Author ORCID identifier.", "orcid")
    country = Column(pl.String, "Author country code.", "country")
    affiliation = Column(pl.String, "Author affiliation.", "affiliation")
    email = Column(pl.String, "Author email address.", "email")


class ReviewAssignments(Table):
    name = "review_assignments"
    description = "Peer review assignments from the extended submissions endpoint"

    submission_id = Column(
        pl.Int64, "Submission the review assignment belongs to.", "id"
    )
    assignment_id = Column(
        pl.Int64, "Review assignment identifier.", "reviewAssignments[].id"
    )
    round = Column(pl.Int64, "Review round number.", "reviewAssignments[].round")
    round_id = Column(
        pl.Int64,
        "Review round id (joins to submission_files.review_round_id).",
        "reviewAssignments[].roundId",
    )
    status_id = Column(
        pl.Int64, "Numeric review status code.", "reviewAssignments[].statusId"
    )
    status = Column(
        pl.String, "Human-readable review status.", "reviewAssignments[].status"
    )
    due = Column(pl.Date, "Review due date (date-only).", "reviewAssignments[].due")
    response_due = Column(
        pl.Date, "Response due date (date-only).", "reviewAssignments[].responseDue"
    )


class Users(Table):
    name = "users"
    description = "One row per OJS user account, active and disabled"

    user_id = Column(pl.Int64, "User account identifier (primary key).", "id")
    username = Column(pl.String, "Login username.", "userName")
    email = Column(pl.String, "Account email address.", "email")
    full_name = Column(pl.String, "Full name in the primary locale.", "fullName")
    orcid = Column(pl.String, "ORCID iD as stored; null if none is set.", "orcid")
    disabled = Column(pl.Boolean, "Whether the account is disabled.", "disabled")
    country = Column(
        pl.String,
        "Country code; null unless `ojs api fetch --user-details` fetched the account.",
        "user_details[].country",
    )
    affiliation = Column(
        pl.String,
        "Affiliation (en_US, else any locale); null without user details.",
        "user_details[].affiliation",
    )
    date_registered = Column(
        pl.Datetime("us"),
        "Datetime the account was registered; null without user details. Naive "
        "server-local time (the API reports no offset).",
        "user_details[].dateRegistered",
    )
    date_last_login = Column(
        pl.Datetime("us"),
        "Datetime of the account's last login, as of the fetch; null without "
        "user details. Naive server-local time.",
        "user_details[].dateLastLogin",
    )


class UserGroups(Table):
    name = "user_groups"
    description = "One row per user per user group the account belongs to"

    user_id = Column(pl.Int64, "User account identifier (joins users).", "id")
    group_id = Column(pl.Int64, "User group identifier.", "groups[].id")
    group_name = Column(
        pl.String,
        "User group name (en_US, else any locale); install-specific.",
        "groups[].name",
    )
    group_abbrev = Column(
        pl.String,
        "User group abbreviation (en_US, else any locale).",
        "groups[].abbrev",
    )
    role_id = Column(
        pl.Int64,
        "OJS role id of the group: 16 manager, 17 sub editor, 65536 author, "
        "4096 reviewer, 4097 assistant, 1048576 reader, 2097152 subscription "
        "manager, 1 site admin.",
        "groups[].roleId",
    )


class SubmissionFiles(Table):
    name = "submission_files"
    description = "Current submission files with stage, review round, and revision info"

    submission_id = Column(
        pl.Int64, "Submission the file belongs to (join key).", "_submission_id"
    )
    submission_file_id = Column(pl.Int64, "Stable SubmissionFile record id.", "id")
    file_id = Column(pl.Int64, "Physical file id used for downloads.", "fileId")
    file_stage = Column(pl.Int64, "Workflow file-stage id.", "fileStage")
    file_stage_label = Column(pl.String, "Readable file-stage name.")
    review_round_id = Column(
        pl.Int64,
        "Review round id if the file is in a review round (joins review_assignments).",
    )
    genre_id = Column(
        pl.Int64, "File genre id (e.g. article text, supplementary).", "genreId"
    )
    # Output column is "name"; the Python attribute is distinct to avoid
    # colliding with Table.name (the reserved table-name metadata).
    file_name = Column(pl.String, "File display name.", "name", name="name")
    mimetype = Column(pl.String, "MIME type of the file.", "mimetype")
    document_type = Column(
        pl.String, "OJS document type classification.", "documentType"
    )
    uploader_user_id = Column(pl.Int64, "User id of the uploader.", "uploaderUserId")
    source_submission_file_id = Column(
        pl.Int64,
        "Id of the source file this was derived from, if any.",
        "sourceSubmissionFileId",
    )
    revision_count = Column(pl.Int64, "Number of prior uploads/revisions for the file.")
    created_at = Column(
        pl.String, "Creation timestamp (kept as raw string, not parsed).", "createdAt"
    )
    updated_at = Column(
        pl.String, "Update timestamp (kept as raw string, not parsed).", "updatedAt"
    )
    url = Column(pl.String, "API download URL for the file.", "url")


class PublicationStats(Table):
    name = "publication_stats"
    description = "Per-publication cumulative view totals"

    submission_id = Column(
        pl.Int64,
        "Submission id (stats keyed by publication; join key).",
        "publication.id",
    )
    abstract_views = Column(pl.Int64, "Total abstract page views.", "abstractViews")
    galley_views = Column(pl.Int64, "Total all-galley views.", "galleyViews")
    pdf_views = Column(pl.Int64, "Total PDF galley views.", "pdfViews")
    html_views = Column(pl.Int64, "Total HTML galley views.", "htmlViews")
    other_views = Column(pl.Int64, "Total other-format views.", "otherViews")


class ViewsTimeline(Table):
    name = "views_timeline"
    description = "Long-format per-submission views timeline (zero-value rows dropped)"

    submission_id = Column(
        pl.Int64, "Submission the timeline point belongs to.", "_submission_id"
    )
    date = Column(
        pl.String,
        "Period label kept as string (day YYYY-MM-DD or month YYYY-MM).",
        "date",
    )
    interval = Column(
        pl.String,
        "Timeline granularity the point was fetched at: 'day' or 'month'.",
        "interval",
    )
    views = Column(
        pl.Int64, "View count for that submission/date/kind (always > 0).", "value"
    )
    kind = Column(pl.String, "View kind: 'abstract' or 'galley'.", "kind")


class ViewsTimelineTotals(Table):
    name = "views_timeline_totals"
    description = "Long-format journal-wide aggregate views timeline"

    date = Column(pl.String, "Period label kept as string (day or month).", "date")
    interval = Column(
        pl.String,
        "Timeline granularity the point was fetched at: 'day' or 'month'.",
        "interval",
    )
    views = Column(
        pl.Int64, "Journal-wide aggregate view count for that date/kind.", "value"
    )
    kind = Column(pl.String, "View kind: 'abstract' or 'galley'.", "kind")


API_TABLES: tuple[type[Table], ...] = (
    Submissions,
    Publications,
    Authors,
    ReviewAssignments,
    Users,
    UserGroups,
    SubmissionFiles,
    PublicationStats,
    ViewsTimeline,
    ViewsTimelineTotals,
)
