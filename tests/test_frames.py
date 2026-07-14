"""Hierarchical frames (S3/M5): flow node (FR-516, D21), loop node
(FR-515, EC06/EC36), per-frame data isolation with run-wide outcome
aggregation (FR-404, D20). Completes TR-3 (required-End across child
frames), TR-5 (guard isolation per frame), and TR-8's flow/loop
timeout halves (D24).

Child flows are written to disk as JSON (a YAML subset the loader
reads) under a tmp workspace root.
"""

import asyncio
import json
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from napflow.core.engine import FlowRun
from napflow.core.events import EventStream
from test_engine import (
    NO_SECRETS,
    CaptureSink,
    end,
    events_of,
    flow,
    manifest,
    run,
    start,
)


def write_flow(root, ref, nodes, edges, nodes_py=None, env_required=None):
    doc = {
        "schema": "napflow/v1",
        "flow": {"name": ref.rsplit("/", 1)[-1]},
        "nodes": nodes,
        "edges": [{"from": f, "to": t} for f, t in edges],
    }
    if env_required:
        doc["env"] = {"required": env_required}
    flow_dir = root / ref
    flow_dir.mkdir(parents=True, exist_ok=True)
    (flow_dir / "flow.yaml").write_text(json.dumps(doc), encoding="utf-8")
    if nodes_py:
        (flow_dir / "nodes.py").write_text(textwrap.dedent(nodes_py), encoding="utf-8")


def sub(node_id, ref, **extra):
    return {"id": node_id, "type": "flow", "config": {"flow": ref}} | extra


def loop(node_id, over, body, **config):
    merged = {"over": over, "body": body} | config
    return {"id": node_id, "type": "loop", "config": merged}


# --------------------------------------------------------------------------
# flow node: values cross via Start/End, frames isolate data (FR-516)


def test_subflow_passes_values_with_hierarchical_frames(tmp_path):
    write_flow(
        tmp_path,
        "flows/child",
        [
            start({"name": "x", "type": "number"}),
            {"id": "gate", "type": "condition", "config": {"expr": "true"}},
            end({"name": "out"}),
        ],
        [("start.x", "gate.in"), ("gate.true", "end.out")],
    )
    f = flow(
        start({"name": "n", "type": "number"}),
        sub("child_run", "flows/child"),
        end({"name": "res"}),
        edges=[("start.n", "child_run.x"), ("child_run.out", "end.res")],
    )
    result, records = run(f, inputs={"n": 7}, workspace_root=tmp_path)
    assert result.state == "passed"
    assert result.end_outputs == {"res": 7}
    frames = {r["frame"] for r in events_of(records, "node_fired")}
    assert frames == {"f-0", "f-0/f-1"}  # hierarchical ids (FR-404)
    summary = events_of(records, "frame_finished")
    assert len(summary) == 1
    assert {
        key: summary[0][key]
        for key in (
            "frame",
            "parent_frame",
            "parent_node",
            "flow",
            "kind",
            "loop_index",
            "state",
            "asserts",
            "unhandled_errors",
            "end_outputs",
        )
    } == {
        "frame": "f-0/f-1",
        "parent_frame": "f-0",
        "parent_node": "child_run",
        "flow": "flows/child",
        "kind": "flow",
        "loop_index": None,
        "state": "passed",
        "asserts": {"passed": 0, "failed": 0},
        "unhandled_errors": [],
        "end_outputs": {"out": 7},
    }
    assert summary[0]["duration_ms"] >= 0
    child_output = next(
        record
        for record in events_of(records, "message_emitted")
        if record["from_port"] == "child_run.out"
    )
    assert summary[0]["seq"] < child_output["seq"]


def test_subflow_invocations_get_fresh_frames(tmp_path):
    # TR-5 subflow half: a counter inside the child resets per
    # invocation — two firings of the flow node both pass
    write_flow(
        tmp_path,
        "flows/guarded",
        [
            start({"name": "x", "type": "number"}),
            {"id": "g", "type": "counter", "config": {"count": 1}},
            end({"name": "out"}),
        ],
        [("start.x", "g.in"), ("g.continue", "end.out")],
    )
    f = flow(
        start({"name": "n", "type": "number"}),
        sub("child_run", "flows/guarded"),
        {"id": "dly", "type": "delay", "config": {"seconds": 0.03}},
        end({"name": "res"}),
        edges=[
            ("start.n", "child_run.x"),
            ("start.n", "dly.in"),
            ("dly.out", "child_run.x"),  # second invocation
            ("child_run.out", "end.res"),
        ],
    )
    result, records = run(f, inputs={"n": 1}, workspace_root=tmp_path)
    assert result.state == "passed"
    fired = [r for r in events_of(records, "node_fired") if r["node"] == "child_run"]
    assert len(fired) == 2
    assert len(events_of(records, "guard_tripped")) == 0  # both frames fresh


