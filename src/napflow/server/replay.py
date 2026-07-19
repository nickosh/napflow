"""Bounded replay reads and disposable browser view projections."""

import json
import stat
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from itertools import chain
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from blacksheep import Request

from napflow.core.events import (
    HistoryFormatError,
    last_jsonl_record,
    validate_history_envelope,
)

REPLAY_API_FORMAT = "napflow-replay/1"
REPLAY_DEFAULT_LIMIT = 200
REPLAY_MAX_LIMIT = 500
REPLAY_ROOT_FRAME = "f-0"
REPLAY_LOG_RING = 50
MAX_REPLAY_SEQUENCE = (1 << 63) - 1
RUN_ENVELOPE_EVENTS = frozenset({"run_started", "run_finished"})


class ReplayQueryError(ValueError):
    """A replay query cannot be interpreted without ambiguity."""


@dataclass(frozen=True)
class ReplayMetadata:
    run_format: str | None
    features: list[str]
    root_frame: str


@dataclass(frozen=True)
class ReplaySnapshot:
    history_state: str
    run_summary: dict[str, Any] | None
    through_seq: int
    allow_empty: bool


class ReplayViewBuilder:
    """Fold a complete selected frame into bounded browser overlay state.

    This is a disposable projection over canonical JSONL, not a second source
    of truth.  It is bounded by the flow's nodes/edges/ports plus a fixed log
    ring per node, while event rows remain independently page-sized.
    """

    def __init__(self, scope_frame: str) -> None:
        self.scope_frame = scope_frame
        self.record_count = 0
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: dict[str, dict[str, int]] = {}
        self.asserts = {"passed": 0, "failed": 0}
        self.started_ts: str | None = None

    @staticmethod
    def _fresh_node() -> dict[str, Any]:
        return {
            "firings": 0,
            "active": False,
            "outcome": "none",
            "guard": None,
            "lastSeq": -1,
            "log": None,
            "ports": {},
            "request": None,
        }

    def _node(self, node: str) -> dict[str, Any]:
        return self.nodes.setdefault(node, self._fresh_node())

    def _touch(self, node: str, seq: int, **patch: Any) -> None:
        state = self._node(node)
        state.update(patch)
        state["lastSeq"] = seq

    @staticmethod
    def _message_value(record: dict[str, Any]) -> Any:
        return record["value"] if "value" in record else record.get("value_preview")

    @staticmethod
    def _traffic(ports: dict[str, Any], key: str, value: Any, ts: str | None) -> None:
        previous = ports.get(key)
        ports[key] = {
            "count": (previous.get("count", 0) if isinstance(previous, dict) else 0)
            + 1,
            "lastValue": value,
            "lastTs": ts,
        }

    def apply(self, record: dict[str, Any]) -> None:
        self.record_count += 1
        seq = record["seq"]
        event = record.get("event")

        if event == "run_started":
            self.started_ts = record.get("ts")
            return
        if event == "run_finished":
            assertions = record.get("asserts")
            if isinstance(assertions, dict):
                self.asserts = {
                    "passed": assertions.get("passed", self.asserts["passed"]),
                    "failed": assertions.get("failed", self.asserts["failed"]),
                }
            never_fired = record.get("nodes_never_fired")
            if isinstance(never_fired, list):
                for node in never_fired:
                    if isinstance(node, str):
                        self._touch(node, seq, outcome="skipped")
            for state in self.nodes.values():
                if state["active"]:
                    state["active"] = False
            return

        if record.get("frame") != self.scope_frame:
            return

        node = record.get("node")
        if event == "node_fired" and isinstance(node, str):
            state = self._node(node)
            self._touch(
                node,
                seq,
                firings=state["firings"] + 1,
                active=True,
            )
        elif event == "request_started" and isinstance(node, str):
            self._touch(
                node,
                seq,
                active=True,
                request={
                    "method": record.get("method"),
                    "url": record.get("url"),
                    "status": None,
                    "sizeBytes": None,
                    "totalMs": None,
                    "attempt": record.get("attempt", 1),
                    "error": None,
                },
            )
        elif event == "request_finished" and isinstance(node, str):
            previous = self._node(node).get("request")
            previous = previous if isinstance(previous, dict) else {}
            timing = record.get("timing")
            timing = timing if isinstance(timing, dict) else {}
            self._touch(
                node,
                seq,
                outcome="ok",
                request={
                    "method": previous.get("method"),
                    "url": previous.get("url"),
                    "status": record.get("status"),
                    "sizeBytes": record.get("size_bytes"),
                    "totalMs": timing.get("total_ms"),
                    "attempt": record.get("attempt", previous.get("attempt", 1)),
                    "error": None,
                },
            )
        elif event == "request_failed" and isinstance(node, str):
            previous = self._node(node).get("request")
            previous = previous if isinstance(previous, dict) else {}
            request = {
                "method": previous.get("method"),
                "url": previous.get("url"),
                "status": None,
                "sizeBytes": None,
                "totalMs": None,
                "attempt": record.get("attempt", previous.get("attempt", 1)),
                "error": f"{record.get('error_kind')}: {record.get('message')}",
            }
            patch: dict[str, Any] = {"request": request}
            if record.get("will_retry") is not True:
                patch["outcome"] = "error"
            self._touch(node, seq, **patch)
        elif event == "message_emitted":
            from_port = str(record.get("from_port", ""))
            to_node = record.get("to_node")
            to_port = record.get("to_port")
            edge = f"{from_port}→{to_node}.{to_port}"
            previous_edge = self.edges.get(edge)
            self.edges[edge] = {
                "count": (
                    previous_edge.get("count", 0)
                    if isinstance(previous_edge, dict)
                    else 0
                )
                + 1,
                "lastSeq": seq,
            }
            value = self._message_value(record)
            ts = record.get("ts")
            if isinstance(node, str):
                state = self._node(node)
                port = from_port.partition(".")[2]
                outcome = (
                    "error"
                    if port == "error"
                    else state["outcome"]
                    if state["outcome"] in {"failed", "error"}
                    else "ok"
                )
                self._traffic(state["ports"], f"out:{port}", value, ts)
                self._touch(node, seq, active=False, outcome=outcome)
            if isinstance(to_node, str) and isinstance(to_port, str):
                state = self._node(to_node)
                self._traffic(state["ports"], f"in:{to_port}", value, ts)
        elif event == "assert_result":
            passed = record.get("passed") is True
            self.asserts = {
                "passed": self.asserts["passed"] + int(passed),
                "failed": self.asserts["failed"] + int(not passed),
            }
            if isinstance(node, str):
                previous = self._node(node)["outcome"]
                self._touch(
                    node,
                    seq,
                    outcome=("failed" if not passed or previous == "failed" else "ok"),
                )
        elif event == "python_error" and isinstance(node, str):
            self._touch(node, seq, outcome="error", active=False)
        elif event == "log" and isinstance(node, str):
            state = self._node(node)
            previous = state.get("log")
            previous = previous if isinstance(previous, dict) else {}
            ring = list(previous.get("ring", []))
            ring.append(record.get("value"))
            self._touch(
                node,
                seq,
                log={
                    "ring": ring[-REPLAY_LOG_RING:],
                    "count": previous.get("count", 0) + 1,
                },
            )
        elif event == "guard_tripped" and isinstance(node, str):
            self._touch(node, seq, guard=record.get("port"))

    def payload(self) -> dict[str, Any]:
        return {
            "scope_frame": self.scope_frame,
            "record_count": self.record_count,
            "nodes": self.nodes,
            "edges": self.edges,
            "asserts": self.asserts,
            "started_ts": self.started_ts,
        }


