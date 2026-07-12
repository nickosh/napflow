"""Event vocabulary + JSONL sink + secret masking (EN §7, D22).

One JSON object per line/frame; the JSONL file and the future WebSocket
stream carry IDENTICAL records (D13) — both are fed by `EventStream`,
which stamps `run_id`/`ts`/`seq` and masks payloads at emission. Structural
envelope fields (`event`, run/frame/node identity, ordering, history format,
and feature declarations) are never rewritten by masking.

Wire shape: common fields `{event, run_id, frame, node, ts, seq}` then
the event's payload fields in declaration order. Optional fields
(declared with default None — incl. `frame`/`node`) are omitted when
unset; required nullable fields (e.g. run_started `env_name`) appear as
null.

Payload masking (D22): the VALUES of env vars whose names match
`environments.secrets` glob patterns (layered active profile + process
env) are replaced with `***` wherever they appear in payload fields —
substring scan over strings and keys, 5-char minimum, longest value first.
Declared secrets only; runtime-acquired tokens are stored in full (roadmap).
"""

import json
import secrets as _secrets
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict as _asdict
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar, Literal

MASK = "***"

# --------------------------------------------------------------------------
# Run-history format version (FR-1101, D34)
#
# The `run_started` event is the run-history ENVELOPE HEADER: it is always
# seq 1, and it carries `format` = HISTORY_FORMAT. A reader identifies the
# on-disk contract from that field before interpreting the rest of the log.
#
# `napflow-run/<major>`: the major bumps on a breaking base event/envelope
# change. Additive storage capabilities carry separately versioned feature
# names. A newer major or unknown feature is refused before replay; a log
# with no `format` field predates versioning and is read best-effort as major
# 0 (v0.1 logs, D33).
#
# v0.2 storage (blobs/indexes) lands in later milestones; the marker is
# pinned here, BEFORE the format changes, so every run written from now on
# is self-identifying.

HISTORY_FORMAT = "napflow-run/1"
HISTORY_FORMAT_MAJOR = 1

# Optional format capabilities are declared separately from the event-format
# major. This M0 build writes/reads pure inline JSONL only. M4 will declare
# `content-blobs/1` when it can both resolve and verify `$napflow` persisted-
# value envelopes; older readers then reject the feature instead of silently
# exposing descriptors as user data.
HISTORY_FEATURE_CONTENT_BLOBS = "content-blobs/1"
HISTORY_WRITE_FEATURES: tuple[str, ...] = ()
HISTORY_SUPPORTED_FEATURES: frozenset[str] = frozenset()


class HistoryFormatError(ValueError):
    """A run log declares a history format this build cannot read."""


def _marker_repr(value: Any, limit: int = 96) -> str:
    """Bounded diagnostic representation for untrusted history metadata."""
    shown = repr(value)
    return shown if len(shown) <= limit else f"{shown[: limit - 3]}..."


def parse_history_format(value: Any) -> int:
    """Major version from a `napflow-run/<major>` marker.

    The envelope reader passes `None` only for a missing legacy marker;
    explicitly present null is rejected before this helper. A malformed
    marker raises `HistoryFormatError`.
    """
    if value is None:
        return 0
    if not isinstance(value, str):
        raise HistoryFormatError(
            f"run-history format must be a string, not {type(value).__name__}"
        )
    prefix, sep, major = value.partition("/")
    if (
        prefix != "napflow-run"
        or not sep
        or not major
        or any(char < "0" or char > "9" for char in major)
    ):
        raise HistoryFormatError(
            f"unrecognized run-history format {_marker_repr(value)}"
        )
    try:
        return int(major)
    except ValueError as e:
        # CPython bounds decimal conversion length; even an all-ASCII digit
        # marker must still fail through the public format-error contract.
        raise HistoryFormatError(
            f"unrecognized run-history format {_marker_repr(value)}"
        ) from e


def is_supported(value: Any) -> bool:
    """True when this build can read a run log declaring `value`. Older or
    equal majors are readable (best-effort for major 0); a newer major is
    not."""
    try:
        return parse_history_format(value) <= HISTORY_FORMAT_MAJOR
    except HistoryFormatError:
        return False


def parse_history_features(value: Any) -> frozenset[str]:
    """Validate an explicitly present history feature list.

    A missing field is handled by the envelope reader as the legacy empty
    set. Explicit null is not a list and is therefore malformed.
    """
    if not isinstance(value, list):
        raise HistoryFormatError(
            f"run-history features must be an array, not {type(value).__name__}"
        )
    if any(not isinstance(item, str) or not item for item in value):
        raise HistoryFormatError("run-history features must be non-empty strings")
    if len(set(value)) != len(value):
        raise HistoryFormatError("run-history features must not contain duplicates")
    return frozenset(value)


# --------------------------------------------------------------------------
# Vocabulary (EN §7) — one dataclass per event type


