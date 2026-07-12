"""Request node + niquests adapter (S2/M4): FR-503 semantics against a
local threaded HTTP server — non-2xx-is-data (EC13), transport errors on
the error port, engine-level retry, defaults.request merge (FR-105/
EC23), capture valves (FR-703/706), timing (FR-705), binary envelope
(FR-207), max_seconds routing (FR-410/TR-8 request paths), native-value
body round-trip (TR-10). No external network anywhere.
"""

import asyncio
import base64
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import pytest

from napflow.core.engine import execute_flow
from napflow.core.events import EventStream, SecretMasker
from napflow.core.models.manifest import Manifest
from test_engine import CaptureSink, end, events_of, flow, run, start

SRC = Path(__file__).resolve().parent.parent / "src" / "napflow"


class Handler(BaseHTTPRequestHandler):
    flaky_failures = 0  # /flaky: drop the connection this many times

    def log_message(self, *args):  # keep pytest output clean
        pass

    def _send(self, status, payload, content_type="application/json"):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802 (http.server API)
        path = urlparse(self.path).path
        if path == "/json":
            self._send(200, {"id": "abc123", "ok": True})
        elif path == "/notfound":
            self._send(404, {"error": "nope"})
        elif path == "/echo":
            self._send(
                200,
                {
                    "headers": dict(self.headers),
                    "query": dict(parse_qsl(urlparse(self.path).query)),
                },
            )
        elif path == "/slow":
            time.sleep(2)
            self._send(200, {"late": True})
        elif path == "/flaky":
            if Handler.flaky_failures > 0:
                Handler.flaky_failures -= 1
                self.connection.close()  # abort mid-handshake → transport error
                return
            self._send(200, {"recovered": True})
        elif path == "/bin":
            self._send(200, b"\x00\x01\xfe\xff", "application/octet-stream")
        elif path == "/big":
            self._send(200, b"x" * 4096, "text/plain")
        else:
            self._send(404, {"error": "no route"})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except ValueError:
            body = raw.decode("utf-8", "replace")
        self._send(
            201, {"received": body, "content_type": self.headers.get("Content-Type")}
        )


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    thread.join(timeout=5)


def request_flow(config, *, node_extra=None, wire_error_port=False):
    node = {"id": "req", "type": "request", "config": config} | (node_extra or {})
    ports = [{"name": "resp", "required": False}]
    edges = [("start.out", "req.trigger"), ("req.response", "end.resp")]
    if wire_error_port:
        ports.append({"name": "err", "required": False})
        edges.append(("req.error", "end.err"))
    return flow(start(), node, end(*ports), edges=edges)


# --------------------------------------------------------------------------
# NFR-09: the adapter is the only niquests import in the package


def test_niquests_isolated_to_the_adapter():
    offenders = [
        p
        for p in SRC.rglob("*.py")
        if p.name != "httpclient.py"
        and any(
            token in p.read_text(encoding="utf-8")
            for token in ("import niquests", "from niquests")
        )
    ]
    assert offenders == []


# --------------------------------------------------------------------------
# Response semantics (EC13)


def test_2xx_json_response_shape(server):
    result, records = run(request_flow({"url": f"{server}/json"}))
    assert result.state == "passed"
    resp = result.end_outputs["resp"]
    assert resp["status"] == 200
    assert resp["body"] == {"id": "abc123", "ok": True}  # parsed, native
    assert resp["attempt"] == 1
    assert resp["elapsed_ms"] > 0
    finished = events_of(records, "request_finished")[0]
    assert finished["status"] == 200
    assert finished["timing"]["total_ms"] > 0
    assert finished["retries_total"] == 0


def test_non_2xx_is_a_valid_response(server):
    result, records = run(request_flow({"url": f"{server}/notfound"}))
    assert result.state == "passed"  # EC13: 404 emits on `response`
    assert result.end_outputs["resp"]["status"] == 404
    assert not events_of(records, "request_failed")


def test_connection_refused_routes_to_error_port(server):
    # grab a port with nothing listening
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    dead_port = probe.getsockname()[1]
    probe.close()
    url = f"http://127.0.0.1:{dead_port}/"
    wired, _ = run(request_flow({"url": url}, wire_error_port=True))
    assert wired.state == "passed"  # handled: error is data (EC13)
    assert wired.end_outputs["err"]["error_kind"] == "connection"
    unwired, _ = run(request_flow({"url": url}))
    assert unwired.state == "failed"  # unhandled error port ⇒ failed
    assert unwired.unhandled_errors[0]["port"] == "error"


