"""`napf run` (S2/M5, FR-803/804): headless runs with stdout JSON,
stderr logs, JSONL history, reports, and run-state exit codes — the S2
DoD: a linear flow runs headless with correct exit codes."""

import json
import tracemalloc
from pathlib import Path
from xml.etree import ElementTree

import pytest
from typer.testing import CliRunner

import napflow.cli.main as cli_main
import napflow.cli.report as cli_report
from napflow.cli.main import app
from napflow.core.engine import RunResult
from napflow.core.events import EventStream, SecretMasker

runner = CliRunner()

MANIFEST = """\
schema: "napflow/v1"
environments:
  default: "dev"
  secrets: ["*TOKEN*"]
"""

HELLO_FLOW = """\
schema: "napflow/v1"
flow:
  name: "hello"
nodes:
  - id: "start"
    type: "start"
    config:
      ports:
        - {name: "msg", type: "string", default: "{{ env.GREETING }}"}
  - id: "check"
    type: "assert"
    config:
      checks:
        - {kind: "expr", expr: "trigger.value.msg", op: "present"}
  - id: "end"
    type: "end"
    config:
      ports:
        - {name: "result"}
edges:
  - {from: "start.out", to: "check.in"}
  - {from: "check.passed", to: "end.result"}
"""


def make_workspace(tmp_path: Path, flow_yaml=HELLO_FLOW, manifest=MANIFEST) -> Path:
    ws = tmp_path / "ws"
    (ws / "flows" / "hello").mkdir(parents=True)
    (ws / "envs").mkdir()
    (ws / "napflow.yaml").write_text(manifest, encoding="utf-8")
    (ws / "envs" / "dev.env").write_text(
        "GREETING=hello\nAPI_TOKEN=supersecret-token\n", encoding="utf-8"
    )
    (ws / "flows" / "hello" / "flow.yaml").write_text(flow_yaml, encoding="utf-8")
    return ws


@pytest.fixture
def ws(tmp_path, monkeypatch) -> Path:
    workspace = make_workspace(tmp_path)
    monkeypatch.chdir(workspace)
    return workspace


def jsonl_records(ws: Path, flow: str = "hello") -> list[dict]:
    logs = sorted((ws / ".napflow" / "runs" / "flows" / flow).glob("*.jsonl"))
    assert logs, "no run log written"
    lines = logs[-1].read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


# --------------------------------------------------------------------------
# Happy path (FR-803)


def test_run_passes_with_stdout_json_and_jsonl(ws):
    result = runner.invoke(app, ["run", "flows/hello", "-i", "msg=hi"])
    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout) == {"result": {"msg": "hi"}}
    assert "passed — asserts: 1 passed, 0 failed" in result.stderr
    records = jsonl_records(ws)
    assert records[0]["event"] == "run_started"
    assert records[-1]["event"] == "run_finished"
    assert records[-1]["state"] == "passed"


def test_run_rejects_history_directory_symlink_escape(ws, tmp_path):
    outside = tmp_path / "outside-runs"
    outside.mkdir()
    flow_runs = ws / ".napflow" / "runs" / "flows"
    flow_runs.mkdir(parents=True)
    try:
        (flow_runs / "hello").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/privilege level")

    result = runner.invoke(app, ["run", "flows/hello", "-i", "msg=hi"])
    assert result.exit_code == 2
    assert "outside" in result.stderr
    assert "Traceback" not in result.stderr
    assert list(outside.iterdir()) == []


def test_run_uses_default_env_profile(ws):
    result = runner.invoke(app, ["run", "flows/hello"])
    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout) == {"result": {"msg": "hello"}}  # dev.env


def test_run_input_json_with_i_override(ws):
    result = runner.invoke(
        app,
        ["run", "flows/hello", "--input-json", '{"msg": "from-json"}'],
    )
    assert json.loads(result.stdout) == {"result": {"msg": "from-json"}}
    result = runner.invoke(
        app,
        ["run", "flows/hello", "--input-json", '{"msg": "a"}', "-i", "msg=b"],
    )
    assert json.loads(result.stdout) == {"result": {"msg": "b"}}  # -i wins


