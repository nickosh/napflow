"""Engine scheduler + frames + start/end/condition/assert/delay (S2/M3).

TR-2 (quiescence: sentinel race + empty-seed finalize), TR-3 root-frame
half (required-End ⇒ failed ⇒ exit 1; subflow/loop frames land S3),
FR-401–411 scheduler behaviors, FR-501/502/504/505 runners,
FR-602/603 templating context + error routing (EC24).

The engine trusts the checker (LOAD/CHECK are `napf run` steps): tests
build FlowFile models directly, including runtime-tolerated shapes the
checker would flag (multi-edge inputs in the budget cycle).
"""

import asyncio

from napflow.core.engine import EXIT_CODES, FlowRun, execute_flow
from napflow.core.events import EventStream, SecretMasker
from napflow.core.models import FlowFile
from napflow.core.models.manifest import Manifest

NO_SECRETS = SecretMasker([], {})


class CaptureSink:
    def __init__(self):
        self.records = []

    def write(self, record):
        self.records.append(record)

    def close(self):
        pass


def flow(*nodes, edges=(), env_required=()):
    data = {
        "schema": "napflow/v1",
        "flow": {"name": "t"},
        "nodes": list(nodes),
        "edges": [{"from": f, "to": t} for f, t in edges],
    }
    if env_required:
        data["env"] = {"required": list(env_required)}
    return FlowFile.model_validate(data)


def start(*ports):
    return {"id": "start", "type": "start", "config": {"ports": list(ports)}}


def end(*ports):
    return {"id": "end", "type": "end", "config": {"ports": list(ports)}}


def manifest(**run_defaults):
    data = {"schema": "napflow/v1"}
    if run_defaults:
        data["defaults"] = {"run": run_defaults}
    return Manifest.model_validate(data)


def run(
    flow_file,
    *,
    inputs=None,
    env=None,
    env_name="dev",
    mani=None,
    timeout=None,
    flow_dir=None,
    workspace_root=None,
):
    sink = CaptureSink()
    stream = EventStream("test-run", NO_SECRETS, [sink])
    result = asyncio.run(
        asyncio.wait_for(  # engine hangs are test failures, not CI stalls
            execute_flow(
                flow_file,
                flow_identity="flows/t",
                manifest=mani or manifest(),
                env=env or {},
                env_name=env_name,
                inputs=inputs,
                stream=stream,
                run_timeout_s=timeout,
                flow_dir=flow_dir,
                workspace_root=workspace_root,
            ),
            timeout=30,
        )
    )
    return result, sink.records


def events_of(records, kind):
    return [r for r in records if r["event"] == kind]


# --------------------------------------------------------------------------
# Happy path + TR-2


def test_linear_flow_passes():
    f = flow(
        start(),
        {"id": "gate", "type": "condition", "config": {"expr": "true"}},
        end({"name": "result"}),
        edges=[("start.out", "gate.in"), ("gate.true", "end.result")],
    )
    result, records = run(f, inputs={})
    assert result.state == "passed"
    assert result.exit_code == 0
    assert result.end_outputs == {"result": {}}  # start.out = inputs dict
    assert [r["event"] for r in records][0] == "run_started"
    assert [r["event"] for r in records][-1] == "run_finished"


def test_quiescence_under_overlapping_async_firings():
    # TR-2 sentinel race: two delays overlap; the LAST decrement (from a
    # task finishing while the pump sleeps on an empty queue) must wake
    # the pump — polling would hang or exit early here.
    f = flow(
        start(),
        {"id": "slow", "type": "delay", "config": {"seconds": 0.05}},
        {"id": "fast", "type": "delay", "config": {"seconds": 0.01}},
        end({"name": "a"}, {"name": "b"}),
        edges=[
            ("start.out", "slow.in"),
            ("start.out", "fast.in"),
            ("slow.out", "end.a"),
            ("fast.out", "end.b"),
        ],
    )
    result, _ = run(f)
    assert result.state == "passed"
    assert set(result.end_outputs) == {"a", "b"}