def test_retry_recovers_from_transport_failure(server):
    Handler.flaky_failures = 1
    f = request_flow(
        {"url": f"{server}/flaky", "retry": {"max_attempts": 3}},
    )
    result, records = run(f)
    assert result.state == "passed"
    assert result.end_outputs["resp"]["body"] == {"recovered": True}
    assert result.end_outputs["resp"]["attempt"] == 2
    failed = events_of(records, "request_failed")
    assert len(failed) == 1 and failed[0]["will_retry"] is True
    assert events_of(records, "request_finished")[0]["retries_total"] == 1


def test_transport_timeout_after_retries(server):
    f = request_flow(
        {"url": f"{server}/slow", "timeout_s": 0.1, "retry": {"max_attempts": 2}},
        wire_error_port=True,
    )
    result, records = run(f)
    assert result.state == "passed"
    assert result.end_outputs["err"]["error_kind"] == "timeout"
    failed = events_of(records, "request_failed")
    assert [e["will_retry"] for e in failed] == [True, False]


def test_max_seconds_hard_stop_routes_to_error_port(server):
    # D24/TR-8: the node ceiling cancels ALL attempts at once and is
    # error-port data; wired = handled = run passes
    f = request_flow(
        {"url": f"{server}/slow"},
        node_extra={"max_seconds": 0.1},
        wire_error_port=True,
    )
    result, _ = run(f)
    assert result.state == "passed"
    assert result.end_outputs["err"]["error_kind"] == "timeout"


# --------------------------------------------------------------------------
# Config templating + native-value body (TR-10)


def test_templated_url_headers_query(server):
    f = flow(
        start({"name": "page", "type": "number"}),
        {
            "id": "req",
            "type": "request",
            "config": {
                "url": "{{ env.BASE }}/echo",
                "headers": {"X-Token": "Bearer {{ env.TOKEN }}"},
                "query": {"page": "{{ inputs.page }}", "n": 3},
            },
        },
        end({"name": "resp", "required": False}),
        edges=[("start.out", "req.trigger"), ("req.response", "end.resp")],
    )
    result, _ = run(
        f,
        inputs={"page": 7},
        env={"BASE": server, "TOKEN": "tok-12345"},
    )
    assert result.state == "passed"
    echoed = result.end_outputs["resp"]["body"]
    assert echoed["headers"]["X-Token"] == "Bearer tok-12345"
    assert echoed["query"] == {"page": "7", "n": "3"}  # stringified (D25)


def test_native_body_round_trip(server):
    # TR-10: a dict crosses config → wire → response as a dict, and a
    # single-expression body passes the whole structure natively
    f = flow(
        start({"name": "payload", "type": "object"}),
        {
            "id": "req",
            "type": "request",
            "config": {
                "url": "{{ env.BASE }}/echo",
                "method": "POST",
                "body": "{{ inputs.payload }}",
            },
        },
        end({"name": "resp", "required": False}),
        edges=[("start.out", "req.trigger"), ("req.response", "end.resp")],
    )
    payload = {"user": {"id": 7, "tags": ["a", "b"]}, "active": True}
    result, _ = run(f, inputs={"payload": payload}, env={"BASE": server})
    assert result.state == "passed"
    body = result.end_outputs["resp"]["body"]
    assert body["received"] == payload  # dict, not a repr string
    assert body["content_type"].startswith("application/json")


def test_string_body_sent_verbatim(server):
    f = request_flow({"url": f"{server}/echo", "method": "POST", "body": "plain text"})
    result, _ = run(f)
    assert result.end_outputs["resp"]["body"]["received"] == "plain text"


# --------------------------------------------------------------------------
# defaults.request merge (FR-105, EC23)


def manifest_with_request_defaults(**request_defaults):
    return Manifest.model_validate(
        {"schema": "napflow/v1", "defaults": {"request": request_defaults}}
    )


