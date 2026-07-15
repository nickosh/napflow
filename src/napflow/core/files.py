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


def _link(source: Path, destination: Path) -> None:
    os.link(source, destination)


def _exclusive_create(path: Path, data: bytes) -> bool:
    """Portable no-replace fallback for filesystems without hard links."""

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        return False
    fd_open = True
    try:
        with os.fdopen(fd, "wb") as file:
            fd_open = False
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    finally:
        if fd_open:
            os.close(fd)
    return True


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


def atomic_create_text(path: Path, text: str) -> bool:
    """Create a canonical UTF-8/LF file without replacing any existing path.

    A fully flushed temporary file is hard-linked into place when supported.
    Filesystems without hard links use an exclusive-create fallback. ``False``
    means another path already owns the destination; that path is never
    followed or changed.
    """

    path = Path(path)
    data = _lf_text(text).encode("utf-8")
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
            os.fsync(file.fileno())
        try:
            _link(temporary, path)
        except FileExistsError:
            return False
        except OSError:
            return _exclusive_create(path, data)
        return True
    finally:
        if fd_open:
            os.close(fd)
        temporary.unlink(missing_ok=True)