@dataclass(kw_only=True)
class Event:
    """Base event: producers set `frame`/`node` where meaningful;
    `run_id`/`ts`/`seq` are stamped by the EventStream at emission."""

    event: ClassVar[str]
    frame: str | None = None
    node: str | None = None


@dataclass(kw_only=True)
class RunStarted(Event):
    """The run-history envelope header (seq 1). `format` self-identifies
    the on-disk contract (FR-1101) so any reader can gate before parsing
    the rest of the log."""

    event: ClassVar[str] = "run_started"
    format: str = HISTORY_FORMAT
    flow: str
    env_name: str | None
    inputs: dict[str, Any]
    engine_version: str
    features: list[str] = field(default_factory=lambda: list(HISTORY_WRITE_FEATURES))


@dataclass(kw_only=True)
class NodeFired(Event):
    event: ClassVar[str] = "node_fired"
    firing_no: int


@dataclass(kw_only=True)
class RequestStarted(Event):
    event: ClassVar[str] = "request_started"
    method: str
    url: str
    headers: dict[str, Any]
    body_preview: Any
    attempt: int


@dataclass(kw_only=True)
class RequestFinished(Event):
    """`body` is COMPLETE (full wire detail, D13) — capture valves mark
    `truncated: true` inside the body envelope, they never elide the
    event. `timing` carries only the fields niquests exposes."""

    event: ClassVar[str] = "request_finished"
    status: int
    http_version: str | None
    headers: dict[str, Any]
    body: Any
    size_bytes: int
    timing: dict[str, float]
    attempt: int
    retries_total: int


@dataclass(kw_only=True)
class RequestFailed(Event):
    event: ClassVar[str] = "request_failed"
    error_kind: str
    message: str
    attempt: int
    will_retry: bool


@dataclass(kw_only=True)
class MessageEmitted(Event):
    event: ClassVar[str] = "message_emitted"
    from_port: str
    to_node: str
    to_port: str
    msg_id: str
    value_preview: Any


@dataclass(kw_only=True)
class AssertResult(Event):
    event: ClassVar[str] = "assert_result"
    check: str
    op: str | None = None  # expr checks only; omitted for status/response_time
    expected: Any
    actual: Any
    passed: bool


@dataclass(kw_only=True)
class PythonError(Event):
    event: ClassVar[str] = "python_error"
    function: str
    error_type: str
    message: str
    traceback: str


@dataclass(kw_only=True)
class LogEvent(Event):
    event: ClassVar[str] = "log"
    label: str | None = None
    level: Literal["debug", "info", "warn", "error"] = "info"
    value: Any


@dataclass(kw_only=True)
class GuardTripped(Event):
    event: ClassVar[str] = "guard_tripped"
    kind: Literal["counter", "timeout"]
    port: Literal["exhausted", "expired"]


@dataclass(kw_only=True)
class BudgetWarning(Event):
    event: ClassVar[str] = "budget_warning"
    remaining: int


@dataclass(kw_only=True)
class CaptureWarning(Event):
    event: ClassVar[str] = "capture_warning"
    remaining_mb: float


@dataclass(kw_only=True)
class RunFinished(Event):
    event: ClassVar[str] = "run_finished"
    state: Literal["passed", "failed", "error", "aborted"]
    duration_ms: float
    asserts: dict[str, int]  # {passed, failed}
    unhandled_errors: Any  # shape pinned when the engine emits it (M3)
    end_outputs: dict[str, Any]
    nodes_never_fired: list[str]  # "skipped" for UI/report
    error_reason: str | None = None  # only when state=error


EVENT_TYPES: dict[str, type[Event]] = {
    cls.event: cls
    for cls in (
        RunStarted,
        NodeFired,
        RequestStarted,
        RequestFinished,
        RequestFailed,
        MessageEmitted,
        AssertResult,
        PythonError,
        LogEvent,
        GuardTripped,
        BudgetWarning,
        CaptureWarning,
        RunFinished,
    )
}


@lru_cache
def _omit_if_none(cls: type[Event]) -> frozenset[str]:
    """Optional fields: declared with default None ⇒ omitted when unset."""
    return frozenset(f.name for f in fields(cls) if f.default is None)


# --------------------------------------------------------------------------
# Masking (D22, FR-106)


