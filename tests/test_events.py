"""Events + JSONL + masking (S2/M2): vocabulary per EN §7 (FR-702),
JSONL sink + retention (FR-701), secrets masked at emission (FR-106,
D22 — events are born masked)."""

import json
import os
import re
from datetime import UTC, datetime

import pytest

import napflow.core.events as events_module
from napflow.core.events import (
    EVENT_TYPES,
    HISTORY_FEATURE_CONTENT_BLOBS,
    HISTORY_FORMAT,
    HISTORY_FORMAT_MAJOR,
    HISTORY_SUPPORTED_FEATURES,
    HISTORY_WRITE_FEATURES,
    MASK,
    AssertResult,
    EventStream,
    FrameFinished,
    HistoryFormatError,
    JsonlSink,
    LogEvent,
    NodeFired,
    RunFinished,
    RunStarted,
    SecretMasker,
    abandon_run_history,
    apply_retention,
    begin_run_history,
    complete_run_history,
    history_reader,
    is_supported,
    last_jsonl_record,
    new_run_id,
    parse_history_features,
    parse_history_format,
    run_history_sort_key,
    run_log_path,
)
from napflow.core.workspace import WorkspaceBoundaryError

NO_SECRETS = SecretMasker([], {})
FIXED_CLOCK = lambda: datetime(2026, 7, 5, 10, 0, 0, 123456, tzinfo=UTC)  # noqa: E731


class CaptureSink:
    def __init__(self):
        self.records = []
        self.closed = False
        self.close_calls = 0

    def write(self, record):
        self.records.append(record)

    def close(self):
        self.close_calls += 1
        self.closed = True


def stream(masker=NO_SECRETS):
    sink = CaptureSink()
    return EventStream("20260705-100000-abc123", masker, [sink], FIXED_CLOCK), sink


# --------------------------------------------------------------------------
# Vocabulary (FR-702)


def test_vocabulary_is_exactly_en7():
    assert set(EVENT_TYPES) == {
        "run_started",
        "node_fired",
        "request_started",
        "request_finished",
        "request_failed",
        "message_emitted",
        "assert_result",
        "python_error",
        "log",
        "guard_tripped",
        "budget_warning",
        "capture_warning",
        "frame_finished",
        "run_finished",
    }


def test_common_fields_stamped_in_order():
    s, sink = stream()
    s.emit(NodeFired(frame="f-0", node="req", firing_no=1))
    record = sink.records[0]
    expected_keys = ["event", "run_id", "frame", "node", "ts", "seq", "firing_no"]
    assert list(record) == expected_keys
    assert record["event"] == "node_fired"
    assert record["run_id"] == "20260705-100000-abc123"
    assert record["ts"] == "2026-07-05T10:00:00.123Z"
    assert record["seq"] == 1


def test_seq_increments_per_run():
    s, sink = stream()
    for i in range(3):
        s.emit(NodeFired(frame="f-0", node="n", firing_no=i))
    assert [r["seq"] for r in sink.records] == [1, 2, 3]


def test_run_started_carries_history_format_marker():
    # FR-1101: run_started is the envelope header — seq 1, self-identifying
    # via `format`. Every run written is now version-stamped.
    s, sink = stream()
    s.emit(
        RunStarted(flow="flows/demo", env_name="dev", inputs={}, engine_version="0.2")
    )
    record = sink.records[0]
    assert record["seq"] == 1
    assert record["format"] == HISTORY_FORMAT == "napflow-run/1"
    assert record["features"] == list(HISTORY_WRITE_FEATURES) == []


def test_history_format_reader_gate():
    # A reader identifies the on-disk contract before parsing (FR-1101):
    # older/equal majors readable, a newer major refused, a pre-versioning
    # v0.1 log (no marker) read best-effort as major 0.
    assert parse_history_format("napflow-run/1") == HISTORY_FORMAT_MAJOR == 1
    assert parse_history_format(None) == 0  # v0.1 log, best-effort (D33)
    assert is_supported("napflow-run/1") is True
    assert is_supported(None) is True
    assert is_supported("napflow-run/2") is False  # newer major refused
    with pytest.raises(HistoryFormatError):
        parse_history_format("postman-run/1")


