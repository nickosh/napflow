"""S4/M1 server: BlackSheep thin adapter over core (FR-1001, FR-806).

REST + WebSocket surface as pinned in the WM spec ("Server surface").
The load-bearing claims here: WS frames are byte-identical to the JSONL
lines (D13), the prepare gate maps to 404/400 with diagnostics, and —
in the real-uvicorn test — TR-9's through-the-server half: a
python-node flow (worker subprocess) runs via HTTP + WS on a localhost
socket. On Windows CI that test proves the Proactor loop, worker pipes,
and the ASGI/WebSocket stack coexist (EC28/EC33).
"""

import asyncio
import json
import socket
from pathlib import Path

import pytest
from blacksheep.contents import JSONContent
from blacksheep.testing import TestClient

from napflow.cli.main import DEFAULT_UI_PORT, _pick_ui_port
from napflow.cli.scaffold import scaffold_workspace
from napflow.core.workspace import Workspace, load_workspace
from napflow.server import build_app

# --------------------------------------------------------------------------
# Harness — suite style: sync tests drive async scenarios via asyncio.run


def make_scaffold_ws(tmp_path: Path) -> Workspace:
    """A real `napf init` workspace — flows/smoke is the offline
    fixture→python→assert flow (EC34), i.e. it exercises the worker."""
    wsdir = tmp_path / "ws"
    list(scaffold_workspace(wsdir))
    return load_workspace(wsdir)


def with_client(workspace: Workspace, scenario) -> None:
    async def runner():
        app = build_app(workspace)
        await app.start()
        try:
            await scenario(TestClient(app))
        finally:
            await app.stop()

    asyncio.run(runner())


async def wait_finished(client: TestClient, run_id: str, timeout_s: float = 30.0):
    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        response = await client.get(f"/api/runs/{run_id}")
        status = await response.json()
        if status["state"] != "running":
            return status
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"run {run_id} still running after {timeout_s}s")
        await asyncio.sleep(0.02)


async def start_run(client: TestClient, flow: str, **body) -> dict:
    response = await client.post(
        "/api/runs", content=JSONContent({"flow": flow, **body})
    )
    assert response.status == 202, await response.text()
    return await response.json()


# --------------------------------------------------------------------------
# REST surface


def test_workspace_endpoint(tmp_path):
    ws = make_scaffold_ws(tmp_path)

    async def scenario(client):
        response = await client.get("/api/workspace")
        assert response.status == 200
        payload = await response.json()
        assert payload["flows_root"] == "flows"
        assert payload["main"] == "flows/main"
        assert payload["env_profiles"] == ["dev", "example"]
        assert payload["env_default"] == "dev"
        assert payload["version"]

    with_client(ws, scenario)


def test_flows_listing_flags_invalid_flows(tmp_path):
    ws = make_scaffold_ws(tmp_path)
    broken = ws.root / "flows" / "broken"
    broken.mkdir()
    (broken / "flow.yaml").write_text("schema: [not-a-flow", encoding="utf-8")

    async def scenario(client):
        response = await client.get("/api/flows")
        payload = await response.json()
        by_identity = {f["identity"]: f for f in payload["flows"]}
        assert by_identity["flows/broken"] == {
            "identity": "flows/broken",
            "valid": False,
        }
        smoke = by_identity["flows/smoke"]
        assert smoke["valid"] is True
        assert {p["name"] for p in smoke["outputs"]}

    with_client(ws, scenario)


def test_flow_detail_returns_model_and_diagnostics(tmp_path):
    ws = make_scaffold_ws(tmp_path)

    async def scenario(client):
        response = await client.get("/api/flows/flows/smoke")
        assert response.status == 200
        payload = await response.json()
        assert payload["identity"] == "flows/smoke"
        node_ids = [n["id"] for n in payload["flow"]["nodes"]]
        assert "start" in node_ids or len(node_ids) > 0
        assert isinstance(payload["diagnostics"], list)

        missing = await client.get("/api/flows/flows/nope")
        assert missing.status == 404
        assert (await missing.json())["error"] == "flow_not_found"

    with_client(ws, scenario)


