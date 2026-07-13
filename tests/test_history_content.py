"""D34 persisted-value codec and per-run content-store regressions."""

import base64
import copy
import hashlib
import json
import os
import stat
import sys

import pytest

from napflow.core.history_content import (
    DEFAULT_INLINE_THRESHOLD_BYTES,
    ContentCorruptError,
    ContentMissingError,
    ContentOmittedError,
    ContentStoreError,
    RunContentStore,
)


def _descriptor(value):
    assert isinstance(value, dict)
    assert set(value) == {"$napflow"}
    descriptor = value["$napflow"]
    assert isinstance(descriptor, dict)
    return descriptor


def _blob_files(store):
    if not store.blob_dir.exists():
        return []
    return sorted(
        path
        for path in store.blob_dir.rglob("*")
        if path.is_file() and not path.is_symlink()
    )


@pytest.mark.parametrize(
    ("value", "raw", "media_type", "expected_media_type", "codec"),
    [
        (
            "café",
            "café".encode(),
            "text/x-test; charset=utf-8",
            "text/x-test; charset=utf-8",
            "utf-8",
        ),
        (
            {"z": "é", "a": [1, True, None]},
            json.dumps(
                {"z": "é", "a": [1, True, None]},
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode(),
            "application/vnd.napflow-test+json",
            "application/vnd.napflow-test+json",
            "json",
        ),
        (
            {
                "__binary__": True,
                "content_type": "application/octet-stream",
                "base64": base64.b64encode(b"\x00\x01\xfe\xff").decode(),
            },
            b"\x00\x01\xfe\xff",
            None,
            "application/octet-stream",
            "binary",
        ),
    ],
    ids=("utf-8", "json", "binary"),
)
def test_blob_codecs_store_exact_bytes_and_round_trip(
    tmp_path, value, raw, media_type, expected_media_type, codec
):
    store = RunContentStore(tmp_path / "run.jsonl", inline_threshold_bytes=1)

    persisted = store.persist(value, media_type=media_type)

    assert _descriptor(persisted) == {
        "kind": "blob",
        "hash": f"sha256:{hashlib.sha256(raw).hexdigest()}",
        "bytes": len(raw),
        "media_type": expected_media_type,
        "codec": codec,
    }
    blobs = _blob_files(store)
    assert len(blobs) == 1
    assert blobs[0].read_bytes() == raw
    reader = RunContentStore(tmp_path / "run.jsonl", inline_threshold_bytes=1)
    assert reader.resolve(persisted) == value


def test_binary_default_preserves_exact_content_type(tmp_path):
    value = {
        "__binary__": True,
        "content_type": " text/plain ",
        "base64": base64.b64encode(b"abc").decode(),
    }
    store = RunContentStore(tmp_path / "run.jsonl", inline_threshold_bytes=1)

    persisted = store.persist(value)

    assert _descriptor(persisted)["media_type"] == " text/plain "
    assert store.resolve(persisted) == value


def test_inline_threshold_is_inclusive_and_measured_in_bytes(tmp_path):
    assert DEFAULT_INLINE_THRESHOLD_BYTES == 64 * 1024
    store = RunContentStore(tmp_path / "run.jsonl", inline_threshold_bytes=4)

    inline = "éé"  # two code points, exactly four UTF-8 bytes
    large = "ééa"  # three code points, five UTF-8 bytes

    assert store.persist(inline) == inline
    assert store.resolve(inline) == inline
    assert not store.blob_dir.exists()
    assert _descriptor(store.persist(large))["bytes"] == 5


def test_repeated_content_is_deduplicated(tmp_path):
    store = RunContentStore(tmp_path / "run.jsonl", inline_threshold_bytes=1)
    value = "repeat-me" * 20

    first = store.persist(value, media_type="text/plain")
    second = store.persist(value, media_type="text/plain")

    assert first == second
    assert len(_blob_files(store)) == 1
    assert store.resolve(first) == value


def test_identical_bytes_deduplicate_across_codecs_without_changing_value(tmp_path):
    store = RunContentStore(tmp_path / "run.jsonl", inline_threshold_bytes=1)

    text = store.persist("null")
    structured = store.persist(None)

    assert _descriptor(text)["hash"] == _descriptor(structured)["hash"]
    assert _descriptor(text)["codec"] == "utf-8"
    assert _descriptor(structured)["codec"] == "json"
    assert len(_blob_files(store)) == 1
    assert store.resolve(text) == "null"
    assert store.resolve(structured) is None


@pytest.mark.parametrize(
    "value",
    [
        {"$napflow": "user data", "other": 1},
        {
            "$napflow": {
                "kind": "blob",
                "hash": f"sha256:{'0' * 64}",
                "bytes": 0,
                "media_type": "text/plain",
                "codec": "utf-8",
            }
        },
    ],
    ids=("ordinary-reserved-key", "exact-blob-imitation"),
)
def test_reserved_marker_shaped_user_values_round_trip_as_literals(tmp_path, value):
    store = RunContentStore(tmp_path / "run.jsonl", inline_threshold_bytes=10_000)
    original = copy.deepcopy(value)

    persisted = store.persist(value)

    assert persisted == {"$napflow": {"kind": "literal", "value": original}}
    assert store.resolve(persisted) == original
    assert value == original
    assert _blob_files(store) == []


_VALID_BLOB = {
    "kind": "blob",
    "hash": f"sha256:{'0' * 64}",
    "bytes": 0,
    "media_type": "text/plain",
    "codec": "utf-8",
}


def test_resolve_does_not_guess_protocol_when_reserved_key_has_siblings(tmp_path):
    store = RunContentStore(tmp_path / "run.jsonl")
    value = {"$napflow": _VALID_BLOB, "extra": True}

    assert store.resolve(value) == value


@pytest.mark.parametrize(
    "value",
    [
        {"$napflow": _VALID_BLOB | {"extra": True}},
        {"$napflow": {key: item for key, item in _VALID_BLOB.items() if key != "hash"}},
        {"$napflow": _VALID_BLOB | {"kind": "future"}},
        {"$napflow": _VALID_BLOB | {"hash": f"sha256:{'A' * 64}"}},
        {"$napflow": _VALID_BLOB | {"bytes": -1}},
        {"$napflow": _VALID_BLOB | {"bytes": True}},
        {"$napflow": _VALID_BLOB | {"media_type": ""}},
        {"$napflow": _VALID_BLOB | {"codec": "gzip"}},
        {"$napflow": {"kind": "literal", "value": 1, "extra": True}},
        {"$napflow": _VALID_BLOB | {"kind": "omitted"}},
        {
            "$napflow": _VALID_BLOB
            | {"kind": "omitted", "reason": "hard_limit", "extra": True},
        },
    ],
    ids=(
        "descriptor-extra",
        "missing-key",
        "unknown-kind",
        "uppercase-hash",
        "negative-bytes",
        "boolean-bytes",
        "empty-media-type",
        "unknown-codec",
        "literal-extra",
        "omitted-missing-reason",
        "omitted-extra",
    ),
)
def test_malformed_descriptors_are_rejected_strictly(tmp_path, value):
    store = RunContentStore(tmp_path / "run.jsonl")

    with pytest.raises(ContentStoreError) as excinfo:
        store.resolve(value)

    assert not isinstance(
        excinfo.value,
        (ContentMissingError, ContentCorruptError, ContentOmittedError),
    )


def test_omitted_content_has_exact_metadata_and_typed_resolution_error(tmp_path):
    store = RunContentStore(tmp_path / "run.jsonl", inline_threshold_bytes=1)
    value = "not-written"
    raw = value.encode()

    omitted = store.omit(value, "hard_limit", media_type="text/plain")

    assert _descriptor(omitted) == {
        "kind": "omitted",
        "hash": f"sha256:{hashlib.sha256(raw).hexdigest()}",
        "bytes": len(raw),
        "media_type": "text/plain",
        "codec": "utf-8",
        "reason": "hard_limit",
    }
    assert _blob_files(store) == []
    with pytest.raises(ContentOmittedError) as excinfo:
        store.resolve(omitted)
    assert isinstance(excinfo.value, ContentStoreError)


@pytest.mark.parametrize(
    ("failure", "error_type"),
    [("missing", ContentMissingError), ("corrupt", ContentCorruptError)],
)
def test_resolution_surfaces_missing_and_corrupt_blobs(tmp_path, failure, error_type):
    store = RunContentStore(tmp_path / "run.jsonl", inline_threshold_bytes=1)
    persisted = store.persist("original-content", media_type="text/plain")
    [blob] = _blob_files(store)
    if failure == "missing":
        blob.unlink()
    else:
        blob.write_bytes(b"x" * len(b"original-content"))

    reader = RunContentStore(tmp_path / "run.jsonl", inline_threshold_bytes=1)
    with pytest.raises(error_type) as excinfo:
        reader.resolve(persisted)
    assert isinstance(excinfo.value, ContentStoreError)


def test_blob_directory_symlink_is_never_followed(tmp_path):
    log_path = tmp_path / "run.jsonl"
    probe = RunContentStore(log_path, inline_threshold_bytes=1)
    blob_dir = probe.blob_dir
    if blob_dir.exists():
        blob_dir.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        blob_dir.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"symlinks unavailable: {error}")

    with pytest.raises(ContentStoreError):
        RunContentStore(log_path, inline_threshold_bytes=1).persist("large value")
    assert list(outside.iterdir()) == []