@pytest.mark.parametrize(
    "value",
    [
        1,
        True,
        1.5,
        [],
        {},
        "napflow-run/²",
        "napflow-run/-1",
        "napflow-run/" + "9" * 5_000,
    ],
)
def test_history_format_reader_gate_rejects_arbitrary_json(value):
    with pytest.raises(HistoryFormatError):
        parse_history_format(value)
    assert is_supported(value) is False


def test_history_feature_reader_gate():
    assert parse_history_features([]) == HISTORY_SUPPORTED_FEATURES == frozenset()
    with pytest.raises(HistoryFormatError):
        parse_history_features(None)
    with pytest.raises(HistoryFormatError):
        parse_history_features(
            [HISTORY_FEATURE_CONTENT_BLOBS, HISTORY_FEATURE_CONTENT_BLOBS]
        )
    with pytest.raises(HistoryFormatError):
        parse_history_features([1])


def test_unset_frame_and_node_omitted():
    s, sink = stream()
    s.emit(
        RunStarted(flow="flows/demo", env_name="dev", inputs={}, engine_version="0.1")
    )
    record = sink.records[0]
    assert "frame" not in record
    assert "node" not in record


def test_required_nullable_field_kept_as_null():
    # env_name has no default: a run without a profile emits null, not absence.
    s, sink = stream()
    s.emit(
        RunStarted(flow="flows/demo", env_name=None, inputs={}, engine_version="0.1")
    )
    assert sink.records[0]["env_name"] is None


def test_optional_payload_fields_omitted_when_unset():
    s, sink = stream()
    finished = {
        "state": "passed",
        "duration_ms": 12.5,
        "asserts": {"passed": 1, "failed": 0},
        "unhandled_errors": [],
        "end_outputs": {},
        "nodes_never_fired": [],
    }
    s.emit(RunFinished(**finished))
    s.emit(RunFinished(**finished | {"state": "error"}, error_reason="run_timeout"))
    s.emit(
        AssertResult(node="a", check="status", expected=200, actual=200, passed=True)
    )
    assert "error_reason" not in sink.records[0]
    assert sink.records[1]["error_reason"] == "run_timeout"
    assert "op" not in sink.records[2]  # status checks carry no op


def test_frame_finished_is_a_reconstructable_child_summary():
    s, sink = stream()
    s.emit(
        FrameFinished(
            frame="f-0/f-4",
            parent_frame="f-0",
            parent_node="items",
            flow="flows/item",
            kind="loop",
            loop_index=3,
            duration_ms=12.5,
            state="passed",
            asserts={"passed": 1, "failed": 0},
            unhandled_errors=[],
            end_outputs={"value": 7},
        )
    )
    assert sink.records[0] == {
        "event": "frame_finished",
        "run_id": "20260705-100000-abc123",
        "frame": "f-0/f-4",
        "ts": "2026-07-05T10:00:00.123Z",
        "seq": 1,
        "parent_frame": "f-0",
        "parent_node": "items",
        "flow": "flows/item",
        "kind": "loop",
        "loop_index": 3,
        "duration_ms": 12.5,
        "state": "passed",
        "asserts": {"passed": 1, "failed": 0},
        "unhandled_errors": [],
        "end_outputs": {"value": 7},
    }


# --------------------------------------------------------------------------
# Masking (FR-106, D22)


def masker(**env):
    return SecretMasker(["*TOKEN*", "*_SECRET"], env)


def test_masks_matching_values_everywhere():
    m = masker(API_TOKEN="s3cr3t-value", DB_SECRET="p4ssw0rd!")
    masked = m.mask(
        {
            "url": "https://api.test?token=s3cr3t-value",
            "nested": {"list": ["p4ssw0rd!", 3, None]},
            "s3cr3t-value-key": True,
        }
    )
    assert masked == {
        "url": f"https://api.test?token={MASK}",
        "nested": {"list": [MASK, 3, None]},
        f"{MASK}-key": True,
    }


