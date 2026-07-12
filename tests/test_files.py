import errno
import stat

import pytest

import napflow.core.files as files
from napflow.core.files import atomic_write_text


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
