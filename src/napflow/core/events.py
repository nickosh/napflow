"""Event vocabulary, canonical JSONL, and redacted views (EN §7, D35).

One JSON object per line/frame; the JSONL file and the local WebSocket
stream carry IDENTICAL records (D13) — both are fed by `EventStream`,
which stamps `run_id`/`ts`/`seq` and fans out the raw canonical record.
Terminal/report sinks receive a separate schema-aware redacted projection;
redaction never rewrites protocol structure or dictionary keys.

Wire shape: common fields `{event, run_id, frame, node, ts, seq}` then
the event's payload fields in declaration order. Optional fields
(declared with default None — incl. `frame`/`node`) are omitted when
unset; required nullable fields (e.g. run_started `env_name`) appear as
null.

Presentation redaction (D35): the VALUES of env vars whose names match
`environments.secrets` glob patterns (layered active profile + process
env) are replaced with `***` only inside schema-declared content fields —
substring scan over string values, 5-char minimum, longest value first.
Declared secrets only; runtime-acquired tokens remain a roadmap item.
"""

import json
import os
import re
import secrets as _secrets
import shutil
import stat
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager, suppress
from copy import deepcopy
from dataclasses import asdict as _asdict
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from enum import Enum
from fnmatch import fnmatchcase
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar, Literal

from napflow.core.files import atomic_write_text
from napflow.core.history_content import RunContentStore
from napflow.core.workspace import WorkspaceResolver

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
# v0.2 storage (blobs/indexes) lands in staged milestones; the marker was
# pinned before the format changed, so every run written from M0 on is
# self-identifying.

HISTORY_FORMAT = "napflow-run/1"
HISTORY_FORMAT_MAJOR = 1

# Optional format capabilities are declared separately from the event-format
# major. A stream advertises ``content-blobs/1`` only when it has an explicit
# RunContentStore and therefore encodes every schema-declared content field.
# Featureless/legacy records never interpret ``$napflow`` as protocol data.
HISTORY_FEATURE_CONTENT_BLOBS = "content-blobs/1"
HISTORY_WRITE_FEATURES: tuple[str, ...] = (HISTORY_FEATURE_CONTENT_BLOBS,)
HISTORY_SUPPORTED_FEATURES: frozenset[str] = frozenset(HISTORY_WRITE_FEATURES)


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
    # EventStream owns this envelope field: store-backed streams replace it
    # with HISTORY_WRITE_FEATURES; featureless streams replace it with [].
    features: list[str] = field(default_factory=list)


@dataclass(kw_only=True)
class NodeFired(Event):
    event: ClassVar[str] = "node_fired"
    firing_no: int


@dataclass(kw_only=True)
class RequestStarted(Event):
    event: ClassVar[str] = "request_started"
    method: str
    url: str
    attempt: int
    request: dict[str, Any]


@dataclass(kw_only=True)
class RequestFinished(Event):
    """Full logical response plus cheap replay/list summaries (D34)."""

    event: ClassVar[str] = "request_finished"
    method: str
    url: str
    status: int
    http_version: str | None
    size_bytes: int
    timing: dict[str, float]
    attempt: int
    retries_total: int
    redirects_total: int
    request: dict[str, Any]
    response: dict[str, Any]


@dataclass(kw_only=True)
class RequestFailed(Event):
    event: ClassVar[str] = "request_failed"
    method: str
    url: str
    error_kind: str
    message: str
    attempt: int
    will_retry: bool
    redirects_total: int
    request: dict[str, Any] | None = None


@dataclass(kw_only=True)
class MessageEmitted(Event):
    event: ClassVar[str] = "message_emitted"
    from_port: str
    to_node: str
    to_port: str
    msg_id: str
    value: Any


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
class FrameFinished(Event):
    """Durable child-frame completion record (D36/NFR-14).

    Runtime ``Frame`` objects are released after this record is emitted;
    replay uses these records, not retained Python objects, to reconstruct
    the completed frame tree.
    """

    event: ClassVar[str] = "frame_finished"
    parent_frame: str
    parent_node: str
    flow: str
    kind: Literal["flow", "loop"]
    loop_index: int | None
    duration_ms: float
    state: Literal["passed", "failed", "aborted"]
    asserts: dict[str, int]
    unhandled_errors: list[dict[str, Any]]
    end_outputs: dict[str, Any]


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
        FrameFinished,
        RunFinished,
    )
}


class EventFieldPolicy(Enum):
    """How one persisted event field participates in M4 processing.

    ``STRUCTURE`` is copied exactly. ``CONTENT`` is one logical value;
    ``CONTENT_MAP_VALUES`` keeps the mapping keys structural and treats each
    value independently; ``ERROR_MESSAGES`` keeps error records structural
    and treats only their ``message`` as content. ``PREPARED_REQUEST`` and
    ``HTTP_RESPONSE`` are persisted as one logical value while presentation
    redaction preserves their nested protocol vocabulary.
    """

    STRUCTURE = "structure"
    CONTENT = "content"
    CONTENT_MAP_VALUES = "content_map_values"
    ERROR_MESSAGES = "error_messages"
    PREPARED_REQUEST = "prepared_request"
    HTTP_RESPONSE = "http_response"