# --------------------------------------------------------------------------
# Exit codes (FR-406 surface)


def test_failed_assert_exits_1(ws):
    failing = HELLO_FLOW.replace(
        '- {kind: "expr", expr: "trigger.value.msg", op: "present"}',
        '- {kind: "expr", expr: "trigger.value.msg", op: "equals", value: "nope"}',
    )
    (ws / "flows" / "hello" / "flow.yaml").write_text(failing, encoding="utf-8")
    result = runner.invoke(app, ["run", "flows/hello", "-i", "msg=hi"])
    assert result.exit_code == 1
    assert "failed" in result.stderr


def test_check_error_blocks_run_with_exit_2(ws):
    broken = HELLO_FLOW.replace('to: "check.in"', 'to: "ghost.in"')
    (ws / "flows" / "hello" / "flow.yaml").write_text(broken, encoding="utf-8")
    result = runner.invoke(app, ["run", "flows/hello"])
    assert result.exit_code == 2
    assert "E003" in result.stderr
    assert not (ws / ".napflow" / "runs").exists()  # blocked before any run


def test_unknown_flow_exits_2(ws):
    result = runner.invoke(app, ["run", "flows/ghost"])
    assert result.exit_code == 2
    assert "flows/hello" in result.stderr  # discovered flows listed


def test_missing_explicit_env_exits_2(ws):
    result = runner.invoke(app, ["run", "flows/hello", "--env", "staging"])
    assert result.exit_code == 2
    assert "staging" in result.stderr


def test_bad_input_pair_exits_2(ws):
    result = runner.invoke(app, ["run", "flows/hello", "-i", "no-equals-here"])
    assert result.exit_code == 2


def test_timeout_flag_exits_2(ws):
    slow = HELLO_FLOW.replace(
        '- id: "check"\n    type: "assert"\n    config:\n      checks:\n'
        '        - {kind: "expr", expr: "trigger.value.msg", op: "present"}',
        '- id: "check"\n    type: "delay"\n    config:\n      seconds: 30',
    ).replace('from: "check.passed"', 'from: "check.out"')
    (ws / "flows" / "hello" / "flow.yaml").write_text(slow, encoding="utf-8")
    result = runner.invoke(app, ["run", "flows/hello", "--timeout", "0.1"])
    assert result.exit_code == 2
    records = jsonl_records(ws)
    assert records[-1]["error_reason"] == "run_timeout"  # report still written


# --------------------------------------------------------------------------
# D35 boundary: stdout/history raw, terminal/reports redacted


def test_stdout_and_private_jsonl_preserve_raw_local_truth(ws):
    leaky = HELLO_FLOW.replace("{{ env.GREETING }}", "{{ env.API_TOKEN }}")
    (ws / "flows" / "hello" / "flow.yaml").write_text(leaky, encoding="utf-8")
    result = runner.invoke(app, ["run", "flows/hello"])
    assert result.exit_code == 0, result.stderr
    # stdout is the functional output — `| jq .token` must keep working
    assert json.loads(result.stdout) == {"result": {"msg": "supersecret-token"}}
    finished = jsonl_records(ws)[-1]
    assert finished["end_outputs"]["result"]["msg"] == "supersecret-token"


# --------------------------------------------------------------------------
# Reports (FR-804)


def report_manifest(kind: str) -> str:
    return MANIFEST + f'defaults:\n  run:\n    report: "{kind}"\n'


def retained_report_manifest(kind: str) -> str:
    return (
        MANIFEST
        + f'defaults:\n  run:\n    report: "{kind}"\n    history: 1\n'
    )