def test_child_required_end_unwritten_fails_run(tmp_path):
    # TR-3 cross-frame: D18 applies at CHILD quiescence; handling the
    # implicit error port branches but never un-fails the run (D20)
    write_flow(
        tmp_path,
        "flows/dropper",
        [
            start({"name": "x", "type": "number"}),
            {"id": "gate", "type": "condition", "config": {"expr": "false"}},
            end({"name": "out"}),  # required, never written
        ],
        [("start.x", "gate.in"), ("gate.true", "end.out")],
    )
    f = flow(
        start({"name": "n", "type": "number"}),
        sub("child_run", "flows/dropper"),
        end({"name": "res", "required": False}, {"name": "err", "required": False}),
        edges=[
            ("start.n", "child_run.x"),
            ("child_run.out", "end.res"),
            ("child_run.error", "end.err"),
        ],
    )
    result, _ = run(f, inputs={"n": 1}, workspace_root=tmp_path)
    assert result.state == "failed"
    assert result.exit_code == 1
    payload = result.end_outputs["err"]
    assert payload["state"] == "failed"
    entry = payload["unhandled_errors"][0]
    assert entry["kind"] == "required_end_unwritten"
    assert entry["frame"] == "f-0/f-1"
    assert result.end_outputs["res"] is None


def test_nested_subflow_outcomes_bubble_through_subtree(tmp_path):
    # leaf assert failure → mid's implicit error → parent's implicit
    # error; subtree counts sum through the frame tree (D21)
    write_flow(
        tmp_path,
        "flows/leaf",
        [
            start({"name": "x", "type": "number"}),
            {
                "id": "chk",
                "type": "assert",
                "config": {
                    "checks": [
                        {
                            "kind": "expr",
                            "expr": "inputs.x",
                            "op": "equals",
                            "value": 999,
                        }
                    ]
                },
            },
            end({"name": "out", "required": False}),
        ],
        [("start.x", "chk.in"), ("chk.passed", "end.out")],
    )
    write_flow(
        tmp_path,
        "flows/mid",
        [
            start({"name": "x", "type": "number"}),
            sub("leaf_run", "flows/leaf"),
            end({"name": "out", "required": False}),
        ],
        [("start.x", "leaf_run.x"), ("leaf_run.out", "end.out")],
    )
    f = flow(
        start({"name": "n", "type": "number"}),
        sub("mid_run", "flows/mid"),
        end({"name": "err", "required": False}),
        edges=[("start.n", "mid_run.x"), ("mid_run.error", "end.err")],
    )
    result, records = run(f, inputs={"n": 1}, workspace_root=tmp_path)
    assert result.state == "failed"  # D20: aggregation is run-wide
    assert result.asserts_failed == 1
    payload = result.end_outputs["err"]
    assert payload["state"] == "failed"
    assert payload["failed_asserts"] == 1  # summed through the subtree
    frames = {r["frame"] for r in events_of(records, "node_fired")}
    assert "f-0/f-1/f-2" in frames  # two levels deep
    summaries = {r["flow"]: r for r in events_of(records, "frame_finished")}
    assert summaries["flows/leaf"]["asserts"]["failed"] == 1
    assert summaries["flows/mid"]["asserts"]["failed"] == 1
    assert summaries["flows/leaf"]["seq"] < summaries["flows/mid"]["seq"]