def test_default_headers_apply_and_node_headers_replace_wholesale(server):
    mani = manifest_with_request_defaults(headers={"X-Def": "{{ env.TAG }}"})
    inherited, _ = run(
        request_flow({"url": f"{server}/echo"}), mani=mani, env={"TAG": "dev"}
    )
    assert inherited.end_outputs["resp"]["body"]["headers"]["X-Def"] == "dev"
    overridden, _ = run(
        request_flow({"url": f"{server}/echo", "headers": {"X-Own": "1"}}),
        mani=mani,
        env={"TAG": "dev"},
    )
    echoed = overridden.end_outputs["resp"]["body"]["headers"]
    assert "X-Def" not in echoed  # shallow merge: node key wins WHOLESALE
    assert echoed["X-Own"] == "1"


def test_default_retry_replaced_by_node_block(server):
    Handler.flaky_failures = 5
    mani = manifest_with_request_defaults(retry={"max_attempts": 3})
    f = request_flow(
        {"url": f"{server}/flaky", "retry": {"max_attempts": 1}},
        wire_error_port=True,
    )
    result, records = run(f, mani=mani)
    Handler.flaky_failures = 0
    assert result.state == "passed"
    assert result.end_outputs["err"]["error_kind"] == "connection"
    assert len(events_of(records, "request_started")) == 1  # node block won


def test_default_referencing_inputs_is_node_error(server):
    # EC23: defaults.request sees env/run only — inputs is undefined
    mani = manifest_with_request_defaults(headers={"X-Bad": "{{ inputs.x }}"})
    result, _ = run(request_flow({"url": f"{server}/json"}), mani=mani)
    # the inheriting request fails; error port unwired ⇒ run failed
    assert result.state == "failed"
    assert result.unhandled_errors[0]["node"] == "req"


# --------------------------------------------------------------------------
# Binary envelope + capture valves (FR-207/703/706)


def test_binary_response_envelope(server):
    result, _ = run(request_flow({"url": f"{server}/bin"}))
    body = result.end_outputs["resp"]["body"]
    assert body["__binary__"] is True
    assert body["content_type"] == "application/octet-stream"
    assert base64.b64decode(body["base64"]) == b"\x00\x01\xfe\xff"


def test_valid_binary_request_envelope_is_decoded_strictly(server):
    body = {
        "__binary__": True,
        "content_type": "application/octet-stream",
        "base64": base64.b64encode(b"\x00\x01\x02").decode("ascii"),
    }
    result, _ = run(
        request_flow({"url": f"{server}/echo", "method": "POST", "body": body})
    )

    assert result.state == "passed"
    echoed = result.end_outputs["resp"]["body"]
    assert echoed["received"] == "\x00\x01\x02"
    assert echoed["content_type"] == "application/octet-stream"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            {"__binary__": True, "content_type": "application/octet-stream"},
            "missing base64",
        ),
        (
            {
                "__binary__": True,
                "content_type": "application/octet-stream",
                "base64": "not base64!",
            },
            "canonical base64",
        ),
        (
            {
                "__binary__": True,
                "content_type": "application/octet-stream",
                "base64": "AA==",
                "extra": True,
            },
            "unexpected extra",
        ),
        (
            {"__binary__": True, "content_type": None, "base64": "AA=="},
            "content_type must be a non-empty string",
        ),
    ],
)
def test_malformed_binary_request_envelope_routes_to_wired_error_once(
    server, body, message
):
    f = request_flow(
        {
            "url": f"{server}/echo",
            "method": "POST",
            "body": body,
            "retry": {"max_attempts": 3},
        },
        wire_error_port=True,
    )

    result, records = run(f)

    assert result.state == "passed"
    assert result.end_outputs["err"]["error_kind"] == "request_encoding"
    assert message in result.end_outputs["err"]["message"]
    failed = events_of(records, "request_failed")
    assert len(failed) == 1
    assert failed[0]["error_kind"] == "request_encoding"
    assert failed[0]["will_retry"] is False
    assert not events_of(records, "request_finished")


def test_malformed_binary_request_envelope_unwired_is_node_failure(server):
    body = {
        "__binary__": True,
        "content_type": "application/octet-stream",
        "base64": "%%%",
    }

    result, records = run(
        request_flow({"url": f"{server}/echo", "method": "POST", "body": body})
    )

    assert result.state == "failed"
    assert result.unhandled_errors[0]["kind"] == "unhandled_error_port"
    assert "request_encoding" in result.unhandled_errors[0]["message"]
    assert events_of(records, "run_finished")[0]["state"] == "failed"


