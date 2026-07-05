"""Python node + worker subprocess (S3/M2): FR-506 semantics, FR-108
interpreter selection, FR-901–906 worker lifecycle (TR-6), the EC28
protocol-integrity half of TR-9, and rule-2 retention with a real
multi-param consumer (TR-1 addendum).

Every test spawns real worker subprocesses via `sys.executable` —
the identical tests exercise Windows spawn semantics in CI (FR-906).
"""

import ast
import sys
import textwrap
from pathlib import Path

from napflow.core.models.manifest import Manifest
from test_engine import end, events_of, flow, run, start

SRC = Path(__file__).resolve().parent.parent / "src" / "napflow"


def write_nodes(tmp_path, source):
    (tmp_path / "nodes.py").write_text(textwrap.dedent(source), encoding="utf-8")
    return tmp_path


def py(node_id, function, outputs=(), **extra):
    config = {"function": function, "outputs": list(outputs)}
    return {"id": node_id, "type": "python", "config": config} | extra


def mani_python(interpreter):
    return Manifest.model_validate(
        {"schema": "napflow/v1", "python": {"interpreter": interpreter}}
    )


# --------------------------------------------------------------------------
# FR-506: inputs by param name, outputs dict-keyed, defaults optional

NODES_BASIC = """
def combine(a, b):
    return {"total": a + b, "pair": [a, b]}

def add(value, step=10):
    return {"out": value + step}
"""


def test_multi_param_fires_on_full_inputs_and_maps_outputs(tmp_path):
    write_nodes(tmp_path, NODES_BASIC)
    f = flow(
        start({"name": "a", "type": "number"}, {"name": "b", "type": "number"}),
        py("calc", "combine", ["total", "pair"]),
        end({"name": "t"}, {"name": "p"}),
        edges=[
            ("start.a", "calc.a"),
            ("start.b", "calc.b"),
            ("calc.total", "end.t"),
            ("calc.pair", "end.p"),
        ],
    )
    result, _ = run(f, inputs={"a": 2, "b": 3}, flow_dir=tmp_path)
    assert result.state == "passed"
    assert result.end_outputs == {"t": 5, "p": [2, 3]}


def test_rule2_overwrite_refires_through_worker(tmp_path):
    # TR-1 addendum: the real rule-2 consumer — a late re-delivery to
    # one param re-fires the function with retained slots (serial at
    # the worker, FR-902)
    write_nodes(tmp_path, NODES_BASIC)
    f = flow(
        start({"name": "a", "type": "number"}, {"name": "b", "type": "number"}),
        py("calc", "combine", ["total", "pair"]),
        {"id": "dly", "type": "delay", "config": {"seconds": 0.05}},
        end({"name": "t"}),
        edges=[
            ("start.a", "calc.a"),
            ("start.b", "calc.b"),
            ("start.a", "dly.in"),
            ("dly.out", "calc.a"),  # overwrite → immediate re-fire
            ("calc.total", "end.t"),
        ],
    )
    result, records = run(f, inputs={"a": 2, "b": 3}, flow_dir=tmp_path)
    assert result.state == "passed"
    fired = [r for r in events_of(records, "node_fired") if r["node"] == "calc"]
    assert len(fired) == 2
    assert result.end_outputs == {"t": 5}


def test_single_input_by_port_name_and_literal_default(tmp_path):
    write_nodes(tmp_path, NODES_BASIC)
    f = flow(
        start({"name": "n", "type": "number"}),
        py("plus", "add", ["out"]),  # `step` unconnected → default 10
        end({"name": "x"}),
        edges=[("start.n", "plus.value"), ("plus.out", "end.x")],
    )
    result, _ = run(f, inputs={"n": 5}, flow_dir=tmp_path)
    assert result.state == "passed"
    assert result.end_outputs == {"x": 15}


NODES_STATE = """
CALLS = {"n": 0}

def tick(value):
    CALLS["n"] += 1
    return {"count": CALLS["n"]}
"""


