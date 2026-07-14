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
import os
import socket
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from blacksheep.contents import Content, JSONContent
from blacksheep.testing import TestClient

import napflow.core.events as events_module
import napflow.server.app as app_module
import napflow.server.runs as runs_module
from napflow.cli.main import DEFAULT_UI_PORT, _pick_ui_port
from napflow.cli.scaffold import scaffold_workspace
from napflow.core.engine import RunResult
from napflow.core.events import (
    HISTORY_FEATURE_CONTENT_BLOBS,
    HISTORY_FORMAT,
    apply_retention,
    begin_history_reader,
)
from napflow.core.workspace import Workspace, load_workspace
from napflow.server import build_app
from napflow.server.app import (
    WS_HISTORY_FORMAT,
    WS_REQUEST_ORIGIN,
    _read_records,
    _send_history_range,
    _send_ws_record,
    _SourceWriteCoordinator,
    _stream_run_websocket,
)
from napflow.server.runs import (
    SUBSCRIBER_QUEUE_LIMIT,
    SUBSCRIBER_RESYNC,
    SUBSCRIBERS_PER_RUN_LIMIT,
    ActiveRun,
    RunManager,
    SubscriberLimitError,
    _LiveSink,
)

# --------------------------------------------------------------------------
# Harness — suite style: sync tests drive async scenarios via asyncio.run


def make_scaffold_ws(tmp_path: Path) -> Workspace:
    """A real `napf init` workspace — flows/smoke is the offline
    fixture→python→assert flow (EC34), i.e. it exercises the worker."""
    wsdir = tmp_path / "ws"
    list(scaffold_workspace(wsdir))
    return load_workspace(wsdir)


def make_retained_scaffold_ws(tmp_path: Path, history: int = 1) -> Workspace:
    wsdir = tmp_path / "ws"
    list(scaffold_workspace(wsdir))
    manifest = wsdir / "napflow.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        + f"defaults:\n  run:\n    history: {history}\n",
        encoding="utf-8",
    )
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


def test_server_replay_preserves_raw_declared_secret_for_local_inspection(tmp_path):
    ws = make_scaffold_ws(tmp_path)
    secret = "server-secret-token"
    dev_env = ws.root / "envs" / "dev.env"
    dev_env.write_text(
        dev_env.read_text(encoding="utf-8") + f"\nAPI_TOKEN={secret}\n",
        encoding="utf-8",
    )
    flow_dir = ws.root / "flows" / "secret"
    flow_dir.mkdir()
    (flow_dir / "flow.yaml").write_text(
        """\
schema: "napflow/v1"
flow:
  name: "secret"
nodes:
  - id: "start"
    type: "start"
    config:
      ports:
        - {name: "msg", type: "string", default: "{{ env.API_TOKEN }}"}
  - id: "show"
    type: "log"
    config: {}
  - id: "end"
    type: "end"
    config:
      ports:
        - {name: "result"}
edges:
  - {from: "start.msg", to: "show.in"}
  - {from: "show.out", to: "end.result"}
""",
        encoding="utf-8",
    )

    async def scenario(client):
        started = await start_run(client, "flows/secret")
        status = await wait_finished(client, started["run_id"])
        assert status["state"] == "passed"

        log_path = ws.root / started["log"]
        canonical = [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
        ]
        replay = await client.get(f"/api/runs/{started['run_id']}/events")
        events = (await replay.json())["events"]

        assert events == canonical
        assert (
            next(event for event in events if event["event"] == "log")["value"]
            == secret
        )
        assert events[-1]["end_outputs"] == {"result": secret}

    with_client(ws, scenario)


def test_server_sink_close_failure_still_finishes_lifecycle(tmp_path, monkeypatch):
    ws = make_scaffold_ws(tmp_path)

    def fail_close(_self):
        raise OSError("close failed")

    monkeypatch.setattr(runs_module._LiveSink, "close", fail_close)

    async def scenario(client):
        started = await start_run(client, "flows/smoke")
        status = await wait_finished(client, started["run_id"])
        assert status["state"] == "passed"

        log_path = ws.root / started["log"]
        active = log_path.with_name(f"{started['run_id']}.active")
        deadline = asyncio.get_running_loop().time() + 2
        while active.exists() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)

        assert not active.exists()
        assert log_path.with_name(f"{started['run_id']}.incomplete").exists()
        assert not log_path.with_name(f"{started['run_id']}.complete.json").exists()

    with_client(ws, scenario)


