"""Incremental-sync plumbing: high-water-mark state and raw-JSON merge.

The incremental fetch path (plan 001) layers deltas onto the *complete* JSON
dumps here, so ``api norm`` stays a stateless re-derivation from the merged JSON
and never has to know incremental mode exists (Option A). Keeping this logic out
of ``client.py`` (HTTP) and ``normalize.py`` (JSON -> tables) leaves both of
those modules focused on their single concern.
"""

import json
import os
import tempfile
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Sync-state file name, resolved relative to the API dir by the CLI.
SYNC_STATE_FILENAME = "sync_state.json"

# How far back to re-pull on each incremental run. The window deliberately
# overlaps the previous sync so boundary edits (clock skew, same-second ties,
# late-arriving view counts) are not missed; upsert makes the overlap idempotent.
SYNC_OVERLAP = timedelta(days=1)

# OJS serializes submission timestamps as `Y-m-d H:i:s` (see swagger validation
# on dateLastActivity / lastModified). Same-format strings compare lexically.
_TS_FORMAT = "%Y-%m-%d %H:%M:%S"

KeyFn = Callable[[dict[str, Any]], object]


def write_json(path: Path, data: Any) -> None:
    """Write `data` to `path` as indented JSON, atomically.

    Serializes once, writes to a temp file in the same directory, fsyncs it, then
    atomically renames it over `path` (``os.replace``). A crash or full disk
    mid-write leaves the previous complete file intact rather than a truncated
    dump, so every caller (sync state, raw dumps, manifest, skip log) is durable.
    """
    text = json.dumps(data, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        # mkstemp creates the file 0600; restore the umask-derived mode so the
        # atomic write does not silently make dumps owner-only (write_text gave
        # ~0644). Read-and-restore the process umask to compute the mode.
        umask = os.umask(0o022)
        os.umask(umask)
        os.fchmod(fd, 0o666 & ~umask)
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _empty_state() -> dict[str, Any]:
    """Default state for a journal that has never been synced incrementally."""
    return {"last_sync": None, "stats_last_sync": None, "submission_modified": {}}


def load_sync_state(path: Path) -> dict[str, Any]:
    """Load the incremental sync state, or an empty default if absent/unreadable.

    The state holds ``last_sync`` (ISO wall-clock of the last successful fetch),
    ``stats_last_sync`` (wall-clock of the last *successful stats* fetch, the
    anchor for the rolling view-stats window), and ``submission_modified``
    (submission_id -> ``dateLastActivity`` high-water mark, used to early-stop and
    skip-unchanged). A missing or corrupt file yields an empty state so a first
    run falls back to a full pull rather than crashing.
    """
    if not path.exists():
        return _empty_state()
    try:
        state = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return _empty_state()
    state.setdefault("last_sync", None)
    state.setdefault("stats_last_sync", None)
    state.setdefault("submission_modified", {})
    return state


def save_sync_state(path: Path, state: dict[str, Any]) -> None:
    """Write the sync state. Call only after a fetch fully succeeds.

    Persisting only on success means a failed run never advances the watermark,
    so the next run re-pulls the same window.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, state)


def build_submission_modified(
    submissions: list[dict[str, Any]],
) -> dict[str, str | None]:
    """Map submission_id -> ``dateLastActivity`` for the sync-state watermark.

    Keys are stringified because JSON object keys are always strings; reading
    them back as ``str`` keeps round-trips stable.
    """
    return {str(s["id"]): s.get("dateLastActivity") for s in submissions}


def submission_watermark(
    state: dict[str, Any], *, buffer: timedelta = SYNC_OVERLAP
) -> str | None:
    """High-water mark for submission/publication early-stop.

    Returns the most recent ``dateLastActivity`` seen on the previous sync, minus
    an overlap buffer, as a ``Y-m-d H:i:s`` string (the same format the API emits,
    so callers can compare lexically). Returns ``None`` when no prior timestamps
    are known, which forces a full pull.
    """
    seen = [v for v in state.get("submission_modified", {}).values() if v]
    if not seen:
        return None
    high = datetime.strptime(max(seen), _TS_FORMAT) - buffer
    return high.strftime(_TS_FORMAT)


def stats_window_start(
    state: dict[str, Any], *, buffer: timedelta = SYNC_OVERLAP
) -> str | None:
    """Rolling-window ``dateStart`` (``YYYY-MM-DD``) for re-pulling view stats.

    Derived from ``stats_last_sync`` -- the last time stats were *successfully*
    fetched -- not the editorial watermark and not the general ``last_sync``:
    view counts accrue by calendar time, so the window must cover everything since
    stats last succeeded. Anchoring on a stats-specific mark means a run that
    skipped or failed stats does not advance the window past the days it missed.
    Falls back to ``last_sync`` for state files written before this field existed.
    Returns ``None`` when there is no prior mark, forcing a full timeline pull.
    """
    last = state.get("stats_last_sync") or state.get("last_sync")
    if not last:
        return None
    return (datetime.fromisoformat(last) - buffer).date().isoformat()


def upsert_json_list(
    existing: list[dict[str, Any]], new: list[dict[str, Any]], key: str | KeyFn
) -> list[dict[str, Any]]:
    """Merge ``new`` records into ``existing``, replacing matches by ``key``.

    ``key`` is either a field name or a callable mapping a record to its key
    (use a callable for nested or composite keys, e.g. ``(submission, date,
    kind)``). Existing records keep their position, updated ones are replaced in
    place, and brand-new records are appended in input order. Idempotent:
    re-upserting the same records only rewrites equal values, so an overlapping
    re-pull is safe.
    """
    keyfn: KeyFn = key if callable(key) else (lambda r: r.get(key))
    index = {keyfn(r): i for i, r in enumerate(existing)}
    merged = list(existing)
    for rec in new:
        k = keyfn(rec)
        if k in index:
            merged[index[k]] = rec
        else:
            index[k] = len(merged)
            merged.append(rec)
    return merged


def merge_write_json(
    path: Path, new_items: list[dict[str, Any]], key: str | KeyFn
) -> list[dict[str, Any]]:
    """Upsert `new_items` into the existing JSON list at `path` (Option A merge).

    Loads the complete list already on disk (empty if absent), upserts the delta
    by `key`, writes the merged list back, and returns it. Keeping each JSON dump
    complete lets `api norm` stay a stateless re-derivation that never needs to
    know incremental mode exists.
    """
    existing = json.loads(path.read_text()) if path.exists() else []
    merged = upsert_json_list(existing, new_items, key)
    write_json(path, merged)
    return merged
