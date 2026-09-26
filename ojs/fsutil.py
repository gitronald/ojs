"""Filesystem helpers shared by the pipelines."""

import os
import tempfile
from pathlib import Path

__all__ = ["write_bytes_atomic"]


def write_bytes_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically (temp file + fsync + ``os.replace``).

    The temp file lives in the target directory so the rename is atomic. A crash
    or full disk mid-write leaves the previous file (or no file) rather than a
    truncated one, so a later run can never mistake a partial write for a
    complete artifact. The file gets the umask-derived mode rather than mkstemp's
    owner-only 0600 default.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        # Read-and-restore the process umask to compute the mode write_text
        # would have produced (~0644).
        umask = os.umask(0o022)
        os.umask(umask)
        os.fchmod(fd, 0o666 & ~umask)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