def test_server_fresh_cancellation_during_adapter_reclose_propagates_after_cleanup(
    tmp_path, monkeypatch
):
    ws = make_scaffold_ws(tmp_path)
    original_close = events_module.EventStream.close
    close_calls = 0

    def cancel_second_close(self):
        nonlocal close_calls
        close_calls += 1
        if close_calls == 2:
            raise asyncio.CancelledError
        return original_close(self)

    monkeypatch.setattr(events_module.EventStream, "close", cancel_second_close)

    async def scenario(client):
        started = await start_run(client, "flows/smoke")
        status = await wait_finished(client, started["run_id"])
        assert status["state"] == "passed"

        log_path = ws.root / started["log"]
        active = log_path.with_name(f"{started['run_id']}.active")
        deadline = asyncio.get_running_loop().time() + 2
        while active.exists() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)

        assert not active.exists()
        assert log_path.with_name(f"{started['run_id']}.incomplete").exists()
        assert not log_path.with_name(f"{started['run_id']}.complete.json").exists()

    with_client(ws, scenario)


def test_server_retention_runs_after_completion_and_evicts_registry(tmp_path):
    ws = make_retained_scaffold_ws(tmp_path)

    async def scenario(client):
        first = await start_run(client, "flows/smoke")
        await wait_finished(client, first["run_id"])
        second = await start_run(client, "flows/smoke")
        await wait_finished(client, second["run_id"])

        history = await client.get("/api/runs", query={"flow": "flows/smoke"})
        assert (await history.json())["runs"] == [
            {"run_id": second["run_id"], "state": "passed"}
        ]
        evicted = await client.get(f"/api/runs/{first['run_id']}")
        assert evicted.status == 404
        runs_dir = ws.root / ".napflow" / "runs" / "flows" / "smoke"
        assert list(runs_dir.glob("*.active")) == []
        assert list(runs_dir.glob("*.deleting")) == []

    with_client(ws, scenario)


def test_live_subscriber_queue_is_bounded_and_collapses_to_resync():
    manager = RunManager()
    run = SimpleNamespace(
        finished=False,
        last_seq=0,
        subscribers=set(),
        replay_readers=0,
        presentation_changed=asyncio.Event(),
    )
    through_seq, subscriber = manager.subscribe(run)
    sink = _LiveSink()
    sink.run = run

    for seq in range(1, SUBSCRIBER_QUEUE_LIMIT + 2):
        sink.write({"event": "node_fired", "seq": seq})

    assert through_seq == 0
    assert run.last_seq == SUBSCRIBER_QUEUE_LIMIT + 1
    assert subscriber.queue.maxsize == SUBSCRIBER_QUEUE_LIMIT
    assert subscriber.queue.qsize() == 1
    assert subscriber.queue.get_nowait() is SUBSCRIBER_RESYNC
    assert subscriber.overflowed


def test_live_subscriber_count_is_bounded():
    manager = RunManager()
    run = SimpleNamespace(
        finished=False,
        last_seq=0,
        subscribers=set(),
        replay_readers=0,
        presentation_changed=asyncio.Event(),
    )
    for _ in range(SUBSCRIBERS_PER_RUN_LIMIT):
        manager.subscribe(run)

    with pytest.raises(SubscriberLimitError):
        manager.subscribe(run)


def test_run_history_finalization_drops_engine_state(tmp_path, monkeypatch):
    finalized = False

    def finalize(_opened, *, completed):
        nonlocal finalized
        assert completed
        finalized = True
        return []

    monkeypatch.setattr(runs_module, "finalize_run_history", finalize)

    class Flow:
        async def execute(self):
            return RunResult(
                state="passed",
                end_outputs={},
                asserts_passed=0,
                asserts_failed=0,
                unhandled_errors=[],
                nodes_never_fired=[],
                duration_ms=1.0,
            )

    class Stream:
        def close(self):
            pass

    async def scenario():
        manager = RunManager()
        run = ActiveRun("run", "flows/run", tmp_path / "run.jsonl", Flow())
        _through_seq, subscriber = manager.subscribe(run)
        drive = asyncio.create_task(
            manager._drive(run, SimpleNamespace(stream=Stream()))
        )
        await drive
        assert run.finished
        assert run.flow_run is None
        assert finalized
        assert subscriber.queue.get_nowait() is runs_module.SUBSCRIBER_END
        manager.unsubscribe(run, subscriber)

    asyncio.run(scenario())


def test_server_shutdown_releases_deferred_resync_reader(tmp_path):
    run_id = "20260712-100000-000001"
    log = tmp_path / f"{run_id}.jsonl"
    log.write_text(
        json.dumps({"event": "run_finished", "run_id": run_id}) + "\n",
        encoding="utf-8",
    )

    async def scenario():
        manager = RunManager()
        lease = begin_history_reader(log)
        manager.defer_history_reader_release(log, lease, history_limit=20)
        assert lease.exists()
        await manager.shutdown()
        assert not lease.exists()
        assert manager._deferred_readers == {}

    asyncio.run(scenario())