def test_short_values_never_masked():
    assert masker(API_TOKEN="abcd").mask("abcd") == "abcd"  # < 5 chars


def test_non_matching_names_not_masked():
    assert masker(HOSTNAME="visible-value").mask("visible-value") == "visible-value"


def test_longer_secret_masked_before_its_substring():
    m = masker(A_TOKEN="secret", B_TOKEN="secret-extended")
    assert m.mask("x secret-extended y") == f"x {MASK} y"


def test_events_born_masked():
    m = masker(API_TOKEN="s3cr3t-value")
    sink = CaptureSink()
    s = EventStream("r", m, [sink], FIXED_CLOCK)
    record = s.emit(LogEvent(node="log1", value={"auth": "Bearer s3cr3t-value"}))
    assert record["value"]["auth"] == f"Bearer {MASK}"
    assert sink.records[0] == record  # the sink never saw the secret


@pytest.mark.parametrize("secret", ["format", "features", HISTORY_FORMAT])
def test_run_started_envelope_is_never_masked(secret):
    m = SecretMasker(["TOKEN"], {"TOKEN": secret})
    sink = CaptureSink()
    s = EventStream("r", m, [sink], FIXED_CLOCK)
    record = s.emit(
        RunStarted(
            flow="flows/demo",
            env_name="dev",
            inputs={"value": secret},
            engine_version="0.2",
        )
    )

    assert record["event"] == "run_started"
    assert record["run_id"] == "r"
    assert record["seq"] == 1
    assert record["format"] == HISTORY_FORMAT
    assert record["features"] == []
    assert record["inputs"]["value"] == MASK  # payload masking still applies


def test_run_started_feature_names_are_never_masked():
    feature = HISTORY_FEATURE_CONTENT_BLOBS
    s = EventStream("r", SecretMasker(["TOKEN"], {"TOKEN": feature}), [], FIXED_CLOCK)
    record = s.emit(
        RunStarted(
            flow="flows/demo",
            env_name=None,
            inputs={},
            engine_version="0.2",
            features=[feature],
        )
    )
    assert record["features"] == [feature]


# --------------------------------------------------------------------------
# JSONL sink + run log layout (FR-701)


def test_jsonl_roundtrip(tmp_path):
    run_id = new_run_id()
    path = run_log_path(tmp_path, "flows/payments/refund", run_id)
    runs_root = tmp_path / ".napflow" / "runs"
    assert path == runs_root / "flows" / "payments" / "refund" / f"{run_id}.jsonl"
    sink = JsonlSink(path)
    s = EventStream(run_id, NO_SECRETS, [sink], FIXED_CLOCK)
    s.emit(
        RunStarted(
            flow="flows/payments/refund",
            env_name=None,
            inputs={"user": "ü"},
            engine_version="0.1",
        )
    )
    s.emit(NodeFired(frame="f-0", node="req", firing_no=1))
    s.close()

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    records = [json.loads(line) for line in lines]
    assert [r["event"] for r in records] == ["run_started", "node_fired"]
    assert records[0]["inputs"] == {"user": "ü"}  # ensure_ascii=False
    # compact separators, byte-stable
    assert lines[0] == json.dumps(records[0], ensure_ascii=False, separators=(",", ":"))


def test_last_jsonl_record_handles_lines_larger_than_the_read_block(tmp_path):
    path = tmp_path / "large.jsonl"
    expected = {
        "event": "run_finished",
        "state": "passed",
        "detail": "z" * 200_000,
    }
    path.write_bytes(
        b'{"event":"run_started","seq":1}\n'
        + json.dumps(expected, separators=(",", ":")).encode()
        + b"\n"
    )

    assert last_jsonl_record(path) == expected