_S = EventFieldPolicy.STRUCTURE
_C = EventFieldPolicy.CONTENT
_M = EventFieldPolicy.CONTENT_MAP_VALUES
_E = EventFieldPolicy.ERROR_MESSAGES
_Q = EventFieldPolicy.PREPARED_REQUEST
_R = EventFieldPolicy.HTTP_RESPONSE

# Exhaustive event-specific field classification. This is deliberately a
# registry of *every* dataclass field, not a content-only allowlist: adding a
# field without deciding whether it is structure or complete content is a
# protocol error. The content encoder/resolver and presentation redactor share
# this boundary.
EVENT_FIELD_POLICIES: dict[type[Event], dict[str, EventFieldPolicy]] = {
    RunStarted: {
        "format": _S,
        "flow": _S,
        "env_name": _S,
        "inputs": _M,
        "engine_version": _S,
        "features": _S,
    },
    NodeFired: {"firing_no": _S},
    RequestStarted: {
        "method": _S,
        "url": _C,
        "attempt": _S,
        "request": _Q,
    },
    RequestFinished: {
        "method": _S,
        "url": _C,
        "status": _S,
        "http_version": _S,
        "size_bytes": _S,
        "timing": _S,
        "attempt": _S,
        "retries_total": _S,
        "redirects_total": _S,
        "request": _Q,
        "response": _R,
    },
    RequestFailed: {
        "method": _S,
        "url": _C,
        "error_kind": _S,
        "message": _C,
        "attempt": _S,
        "will_retry": _S,
        "redirects_total": _S,
        "request": _Q,
    },
    MessageEmitted: {
        "from_port": _S,
        "to_node": _S,
        "to_port": _S,
        "msg_id": _S,
        "value": _C,
    },
    AssertResult: {
        "check": _C,
        "op": _S,
        "expected": _C,
        "actual": _C,
        "passed": _S,
    },
    PythonError: {
        "function": _S,
        "error_type": _S,
        "message": _C,
        "traceback": _C,
    },
    LogEvent: {"label": _C, "level": _S, "value": _C},
    GuardTripped: {"kind": _S, "port": _S},
    BudgetWarning: {"remaining": _S},
    CaptureWarning: {"remaining_mb": _S},
    FrameFinished: {
        "parent_frame": _S,
        "parent_node": _S,
        "flow": _S,
        "kind": _S,
        "loop_index": _S,
        "duration_ms": _S,
        "state": _S,
        "asserts": _S,
        "unhandled_errors": _E,
        "end_outputs": _M,
    },
    RunFinished: {
        "state": _S,
        "duration_ms": _S,
        "asserts": _S,
        "unhandled_errors": _E,
        "end_outputs": _M,
        "nodes_never_fired": _S,
        "error_reason": _S,
    },
}

_DATACLASS_COMMON_FIELDS = frozenset({"frame", "node"})
_WIRE_COMMON_FIELDS = frozenset({"event", "run_id", "frame", "node", "ts", "seq"})
_ERROR_RECORD_FIELDS = frozenset({"frame", "node", "port", "kind", "message"})
_PREPARED_REQUEST_FIELDS = frozenset({"method", "url", "headers", "body", "size_bytes"})
_HTTP_RESPONSE_FIELDS = frozenset(
    {
        "status",
        "headers",
        "body",
        "size_bytes",
        "timing",
        "elapsed_ms",
        "url",
        "http_version",
        "attempt",
        "retries_total",
        "redirects_total",
    }
)


def _validate_event_field_policies() -> None:
    """Fail import if an event field has no explicit persistence policy."""
    if set(EVENT_FIELD_POLICIES) != set(EVENT_TYPES.values()):
        raise RuntimeError("event field policy registry does not cover EVENT_TYPES")
    for event_type, policies in EVENT_FIELD_POLICIES.items():
        declared = {item.name for item in fields(event_type)} - _DATACLASS_COMMON_FIELDS
        if set(policies) != declared:
            missing = sorted(declared - set(policies))
            extra = sorted(set(policies) - declared)
            raise RuntimeError(
                f"{event_type.event} field policies mismatch: "
                f"missing={missing}, extra={extra}"
            )


_validate_event_field_policies()