def iter_records(
    path: Path,
    *,
    validate_history: bool = True,
    allow_empty: bool = False,
    through_seq: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream a JSONL prefix; a trailing partial line is tolerated (EC20).

    The envelope is validated by default before later records are interpreted.
    `allow_empty` is only for the brief live-run prefix before run_started has
    flushed; an empty completed/imported history is invalid. ``through_seq``
    freezes live replay at the atomically captured subscription boundary.
    """
    seen = False
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except ValueError as e:
                    if validate_history and not seen:
                        raise HistoryFormatError(
                            "run history does not begin with a valid JSON envelope"
                        ) from e
                    if any(later.strip() for later in f):
                        raise HistoryFormatError(
                            "run history contains malformed JSON before a later record"
                        ) from e
                    break  # one malformed/partial final line is an EC20 prefix
                if (
                    through_seq is not None
                    and type(record.get("seq")) is int
                    and record["seq"] > through_seq
                ):
                    break
                if validate_history and not seen:
                    validate_history_envelope(record)
                seen = True
                yield record
    except UnicodeError as e:
        raise HistoryFormatError("run history must contain valid UTF-8") from e
    if validate_history and not seen and not allow_empty:
        raise HistoryFormatError("run history is empty; no run_started envelope")


def read_records(
    path: Path, *, validate_history: bool = True, allow_empty: bool = False
) -> list[dict[str, Any]]:
    """Compatibility helper retained for focused stream/WS tests."""
    return list(
        iter_records(
            path,
            validate_history=validate_history,
            allow_empty=allow_empty,
        )
    )


def iter_replay_records(
    path: Path, *, allow_empty: bool, through_seq: int
) -> Iterator[dict[str, Any]]:
    """Validate the sequence contract needed by cursor-based REST replay."""
    expected_seq = 1
    for record in iter_records(path, allow_empty=allow_empty, through_seq=through_seq):
        seq = record.get("seq")
        if type(seq) is not int or seq != expected_seq:
            raise HistoryFormatError(
                "run history sequence must contain consecutive positive integers; "
                f"expected {expected_seq}, found {seq!r}"
            )
        expected_seq += 1
        yield record


def query_value(request: Request, name: str) -> str | None:
    """Return one query value, rejecting duplicate/empty ambiguity upstream."""
    raw_query = request.url.query
    try:
        query = parse_qs(
            raw_query.decode("utf-8") if isinstance(raw_query, bytes) else raw_query,
            keep_blank_values=True,
            errors="strict",
        )
    except (UnicodeDecodeError, UnicodeEncodeError, ValueError) as error:
        raise ReplayQueryError("query string must be valid UTF-8") from error
    values = query.get(name)
    if values is None:
        return None
    if len(values) != 1 or not isinstance(values[0], str):
        raise ReplayQueryError(f"{name} must be provided at most once")
    return values[0]


def parse_replay_integer(
    value: str | None,
    *,
    name: str,
    default: int | None,
    minimum: int,
    maximum: int,
) -> int:
    if value is None:
        if default is None:  # pragma: no cover - every current caller defaults
            raise ReplayQueryError(f"{name} is required")
        return default
    if (
        not value
        or len(value) > 19
        or any(character < "0" or character > "9" for character in value)
    ):
        raise ReplayQueryError(f"{name} must be an integer")
    parsed = int(value)
    if not minimum <= parsed <= maximum:
        raise ReplayQueryError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def parse_replay_page_query(request: Request) -> tuple[int, int]:
    after_seq = parse_replay_integer(
        query_value(request, "after_seq"),
        name="after_seq",
        default=0,
        minimum=0,
        maximum=MAX_REPLAY_SEQUENCE,
    )
    limit = parse_replay_integer(
        query_value(request, "limit"),
        name="limit",
        default=REPLAY_DEFAULT_LIMIT,
        minimum=1,
        maximum=REPLAY_MAX_LIMIT,
    )
    return after_seq, limit


def parse_frame_query(request: Request, name: str) -> str | None:
    value = query_value(request, name)
    if value is None:
        return None
    if (
        not value
        or len(value) > 4096
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise ReplayQueryError(f"{name} must be a non-empty frame id")
    return value


def metadata_from_header(header: dict[str, Any] | None) -> ReplayMetadata:
    if header is None:
        return ReplayMetadata(None, [], REPLAY_ROOT_FRAME)
    declared_root = header.get("frame")
    root_frame = (
        declared_root
        if isinstance(declared_root, str) and declared_root
        else REPLAY_ROOT_FRAME
    )
    return ReplayMetadata(
        run_format=header.get("format"),
        features=list(header.get("features", [])),
        root_frame=root_frame,
    )


def read_replay_page(
    path: Path,
    *,
    after_seq: int,
    limit: int,
    allow_empty: bool,
    through_seq: int,
    matches: Callable[[dict[str, Any], ReplayMetadata], bool],
    on_record: Callable[[dict[str, Any], ReplayMetadata], None] | None = None,
) -> tuple[ReplayMetadata, list[dict[str, Any]], bool]:
    """Read at most one bounded page plus one matching lookahead record."""
    iterator = iter_replay_records(
        path, allow_empty=allow_empty, through_seq=through_seq
    )
    try:
        try:
            header = next(iterator)
        except StopIteration:
            return metadata_from_header(None), [], False

        metadata = metadata_from_header(header)
        selected: list[dict[str, Any]] = []
        has_more = False
        for record in chain((header,), iterator):
            if on_record is not None:
                on_record(record, metadata)
            seq = record.get("seq")
            if (
                type(seq) is not int
                or seq <= after_seq
                or not matches(record, metadata)
            ):
                continue
            if len(selected) == limit:
                has_more = True
            else:
                selected.append(record)
        return metadata, selected, has_more
    finally:
        iterator.close()


def read_replay_event(
    path: Path,
    *,
    seq: int,
    allow_empty: bool,
    through_seq: int,
) -> tuple[ReplayMetadata, dict[str, Any] | None]:
    """Read one canonical record by sequence without retaining its prefix."""
    iterator = iter_replay_records(
        path, allow_empty=allow_empty, through_seq=through_seq
    )
    try:
        try:
            header = next(iterator)
        except StopIteration:
            return metadata_from_header(None), None
        metadata = metadata_from_header(header)
        selected: dict[str, Any] | None = None
        for record in chain((header,), iterator):
            if type(record.get("seq")) is int and record["seq"] == seq:
                selected = record
        return metadata, selected
    finally:
        iterator.close()


def replay_envelope(
    run_id: str,
    metadata: ReplayMetadata,
    history_state: str,
    run_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "api_format": REPLAY_API_FORMAT,
        "run_id": run_id,
        "run_format": metadata.run_format,
        "features": metadata.features,
        "root_frame": metadata.root_frame,
        "history_state": history_state,
        "run_summary": run_summary,
    }


def replay_run_summary(record: dict[str, Any]) -> dict[str, Any]:
    """Project the durable final fact into a bounded browser summary.

    End outputs and error bodies stay exclusively in the canonical event (and
    its lazy content store).  The replay envelope needs only enough scalar
    state to settle a first page that deliberately does not fetch the tail.
    """
    errors = record.get("unhandled_errors")
    never_fired = record.get("nodes_never_fired")
    summary: dict[str, Any] = {
        "state": record.get("state", "error"),
        "duration_ms": record.get("duration_ms"),
        "asserts": record.get("asserts", {"passed": 0, "failed": 0}),
        "unhandled_error_count": len(errors) if isinstance(errors, list) else 0,
        "nodes_never_fired_count": (
            len(never_fired) if isinstance(never_fired, list) else 0
        ),
    }
    if record.get("error_reason") is not None:
        summary["error_reason"] = record["error_reason"]
    return summary


def replay_frame_summary(record: dict[str, Any]) -> dict[str, Any]:
    """Project one canonical frame completion into bounded navigation data."""
    errors = record.get("unhandled_errors")
    outputs = record.get("end_outputs")
    return {
        key: record.get(key)
        for key in (
            "event",
            "run_id",
            "ts",
            "seq",
            "frame",
            "parent_frame",
            "parent_node",
            "flow",
            "kind",
            "loop_index",
            "duration_ms",
            "state",
            "asserts",
        )
    } | {
        "unhandled_error_count": len(errors) if isinstance(errors, list) else 0,
        "end_output_names": sorted(outputs) if isinstance(outputs, dict) else [],
    }


def capture_replay_snapshot(run: Any | None, log_path: Path) -> ReplaySnapshot:
    """Capture one sequence/lifecycle boundary before a replay scan.

    Writers do not take reader leases.  Freezing at the last valid sequence
    prevents a concurrent append from producing an older page/projection with
    a newer completion classification.
    """
    tail = last_jsonl_record(log_path)
    seq = tail.get("seq") if tail is not None else None
    through_seq = seq if type(seq) is int and seq > 0 else 0
    if run is not None and not run.finished:
        return ReplaySnapshot("running", None, through_seq, True)
    if tail and tail.get("event") == "run_finished":
        return ReplaySnapshot("complete", replay_run_summary(tail), through_seq, False)
    external_active = has_external_active_marker(log_path)
    state = "indeterminate" if external_active else "incomplete"
    return ReplaySnapshot(state, None, through_seq, external_active)


def replay_history_state(run: Any | None, log_path: Path) -> str:
    """Compatibility/listing projection of the richer replay snapshot."""
    return capture_replay_snapshot(run, log_path).history_state


def has_external_active_marker(log_path: Path) -> bool:
    marker = log_path.with_name(f"{log_path.stem}.active")
    try:
        return stat.S_ISREG(marker.lstat().st_mode)
    except OSError:
        return False
