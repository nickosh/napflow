"""Guard nodes (S3/M4): counter (FR-509, EC16 check-then-decrement),
timeout (FR-510, lazy deadline), rule-4 `reset` inputs, D19
pass-through exhaustion routing (TR-4) — and the flagship
retry-until-ready pattern from the flow-schema example, run against a
local server (the S3 DoD's "flagship retry example runs", plus TR-1's
guarded-cycle re-exercise).
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from test_engine import end, events_of, flow, run, start


def counter(node_id, count):
    return {"id": node_id, "type": "counter", "config": {"count": count}}


def emissions(records, from_port):
    return [
        r for r in events_of(records, "message_emitted") if r["from_port"] == from_port
    ]


# --------------------------------------------------------------------------
# counter: EC16 boundary — exactly N passes, the N+1th message exhausts


def test_counter_passes_exactly_count_then_exhausts():
    f = flow(
        start(
            {"name": "a", "type": "number"},
            {"name": "b", "type": "number"},
            {"name": "c", "type": "number"},
        ),
        counter("g", 2),
        end({"name": "cont", "required": False}, {"name": "exh", "required": False}),
        edges=[
            ("start.a", "g.in"),
            ("start.b", "g.in"),
            ("start.c", "g.in"),
            ("g.continue", "end.cont"),
            ("g.exhausted", "end.exh"),
        ],
    )
    result, records = run(f, inputs={"a": 1, "b": 2, "c": 3})
    assert result.state == "passed"
    assert len(emissions(records, "g.continue")) == 2
    assert len(emissions(records, "g.exhausted")) == 1
    assert result.end_outputs == {"cont": 2, "exh": 3}  # pass-through, in order
    tripped = events_of(records, "guard_tripped")
    assert len(tripped) == 1
    assert (tripped[0]["kind"], tripped[0]["port"]) == ("counter", "exhausted")


def test_counter_zero_exhausts_immediately():
    f = flow(
        start(),
        counter("g", 0),
        end({"name": "exh", "required": False}),
        edges=[("start.out", "g.in"), ("g.exhausted", "end.exh")],
    )
    result, records = run(f)
    assert result.state == "passed"
    assert len(emissions(records, "g.exhausted")) == 1
    assert not emissions(records, "g.continue")


def test_counter_reset_restores_count_silently():
    # count=1; in @0s → continue, in @0.03 → exhausted, reset @0.06
    # (absorbed: no firing, no emission), in @0.09 → continue again
    f = flow(
        start(),
        counter("g", 1),
        {"id": "d1", "type": "delay", "config": {"seconds": 0.03}},
        {"id": "d2", "type": "delay", "config": {"seconds": 0.06}},
        {"id": "d3", "type": "delay", "config": {"seconds": 0.09}},
        end({"name": "cont", "required": False}, {"name": "exh", "required": False}),
        edges=[
            ("start.out", "g.in"),
            ("start.out", "d1.in"),
            ("d1.out", "g.in"),
            ("start.out", "d2.in"),
            ("d2.out", "g.reset"),
            ("start.out", "d3.in"),
            ("d3.out", "g.in"),
            ("g.continue", "end.cont"),
            ("g.exhausted", "end.exh"),
        ],
    )
    result, records = run(f)
    assert result.state == "passed"
    assert len(emissions(records, "g.continue")) == 2
    assert len(emissions(records, "g.exhausted")) == 1
    fired = [r for r in events_of(records, "node_fired") if r["node"] == "g"]
    assert len(fired) == 3  # the reset delivery is absorbed, not a firing


def test_unconnected_exhausted_drops_message_run_passes():
    # D19: exhausted is an ordinary output, NOT an error port — unwired,
    # the message is dropped like any non-error output
    f = flow(
        start(),
        counter("g", 0),
        end({"name": "x", "required": False}),
        edges=[("start.out", "g.in"), ("g.continue", "end.x")],
    )
    result, _ = run(f)
    assert result.state == "passed"
    assert result.unhandled_errors == []


# --------------------------------------------------------------------------
# timeout guard: lazy deadline, reset clears the clock


def test_timeout_guard_lazy_expiry_and_reset():
    # seconds=0.15: in @0s → continue (clock starts), in @0.3 → expired
    # (evaluated on arrival), reset @0.35, in @0.45 → continue (fresh
    # clock starts at that delivery)
    f = flow(
        start(),
        {"id": "g", "type": "timeout", "config": {"seconds": 0.15}},
        {"id": "d1", "type": "delay", "config": {"seconds": 0.3}},
        {"id": "d2", "type": "delay", "config": {"seconds": 0.35}},
        {"id": "d3", "type": "delay", "config": {"seconds": 0.45}},
        end({"name": "cont", "required": False}, {"name": "exp", "required": False}),
        edges=[
            ("start.out", "g.in"),
            ("start.out", "d1.in"),
            ("d1.out", "g.in"),
            ("start.out", "d2.in"),
            ("d2.out", "g.reset"),
            ("start.out", "d3.in"),
            ("d3.out", "g.in"),
            ("g.continue", "end.cont"),
            ("g.expired", "end.exp"),
        ],
    )
    result, records = run(f)
    assert result.state == "passed"
    assert len(emissions(records, "g.continue")) == 2
    assert len(emissions(records, "g.expired")) == 1
    tripped = events_of(records, "guard_tripped")
    assert (tripped[0]["kind"], tripped[0]["port"]) == ("timeout", "expired")


# --------------------------------------------------------------------------
# TR-1 guarded-cycle re-exercise: merge `any` + counter + delay


def test_guarded_cycle_terminates_by_exhaustion():
    f = flow(
        start(),
        {"id": "m", "type": "merge", "config": {"mode": "any"}},
        counter("g", 3),
        {"id": "wait", "type": "delay", "config": {"seconds": 0.01}},
        end({"name": "done"}),
        edges=[
            ("start.out", "m.in1"),
            ("m.out", "g.in"),
            ("g.continue", "wait.in"),
            ("wait.out", "m.in2"),  # the cycle
            ("g.exhausted", "end.done"),
        ],
    )
    result, records = run(f)
    assert result.state == "passed"
    assert result.end_outputs == {"done": {}}
    fired = [r for r in events_of(records, "node_fired") if r["node"] == "g"]
    assert len(fired) == 4  # 3 laps + the exhausting pass
    assert len(events_of(records, "guard_tripped")) == 1


# --------------------------------------------------------------------------
# Flagship: retry-until-ready polling against a local server (S3 DoD)


class JobHandler(BaseHTTPRequestHandler):
    ready_after = 3  # GETs until the job reports ready
    gets = 0

    def log_message(self, *args):
        pass

    def _send(self, status, payload):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):  # noqa: N802 (http.server API)
        JobHandler.gets = 0
        self._send(201, {"job_id": "j-1", "status": "queued"})

    def do_GET(self):  # noqa: N802
        JobHandler.gets += 1
        ready = JobHandler.gets >= JobHandler.ready_after
        self._send(200, {"job_id": "j-1", "status": "ready" if ready else "pending"})


@pytest.fixture(scope="module")
def job_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), JobHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    thread.join(timeout=5)


def flagship_flow(base, attempts):
    """The flow-schema example shape: create → poll → condition →
    counter → delay → re-poll via merge `any`; exhaustion → gave_up."""
    return flow(
        start(),
        {
            "id": "create",
            "type": "request",
            "config": {"method": "POST", "url": f"{base}/jobs", "body": {"n": 1}},
        },
        {"id": "kick", "type": "merge", "config": {"mode": "any"}},
        {"id": "check", "type": "request", "config": {"url": f"{base}/jobs/j-1"}},
        {
            "id": "ready",
            "type": "condition",
            "config": {"expr": "trigger.value.body.status == 'ready'"},
        },
        counter("attempts", attempts),
        {"id": "wait", "type": "delay", "config": {"seconds": 0.01}},
        end({"name": "job", "required": False}, {"name": "gave_up", "required": False}),
        edges=[
            ("start.out", "create.trigger"),
            ("create.response", "kick.in1"),
            ("kick.out", "check.trigger"),
            ("check.response", "ready.in"),
            ("ready.true", "end.job"),
            ("ready.false", "attempts.in"),
            ("attempts.continue", "wait.in"),
            ("wait.out", "kick.in2"),
            ("attempts.exhausted", "end.gave_up"),
        ],
    )


def test_flagship_polls_until_ready(job_server):
    JobHandler.ready_after = 3
    result, records = run(flagship_flow(job_server, attempts=5))
    assert result.state == "passed"
    assert result.end_outputs["job"]["body"]["status"] == "ready"
    assert result.end_outputs["gave_up"] is None
    checks = [r for r in events_of(records, "node_fired") if r["node"] == "check"]
    assert len(checks) == 3  # initial poll + two retries
    assert not events_of(records, "guard_tripped")


def test_flagship_gives_up_after_attempts(job_server):
    JobHandler.ready_after = 99  # never ready
    result, records = run(flagship_flow(job_server, attempts=2))
    assert result.state == "passed"  # D19: giving up is data, not failure
    assert result.end_outputs["job"] is None
    assert result.end_outputs["gave_up"]["body"]["status"] == "pending"
    checks = [r for r in events_of(records, "node_fired") if r["node"] == "check"]
    assert len(checks) == 3  # initial + 2 allowed retries
    assert len(events_of(records, "guard_tripped")) == 1