UNSET_SECRET_FLOW = """\
schema: "napflow/v1"
flow:
  name: "unset-secret"
nodes:
  - id: "start"
    type: "start"
  - id: "recall"
    type: "get"
    config:
      name: "supersecret-token"
  - id: "end"
    type: "end"
    config:
      ports:
        - {name: "result", required: false}
edges:
  - {from: "start.out", to: "recall.trigger"}
  - {from: "recall.value", to: "end.result"}
"""


def test_error_terminal_and_report_redact_while_history_stays_raw(
    tmp_path, monkeypatch
):
    ws = make_workspace(
        tmp_path,
        flow_yaml=UNSET_SECRET_FLOW,
        manifest=report_manifest("json"),
    )
    monkeypatch.chdir(ws)

    result = runner.invoke(app, ["run", "flows/hello"])

    assert result.exit_code == 1
    assert "supersecret-token" not in result.stderr
    assert "variable '***' was never set" in result.stderr
    finished = jsonl_records(ws)[-1]
    assert "supersecret-token" in finished["unhandled_errors"][0]["message"]
    report = next(
        (ws / ".napflow" / "runs" / "flows" / "hello").glob("*.report.json")
    )
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["state"] == "failed"
    assert "supersecret-token" not in payload["unhandled_errors"][0]["message"]
    assert "***" in payload["unhandled_errors"][0]["message"]


def test_json_report(tmp_path, monkeypatch):
    leaky = HELLO_FLOW.replace("{{ env.GREETING }}", "{{ env.API_TOKEN }}")
    ws = make_workspace(
        tmp_path, flow_yaml=leaky, manifest=report_manifest("json")
    )
    monkeypatch.chdir(ws)
    seen_presentation_sinks = []
    real_open = cli_main.open_run_stream

    def observed_open(
        workspace, prepared, *, extra_sinks=(), presentation_sinks=()
    ):
        presentation_sinks_seen = list(presentation_sinks)
        seen_presentation_sinks.extend(presentation_sinks_seen)
        return real_open(
            workspace,
            prepared,
            extra_sinks=extra_sinks,
            presentation_sinks=presentation_sinks_seen,
        )

    monkeypatch.setattr(cli_main, "open_run_stream", observed_open)
    result = runner.invoke(app, ["run", "flows/hello"])
    assert result.exit_code == 0, result.stderr
    assert len(seen_presentation_sinks) == 1
    assert type(seen_presentation_sinks[0]).__name__ == "_LogEcho"
    reports = list((ws / ".napflow" / "runs" / "flows" / "hello").glob("*.report.json"))
    assert len(reports) == 1
    report_text = reports[0].read_text(encoding="utf-8")
    assert "supersecret-token" not in report_text
    payload = json.loads(report_text)
    assert payload["flow"] == "flows/hello"
    assert payload["state"] == "passed"
    assert payload["exit_code"] == 0
    assert payload["asserts"] == {"passed": 1, "failed": 0}
    assert payload["end_outputs"] == {"result": {"msg": "***"}}
    finished = jsonl_records(ws)[-1]
    assert finished["end_outputs"] == {
        "result": {"msg": "supersecret-token"}
    }


def test_junit_report_counts_match(tmp_path, monkeypatch):
    failing = HELLO_FLOW.replace(
        '- {kind: "expr", expr: "trigger.value.msg", op: "present"}',
        '- {kind: "expr", expr: "trigger.value.msg", op: "present"}\n'
        '        - {kind: "expr", expr: "trigger.value.msg", op: "equals", value: "x"}',
    )
    ws = make_workspace(tmp_path, flow_yaml=failing, manifest=report_manifest("junit"))
    monkeypatch.chdir(ws)
    result = runner.invoke(app, ["run", "flows/hello", "-i", "msg=hi"])
    assert result.exit_code == 1
    reports = list((ws / ".napflow" / "runs" / "flows" / "hello").glob("*.junit.xml"))
    assert len(reports) == 1
    suite = ElementTree.parse(reports[0]).getroot()
    assert suite.tag == "testsuite"
    assert suite.get("failures") == "1"
    cases = suite.findall("testcase")
    assert len(cases) == int(suite.get("tests"))
    assert any(case.find("failure") is not None for case in cases)