def test_flow_detail_ports_include_ast_derived_python_surface(tmp_path):
    """The canvas draws handles from `ports` (D11) — the python node's
    inputs come from AST-parsing nodes.py server-side (EC14)."""
    ws = make_scaffold_ws(tmp_path)

    async def scenario(client):
        response = await client.get("/api/flows/flows/smoke")
        ports = (await response.json())["ports"]
        python = ports["summarize"]
        assert python["inputs"] == {"users": "any"}
        assert python["outputs"] == {"summary": "any", "error": "object"}
        assert python["required_inputs"] == ["users"]
        start = ports["start"]
        assert start["inputs"] == {}
        assert "out" in start["outputs"]
        end = ports["end"]
        assert set(end["inputs"]) == {"summary", "failed_check", "python_error"}
        assert end["required_inputs"] == ["summary"]

    with_client(ws, scenario)


def test_run_smoke_flow_to_passed_with_replay(tmp_path):
    ws = make_scaffold_ws(tmp_path)

    async def scenario(client):
        started = await start_run(client, "flows/smoke")
        assert started["state"] == "running"
        status = await wait_finished(client, started["run_id"])
        assert status["state"] == "passed"
        assert status["asserts"]["failed"] == 0

        log_path = ws.root / started["log"]
        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        replay = await client.get(f"/api/runs/{started['run_id']}/events")
        events = (await replay.json())["events"]
        assert len(events) == len(lines)
        assert events[0]["event"] == "run_started"
        assert events[-1]["event"] == "run_finished"

        history = await client.get("/api/runs", query={"flow": "flows/smoke"})
        runs = (await history.json())["runs"]
        assert {"run_id": started["run_id"], "state": "passed"} in runs

    with_client(ws, scenario)


def test_prepare_gate_maps_to_http_statuses(tmp_path):
    ws = make_scaffold_ws(tmp_path)
    # E004: two edges into one input — a check error, blocks with 400
    bad = ws.root / "flows" / "bad"
    bad.mkdir()
    (bad / "flow.yaml").write_text(
        """\
schema: "napflow/v1"
flow: {name: "bad"}
nodes:
  - {id: "start", type: "start", config: {ports: []}}
  - id: "note1"
    type: "log"
    config: {}
  - {id: "end", type: "end", config: {ports: [{name: "out"}]}}
edges:
  - {from: "start.out", to: "end.out"}
  - {from: "note1.out", to: "end.out"}
""",
        encoding="utf-8",
    )

    async def scenario(client):
        unknown = await client.post(
            "/api/runs", content=JSONContent({"flow": "flows/nope"})
        )
        assert unknown.status == 404

        check_blocked = await client.post(
            "/api/runs", content=JSONContent({"flow": "flows/bad"})
        )
        assert check_blocked.status == 400
        payload = await check_blocked.json()
        assert payload["error"] == "check"
        assert any(d["code"] == "E004" for d in payload["diagnostics"])
        assert all("severity" in d and "file" in d for d in payload["diagnostics"])

        bad_env = await client.post(
            "/api/runs", content=JSONContent({"flow": "flows/smoke", "env": "nope"})
        )
        assert bad_env.status == 400
        assert (await bad_env.json())["error"] == "env_not_found"

        bad_inputs = await client.post(
            "/api/runs",
            content=JSONContent({"flow": "flows/smoke", "inputs": ["not-a-dict"]}),
        )
        assert bad_inputs.status == 400

        no_flow = await client.post("/api/runs", content=JSONContent({}))
        assert no_flow.status == 400

    with_client(ws, scenario)


def test_unknown_run_endpoints_404(tmp_path):
    ws = make_scaffold_ws(tmp_path)

    async def scenario(client):
        for path in ("/api/runs/nope", "/api/runs/nope/events"):
            response = await client.get(path)
            assert response.status == 404
        response = await client.post("/api/runs/nope/abort")
        assert response.status == 404

    with_client(ws, scenario)


DELAY_FLOW = """\
schema: "napflow/v1"
flow: {name: "slow"}
nodes:
  - {id: "start", type: "start", config: {ports: []}}
  - id: "nap"
    type: "delay"
    config: {seconds: 30}
  - {id: "end", type: "end", config: {ports: [{name: "done"}]}}
edges:
  - {from: "start.out", to: "nap.in"}
  - {from: "nap.out", to: "end.done"}
"""