def test_empty_seed_finalizes_immediately():
    # EC08: start.out unwired — nothing increments in_flight, QUIESCENT
    # is never enqueued; without the guard the pump blocks forever.
    f = flow(start(), end({"name": "x", "required": False}))
    result, records = run(f)
    assert result.state == "passed"
    assert result.end_outputs == {"x": None}
    assert events_of(records, "run_finished")


# --------------------------------------------------------------------------
# TR-3: required End ports (D18)


def test_required_end_unwritten_fails_run():
    f = flow(start(), end({"name": "x"}))  # required by default, unwired
    result, _ = run(f)
    assert result.state == "failed"
    assert result.exit_code == 1
    assert result.unhandled_errors[0]["kind"] == "required_end_unwritten"
    assert result.unhandled_errors[0]["port"] == "x"


def test_untaken_branch_leaves_required_end_unwritten():
    f = flow(
        start(),
        {"id": "gate", "type": "condition", "config": {"expr": "false"}},
        end({"name": "x"}),
        edges=[("start.out", "gate.in"), ("gate.true", "end.x")],
    )
    result, _ = run(f)
    assert result.state == "failed"  # false branch taken; x never written


def test_optional_end_port_yields_null():
    f = flow(
        start(),
        {"id": "gate", "type": "condition", "config": {"expr": "false"}},
        end({"name": "x", "required": False}),
        edges=[("start.out", "gate.in"), ("gate.true", "end.x")],
    )
    result, _ = run(f)
    assert result.state == "passed"
    assert result.end_outputs == {"x": None}


# --------------------------------------------------------------------------
# Condition + templating context (FR-504, FR-602/603)


def test_condition_routes_and_forwards_value():
    f = flow(
        start({"name": "n", "type": "number"}),
        {"id": "gate", "type": "condition", "config": {"expr": "inputs.n > 3"}},
        end({"name": "big", "required": False}, {"name": "small", "required": False}),
        edges=[
            ("start.out", "gate.in"),
            ("gate.true", "end.big"),
            ("gate.false", "end.small"),
        ],
    )
    result, _ = run(f, inputs={"n": 5})
    assert result.end_outputs == {"big": {"n": 5}, "small": None}
    result, _ = run(f, inputs={"n": 1})
    assert result.end_outputs == {"big": None, "small": {"n": 1}}


def test_nodes_context_and_trigger_envelope():
    expr = "nodes.first.true.n == 7 and trigger.value.n == 7"
    f = flow(
        start({"name": "n", "type": "number"}),
        {"id": "first", "type": "condition", "config": {"expr": "true"}},
        {"id": "second", "type": "condition", "config": {"expr": expr}},
        end({"name": "out"}),
        edges=[
            ("start.out", "first.in"),
            ("first.true", "second.in"),
            ("second.true", "end.out"),
        ],
    )
    result, _ = run(f, inputs={"n": 7})
    assert result.state == "passed"


def test_undefined_variable_is_unhandled_node_error():
    # FR-603/EC24: condition has no error port — evaluation failure is
    # an unhandled node error ⇒ run failed (never a silent empty string)
    f = flow(
        start(),
        {"id": "gate", "type": "condition", "config": {"expr": "nodes.ghost.out"}},
        end({"name": "x", "required": False}),
        edges=[("start.out", "gate.in"), ("gate.true", "end.x")],
    )
    result, _ = run(f)
    assert result.state == "failed"
    assert result.unhandled_errors[0]["kind"] == "template_error"
    assert result.unhandled_errors[0]["node"] == "gate"


# --------------------------------------------------------------------------
# Assert (FR-505)

RESPONSE = {"status": 201, "elapsed_ms": 42, "body": {"id": "abc123"}}


def assert_flow(checks, mode="report_all"):
    return flow(
        start({"name": "resp", "type": "object"}),
        {"id": "check", "type": "assert", "config": {"checks": checks, "mode": mode}},
        end({"name": "ok", "required": False}),
        edges=[("start.resp", "check.in"), ("check.passed", "end.ok")],
    )