@pytest.mark.parametrize(
    ("kind", "suffix"),
    [("json", ".report.json"), ("junit", ".junit.xml")],
)
def test_retention_keeps_one_complete_log_and_matching_report(
    tmp_path, monkeypatch, kind, suffix
):
    ws = make_workspace(tmp_path, manifest=retained_report_manifest(kind))
    monkeypatch.chdir(ws)
    for value in ("one", "two"):
        result = runner.invoke(app, ["run", "flows/hello", "-i", f"msg={value}"])
        assert result.exit_code == 0, result.stderr

    runs = ws / ".napflow" / "runs" / "flows" / "hello"
    logs = list(runs.glob("*.jsonl"))
    reports = list(runs.glob(f"*{suffix}"))
    assert len(logs) == len(reports) == 1
    assert reports[0].name == f"{logs[0].stem}{suffix}"
    assert list(runs.glob("*.active")) == []
    assert list(runs.glob("*.deleting")) == []


@pytest.mark.parametrize(
    ("kind", "suffix"),
    [("json", ".report.json"), ("junit", ".junit.xml")],
)
def test_report_publication_does_not_follow_target_symlink(
    tmp_path, monkeypatch, kind, suffix
):
    ws = make_workspace(tmp_path, manifest=report_manifest(kind))
    monkeypatch.chdir(ws)
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    real_open = cli_main.open_run_stream
    planted = None

    def planted_open(
        workspace, prepared, *, extra_sinks=(), presentation_sinks=()
    ):
        nonlocal planted
        opened = real_open(
            workspace,
            prepared,
            extra_sinks=extra_sinks,
            presentation_sinks=presentation_sinks,
        )
        planted = opened.log_path.with_name(f"{opened.run_id}{suffix}")
        planted.symlink_to(outside)
        return opened

    monkeypatch.setattr(cli_main, "open_run_stream", planted_open)

    result = runner.invoke(app, ["run", "flows/hello"])

    assert result.exit_code == 0, result.stderr
    assert outside.read_text(encoding="utf-8") == "keep"
    assert planted is not None and planted.is_file() and not planted.is_symlink()