class SecretMasker:
    """Replaces declared-secret VALUES with `***` in event records.

    `patterns` are the `environments.secrets` globs over env var NAMES
    (matched case-sensitively); `env` is the layered environment
    (active profile + process env, FR-104). Values shorter than 5 chars
    are never masked (D22 — avoids over-masking short common strings).
    Longest value first, so one secret embedded in another masks fully.
    """

    def __init__(self, patterns: Iterable[str], env: Mapping[str, str]):
        patterns = list(patterns)
        self._values = sorted(
            {
                value
                for name, value in env.items()
                if len(value) >= 5 and any(fnmatchcase(name, p) for p in patterns)
            },
            key=len,
            reverse=True,
        )

    def mask_text(self, text: str) -> str:
        for value in self._values:
            text = text.replace(value, MASK)
        return text

    def mask(self, value: Any) -> Any:
        """Recursively mask every string in a JSON-compatible structure
        — dict keys included ("wherever they appear", D22)."""
        if not self._values:
            return value
        if isinstance(value, str):
            return self.mask_text(value)
        if isinstance(value, dict):
            return {
                self.mask_text(k) if isinstance(k, str) else k: self.mask(v)
                for k, v in value.items()
            }
        if isinstance(value, list | tuple):
            return [self.mask(v) for v in value]
        return value


# --------------------------------------------------------------------------
# Run log location + retention (FR-701)

RUNS_DIRNAME = Path(".napflow") / "runs"


def new_run_id(now: datetime | None = None) -> str:
    """Sortable, Windows-safe (no `:`): `YYYYmmdd-HHMMSS-xxxxxx` (UTC +
    6 hex). Doubles as the JSONL filename stem."""
    now = now if now is not None else datetime.now(UTC)
    return f"{now:%Y%m%d-%H%M%S}-{_secrets.token_hex(3)}"


def run_log_path(workspace_root: Path, flow_identity: str, run_id: str) -> Path:
    """`.napflow/runs/<flow>/<run-id>.jsonl` — <flow> is the
    workspace-relative identity, so nested flows nest here too."""
    return workspace_root / RUNS_DIRNAME / Path(flow_identity) / f"{run_id}.jsonl"


def apply_retention(runs_dir: Path, history: int) -> list[Path]:
    """Keep the newest `history` run logs in one flow's directory,
    delete the rest (returned). Filenames sort chronologically (run ids
    are UTC-timestamp-prefixed). Called at run start, after the new
    run's file is created — the new run counts toward the cap."""
    logs = sorted(runs_dir.glob("*.jsonl"))
    excess = logs[:-history] if len(logs) > history else []
    for log in excess:
        log.unlink()
    return excess


# --------------------------------------------------------------------------
# Sinks + stream


def encode_record(record: dict[str, Any]) -> str:
    """THE record wire encoding — compact JSON, `ensure_ascii=False`.
    JSONL lines and WebSocket frames both use it, so they are identical
    by construction (D13: replay = re-read file)."""
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


class JsonlSink:
    """Append-only run log, one compact JSON object per line (UTF-8,
    LF, `ensure_ascii=False`). Every line is flushed as written, so an
    abort leaves a valid replayable prefix — a dangling
    `request_started` is tolerated by replay (EC20)."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        # "x": run ids must be unique — collide loudly, never overwrite
        self._file = path.open("x", encoding="utf-8", newline="\n")

    def write(self, record: dict[str, Any]) -> None:
        self._file.write(encode_record(record) + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()


class EventStream:
    """Stamps common fields, masks, fans out. A sink is anything with
    `write(record: dict)` and `close()` — JSONL now, WebSocket at S4."""

    def __init__(
        self,
        run_id: str,
        masker: SecretMasker,
        sinks: Iterable[Any],
        clock: Callable[[], datetime] | None = None,
    ):
        self.run_id = run_id
        self._masker = masker
        self._sinks = list(sinks)
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        self._seq = 0

    def emit(self, event: Event) -> dict[str, Any]:
        """Serialize + mask payloads + fan out; returns the wire record.
        Protocol-envelope fields are structural and never rewritten by
        masking."""
        self._seq += 1
        data = _asdict(event)
        record: dict[str, Any] = {"event": type(event).event, "run_id": self.run_id}
        for common in ("frame", "node"):
            value = data.pop(common)
            if value is not None:
                record[common] = value
        record["ts"] = isoformat_ms(self._clock())
        record["seq"] = self._seq
        # `format`/`features` are part of the run_started envelope, not user
        # payload. Keep them outside recursive masking just like event/run_id/
        # seq: a declared secret must not destroy the reader gate.
        if isinstance(event, RunStarted):
            record["format"] = data.pop("format")
            record["features"] = data.pop("features")
        omittable = _omit_if_none(type(event))
        payload: dict[str, Any] = {}
        for key, value in data.items():
            if value is None and key in omittable:
                continue
            payload[key] = value
        record.update(self._masker.mask(payload))
        for sink in self._sinks:
            sink.write(record)
        return record

    def close(self) -> None:
        for sink in self._sinks:
            sink.close()


def isoformat_ms(dt: datetime) -> str:
    """UTC, millisecond precision, `Z` suffix — the Message meta format
    (EN §1): `2026-06-11T10:00:00.123Z`."""
    dt = dt.astimezone(UTC)
    return f"{dt:%Y-%m-%dT%H:%M:%S}.{dt.microsecond // 1000:03d}Z"