def test_assert_kinds_pass():
    f = assert_flow(
        [
            {"kind": "status", "equals": 201},
            {"kind": "response_time", "under_ms": 1500},
            {"kind": "expr", "expr": "trigger.value.body.id", "op": "present"},
            {
                "kind": "expr",
                "expr": "trigger.value.body.id",
                "op": "matches",
                "value": "^abc",
            },
            {"kind": "expr", "expr": "trigger.value.status", "op": "lt", "value": 300},
        ]
    )
    result, records = run(f, inputs={"resp": RESPONSE})
    assert result.state == "passed"
    assert (result.asserts_passed, result.asserts_failed) == (5, 0)
    assert len(events_of(records, "assert_result")) == 5


def test_failed_assert_fails_run_and_unconnected_failed_port_records():
    f = assert_flow([{"kind": "status", "equals": 200}])
    result, records = run(f, inputs={"resp": RESPONSE})
    assert result.state == "failed"
    assert (result.asserts_passed, result.asserts_failed) == (0, 1)
    event = events_of(records, "assert_result")[0]
    assert (event["expected"], event["actual"], event["passed"]) == (200, 201, False)
    # the message into the unwired `failed` port is ALSO an unhandled
    # error-port message (EN §2 / W103)
    kinds = {e["kind"] for e in result.unhandled_errors}
    assert "unhandled_error_port" in kinds


def test_fail_fast_stops_after_first_failure():
    checks = [
        {"kind": "status", "equals": 200},
        {"kind": "response_time", "under_ms": 1500},
    ]
    fast = assert_flow(checks, mode="fail_fast")
    result, records = run(fast, inputs={"resp": RESPONSE})
    assert len(events_of(records, "assert_result")) == 1
    result_all, records_all = run(assert_flow(checks), inputs={"resp": RESPONSE})
    assert len(events_of(records_all, "assert_result")) == 2
    assert result.state == result_all.state == "failed"


def test_present_on_missing_path_fails_check_not_run_error():
    f = assert_flow(
        [{"kind": "expr", "expr": "trigger.value.body.missing", "op": "present"}]
    )
    result, _ = run(f, inputs={"resp": RESPONSE})
    assert result.state == "failed"
    assert result.asserts_failed == 1  # a failed CHECK, not a node error
    kinds = {e["kind"] for e in result.unhandled_errors}
    assert "template_error" not in kinds


def test_non_response_value_into_status_check_is_node_error():
    f = assert_flow([{"kind": "status", "equals": 200}])
    result, _ = run(f, inputs={"resp": {"nope": 1}})
    assert result.state == "failed"
    assert result.unhandled_errors[0]["kind"] == "assert_error"


# --------------------------------------------------------------------------
# BIND + ENV (FR-501, EN §2)


def test_bind_coerces_and_defaults():
    f = flow(
        start(
            {"name": "n", "type": "number"},
            {"name": "base", "type": "string", "default": "{{ env.BASE }}"},
        ),
        end({"name": "out"}),
        edges=[("start.out", "end.out")],
    )
    result, _ = run(f, inputs={"n": "42"}, env={"BASE": "https://dev.test"})
    assert result.state == "passed"
    assert result.end_outputs["out"] == {"n": 42, "base": "https://dev.test"}


def test_bind_missing_and_unknown_inputs_are_run_errors():
    f = flow(start({"name": "n", "type": "number"}), end({"name": "x"}))
    for bad_inputs in ({}, {"n": 1, "ghost": 2}, {"n": "not-a-number"}):
        result, records = run(f, inputs=bad_inputs)
        assert result.state == "error"
        assert result.exit_code == 2
        assert result.error_reason == "bind_error"
        assert events_of(records, "run_finished")  # report still written


def test_env_required_missing_is_run_error():
    f = flow(start(), end({"name": "x", "required": False}), env_required=["API_KEY"])
    result, _ = run(f, env={})
    assert result.state == "error"
    assert result.error_reason == "env_missing"
    result, _ = run(f, env={"API_KEY": "k"})
    assert result.state == "passed"


def test_flow_reference_without_workspace_root_is_clean_failure():
    # the full catalog is runnable since S3/M5; standalone engine use
    # without workspace_root degrades to a node error, never a crash
    f = flow(
        start(),
        {
            "id": "lp",
            "type": "loop",
            "config": {"over": "[1, 2]", "body": "flows/x"},
        },
        end({"name": "x", "required": False}),
        edges=[("start.out", "lp.trigger")],
    )
    result, _ = run(f)
    assert result.state == "failed"
    assert result.unhandled_errors[0]["kind"] == "flow_load_error"
    assert "workspace_root" in result.unhandled_errors[0]["message"]


