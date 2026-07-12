"""Crash-safe filesystem writes shared by source-file persistence.

Source files are never truncated in place.  A complete UTF-8/LF temporary
file is flushed in the target directory, then atomically replaces the live
path.  Streaming run histories deliberately do not use this primitive.
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path


def _replace(source: Path, destination: Path) -> None:
    source.replace(destination)


def _lf_text(text: str) -> str:
    """Normalize every conventional text newline to the canonical LF form."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically replace *path* with canonical UTF-8/LF *text*.

    The temporary file lives beside the target so ``os.replace`` cannot
    cross a filesystem boundary. Existing permission bits are copied to the
    replacement. Any failure before replacement leaves the original bytes
    untouched and removes the temporary file.
    """

    path = Path(path)
    data = _lf_text(text).encode("utf-8")
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        mode = None

    fd, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    fd_open = True
    try:
        with os.fdopen(fd, "wb") as file:
            fd_open = False
            file.write(data)
            file.flush()
            if mode is not None:
                temporary.chmod(mode)
            os.fsync(file.fileno())
        # Windows cannot replace a file while the temporary handle is open.
        _replace(temporary, path)
    finally:
        if fd_open:
            os.close(fd)
        temporary.unlink(missing_ok=True)
