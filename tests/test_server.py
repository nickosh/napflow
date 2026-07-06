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


def test_placeholder_page_until_ui_bundle_lands(tmp_path):
    ws = make_scaffold_ws(tmp_path)

    async def scenario(client):
        response = await client.get("/")
        assert response.status == 200
        assert "napflow" in await response.text()

    with_client(ws, scenario)


# --------------------------------------------------------------------------
# WebSocket (D13: frames identical to JSONL lines)


def test_ws_frames_are_the_jsonl_lines_verbatim(tmp_path):
    ws = make_scaffold_ws(tmp_path)

    async def scenario(client):
        started = await start_run(client, "flows/smoke")
        frames = []
        async with client.websocket_connect(
            f"/ws/runs/{started['run_id']}"
        ) as sock:
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
        async with client.websocket_connect(
            f"/ws/runs/{started['run_id']}"
        ) as sock:
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