def test_existing_blob_symlink_is_never_followed(tmp_path):
    log_path = tmp_path / "run.jsonl"
    store = RunContentStore(log_path, inline_threshold_bytes=1)
    value = "large value"
    store.persist(value)
    [blob] = _blob_files(store)
    blob.unlink()
    outside = tmp_path / "outside-blob"
    outside.write_bytes(b"safe")
    try:
        blob.symlink_to(outside)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"symlinks unavailable: {error}")

    with pytest.raises(ContentStoreError):
        RunContentStore(log_path, inline_threshold_bytes=1).persist(value)
    assert outside.read_bytes() == b"safe"


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits are not portable")
def test_blob_directory_and_files_are_private(tmp_path):
    store = RunContentStore(tmp_path / "run.jsonl", inline_threshold_bytes=1)
    store.persist("private content")
    [blob] = _blob_files(store)

    assert stat.S_IMODE(store.blob_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(blob.stat().st_mode) == 0o600


def test_persist_does_not_mutate_runtime_input(tmp_path):
    store = RunContentStore(tmp_path / "run.jsonl", inline_threshold_bytes=1)
    value = {
        "items": [{"name": "café", "payload": "x" * 20}],
        "enabled": True,
    }
    original = copy.deepcopy(value)

    persisted = store.persist(value, media_type="application/json")

    assert value == original
    assert store.resolve(persisted) == original


@pytest.mark.parametrize("inline_threshold_bytes", [0, 10_000])
@pytest.mark.parametrize(
    "value",
    [
        (1, 2),
        {1: "numeric"},
        {1: "numeric", "1": "string"},
    ],
    ids=("tuple", "numeric-key", "colliding-normalized-keys"),
)
def test_python_values_json_would_normalize_are_rejected(
    tmp_path, inline_threshold_bytes, value
):
    store = RunContentStore(
        tmp_path / "run.jsonl",
        inline_threshold_bytes=inline_threshold_bytes,
    )
    original = copy.deepcopy(value)

    with pytest.raises(ContentStoreError):
        store.persist(value)

    assert value == original
    assert _blob_files(store) == []


def test_deep_json_failure_stays_in_typed_error_family(tmp_path):
    value = None
    for _ in range(sys.getrecursionlimit() + 10):
        value = [value]

    with pytest.raises(ContentStoreError) as excinfo:
        RunContentStore(tmp_path / "run.jsonl").persist(value)

    assert not isinstance(excinfo.value, RecursionError)


def test_blob_size_is_rejected_before_content_is_read(tmp_path, monkeypatch):
    store = RunContentStore(tmp_path / "run.jsonl", inline_threshold_bytes=1)
    persisted = store.persist("content")
    [blob] = _blob_files(store)
    blob.write_bytes(b"x" * 1_000_000)

    def fail_fdopen(*args, **kwargs):
        raise AssertionError("size mismatch must fail before reading")

    monkeypatch.setattr(os, "fdopen", fail_fdopen)
    with pytest.raises(ContentCorruptError, match="size mismatch"):
        store.resolve(persisted)


def test_blob_write_io_error_stays_typed_and_removes_partial(tmp_path, monkeypatch):
    store = RunContentStore(tmp_path / "run.jsonl", inline_threshold_bytes=1)
    real_fsync = os.fsync

    def fail_fsync(fd):
        if stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("injected fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_fsync)
    with pytest.raises(ContentStoreError, match="cannot write"):
        store.persist("content")
    assert _blob_files(store) == []


def test_blob_read_io_error_stays_typed(tmp_path, monkeypatch):
    store = RunContentStore(tmp_path / "run.jsonl", inline_threshold_bytes=1)
    persisted = store.persist("content")
    real_fstat = os.fstat

    def fail_regular_fstat(fd):
        opened = real_fstat(fd)
        if stat.S_ISREG(opened.st_mode):
            raise OSError("injected fstat failure")
        return opened

    monkeypatch.setattr(os, "fstat", fail_regular_fstat)
    with pytest.raises(ContentCorruptError, match="cannot inspect open"):
        store.resolve(persisted)


@pytest.mark.skipif(
    not all(
        function in os.supports_dir_fd
        for function in (os.open, os.stat, os.unlink)
    ),
    reason="verified directory handles are unavailable",
)
def test_blob_directory_swap_cannot_redirect_write(tmp_path, monkeypatch):
    store = RunContentStore(tmp_path / "run.jsonl", inline_threshold_bytes=1)
    value = "content"
    digest = hashlib.sha256(value.encode()).hexdigest()
    detached = tmp_path / "detached-blobs"
    outside = tmp_path / "outside"
    outside.mkdir()
    real_open = os.open
    swapped = False

    def racing_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if path == digest and dir_fd is not None and not swapped:
            swapped = True
            store.blob_dir.rename(detached)
            try:
                store.blob_dir.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError) as error:
                pytest.skip(f"symlinks unavailable: {error}")
        if dir_fd is None:
            return real_open(path, flags, mode)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", racing_open)
    with pytest.raises(ContentStoreError):
        store.persist(value)

    assert swapped
    assert list(outside.iterdir()) == []
    assert list(detached.iterdir()) == []