def _policies_for_record(
    record: Mapping[str, Any],
) -> tuple[str, dict[str, EventFieldPolicy]]:
    """Return the exhaustive field policy for one logical/canonical record."""
    event_name = record.get("event")
    event_type = EVENT_TYPES.get(event_name) if isinstance(event_name, str) else None
    if event_type is None:
        raise HistoryFormatError(
            f"no content policy for event {_marker_repr(event_name)}"
        )
    policies = EVENT_FIELD_POLICIES[event_type]
    unknown = set(record) - _WIRE_COMMON_FIELDS - set(policies)
    if unknown:
        raise HistoryFormatError(
            f"no content policy for {event_name} fields {sorted(unknown)!r}"
        )
    return event_name, policies


def _require_string_map(value: Any, *, field_name: str) -> dict[str, Any]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise HistoryFormatError(f"{field_name} must be an object with string keys")
    return value


def _require_exact_object(
    value: Any,
    expected: frozenset[str],
    *,
    field_name: str,
) -> dict[str, Any]:
    value = _require_string_map(value, field_name=field_name)
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise HistoryFormatError(
            f"{field_name} fields mismatch: missing={missing}, extra={extra}"
        )
    _require_string_map(value["headers"], field_name=f"{field_name}.headers")
    return value


def _transform_error_messages(
    value: Any,
    event_name: str,
    transform: Callable[[Any], Any],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise HistoryFormatError(
            f"{event_name}.unhandled_errors must be an array for content processing"
        )
    transformed: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) - _ERROR_RECORD_FIELDS:
            raise HistoryFormatError(
                f"{event_name}.unhandled_errors has an unclassified record"
            )
        shown = deepcopy(item)
        if "message" in shown:
            shown["message"] = transform(shown["message"])
        transformed.append(shown)
    return transformed


def _transform_record_content(
    record: Mapping[str, Any],
    store: RunContentStore,
    *,
    resolve: bool,
) -> dict[str, Any]:
    event_name, policies = _policies_for_record(record)
    transformed = deepcopy(dict(record))
    transform = store.resolve if resolve else store.persist

    for name, policy in policies.items():
        if name not in record or policy is EventFieldPolicy.STRUCTURE:
            continue
        value = record[name]
        if policy is EventFieldPolicy.CONTENT:
            transformed[name] = transform(value)
        elif policy is EventFieldPolicy.CONTENT_MAP_VALUES:
            values = _require_string_map(value, field_name=f"{event_name}.{name}")
            transformed[name] = {key: transform(item) for key, item in values.items()}
        elif policy is EventFieldPolicy.ERROR_MESSAGES:
            transformed[name] = _transform_error_messages(value, event_name, transform)
        elif policy in {
            EventFieldPolicy.PREPARED_REQUEST,
            EventFieldPolicy.HTTP_RESPONSE,
        }:
            expected = (
                _PREPARED_REQUEST_FIELDS
                if policy is EventFieldPolicy.PREPARED_REQUEST
                else _HTTP_RESPONSE_FIELDS
            )
            field_name = f"{event_name}.{name}"
            if resolve:
                logical = transform(value)
                _require_exact_object(logical, expected, field_name=field_name)
                transformed[name] = logical
            else:
                _require_exact_object(value, expected, field_name=field_name)
                transformed[name] = transform(value)
        else:  # pragma: no cover - enum/registry exhaustiveness guard
            raise AssertionError(f"unhandled event field policy: {policy}")
    return transformed


def persist_record_content(
    record: Mapping[str, Any], store: RunContentStore
) -> dict[str, Any]:
    """Persist every schema-declared content value in one event record.

    The returned record is a canonical copy; ``record`` and its runtime values
    are not mutated. The caller must advertise ``content-blobs/1`` on the
    enclosing run before exposing the returned descriptors to readers.
    """
    return _transform_record_content(record, store, resolve=False)


def _supported_feature_set(features: Iterable[str]) -> frozenset[str]:
    if isinstance(features, str | bytes):
        raise HistoryFormatError("run-history features must be an array")
    try:
        parsed = parse_history_features(list(features))
    except TypeError as error:
        raise HistoryFormatError("run-history features must be an array") from error
    unsupported = parsed - HISTORY_SUPPORTED_FEATURES
    if unsupported:
        raise HistoryFormatError(
            f"unsupported run-history features: {sorted(unsupported)!r}"
        )
    return parsed


def resolve_record_content(
    record: Mapping[str, Any],
    features: Iterable[str],
    store: RunContentStore,
) -> dict[str, Any]:
    """Resolve one canonical record under its declared feature envelope.

    Featureless history is copied through without interpreting marker-shaped
    user data. Unknown features fail before any content field is inspected.
    """
    active = _supported_feature_set(features)
    if HISTORY_FEATURE_CONTENT_BLOBS not in active:
        return deepcopy(dict(record))
    return _transform_record_content(record, store, resolve=True)


@lru_cache
def _omit_if_none(cls: type[Event]) -> frozenset[str]:
    """Optional fields: declared with default None ⇒ omitted when unset."""
    return frozenset(f.name for f in fields(cls) if f.default is None)