# --------------------------------------------------------------------------
# Budget, deadline, abort, max_seconds (FR-407/408/410/411)


def test_budget_exhaustion_names_hot_edge():
    # Runtime backstop for a guardless cycle (the checker would W101
    # this; the engine must survive it regardless). Two conditions
    # ping-pong forever.
    f = flow(
        start(),
        {"id": "a", "type": "condition", "config": {"expr": "true"}},
        {"id": "b", "type": "condition", "config": {"expr": "true"}},
        end({"name": "x", "required": False}),
        edges=[("start.out", "a.in"), ("a.true", "b.in"), ("b.true", "a.in")],
    )
    result, records = run(f, mani=manifest(message_budget=25))
    assert result.state == "error"
    assert result.exit_code == 2
    assert result.error_reason == "budget_exhausted"
    assert "a.true → b.in" in str(result.unhandled_errors) or "b.true → a.in" in str(
        result.unhandled_errors
    )
    assert events_of(records, "budget_warning")
    assert events_of(records, "run_finished")


def test_run_deadline_expires_with_report(mani_timeout=0.05):
    f = flow(
        start(),
        {"id": "sleep", "type": "delay", "config": {"seconds": 30}},
        end({"name": "x"}),
        edges=[("start.out", "sleep.in"), ("sleep.out", "end.x")],
    )
    result, records = run(f, timeout=mani_timeout)
    assert result.state == "error"
    assert result.exit_code == 2
    assert result.error_reason == "run_timeout"
    finished = events_of(records, "run_finished")[0]
    assert finished["error_reason"] == "run_timeout"  # JSONL written (D24)


def test_abort_finalizes_as_aborted():
    f = flow(
        start(),
        {"id": "sleep", "type": "delay", "config": {"seconds": 30}},
        end({"name": "x"}),
        edges=[("start.out", "sleep.in"), ("sleep.out", "end.x")],
    )
    sink = CaptureSink()

    async def scenario():
        flow_run = FlowRun(
            f,
            flow_identity="flows/t",
            manifest=manifest(),
            env={},
            env_name=None,
            inputs={},
            stream=EventStream("r", NO_SECRETS, [sink]),
        )
        task = asyncio.create_task(flow_run.execute())
        await asyncio.sleep(0.05)
        flow_run.abort()
        return await asyncio.wait_for(task, timeout=10)

    result = asyncio.run(scenario())
    assert result.state == "aborted"
    assert result.exit_code == 130
    assert events_of(sink.records, "run_finished")[0]["state"] == "aborted"


def test_explicit_max_seconds_trips_portless_node_to_failed():
    # D24 + EC24: delay has no error port — a tripped ceiling is an
    # unhandled node error ⇒ run failed (not error, not a hang)
    f = flow(
        start(),
        {
            "id": "sleep",
            "type": "delay",
            "config": {"seconds": 30},
            "max_seconds": 0.05,
        },
        end({"name": "x", "required": False}),
        edges=[("start.out", "sleep.in"), ("sleep.out", "end.x")],
    )
    result, _ = run(f)
    assert result.state == "failed"
    assert result.unhandled_errors[0]["kind"] == "timeout"
    assert result.unhandled_errors[0]["node"] == "sleep"


def test_default_ceiling_exempts_delay():
    # The manifest default applies to request/python only (D24): a delay
    # longer than node_timeout_s must NOT be killed by the default.
    f = flow(
        start(),
        {"id": "sleep", "type": "delay", "config": {"seconds": 0.1}},
        end({"name": "x"}),
        edges=[("start.out", "sleep.in"), ("sleep.out", "end.x")],
    )
    result, _ = run(f, mani=manifest(node_timeout_s=0.01))
    assert result.state == "passed"


def test_templated_delay_seconds():
    f = flow(
        start({"name": "wait", "type": "number"}),
        {"id": "sleep", "type": "delay", "config": {"seconds": "{{ inputs.wait }}"}},
        end({"name": "x"}),
        edges=[("start.out", "sleep.in"), ("sleep.out", "end.x")],
    )
    result, _ = run(f, inputs={"wait": 0.01})
    assert result.state == "passed"


