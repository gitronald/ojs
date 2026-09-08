"""Download submission file artifacts from the OJS API.

``client.py`` fetches *metadata about* files (JSON); this module fetches the
file *bytes*, lays them out on disk by submission, and keeps a manifest so
repeat runs skip artifacts already downloaded.

OJS distinguishes two axes that both matter when downloading:

* **fileStage** -- where in the workflow a file lives (submission, review,
  copyedit, production/galley, ...). ``FILE_STAGE_LABELS`` names the ones the
  API validates, and ``STAGE_GROUPS`` bundles them into the ``--type`` choices.
* **revisions** -- each ``SubmissionFile`` may carry prior uploads of the same
  logical file in ``revisions[]``. Each revision is a distinct *physical* file
  (its own ``fileId``), so we download and track them individually.
"""

import logging
import re
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

import httpx

from ojs.api.client import SKIP_STATUSES, _http_client, _request_with_retry
from ojs.utils import localized

logger = logging.getLogger(__name__)

# OJS / PKP `SubmissionFile` stage constants (the ids the API validates on
# `fileStage`). 7 (fair copy) and 8 (editor) are deprecated upstream but still
# accepted, so they are mapped for completeness.
FILE_STAGE_LABELS: dict[int, str] = {
    2: "submission",
    3: "note",
    4: "review_file",
    5: "review_attachment",
    6: "final",
    7: "fair_copy",
    8: "editor",
    9: "copyedit",
    10: "proof",
    11: "production_ready",
    13: "attachment",
    15: "review_revision",
    17: "dependent",
    18: "query",
}

# `assocType` constant that ties a review file/revision to a particular review
# round; when set, `assocId` is the reviewRoundId.
ASSOC_TYPE_REVIEW_ROUND = 521

# Named bundles for the CLI `--type` option. `all` means "no stage filter".
STAGE_GROUPS: dict[str, list[int] | None] = {
    "all": None,
    # Production-ready files are the artifacts published as galleys.
    "galleys": [11],
    # Review files, author revisions, and reviewer attachments across rounds.
    "review": [4, 15, 5],
}

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def stage_label(file_stage: Any) -> str:
    """Human-readable directory name for a `fileStage` id (falls back gracefully)."""
    try:
        return FILE_STAGE_LABELS.get(int(file_stage), f"stage_{int(file_stage)}")
    except (TypeError, ValueError):
        return "stage_unknown"


def review_round_id(file: dict[str, Any]) -> int | None:
    """The reviewRoundId a file belongs to, or None if it is not a review-round file."""
    if file.get("assocType") == ASSOC_TYPE_REVIEW_ROUND:
        return file.get("assocId")
    return None


def _safe_name(name: str | None) -> str:
    """Collapse anything filesystem-unfriendly in a filename to underscores."""
    cleaned = _UNSAFE.sub("_", name or "").strip("_")
    return cleaned or "file"


def download_targets(
    file: dict[str, Any], *, include_revisions: bool = True
) -> Iterator[dict[str, Any]]:
    """Yield one downloadable artifact per physical `fileId` for a SubmissionFile.

    Emits the current file plus, when ``include_revisions`` is set, every prior
    revision. Each yielded dict carries the resolved download ``url``, the
    physical ``file_id``, and the metadata needed to place and record it.
    Targets without a ``url`` or ``file_id`` are skipped (nothing to fetch), and
    duplicate ``file_id`` values (a revision repeating the current file) are
    de-duplicated.
    """
    submission_id = file.get("_submission_id")
    submission_file_id = file.get("id")
    stage = stage_label(file.get("fileStage"))
    base_name = _safe_name(localized(file.get("name"), fallback_any=True))

    seen: set[Any] = set()

    def _record(file_id: Any, url: Any) -> dict[str, Any] | None:
        if not file_id or not url or file_id in seen:
            return None
        seen.add(file_id)
        return {
            "submission_id": submission_id,
            "submission_file_id": submission_file_id,
            "file_id": file_id,
            "file_stage": file.get("fileStage"),
            "stage": stage,
            "review_round_id": review_round_id(file),
            "url": url,
            "filename": f"{file_id}_{base_name}",
        }

    # The current file, then (optionally) each prior revision. _record dedupes
    # by fileId and drops anything without a url/fileId.
    candidates = [(file.get("fileId"), file.get("url"))]
    if include_revisions:
        revisions = file.get("revisions") or []
        candidates += [(r.get("fileId"), r.get("url")) for r in revisions]
    for file_id, url in candidates:
        if record := _record(file_id, url):
            yield record


def manifest_key(record: dict[str, Any]) -> Any:
    """Manifest identity for a downloaded artifact: its immutable physical fileId."""
    return record.get("file_id")


def _is_within(base: Path, candidate: Path) -> bool:
    """True if ``candidate`` resolves inside ``base`` -- blocks ``..``/absolute escapes.

    Path components for downloads (``submission_id``/stage) and the orphan-cleanup
    unlink (the manifest ``path``) can come from on-disk state that could be
    corrupt or hand-edited, so every filesystem effect is gated on containment.
    """
    try:
        return candidate.resolve().is_relative_to(base.resolve())
    except OSError:
        return False