def test_worker_persists_module_state_across_firings(tmp_path):
    # FR-901: one PERSISTENT worker per module — two firings see the
    # same process, so module state accumulates
    write_nodes(tmp_path, NODES_STATE)
    f = flow(
        start(),
        py("t1", "tick", ["count"]),
        py("t2", "tick", ["count"]),
        end({"name": "x"}),
        edges=[
            ("start.out", "t1.value"),
            ("t1.count", "t2.value"),
            ("t2.count", "end.x"),
        ],
    )
    result, _ = run(f, flow_dir=tmp_path)
    assert result.state == "passed"
    assert result.end_outputs == {"x": 2}


# --------------------------------------------------------------------------
# FR-506: AssertionError = python-assert; other exceptions carry traceback

NODES_ASSERT = """
def check(value):
    assert value > 10, f"value {value} not > 10"
    return {"ok": value}
"""


def test_assertion_error_counts_as_python_assert(tmp_path):
    write_nodes(tmp_path, NODES_ASSERT)
    f = flow(
        start({"name": "n", "type": "number"}),
        py("chk", "check", ["ok"]),
        end({"name": "x", "required": False}),
        edges=[("start.n", "chk.value"), ("chk.ok", "end.x")],
    )
    result, records = run(f, inputs={"n": 3}, flow_dir=tmp_path)
    assert result.state == "failed"
    assert result.asserts_failed == 1  # FR-405: python-asserts roll up
    python_errors = events_of(records, "python_error")
    assert python_errors[0]["error_type"] == "AssertionError"
    assert "not > 10" in python_errors[0]["message"]
    assert_results = events_of(records, "assert_result")
    assert assert_results[0]["op"] == "python-assert"
    assert assert_results[0]["passed"] is False
    # the unwired error port is ALSO an unhandled error-port message
    kinds = {e["kind"] for e in result.unhandled_errors}
    assert "unhandled_error_port" in kinds


def test_wired_python_assert_still_fails_run(tmp_path):
    # asserts are outcomes, not control flow (D20): handling the error
    # port does not un-fail a python assert
    write_nodes(tmp_path, NODES_ASSERT)
    f = flow(
        start({"name": "n", "type": "number"}),
        py("chk", "check", ["ok"]),
        end({"name": "x", "required": False}, {"name": "err", "required": False}),
        edges=[
            ("start.n", "chk.value"),
            ("chk.ok", "end.x"),
            ("chk.error", "end.err"),
        ],
    )
    result, _ = run(f, inputs={"n": 3}, flow_dir=tmp_path)
    assert result.state == "failed"
    assert result.asserts_failed == 1
    kinds = {e["kind"] for e in result.unhandled_errors}
    assert "unhandled_error_port" not in kinds  # handled, but still failed
    payload = result.end_outputs["err"]
    assert payload["error_kind"] == "python_assert"
    assert "traceback" in payload and "function" in payload


NODES_BOOM = """
def boom(value):
    raise ValueError("kaput")

def rescue(err):
    return {"out": sorted(err), "kind": err["error_kind"]}
"""


def test_exception_routes_to_error_port_with_traceback(tmp_path):
    write_nodes(tmp_path, NODES_BOOM)
    f = flow(
        start(),
        py("b", "boom", ["out"]),
        py("r", "rescue", ["out", "kind"]),
        end({"name": "keys"}, {"name": "kind"}),
        edges=[
            ("start.out", "b.value"),
            ("b.error", "r.err"),
            ("r.out", "end.keys"),
            ("r.kind", "end.kind"),
        ],
    )
    result, records = run(f, flow_dir=tmp_path)
    assert result.state == "passed"  # wired error port = handled (EC13-alike)
    assert result.end_outputs["kind"] == "python_error"
    assert result.end_outputs["keys"] == [
        "error_kind",
        "function",
        "message",
        "traceback",
    ]
    python_errors = events_of(records, "python_error")
    assert "ValueError: kaput" in python_errors[0]["traceback"]


