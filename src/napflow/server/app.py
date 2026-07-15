"""BlackSheep app — a THIN adapter over napflow.core (FR-1001, D03/D04).

Surface pinned in the workspace-manifest spec ("Server surface"):
REST under /api, one WebSocket per run under /ws/runs/{run_id}, static
UI bundle at / (release artifacts carry it; unsupported raw-source installs
get an explanatory placeholder page).
Everything binds to localhost only; the server trusts core for all
semantics (gate, env, masking) via core/runprep.py.
"""

import ast
import asyncio
import hashlib
import ipaddress
import json
import os
import shutil
import stat
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from itertools import chain
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from blacksheep import Application, Request, Response, WebSocket
from blacksheep.server.responses import html
from blacksheep.server.responses import json as json_response
from blacksheep.server.routing import Router
from ruamel.yaml.comments import CommentedMap

import napflow
from napflow.core.checker import (
    CheckDiagnostic,
    check_run_closure,
    diagnostics_from_load_error,
    node_surfaces,
    python_functions,
    template_refs,
    used_by,
)
from napflow.core.events import (
    HistoryFormatError,
    begin_history_reader,
    encode_record,
    last_jsonl_record,
    resolve_record_content,
    run_history_sort_key,
)
from napflow.core.events import (
    validate_history_envelope as _validate_history_envelope,
)
from napflow.core.files import atomic_write_text
from napflow.core.history_content import (
    ContentCorruptError,
    ContentMissingError,
    ContentOmittedError,
    ContentStoreError,
    RunContentStore,
)
from napflow.core.loader import (
    LoadError,
    load_document,
    load_flow,
    merge_flow_document,
    save_document,
    validate_flow_payload,
)
from napflow.core.models import EndNode, StartNode
from napflow.core.runprep import RunPrepError, prepare_run
from napflow.core.workspace import Workspace, WorkspaceBoundaryError
from napflow.server.runs import (
    SUBSCRIBER_END,
    SUBSCRIBER_RESYNC,
    RunManager,
    SubscriberLimitError,
)

STATIC_DIR = Path(__file__).parent / "static"

_PLACEHOLDER = """<!doctype html>
<html><head><title>napflow</title></head><body>
<h1>napflow server is running</h1>
<p>This installation does not contain the pre-built canvas bundle. Install
napflow from PyPI or a GitHub release artifact; direct VCS and raw-source
installs are unsupported. The API is live under <code>/api</code>.</p>
</body></html>"""

# WebSocket close codes (4xxx = application-defined)
WS_WORKSPACE_BOUNDARY = 4400
WS_REQUEST_ORIGIN = 4403
WS_UNKNOWN_RUN = 4404
WS_HISTORY_FORMAT = 4409
WS_RESYNC_REQUIRED = 4410
WS_SUBSCRIBER_LIMIT = 4411
WS_SEND_TIMEOUT_S = 5.0
WS_CLOSE_TIMEOUT_S = 1.0

REPLAY_API_FORMAT = "napflow-replay/1"
REPLAY_DEFAULT_LIMIT = 200
REPLAY_MAX_LIMIT = 500
REPLAY_ROOT_FRAME = "f-0"
REPLAY_LOG_RING = 50
_MAX_REPLAY_SEQUENCE = (1 << 63) - 1
_RUN_ENVELOPE_EVENTS = frozenset({"run_started", "run_finished"})

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class _ReplayQueryError(ValueError):
    """A replay query cannot be interpreted without ambiguity."""


@dataclass(frozen=True)
class _ReplayMetadata:
    run_format: str | None
    features: list[str]
    root_frame: str


@dataclass(frozen=True)
class _ReplaySnapshot:
    history_state: str
    run_summary: dict[str, Any] | None
    through_seq: int
    allow_empty: bool


class _ReplayViewBuilder:
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


@dataclass(frozen=True)
class _Authority:
    scheme: str
    host: str
    port: int


def _request_scheme(request: Request) -> str:
    scheme = request.scheme.lower()
    if scheme == "ws":
        return "http"
    if scheme == "wss":
        return "https"
    return scheme


def _parse_authority(value: str, scheme: str) -> _Authority | None:
    """Parse a Host authority without accepting URL-shaped surprises."""
    try:
        parsed = urlsplit(f"//{value}")
        port = parsed.port
    except ValueError:
        return None
    if (
        not value
        or value.endswith(":")
        or any(char.isspace() for char in value)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.hostname is None
    ):
        return None
    host = parsed.hostname.lower().removesuffix(".")
    if port is None:
        port = 443 if scheme == "https" else 80
    if not 1 <= port <= 65535:
        return None
    return _Authority(scheme=scheme, host=host, port=port)


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _request_authority(request: Request) -> _Authority | None:
    hosts = request.get_headers(b"host")
    if len(hosts) != 1:
        return None
    try:
        raw_host = hosts[0].decode("ascii")
    except UnicodeDecodeError:
        return None
    authority = _parse_authority(raw_host, _request_scheme(request))
    if authority is None or not _is_loopback_host(authority.host):
        return None
    return authority


def _origin_matches(request: Request, authority: _Authority) -> bool:
    origins = request.get_headers(b"origin")
    if not origins:
        # Origin is a browser boundary. Programmatic localhost clients such
        # as niquests and websockets remain supported without fabricating it.
        return True
    if len(origins) != 1:
        return False
    try:
        value = origins[0].decode("ascii")
        parsed = urlsplit(value)
        port = parsed.port
    except (UnicodeDecodeError, ValueError):
        return False
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.netloc == ""
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.hostname is None
    ):
        return False
    host = parsed.hostname.lower().removesuffix(".")
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return _Authority(parsed.scheme, host, port) == authority


