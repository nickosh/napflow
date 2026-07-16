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
from napflow.core.events import (
    HISTORY_FEATURE_CONTENT_BLOBS,
    HISTORY_FORMAT,
    EventStream,
    HistoryFormatError,
    SecretMasker,
    persist_record_content,
)
from napflow.core.history_content import RunContentStore
from napflow.core.runprep import RunPrepError, prepare_run
from napflow.core.workspace import load_workspace

runner = CliRunner()

MANIFEST = """\
schema: "napflow/v1"
environments:
  root: "envs"
  default: "dev.env"
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
    result = runner.invoke(app, ["run", "flows/hello", "--env", "staging.env"])
    assert result.exit_code == 2
    assert "staging.env" in result.stderr


def test_profile_identifier_is_the_literal_filename(ws):
    result = runner.invoke(app, ["run", "flows/hello", "--env", "dev"])

    assert result.exit_code == 2
    assert "no literal filename" in result.stderr
    assert "dev.env" in result.stderr


def test_dotenv_names_under_workspace_root_are_selectable(tmp_path, monkeypatch):
    manifest = """\
schema: "napflow/v1"
environments:
  root: "."
  default: ".env.staging"
"""
    workspace = make_workspace(tmp_path, manifest=manifest)
    (workspace / ".env").write_text("GREETING=base\n", encoding="utf-8")
    (workspace / ".env.staging").write_text("GREETING=staging\n", encoding="utf-8")
    monkeypatch.chdir(workspace)

    default = runner.invoke(app, ["run", "flows/hello"])
    explicit = runner.invoke(app, ["run", "flows/hello", "--env", ".env"])

    assert default.exit_code == explicit.exit_code == 0
    assert json.loads(default.stdout) == {"result": {"msg": "staging"}}
    assert json.loads(explicit.stdout) == {"result": {"msg": "base"}}


def test_invalid_selected_profile_is_a_hard_error(ws):
    bad = ws / "envs" / "bad.env"
    bad.write_text("export GREETING=nope\n", encoding="utf-8")

    result = runner.invoke(app, ["run", "flows/hello", "--env", "bad.env"])

    assert result.exit_code == 2
    assert "invalid" in result.stderr
    assert "export" in result.stderr


def test_invalid_unselected_profile_is_skipped_with_warning(ws):
    (ws / "envs" / ".env.broken").write_text("not-an-assignment\n", encoding="utf-8")

    result = runner.invoke(app, ["run", "flows/hello"])

    assert result.exit_code == 0
    assert "warning: env profile '.env.broken' was skipped" in result.stderr


def test_missing_manifest_default_is_a_hard_error(ws):
    manifest = ws / "napflow.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("dev.env", "missing.env"),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["run", "flows/hello"])

    assert result.exit_code == 2
    assert "default env profile 'missing.env'" in result.stderr


@pytest.mark.parametrize("name", ["../secret.env", "nested/dev.env", "C:/secret.env"])
def test_unsafe_selected_profile_keeps_workspace_boundary_reason(ws, name):
    with pytest.raises(RunPrepError) as excinfo:
        prepare_run(load_workspace(ws), "flows/hello", name)

    assert excinfo.value.reason == "workspace_boundary"
    assert "violates workspace boundary" in str(excinfo.value)


def test_unsafe_default_profile_keeps_workspace_boundary_reason(ws):
    manifest = ws / "napflow.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("dev.env", "../secret.env"),
        encoding="utf-8",
    )

    with pytest.raises(RunPrepError) as excinfo:
        prepare_run(load_workspace(ws), "flows/hello")

    assert excinfo.value.reason == "workspace_boundary"
    assert "default env profile" in str(excinfo.value)


def test_selected_escaping_profile_symlink_keeps_workspace_boundary_reason(
    ws, tmp_path
):
    outside = tmp_path / "outside.env"
    outside.write_text("GREETING=outside\n", encoding="utf-8")
    try:
        (ws / "envs" / "escape.env").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/privilege level")

    with pytest.raises(RunPrepError) as excinfo:
        prepare_run(load_workspace(ws), "flows/hello", "escape.env")

    assert excinfo.value.reason == "workspace_boundary"


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


def test_stdout_and_local_jsonl_preserve_raw_local_truth(ws):
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
    return MANIFEST + f'defaults:\n  run:\n    report: "{kind}"\n    history: 1\n'


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
    report = next((ws / ".napflow" / "runs" / "flows" / "hello").glob("*.report.json"))
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["state"] == "failed"
    assert "supersecret-token" not in payload["unhandled_errors"][0]["message"]
    assert "***" in payload["unhandled_errors"][0]["message"]


def test_json_report(tmp_path, monkeypatch):
    leaky = HELLO_FLOW.replace("{{ env.GREETING }}", "{{ env.API_TOKEN }}")
    ws = make_workspace(tmp_path, flow_yaml=leaky, manifest=report_manifest("json"))
    monkeypatch.chdir(ws)
    seen_presentation_sinks = []
    real_open = cli_main.open_run_stream

    def observed_open(workspace, prepared, *, extra_sinks=(), presentation_sinks=()):
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
    assert finished["end_outputs"] == {"result": {"msg": "supersecret-token"}}


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

    def planted_open(workspace, prepared, *, extra_sinks=(), presentation_sinks=()):
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


def test_stream_close_failure_preserves_result_and_abandons_history(ws, monkeypatch):
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


def test_fresh_keyboard_interrupt_during_adapter_reclose_exits_130(ws, monkeypatch):
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


def _write_report_log(log_path: Path, records: list[dict]) -> None:
    log_path.write_text(
        "".join(f"{json.dumps(record, ensure_ascii=True)}\n" for record in records),
        encoding="utf-8",
    )


def test_reports_resolve_blob_values_before_masking(tmp_path):
    log_path = tmp_path / "blob-values.jsonl"
    secret = "supersecret-token"
    value = {"token": secret, "padding": "x" * 256}
    store = RunContentStore(log_path, inline_threshold_bytes=32)
    assertion = persist_record_content(
        {
            "event": "assert_result",
            "node": "check",
            "check": "equals",
            "expected": value,
            "actual": value,
            "passed": False,
        },
        store,
    )
    finished = persist_record_content(
        {
            "event": "run_finished",
            "state": "failed",
            "duration_ms": 1.0,
            "asserts": {"passed": 0, "failed": 1},
            "unhandled_errors": [],
            "end_outputs": {"result": value},
            "nodes_never_fired": [],
        },
        store,
    )
    assert assertion["actual"]["$napflow"]["kind"] == "blob"
    assert finished["end_outputs"]["result"]["$napflow"]["kind"] == "blob"
    _write_report_log(
        log_path,
        [
            {
                "event": "run_started",
                "seq": 1,
                "format": HISTORY_FORMAT,
                "features": [HISTORY_FEATURE_CONTENT_BLOBS],
            },
            assertion,
            finished,
        ],
    )
    result = RunResult(
        state="failed",
        end_outputs={"result": value},
        asserts_passed=0,
        asserts_failed=1,
        unhandled_errors=[],
        nodes_never_fired=[],
        duration_ms=1.0,
    )
    masker = SecretMasker(["*TOKEN*"], {"API_TOKEN": secret})

    json_path = cli_report.write_report(
        "json", log_path, "flows/blob-values", result, masker=masker
    )
    junit_path = cli_report.write_report(
        "junit", log_path, "flows/blob-values", result, masker=masker
    )

    assert json_path is not None
    assert junit_path is not None
    json_payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert json_payload["format"] == HISTORY_FORMAT
    assert json_payload["features"] == [HISTORY_FEATURE_CONTENT_BLOBS]
    assert store.resolve(json_payload["end_outputs"]["result"]) == {
        "token": "***",
        "padding": "x" * 256,
    }
    failure = ElementTree.parse(junit_path).getroot().find("testcase/failure")
    assert failure is not None
    assert "***" in failure.get("message")
    assert secret not in json_path.read_text(encoding="utf-8")
    assert secret not in junit_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("kind", ["json", "junit"])
def test_reports_accept_markerless_v01_envelope(tmp_path, kind):
    log_path = tmp_path / "legacy-marker.jsonl"
    marker_literal = {
        "$napflow": {
            "kind": "blob",
            "hash": "sha256:" + "0" * 64,
            "bytes": 999,
            "media_type": "application/json",
            "codec": "json",
        }
    }
    _write_report_log(
        log_path,
        [
            {"event": "run_started", "seq": 1},
            {
                "event": "run_finished",
                "state": "passed",
                "duration_ms": 1.0,
                "asserts": {"passed": 0, "failed": 0},
                "unhandled_errors": [],
                "end_outputs": {"result": marker_literal},
                "nodes_never_fired": [],
            },
        ],
    )
    result = RunResult(
        state="passed",
        end_outputs={"result": marker_literal},
        asserts_passed=0,
        asserts_failed=0,
        unhandled_errors=[],
        nodes_never_fired=[],
        duration_ms=1.0,
    )

    report_path = cli_report.write_report(
        kind,
        log_path,
        "flows/legacy-marker",
        result,
        masker=SecretMasker([], {}),
    )

    assert report_path is not None
    if kind == "json":
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        assert payload["end_outputs"] == {"result": marker_literal}
    else:
        assert ElementTree.parse(report_path).getroot().get("tests") == "0"


def test_reports_skip_unrelated_missing_request_blob(tmp_path):
    log_path = tmp_path / "lazy-report.jsonl"
    store = RunContentStore(log_path, inline_threshold_bytes=64)
    response = {
        "status": 200,
        "headers": {"content-type": "text/plain"},
        "body": "unrelated" * 128,
        "size_bytes": 1_152,
        "timing": {},
        "elapsed_ms": 1.0,
        "url": "https://example.test/final",
        "http_version": "HTTP/1.1",
        "attempt": 1,
        "retries_total": 0,
        "redirects_total": 0,
    }
    request_record = persist_record_content(
        {"event": "request_finished", "response": response}, store
    )
    descriptor = request_record["response"]["$napflow"]
    assert descriptor["kind"] == "blob"
    digest = descriptor["hash"].removeprefix("sha256:")
    (store.blob_dir / digest).unlink()
    _write_report_log(
        log_path,
        [
            {
                "event": "run_started",
                "seq": 1,
                "format": HISTORY_FORMAT,
                "features": [HISTORY_FEATURE_CONTENT_BLOBS],
            },
            request_record,
            {
                "event": "assert_result",
                "node": "check",
                "check": "present",
                "expected": None,
                "actual": "ok",
                "passed": True,
            },
            {
                "event": "run_finished",
                "state": "passed",
                "duration_ms": 1.0,
                "asserts": {"passed": 1, "failed": 0},
                "unhandled_errors": [],
                "end_outputs": {"result": "ok"},
                "nodes_never_fired": [],
            },
        ],
    )
    result = RunResult(
        state="passed",
        end_outputs={"result": "ok"},
        asserts_passed=1,
        asserts_failed=0,
        unhandled_errors=[],
        nodes_never_fired=[],
        duration_ms=1.0,
    )

    json_path = cli_report.write_report(
        "json", log_path, "flows/lazy", result, masker=SecretMasker([], {})
    )
    junit_path = cli_report.write_report(
        "junit", log_path, "flows/lazy", result, masker=SecretMasker([], {})
    )

    assert json_path is not None
    assert json.loads(json_path.read_text(encoding="utf-8"))["end_outputs"] == {
        "result": "ok"
    }
    assert junit_path is not None
    assert ElementTree.parse(junit_path).getroot().get("tests") == "1"


@pytest.mark.parametrize("kind", ["json", "junit"])
def test_report_rejects_unknown_history_feature_before_records(tmp_path, kind):
    log_path = tmp_path / "unknown-feature.jsonl"
    _write_report_log(
        log_path,
        [
            {
                "event": "run_started",
                "seq": 1,
                "format": HISTORY_FORMAT,
                "features": ["future/1"],
            },
            {"event": "run_finished", "duration_ms": 1.0},
        ],
    )
    result = RunResult(
        state="passed",
        end_outputs={},
        asserts_passed=0,
        asserts_failed=0,
        unhandled_errors=[],
        nodes_never_fired=[],
        duration_ms=1.0,
    )

    with pytest.raises(HistoryFormatError, match="unsupported run-history features"):
        cli_report.write_report(
            kind,
            log_path,
            "flows/unknown",
            result,
            masker=SecretMasker([], {}),
        )


@pytest.mark.parametrize(
    ("header", "message"),
    [
        (
            {"event": "run_started", "seq": 1, "format": "napflow-run/2"},
            "unsupported run-history format major",
        ),
        (
            {"event": "run_started", "seq": 1, "features": []},
            "legacy run history cannot declare storage features",
        ),
        (
            {"event": "run_started", "seq": 1, "format": None},
            "format must be omitted for v0.1 or contain a string",
        ),
        (
            {"event": "run_started", "seq": 1, "format": "postman-run/1"},
            "unrecognized run-history format",
        ),
        (
            {
                "event": "run_started",
                "seq": 1,
                "format": HISTORY_FORMAT,
                "features": None,
            },
            "features must be an array",
        ),
        (
            {"event": "node_fired", "seq": 1},
            "must begin with a run_started envelope at seq 1",
        ),
        (
            {"event": "run_started", "seq": 2},
            "must begin with a run_started envelope at seq 1",
        ),
    ],
)
@pytest.mark.parametrize("kind", ["json", "junit"])
def test_report_rejects_incompatible_history_envelope(tmp_path, header, message, kind):
    log_path = tmp_path / "incompatible.jsonl"
    _write_report_log(log_path, [header])
    result = RunResult(
        state="passed",
        end_outputs={},
        asserts_passed=0,
        asserts_failed=0,
        unhandled_errors=[],
        nodes_never_fired=[],
        duration_ms=0,
    )

    with pytest.raises(HistoryFormatError, match=message):
        cli_report.write_report(
            kind,
            log_path,
            "flows/incompatible",
            result,
            masker=SecretMasker([], {}),
        )


@pytest.mark.parametrize("kind", ["json", "junit"])
def test_report_rejects_malformed_first_json_record(tmp_path, kind):
    log_path = tmp_path / "malformed.jsonl"
    log_path.write_text("\n{not-json}\n", encoding="utf-8")
    result = RunResult(
        state="passed",
        end_outputs={},
        asserts_passed=0,
        asserts_failed=0,
        unhandled_errors=[],
        nodes_never_fired=[],
        duration_ms=0,
    )

    with pytest.raises(HistoryFormatError, match="valid JSON"):
        cli_report.write_report(
            kind,
            log_path,
            "flows/malformed",
            result,
            masker=SecretMasker([], {}),
        )


@pytest.mark.parametrize("kind", ["json", "junit"])
def test_report_rejects_empty_history(tmp_path, kind):
    log_path = tmp_path / "empty.jsonl"
    log_path.write_text("\n", encoding="utf-8")
    result = RunResult(
        state="passed",
        end_outputs={},
        asserts_passed=0,
        asserts_failed=0,
        unhandled_errors=[],
        nodes_never_fired=[],
        duration_ms=0,
    )

    with pytest.raises(HistoryFormatError, match="history is empty"):
        cli_report.write_report(
            kind,
            log_path,
            "flows/empty",
            result,
            masker=SecretMasker([], {}),
        )


def test_junit_report_sanitizes_xml_attributes(tmp_path):
    log_path = tmp_path / "xml.jsonl"
    secret = "supersecret-token"
    assertion_name = "check & < > \" ' \x01"
    error_message = f"failure {secret} & < > \" ' \ud800"
    records = [
        {"event": "run_started", "seq": 1},
        {
            "event": "assert_result",
            "seq": 2,
            "node": "check",
            "check": assertion_name,
            "expected": secret,
            "actual": f"value {secret}",
            "passed": False,
        },
        {
            "event": "run_finished",
            "seq": 3,
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
        log.write('{"event":"run_started","seq":1}\n')
        for seq in range(2, record_count + 2):
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
                    "seq": record_count + 2,
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
    # EC34/FR-107 DoD: a fresh `napf init --example` workspace runs its
    # smoke flow (fixture→python→assert) OFFLINE with exit 0 — the fixture
    # auto-seed drives the run (the start node is fully unwired).
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    monkeypatch.chdir(fresh)
    assert runner.invoke(app, ["init", "--example"]).exit_code == 0
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