def test_body_capture_valve_truncates_event_not_port_value(server):
    tiny = Manifest.model_validate(
        {
            "schema": "napflow/v1",
            "defaults": {"run": {"body_capture_mb": 0.0001}},  # ~105 bytes
        }
    )
    result, records = run(request_flow({"url": f"{server}/big"}), mani=tiny)
    assert result.state == "passed"
    assert result.end_outputs["resp"]["body"] == "x" * 4096  # port: full
    captured = events_of(records, "request_finished")[0]["body"]
    assert captured["__truncated__"] is True
    assert captured["size_bytes"] == 4096
    assert len(captured["prefix"]) < 4096


def test_run_capture_valve_and_warning(server):
    tiny_run = Manifest.model_validate(
        {
            "schema": "napflow/v1",
            "defaults": {"run": {"run_capture_mb": 0.005}},  # ~5.2 KB total
        }
    )
    f = flow(
        start(),
        {"id": "one", "type": "request", "config": {"url": f"{server}/big"}},
        {"id": "two", "type": "request", "config": {"url": f"{server}/big"}},
        end({"name": "a", "required": False}, {"name": "b", "required": False}),
        edges=[
            ("start.out", "one.trigger"),
            ("one.response", "two.trigger"),
            ("two.response", "end.b"),
        ],
    )
    result, records = run(f, mani=tiny_run)
    assert result.state == "passed"
    assert events_of(records, "capture_warning")
    bodies = [e["body"] for e in events_of(records, "request_finished")]
    assert bodies[0] == "x" * 4096  # first fits the run budget
    assert bodies[1]["__truncated__"] is True  # second exceeds it


def test_secret_masked_in_request_events(server):
    f = request_flow(
        {"url": f"{server}/echo", "headers": {"Authorization": "Bearer sk-hidden-1"}}
    )
    sink = CaptureSink()
    masker = SecretMasker(["*TOKEN*"], {"API_TOKEN": "sk-hidden-1"})
    result = asyncio.run(
        execute_flow(
            f,
            flow_identity="flows/t",
            manifest=Manifest.model_validate({"schema": "napflow/v1"}),
            env={"API_TOKEN": "sk-hidden-1"},
            env_name="dev",
            inputs={},
            stream=EventStream("r", masker, [sink]),
        )
    )
    assert result.state == "passed"
    started = events_of(sink.records, "request_started")[0]
    assert started["headers"]["Authorization"] == "Bearer ***"  # born masked


# --------------------------------------------------------------------------
# S2 DoD: a linear request→assert flow runs headless with correct exit codes


DOD_FLOW = """\
schema: "napflow/v1"
flow:
  name: "job"
env:
  required: ["BASE_URL"]
nodes:
  - id: "start"
    type: "start"
  - id: "req"
    type: "request"
    config:
      url: "{{ env.BASE_URL }}/json"
  - id: "check"
    type: "assert"
    config:
      checks:
        - {kind: "status", equals: STATUS}
        - {kind: "expr", expr: "trigger.value.body.id", op: "present"}
  - id: "end"
    type: "end"
    config:
      ports:
        - {name: "job"}
edges:
  - {from: "start.out", to: "req.trigger"}
  - {from: "req.response", to: "check.in"}
  - {from: "check.passed", to: "end.job"}
"""


def test_s2_dod_request_assert_headless(server, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from napflow.cli.main import app

    ws = tmp_path / "ws"
    for identity, status in (("good", 200), ("bad", 999)):
        (ws / "flows" / identity).mkdir(parents=True)
        flow_yaml = DOD_FLOW.replace("STATUS", str(status))
        (ws / "flows" / identity / "flow.yaml").write_text(flow_yaml, encoding="utf-8")
    (ws / "envs").mkdir()
    (ws / "napflow.yaml").write_text('schema: "napflow/v1"\n', encoding="utf-8")
    (ws / "envs" / "dev.env").write_text(f"BASE_URL={server}\n", encoding="utf-8")
    monkeypatch.chdir(ws)

    runner = CliRunner()
    passed = runner.invoke(app, ["run", "flows/good", "--env", "dev"])
    assert passed.exit_code == 0, passed.stderr
    assert json.loads(passed.stdout)["job"]["body"]["id"] == "abc123"

    failed = runner.invoke(app, ["run", "flows/bad", "--env", "dev"])
    assert failed.exit_code == 1  # assert-driven exit code, headless