def test_abort_running_flow(tmp_path):
    ws = make_scaffold_ws(tmp_path)
    slow = ws.root / "flows" / "slow"
    slow.mkdir()
    (slow / "flow.yaml").write_text(DELAY_FLOW, encoding="utf-8")

    async def scenario(client):
        started = await start_run(client, "flows/slow")
        aborting = await client.post(f"/api/runs/{started['run_id']}/abort")
        assert aborting.status == 202
        status = await wait_finished(client, started["run_id"])
        assert status["state"] == "aborted"
        # aborting a finished run is a no-op reporting the final state
        again = await client.post(f"/api/runs/{started['run_id']}/abort")
        assert again.status == 200
        assert (await again.json())["state"] == "aborted"

    with_client(ws, scenario)


def test_start_port_inputs_bind(tmp_path):
    ws = make_scaffold_ws(tmp_path)
    hello = ws.root / "flows" / "hello"
    hello.mkdir()
    (hello / "flow.yaml").write_text(
        """\
schema: "napflow/v1"
flow: {name: "hello"}
nodes:
  - id: "start"
    type: "start"
    config:
      ports:
        - {name: "msg", type: "string"}
  - {id: "end", type: "end", config: {ports: [{name: "echo"}]}}
edges:
  - {from: "start.out", to: "end.echo"}
""",
        encoding="utf-8",
    )

    async def scenario(client):
        started = await start_run(client, "flows/hello", inputs={"msg": "hi"})
        status = await wait_finished(client, started["run_id"])
        assert status["state"] == "passed"
        replay = await client.get(f"/api/runs/{started['run_id']}/events")
        finished = (await replay.json())["events"][-1]
        assert finished["end_outputs"] == {"echo": {"msg": "hi"}}

    with_client(ws, scenario)


def test_placeholder_page_without_a_ui_bundle(tmp_path, monkeypatch):
    """No static dir (e.g. a source checkout that never ran the UI
    build) ⇒ `/` serves the plain placeholder, not a 404."""
    import napflow.server.app as server_app

    monkeypatch.setattr(server_app, "STATIC_DIR", tmp_path / "no-static")
    ws = make_scaffold_ws(tmp_path)

    async def scenario(client):
        response = await client.get("/")
        assert response.status == 200
        assert "napflow server is running" in await response.text()

    with_client(ws, scenario)


def test_ui_bundle_served_with_spa_fallback(tmp_path, monkeypatch):
    """With a bundle in place (NFR-03: it ships inside the wheel), `/`
    serves index.html, assets are reachable, and unknown client-side
    routes fall back to index.html (SPA history API)."""
    import napflow.server.app as server_app

    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text(
        "<!doctype html><title>bundle</title>", encoding="utf-8"
    )
    (static / "assets" / "app.js").write_text("// js", encoding="utf-8")
    monkeypatch.setattr(server_app, "STATIC_DIR", static)
    ws = make_scaffold_ws(tmp_path)

    async def scenario(client):
        index = await client.get("/")
        assert index.status == 200
        assert "bundle" in await index.text()
        asset = await client.get("/assets/app.js")
        assert asset.status == 200
        fallback = await client.get("/flows/some/client/route")
        assert fallback.status == 200
        assert "bundle" in await fallback.text()

    with_client(ws, scenario)


# --------------------------------------------------------------------------
# Write path (S4/M4): PUT flows + code, etags (FR-1003)


async def _flow_detail(client: TestClient, identity: str) -> dict:
    response = await client.get(f"/api/flows/{identity}")
    assert response.status == 200, await response.text()
    return await response.json()


async def _put_flow(client: TestClient, identity: str, **body):
    return await client.put(f"/api/flows/{identity}", content=JSONContent(body))


def test_flow_detail_carries_etags_and_functions(tmp_path):
    ws = make_scaffold_ws(tmp_path)

    async def scenario(client):
        detail = await _flow_detail(client, "flows/smoke")
        assert detail["etag"]
        assert detail["code_etag"]  # smoke has a nodes.py
        assert "summarize" in detail["functions"]
        # the scaffold main flow ships a stub nodes.py — empty fn list,
        # which the dropdown must distinguish from None (no/broken file)
        main = await _flow_detail(client, "flows/main")
        assert main["etag"]
        assert main["code_etag"]
        assert main["functions"] == []

    with_client(ws, scenario)


