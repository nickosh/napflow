"""Events + JSONL + masking (S2/M2): vocabulary per EN §7 (FR-702),
JSONL sink + retention (FR-701), secrets masked at emission (FR-106,
D22 — events are born masked)."""

import json
import re
from datetime import UTC, datetime

import pytest

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
    HistoryFormatError,
    JsonlSink,
    LogEvent,
    NodeFired,
    RunFinished,
    RunStarted,
    SecretMasker,
    apply_retention,
    is_supported,
    new_run_id,
    parse_history_features,
    parse_history_format,
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


def test_run_id_format_and_sortability():
    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{6}", new_run_id())
    older = new_run_id(datetime(2026, 7, 5, 9, 0, 0, tzinfo=UTC))
    newer = new_run_id(datetime(2026, 7, 5, 10, 0, 0, tzinfo=UTC))
    assert older < newer  # lexicographic == chronological


def test_retention_keeps_newest(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    ids = [new_run_id(datetime(2026, 7, 5, 9, m, 0, tzinfo=UTC)) for m in range(5)]
    for run_id in ids:
        (runs / f"{run_id}.jsonl").touch()
    deleted = apply_retention(runs, history=3)
    assert sorted(p.name for p in deleted) == [f"{i}.jsonl" for i in ids[:2]]
    assert sorted(p.name for p in runs.iterdir()) == [f"{i}.jsonl" for i in ids[2:]]


def test_retention_under_cap_deletes_nothing(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / f"{new_run_id()}.jsonl").touch()
    assert apply_retention(runs, history=20) == []
    assert len(list(runs.iterdir())) == 1


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