def test_server_evicts_registry_when_interrupted_deletion_resumes(
    tmp_path, monkeypatch
):
    ws = make_retained_scaffold_ws(tmp_path)
    real_remove = events_module._remove_owned_path
    failed_once = False

    def fail_first_tombstone_removal(path):
        nonlocal failed_once
        if path.name.endswith(".deleting") and not failed_once:
            failed_once = True
            raise OSError("simulated sharing violation")
        real_remove(path)

    monkeypatch.setattr(
        events_module, "_remove_owned_path", fail_first_tombstone_removal
    )

    async def scenario(client):
        first = await start_run(client, "flows/smoke")
        await wait_finished(client, first["run_id"])
        second = await start_run(client, "flows/smoke")
        await wait_finished(client, second["run_id"])
        assert failed_once

        # A later retention pass resumes the first tombstone and must report
        # that deletion back to the registry as well as its newly claimed unit.
        third = await start_run(client, "flows/smoke")
        await wait_finished(client, third["run_id"])
        for deleted in (first, second):
            response = await client.get(f"/api/runs/{deleted['run_id']}")
            assert response.status == 404

    with_client(ws, scenario)


def test_history_listing_recognizes_large_completion_before_partial_tail(tmp_path):
    ws = make_scaffold_ws(tmp_path)
    run_id = "19700101-000000-abcdef"
    log_path = ws.root / ".napflow" / "runs" / "flows" / "smoke" / f"{run_id}.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_bytes(
        json.dumps({"event": "run_started", "seq": 1}).encode()
        + b"\n"
        + json.dumps(
            {"event": "run_finished", "state": "failed", "detail": "z" * 80_000}
        ).encode()
        + b'\n{"event":"partial"'
    )

    async def scenario(client):
        history = await client.get("/api/runs", query={"flow": "flows/smoke"})
        assert history.status == 200
        assert (await history.json())["runs"] == [{"run_id": run_id, "state": "failed"}]

    with_client(ws, scenario)


