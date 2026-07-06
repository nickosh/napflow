"""`napf run` (S2/M5, FR-803/804): headless runs with stdout JSON,
stderr logs, JSONL history, reports, and run-state exit codes — the S2
DoD: a linear flow runs headless with correct exit codes."""

import json
from pathlib import Path
from xml.etree import ElementTree

import pytest
from typer.testing import CliRunner

from napflow.cli.main import app

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


def jsonl_records(ws: Path) -> list[dict]:
    logs = sorted((ws / ".napflow" / "runs" / "flows" / "hello").glob("*.jsonl"))
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
# Masking boundary: stdout functional, history masked (D22 + M5 pin)


def test_stdout_unmasked_but_jsonl_masked(ws):
    leaky = HELLO_FLOW.replace("{{ env.GREETING }}", "{{ env.API_TOKEN }}")
    (ws / "flows" / "hello" / "flow.yaml").write_text(leaky, encoding="utf-8")
    result = runner.invoke(app, ["run", "flows/hello"])
    assert result.exit_code == 0, result.stderr
    # stdout is the functional output — `| jq .token` must keep working
    assert json.loads(result.stdout) == {"result": {"msg": "supersecret-token"}}
    finished = jsonl_records(ws)[-1]
    assert finished["end_outputs"]["result"]["msg"] == "***"  # history masked


# --------------------------------------------------------------------------
# Reports (FR-804)


def report_manifest(kind: str) -> str:
    return MANIFEST + f'defaults:\n  run:\n    report: "{kind}"\n'


def test_json_report(tmp_path, monkeypatch):
    ws = make_workspace(tmp_path, manifest=report_manifest("json"))
    monkeypatch.chdir(ws)
    result = runner.invoke(app, ["run", "flows/hello"])
    assert result.exit_code == 0, result.stderr
    reports = list((ws / ".napflow" / "runs" / "flows" / "hello").glob("*.report.json"))
    assert len(reports) == 1
    payload = json.loads(reports[0].read_text(encoding="utf-8"))
    assert payload["flow"] == "flows/hello"
    assert payload["state"] == "passed"
    assert payload["exit_code"] == 0
    assert payload["asserts"] == {"passed": 1, "failed": 0}


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
      label: "checkpoint"
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
    # FR-512: log nodes are visible live on stderr, secrets masked at
    # emission (the API_TOKEN value from dev.env is a *TOKEN* secret)
    ws = make_workspace(tmp_path, flow_yaml=LOG_FLOW)
    (ws / "flows" / "logging").mkdir()
    (ws / "flows" / "hello" / "flow.yaml").rename(
        ws / "flows" / "logging" / "flow.yaml"
    )
    monkeypatch.chdir(ws)
    result = runner.invoke(app, ["run", "flows/logging", "-i", "msg=supersecret-token"])
    assert result.exit_code == 0, result.stderr
    assert "[info] checkpoint: ***" in result.stderr
    assert "supersecret-token" not in result.stderr


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