def test_flow_timeout_aborts_child_and_keeps_recorded_asserts(tmp_path):
    # TR-8 flow half (D24): tripped ceiling cancels the child subtree,
    # fires the implicit error port with state aborted, and outcomes
    # the child already recorded still aggregate
    write_flow(
        tmp_path,
        "flows/slow_fail",
        [
            start({"name": "x", "type": "number"}),
            {
                "id": "chk",
                "type": "assert",
                "config": {
                    "checks": [
                        {
                            "kind": "expr",
                            "expr": "inputs.x",
                            "op": "equals",
                            "value": 999,
                        }
                    ]
                },
            },
            {"id": "sleep", "type": "delay", "config": {"seconds": 30}},
            end({"name": "out", "required": False}),
        ],
        [
            ("start.x", "chk.in"),
            ("chk.failed", "sleep.in"),
            ("sleep.out", "end.out"),
        ],
    )
    f = flow(
        start({"name": "n", "type": "number"}),
        sub("child_run", "flows/slow_fail", max_seconds=0.3),
        end({"name": "err", "required": False}),
        edges=[("start.n", "child_run.x"), ("child_run.error", "end.err")],
    )
    result, _ = run(f, inputs={"n": 1}, workspace_root=tmp_path)
    assert result.state == "failed"
    assert result.asserts_failed == 1  # recorded before the abort
    payload = result.end_outputs["err"]
    assert payload["error_kind"] == "timeout"
    assert payload["state"] == "aborted"
    assert payload["max_seconds"] == 0.3
    assert result.duration_ms < 5000  # the 30s delay was cancelled


def test_child_env_required_missing_is_node_error(tmp_path):
    write_flow(
        tmp_path,
        "flows/needs_env",
        [start(), end({"name": "out", "required": False})],
        [("start.out", "end.out")],
        env_required=["MISSING_KEY"],
    )
    f = flow(
        start(),
        sub("child_run", "flows/needs_env"),
        end({"name": "res", "required": False}),
        edges=[("start.out", "child_run.trigger"), ("child_run.out", "end.res")],
    )
    result, _ = run(f, workspace_root=tmp_path)
    assert result.state == "failed"  # unwired error port ⇒ unhandled
    assert "MISSING_KEY" in str(result.unhandled_errors)


# --------------------------------------------------------------------------
# loop node (FR-515)

DOUBLE_BODY = """
def double(item):
    return {"value": item * 2}
"""


def make_double_body(tmp_path):
    write_flow(
        tmp_path,
        "flows/double",
        [
            start({"name": "item", "type": "number"}),
            {
                "id": "calc",
                "type": "python",
                "config": {"function": "double", "outputs": ["value"]},
            },
            end({"name": "value"}),
        ],
        [("start.item", "calc.item"), ("calc.value", "end.value")],
        nodes_py=DOUBLE_BODY,
    )


def test_loop_sequential_collects_results_in_order(tmp_path):
    make_double_body(tmp_path)
    f = flow(
        start(),
        loop("lp", "[1, 2, 3]", "flows/double"),
        end({"name": "res"}),
        edges=[("start.out", "lp.trigger"), ("lp.results", "end.res")],
    )
    result, _ = run(f, workspace_root=tmp_path)
    assert result.state == "passed"
    assert result.end_outputs == {"res": [{"value": 2}, {"value": 4}, {"value": 6}]}


def test_loop_parallel_results_stay_index_ordered(tmp_path):
    # EC36: items sleep INVERSELY to their index — completion order is
    # reversed, results order must not be
    write_flow(
        tmp_path,
        "flows/sleepy",
        [
            start({"name": "item", "type": "number"}),
            {"id": "dly", "type": "delay", "config": {"seconds": "{{ inputs.item }}"}},
            end({"name": "value"}),
        ],
        [("start.item", "dly.in"), ("dly.out", "end.value")],
    )
    f = flow(
        start(),
        loop(
            "lp",
            "[0.09, 0.05, 0.01]",
            "flows/sleepy",
            mode="parallel",
            max_concurrency=3,
        ),
        end({"name": "res"}),
        edges=[("start.out", "lp.trigger"), ("lp.results", "end.res")],
    )
    result, _ = run(f, workspace_root=tmp_path)
    assert result.state == "passed"
    assert result.end_outputs == {
        "res": [{"value": 0.09}, {"value": 0.05}, {"value": 0.01}]
    }