def test_last_jsonl_record_skips_trailing_partial_and_non_object_lines(tmp_path):
    path = tmp_path / "interrupted.jsonl"
    expected = {"event": "request_started", "seq": 2}
    path.write_bytes(
        json.dumps(expected).encode()
        + b"\n[1,2,3]\n\n"
        + b'{"event":"request_finished","body":"'
        + b"x" * 80_000
    )

    assert last_jsonl_record(path) == expected


def test_last_jsonl_record_tolerates_missing_empty_and_unterminated_files(tmp_path):
    path = tmp_path / "record.jsonl"
    assert last_jsonl_record(path) is None

    path.touch()
    assert last_jsonl_record(path) is None

    expected = {"event": "run_finished", "state": "aborted"}
    path.write_text(json.dumps(expected), encoding="utf-8")
    assert last_jsonl_record(path) == expected


def test_sink_never_overwrites(tmp_path):
    path = run_log_path(tmp_path, "flows/demo", "20260712-120000-abcdef")
    JsonlSink(path).close()
    with pytest.raises(FileExistsError):
        JsonlSink(path)


def test_run_log_path_uses_workspace_boundary(tmp_path):
    with pytest.raises(WorkspaceBoundaryError):
        run_log_path(tmp_path, "../outside", new_run_id())
    with pytest.raises(WorkspaceBoundaryError):
        run_log_path(tmp_path, "flows/demo", "../outside")


def test_run_id_format_and_timestamp_prefix_sorting():
    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{6}", new_run_id())
    older = new_run_id(datetime(2026, 7, 5, 9, 0, 0, tzinfo=UTC))
    newer = new_run_id(datetime(2026, 7, 5, 10, 0, 0, tzinfo=UTC))
    assert older < newer  # distinct UTC seconds retain filename order