# --------------------------------------------------------------------------
# TR-6: timeout-kill-respawn, crash isolation

NODES_SLOW = """
import time

def slow(value):
    time.sleep(30)
    return {"out": value}

def report(err):
    return {"kind": err["error_kind"], "ceiling": err["max_seconds"]}
"""


def test_timeout_kills_worker_and_next_firing_respawns(tmp_path):
    write_nodes(tmp_path, NODES_SLOW)
    f = flow(
        start(),
        py("s", "slow", ["out"], max_seconds=0.3),
        py("r", "report", ["kind", "ceiling"]),
        end({"name": "kind"}, {"name": "ceiling"}),
        edges=[
            ("start.out", "s.value"),
            ("s.error", "r.err"),
            ("r.kind", "end.kind"),
            ("r.ceiling", "end.ceiling"),
        ],
    )
    result, _ = run(f, flow_dir=tmp_path)
    # `report` ran on a fresh worker AFTER the kill — lazy respawn works
    assert result.state == "passed"
    assert result.end_outputs == {"kind": "timeout", "ceiling": 0.3}


NODES_CRASH = """
import os

def die(value):
    os._exit(3)

def survive(err):
    return {"kind": err["error_kind"], "msg": err["message"]}
"""


def test_worker_crash_is_node_error_and_respawns(tmp_path):
    write_nodes(tmp_path, NODES_CRASH)
    f = flow(
        start(),
        py("d", "die", ["out"]),
        py("v", "survive", ["kind", "msg"]),
        end({"name": "kind"}, {"name": "msg"}),
        edges=[
            ("start.out", "d.value"),
            ("d.error", "v.err"),
            ("v.kind", "end.kind"),
            ("v.msg", "end.msg"),
        ],
    )
    result, _ = run(f, flow_dir=tmp_path)
    assert result.state == "passed"  # FR-904: never an engine failure
    assert result.end_outputs["kind"] == "worker_crash"
    assert "exit code 3" in result.end_outputs["msg"]


# --------------------------------------------------------------------------
# EC28 / TR-9 half: protocol integrity under output floods

NODES_NOISY = """
import os
import sys

def noisy(value):
    for i in range(500):
        print(f"line {i}")
    print("to stderr", file=sys.stderr)
    os.write(1, b"raw fd write\\n")
    return {"out": value * 2}
"""


def test_print_flood_cannot_corrupt_protocol(tmp_path):
    write_nodes(tmp_path, NODES_NOISY)
    f = flow(
        start({"name": "n", "type": "number"}),
        py("loud", "noisy", ["out"]),
        end({"name": "x"}),
        edges=[("start.n", "loud.value"), ("loud.out", "end.x")],
    )
    result, records = run(f, inputs={"n": 21}, flow_dir=tmp_path)
    assert result.state == "passed"
    assert result.end_outputs == {"x": 42}  # the result survived the flood
    logs = events_of(records, "log")
    stdout_lines = [r for r in logs if r["level"] == "info"]
    assert len(stdout_lines) == 500
    assert stdout_lines[0]["value"] == "line 0"
    assert stdout_lines[0]["node"] == "loud"
    warn_values = {r["value"] for r in logs if r["level"] == "warn"}
    assert "to stderr" in warn_values
    # raw fd-1 writes land in the stderr pipe, never the protocol (EC28)
    assert "raw fd write" in warn_values


# --------------------------------------------------------------------------
# Failure shapes: import error, missing files, bad function, bad JSON