def test_parallel_loop_bounds_workers_and_releases_finished_frames(
    tmp_path, monkeypatch
):
    """M3/NFR-14: total items do not determine helper-task or live-Frame
    counts. Every completed iteration remains reconstructable from its durable
    frame summary after the runtime object is detached.
    """
    count = 200
    concurrency = 4
    write_flow(
        tmp_path,
        "flows/bounded",
        [
            start({"name": "item", "type": "number"}),
            {"id": "pause", "type": "delay", "config": {"seconds": 0.001}},
            end({"name": "value"}),
        ],
        [("start.item", "pause.in"), ("pause.out", "end.value")],
    )
    f = flow(
        start(),
        loop(
            "lp",
            f"range({count}) | list",
            "flows/bounded",
            mode="parallel",
            max_concurrency=concurrency,
        ),
        end({"name": "res"}),
        edges=[("start.out", "lp.trigger"), ("lp.results", "end.res")],
    )

    active = 0
    peak = 0
    original_spawn = FlowRun._spawn_frame
    original_finish = FlowRun._finish_child

    def tracked_spawn(self, *args, **kwargs):
        nonlocal active, peak
        child = original_spawn(self, *args, **kwargs)
        active += 1
        peak = max(peak, active)
        return child

    def tracked_finish(self, *args, **kwargs):
        nonlocal active
        try:
            return original_finish(self, *args, **kwargs)
        finally:
            active -= 1

    worker_names = []
    original_create_task = asyncio.TaskGroup.create_task

    def tracked_create_task(group, coro, *, name=None, context=None):
        if name and name.startswith("napf-loop-"):
            worker_names.append(name)
        return original_create_task(group, coro, name=name, context=context)

    monkeypatch.setattr(FlowRun, "_spawn_frame", tracked_spawn)
    monkeypatch.setattr(FlowRun, "_finish_child", tracked_finish)
    monkeypatch.setattr(asyncio.TaskGroup, "create_task", tracked_create_task)

    result, records = run(f, workspace_root=tmp_path)

    assert result.state == "passed"
    assert len(result.end_outputs["res"]) == count
    assert len(worker_names) == concurrency
    assert peak <= concurrency
    assert active == 0
    summaries = events_of(records, "frame_finished")
    assert len(summaries) == count
    assert {record["loop_index"] for record in summaries} == set(range(count))


def test_parallel_loop_abort_keeps_only_active_frames_and_replays_abort(tmp_path):
    concurrency = 4
    write_flow(
        tmp_path,
        "flows/abortable",
        [
            start({"name": "item", "type": "number"}),
            {"id": "pause", "type": "delay", "config": {"seconds": 60}},
            end({"name": "value"}),
        ],
        [("start.item", "pause.in"), ("pause.out", "end.value")],
    )
    parent = flow(
        start(),
        loop(
            "lp",
            "range(100) | list",
            "flows/abortable",
            mode="parallel",
            max_concurrency=concurrency,
        ),
        end(),
        edges=[("start.out", "lp.trigger")],
    )

    async def scenario():
        reached = asyncio.Event()

        class AbortSink(CaptureSink):
            def write(self, record):
                super().write(record)
                active_frames = {
                    item["frame"]
                    for item in self.records
                    if item["event"] == "node_fired" and item.get("node") == "pause"
                }
                if len(active_frames) == concurrency:
                    reached.set()

        sink = AbortSink()
        engine = FlowRun(
            parent,
            flow_identity="flows/abort-parent",
            manifest=manifest(),
            env={},
            env_name=None,
            inputs={},
            stream=EventStream("abort-loop", NO_SECRETS, [sink]),
            workspace_root=tmp_path,
        )
        task = asyncio.create_task(engine.execute())
        async with asyncio.timeout(5):
            await reached.wait()
            engine.abort()
            result = await task
        return result, sink.records

    result, records = asyncio.run(scenario())
    child_frames = {
        record["frame"]
        for record in records
        if record["event"] == "node_fired" and record.get("node") == "pause"
    }
    assert result.state == "aborted"
    assert len(child_frames) == concurrency
    assert events_of(records, "frame_finished") == []
    assert events_of(records, "run_finished")[-1]["state"] == "aborted"


def make_small_checker(tmp_path):
    write_flow(
        tmp_path,
        "flows/small",
        [
            start({"name": "item", "type": "number"}),
            {
                "id": "chk",
                "type": "assert",
                "config": {
                    "checks": [
                        {"kind": "expr", "expr": "inputs.item", "op": "lt", "value": 3}
                    ]
                },
            },
            end({"name": "ok"}),
        ],
        [("start.item", "chk.in"), ("chk.passed", "end.ok")],
    )


def test_loop_on_error_stop_gates_scheduling(tmp_path):
    make_small_checker(tmp_path)
    f = flow(
        start(),
        loop("lp", "[1, 2, 3, 4, 5]", "flows/small"),  # on_error: stop default
        end({"name": "res", "required": False}, {"name": "errs", "required": False}),
        edges=[
            ("start.out", "lp.trigger"),
            ("lp.results", "end.res"),
            ("lp.errors", "end.errs"),
        ],
    )
    result, _ = run(f, workspace_root=tmp_path)
    assert result.state == "failed"  # EC06: failures count regardless
    assert result.end_outputs["res"] == [{"ok": 1}, {"ok": 2}]
    errors = result.end_outputs["errs"]
    assert [e["index"] for e in errors] == [2]  # 4th/5th never scheduled
    assert errors[0]["state"] == "failed"
    assert errors[0]["failed_asserts"] == 1