class _LocalRequestBoundary:
    """Loopback Host + browser same-origin boundary (D37/FR-1108)."""

    async def __call__(
        self,
        request: Request,
        next_handler: Callable[[Request], Awaitable[Response | None]],
    ) -> Response | None:
        authority = _request_authority(request)
        is_websocket = isinstance(request, WebSocket)
        origin_required = is_websocket or request.method.upper() in _UNSAFE_METHODS
        if authority is None or (
            origin_required and not _origin_matches(request, authority)
        ):
            if is_websocket:
                await request.close(WS_REQUEST_ORIGIN, "request origin rejected")
                return None
            return json_response(
                {
                    "error": "request_origin",
                    "message": "request rejected by local server boundary",
                },
                status=403,
            )
        return await next_handler(request)


class _SourceWriteCoordinator:
    """Per-canonical-file serialization for ETag check + replacement."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def lock(self, path: Path) -> AsyncIterator[None]:
        key = os.path.normcase(str(path))
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            yield


def _diag_payload(diag: CheckDiagnostic, root: Path) -> dict[str, Any]:
    try:
        file = diag.path.relative_to(root).as_posix()
    except ValueError:
        file = str(diag.path)
    return {
        "severity": diag.severity,
        "code": diag.code,
        "message": diag.message,
        "hint": diag.hint,
        "file": file,
        "line": diag.line,
        "column": diag.column,
        "node": diag.node_id,
    }


def _prep_error(e: RunPrepError, root: Path) -> Response:
    status = 404 if e.reason == "flow_not_found" else 400
    return json_response(
        {
            "error": e.reason,
            "message": str(e),
            "diagnostics": [_diag_payload(d, root) for d in e.diagnostics],
        },
        status=status,
    )


def _etag(path: Path) -> str | None:
    """Content hash of a file, or None when it doesn't exist. Hash (not
    mtime): editors and git checkouts can rewrite identical bytes."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()[:16]


def _syntax_diagnostic(code: str) -> dict[str, Any] | None:
    """One-line syntax report for a nodes.py body (EC14 — AST only,
    never imports user code). None = parses."""
    try:
        ast.parse(code)
    except SyntaxError as e:
        return {
            "message": e.msg or "syntax error",
            "line": e.lineno,
            "column": e.offset,
        }
    return None


async def _json_object(request: Request) -> dict[str, Any] | None:
    """The request body as a JSON object, or None when it isn't one."""
    try:
        body = await request.json() or {}
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _bad_request(message: str) -> Response:
    return json_response({"error": "bad_request", "message": message}, status=400)


def _workspace_boundary(error: WorkspaceBoundaryError) -> Response:
    return json_response(
        {"error": error.reason, "message": str(error)},
        status=400,
    )


def _write_failure(path: Path, error: OSError) -> Response:
    detail = error.strerror or type(error).__name__
    return json_response(
        {
            "error": "write_failed",
            "message": f"could not save {path.name}: {detail}",
        },
        status=507,
    )


def _copy_clone_file(source: str, destination: str) -> str:
    """Copy flow sources through the same durable primitive as editor saves."""
    source_path = Path(source)
    destination_path = Path(destination)
    if source_path.name in {"flow.yaml", "nodes.py"}:
        atomic_write_text(
            destination_path,
            source_path.read_text(encoding="utf-8"),
        )
        shutil.copystat(source_path, destination_path)
        return str(destination_path)
    return shutil.copy2(source, destination)


def _flow_summary(ref: Any) -> dict[str, Any]:
    """One GET /api/flows entry — the structured `napf list` line."""
    try:
        model = load_flow(ref.file).model
    except LoadError:
        return {"identity": ref.identity, "valid": False}
    start = next((n for n in model.nodes if isinstance(n, StartNode)), None)
    end = next((n for n in model.nodes if isinstance(n, EndNode)), None)
    inputs = []
    for port in start.config.ports if start else []:
        entry: dict[str, Any] = {
            "name": port.name,
            "type": port.type,
            "required": "default" not in port.model_fields_set,
        }
        if "default" in port.model_fields_set:
            entry["default"] = port.default
        inputs.append(entry)
    outputs = [
        {"name": p.name, "required": p.required}
        for p in (end.config.ports if end else [])
    ]
    return {
        "identity": ref.identity,
        "valid": True,
        "name": model.flow.name,
        "inputs": inputs,
        "outputs": outputs,
    }