# --------------------------------------------------------------------------
# Masking (D22, FR-106)


class SecretMasker:
    """Build declared-secret redacted presentation values and records.

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
        """Recursively mask string values while preserving dictionary keys."""
        if not self._values:
            return value
        if isinstance(value, str):
            return self.mask_text(value)
        if isinstance(value, dict):
            return {key: self.mask(item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [self.mask(v) for v in value]
        return value

    def redact_record(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Return a redacted presentation copy of one canonical event.

        The exhaustive field registry defines the content boundary. Unknown
        events or fields fail closed rather than leaking an unclassified value
        into a nominally safe report.
        """
        try:
            event_name, policies = _policies_for_record(record)
        except HistoryFormatError as error:
            raise HistoryFormatError(
                str(error).replace("content policy", "redaction policy")
            ) from error

        redacted = dict(record)
        for name, policy in policies.items():
            if name not in record or policy is EventFieldPolicy.STRUCTURE:
                continue
            if policy is EventFieldPolicy.ERROR_MESSAGES:
                redacted[name] = self._redact_error_messages(record[name], event_name)
            elif policy is EventFieldPolicy.PREPARED_REQUEST:
                redacted[name] = self._redact_prepared_request(
                    record[name], f"{event_name}.{name}"
                )
            elif policy is EventFieldPolicy.HTTP_RESPONSE:
                redacted[name] = self._redact_http_response(
                    record[name], f"{event_name}.{name}"
                )
            else:
                redacted[name] = self.mask(record[name])
        return redacted

    def _redact_prepared_request(self, value: Any, field_name: str) -> dict[str, Any]:
        request = _require_exact_object(
            value, _PREPARED_REQUEST_FIELDS, field_name=field_name
        )
        return {
            "method": request["method"],
            "url": self.mask(request["url"]),
            "headers": {
                key: self.mask(item) for key, item in request["headers"].items()
            },
            "body": self.mask(request["body"]),
            "size_bytes": request["size_bytes"],
        }

    def _redact_http_response(self, value: Any, field_name: str) -> dict[str, Any]:
        response = _require_exact_object(
            value, _HTTP_RESPONSE_FIELDS, field_name=field_name
        )
        shown = deepcopy(response)
        shown["headers"] = {
            key: self.mask(item) for key, item in response["headers"].items()
        }
        shown["body"] = self.mask(response["body"])
        shown["url"] = self.mask(response["url"])
        return shown

    def _redact_error_messages(
        self, value: Any, event_name: str
    ) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise HistoryFormatError(
                f"{event_name}.unhandled_errors must be an array for redaction"
            )
        redacted: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict) or set(item) - _ERROR_RECORD_FIELDS:
                raise HistoryFormatError(
                    f"{event_name}.unhandled_errors has an unclassified record"
                )
            shown = dict(item)
            if "message" in shown:
                shown["message"] = self.mask(shown["message"])
            redacted.append(shown)
        return redacted


# --------------------------------------------------------------------------
# Run log location + retention (FR-701)

RUNS_DIRNAME = Path(".napflow") / "runs"
_RUN_ID_FILENAME_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
_ACTIVE_SUFFIX = ".active"
_INCOMPLETE_SUFFIX = ".incomplete"
_COMPLETE_SUFFIX = ".complete.json"
_DELETING_SUFFIX = ".deleting"
_READER_SUFFIX_PREFIX = ".reader-"
_ORDER_FILENAME = ".history-order.json"
_LOCK_FILENAME = ".history.lock"
_UNIT_FILE_SUFFIXES = (
    ".report.json",
    ".junit.xml",
    _COMPLETE_SUFFIX,
    _ACTIVE_SUFFIX,
    _INCOMPLETE_SUFFIX,
)
_UNIT_DIRECTORY_SUFFIXES = (".blobs", ".index")


@dataclass(frozen=True)
class RunHistoryUnit:
    """Lifecycle companions for one persisted run.

    ``active`` protects the JSONL while execution/report finalization is in
    progress across CLI/server processes. ``complete`` publishes sortable
    chronology only after the final record and report companions are durable.
    """

    run_id: str
    log_path: Path
    active_path: Path
    incomplete_path: Path
    complete_path: Path
    started_ns: int
    order: int


def _companion(log_path: Path, suffix: str) -> Path:
    return log_path.with_name(f"{log_path.stem}{suffix}")