def test_put_flow_noop_save_is_byte_identical(tmp_path):
    """The canvas PUTs back the dump it received unchanged ⇒ the file
    must not change at all (FR-1003's no-op guarantee via the merge)."""
    ws = make_scaffold_ws(tmp_path)
    file = ws.root / "flows" / "smoke" / "flow.yaml"
    before = file.read_bytes()

    async def scenario(client):
        detail = await _flow_detail(client, "flows/smoke")
        response = await _put_flow(
            client, "flows/smoke", flow=detail["flow"], base_etag=detail["etag"]
        )
        assert response.status == 200, await response.text()
        payload = await response.json()
        assert payload["etag"] == detail["etag"]  # bytes unchanged ⇒ same hash
        assert file.read_bytes() == before

    with_client(ws, scenario)


def test_put_flow_layout_move_persists(tmp_path):
    ws = make_scaffold_ws(tmp_path)
    file = ws.root / "flows" / "main" / "flow.yaml"

    async def scenario(client):
        detail = await _flow_detail(client, "flows/main")
        detail["flow"]["layout"]["start"] = [80.0, 200.0]
        response = await _put_flow(
            client, "flows/main", flow=detail["flow"], base_etag=detail["etag"]
        )
        assert response.status == 200
        payload = await response.json()
        assert payload["etag"] != detail["etag"]
        assert payload["diagnostics"] == []
        assert "start: [80, 200]" in file.read_text(encoding="utf-8")

    with_client(ws, scenario)


def test_put_flow_etag_conflict_and_force(tmp_path):
    """409 on a stale base_etag; `force` overrides (last-write-wins is
    the v1 conflict ceiling, FR-1004)."""
    ws = make_scaffold_ws(tmp_path)
    file = ws.root / "flows" / "main" / "flow.yaml"

    async def scenario(client):
        detail = await _flow_detail(client, "flows/main")
        # an external edit lands between GET and PUT
        file.write_text(
            file.read_text(encoding="utf-8") + "# external edit\n", encoding="utf-8"
        )
        stale = await _put_flow(
            client, "flows/main", flow=detail["flow"], base_etag=detail["etag"]
        )
        assert stale.status == 409
        conflict = await stale.json()
        assert conflict["error"] == "etag_conflict"
        assert conflict["etag"] != detail["etag"]  # the current hash, for reload
        forced = await _put_flow(client, "flows/main", flow=detail["flow"], force=True)
        assert forced.status == 200

    with_client(ws, scenario)


def test_put_flow_invalid_payload_400_with_diagnostics(tmp_path):
    ws = make_scaffold_ws(tmp_path)
    file = ws.root / "flows" / "main" / "flow.yaml"
    before = file.read_bytes()

    async def scenario(client):
        detail = await _flow_detail(client, "flows/main")
        detail["flow"]["nodes"].append({"id": "bad id!", "type": "nope"})
        response = await _put_flow(
            client, "flows/main", flow=detail["flow"], base_etag=detail["etag"]
        )
        assert response.status == 400
        payload = await response.json()
        assert payload["error"] == "validation"
        assert payload["diagnostics"]
        assert file.read_bytes() == before  # nothing written

        not_an_object = await _put_flow(client, "flows/main", flow="nope")
        assert not_an_object.status == 400

    with_client(ws, scenario)


def test_put_flow_with_check_warnings_saves_and_reports_them(tmp_path):
    """E-codes gate RUNS, not saves — the canvas must persist
    work-in-progress flows (M4 pin). W-codes come back on the response."""
    ws = make_scaffold_ws(tmp_path)

    async def scenario(client):
        detail = await _flow_detail(client, "flows/main")
        # a request node with an unconnected error port ⇒ W103 warning
        detail["flow"]["nodes"].insert(
            1, {"id": "req", "type": "request", "config": {"url": "http://x"}}
        )
        detail["flow"]["edges"].append({"from": "start.out", "to": "req.trigger"})
        response = await _put_flow(
            client, "flows/main", flow=detail["flow"], base_etag=detail["etag"]
        )
        assert response.status == 200
        payload = await response.json()
        assert any(d["code"] == "W103" for d in payload["diagnostics"])
        # and the detail round-trips with the node present
        after = await _flow_detail(client, "flows/main")
        assert any(n["id"] == "req" for n in after["flow"]["nodes"])

    with_client(ws, scenario)