@pytest.mark.parametrize(
    ("case", "accepted"),
    [
        ("supported", True),
        ("missing", True),
        ("legacy-with-features", False),
        ("missing-features", True),
        ("newer", False),
        ("malformed", False),
        ("non-string", False),
        ("null-format", False),
        ("long-malformed", False),
        ("bad-seq", False),
        ("bool-seq", False),
        ("float-seq", False),
        ("wrong-event", False),
        ("unknown-feature", False),
        ("surrogate-feature", False),
        ("null-features", False),
        ("duplicate-features", False),
    ],
)
def test_history_replay_validates_envelope_for_rest_and_finished_ws(
    tmp_path, case, accepted
):
    ws = make_scaffold_ws(tmp_path)

    async def scenario(client):
        started = await start_run(client, "flows/smoke")
        await wait_finished(client, started["run_id"])
        log_path = ws.root / started["log"]
        records = [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if case == "missing":
            records[0].pop("format")
            records[0].pop("features")
        elif case == "legacy-with-features":
            records[0].pop("format")
        elif case == "missing-features":
            records[0].pop("features")
        elif case == "newer":
            records[0]["format"] = "napflow-run/2"
        elif case == "malformed":
            records[0]["format"] = "postman-run/1"
        elif case == "non-string":
            records[0]["format"] = 1
        elif case == "null-format":
            records[0]["format"] = None
        elif case == "long-malformed":
            records[0]["format"] = "x" * 1_000
        elif case == "bad-seq":
            records[0]["seq"] = 2
        elif case == "bool-seq":
            records[0]["seq"] = True
        elif case == "float-seq":
            records[0]["seq"] = 1.0
        elif case == "wrong-event":
            records[0]["event"] = "node_fired"
        elif case == "unknown-feature":
            records[0]["features"] = ["content-blobs/2"]
        elif case == "surrogate-feature":
            records[0]["features"] = ["\ud800"]
        elif case == "null-features":
            records[0]["features"] = None
        elif case == "duplicate-features":
            records[0]["features"] = ["x/1", "x/1"]
        else:
            assert records[0]["format"] == HISTORY_FORMAT
        log_path.write_text(
            "".join(f"{json.dumps(record)}\n" for record in records),
            encoding="utf-8",
        )

        replay = await client.get(f"/api/runs/{started['run_id']}/events")
        if accepted:
            assert replay.status == 200
            assert (await replay.json())["events"] == records
        else:
            assert replay.status == 422
            payload = await replay.json()
            assert payload["error"] == "history_format"
            assert payload["message"]

        async with client.websocket_connect(f"/ws/runs/{started['run_id']}") as sock:
            frames = []
            while True:
                message = await sock.receive()
                if message["type"] == "websocket.close":
                    if accepted:
                        assert message["code"] == 1000
                    else:
                        assert message["code"] == WS_HISTORY_FORMAT
                        assert message["reason"]
                        assert len(message["reason"].encode("utf-8")) <= 123
                    break
                frames.append(json.loads(message["text"]))
        assert frames == (records if accepted else [])

    with_client(ws, scenario)


@pytest.mark.parametrize(
    "case",
    ["supported-content-blob", "featureless-marker-literal"],
)
def test_finished_replay_keeps_persisted_message_records_lazy(tmp_path, case):
    ws = make_scaffold_ws(tmp_path)

    async def scenario(client):
        started = await start_run(client, "flows/smoke")
        await wait_finished(client, started["run_id"])
        log_path = ws.root / started["log"]
        records = [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        message = next(
            record for record in records if record["event"] == "message_emitted"
        )

        if case == "supported-content-blob":
            records[0]["features"] = [HISTORY_FEATURE_CONTENT_BLOBS]
            value = {
                "$napflow": {
                    "kind": "blob",
                    "hash": f"sha256:{'a' * 64}",
                    "bytes": 70_000,
                    "media_type": "application/json",
                    "codec": "json",
                }
            }
            message["value"] = value
        else:
            records[0]["features"] = []
            value = {"$napflow": {"kind": "blob", "user_note": "literal data"}}
            message.pop("value", None)
            message["value_preview"] = value

        log_path.write_text(
            "".join(f"{json.dumps(record)}\n" for record in records),
            encoding="utf-8",
        )

        replay = await client.get(f"/api/runs/{started['run_id']}/events")
        assert replay.status == 200
        rest_records = (await replay.json())["events"]
        assert rest_records == records
        rest_message = next(
            record for record in rest_records if record["event"] == "message_emitted"
        )
        assert (
            rest_message[
                "value" if case == "supported-content-blob" else "value_preview"
            ]
            == value
        )

        async with client.websocket_connect(f"/ws/runs/{started['run_id']}") as sock:
            frames = []
            while True:
                frame = await sock.receive()
                if frame["type"] == "websocket.close":
                    assert frame["code"] == 1000
                    break
                frames.append(json.loads(frame["text"]))
        assert frames == records

    with_client(ws, scenario)


def test_empty_completed_history_is_rejected(tmp_path):
    ws = make_scaffold_ws(tmp_path)
    run_id = "19700101-000000-eeeeee"
    log_path = ws.root / ".napflow" / "runs" / "flows" / "smoke" / f"{run_id}.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.touch()

    async def scenario(client):
        replay = await client.get(
            f"/api/runs/{run_id}/events", query={"flow": "flows/smoke"}
        )
        assert replay.status == 422
        payload = await replay.json()
        assert payload["error"] == "history_format"
        assert "empty" in payload["message"]

    with_client(ws, scenario)


def test_empty_live_history_prefix_is_readable(tmp_path):
    log_path = tmp_path / "live.jsonl"
    log_path.touch()
    assert _read_records(log_path, allow_empty=True) == []


def test_history_endpoints_reject_final_log_symlink_escape(tmp_path):
    ws = make_scaffold_ws(tmp_path)
    run_id = "19700101-000000-abcdef"
    runs = ws.root / ".napflow" / "runs" / "flows" / "smoke"
    runs.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside.jsonl"
    outside.write_text('{"event":"run_started","seq":1}\n', encoding="utf-8")
    try:
        (runs / f"{run_id}.jsonl").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/privilege level")

    async def scenario(client):
        listed = await client.get("/api/runs", query={"flow": "flows/smoke"})
        assert listed.status == 400
        assert (await listed.json())["error"] == "workspace_boundary"

        replay = await client.get(
            f"/api/runs/{run_id}/events", query={"flow": "flows/smoke"}
        )
        assert replay.status == 400
        assert (await replay.json())["error"] == "workspace_boundary"

    with_client(ws, scenario)


def test_history_replay_rejects_malformed_first_json_record(tmp_path):
    """A corrupt first nonblank line is an invalid envelope, not an empty or
    partially flushed live history. Both public replay surfaces must reject
    it through the stable history-format contract."""
    ws = make_scaffold_ws(tmp_path)

    async def scenario(client):
        started = await start_run(client, "flows/smoke")
        await wait_finished(client, started["run_id"])
        log_path = ws.root / started["log"]
        log_path.write_text("\n{not-json}\n", encoding="utf-8")

        replay = await client.get(f"/api/runs/{started['run_id']}/events")
        assert replay.status == 422
        payload = await replay.json()
        assert payload["error"] == "history_format"
        assert "valid JSON envelope" in payload["message"]

        async with client.websocket_connect(f"/ws/runs/{started['run_id']}") as sock:
            message = await sock.receive()
            assert message["type"] == "websocket.close"
            assert message["code"] == WS_HISTORY_FORMAT
            assert "valid JSON envelope" in message["reason"]

    with_client(ws, scenario)


def test_history_reader_tolerates_a_trailing_partial_record(tmp_path):
    header = {
        "event": "run_started",
        "seq": 1,
        "format": HISTORY_FORMAT,
        "features": [],
    }
    log_path = tmp_path / "partial.jsonl"
    log_path.write_text(
        f"{json.dumps(header)}\n" + '{"event":"node_fired"',
        encoding="utf-8",
    )
    assert _read_records(log_path) == [header]


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
    missing = "19700101-000000-abcdef"

    async def scenario(client):
        for path in (f"/api/runs/{missing}", f"/api/runs/{missing}/events"):
            response = await client.get(path)
            assert response.status == 404
        response = await client.post(f"/api/runs/{missing}/abort")
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
        page = await response.text()
        assert "napflow server is running" in page
        assert "PyPI or a GitHub release artifact" in page
        assert "raw-source" in page and "unsupported" in page

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
        fallback = await client.get("/flow/flows/some/client/route")
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


@pytest.mark.parametrize(
    ("source_name", "target_name", "endpoint", "body"),
    [
        ("nodes.py", "flow.yaml", "code", {"code": "# replacement\n"}),
        ("flow.yaml", "nodes.py", "flows", {"flow": {}}),
    ],
)
def test_source_endpoints_reject_sibling_symlink_aliases(
    tmp_path, source_name, target_name, endpoint, body
):
    ws = make_scaffold_ws(tmp_path)
    flow_dir = ws.root / "flows" / "smoke"
    source = flow_dir / source_name
    target = flow_dir / target_name
    target_before = target.read_bytes()
    source.unlink()
    try:
        source.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/privilege level")

    async def scenario(client):
        path = f"/api/{endpoint}/flows/smoke"
        read = await client.get(path)
        assert read.status == 400
        assert (await read.json())["error"] == "workspace_boundary"

        write = await client.put(path, content=JSONContent(body))
        assert write.status == 400
        assert (await write.json())["error"] == "workspace_boundary"
        assert target.read_bytes() == target_before

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


def test_source_write_failure_is_controlled_and_preserves_code(tmp_path, monkeypatch):
    import napflow.server.app as server_app

    ws = make_scaffold_ws(tmp_path)
    path = ws.root / "flows" / "smoke" / "nodes.py"
    before = path.read_bytes()

    def disk_full(_path, _text):
        raise OSError(28, "simulated disk full")

    monkeypatch.setattr(server_app, "atomic_write_text", disk_full)

    async def scenario(client):
        got = await client.get("/api/code/flows/smoke")
        code = await got.json()
        response = await client.put(
            "/api/code/flows/smoke",
            content=JSONContent(
                {"code": code["code"] + "\n# new\n", "base_etag": code["etag"]}
            ),
        )
        assert response.status == 507
        assert (await response.json())["error"] == "write_failed"
        assert path.read_bytes() == before

    with_client(ws, scenario)


def test_source_endpoints_reject_file_symlink_outside_flow_directory(tmp_path):
    ws = make_scaffold_ws(tmp_path)
    flow_dir = ws.root / "flows" / "main"
    nodes = flow_dir / "nodes.py"
    nodes.unlink()
    manifest = ws.root / "napflow.yaml"
    before = manifest.read_bytes()
    try:
        nodes.symlink_to(manifest)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/privilege level")

    async def scenario(client):
        got = await client.get("/api/code/flows/main")
        assert got.status == 400
        assert (await got.json())["error"] == "workspace_boundary"

        put = await client.put(
            "/api/code/flows/main",
            content=JSONContent({"code": "must not overwrite manifest"}),
        )
        assert put.status == 400
        assert (await put.json())["error"] == "workspace_boundary"
        assert manifest.read_bytes() == before

    with_client(ws, scenario)


def test_concurrent_code_puts_serialize_the_etag_check(tmp_path):
    ws = make_scaffold_ws(tmp_path)
    path = ws.root / "flows" / "smoke" / "nodes.py"

    async def scenario(client):
        original = await (await client.get("/api/code/flows/smoke")).json()

        async def save(marker):
            return await client.put(
                "/api/code/flows/smoke",
                content=JSONContent(
                    {
                        "code": original["code"] + f"\n# {marker}\n",
                        "base_etag": original["etag"],
                    }
                ),
            )

        responses = await asyncio.gather(save("first"), save("second"))
        assert sorted(response.status for response in responses) == [200, 409]
        final = path.read_text(encoding="utf-8")
        assert ("# first" in final) != ("# second" in final)

    with_client(ws, scenario)


def test_source_write_coordinator_serializes_yielding_critical_sections(tmp_path):
    async def scenario():
        coordinator = _SourceWriteCoordinator()
        path = tmp_path / "flow.yaml"
        active = 0
        maximum = 0

        async def enter():
            nonlocal active, maximum
            async with coordinator.lock(path):
                active += 1
                maximum = max(maximum, active)
                await asyncio.sleep(0)
                active -= 1

        await asyncio.gather(enter(), enter())
        assert maximum == 1

    asyncio.run(scenario())


def test_run_start_rejects_history_directory_symlink_escape(tmp_path):
    ws = make_scaffold_ws(tmp_path)
    outside = tmp_path / "outside-runs"
    outside.mkdir()
    flow_runs = ws.root / ".napflow" / "runs" / "flows"
    flow_runs.mkdir(parents=True)
    try:
        (flow_runs / "smoke").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/privilege level")

    async def scenario(client):
        response = await client.post(
            "/api/runs", content=JSONContent({"flow": "flows/smoke"})
        )
        assert response.status == 400
        assert (await response.json())["error"] == "workspace_boundary"
        assert list(outside.iterdir()) == []

    with_client(ws, scenario)


def test_server_rejects_surrogate_identity_with_boundary_reason(tmp_path):
    ws = make_scaffold_ws(tmp_path)

    async def scenario(client):
        response = await client.post(
            "/api/runs",
            content=Content(b"application/json", b'{"flow":"flows/\\ud800"}'),
        )
        assert response.status == 400
        assert (await response.json())["error"] == "workspace_boundary"

    with_client(ws, scenario)


def test_concurrent_flow_puts_serialize_the_etag_check(tmp_path):
    ws = make_scaffold_ws(tmp_path)

    async def scenario(client):
        original = await _flow_detail(client, "flows/main")

        async def save(x):
            flow = json.loads(json.dumps(original["flow"]))
            flow["layout"]["start"] = [x, 200]
            return await _put_flow(
                client,
                "flows/main",
                flow=flow,
                base_etag=original["etag"],
            )

        responses = await asyncio.gather(save(101), save(202))
        assert sorted(response.status for response in responses) == [200, 409]
        final = await _flow_detail(client, "flows/main")
        assert final["flow"]["layout"]["start"][0] in {101, 202}

    with_client(ws, scenario)


def test_loopback_host_and_same_origin_mutation_boundary(tmp_path):
    ws = make_scaffold_ws(tmp_path)
    path = ws.root / "flows" / "smoke" / "nodes.py"

    async def scenario(client):
        foreign_host = await client.get(
            "/api/workspace", headers={"host": "attacker.example:8000"}
        )
        assert foreign_host.status == 403
        assert (await foreign_host.json())["error"] == "request_origin"

        malformed_host = await client.get(
            "/api/workspace", headers={"host": "localhost:"}
        )
        assert malformed_host.status == 403

        ipv6 = await client.get("/api/workspace", headers={"host": "[::1]:8000"})
        assert ipv6.status == 200

        got = await client.get("/api/code/flows/smoke")
        code = await got.json()
        accepted = await client.put(
            "/api/code/flows/smoke",
            headers={"origin": "http://127.0.0.1:8000"},
            content=JSONContent(
                {"code": code["code"] + "\n# accepted\n", "base_etag": code["etag"]}
            ),
        )
        assert accepted.status == 200
        accepted_payload = await accepted.json()
        before_rejection = path.read_bytes()

        for origin in (
            "https://127.0.0.1:8000",
            "http://127.0.0.1:9999",
            "http://attacker.example:8000",
            "null",
        ):
            rejected = await client.put(
                "/api/code/flows/smoke",
                headers={"origin": origin},
                content=JSONContent(
                    {
                        "code": code["code"] + "\n# rejected\n",
                        "base_etag": accepted_payload["etag"],
                    }
                ),
            )
            assert rejected.status == 403
            assert (await rejected.json())["error"] == "request_origin"
            assert path.read_bytes() == before_rejection

    with_client(ws, scenario)


def test_foreign_origin_rejects_every_mutation_route(tmp_path):
    ws = make_scaffold_ws(tmp_path)
    run_id = "19700101-000000-abcdef"
    headers = {"origin": "http://attacker.example:8000"}

    async def scenario(client):
        responses = [
            await client.put(
                "/api/flows/flows/main",
                headers=headers,
                content=JSONContent({"flow": {}}),
            ),
            await client.put(
                "/api/code/flows/main",
                headers=headers,
                content=JSONContent({"code": "# rejected\n"}),
            ),
            await client.post(
                "/api/flows/clone",
                headers=headers,
                content=JSONContent({"source": "flows/main", "dest": "flows/rejected"}),
            ),
            await client.post(
                "/api/runs",
                headers=headers,
                content=JSONContent({"flow": "flows/main"}),
            ),
            await client.post(f"/api/runs/{run_id}/abort", headers=headers),
        ]
        assert [response.status for response in responses] == [403] * len(responses)
        assert not (ws.root / "flows" / "rejected").exists()

    with_client(ws, scenario)


def test_encoded_reserved_identity_round_trips_through_api(tmp_path):
    ws = make_scaffold_ws(tmp_path)
    identity = "flows/team space #100%"
    directory = ws.root / identity
    directory.mkdir()
    source = ws.root / "flows" / "main" / "flow.yaml"
    (directory / "flow.yaml").write_bytes(source.read_bytes())
    encoded = quote(identity, safe="/")

    async def scenario(client):
        response = await client.get(f"/api/flows/{encoded}")
        assert response.status == 200, await response.text()
        detail = await response.json()
        assert detail["identity"] == identity

        saved = await client.put(
            f"/api/flows/{encoded}",
            content=JSONContent({"flow": detail["flow"], "base_etag": detail["etag"]}),
        )
        assert saved.status == 200, await saved.text()

        listed = await client.get("/api/runs", query={"flow": identity})
        assert listed.status == 200
        assert (await listed.json())["flow"] == identity

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


def test_clone_flow_forks_the_folder_through_durable_source_writes(
    tmp_path, monkeypatch
):
    import napflow.server.app as server_app

    ws = make_scaffold_ws(tmp_path)
    real_atomic_write = server_app.atomic_write_text
    durable_writes = []

    def observe_atomic_write(path, text):
        durable_writes.append(path.name)
        real_atomic_write(path, text)

    monkeypatch.setattr(server_app, "atomic_write_text", observe_atomic_write)

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
        assert sorted(durable_writes) == ["flow.yaml", "nodes.py"]
        listed = await client.get("/api/flows")
        identities = [f["identity"] for f in (await listed.json())["flows"]]
        assert "flows/smoke_copy" in identities

    with_client(ws, scenario)


def test_interrupted_clone_removes_unaccepted_destination(tmp_path, monkeypatch):
    import napflow.server.app as server_app

    ws = make_scaffold_ws(tmp_path)
    source = ws.root / "flows" / "smoke" / "flow.yaml"
    before = source.read_bytes()
    real_atomic_write = server_app.atomic_write_text

    def interrupt_clone(path, text):
        if path.name == "nodes.py":
            raise OSError("simulated clone interruption")
        real_atomic_write(path, text)

    monkeypatch.setattr(server_app, "atomic_write_text", interrupt_clone)

    async def scenario(client):
        response = await client.post(
            "/api/flows/clone",
            content=JSONContent(
                {"source": "flows/smoke", "dest": "flows/interrupted_copy"}
            ),
        )
        assert response.status == 507
        assert (await response.json())["error"] == "write_failed"
        assert not (ws.root / "flows" / "interrupted_copy").exists()
        assert source.read_bytes() == before

    with_client(ws, scenario)


def test_clone_preserves_nested_symlinks_without_dereferencing(tmp_path):
    ws = make_scaffold_ws(tmp_path)
    source = ws.root / "flows" / "smoke"
    outside = tmp_path / "outside.txt"
    outside.write_text("must not be copied", encoding="utf-8")
    link = source / "outside-link"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/privilege level")

    async def scenario(client):
        response = await client.post(
            "/api/flows/clone",
            content=JSONContent(
                {"source": "flows/smoke", "dest": "flows/smoke_link_copy"}
            ),
        )
        assert response.status == 201, await response.text()
        copied = ws.root / "flows" / "smoke_link_copy" / "outside-link"
        assert copied.is_symlink()
        assert copied.samefile(outside)

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


def test_ws_history_range_resumes_without_duplicates(tmp_path):
    path = tmp_path / "range.jsonl"
    records = [
        {
            "event": "run_started" if seq == 1 else "node_fired",
            "run_id": "range",
            "seq": seq,
            **({"format": HISTORY_FORMAT, "features": []} if seq == 1 else {}),
        }
        for seq in range(1, 6)
    ]
    path.write_text(
        "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )

    class Socket:
        def __init__(self):
            self.sent = []

        async def send_text(self, text):
            self.sent.append(json.loads(text))

    socket = Socket()
    last_sent = asyncio.run(
        _send_history_range(socket, path, after_seq=2, through_seq=4)
    )

    assert [record["seq"] for record in socket.sent] == [3, 4]
    assert last_sent == 4


def test_ws_overflow_catches_up_exactly_once_on_same_socket(tmp_path):
    run_id = "20260712-100000-000001"
    log = tmp_path / f"{run_id}.jsonl"
    header = {
        "event": "run_started",
        "run_id": run_id,
        "seq": 1,
        "format": HISTORY_FORMAT,
        "features": [],
    }
    log.write_text(json.dumps(header, separators=(",", ":")) + "\n", encoding="utf-8")
    run = SimpleNamespace(
        run_id=run_id,
        log_path=log,
        history_limit=20,
        finished=False,
        last_seq=1,
        subscribers=set(),
        replay_readers=0,
        resync_until=0.0,
    )
    manager = RunManager()
    sink = _LiveSink()
    sink.run = run

    class Socket:
        def __init__(self):
            self.sent = []
            self.first_send = asyncio.Event()
            self.release = asyncio.Event()
            self.closed = None

        async def send_text(self, text):
            self.sent.append(json.loads(text))
            if len(self.sent) == 1:
                self.first_send.set()
                await self.release.wait()

        async def close(self, code, reason):
            self.closed = (code, reason)

    async def scenario():
        socket = Socket()
        serving = asyncio.create_task(_stream_run_websocket(socket, run, manager))
        await socket.first_send.wait()
        final_seq = SUBSCRIBER_QUEUE_LIMIT + 20
        with log.open("a", encoding="utf-8") as history:
            for seq in range(2, final_seq + 1):
                record = {
                    "event": "run_finished" if seq == final_seq else "node_fired",
                    "run_id": run_id,
                    "seq": seq,
                    **({"state": "passed"} if seq == final_seq else {}),
                }
                history.write(json.dumps(record, separators=(",", ":")) + "\n")
                history.flush()
                sink.write(record)
        run.finished = True
        socket.release.set()
        await serving
        return socket, final_seq

    socket, final_seq = asyncio.run(scenario())
    assert [record["seq"] for record in socket.sent] == list(range(1, final_seq + 1))
    assert socket.closed == (1000, "")


def test_finished_ws_reader_lease_blocks_newer_run_retention(tmp_path):
    old_id = "20260712-100000-000001"
    new_id = "20260712-100000-000002"
    old = tmp_path / f"{old_id}.jsonl"
    new = tmp_path / f"{new_id}.jsonl"
    old.write_text(
        json.dumps(
            {
                "event": "run_started",
                "run_id": old_id,
                "seq": 1,
                "format": HISTORY_FORMAT,
                "features": [],
            },
            separators=(",", ":"),
        )
        + "\n"
        + json.dumps(
            {"event": "run_finished", "run_id": old_id, "seq": 2},
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    new.write_text(
        json.dumps({"event": "run_finished", "run_id": new_id}) + "\n",
        encoding="utf-8",
    )
    os.utime(old, ns=(1_000_000_000, 1_000_000_000))
    os.utime(new, ns=(2_000_000_000, 2_000_000_000))
    run = SimpleNamespace(
        run_id=old_id,
        log_path=old,
        history_limit=20,
        finished=True,
        subscribers=set(),
        replay_readers=0,
        resync_until=0.0,
    )
    manager = RunManager()

    class Socket:
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def send_text(self, _text):
            self.started.set()
            await self.release.wait()

        async def close(self, _code, _reason):
            pass

    async def scenario():
        socket = Socket()
        serving = asyncio.create_task(_stream_run_websocket(socket, run, manager))
        await socket.started.wait()
        assert len(list(tmp_path.glob(f"{old_id}.reader-*"))) == 1
        assert apply_retention(tmp_path, history=1) == []
        assert old.exists()
        socket.release.set()
        await serving

    asyncio.run(scenario())
    assert list(tmp_path.glob(f"{old_id}.reader-*")) == []
    assert apply_retention(tmp_path, history=1) == [old]


def test_ws_send_timeout_bounds_blocked_subscriber(monkeypatch):
    monkeypatch.setattr(app_module, "WS_SEND_TIMEOUT_S", 0.01)

    class BlockedSocket:
        async def send_text(self, _text):
            await asyncio.Event().wait()

    async def scenario():
        with pytest.raises(app_module._SlowSubscriber):
            await _send_ws_record(BlockedSocket(), {"event": "node_fired", "seq": 1})

    asyncio.run(scenario())


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


def test_ws_late_subscriber_replays_durable_prefix_then_live_tail(tmp_path):
    ws = make_scaffold_ws(tmp_path)
    slow = ws.root / "flows" / "late"
    slow.mkdir()
    (slow / "flow.yaml").write_text(
        DELAY_FLOW.replace('name: "slow"', 'name: "late"').replace(
            "seconds: 30", "seconds: 0.5"
        ),
        encoding="utf-8",
    )

    async def scenario(client):
        started = await start_run(client, "flows/late")
        log_path = ws.root / started["log"]
        deadline = asyncio.get_running_loop().time() + 2
        while True:
            prefix = log_path.read_text(encoding="utf-8").splitlines()
            if len(prefix) >= 2:
                break
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.005)
        assert (await client.get(f"/api/runs/{started['run_id']}")).status == 200

        frames = []
        async with client.websocket_connect(f"/ws/runs/{started['run_id']}") as sock:
            while True:
                frame = await sock.receive_text()
                frames.append(frame)
                if json.loads(frame)["event"] == "run_finished":
                    break

        await wait_finished(client, started["run_id"])
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert frames == lines
        assert frames[: len(prefix)] == prefix

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
    missing = "19700101-000000-abcdef"

    async def scenario(client):
        async with client.websocket_connect(f"/ws/runs/{missing}") as sock:
            message = await sock.receive()
            assert message["type"] == "websocket.close"
            assert message["code"] == 4404

    with_client(ws, scenario)


def test_ws_rejects_foreign_host_and_origin_before_accept(tmp_path):
    ws = make_scaffold_ws(tmp_path)
    missing = "19700101-000000-abcdef"

    async def scenario(client):
        for headers in (
            {"host": "attacker.example:8000"},
            {"origin": "http://attacker.example:8000"},
        ):
            socket = client.websocket_connect(f"/ws/runs/{missing}", headers=headers)
            await socket.send({"type": "websocket.connect"})
            message = await socket.receive()
            assert message["type"] == "websocket.close"
            assert message["code"] == WS_REQUEST_ORIGIN
        await client.websocket_all_closed()

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