@contextmanager
def _history_directory_lock(runs_dir: Path) -> Iterator[None]:
    """Serialize history mutation without following a planted lock symlink."""
    lock_path = runs_dir / _LOCK_FILENAME
    flags = os.O_RDWR
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(lock_path, flags)
    except FileNotFoundError:
        try:
            # O_EXCL makes a raced symlink/file fail without following it,
            # including on Windows where O_NOFOLLOW is usually unavailable.
            fd = os.open(lock_path, flags | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError as error:
            raise OSError(f"history lock creation failed: {lock_path}") from error
    except OSError as error:
        raise OSError(f"history lock open failed: {lock_path}") from error
    fd_open = True
    try:
        opened = os.fstat(fd)
        current = lock_path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise OSError(f"history lock changed during open: {lock_path}")
        lock = os.fdopen(fd, "r+b")
        fd_open = False
        with lock:
            if os.name == "nt":
                import msvcrt

                lock.seek(0, 2)
                if lock.tell() == 0:
                    lock.write(b"\0")
                    lock.flush()
                    os.fsync(lock.fileno())
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    finally:
        if fd_open:
            os.close(fd)


def _write_exclusive_marker(path: Path, payload: dict[str, Any]) -> None:
    """Create one fsynced marker, removing only our partial on error."""
    marker = None
    try:
        marker = path.open("x", encoding="utf-8", newline="\n")
        with marker:
            marker.write(json.dumps(payload, separators=(",", ":")) + "\n")
            marker.flush()
            os.fsync(marker.fileno())
    except BaseException:
        if marker is not None:
            with suppress(Exception):
                marker.close()
            path.unlink(missing_ok=True)
        raise


def begin_history_reader(log_path: Path) -> Path:
    """Publish a cross-process lease that excludes this unit from retention."""
    run_id = log_path.stem
    if _RUN_ID_FILENAME_RE.fullmatch(run_id) is None:
        raise ValueError(f"invalid run id for history reader: {run_id!r}")
    with _history_directory_lock(log_path.parent):
        try:
            if not stat.S_ISREG(log_path.lstat().st_mode):
                raise FileNotFoundError(log_path)
        except OSError as error:
            raise FileNotFoundError(log_path) from error
        if _lexists(_companion(log_path, _DELETING_SUFFIX)):
            raise FileNotFoundError(log_path)
        for _ in range(10):
            lease = log_path.with_name(
                f"{run_id}{_READER_SUFFIX_PREFIX}{os.getpid()}-{_secrets.token_hex(6)}"
            )
            try:
                _write_exclusive_marker(
                    lease,
                    {"version": 1, "run_id": run_id, "pid": os.getpid()},
                )
            except FileExistsError:
                continue
            return lease
    raise FileExistsError(f"could not allocate history reader lease for {run_id}")


def end_history_reader(lease: Path) -> None:
    lease.unlink(missing_ok=True)


@contextmanager
def history_reader(log_path: Path) -> Iterator[None]:
    lease = begin_history_reader(log_path)
    try:
        yield
    finally:
        end_history_reader(lease)


def _history_order(path: Path) -> int | None:
    try:
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode):
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    order = value.get("order") if isinstance(value, dict) else None
    return order if type(order) is int and order > 0 else None


def _next_history_order(runs_dir: Path) -> int:
    """Allocate a durable per-flow creation order immune to wall-clock jumps."""
    with _history_directory_lock(runs_dir):
        counter_path = runs_dir / _ORDER_FILENAME
        counter_order = _history_order(counter_path) or 0
        marker_order = max(
            (
                order
                for suffix in (_ACTIVE_SUFFIX, _INCOMPLETE_SUFFIX, _COMPLETE_SUFFIX)
                for marker in runs_dir.glob(f"*{suffix}")
                if (order := _history_order(marker)) is not None
            ),
            default=0,
        )
        order = max(counter_order, marker_order) + 1
        atomic_write_text(
            counter_path,
            json.dumps({"version": 1, "order": order}, separators=(",", ":")) + "\n",
        )
        return order


def begin_run_history(log_path: Path, run_id: str) -> RunHistoryUnit:
    """Create the exclusive active marker before any event can be emitted."""
    started_ns = time.time_ns()
    order = _next_history_order(log_path.parent)
    unit = RunHistoryUnit(
        run_id=run_id,
        log_path=log_path,
        active_path=_companion(log_path, _ACTIVE_SUFFIX),
        incomplete_path=_companion(log_path, _INCOMPLETE_SUFFIX),
        complete_path=_companion(log_path, _COMPLETE_SUFFIX),
        started_ns=started_ns,
        order=order,
    )
    payload = {
        "version": 1,
        "run_id": run_id,
        "started_ns": started_ns,
        "order": order,
    }
    _write_exclusive_marker(unit.active_path, payload)
    return unit


def abandon_run_history(unit: RunHistoryUnit) -> None:
    """Mark a known-closed prefix incomplete without making it retainable."""
    with suppress(FileNotFoundError):
        unit.active_path.replace(unit.incomplete_path)