def test_flow_detail_with_check_errors_still_returns_the_model(tmp_path):
    """M4 pin: the editor keeps working on an E-code flow — GET returns
    the model + error diagnostics instead of 400 (only runs are gated)."""
    ws = make_scaffold_ws(tmp_path)
    bad = ws.root / "flows" / "badcheck"
    bad.mkdir()
    (bad / "flow.yaml").write_text(
        """\
schema: "napflow/v1"
flow: {name: "badcheck"}
nodes:
  - {id: "start", type: "start", config: {ports: []}}
  - {id: "note1", type: "log", config: {}}
  - {id: "end", type: "end", config: {ports: [{name: "out"}]}}
edges:
  - {from: "start.out", to: "end.out"}
  - {from: "note1.out", to: "end.out"}
""",
        encoding="utf-8",
    )

    async def scenario(client):
        detail = await _flow_detail(client, "flows/badcheck")
        assert any(d["code"] == "E004" for d in detail["diagnostics"])
        assert [n["id"] for n in detail["flow"]["nodes"]] == ["start", "note1", "end"]

    with_client(ws, scenario)


def test_write_endpoints_reject_path_escapes(tmp_path):
    ws = make_scaffold_ws(tmp_path)

    async def scenario(client):
        for path in (
            "/api/flows/flows/../../etc",
            "/api/code/flows/../../etc",
            "/api/etags/flows/../../etc",
        ):
            get = await client.get(path)
            assert get.status in (400, 404)  # never 200, never escapes
        put = await client.put(
            "/api/flows/flows/../../etc", content=JSONContent({"flow": {}})
        )
        assert put.status == 400

    with_client(ws, scenario)


def test_code_get_put_roundtrip_with_syntax_report(tmp_path):
    ws = make_scaffold_ws(tmp_path)
    path = ws.root / "flows" / "smoke" / "nodes.py"

    async def scenario(client):
        got = await client.get("/api/code/flows/smoke")
        assert got.status == 200
        code = await got.json()
        assert code["exists"] is True
        assert "def summarize" in code["code"]
        assert code["syntax_error"] is None
        assert "summarize" in code["functions"]

        # broken code SAVES anyway (last-write-wins) but reports the error
        broken = code["code"] + "\ndef oops(:\n"
        saved = await client.put(
            "/api/code/flows/smoke",
            content=JSONContent({"code": broken, "base_etag": code["etag"]}),
        )
        assert saved.status == 200
        payload = await saved.json()
        assert payload["syntax_error"]["line"]
        assert payload["functions"] is None  # unparseable ⇒ no fn list
        assert path.read_text(encoding="utf-8") == broken

        # stale etag conflicts now
        stale = await client.put(
            "/api/code/flows/smoke",
            content=JSONContent({"code": code["code"], "base_etag": code["etag"]}),
        )
        assert stale.status == 409
        # fix it back with the fresh etag
        fixed = await client.put(
            "/api/code/flows/smoke",
            content=JSONContent({"code": code["code"], "base_etag": payload["etag"]}),
        )
        assert fixed.status == 200
        assert (await fixed.json())["syntax_error"] is None

    with_client(ws, scenario)


def test_code_put_creates_nodes_py(tmp_path):
    """PUT can CREATE nodes.py — a flow gains python nodes from the
    canvas without touching a terminal."""
    ws = make_scaffold_ws(tmp_path)
    bare = ws.root / "flows" / "bare"
    bare.mkdir()
    (bare / "flow.yaml").write_text(
        """\
schema: "napflow/v1"
flow: {name: "bare"}
nodes:
  - {id: "start", type: "start", config: {ports: []}}
  - {id: "end", type: "end", config: {ports: [{name: "out"}]}}
edges:
  - {from: "start.out", to: "end.out"}
""",
        encoding="utf-8",
    )
    path = bare / "nodes.py"

    async def scenario(client):
        got = await client.get("/api/code/flows/bare")
        code = await got.json()
        assert code == {
            "identity": "flows/bare",
            "exists": False,
            "code": "",
            "etag": None,
            "syntax_error": None,
            "functions": None,
        }
        created = await client.put(
            "/api/code/flows/bare",
            content=JSONContent({"code": "def go(x):\n    return x\n"}),
        )
        assert created.status == 200, await created.text()
        payload = await created.json()
        assert payload["functions"] == ["go"]
        assert path.is_file()

        missing = await client.get("/api/code/flows/nope")
        assert missing.status == 404

    with_client(ws, scenario)


def test_etags_poll_endpoint(tmp_path):
    ws = make_scaffold_ws(tmp_path)

    async def scenario(client):
        response = await client.get("/api/etags/flows/smoke")
        assert response.status == 200
        payload = await response.json()
        detail = await _flow_detail(client, "flows/smoke")
        assert payload["etag"] == detail["etag"]
        assert payload["code_etag"] == detail["code_etag"]

        missing = await client.get("/api/etags/flows/nope")
        assert missing.status == 404

    with_client(ws, scenario)