def test_import_failure_is_worker_crash_node_error(tmp_path):
    write_nodes(tmp_path, "import nonexistent_module_xyz\n")
    f = flow(
        start(),
        py("p", "f", ["out"]),
        end({"name": "x", "required": False}),
        edges=[("start.out", "p.value"), ("p.out", "end.x")],
    )
    result, _ = run(f, flow_dir=tmp_path)
    assert result.state == "failed"  # unwired error port ⇒ unhandled
    unhandled = result.unhandled_errors[0]
    assert unhandled["kind"] == "unhandled_error_port"
    assert "nonexistent_module_xyz" in unhandled["message"]


def test_missing_nodes_py_and_missing_flow_dir(tmp_path):
    f = flow(
        start(),
        py("p", "f", ["out"]),
        end({"name": "x", "required": False}),
        edges=[("start.out", "p.value"), ("p.out", "end.x")],
    )
    result, _ = run(f, flow_dir=tmp_path)  # dir exists, no nodes.py
    assert result.state == "failed"
    assert "no nodes.py" in str(result.unhandled_errors)
    result, _ = run(f)  # engine used standalone without flow_dir
    assert result.state == "failed"
    assert "flow_dir" in str(result.unhandled_errors)


def test_unknown_function_and_unserializable_return(tmp_path):
    write_nodes(tmp_path, "def gives_set(value):\n    return {'out': {1, 2}}\n")
    base = [
        start(),
        end({"name": "x", "required": False}),
    ]
    f = flow(
        *base,
        py("p", "ghost", ["out"]),
        edges=[("start.out", "p.value"), ("p.out", "end.x")],
    )
    result, _ = run(f, flow_dir=tmp_path)
    assert result.state == "failed"
    assert "no function 'ghost'" in str(result.unhandled_errors)
    f = flow(
        *base,
        py("p", "gives_set", ["out"]),
        edges=[("start.out", "p.value"), ("p.out", "end.x")],
    )
    result, _ = run(f, flow_dir=tmp_path)
    assert result.state == "failed"
    assert "not JSON-serializable" in str(result.unhandled_errors)


def test_missing_declared_output_key_is_node_error(tmp_path):
    write_nodes(tmp_path, "def half(value):\n    return {'a': 1}\n")
    f = flow(
        start(),
        py("p", "half", ["a", "b"]),
        end({"name": "x", "required": False}),
        edges=[("start.out", "p.value"), ("p.a", "end.x")],
    )
    result, _ = run(f, flow_dir=tmp_path)
    assert result.state == "failed"
    assert "missing 'b'" in str(result.unhandled_errors)


# --------------------------------------------------------------------------
# FR-108: interpreter selection


def test_explicit_interpreter_is_honored(tmp_path):
    write_nodes(tmp_path, NODES_BASIC)
    f = flow(
        start({"name": "n", "type": "number"}),
        py("plus", "add", ["out"]),
        end({"name": "x"}),
        edges=[("start.n", "plus.value"), ("plus.out", "end.x")],
    )
    result, _ = run(
        f, inputs={"n": 1}, flow_dir=tmp_path, mani=mani_python(sys.executable)
    )
    assert result.state == "passed"
    assert result.end_outputs == {"x": 11}


def test_missing_interpreter_is_worker_crash(tmp_path):
    write_nodes(tmp_path, NODES_BASIC)
    f = flow(
        start({"name": "n", "type": "number"}),
        py("plus", "add", ["out"]),
        end({"name": "x", "required": False}),
        edges=[("start.n", "plus.value"), ("plus.out", "end.x")],
    )
    result, _ = run(
        f,
        inputs={"n": 1},
        flow_dir=tmp_path,
        mani=mani_python("definitely-not-a-python-xyz"),
    )
    assert result.state == "failed"
    assert "cannot spawn python worker" in str(result.unhandled_errors)


# --------------------------------------------------------------------------
# The child script must stay importable-nothing-from-napflow (EN §5a)


def test_worker_main_is_stdlib_only():
    source = (SRC / "core" / "worker_main.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            assert name.split(".")[0] != "napflow", (
                "worker_main.py must not import napflow — it runs under"
                " the configured interpreter (FR-108)"
            )