def test_loop_on_error_continue_attempts_every_item(tmp_path):
    make_small_checker(tmp_path)
    f = flow(
        start(),
        loop("lp", "[1, 2, 3, 4, 5]", "flows/small", on_error="continue"),
        end({"name": "res", "required": False}, {"name": "errs", "required": False}),
        edges=[
            ("start.out", "lp.trigger"),
            ("lp.results", "end.res"),
            ("lp.errors", "end.errs"),
        ],
    )
    result, _ = run(f, workspace_root=tmp_path)
    assert result.state == "failed"
    assert result.end_outputs["res"] == [{"ok": 1}, {"ok": 2}]
    assert [e["index"] for e in result.end_outputs["errs"]] == [2, 3, 4]
    assert result.asserts_failed == 3


def test_loop_guard_state_is_per_iteration(tmp_path):
    # TR-5 loop half: a count-1 counter inside the body passes in
    # EVERY iteration — guard state dies with its frame
    write_flow(
        tmp_path,
        "flows/guarded_body",
        [
            start({"name": "item", "type": "number"}),
            {"id": "g", "type": "counter", "config": {"count": 1}},
            end({"name": "ok"}),
        ],
        [("start.item", "g.in"), ("g.continue", "end.ok")],
    )
    f = flow(
        start(),
        loop("lp", "[1, 2, 3]", "flows/guarded_body"),
        end({"name": "res"}),
        edges=[("start.out", "lp.trigger"), ("lp.results", "end.res")],
    )
    result, records = run(f, workspace_root=tmp_path)
    assert result.state == "passed"
    assert result.end_outputs == {"res": [{"ok": 1}, {"ok": 2}, {"ok": 3}]}
    assert len(events_of(records, "guard_tripped")) == 0


def test_loop_over_non_list_is_unhandled_node_error(tmp_path):
    make_double_body(tmp_path)
    f = flow(
        start({"name": "n", "type": "number"}),
        loop("lp", "inputs.n", "flows/double"),
        end({"name": "res", "required": False}),
        edges=[("start.n", "lp.trigger"), ("lp.results", "end.res")],
    )
    result, _ = run(f, inputs={"n": 5}, workspace_root=tmp_path)
    assert result.state == "failed"  # EC24: loop has no error outlet
    assert result.unhandled_errors[0]["kind"] == "loop_error"
    assert result.end_outputs == {"res": None}  # the loop emitted nothing


# --------------------------------------------------------------------------
# fresh_session (FR-515): cookie isolation per iteration


class CookieHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802 (http.server API)
        had = "Cookie" in self.headers
        raw = json.dumps({"had": had}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if not had:
            self.send_header("Set-Cookie", "sid=abc123")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture(scope="module")
def cookie_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), CookieHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    thread.join(timeout=5)


def cookie_loop_flow(tmp_path, fresh):
    write_flow(
        tmp_path,
        "flows/cookie",
        [
            start({"name": "item", "type": "number"}),
            {
                "id": "req",
                "type": "request",
                "config": {"url": "{{ env.BASE }}/c"},
            },
            end({"name": "r"}),
        ],
        [("start.item", "req.trigger"), ("req.response", "end.r")],
    )
    return flow(
        start(),
        loop("lp", "[1, 2]", "flows/cookie", fresh_session=fresh),
        end({"name": "res"}),
        edges=[("start.out", "lp.trigger"), ("lp.results", "end.res")],
    )


def had_cookie(result):
    return [r["r"]["body"]["had"] for r in result.end_outputs["res"]]


def test_shared_session_carries_cookies_across_iterations(tmp_path, cookie_server):
    f = cookie_loop_flow(tmp_path, fresh=False)
    result, _ = run(f, env={"BASE": cookie_server}, workspace_root=tmp_path)
    assert result.state == "passed"
    assert had_cookie(result) == [False, True]  # default: shared session


def test_fresh_session_isolates_cookies_per_iteration(tmp_path, cookie_server):
    f = cookie_loop_flow(tmp_path, fresh=True)
    result, _ = run(f, env={"BASE": cookie_server}, workspace_root=tmp_path)
    assert result.state == "passed"
    assert had_cookie(result) == [False, False]