# --------------------------------------------------------------------------
# Subflow UX (S4/M6, FR-1007): detail refs/used_by + clone endpoint

_PARENT_FLOW = """\
schema: napflow/v1
flow: {name: parent}
nodes:
  - {id: start, type: start}
  - {id: sub, type: flow, config: {flow: flows/child}}
  - {id: tail, type: log, config: {label: "saw {{ nodes.sub.done }}"}}
  - {id: end, type: end, config: {ports: [{name: r, required: false}]}}
edges:
  - {from: start.out, to: sub.val}
  - {from: sub.done, to: tail.in}
  - {from: tail.out, to: end.r}
"""

_CHILD_FLOW = """\
schema: napflow/v1
flow: {name: child}
nodes:
  - {id: start, type: start, config: {ports: [{name: val, default: 1}]}}
  - {id: end, type: end, config: {ports: [{name: done, required: false}]}}
edges:
  - {from: start.val, to: end.done}
"""


def _write_flow(ws: Workspace, identity: str, content: str) -> None:
    directory = ws.root / identity
    directory.mkdir(parents=True)
    (directory / "flow.yaml").write_text(content, encoding="utf-8")


def test_flow_detail_template_refs_and_used_by(tmp_path):
    ws = make_scaffold_ws(tmp_path)
    _write_flow(ws, "flows/parent", _PARENT_FLOW)
    _write_flow(ws, "flows/child", _CHILD_FLOW)

    async def scenario(client):
        parent = await _flow_detail(client, "flows/parent")
        # ghost-wire data: tail's label reaches into sub's output
        assert parent["template_refs"] == {"tail": ["sub"]}
        assert parent["used_by"] == []

        child = await _flow_detail(client, "flows/child")
        assert child["template_refs"] == {}
        assert child["used_by"] == [{"identity": "flows/parent", "nodes": ["sub"]}]

    with_client(ws, scenario)


def test_clone_flow_forks_the_folder(tmp_path):
    ws = make_scaffold_ws(tmp_path)

    async def scenario(client):
        response = await client.post(
            "/api/flows/clone",
            content=JSONContent({"source": "flows/smoke", "dest": "flows/smoke_copy"}),
        )
        assert response.status == 201, await response.text()
        assert (await response.json())["identity"] == "flows/smoke_copy"
        # the FOLDER forks (D09): nodes.py travels with flow.yaml
        assert (ws.root / "flows/smoke_copy/flow.yaml").is_file()
        assert (ws.root / "flows/smoke_copy/nodes.py").is_file()
        listed = await client.get("/api/flows")
        identities = [f["identity"] for f in (await listed.json())["flows"]]
        assert "flows/smoke_copy" in identities

    with_client(ws, scenario)


def test_clone_flow_rejections(tmp_path):
    ws = make_scaffold_ws(tmp_path)

    async def scenario(client):
        async def clone(source, dest):
            return await client.post(
                "/api/flows/clone",
                content=JSONContent({"source": source, "dest": dest}),
            )

        assert (await clone("flows/nope", "flows/copy")).status == 404
        # dest collides with an existing folder: the second clone loses
        assert (await clone("flows/smoke", "flows/copy")).status == 201
        assert (await clone("flows/smoke", "flows/copy")).status == 409
        # cloning onto the source itself is a bad request, not a collision
        assert (await clone("flows/smoke", "flows/smoke")).status == 400
        # dest outside flows.root would be invisible to discovery
        assert (await clone("flows/smoke", "elsewhere/copy")).status == 400
        # dest nested inside source would recurse the copy
        assert (await clone("flows/smoke", "flows/smoke/inner")).status == 400
        # identity escapes rejected on both ends
        assert (await clone("../smoke", "flows/copy")).status == 400
        assert (await clone("flows/smoke", "flows/../../out")).status == 400
        bad_body = await client.post(
            "/api/flows/clone", content=JSONContent({"source": "flows/smoke"})
        )
        assert bad_body.status == 400

    with_client(ws, scenario)


# --------------------------------------------------------------------------
# WebSocket (D13: frames identical to JSONL lines)