def complete_run_history(unit: RunHistoryUnit, history: int) -> list[Path]:
    """Publish completion metadata, release protection, then retain units."""
    final = last_jsonl_record(unit.log_path)
    if (
        final is None
        or final.get("event") != "run_finished"
        or final.get("run_id") != unit.run_id
    ):
        abandon_run_history(unit)
        return []
    metadata = {
        "version": 1,
        "run_id": unit.run_id,
        "started_ns": unit.started_ns,
        "completed_ns": time.time_ns(),
        "order": unit.order,
    }
    atomic_write_text(
        unit.complete_path,
        json.dumps(metadata, separators=(",", ":")) + "\n",
    )
    unit.active_path.unlink(missing_ok=True)
    unit.incomplete_path.unlink(missing_ok=True)
    return apply_retention(unit.log_path.parent, history)


def new_run_id(now: datetime | None = None) -> str:
    """Timestamp-prefixed, Windows-safe id: `YYYYmmdd-HHMMSS-xxxxxx`.

    The random suffix prevents collisions; internal lifecycle metadata carries
    exact same-second chronology for retention and presentation.
    """
    now = now if now is not None else datetime.now(UTC)
    return f"{now:%Y%m%d-%H%M%S}-{_secrets.token_hex(3)}"


def run_log_path(workspace_root: Path, flow_identity: str, run_id: str) -> Path:
    """`.napflow/runs/<flow>/<run-id>.jsonl` — <flow> is the
    workspace-relative identity, so nested flows nest here too. This
    compatibility helper delegates to the central workspace boundary."""
    return WorkspaceResolver(workspace_root).run_log(flow_identity, run_id)


def apply_retention(runs_dir: Path, history: int) -> list[Path]:
    """Retain newest completed run units; active/incomplete runs do not count.

    New runs use an internal, locked monotonic creation order.
    Markerless legacy runs remain eligible when their robust last record is
    ``run_finished`` and use JSONL mtime. Exact companions are claimed and
    removed together; the JSONL is deleted last, and interrupted deletions
    resume from their tombstones on the next pass.
    """
    claimed: list[Path] = []
    with _history_directory_lock(runs_dir):
        deleted = _resume_deletions(runs_dir)
        completed: list[tuple[tuple[int, int, int, str], Path]] = []
        for log in runs_dir.glob("*.jsonl"):
            try:
                mode = log.lstat().st_mode
            except OSError:
                continue
            if not stat.S_ISREG(mode):
                continue
            run_id = log.stem
            if _RUN_ID_FILENAME_RE.fullmatch(run_id) is None:
                continue
            if (
                _lexists(_companion(log, _ACTIVE_SUFFIX))
                or _lexists(_companion(log, _INCOMPLETE_SUFFIX))
                or _has_history_reader(log)
            ):
                continue
            final = last_jsonl_record(log)
            if (
                final is None
                or final.get("event") != "run_finished"
                or final.get("run_id") != run_id
            ):
                continue
            complete_path = _companion(log, _COMPLETE_SUFFIX)
            metadata = _completion_metadata(log)
            if metadata is not None:
                chronology = (
                    1,
                    metadata["order"],
                    metadata["completed_ns"],
                    run_id,
                )
            elif _lexists(complete_path):
                continue  # malformed internal metadata is protected, never guessed
            else:
                try:
                    modified_ns = log.lstat().st_mtime_ns
                except OSError:
                    continue
                chronology = (0, modified_ns, modified_ns, run_id)
            completed.append((chronology, log))

        completed.sort(key=lambda entry: entry[0])
        excess = completed[:-history] if len(completed) > history else []
        for _, log in excess:
            if _claim_deletion(log):
                claimed.append(log)

    for log in claimed:
        try:
            _delete_claimed_unit(log)
        except OSError:
            continue
        deleted.append(log)
    return deleted


def _lexists(path: Path) -> bool:
    try:
        path.lstat()
    except OSError:
        return False
    return True


def _completion_metadata(log: Path) -> dict[str, int] | None:
    path = _companion(log, _COMPLETE_SUFFIX)
    try:
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode):
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        not isinstance(value, dict)
        or value.get("version") != 1
        or value.get("run_id") != log.stem
        or type(value.get("started_ns")) is not int
        or type(value.get("completed_ns")) is not int
        or type(value.get("order")) is not int
        or value["order"] <= 0
    ):
        return None
    return {
        "started_ns": value["started_ns"],
        "completed_ns": value["completed_ns"],
        "order": value["order"],
    }