def download_files(
    files: Iterable[dict[str, Any]],
    *,
    api_key: str,
    dest_dir: Path,
    downloaded: dict[Any, dict[str, Any]] | None = None,
    include_revisions: bool = True,
    on_record: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Download artifacts for ``files`` into ``dest_dir``.

    Returns ``(new_records, failed_records)``. ``new_records`` is one manifest
    record per artifact written this run (the caller persists it). ``downloaded``
    maps already-fetched ``file_id`` -> manifest record; targets present there
    with the file still on disk are skipped, making repeat runs incremental.
    Layout is ``dest_dir/<submission_id>/<stage>/<fileId>_<name>``. A renamed file
    (same ``file_id``, new name) is re-fetched to its new path and its now-orphaned
    prior artifact is deleted, so reruns do not silently accumulate stale files.

    ``on_record`` is invoked with each manifest record immediately after its bytes
    are written, before the next file is fetched. Callers use it to persist the
    manifest incrementally, so a mid-batch error that propagates out cannot lose
    the record of files already written to disk.

    ``failed_records`` is one record per artifact the API would not serve
    (``403``/``404`` -- a stage the key cannot view, or a removed file), carrying
    the HTTP ``status``/``reason`` so the caller can persist a skip log. Other
    statuses are unexpected and propagate.
    """
    downloaded = downloaded or {}
    new_records: list[dict[str, Any]] = []
    failed_records: list[dict[str, Any]] = []
    skipped = 0

    with _http_client() as client:
        for file in files:
            for target in download_targets(file, include_revisions=include_revisions):
                file_id = target["file_id"]
                rel = Path(str(target["submission_id"])) / target["stage"]
                dest = dest_dir / rel / target["filename"]
                # Defense-in-depth: keep every artifact inside the download tree.
                # `submission_id`/stage come from on-disk state that could be
                # corrupt; a `..` in either must not let a write (or the skip
                # probe / mkdir below) escape dest_dir.
                if not _is_within(dest_dir, dest):
                    raise ValueError(f"refusing path outside {dest_dir}: {dest}")

                prior = downloaded.get(file_id)
                # Skip only when this file_id is already recorded AND the file
                # resolved for THIS run is on disk. Testing the freshly resolved
                # `dest` (not the old recorded path) means a renamed file -- whose
                # new `dest` does not yet exist -- correctly falls through and is
                # re-fetched, replacing the stale manifest entry by file_id.
                if prior is not None and dest.exists():
                    skipped += 1
                    continue

                try:
                    response = _request_with_retry(
                        client, target["url"], {"apiToken": api_key}
                    )
                except httpx.HTTPStatusError as e:
                    # OJS returns 403/404 per file (a stage the key cannot view,
                    # or a file that has since been removed). Record it and keep
                    # going rather than aborting the whole run; other statuses
                    # are unexpected and propagate.
                    if e.response.status_code in SKIP_STATUSES:
                        failed_records.append(
                            {
                                "file_id": file_id,
                                "submission_id": target["submission_id"],
                                "submission_file_id": target["submission_file_id"],
                                "file_stage": target["file_stage"],
                                "stage": target["stage"],
                                "review_round_id": target["review_round_id"],
                                "url": target["url"],
                                "status": e.response.status_code,
                                "reason": e.response.reason_phrase,
                            }
                        )
                        logger.info(
                            f"  Skipping file {file_id}: {e.response.status_code} "
                            f"{e.response.reason_phrase}"
                        )
                        continue
                    raise
                content = response.content
                # `bytes` below is this in-memory response length; write_bytes is a
                # single call, so a partial file without a raised error is not
                # expected (an interrupted call raises and skips the manifest flush).
                size = len(content)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(content)

                record = {
                    "file_id": file_id,
                    "submission_id": target["submission_id"],
                    "submission_file_id": target["submission_file_id"],
                    "file_stage": target["file_stage"],
                    "review_round_id": target["review_round_id"],
                    "path": str(dest.relative_to(dest_dir)),
                    "bytes": size,
                }
                new_records.append(record)
                # Persist before moving on, so a later file's failure cannot
                # strand this already-written file out of the manifest.
                if on_record is not None:
                    on_record(record)
                # A renamed file (same file_id) was just written under a new name;
                # delete the now-orphaned prior artifact so reruns do not silently
                # accumulate stale files. Done AFTER the manifest flush above so a
                # crash here leaves only a harmless orphan, never a manifest entry
                # pointing at a path that no longer exists.
                if prior is not None:
                    prior_path = prior.get("path")
                    if prior_path and prior_path != record["path"]:
                        orphan = dest_dir / prior_path
                        # Only delete inside the download tree: a tampered/corrupt
                        # manifest `path` (e.g. "../x") must not turn cleanup into
                        # arbitrary file deletion outside dest_dir.
                        if _is_within(dest_dir, orphan):
                            orphan.unlink(missing_ok=True)
                logger.info(f"  Downloaded {dest.relative_to(dest_dir)} ({size} bytes)")

    if skipped:
        logger.info(f"  Skipped {skipped} already-downloaded files")
    if failed_records:
        logger.info(f"  Skipped {len(failed_records)} inaccessible files (403/404)")
    return new_records, failed_records