def test_ws_frames_are_the_jsonl_lines_verbatim(tmp_path):
    ws = make_scaffold_ws(tmp_path)

    async def scenario(client):
        started = await start_run(client, "flows/smoke")
        frames = []
        async with client.websocket_connect(f"/ws/runs/{started['run_id']}") as sock:
            while True:
                frame = await sock.receive_text()
                frames.append(frame)
                if json.loads(frame)["event"] == "run_finished":
                    break
        await wait_finished(client, started["run_id"])
        log_path = ws.root / started["log"]
        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        assert frames == lines  # byte-identical: replay = re-read (D13)

    with_client(ws, scenario)


def test_ws_replays_finished_runs_from_the_jsonl(tmp_path):
    ws = make_scaffold_ws(tmp_path)

    async def scenario(client):
        started = await start_run(client, "flows/smoke")
        await wait_finished(client, started["run_id"])
        frames = []
        async with client.websocket_connect(f"/ws/runs/{started['run_id']}") as sock:
            while True:
                message = await sock.receive()
                if message["type"] == "websocket.close":
                    break
                frames.append(message["text"])
        log_path = ws.root / started["log"]
        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        assert frames == lines

    with_client(ws, scenario)


def test_ws_unknown_run_closes_4404(tmp_path):
    ws = make_scaffold_ws(tmp_path)

    async def scenario(client):
        async with client.websocket_connect("/ws/runs/nope") as sock:
            message = await sock.receive()
            assert message["type"] == "websocket.close"
            assert message["code"] == 4404

    with_client(ws, scenario)


# --------------------------------------------------------------------------
# TR-9 through-the-server half: a python-node flow via a REAL uvicorn
# server on a localhost socket. Windows CI is the target audience —
# Proactor + worker subprocess pipes + ASGI/WS stack in one process.


def test_tr9_python_flow_through_real_uvicorn_server(tmp_path):
    import niquests
    import uvicorn
    import websockets

    ws = make_scaffold_ws(tmp_path)
    app = build_app(ws)
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=0, log_level="warning", ws="websockets-sansio"
        )
    )

    async def scenario():
        serving = asyncio.get_running_loop().create_task(server.serve())
        while not server.started:
            assert not serving.done(), "uvicorn failed to start"
            await asyncio.sleep(0.02)
        port = server.servers[0].sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        try:
            async with niquests.AsyncSession() as http:
                posted = await http.post(
                    f"{base}/api/runs", json={"flow": "flows/smoke"}
                )
                assert posted.status_code == 202
                started = posted.json()

                events = []
                async with websockets.connect(
                    f"ws://127.0.0.1:{port}/ws/runs/{started['run_id']}"
                ) as sock:
                    async for frame in sock:
                        record = json.loads(frame)
                        events.append(record["event"])
                        if record["event"] == "run_finished":
                            assert record["state"] == "passed"
                            break

                assert "node_fired" in events
                status = await http.get(f"{base}/api/runs/{started['run_id']}")
                for _ in range(200):
                    if status.json()["state"] != "running":
                        break
                    await asyncio.sleep(0.02)
                    status = await http.get(f"{base}/api/runs/{started['run_id']}")
                assert status.json()["state"] == "passed"
        finally:
            server.should_exit = True
            await asyncio.wait_for(serving, timeout=10)

    asyncio.run(scenario())


# --------------------------------------------------------------------------
# `napf ui` port picking


def test_pick_ui_port_prefers_the_default():
    # anything in the scan span is acceptable — the machine running the
    # suite may legitimately have ports taken (including 6273 itself)
    assert _pick_ui_port(None) in range(DEFAULT_UI_PORT, DEFAULT_UI_PORT + 20)


def test_pick_ui_port_scans_past_a_taken_default(monkeypatch):
    import napflow.cli.main as cli_main

    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        controlled_default = taken.getsockname()[1]
        monkeypatch.setattr(cli_main, "DEFAULT_UI_PORT", controlled_default)
        picked = _pick_ui_port(None)
        assert picked != controlled_default
        assert picked in range(controlled_default + 1, controlled_default + 20)


def test_pick_ui_port_explicit_busy_port_fails():
    import typer

    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        busy = taken.getsockname()[1]
        with pytest.raises(typer.Exit) as excinfo:
            _pick_ui_port(busy)
        assert excinfo.value.exit_code == 2


def test_pick_ui_port_explicit_free_port_used():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    assert _pick_ui_port(free) == free