def test_retention_keeps_newest(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    ids = [new_run_id(datetime(2026, 7, 5, 9, m, 0, tzinfo=UTC)) for m in range(5)]
    for run_id in ids:
        _write_finished_log(runs / f"{run_id}.jsonl", run_id)
    deleted = apply_retention(runs, history=3)
    assert sorted(p.name for p in deleted) == [f"{i}.jsonl" for i in ids[:2]]
    assert sorted(p.name for p in runs.glob("*.jsonl")) == [
        f"{i}.jsonl" for i in ids[2:]
    ]


def test_retention_under_cap_deletes_nothing(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    run_id = new_run_id()
    _write_finished_log(runs / f"{run_id}.jsonl", run_id)
    assert apply_retention(runs, history=20) == []
    assert list(runs.glob("*.jsonl")) == [runs / f"{run_id}.jsonl"]


def _write_finished_log(path, run_id, *, state="passed"):
    path.write_text(
        json.dumps({"event": "run_finished", "run_id": run_id, "state": state})
        + "\n",
        encoding="utf-8",
    )


def test_retention_protects_active_and_incomplete_units(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    kept_id = "20260712-100000-000001"
    active_id = "20260712-100000-000002"
    incomplete_id = "20260712-100000-000003"
    kept = runs / f"{kept_id}.jsonl"
    active = runs / f"{active_id}.jsonl"
    incomplete = runs / f"{incomplete_id}.jsonl"
    for path, run_id in (
        (kept, kept_id),
        (active, active_id),
        (incomplete, incomplete_id),
    ):
        _write_finished_log(path, run_id)
    (runs / f"{active_id}.active").touch()
    (runs / f"{incomplete_id}.incomplete").touch()

    assert apply_retention(runs, history=1) == []
    assert kept.exists()
    assert active.exists()
    assert incomplete.exists()


def test_retention_requires_matching_canonical_completion_despite_metadata(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    incomplete_id = "20260712-100000-000001"
    mismatched_id = "20260712-100000-000002"
    complete_id = "20260712-100000-000003"
    incomplete = runs / f"{incomplete_id}.jsonl"
    mismatched = runs / f"{mismatched_id}.jsonl"
    complete = runs / f"{complete_id}.jsonl"
    incomplete.write_text('{"event":"run_started"}\n', encoding="utf-8")
    _write_finished_log(mismatched, complete_id)
    _write_finished_log(complete, complete_id)
    for order, log in enumerate((incomplete, mismatched, complete), start=1):
        (runs / f"{log.stem}.complete.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "run_id": log.stem,
                    "started_ns": order,
                    "completed_ns": order,
                    "order": order,
                }
            ),
            encoding="utf-8",
        )

    assert apply_retention(runs, history=1) == []
    assert incomplete.exists()
    assert mismatched.exists()
    assert complete.exists()


def test_retention_removes_exact_whole_unit_without_following_symlinks(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    old_id = "20260712-100000-000001"
    new_id = "20260712-100000-000002"
    old = runs / f"{old_id}.jsonl"
    new = runs / f"{new_id}.jsonl"
    _write_finished_log(old, old_id)
    _write_finished_log(new, new_id)
    old.touch()
    new.touch()
    os.utime(old, ns=(1_000_000_000, 1_000_000_000))
    os.utime(new, ns=(2_000_000_000, 2_000_000_000))
    (runs / f"{old_id}.report.json").write_text("{}", encoding="utf-8")
    (runs / f"{old_id}.junit.xml").write_text("<testsuite />", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("safe", encoding="utf-8")
    (runs / f"{old_id}.blobs").symlink_to(outside, target_is_directory=True)
    index = runs / f"{old_id}.index"
    index.mkdir()
    (index / "seek").write_text("index", encoding="utf-8")

    assert apply_retention(runs, history=1) == [old]
    assert not old.exists()
    assert not (runs / f"{old_id}.report.json").exists()
    assert not (runs / f"{old_id}.junit.xml").exists()
    assert not (runs / f"{old_id}.blobs").exists()
    assert not index.exists()
    assert new.exists()
    assert sentinel.read_text(encoding="utf-8") == "safe"


def test_run_history_lifecycle_publishes_complete_or_incomplete(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    complete_id = "20260712-100000-000001"
    complete_log = runs / f"{complete_id}.jsonl"
    complete_log.touch()
    complete = begin_run_history(complete_log, complete_id)
    assert complete.active_path.is_file()
    _write_finished_log(complete_log, complete_id)
    assert complete_run_history(complete, history=20) == []
    assert not complete.active_path.exists()
    assert complete.complete_path.is_file()

    incomplete_id = "20260712-100000-000002"
    incomplete_log = runs / f"{incomplete_id}.jsonl"
    incomplete_log.write_text('{"event":"run_started"}\n', encoding="utf-8")
    incomplete = begin_run_history(incomplete_log, incomplete_id)
    abandon_run_history(incomplete)
    assert not incomplete.active_path.exists()
    assert incomplete.incomplete_path.is_file()


def test_active_marker_failure_removes_its_partial_file(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    run_id = "20260712-100000-000001"
    log = runs / f"{run_id}.jsonl"
    log.touch()

    def fail_fsync(_fd):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(events_module.os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="simulated disk failure"):
        begin_run_history(log, run_id)

    assert not (runs / f"{run_id}.active").exists()


def test_history_lock_does_not_follow_symlink_outside_runs(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    run_id = "20260712-100000-000001"
    log = runs / f"{run_id}.jsonl"
    log.touch()
    outside = tmp_path / "outside-lock"
    try:
        (runs / ".history.lock").symlink_to(outside)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")

    with pytest.raises(OSError, match="history lock"):
        begin_run_history(log, run_id)

    assert not outside.exists()
    assert not (runs / f"{run_id}.active").exists()


def test_reader_lease_excludes_older_unit_from_cross_process_retention(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    old_id = "20260712-100000-000001"
    new_id = "20260712-100000-000002"
    old = runs / f"{old_id}.jsonl"
    new = runs / f"{new_id}.jsonl"
    _write_finished_log(old, old_id)
    _write_finished_log(new, new_id)
    os.utime(old, ns=(1_000_000_000, 1_000_000_000))
    os.utime(new, ns=(2_000_000_000, 2_000_000_000))

    with history_reader(old):
        assert len(list(runs.glob(f"{old_id}.reader-*"))) == 1
        assert apply_retention(runs, history=1) == []
        assert old.exists() and new.exists()

    assert list(runs.glob(f"{old_id}.reader-*")) == []
    assert apply_retention(runs, history=1) == [old]


def test_history_order_survives_equal_or_backward_wall_clock(tmp_path, monkeypatch):
    ticks = iter((100, 100, 50, 25))
    monkeypatch.setattr(events_module.time, "time_ns", lambda: next(ticks))
    runs = tmp_path / "runs"
    runs.mkdir()
    older_id = "20260712-100000-ffffff"
    newer_id = "20260712-100000-000000"
    older_log = runs / f"{older_id}.jsonl"
    newer_log = runs / f"{newer_id}.jsonl"
    older_log.touch()
    older = begin_run_history(older_log, older_id)
    _write_finished_log(older_log, older_id)
    complete_run_history(older, history=20)
    newer_log.touch()
    newer = begin_run_history(newer_log, newer_id)
    _write_finished_log(newer_log, newer_id)
    complete_run_history(newer, history=20)

    assert run_history_sort_key(older_log) < run_history_sort_key(newer_log)
    assert sorted((older_log, newer_log), key=run_history_sort_key) == [
        older_log,
        newer_log,
    ]
    assert apply_retention(runs, history=1) == [older_log]
    assert newer_log.exists()


def test_retention_resumes_an_interrupted_claim(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    run_id = "20260712-100000-000001"
    log = runs / f"{run_id}.jsonl"
    _write_finished_log(log, run_id)
    (runs / f"{run_id}.report.json").write_text("{}", encoding="utf-8")
    (runs / f"{run_id}.deleting").touch()

    assert apply_retention(runs, history=20) == [log]
    assert not log.exists()
    assert not (runs / f"{run_id}.report.json").exists()
    assert not (runs / f"{run_id}.deleting").exists()


def test_deletion_claim_is_durable_metadata_before_unit_removal(
    tmp_path, monkeypatch
):
    runs = tmp_path / "runs"
    runs.mkdir()
    old_id = "20260712-100000-000001"
    new_id = "20260712-100000-000002"
    old = runs / f"{old_id}.jsonl"
    new = runs / f"{new_id}.jsonl"
    _write_finished_log(old, old_id)
    _write_finished_log(new, new_id)
    os.utime(old, ns=(1_000_000_000, 1_000_000_000))
    os.utime(new, ns=(2_000_000_000, 2_000_000_000))

    def interrupt(_log):
        raise OSError("interrupted")

    monkeypatch.setattr(events_module, "_delete_claimed_unit", interrupt)

    assert apply_retention(runs, history=1) == []
    claim = runs / f"{old_id}.deleting"
    assert json.loads(claim.read_text(encoding="utf-8")) == {
        "version": 1,
        "run_id": old_id,
    }


def test_retention_never_resumes_tombstone_over_active_unit(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    run_id = "20260712-100000-000001"
    log = runs / f"{run_id}.jsonl"
    _write_finished_log(log, run_id)
    (runs / f"{run_id}.active").touch()
    (runs / f"{run_id}.deleting").touch()

    assert apply_retention(runs, history=1) == []
    assert log.exists()
    assert (runs / f"{run_id}.active").exists()
    assert not (runs / f"{run_id}.deleting").exists()


def test_stream_close_closes_sinks():
    s, sink = stream()
    s.close()
    s.close()
    assert sink.closed
    assert sink.close_calls == 1


def test_stream_close_attempts_every_sink_before_reporting_failure():
    class BrokenSink(CaptureSink):
        def close(self):
            super().close()
            raise OSError("close failed")

    broken = BrokenSink()
    healthy = CaptureSink()
    s = EventStream("r", NO_SECRETS, [broken, healthy], FIXED_CLOCK)

    with pytest.raises(OSError, match="close failed"):
        s.close()

    assert broken.closed
    assert healthy.closed