def _iter_records(
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
                    _validate_history_envelope(record)
                seen = True
                yield record
    except UnicodeError as e:
        raise HistoryFormatError("run history must contain valid UTF-8") from e
    if validate_history and not seen and not allow_empty:
        raise HistoryFormatError("run history is empty; no run_started envelope")


def _read_records(
    path: Path, *, validate_history: bool = True, allow_empty: bool = False
) -> list[dict[str, Any]]:
    """Compatibility helper retained for focused stream/WS tests."""
    return list(
        _iter_records(
            path,
            validate_history=validate_history,
            allow_empty=allow_empty,
        )
    )


def _iter_replay_records(
    path: Path, *, allow_empty: bool, through_seq: int
) -> Iterator[dict[str, Any]]:
    """Validate the sequence contract needed by cursor-based REST replay."""
    expected_seq = 1
    for record in _iter_records(path, allow_empty=allow_empty, through_seq=through_seq):
        seq = record.get("seq")
        if type(seq) is not int or seq != expected_seq:
            raise HistoryFormatError(
                "run history sequence must contain consecutive positive integers; "
                f"expected {expected_seq}, found {seq!r}"
            )
        expected_seq += 1
        yield record


def _query_value(request: Request, name: str) -> str | None:
    """Return one query value, rejecting duplicate/empty ambiguity upstream."""
    raw_query = request.url.query
    try:
        query = parse_qs(
            raw_query.decode("utf-8") if isinstance(raw_query, bytes) else raw_query,
            keep_blank_values=True,
            errors="strict",
        )
    except (UnicodeDecodeError, UnicodeEncodeError, ValueError) as error:
        raise _ReplayQueryError("query string must be valid UTF-8") from error
    values = query.get(name)
    if values is None:
        return None
    if len(values) != 1 or not isinstance(values[0], str):
        raise _ReplayQueryError(f"{name} must be provided at most once")
    return values[0]


def _parse_replay_integer(
    value: str | None,
    *,
    name: str,
    default: int | None,
    minimum: int,
    maximum: int,
) -> int:
    if value is None:
        if default is None:  # pragma: no cover - every current caller defaults
            raise _ReplayQueryError(f"{name} is required")
        return default
    if (
        not value
        or len(value) > 19
        or any(character < "0" or character > "9" for character in value)
    ):
        raise _ReplayQueryError(f"{name} must be an integer")
    parsed = int(value)
    if not minimum <= parsed <= maximum:
        raise _ReplayQueryError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def _parse_replay_page_query(request: Request) -> tuple[int, int]:
    after_seq = _parse_replay_integer(
        _query_value(request, "after_seq"),
        name="after_seq",
        default=0,
        minimum=0,
        maximum=_MAX_REPLAY_SEQUENCE,
    )
    limit = _parse_replay_integer(
        _query_value(request, "limit"),
        name="limit",
        default=REPLAY_DEFAULT_LIMIT,
        minimum=1,
        maximum=REPLAY_MAX_LIMIT,
    )
    return after_seq, limit


def _parse_frame_query(request: Request, name: str) -> str | None:
    value = _query_value(request, name)
    if value is None:
        return None
    if (
        not value
        or len(value) > 4096
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise _ReplayQueryError(f"{name} must be a non-empty frame id")
    return value


def _metadata_from_header(header: dict[str, Any] | None) -> _ReplayMetadata:
    if header is None:
        return _ReplayMetadata(None, [], REPLAY_ROOT_FRAME)
    declared_root = header.get("frame")
    root_frame = (
        declared_root
        if isinstance(declared_root, str) and declared_root
        else REPLAY_ROOT_FRAME
    )
    return _ReplayMetadata(
        run_format=header.get("format"),
        features=list(header.get("features", [])),
        root_frame=root_frame,
    )


def _read_replay_page(
    path: Path,
    *,
    after_seq: int,
    limit: int,
    allow_empty: bool,
    through_seq: int,
    matches: Callable[[dict[str, Any], _ReplayMetadata], bool],
    on_record: Callable[[dict[str, Any], _ReplayMetadata], None] | None = None,
) -> tuple[_ReplayMetadata, list[dict[str, Any]], bool]:
    """Read at most one bounded page plus one matching lookahead record."""
    iterator = _iter_replay_records(
        path, allow_empty=allow_empty, through_seq=through_seq
    )
    try:
        try:
            header = next(iterator)
        except StopIteration:
            return _metadata_from_header(None), [], False

        metadata = _metadata_from_header(header)
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


def _read_replay_event(
    path: Path,
    *,
    seq: int,
    allow_empty: bool,
    through_seq: int,
) -> tuple[_ReplayMetadata, dict[str, Any] | None]:
    """Read one canonical record by sequence without retaining its prefix."""
    iterator = _iter_replay_records(
        path, allow_empty=allow_empty, through_seq=through_seq
    )
    try:
        try:
            header = next(iterator)
        except StopIteration:
            return _metadata_from_header(None), None
        metadata = _metadata_from_header(header)
        selected: dict[str, Any] | None = None
        for record in chain((header,), iterator):
            if type(record.get("seq")) is int and record["seq"] == seq:
                selected = record
        return metadata, selected
    finally:
        iterator.close()


def _replay_envelope(
    run_id: str,
    metadata: _ReplayMetadata,
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


def _replay_run_summary(record: dict[str, Any]) -> dict[str, Any]:
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


def _replay_frame_summary(record: dict[str, Any]) -> dict[str, Any]:
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


def _capture_replay_snapshot(run: Any | None, log_path: Path) -> _ReplaySnapshot:
    """Capture one sequence/lifecycle boundary before a replay scan.

    Writers do not take reader leases.  Freezing at the last valid sequence
    prevents a concurrent append from producing an older page/projection with
    a newer completion classification.
    """
    tail = last_jsonl_record(log_path)
    seq = tail.get("seq") if tail is not None else None
    through_seq = seq if type(seq) is int and seq > 0 else 0
    if run is not None and not run.finished:
        return _ReplaySnapshot("running", None, through_seq, True)
    if tail and tail.get("event") == "run_finished":
        return _ReplaySnapshot(
            "complete", _replay_run_summary(tail), through_seq, False
        )
    external_active = _has_external_active_marker(log_path)
    state = "indeterminate" if external_active else "incomplete"
    return _ReplaySnapshot(state, None, through_seq, external_active)


def _replay_history_state(run: Any | None, log_path: Path) -> str:
    """Compatibility/listing projection of the richer replay snapshot."""
    return _capture_replay_snapshot(run, log_path).history_state


def _has_external_active_marker(log_path: Path) -> bool:
    marker = log_path.with_name(f"{log_path.stem}.active")
    try:
        return stat.S_ISREG(marker.lstat().st_mode)
    except OSError:
        return False


def _history_format_response(error: HistoryFormatError) -> Response:
    message = str(error).encode("utf-8", errors="backslashreplace").decode("utf-8")
    return json_response({"error": "history_format", "message": message}, status=422)


def _history_content_response(
    error: ContentStoreError | HistoryFormatError,
    *,
    code: str,
    status: int,
) -> Response:
    message = str(error).encode("utf-8", errors="backslashreplace").decode("utf-8")
    return json_response({"error": code, "message": message}, status=status)


def _ws_close_reason(message: str, limit: int = 120) -> str:
    """Application close reasons must fit the WebSocket control frame."""
    safe = message.encode("utf-8", errors="backslashreplace").decode("utf-8")
    encoded = safe.encode("utf-8")
    if len(encoded) <= limit:
        return safe
    return encoded[: limit - 3].decode("utf-8", errors="ignore") + "..."


class _SlowSubscriber(TimeoutError):
    pass


async def _send_ws_record(websocket: WebSocket, record: dict[str, Any]) -> None:
    try:
        await asyncio.wait_for(
            websocket.send_text(encode_record(record)),
            timeout=WS_SEND_TIMEOUT_S,
        )
    except TimeoutError as error:
        raise _SlowSubscriber from error


async def _send_history_range(
    websocket: WebSocket,
    path: Path,
    *,
    after_seq: int = 0,
    through_seq: int | None = None,
    allow_empty: bool = False,
) -> int:
    """Send one exact durable range and return its last sequence."""
    last_sent = after_seq
    for record in _iter_records(
        path,
        allow_empty=allow_empty,
        through_seq=through_seq,
    ):
        seq = record.get("seq")
        if type(seq) is int and seq <= after_seq:
            continue
        await _send_ws_record(websocket, record)
        if type(seq) is int:
            last_sent = seq
    return last_sent


async def _close_ws(websocket: WebSocket, code: int, reason: str = "") -> None:
    with suppress(Exception):
        await asyncio.wait_for(
            websocket.close(code, reason), timeout=WS_CLOSE_TIMEOUT_S
        )


async def _stream_run_websocket(
    websocket: WebSocket, run: Any, manager: RunManager
) -> None:
    """Serve one filesystem-leased run without retaining its event prefix."""
    try:
        lease = begin_history_reader(run.log_path)
    except (OSError, ValueError):
        await _close_ws(websocket, WS_UNKNOWN_RUN, "run history unavailable")
        return

    defer_release = False
    subscriber = None
    replay_pinned = False
    try:
        if run.finished:
            manager.pin_replay(run)
            replay_pinned = True
            await _send_history_range(websocket, run.log_path)
        else:
            through_seq, subscriber = manager.subscribe(run)
            last_sent = await _send_history_range(
                websocket,
                run.log_path,
                allow_empty=True,
                through_seq=through_seq,
            )
            while True:
                item = await subscriber.queue.get()
                if item is SUBSCRIBER_END:
                    break
                if item is SUBSCRIBER_RESYNC:
                    through_seq, subscriber = manager.resubscribe(run, subscriber)
                    last_sent = await _send_history_range(
                        websocket,
                        run.log_path,
                        after_seq=last_sent,
                        through_seq=through_seq,
                        allow_empty=True,
                    )
                    continue
                seq = item.get("seq")
                if type(seq) is int and seq <= last_sent:
                    continue
                await _send_ws_record(websocket, item)
                if type(seq) is int:
                    last_sent = seq
    except SubscriberLimitError:
        await _close_ws(websocket, WS_SUBSCRIBER_LIMIT, "subscriber_limit")
        return
    except HistoryFormatError as error:
        await _close_ws(websocket, WS_HISTORY_FORMAT, _ws_close_reason(str(error)))
        return
    except _SlowSubscriber:
        manager.reserve_resync(run)
        manager.defer_history_reader_release(
            run.log_path,
            lease,
            run.history_limit,
        )
        defer_release = True
        await _close_ws(websocket, WS_RESYNC_REQUIRED, "resync_required")
        return
    finally:
        if replay_pinned:
            manager.unpin_replay(run)
        if subscriber is not None:
            manager.unsubscribe(run, subscriber)
        if not defer_release:
            manager.release_history_reader(run.log_path, lease, run.history_limit)
    await _close_ws(websocket, 1000)


def build_app(workspace: Workspace) -> Application:
    """One server per workspace. A fresh Router per app — the module
    singleton router would leak routes across instances (tests build
    many)."""
    router = Router()
    app = Application(router=router)
    manager = RunManager()
    writes = _SourceWriteCoordinator()
    resolver = workspace.resolver
    app.middlewares.append(_LocalRequestBoundary())
    app.services.register(RunManager, instance=manager)  # test access
    manifest = workspace.manifest.model
    root = workspace.root

    app.on_stop(lambda _app: manager.shutdown())

    def locate_run_history(
        run_id: str, request: Request
    ) -> tuple[Any | None, Path] | Response:
        """Resolve one live/known or flow-qualified durable run log."""
        flow = _query_value(request, "flow")
        run = manager.get(run_id)
        if run is not None:
            return run, resolver.run_log(run.identity, run_id)
        if not flow:
            return json_response(
                {"error": "unknown_run", "message": "unknown run — pass ?flow="},
                status=404,
            )
        identity = resolver.normalize_identity(flow)
        return None, resolver.run_log(identity, run_id)

    def acquire_history_reader(log_path: Path, run_id: str) -> Path | Response:
        if not log_path.is_file():
            return json_response(
                {"error": "unknown_run", "message": f"no run log for {run_id!r}"},
                status=404,
            )
        try:
            return begin_history_reader(log_path)
        except (OSError, ValueError):
            return json_response(
                {"error": "unknown_run", "message": f"no run log for {run_id!r}"},
                status=404,
            )

    @router.get("/api/workspace")
    async def get_workspace() -> Response:
        info = manifest.workspace
        env_discovery = workspace.discover_env_profiles()
        return json_response(
            {
                "name": info.name if info else None,
                "description": info.description if info else None,
                "root": str(root),
                "flows_root": manifest.flows.root,
                "environments_root": manifest.environments.root,
                "data_root": manifest.data.root,
                "main": manifest.flows.main,
                "env_profiles": sorted(env_discovery.profiles),
                "env_profile_warnings": [
                    {
                        "name": issue.name,
                        "path": str(issue.path),
                        "message": issue.message,
                    }
                    for issue in env_discovery.issues.values()
                ],
                "env_default": manifest.environments.default,
                "version": napflow.__version__,
            }
        )

    @router.get("/api/flows")
    async def get_flows() -> Response:
        return json_response(
            {"flows": [_flow_summary(ref) for ref in workspace.discover_flows()]}
        )

    @router.get("/api/flows/*")
    async def get_flow(request: Request) -> Response:
        """Flow detail for the canvas. Check E-codes do NOT 400 here
        (M4 pin): the editor must keep working on a flow that is
        mid-edit invalid (e.g. E008 while the python function is still
        being written) — only RUNS are gated on E-codes. Load failures
        still 400: there is no model to return."""
        try:
            identity = resolver.normalize_identity(request.route_values["tail"])
            file = resolver.flow_file(identity)
            code_file = resolver.source_file(identity, "nodes.py")
        except WorkspaceBoundaryError as e:
            return _workspace_boundary(e)
        flow_dir = file.parent
        if not file.is_file():
            return json_response(
                {"error": "flow_not_found", "message": f"no flow at {identity!r}"},
                status=404,
            )
        try:
            loaded = load_flow(file)
        except LoadError as e:
            return json_response(
                {
                    "error": "load",
                    "message": str(e),
                    "diagnostics": [
                        _diag_payload(d, root) for d in diagnostics_from_load_error(e)
                    ],
                },
                status=400,
            )
        diagnostics = check_run_closure(loaded, identity, workspace)
        surfaces = node_surfaces(loaded.model, flow_dir, workspace)
        return json_response(
            {
                "identity": identity,
                # exclude_unset: the canvas edits and PUTs this dump back;
                # materialized defaults would bloat every file it saves
                "flow": loaded.model.model_dump(
                    mode="json", by_alias=True, exclude_unset=True
                ),
                "diagnostics": [_diag_payload(d, root) for d in diagnostics],
                # optimistic-concurrency tokens for the write path
                "etag": _etag(file),
                "code_etag": _etag(code_file),
                # the canvas python-function dropdown (EC14, AST-only);
                # None = no nodes.py or unparseable
                "functions": python_functions(flow_dir),
                # canvas handle/coloring data (D11) — python ports are
                # AST-derived server-side (EC14), never in the UI
                "ports": {
                    node_id: (
                        None
                        if surface is None
                        else {
                            "inputs": surface.inputs,
                            "outputs": surface.outputs,
                            "required_inputs": sorted(surface.required_inputs),
                            "growable": surface.growable,
                        }
                    )
                    for node_id, surface in surfaces.items()
                },
                # subflow UX (S4/M6, FR-1007): ghost-wire endpoints per
                # node, and D09's "used in N places" for this flow
                "template_refs": template_refs(loaded.model),
                "used_by": [
                    {"identity": flow_id, "nodes": node_ids}
                    for flow_id, node_ids in sorted(
                        used_by(workspace, identity).items()
                    )
                ],
            }
        )

    @router.put("/api/flows/*")
    async def put_flow(request: Request) -> Response:
        """The canvas write path (FR-1003): validate the model JSON,
        merge into the round-trip doc, emit through the ONE canonical
        serializer (D23). `base_etag` is optimistic concurrency —
        a mismatch means someone else wrote the file since the client
        loaded it (409, client reloads or retries with `force`,
        last-write-wins per FR-1004's ceiling)."""
        try:
            identity = resolver.normalize_identity(request.route_values["tail"])
            file = resolver.flow_file(identity)
        except WorkspaceBoundaryError as e:
            return _workspace_boundary(e)
        body = await _json_object(request)
        if body is None or not isinstance(body.get("flow"), dict):
            return _bad_request('body must be {"flow": {...}, "base_etag"?, "force"?}')
        async with writes.lock(file):
            if not file.is_file():
                return json_response(
                    {
                        "error": "flow_not_found",
                        "message": f"no flow at {identity!r}",
                    },
                    status=404,
                )
            current = _etag(file)
            if not body.get("force") and body.get("base_etag") != current:
                return json_response(
                    {"error": "etag_conflict", "etag": current}, status=409
                )
            try:
                model = validate_flow_payload(body["flow"], file)
            except LoadError as e:
                return json_response(
                    {
                        "error": "validation",
                        "message": "flow payload failed validation",
                        "diagnostics": [
                            _diag_payload(d, root)
                            for d in diagnostics_from_load_error(e)
                        ],
                    },
                    status=400,
                )
            try:
                doc = load_document(file)
            except LoadError:
                doc = None
            if not isinstance(doc, CommentedMap):
                # An already-corrupt source can be recovered from the validated
                # canvas model; its comments were unavailable before this save.
                doc = CommentedMap()
            merge_flow_document(
                doc, model.model_dump(mode="json", by_alias=True, exclude_unset=True)
            )
            try:
                save_document(doc, file)
            except OSError as e:
                return _write_failure(file, e)
            # Keep diagnostics and response metadata in the same critical
            # section as the source version they describe.
            loaded = load_flow(file)
            diagnostics = check_run_closure(loaded, identity, workspace)
            return json_response(
                {
                    "identity": identity,
                    "etag": _etag(file),
                    "diagnostics": [_diag_payload(d, root) for d in diagnostics],
                }
            )

    @router.get("/api/code/*")
    async def get_code(request: Request) -> Response:
        """The flow's nodes.py for the code editor — whole-file
        read/write (owner fork, 2026-07-06)."""
        try:
            identity = resolver.normalize_identity(request.route_values["tail"])
            flow_file = resolver.flow_file(identity)
            path = resolver.source_file(identity, "nodes.py")
        except WorkspaceBoundaryError as e:
            return _workspace_boundary(e)
        flow_dir = flow_file.parent
        if not flow_file.is_file():
            return json_response(
                {"error": "flow_not_found", "message": f"no flow at {identity!r}"},
                status=404,
            )
        exists = path.is_file()
        code = path.read_text(encoding="utf-8") if exists else ""
        return json_response(
            {
                "identity": identity,
                "exists": exists,
                "code": code,
                "etag": _etag(path),
                "syntax_error": _syntax_diagnostic(code) if exists else None,
                "functions": python_functions(flow_dir),
            }
        )

    @router.put("/api/code/*")
    async def put_code(request: Request) -> Response:
        """Save nodes.py verbatim. A syntax error is reported but the
        file is SAVED ANYWAY (last-write-wins; the editor must never
        hold user code hostage) — broken code simply surfaces as E008
        on the canvas until fixed."""
        try:
            identity = resolver.normalize_identity(request.route_values["tail"])
            flow_file = resolver.flow_file(identity)
            path = resolver.source_file(identity, "nodes.py")
        except WorkspaceBoundaryError as e:
            return _workspace_boundary(e)
        body = await _json_object(request)
        if body is None or not isinstance(body.get("code"), str):
            return _bad_request('body must be {"code": "...", "base_etag"?, "force"?}')
        flow_dir = flow_file.parent
        code = body["code"]
        async with writes.lock(path):
            if not flow_file.is_file():
                return json_response(
                    {
                        "error": "flow_not_found",
                        "message": f"no flow at {identity!r}",
                    },
                    status=404,
                )
            current = _etag(path)
            if not body.get("force") and body.get("base_etag") != current:
                return json_response(
                    {"error": "etag_conflict", "etag": current}, status=409
                )
            try:
                atomic_write_text(path, code)
            except OSError as e:
                return _write_failure(path, e)
            return json_response(
                {
                    "identity": identity,
                    "etag": _etag(path),
                    "syntax_error": _syntax_diagnostic(code),
                    "functions": python_functions(flow_dir),
                }
            )

    @router.get("/api/etags/*")
    async def get_etags(request: Request) -> Response:
        """Cheap poll target for external-change detection (FR-1004's
        v1 shape: the canvas polls, prompts on drift)."""
        try:
            identity = resolver.normalize_identity(request.route_values["tail"])
            flow_file = resolver.flow_file(identity)
            code_file = resolver.source_file(identity, "nodes.py")
        except WorkspaceBoundaryError as e:
            return _workspace_boundary(e)
        if not flow_file.is_file():
            return json_response(
                {"error": "flow_not_found", "message": f"no flow at {identity!r}"},
                status=404,
            )
        return json_response(
            {
                "identity": identity,
                "etag": _etag(flow_file),
                "code_etag": _etag(code_file),
            }
        )

    @router.post("/api/flows/clone")
    async def clone_flow(request: Request) -> Response:
        """D09's explicit "Clone to new flow…": fork the flow FOLDER
        (flow.yaml + nodes.py + anything else in it) to a new identity.
        The dest must live under flows.root — a clone discovery can't
        see would be invisible in the sidebar and `napf list`."""
        body = await _json_object(request)
        if (
            body is None
            or not isinstance(body.get("source"), str)
            or not isinstance(body.get("dest"), str)
        ):
            return _bad_request('body must be {"source": "flows/…", "dest": "flows/…"}')
        try:
            source = resolver.normalize_identity(body["source"], label="source flow")
            dest = resolver.normalize_identity(body["dest"], label="destination flow")
            source_dir = resolver.clone_source(source)
            dest_dir = resolver.clone_destination(dest)
            source_file = resolver.source_file(source, "flow.yaml")
        except WorkspaceBoundaryError as e:
            return _workspace_boundary(e)
        if not source_file.is_file():
            return json_response(
                {"error": "flow_not_found", "message": f"no flow at {source!r}"},
                status=404,
            )
        if dest_dir == source_dir or dest_dir.is_relative_to(source_dir):
            return _bad_request("dest must not be the source or inside it")
        async with writes.lock(dest_dir):
            if dest_dir.exists():
                return json_response(
                    {"error": "dest_exists", "message": f"{dest!r} already exists"},
                    status=409,
                )
            # Preserve nested symlinks as links. Dereferencing them here could
            # read outside the workspace even though later resolver access
            # would reject the copied path. Source files themselves use the
            # shared fsync+replace primitive; failed clones are removed before
            # they can be mistaken for accepted destinations.
            try:
                shutil.copytree(
                    source_dir,
                    dest_dir,
                    symlinks=True,
                    copy_function=_copy_clone_file,
                )
            except OSError as e:
                shutil.rmtree(dest_dir, ignore_errors=True)
                return _write_failure(dest_dir, e)
        return json_response({"identity": dest}, status=201)

    @router.post("/api/runs")
    async def post_run(request: Request) -> Response:
        try:
            body = await request.json() or {}
        except ValueError:
            body = None
        if not isinstance(body, dict) or not isinstance(body.get("flow"), str):
            return json_response(
                {"error": "bad_request", "message": 'body must be {"flow": ...}'},
                status=400,
            )
        inputs = body.get("inputs") or {}
        if not isinstance(inputs, dict):
            return json_response(
                {"error": "bad_request", "message": "inputs must be an object"},
                status=400,
            )
        env = body.get("env")
        try:
            prepared = prepare_run(workspace, body["flow"], env)
        except RunPrepError as e:
            return _prep_error(e, root)
        try:
            run = manager.start(workspace, prepared, inputs)
        except WorkspaceBoundaryError as e:
            return _workspace_boundary(e)
        return json_response(
            {
                "run_id": run.run_id,
                "flow": run.identity,
                "state": run.state,
                "log": run.log_path.relative_to(root).as_posix(),
                "warnings": [_diag_payload(d, root) for d in prepared.diagnostics],
                "notes": prepared.notes,
            },
            status=202,
        )

    @router.get("/api/runs")
    async def list_runs(flow: str) -> Response:
        try:
            identity = resolver.normalize_identity(flow)
            runs_dir = resolver.runs_dir(identity)
        except WorkspaceBoundaryError as e:
            return _workspace_boundary(e)
        entries = []
        for candidate in sorted(
            runs_dir.glob("*.jsonl"), key=run_history_sort_key, reverse=True
        ):
            try:
                run_id = resolver.validate_run_id(candidate.stem)
                log = resolver.run_log(identity, run_id)
            except WorkspaceBoundaryError as e:
                return _workspace_boundary(e)
            live = manager.get(run_id)
            if live is not None and not live.finished:
                entries.append({"run_id": run_id, "state": "running"})
                continue
            tail = last_jsonl_record(log)
            finished = tail is not None and tail.get("event") == "run_finished"
            entries.append(
                {
                    "run_id": run_id,
                    "state": (
                        tail.get("state", "error")
                        if finished
                        else _replay_history_state(live, log)
                    ),
                }
            )
        return json_response({"flow": identity, "runs": entries})

    @router.get("/api/runs/{run_id}")
    async def get_run(run_id: str) -> Response:
        try:
            run_id = resolver.validate_run_id(run_id)
        except WorkspaceBoundaryError as e:
            return _workspace_boundary(e)
        run = manager.get(run_id)
        if run is None:
            return json_response(
                {"error": "unknown_run", "message": f"no run {run_id!r}"}, status=404
            )
        return json_response(run.status())

    @router.get("/api/runs/{run_id}/events")
    async def get_run_events(run_id: str, request: Request) -> Response:
        """Return one bounded canonical replay page (D13/D34/D36)."""
        try:
            run_id = resolver.validate_run_id(run_id)
            after_seq, limit = _parse_replay_page_query(request)
            selected_frame = _parse_frame_query(request, "frame")
            target = locate_run_history(run_id, request)
        except _ReplayQueryError as e:
            return _bad_request(str(e))
        except WorkspaceBoundaryError as e:
            return _workspace_boundary(e)
        if isinstance(target, Response):
            return target
        run, log_path = target
        acquired = acquire_history_reader(log_path, run_id)
        if isinstance(acquired, Response):
            return acquired
        lease = acquired
        try:
            try:
                snapshot = _capture_replay_snapshot(run, log_path)
                view_builder: _ReplayViewBuilder | None = None

                def matches_selected_frame(
                    record: dict[str, Any], metadata: _ReplayMetadata
                ) -> bool:
                    return (
                        selected_frame is None
                        or record.get("frame") == selected_frame
                        or (
                            selected_frame == metadata.root_frame
                            and record.get("frame") is None
                            and record.get("event") in _RUN_ENVELOPE_EVENTS
                        )
                    )

                def fold_selected_frame(
                    record: dict[str, Any], metadata: _ReplayMetadata
                ) -> None:
                    nonlocal view_builder
                    if view_builder is None:
                        view_builder = _ReplayViewBuilder(
                            selected_frame or metadata.root_frame
                        )
                    if matches_selected_frame(record, metadata):
                        view_builder.apply(record)

                metadata, records, has_more = _read_replay_page(
                    log_path,
                    after_seq=after_seq,
                    limit=limit,
                    allow_empty=snapshot.allow_empty,
                    through_seq=snapshot.through_seq,
                    matches=matches_selected_frame,
                    on_record=fold_selected_frame,
                )
            except HistoryFormatError as e:
                return _history_format_response(e)
        finally:
            manager.release_history_reader(
                log_path,
                lease,
                manifest.defaults.run.history,
            )
        next_after_seq = records[-1]["seq"] if records else after_seq
        return json_response(
            _replay_envelope(
                run_id,
                metadata,
                snapshot.history_state,
                snapshot.run_summary,
            )
            | {
                "frame": selected_frame,
                "after_seq": after_seq,
                "next_after_seq": next_after_seq,
                "has_more": has_more,
                "events": records,
                "view_summary": (
                    view_builder.payload()
                    if view_builder is not None
                    else _ReplayViewBuilder(
                        selected_frame or metadata.root_frame
                    ).payload()
                ),
            }
        )

    @router.get("/api/runs/{run_id}/frames")
    async def get_run_frames(run_id: str, request: Request) -> Response:
        """Page direct-child durable frame summaries for one parent."""
        try:
            run_id = resolver.validate_run_id(run_id)
            after_seq, limit = _parse_replay_page_query(request)
            requested_parent = _parse_frame_query(request, "parent_frame")
            target = locate_run_history(run_id, request)
        except _ReplayQueryError as e:
            return _bad_request(str(e))
        except WorkspaceBoundaryError as e:
            return _workspace_boundary(e)
        if isinstance(target, Response):
            return target
        run, log_path = target
        acquired = acquire_history_reader(log_path, run_id)
        if isinstance(acquired, Response):
            return acquired
        lease = acquired
        try:
            try:
                snapshot = _capture_replay_snapshot(run, log_path)
                metadata, frame_records, has_more = _read_replay_page(
                    log_path,
                    after_seq=after_seq,
                    limit=limit,
                    allow_empty=snapshot.allow_empty,
                    through_seq=snapshot.through_seq,
                    matches=lambda record, meta: (
                        record.get("event") == "frame_finished"
                        and record.get("parent_frame")
                        == (requested_parent or meta.root_frame)
                    ),
                )
            except HistoryFormatError as e:
                return _history_format_response(e)
        finally:
            manager.release_history_reader(
                log_path,
                lease,
                manifest.defaults.run.history,
            )
        frames = [_replay_frame_summary(record) for record in frame_records]
        parent_frame = requested_parent or metadata.root_frame
        next_after_seq = frames[-1]["seq"] if frames else after_seq
        return json_response(
            _replay_envelope(
                run_id,
                metadata,
                snapshot.history_state,
                snapshot.run_summary,
            )
            | {
                "parent_frame": parent_frame,
                "after_seq": after_seq,
                "next_after_seq": next_after_seq,
                "has_more": has_more,
                "frames": frames,
            }
        )

    @router.get("/api/runs/{run_id}/events/{seq}")
    async def get_run_event(run_id: str, seq: str, request: Request) -> Response:
        """Resolve the content of exactly one canonical event on demand."""
        try:
            run_id = resolver.validate_run_id(run_id)
            selected_seq = _parse_replay_integer(
                seq,
                name="seq",
                default=None,
                minimum=1,
                maximum=_MAX_REPLAY_SEQUENCE,
            )
            target = locate_run_history(run_id, request)
        except _ReplayQueryError as e:
            return _bad_request(str(e))
        except WorkspaceBoundaryError as e:
            return _workspace_boundary(e)
        if isinstance(target, Response):
            return target
        run, log_path = target
        acquired = acquire_history_reader(log_path, run_id)
        if isinstance(acquired, Response):
            return acquired
        lease = acquired
        try:
            try:
                snapshot = _capture_replay_snapshot(run, log_path)
                metadata, record = _read_replay_event(
                    log_path,
                    seq=selected_seq,
                    allow_empty=snapshot.allow_empty,
                    through_seq=snapshot.through_seq,
                )
            except HistoryFormatError as e:
                return _history_format_response(e)
            if record is None:
                return json_response(
                    {
                        "error": "event_not_found",
                        "message": f"run {run_id!r} has no event at seq {selected_seq}",
                    },
                    status=404,
                )
            try:
                event = resolve_record_content(
                    record,
                    metadata.features,
                    RunContentStore(log_path),
                )
            except ContentMissingError as e:
                return _history_content_response(
                    e, code="history_content_missing", status=404
                )
            except ContentCorruptError as e:
                return _history_content_response(
                    e, code="history_content_corrupt", status=422
                )
            except ContentOmittedError as e:
                return _history_content_response(
                    e, code="history_content_omitted", status=422
                )
            except (ContentStoreError, HistoryFormatError) as e:
                return _history_content_response(
                    e, code="history_content_malformed", status=422
                )
            return json_response(
                _replay_envelope(
                    run_id,
                    metadata,
                    snapshot.history_state,
                    snapshot.run_summary,
                )
                | {"event": event}
            )
        finally:
            manager.release_history_reader(
                log_path,
                lease,
                manifest.defaults.run.history,
            )

    @router.post("/api/runs/{run_id}/abort")
    async def abort_run(run_id: str) -> Response:
        try:
            run_id = resolver.validate_run_id(run_id)
        except WorkspaceBoundaryError as e:
            return _workspace_boundary(e)
        run = manager.get(run_id)
        if run is None:
            return json_response(
                {"error": "unknown_run", "message": f"no run {run_id!r}"}, status=404
            )
        if run.finished:
            return json_response({"run_id": run_id, "state": run.state})
        assert run.flow_run is not None
        run.flow_run.abort()
        return json_response({"run_id": run_id, "state": "aborting"}, status=202)

    @router.ws("/ws/runs/{run_id}")
    async def ws_run(websocket: WebSocket, run_id: str) -> None:
        """Text frames = the JSONL lines, verbatim (D13). Live runs:
        capture a durable-prefix boundary, stream that prefix from disk, then
        consume a bounded live queue. Slow consumers close 4410 and reconnect
        to resync from disk. Finished runs stream the file, then close."""
        try:
            run_id = resolver.validate_run_id(run_id)
        except WorkspaceBoundaryError:
            await websocket.close(WS_WORKSPACE_BOUNDARY, "workspace_boundary")
            return
        await websocket.accept()
        run = manager.get(run_id)
        if run is None:
            await websocket.close(WS_UNKNOWN_RUN, f"no run {run_id!r}")
            return
        await _stream_run_websocket(websocket, run, manager)

    if STATIC_DIR.is_dir():
        # the pre-built UI bundle (S4/M2, NFR-03); SPA fallback for
        # client-side routes
        app.serve_files(STATIC_DIR, fallback_document="index.html")
    else:

        @router.get("/")
        async def index() -> Response:
            return html(_PLACEHOLDER)

    return app
