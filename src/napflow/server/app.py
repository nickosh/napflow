"""BlackSheep app — a THIN adapter over napflow.core (FR-1001, D03/D04).

Surface pinned in the workspace-manifest spec ("Server surface"):
REST under /api, one WebSocket per run under /ws/runs/{run_id}, static
UI bundle at / (release artifacts carry it; unsupported raw-source installs
get an explanatory placeholder page).
Everything binds to localhost only; the server trusts core for all
semantics (gate, env, masking) via core/runprep.py.
"""

import ast
import hashlib
import shutil
from pathlib import Path
from typing import Any

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
    last_jsonl_record,
    resolve_record_content,
    run_history_sort_key,
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
from napflow.server import replay, ws
from napflow.server.boundary import (
    LocalRequestBoundary,
    SourceWriteCoordinator,
)
from napflow.server.runs import RunManager

STATIC_DIR = Path(__file__).parent / "static"

_PLACEHOLDER = """<!doctype html>
<html><head><title>napflow</title></head><body>
<h1>napflow server is running</h1>
<p>This installation does not contain the pre-built canvas bundle. Install
napflow from PyPI or a GitHub release artifact; direct VCS and raw-source
installs are unsupported. The API is live under <code>/api</code>.</p>
</body></html>"""

# WebSocket close code owned by the route boundary (4xxx = application-defined)
WS_WORKSPACE_BOUNDARY = 4400


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


def build_app(workspace: Workspace) -> Application:
    """One server per workspace. A fresh Router per app — the module
    singleton router would leak routes across instances (tests build
    many)."""
    router = Router()
    app = Application(router=router)
    manager = RunManager()
    writes = SourceWriteCoordinator()
    resolver = workspace.resolver
    app.middlewares.append(LocalRequestBoundary())
    app.services.register(RunManager, instance=manager)  # test access
    manifest = workspace.manifest.model
    root = workspace.root

    app.on_stop(lambda _app: manager.shutdown())

    def locate_run_history(
        run_id: str, request: Request
    ) -> tuple[Any | None, Path] | Response:
        """Resolve one live/known or flow-qualified durable run log."""
        flow = replay.query_value(request, "flow")
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
                        else replay.replay_history_state(live, log)
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
            after_seq, limit = replay.parse_replay_page_query(request)
            selected_frame = replay.parse_frame_query(request, "frame")
            target = locate_run_history(run_id, request)
        except replay.ReplayQueryError as e:
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
                snapshot = replay.capture_replay_snapshot(run, log_path)
                view_builder: replay.ReplayViewBuilder | None = None

                def matches_selected_frame(
                    record: dict[str, Any], metadata: replay.ReplayMetadata
                ) -> bool:
                    return (
                        selected_frame is None
                        or record.get("frame") == selected_frame
                        or (
                            selected_frame == metadata.root_frame
                            and record.get("frame") is None
                            and record.get("event") in replay.RUN_ENVELOPE_EVENTS
                        )
                    )

                def fold_selected_frame(
                    record: dict[str, Any], metadata: replay.ReplayMetadata
                ) -> None:
                    nonlocal view_builder
                    if view_builder is None:
                        view_builder = replay.ReplayViewBuilder(
                            selected_frame or metadata.root_frame
                        )
                    if matches_selected_frame(record, metadata):
                        view_builder.apply(record)

                metadata, records, has_more = replay.read_replay_page(
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
            replay.replay_envelope(
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
                    else replay.ReplayViewBuilder(
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
            after_seq, limit = replay.parse_replay_page_query(request)
            requested_parent = replay.parse_frame_query(request, "parent_frame")
            target = locate_run_history(run_id, request)
        except replay.ReplayQueryError as e:
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
                snapshot = replay.capture_replay_snapshot(run, log_path)
                metadata, frame_records, has_more = replay.read_replay_page(
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
        frames = [replay.replay_frame_summary(record) for record in frame_records]
        parent_frame = requested_parent or metadata.root_frame
        next_after_seq = frames[-1]["seq"] if frames else after_seq
        return json_response(
            replay.replay_envelope(
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
            selected_seq = replay.parse_replay_integer(
                seq,
                name="seq",
                default=None,
                minimum=1,
                maximum=replay.MAX_REPLAY_SEQUENCE,
            )
            target = locate_run_history(run_id, request)
        except replay.ReplayQueryError as e:
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
                snapshot = replay.capture_replay_snapshot(run, log_path)
                metadata, record = replay.read_replay_event(
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
                replay.replay_envelope(
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
            await websocket.close(ws.WS_UNKNOWN_RUN, f"no run {run_id!r}")
            return
        await ws.stream_run_websocket(websocket, run, manager)

    if STATIC_DIR.is_dir():
        # the pre-built UI bundle (S4/M2, NFR-03); SPA fallback for
        # client-side routes
        app.serve_files(STATIC_DIR, fallback_document="index.html")
    else:

        @router.get("/")
        async def index() -> Response:
            return html(_PLACEHOLDER)

    return app
