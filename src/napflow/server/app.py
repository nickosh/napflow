"""BlackSheep app — a THIN adapter over napflow.core (FR-1001, D03/D04).

Surface pinned in the workspace-manifest spec ("Server surface"):
REST under /api, one WebSocket per run under /ws/runs/{run_id}, static
UI bundle at / (arrives S4/M2 — until then a placeholder page).
Everything binds to localhost only; the server trusts core for all
semantics (gate, env, masking) via core/runprep.py.
"""

import json
from pathlib import Path
from typing import Any

from blacksheep import Application, Request, Response, WebSocket
from blacksheep.server.responses import html
from blacksheep.server.responses import json as json_response
from blacksheep.server.routing import Router

import napflow
from napflow.core.checker import CheckDiagnostic
from napflow.core.events import encode_record
from napflow.core.loader import LoadError, load_flow
from napflow.core.models import EndNode, StartNode
from napflow.core.runprep import RunPrepError, prepare_run
from napflow.core.workspace import Workspace
from napflow.server.runs import RunManager

STATIC_DIR = Path(__file__).parent / "static"

_PLACEHOLDER = """<!doctype html>
<html><head><title>napflow</title></head><body>
<h1>napflow server is running</h1>
<p>The canvas UI ships in a later build — this wheel carries no static
bundle. The API is live under <code>/api</code>.</p>
</body></html>"""

# WebSocket close codes (4xxx = application-defined)
WS_UNKNOWN_RUN = 4404


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


def _tail_record(path: Path) -> dict[str, Any] | None:
    """Last complete JSONL record, reading only the file's tail (run
    logs can be hundreds of MB — the capture valves cap events, not
    files). An aborted run's trailing partial line is skipped (EC20)."""
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 65536))
            chunk = f.read()
    except OSError:
        return None
    lines = [line for line in chunk.split(b"\n") if line.strip()]
    # a chunk that doesn't start at byte 0 may open mid-line — drop it
    if size > 65536 and len(lines) > 1:
        lines = lines[1:]
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            return record
    return None


def _read_records(path: Path) -> list[dict[str, Any]]:
    """Full JSONL replay; a trailing partial line is tolerated (EC20)."""
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                break  # flushed-per-line: only the last line can be cut
    return records


def build_app(workspace: Workspace) -> Application:
    """One server per workspace. A fresh Router per app — the module
    singleton router would leak routes across instances (tests build
    many)."""
    router = Router()
    app = Application(router=router)
    manager = RunManager()
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
        identity = request.route_values["tail"].strip("/")
        try:
            prepared = prepare_run(workspace, identity)
        except RunPrepError as e:
            return _prep_error(e, root)
        return json_response(
            {
                "identity": prepared.identity,
                "flow": prepared.loaded.model.model_dump(mode="json", by_alias=True),
                "diagnostics": [_diag_payload(d, root) for d in prepared.diagnostics],
            }
        )

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
        run = manager.start(workspace, prepared, inputs)
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
        identity = Path(flow).as_posix().strip("/")
        runs_dir = root / ".napflow" / "runs" / Path(identity)
        entries = []
        for log in sorted(runs_dir.glob("*.jsonl"), reverse=True):
            run_id = log.stem
            live = manager.get(run_id)
            if live is not None and not live.finished:
                entries.append({"run_id": run_id, "state": "running"})
                continue
            tail = _tail_record(log)
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
        run = manager.get(run_id)
        if run is not None:
            log_path = run.log_path
        else:
            flow = request.query.get("flow")
            if not flow:
                return json_response(
                    {"error": "unknown_run", "message": "unknown run — pass ?flow="},
                    status=404,
                )
            identity = Path(flow[0]).as_posix().strip("/")
            log_path = root / ".napflow" / "runs" / Path(identity) / f"{run_id}.jsonl"
        if not log_path.is_file():
            return json_response(
                {"error": "unknown_run", "message": f"no run log for {run_id!r}"},
                status=404,
            )
        return json_response({"run_id": run_id, "events": _read_records(log_path)})

    @router.post("/api/runs/{run_id}/abort")
    async def abort_run(run_id: str) -> Response:
        run = manager.get(run_id)
        if run is None:
            return json_response(
                {"error": "unknown_run", "message": f"no run {run_id!r}"}, status=404
            )
        if run.finished:
            return json_response({"run_id": run_id, "state": run.state})
        run.flow_run.abort()
        return json_response({"run_id": run_id, "state": "aborting"}, status=202)

    @router.ws("/ws/runs/{run_id}")
    async def ws_run(websocket: WebSocket, run_id: str) -> None:
        """Text frames = the JSONL lines, verbatim (D13). Live runs:
        replay the buffered prefix, then stream until run end (normal
        close). Finished runs: replay the file, then close. Unknown:
        close 4404."""
        await websocket.accept()
        run = manager.get(run_id)
        if run is None:
            await websocket.close(WS_UNKNOWN_RUN, f"no run {run_id!r}")
            return
        if run.finished:
            for record in _read_records(run.log_path):
                await websocket.send_text(encode_record(record))
            await websocket.close()
            return
        snapshot, queue = manager.subscribe(run)
        try:
            for record in snapshot:
                await websocket.send_text(encode_record(record))
            while (record := await queue.get()) is not None:
                await websocket.send_text(encode_record(record))
        finally:
            manager.unsubscribe(run, queue)
        await websocket.close()

    if STATIC_DIR.is_dir():
        # the pre-built UI bundle (S4/M2, NFR-03); SPA fallback for
        # client-side routes
        app.serve_files(STATIC_DIR, fallback_document="index.html")
    else:

        @router.get("/")
        async def index() -> Response:
            return html(_PLACEHOLDER)

    return app