def test_history_finalization_failure_does_not_replace_run_result(
    tmp_path, monkeypatch
):
    ws = make_workspace(tmp_path)
    monkeypatch.chdir(ws)

    def fail_finalization(_opened, *, completed):
        raise OSError(f"finalization failed ({completed=})")

    monkeypatch.setattr(cli_main, "finalize_run_history", fail_finalization)

    result = runner.invoke(app, ["run", "flows/hello"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"result": {"msg": "hello"}}
    assert "warning: run history finalization failed" in result.stderr


def test_stream_close_failure_preserves_result_and_abandons_history(
    ws, monkeypatch
):
    def fail_close(_self):
        raise OSError("close failed")

    monkeypatch.setattr(cli_main._LogEcho, "close", fail_close)

    result = runner.invoke(app, ["run", "flows/hello", "-i", "msg=hi"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"result": {"msg": "hi"}}
    assert "warning: run stream close failed" in result.stderr
    runs = ws / ".napflow" / "runs" / "flows" / "hello"
    assert list(runs.glob("*.active")) == []
    assert len(list(runs.glob("*.incomplete"))) == 1
    assert list(runs.glob("*.complete.json")) == []


def test_keyboard_interrupt_during_stream_close_exits_130_and_abandons_history(
    ws, monkeypatch
):
    def interrupt_close(_self):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_main._LogEcho, "close", interrupt_close)

    result = runner.invoke(app, ["run", "flows/hello", "-i", "msg=hi"])

    assert result.exit_code == 130
    assert "aborted" in result.stderr
    runs = ws / ".napflow" / "runs" / "flows" / "hello"
    assert list(runs.glob("*.active")) == []
    assert len(list(runs.glob("*.incomplete"))) == 1
    assert list(runs.glob("*.complete.json")) == []


def test_fresh_keyboard_interrupt_during_adapter_reclose_exits_130(
    ws, monkeypatch
):
    original_close = EventStream.close
    close_calls = 0

    def interrupt_second_close(self):
        nonlocal close_calls
        close_calls += 1
        if close_calls == 2:
            raise KeyboardInterrupt
        return original_close(self)

    monkeypatch.setattr(EventStream, "close", interrupt_second_close)

    result = runner.invoke(app, ["run", "flows/hello", "-i", "msg=hi"])

    assert result.exit_code == 130
    assert "aborted" in result.stderr
    runs = ws / ".napflow" / "runs" / "flows" / "hello"
    assert list(runs.glob("*.active")) == []
    assert len(list(runs.glob("*.incomplete"))) == 1
    assert list(runs.glob("*.complete.json")) == []


def test_junit_report_sanitizes_xml_attributes(tmp_path):
    log_path = tmp_path / "xml.jsonl"
    secret = "supersecret-token"
    assertion_name = "check & < > \" ' \x01"
    error_message = f"failure {secret} & < > \" ' \ud800"
    records = [
        {
            "event": "assert_result",
            "node": "check",
            "check": assertion_name,
            "expected": secret,
            "actual": f"value {secret}",
            "passed": False,
        },
        {
            "event": "run_finished",
            "duration_ms": 1.0,
            "unhandled_errors": [
                {"node": "check", "kind": "control", "message": error_message}
            ],
        },
    ]
    with log_path.open("w", encoding="utf-8") as log:
        for record in records:
            log.write(json.dumps(record, ensure_ascii=True) + "\n")
    result = RunResult(
        state="failed",
        end_outputs={},
        asserts_passed=0,
        asserts_failed=1,
        unhandled_errors=[],
        nodes_never_fired=[],
        duration_ms=1.0,
    )

    report_path = cli_report.write_report(
        "junit",
        log_path,
        "flows/xml",
        result,
        masker=SecretMasker(["*TOKEN*"], {"API_TOKEN": secret}),
    )

    assert report_path is not None
    suite = ElementTree.parse(report_path).getroot()
    assert suite.findall("testcase")[0].get("name") == assertion_name.replace(
        "\x01", "\ufffd"
    )
    assert suite.find("testcase/failure") is not None
    assert suite.find("testcase/error").get("message") == error_message.replace(
        secret, "***"
    ).replace("\ud800", "\ufffd")
    assert secret not in report_path.read_text(encoding="utf-8")


def test_junit_report_memory_does_not_scale_with_assertion_count(tmp_path):
    record_count = 100_000
    log_path = tmp_path / "bounded.jsonl"
    with log_path.open("w", encoding="utf-8") as log:
        for seq in range(record_count):
            log.write(
                json.dumps(
                    {
                        "event": "assert_result",
                        "seq": seq,
                        "node": "check",
                        "check": "present",
                        "expected": None,
                        "actual": "value",
                        "passed": True,
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )
        log.write(
            json.dumps(
                {
                    "event": "run_finished",
                    "duration_ms": 1.0,
                    "unhandled_errors": [],
                },
                separators=(",", ":"),
            )
            + "\n"
        )
    result = RunResult(
        state="passed",
        end_outputs={},
        asserts_passed=record_count,
        asserts_failed=0,
        unhandled_errors=[],
        nodes_never_fired=[],
        duration_ms=1.0,
    )
    tracemalloc.start()
    try:
        report_path = cli_report.write_report(
            "junit",
            log_path,
            "flows/bounded",
            result,
            masker=SecretMasker([], {}),
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert report_path is not None
    assert peak < 5_000_000
    tests = None
    cases = 0
    for event, element in ElementTree.iterparse(report_path, events=("start", "end")):
        if event == "start" and element.tag == "testsuite":
            tests = element.get("tests")
        elif event == "end" and element.tag == "testcase":
            cases += 1
            element.clear()
    assert tests == str(record_count)
    assert cases == record_count


def test_first_touch_smoke_flow_offline(tmp_path, monkeypatch):
    # EC34/FR-107 DoD: a fresh `napf init` workspace runs its smoke
    # flow (fixture→python→assert) OFFLINE with exit 0 — the fixture
    # auto-seed drives the run (the start node is fully unwired)
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    monkeypatch.chdir(fresh)
    assert runner.invoke(app, ["init"]).exit_code == 0
    result = runner.invoke(app, ["run", "flows/smoke"])
    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["summary"] == {"total": 2, "names": ["Ada", "Linus"]}
    assert payload["failed_check"] is None
    assert payload["python_error"] is None
    assert "passed — asserts: 2 passed, 0 failed" in result.stderr


LOG_FLOW = """\
schema: "napflow/v1"
flow:
  name: "logging"
nodes:
  - id: "start"
    type: "start"
    config:
      ports:
        - {name: "msg", type: "string"}
  - id: "show"
    type: "log"
    config:
      label: "supersecret-token"
  - id: "end"
    type: "end"
    config:
      ports:
        - {name: "result"}
edges:
  - {from: "start.msg", to: "show.in"}
  - {from: "show.out", to: "end.result"}
"""


def test_log_events_echo_to_stderr_masked(tmp_path, monkeypatch):
    # FR-512/D35: raw history stays complete; the live terminal view masks
    # the API_TOKEN value from dev.env.
    ws = make_workspace(tmp_path, flow_yaml=LOG_FLOW)
    (ws / "flows" / "logging").mkdir()
    (ws / "flows" / "hello" / "flow.yaml").rename(
        ws / "flows" / "logging" / "flow.yaml"
    )
    monkeypatch.chdir(ws)
    result = runner.invoke(app, ["run", "flows/logging", "-i", "msg=supersecret-token"])
    assert result.exit_code == 0, result.stderr
    assert "[info] ***: ***" in result.stderr
    assert "supersecret-token" not in result.stderr
    records = jsonl_records(ws, "logging")
    logged = next(record for record in records if record["event"] == "log")
    assert logged["value"] == "supersecret-token"


PARENT_FLOW = """\
schema: "napflow/v1"
flow:
  name: "parent"
nodes:
  - id: "start"
    type: "start"
  - id: "child_run"
    type: "flow"
    config:
      flow: "flows/broken"
  - id: "end"
    type: "end"
    config:
      ports:
        - {name: "res", required: false}
edges:
  - {from: "start.out", to: "child_run.trigger"}
  - {from: "child_run.out", to: "end.res"}
"""

BROKEN_CHILD = """\
schema: "napflow/v1"
flow:
  name: "broken"
nodes:
  - id: "start"
    type: "start"
  - id: "end"
    type: "end"
    config:
      ports:
        - {name: "out"}
edges:
  - {from: "start.out", to: "ghost.in"}
"""


def test_run_gate_checks_reference_closure(tmp_path, monkeypatch):
    # S3/M5: the run gate deepened from single-flow to the reference
    # closure — a broken SUBFLOW blocks with exit 2 before anything runs
    ws = make_workspace(tmp_path, flow_yaml=PARENT_FLOW)
    (ws / "flows" / "parent").mkdir()
    (ws / "flows" / "hello" / "flow.yaml").rename(ws / "flows" / "parent" / "flow.yaml")
    (ws / "flows" / "broken").mkdir()
    (ws / "flows" / "broken" / "flow.yaml").write_text(BROKEN_CHILD, encoding="utf-8")
    monkeypatch.chdir(ws)
    result = runner.invoke(app, ["run", "flows/parent"])
    assert result.exit_code == 2
    assert "broken" in result.stderr  # the diagnostic names the subflow