def run_history_sort_key(log: Path) -> tuple[int, int, int, str]:
    """Chronological key for history lists and retention presentation."""
    metadata = _completion_metadata(log)
    if metadata is not None:
        return 1, metadata["order"], metadata["completed_ns"], log.stem
    for suffix in (_ACTIVE_SUFFIX, _INCOMPLETE_SUFFIX):
        path = _companion(log, suffix)
        try:
            mode = path.lstat().st_mode
            if not stat.S_ISREG(mode):
                continue
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (
            isinstance(value, dict)
            and value.get("version") == 1
            and value.get("run_id") == log.stem
            and type(value.get("started_ns")) is int
            and type(value.get("order")) is int
            and value["order"] > 0
        ):
            return 1, value["order"], value["started_ns"], log.stem
    try:
        modified_ns = log.lstat().st_mtime_ns
    except OSError:
        modified_ns = 0
    return 0, modified_ns, modified_ns, log.stem


def _claim_deletion(log: Path) -> bool:
    claim = _companion(log, _DELETING_SUFFIX)
    try:
        _write_exclusive_marker(
            claim,
            {"version": 1, "run_id": log.stem},
        )
    except FileExistsError:
        return False
    except OSError:
        return False
    return True


def _has_history_reader(log: Path) -> bool:
    return any(
        _lexists(path)
        for path in log.parent.glob(f"{log.stem}{_READER_SUFFIX_PREFIX}*")
    )