# --------------------------------------------------------------------------
# Events + report shape (FR-405/406, EN §7)


def test_event_stream_shape_and_never_fired():
    f = flow(
        start(),
        {"id": "gate", "type": "condition", "config": {"expr": "false"}},
        {"id": "sleep", "type": "delay", "config": {"seconds": 0.01}},
        end({"name": "x", "required": False}),
        edges=[
            ("start.out", "gate.in"),
            ("gate.true", "sleep.in"),
            ("sleep.out", "end.x"),
        ],
    )
    result, records = run(f)
    assert result.state == "passed"
    assert result.nodes_never_fired == ["sleep"]  # skipped, first-class
    fired = [r["node"] for r in events_of(records, "node_fired")]
    assert fired == ["start", "gate"]
    emitted = events_of(records, "message_emitted")[0]
    assert emitted["from_port"] == "start.out"
    assert (emitted["to_node"], emitted["to_port"]) == ("gate", "in")
    assert emitted["msg_id"] == "m-000001"
    finished = events_of(records, "run_finished")[0]
    assert finished["nodes_never_fired"] == ["sleep"]
    assert finished["asserts"] == {"passed": 0, "failed": 0}


def test_exit_codes_mapping():
    assert EXIT_CODES == {"passed": 0, "failed": 1, "error": 2, "aborted": 130}


# --------------------------------------------------------------------------
# Firing rules 2–3 + merge (S3/M1: TR-1, FR-403, FR-508)

AB = (start({"name": "a", "type": "number"}, {"name": "b", "type": "number"}),)


def test_merge_any_forwards_each_delivery():
    f = flow(
        *AB,
        {"id": "m", "type": "merge", "config": {"mode": "any"}},
        end({"name": "x"}),
        edges=[("start.a", "m.in1"), ("start.b", "m.in2"), ("m.out", "end.x")],
    )
    result, records = run(f, inputs={"a": 1, "b": 2})
    assert result.state == "passed"
    fired = [r for r in events_of(records, "node_fired") if r["node"] == "m"]
    assert [r["firing_no"] for r in fired] == [1, 2]
    assert result.end_outputs == {"x": 2}  # latest delivery passed through


def test_merge_all_rendezvous_emits_dict_and_clears_slots():
    # TR-1: strict rendezvous — after the emit clears the slots, a lone
    # re-delivery to ONE input stalls (documented EC03/EC04), it never
    # re-fires with a stale value from the previous rendezvous.
    f = flow(
        *AB,
        {"id": "m", "type": "merge", "config": {"mode": "all"}},
        {"id": "dly", "type": "delay", "config": {"seconds": 0.03}},
        end({"name": "x"}),
        edges=[
            ("start.a", "m.in1"),
            ("start.b", "m.in2"),
            ("start.a", "dly.in"),
            ("dly.out", "m.in1"),  # late refill of in1 only
            ("m.out", "end.x"),
        ],
    )
    result, records = run(f, inputs={"a": 1, "b": 2})
    assert result.state == "passed"
    assert result.end_outputs == {"x": {"in1": 1, "in2": 2}}
    fired = [r for r in events_of(records, "node_fired") if r["node"] == "m"]
    assert len(fired) == 1  # the partial refill did NOT re-fire


def test_merge_all_refires_after_full_refill():
    f = flow(
        *AB,
        {"id": "m", "type": "merge", "config": {"mode": "all"}},
        {"id": "dly1", "type": "delay", "config": {"seconds": 0.03}},
        {"id": "dly2", "type": "delay", "config": {"seconds": 0.06}},
        end({"name": "x"}),
        edges=[
            ("start.a", "m.in1"),
            ("start.b", "m.in2"),
            ("start.a", "dly1.in"),
            ("dly1.out", "m.in1"),
            ("start.out", "dly2.in"),
            ("dly2.out", "m.in2"),
            ("m.out", "end.x"),
        ],
    )
    result, records = run(f, inputs={"a": 1, "b": 2})
    assert result.state == "passed"
    fired = [r for r in events_of(records, "node_fired") if r["node"] == "m"]
    assert len(fired) == 2  # second full set → second rendezvous
    assert result.end_outputs == {"x": {"in1": 1, "in2": {"a": 1, "b": 2}}}


