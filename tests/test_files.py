import errno
import stat

import pytest

import napflow.core.files as files
from napflow.core.files import atomic_create_text, atomic_write_text


def _temporary_files(path):
    return list(path.parent.glob(f".{path.name}.*.tmp"))


def test_atomic_write_is_utf8_lf_same_directory_and_preserves_mode(
    tmp_path, monkeypatch
):
    path = tmp_path / "nodes.py"
    path.write_text("old\n", encoding="utf-8")
    path.chmod(0o640)
    original_mode = stat.S_IMODE(path.stat().st_mode)
    real_replace = files._replace
    replaced = []

    def observe_replace(source, destination):
        replaced.append((source, destination))
        real_replace(source, destination)

    monkeypatch.setattr(files, "_replace", observe_replace)
    atomic_write_text(path, "héllo\r\nworld\r")

    assert path.read_bytes() == "héllo\nworld\n".encode()
    assert len(replaced) == 1
    assert replaced[0][1] == path
    assert replaced[0][0].parent == tmp_path
    assert stat.S_IMODE(path.stat().st_mode) == original_mode
    assert _temporary_files(path) == []


@pytest.mark.parametrize("failure", ["fsync", "replace"])
def test_atomic_write_failure_preserves_original_and_cleans_temp(
    tmp_path, monkeypatch, failure
):
    path = tmp_path / "flow.yaml"
    original = b"complete old source\n"
    path.write_bytes(original)

    def fail(*_args):
        raise OSError(errno.ENOSPC, "simulated disk full")

    if failure == "fsync":
        monkeypatch.setattr(files.os, "fsync", fail)
    else:
        monkeypatch.setattr(files, "_replace", fail)
    with pytest.raises(OSError, match="simulated disk full"):
        atomic_write_text(path, "replacement\n")

    assert path.read_bytes() == original
    assert _temporary_files(path) == []


def test_atomic_create_is_lf_and_never_replaces_an_existing_path(tmp_path):
    path = tmp_path / ".gitignore"

    assert atomic_create_text(path, "first\r\n")
    assert path.read_bytes() == b"first\n"
    assert not atomic_create_text(path, "replacement\n")
    assert path.read_bytes() == b"first\n"
    assert _temporary_files(path) == []


def test_atomic_create_falls_back_when_hard_links_are_unavailable(
    tmp_path, monkeypatch
):
    path = tmp_path / ".gitattributes"

    def unsupported(*_args):
        raise OSError(errno.ENOTSUP, "hard links unavailable")

    monkeypatch.setattr(files, "_link", unsupported)

    assert atomic_create_text(path, "*.yaml text eol=lf\r\n")
    assert path.read_bytes() == b"*.yaml text eol=lf\n"
    assert _temporary_files(path) == []


def test_atomic_create_fallback_never_replaces_an_existing_path(tmp_path, monkeypatch):
    path = tmp_path / ".gitattributes"
    original = b"# owner bytes\r\n"
    path.write_bytes(original)

    def unsupported(*_args):
        raise OSError(errno.ENOTSUP, "hard links unavailable")

    monkeypatch.setattr(files, "_link", unsupported)

    assert not atomic_create_text(path, "replacement\n")
    assert path.read_bytes() == original
    assert _temporary_files(path) == []


def test_atomic_create_fallback_failure_cleans_destination_and_temp(
    tmp_path, monkeypatch
):
    path = tmp_path / ".gitattributes"
    real_fsync = files.os.fsync
    fsync_calls = 0

    def unsupported(*_args):
        raise OSError(errno.ENOTSUP, "hard links unavailable")

    def fail_fallback_fsync(fd):
        nonlocal fsync_calls
        fsync_calls += 1
        if fsync_calls == 2:
            raise OSError(errno.ENOSPC, "simulated create failure")
        return real_fsync(fd)

    monkeypatch.setattr(files, "_link", unsupported)
    monkeypatch.setattr(files.os, "fsync", fail_fallback_fsync)

    with pytest.raises(OSError, match="simulated create failure"):
        atomic_create_text(path, "*.yaml text eol=lf\n")

    assert fsync_calls == 2
    assert not path.exists()
    assert _temporary_files(path) == []
