"""BlackSheep app — a THIN adapter over napflow.core (FR-1001, D03/D04).

Surface pinned in the workspace-manifest spec ("Server surface"):
REST under /api, one WebSocket per run under /ws/runs/{run_id}, static
UI bundle at / (arrives S4/M2 — until then a placeholder page).
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
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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
    HISTORY_FORMAT_MAJOR,
    HISTORY_SUPPORTED_FEATURES,
    HistoryFormatError,
    begin_history_reader,
    encode_record,
    last_jsonl_record,
    parse_history_features,
    parse_history_format,
    run_history_sort_key,
)
from napflow.core.files import atomic_write_text
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
<p>The canvas UI ships in a later build — this wheel carries no static
bundle. The API is live under <code>/api</code>.</p>
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

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


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


def _validate_history_envelope(record: Any) -> None:
    """Validate the first nonblank history record before replaying data."""
    if (
        not isinstance(record, dict)
        or record.get("event") != "run_started"
        or type(record.get("seq")) is not int
        or record["seq"] != 1
    ):
        raise HistoryFormatError(
            "run history must begin with a run_started envelope at seq 1"
        )
    has_format = "format" in record
    if has_format and record["format"] is None:
        raise HistoryFormatError(
            "run-history format must be omitted for v0.1 or contain a string"
        )
    if not has_format and "features" in record:
        raise HistoryFormatError(
            "pre-versioning run history must omit both format and features"
        )
    marker = record.get("format")
    major = parse_history_format(marker)
    if major > HISTORY_FORMAT_MAJOR:
        raise HistoryFormatError(
            "unsupported newer run-history major; "
            f"this build supports up to napflow-run/{HISTORY_FORMAT_MAJOR}"
        )
    features = (
        parse_history_features(record["features"])
        if "features" in record
        else frozenset()
    )
    unsupported = features - HISTORY_SUPPORTED_FEATURES
    if unsupported:
        # repr() escapes malformed Unicode (including lone surrogates), so
        # untrusted feature names cannot break either JSON or WebSocket UTF-8.
        shown = ", ".join(repr(feature) for feature in sorted(unsupported))
        raise HistoryFormatError(f"unsupported run-history feature(s): {shown}")


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
                break  # flushed-per-line: only the last line can be cut
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
    if validate_history and not seen and not allow_empty:
        raise HistoryFormatError("run history is empty; no run_started envelope")


def _read_records(
    path: Path, *, validate_history: bool = True, allow_empty: bool = False
) -> list[dict[str, Any]]:
    """Compatibility/full REST replay; M5 replaces this response with paging."""
    return list(
        _iter_records(
            path,
            validate_history=validate_history,
            allow_empty=allow_empty,
        )
    )


def _history_format_response(error: HistoryFormatError) -> Response:
    message = str(error).encode("utf-8", errors="backslashreplace").decode("utf-8")
    return json_response({"error": "history_format", "message": message}, status=422)


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
        await _close_ws(
            websocket, WS_HISTORY_FORMAT, _ws_close_reason(str(error))
        )
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
            manager.release_history_reader(
                run.log_path, lease, run.history_limit
            )
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

    @router.get("/api/workspace")
    async def get_workspace() -> Response:
        info = manifest.workspace
        return json_response(
            {
                "name": info.name if info else None,
                "description": info.description if info else None,
                "root": str(root),
                "flows_root": manifest.flows.root,
                "main": manifest.flows.main,
                "env_profiles": sorted(workspace.env_profiles()),
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
                    "state": tail.get("state", "error") if finished else "incomplete",
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
        """Replay = re-read the JSONL (D13) — live runs replay their
        flushed prefix. Runs not in the registry need `?flow=`."""
        try:
            run_id = resolver.validate_run_id(run_id)
        except WorkspaceBoundaryError as e:
            return _workspace_boundary(e)
        run = manager.get(run_id)
        if run is not None:
            try:
                log_path = resolver.run_log(run.identity, run_id)
            except WorkspaceBoundaryError as e:
                return _workspace_boundary(e)
        else:
            flow = request.query.get("flow")
            if not flow:
                return json_response(
                    {"error": "unknown_run", "message": "unknown run — pass ?flow="},
                    status=404,
                )
            try:
                identity = resolver.normalize_identity(flow[0])
                log_path = resolver.run_log(identity, run_id)
            except WorkspaceBoundaryError as e:
                return _workspace_boundary(e)
        if not log_path.is_file():
            return json_response(
                {"error": "unknown_run", "message": f"no run log for {run_id!r}"},
                status=404,
            )
        try:
            lease = begin_history_reader(log_path)
        except OSError:
            return json_response(
                {"error": "unknown_run", "message": f"no run log for {run_id!r}"},
                status=404,
            )
        try:
            try:
                records = _read_records(
                    log_path,
                    allow_empty=run is not None and not run.finished,
                )
            except HistoryFormatError as e:
                return _history_format_response(e)
        finally:
            manager.release_history_reader(
                log_path,
                lease,
                manifest.defaults.run.history,
            )
        return json_response({"run_id": run_id, "events": records})

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