def test_merge_collect_batches_count_and_drops_leftovers():
    f = flow(
        start(
            {"name": "a", "type": "number"},
            {"name": "b", "type": "number"},
            {"name": "c", "type": "number"},
        ),
        {"id": "m", "type": "merge", "config": {"mode": "collect", "count": 2}},
        end({"name": "x"}),
        edges=[
            ("start.a", "m.in1"),
            ("start.b", "m.in1"),
            ("start.c", "m.in1"),
            ("m.out", "end.x"),
        ],
    )
    result, records = run(f, inputs={"a": 1, "b": 2, "c": 3})
    assert result.state == "passed"
    fired = [r for r in events_of(records, "node_fired") if r["node"] == "m"]
    assert len(fired) == 1  # one full batch; the leftover never emits
    assert result.end_outputs == {"x": [1, 2]}


def test_merge_all_partial_rendezvous_is_skipped_not_error():
    f = flow(
        *AB,
        {"id": "gate", "type": "condition", "config": {"expr": "false"}},
        {"id": "m", "type": "merge", "config": {"mode": "all"}},
        end({"name": "x", "required": False}),
        edges=[
            ("start.a", "m.in1"),
            ("start.b", "gate.in"),
            ("gate.true", "m.in2"),  # never delivered
            ("m.out", "end.x"),
        ],
    )
    result, _ = run(f, inputs={"a": 1, "b": 2})
    assert result.state == "passed"
    assert "m" in result.nodes_never_fired  # skipped is first-class
    assert result.end_outputs == {"x": None}


def test_rule2_fires_on_full_slots_and_retains_latest_value():
    # TR-1's other half: a plain multi-input node (rule 2) KEEPS its
    # slots across firings — a later delivery overwrites one slot and
    # re-fires immediately, unlike merge `all`'s clear-on-emit. The
    # trigger of each firing is the delivery that completed/overwrote
    # the set, never the first arrival. (Two fabricated inputs on a
    # condition node — checker-invalid, runtime-tolerated, see header.)
    f = flow(
        start(),
        {"id": "dly1", "type": "delay", "config": {"seconds": 0.03}},
        {"id": "dly2", "type": "delay", "config": {"seconds": 0.09}},
        {
            "id": "joint",
            "type": "condition",
            "config": {"expr": "trigger.meta.produced_by != 'start.out'"},
        },
        end({"name": "x", "required": False}),
        edges=[
            ("start.out", "joint.in"),  # slot filled at t≈0, absorbed
            ("start.out", "dly1.in"),
            ("dly1.out", "joint.other"),  # completes the set → firing 1
            ("start.out", "dly2.in"),
            ("dly2.out", "joint.in"),  # overwrites `in` → firing 2
            ("joint.true", "end.x"),
        ],
    )
    result, records = run(f)
    assert result.state == "passed"
    fired = [r for r in events_of(records, "node_fired") if r["node"] == "joint"]
    assert len(fired) == 2  # slots retained: one overwrite re-fired
    routed = [
        r
        for r in events_of(records, "message_emitted")
        if r["from_port"] == "joint.true"
    ]
    assert len(routed) == 2  # both triggers were the completing delivery
    assert result.end_outputs["x"] is not None


def test_budget_cycle_through_merge():
    # TR-1 fast-cycle backstop: merge `any` inside a guardless cycle —
    # the inline (in-pump) merge emission must hit the budget valve and
    # finalize as run `error`, exactly like task-side emissions.
    f = flow(
        start(),
        {"id": "a", "type": "condition", "config": {"expr": "true"}},
        {"id": "m", "type": "merge", "config": {"mode": "any"}},
        end({"name": "x", "required": False}),
        edges=[("start.out", "a.in"), ("a.true", "m.in1"), ("m.out", "a.in")],
    )
    result, records = run(f, mani=manifest(message_budget=25))
    assert result.state == "error"
    assert result.error_reason == "budget_exhausted"
    assert events_of(records, "run_finished")
