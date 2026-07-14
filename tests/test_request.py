"""Request node + niquests adapter (S2/M4): FR-503 semantics against a
local threaded HTTP server — non-2xx-is-data (EC13), transport errors on
the error port, engine-level retry, defaults.request merge (FR-105/
EC23), full-fidelity prepared history (FR-1102/1103), timing (FR-705),
binary envelope (FR-207), max_seconds routing (FR-410/TR-8 request paths),
native-value body round-trip (TR-10). No external network anywhere.
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
from napflow.core.events import (
    HISTORY_FEATURE_CONTENT_BLOBS,
    EventStream,
    JsonlSink,
    SecretMasker,
    resolve_record_content,
)
from napflow.core.history_content import RunContentStore
from napflow.core.httpclient import HttpClient, TransportError, WireRequest
from napflow.core.models.manifest import Manifest
from test_engine import CaptureSink, end, events_of, flow, run, start

SRC = Path(__file__).resolve().parent.parent / "src" / "napflow"


class Handler(BaseHTTPRequestHandler):
    flaky_failures = 0  # /flaky: drop the connection this many times
    redirect_target = None

    def log_message(self, *args):  # keep pytest output clean
        pass

    def _send(self, status, payload, content_type="application/json"):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _redirect(self, location, *, cookie=None):
        self.send_response(302)
        self.send_header("Location", location)
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

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
        elif path == "/set-cookie":
            self.send_response(200)
            self.send_header("Set-Cookie", "sid=abc123; Path=/")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/redirect":
            self._redirect("/echo?redirected=yes", cookie="hop=redirected; Path=/")
        elif path == "/redirect-fail" and Handler.redirect_target is not None:
            self._redirect(Handler.redirect_target)
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
            self._send(200, b"x" * 200_000, "text/plain")
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
            201,
            {
                "received": body,
                "content_type": self.headers.get("Content-Type"),
                "headers": dict(self.headers),
                "raw_base64": base64.b64encode(raw).decode("ascii"),
            },
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
# Prepared-request adapter seam (FR-1103 / EC50 foundation)


def test_adapter_captures_effective_prepared_request(server):
    payload = {"user": {"id": 7}, "active": True}
    prepared = []

    async def scenario():
        client = HttpClient()
        try:
            await client.request(
                method="GET",
                url=f"{server}/set-cookie",
                timeout_s=2,
                verify_tls=True,
            )
            return await client.request(
                method="POST",
                url=f"{server}/echo",
                headers={"X-Napflow": "prepared"},
                query={"term": "a b"},
                body=payload,
                timeout_s=2,
                verify_tls=True,
                on_prepared=prepared.append,
            )
        finally:
            await client.close()

    wire = asyncio.run(scenario())

    assert len(prepared) == 1
    request = prepared[0]
    assert isinstance(request, WireRequest)
    assert request == wire.request
    assert request.method == "POST"
    assert request.url == f"{server}/echo?term=a+b"
    assert request.headers["X-Napflow"] == "prepared"
    assert request.headers["Cookie"] == "sid=abc123"
    assert request.headers["User-Agent"].startswith("niquests/")
    assert request.headers["Accept-Encoding"]
    assert request.headers["Accept"] == "*/*"
    assert request.headers["Connection"] == "keep-alive"
    assert request.headers["Content-Type"].startswith("application/json")
    raw = base64.b64decode(request.body["base64"])
    assert request.body["__binary__"] is True
    assert request.body["content_type"] == request.headers["Content-Type"]
    assert request.size_bytes == len(raw)
    assert request.headers["Content-Length"] == str(len(raw))
    assert raw == base64.b64decode(wire.body["raw_base64"])
    assert json.loads(raw) == payload
    assert wire.redirects_total == 0


def test_adapter_distinguishes_no_body_from_explicit_empty_body(server):
    async def scenario():
        client = HttpClient()
        absent = []
        empty = []
        try:
            absent_wire = await client.request(
                method="POST",
                url=f"{server}/echo",
                body=None,
                timeout_s=2,
                verify_tls=True,
                on_prepared=absent.append,
            )
            empty_wire = await client.request(
                method="POST",
                url=f"{server}/echo",
                body="",
                timeout_s=2,
                verify_tls=True,
                on_prepared=empty.append,
            )
            return absent, empty, absent_wire, empty_wire
        finally:
            await client.close()

    absent, empty, absent_wire, empty_wire = asyncio.run(scenario())

    assert absent[0].body is None
    assert absent[0].size_bytes == 0
    assert absent_wire.request.body is None
    assert empty[0].body == {
        "__binary__": True,
        "content_type": "application/octet-stream",
        "base64": "",
    }
    assert empty[0].size_bytes == 0
    assert empty_wire.request.body == empty[0].body
    assert absent_wire.body["raw_base64"] == empty_wire.body["raw_base64"] == ""


def test_adapter_captures_initial_and_final_redirect_requests(server):
    prepared = []

    async def scenario():
        client = HttpClient()
        try:
            return await client.request(
                method="GET",
                url=f"{server}/redirect",
                query={"source": "initial"},
                timeout_s=2,
                verify_tls=True,
                on_prepared=prepared.append,
            )
        finally:
            await client.close()

    wire = asyncio.run(scenario())

    assert len(prepared) == 1  # callback is engine-attempt scoped, not per hop
    assert prepared[0].url == f"{server}/redirect?source=initial"
    assert "Cookie" not in prepared[0].headers
    assert wire.request.url == f"{server}/echo?redirected=yes"
    assert wire.request.headers["Cookie"] == "hop=redirected"
    assert wire.redirects_total == 1
    assert wire.body["query"] == {"redirected": "yes"}


def test_adapter_transport_error_carries_final_redirect_request(server):
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    dead_port = probe.getsockname()[1]
    probe.close()
    Handler.redirect_target = f"http://127.0.0.1:{dead_port}/final"
    prepared = []

    async def scenario():
        client = HttpClient()
        try:
            with pytest.raises(TransportError) as excinfo:
                await client.request(
                    method="GET",
                    url=f"{server}/redirect-fail",
                    timeout_s=1,
                    verify_tls=True,
                    on_prepared=prepared.append,
                )
            return excinfo.value
        finally:
            await client.close()

    try:
        error = asyncio.run(scenario())
    finally:
        Handler.redirect_target = None

    assert error.kind in {"connection", "timeout"}
    assert len(prepared) == 1
    assert prepared[0].url == f"{server}/redirect-fail"
    assert error.request is not None
    assert error.request.url == f"http://127.0.0.1:{dead_port}/final"
    assert error.redirects_total == 1


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
    response = result.end_outputs["resp"]
    body = response["body"]
    assert body["__binary__"] is True
    assert body["content_type"] == "application/octet-stream"
    assert base64.b64decode(body["base64"]) == b"\x00\x01\xfe\xff"
    assert response["size_bytes"] == 4


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


@pytest.mark.parametrize("removed", ["body_capture_mb", "run_capture_mb"])
def test_removed_destructive_capture_settings_are_rejected(removed):
    with pytest.raises(ValueError, match=removed):
        Manifest.model_validate(
            {"schema": "napflow/v1", "defaults": {"run": {removed: 1}}}
        )


def test_large_response_is_stored_once_through_request_log_end_and_report(
    server, tmp_path
):
    f = flow(
        start(),
        {"id": "req", "type": "request", "config": {"url": f"{server}/big"}},
        {"id": "show", "type": "log", "config": {"label": "large"}},
        end({"name": "response"}),
        edges=[
            ("start.out", "req.trigger"),
            ("req.response", "show.in"),
            ("show.out", "end.response"),
        ],
    )
    log_path = tmp_path / "run.jsonl"
    store = RunContentStore(log_path)
    stream = EventStream(
        "m4-run",
        SecretMasker([], {}),
        [JsonlSink(log_path)],
        content_store=store,
    )

    result = asyncio.run(
        execute_flow(
            f,
            flow_identity="flows/m4",
            manifest=Manifest.model_validate({"schema": "napflow/v1"}),
            env={},
            env_name="dev",
            inputs={},
            stream=stream,
            flow_dir=tmp_path,
            workspace_root=tmp_path,
        )
    )
    records = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert result.state == "passed"
    runtime_response = result.end_outputs["response"]
    assert runtime_response["body"].encode() == b"x" * 200_000
    assert runtime_response["size_bytes"] == 200_000

    features = records[0]["features"]
    assert features == [HISTORY_FEATURE_CONTENT_BLOBS]
    started = events_of(records, "request_started")[0]
    finished = events_of(records, "request_finished")[0]
    logged = events_of(records, "log")[0]
    ended = events_of(records, "run_finished")[0]
    large_messages = [
        record
        for record in events_of(records, "message_emitted")
        if record["from_port"] in {"req.response", "show.out"}
    ]
    reference = finished["response"]
    assert reference["$napflow"]["kind"] == "blob"
    assert logged["value"] == ended["end_outputs"]["response"] == reference
    assert [record["value"] for record in large_messages] == [reference, reference]

    request = started["request"]
    assert request == finished["request"]
    assert request["method"] == "GET"
    assert request["url"] == f"{server}/big"
    assert request["headers"]["User-Agent"].startswith("niquests/")
    assert request["headers"]["Accept-Encoding"]
    assert request["body"] is None
    assert request["size_bytes"] == 0

    resolved = resolve_record_content(finished, features, store)["response"]
    assert resolved == runtime_response
    assert resolved["body"].encode() == b"x" * 200_000
    descriptor = reference["$napflow"]
    blobs = list(store.blob_dir.iterdir())
    assert len(blobs) == 1
    assert blobs[0].name == descriptor["hash"].removeprefix("sha256:")
    assert len(blobs[0].read_bytes()) == descriptor["bytes"]
    assert "__truncated__" not in log_path.read_text(encoding="utf-8")
    assert not events_of(records, "capture_warning")

    from napflow.cli.report import write_report

    report_path = write_report(
        "json",
        log_path,
        "flows/m4",
        result,
        masker=SecretMasker([], {}),
    )
    assert report_path is not None
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["features"] == features
    assert report["end_outputs"]["response"] == reference
    assert (
        resolve_record_content(
            {"event": "run_finished", "end_outputs": report["end_outputs"]},
            report["features"],
            store,
        )["end_outputs"]["response"]
        == runtime_response
    )
    assert len(list(store.blob_dir.iterdir())) == 1


def test_request_history_is_raw_and_presentation_headers_are_redacted(server):
    f = request_flow(
        {"url": f"{server}/echo", "headers": {"Authorization": "Bearer sk-hidden-1"}}
    )
    raw = CaptureSink()
    shown = CaptureSink()
    masker = SecretMasker(["*TOKEN*"], {"API_TOKEN": "sk-hidden-1"})
    result = asyncio.run(
        execute_flow(
            f,
            flow_identity="flows/t",
            manifest=Manifest.model_validate({"schema": "napflow/v1"}),
            env={"API_TOKEN": "sk-hidden-1"},
            env_name="dev",
            inputs={},
            stream=EventStream("r", masker, [raw], presentation_sinks=[shown]),
        )
    )
    assert result.state == "passed"
    started = events_of(raw.records, "request_started")[0]
    presented = events_of(shown.records, "request_started")[0]
    assert started["method"] == presented["method"] == "GET"
    assert started["request"]["headers"]["Authorization"] == "Bearer sk-hidden-1"
    assert presented["request"]["headers"]["Authorization"] == "Bearer ***"
    assert "Authorization" in presented["request"]["headers"]


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