def _remove_owned_path(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if stat.S_ISDIR(mode):
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _delete_claimed_unit(log: Path) -> None:
    run_id = log.stem
    for suffix in (*_UNIT_FILE_SUFFIXES, *_UNIT_DIRECTORY_SUFFIXES):
        _remove_owned_path(log.with_name(f"{run_id}{suffix}"))
    _remove_owned_path(log)  # canonical source disappears last
    _remove_owned_path(log.with_name(f"{run_id}{_DELETING_SUFFIX}"))


def _resume_deletions(runs_dir: Path) -> list[Path]:
    deleted: list[Path] = []
    for claim in runs_dir.glob(f"*{_DELETING_SUFFIX}"):
        try:
            if not stat.S_ISREG(claim.lstat().st_mode):
                continue
        except OSError:
            continue
        run_id = claim.name.removesuffix(_DELETING_SUFFIX)
        if _RUN_ID_FILENAME_RE.fullmatch(run_id) is None:
            continue
        log = runs_dir / f"{run_id}.jsonl"
        if (
            _lexists(_companion(log, _ACTIVE_SUFFIX))
            or _lexists(_companion(log, _INCOMPLETE_SUFFIX))
            or _has_history_reader(log)
        ):
            claim.unlink(missing_ok=True)
            continue
        if _lexists(log):
            final = last_jsonl_record(log)
            if (
                final is None
                or final.get("event") != "run_finished"
                or final.get("run_id") != run_id
            ):
                claim.unlink(missing_ok=True)
                continue
        try:
            _delete_claimed_unit(log)
        except OSError:
            continue
        deleted.append(log)
    return deleted


# --------------------------------------------------------------------------
# Sinks + stream


def encode_record(record: dict[str, Any]) -> str:
    """THE record wire encoding — compact JSON, `ensure_ascii=False`.
    JSONL lines and WebSocket frames both use it, so they are identical
    by construction (D13: replay = re-read file)."""
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


_JSONL_REVERSE_BLOCK_BYTES = 64 * 1024


def _iter_jsonl_lines_reverse(
    file: Any, *, block_bytes: int = _JSONL_REVERSE_BLOCK_BYTES
) -> Iterator[bytes]:
    """Yield lines from a seekable binary file, newest first.

    Reads fixed-size blocks from the end and retains only the fragments for
    the line crossing the current block boundary. A single record may exceed
    ``block_bytes``; its fragments are joined only after its preceding newline
    is found, avoiding both a fixed tail window and whole-file materialization.
    """
    file.seek(0, 2)
    position = file.tell()
    fragments: list[bytes] = []
    while position:
        read_size = min(block_bytes, position)
        position -= read_size
        file.seek(position)
        parts = file.read(read_size).split(b"\n")
        if len(parts) == 1:
            fragments.append(parts[0])
            continue

        yield parts[-1] + b"".join(reversed(fragments))
        yield from reversed(parts[1:-1])
        fragments = [parts[0]]

    if fragments:
        yield b"".join(reversed(fragments))


def last_jsonl_record(path: Path) -> dict[str, Any] | None:
    """Return the last valid JSON-object record in ``path``.

    Blank, malformed, partial, and non-object trailing lines are skipped, so
    an interrupted append does not hide the preceding durable event (EC20).
    The scan works backward in bounded blocks and grows only to accommodate a
    single record, regardless of that record's line size.
    """
    try:
        with path.open("rb") as file:
            for line in _iter_jsonl_lines_reverse(file):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except (UnicodeError, ValueError):
                    continue
                if isinstance(record, dict):
                    return record
    except OSError:
        return None
    return None


def _ensure_history_directory(path: Path) -> None:
    """Create missing components with ordinary inherited OS permissions.

    The component walk retains no-follow/non-directory checks without
    imposing a napflow-specific owner, DACL, or POSIX mode contract.
    """
    missing: list[Path] = []
    current = path
    while True:
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            missing.append(current)
            parent = current.parent
            if parent == current:
                raise FileNotFoundError(f"no existing parent for {path}") from None
            current = parent
            continue
        if not stat.S_ISDIR(mode):
            raise NotADirectoryError(current)
        break

    for directory in reversed(missing):
        with suppress(FileExistsError):
            directory.mkdir()
        if not stat.S_ISDIR(directory.lstat().st_mode):
            raise NotADirectoryError(directory)


class JsonlSink:
    """Append-only run log, one compact JSON object per line (UTF-8,
    LF, `ensure_ascii=False`). Every line is flushed as written, so an
    abort leaves a valid replayable prefix — a dangling
    `request_started` is tolerated by replay (EC20)."""

    def __init__(self, path: Path):
        _ensure_history_directory(path.parent)
        self.path = path
        # Canonical v0.2 history is created exclusively and keeps the ordinary
        # permission policy inherited from the user's OS/workspace (D39).
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_BINARY", 0)
        fd = os.open(path, flags, 0o666)
        try:
            self._file = os.fdopen(fd, "w", encoding="utf-8", newline="\n")
        except BaseException:
            with suppress(OSError):
                os.close(fd)
            with suppress(OSError):
                path.unlink(missing_ok=True)
            raise

    def write(self, record: dict[str, Any]) -> None:
        self._file.write(encode_record(record) + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()


class EventStream:
    """Stamps common fields and fans out canonical/presentation records.

    A sink is anything with `write(record: dict)` and `close()`. Canonical
    sinks (JSONL and local WebSocket) receive the same persisted record;
    presentation sinks receive one schema-aware redacted logical copy. With
    no content store, the stream is featureless and canonical sinks receive
    the complete logical values unchanged.

    `FlowRun.execute()` owns the stream once execution starts and closes
    it on every exit path. Close side effects are deliberately idempotent so
    CLI/server adapters can also close a stream whose run never started;
    the first close failure is remembered and re-raised without retrying the
    sinks, allowing adapters to publish history as incomplete.
    """

    def __init__(
        self,
        run_id: str,
        masker: SecretMasker,
        sinks: Iterable[Any],
        clock: Callable[[], datetime] | None = None,
        *,
        presentation_sinks: Iterable[Any] = (),
        content_store: RunContentStore | None = None,
    ):
        self.run_id = run_id
        self._masker = masker
        self._sinks = list(sinks)
        self._presentation_sinks = list(presentation_sinks)
        self._content_store = content_store
        self._active_features = (
            list(HISTORY_WRITE_FEATURES) if content_store is not None else []
        )
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        self._seq = 0
        self._closed = False
        self._close_error: BaseException | None = None

    def emit(self, event: Event) -> dict[str, Any]:
        """Serialize and fan out; return the canonical wire record."""
        self._seq += 1
        data = _asdict(event)
        record: dict[str, Any] = {"event": type(event).event, "run_id": self.run_id}
        for common in ("frame", "node"):
            value = data.pop(common)
            if value is not None:
                record[common] = value
        record["ts"] = isoformat_ms(self._clock())
        record["seq"] = self._seq
        # `format`/`features` are part of the run_started envelope.
        if isinstance(event, RunStarted):
            record["format"] = data.pop("format")
            data.pop("features")
            record["features"] = list(self._active_features)
        omittable = _omit_if_none(type(event))
        for key, value in data.items():
            if value is None and key in omittable:
                continue
            record[key] = value
        # Redaction sees complete logical values. Persistence then happens
        # exactly once before the same descriptor-bearing record fans out to
        # JSONL and live canonical sinks; a blob is durable before persist()
        # can return its descriptor.
        redacted = (
            self._masker.redact_record(record) if self._presentation_sinks else None
        )
        canonical = (
            persist_record_content(record, self._content_store)
            if self._content_store is not None
            else record
        )
        for sink in self._sinks:
            sink.write(canonical)
        if redacted is not None:
            for sink in self._presentation_sinks:
                sink.write(redacted)
        return canonical

    def close(self) -> None:
        if self._closed:
            if self._close_error is not None:
                raise self._close_error
            return
        self._closed = True
        errors: list[BaseException] = []
        for sink in [*self._sinks, *self._presentation_sinks]:
            try:
                sink.close()
            except BaseException as error:
                errors.append(error)
        if errors:
            self._close_error = errors[0]
            raise self._close_error


def isoformat_ms(dt: datetime) -> str:
    """UTC, millisecond precision, `Z` suffix — the Message meta format
    (EN §1): `2026-06-11T10:00:00.123Z`."""
    dt = dt.astimezone(UTC)
    return f"{dt:%Y-%m-%dT%H:%M:%S}.{dt.microsecond // 1000:03d}Z"
