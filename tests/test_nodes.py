"""Simple frame-local nodes (S3/M3): switch (FR-507), set/get
(FR-513, D17/EC19), log (FR-512), fixture (FR-514, D17 auto-seed,
per-run cache) — plus rule-6 seeding through the EC08 empty-seed guard
(a fixture-driven flow with a fully unwired start must still run).
"""

import textwrap

from test_engine import end, events_of, flow, run, start


def write_fixture(tmp_path, rel, content):
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# switch (FR-507)


def switch_flow(cases):
    return flow(
        start({"name": "n", "type": "number"}),
        {
            "id": "sw",
            "type": "switch",
            "config": {"expr": "inputs.n", "cases": cases},
        },
        end(
            {"name": "small", "required": False},
            {"name": "big", "required": False},
            {"name": "other", "required": False},
        ),
        edges=[
            ("start.n", "sw.in"),
            ("sw.small", "end.small"),
            ("sw.big", "end.big"),
            ("sw.default", "end.other"),
        ],
    )


def test_switch_routes_matching_case_and_default():
    cases = [{"name": "small", "equals": 1}, {"name": "big", "equals": 100}]
    result, _ = run(switch_flow(cases), inputs={"n": 100})
    assert result.state == "passed"
    assert result.end_outputs == {"small": None, "big": 100, "other": None}
    result, _ = run(switch_flow(cases), inputs={"n": 7})
    assert result.end_outputs == {"small": None, "big": None, "other": 7}


def test_switch_first_matching_case_wins():
    cases = [{"name": "small", "equals": 5}, {"name": "big", "equals": 5}]
    result, _ = run(switch_flow(cases), inputs={"n": 5})
    assert result.end_outputs == {"small": 5, "big": None, "other": None}


def test_switch_eval_error_is_unhandled_node_error():
    f = flow(
        start(),
        {
            "id": "sw",
            "type": "switch",
            "config": {
                "expr": "nodes.ghost.out",
                "cases": [{"name": "a", "equals": 1}],
            },
        },
        end({"name": "x", "required": False}),
        edges=[("start.out", "sw.in"), ("sw.a", "end.x")],
    )
    result, _ = run(f)
    assert result.state == "failed"  # EC24: no error port on switch
    assert result.unhandled_errors[0]["kind"] == "template_error"


# --------------------------------------------------------------------------
# set / get (FR-513, D17, EC19)


def test_set_forwards_written_value_and_get_reads_it():
    f = flow(
        start({"name": "payload", "type": "object"}),
        {
            "id": "remember",
            "type": "set",
            "config": {"name": "saved", "value": "{{ inputs.payload }}"},
        },
        {"id": "recall", "type": "get", "config": {"name": "saved"}},
        end({"name": "forwarded"}, {"name": "read"}),
        edges=[
            ("start.out", "remember.in"),
            ("remember.out", "end.forwarded"),  # set forwards the WRITTEN value
            ("remember.out", "recall.trigger"),  # EC19: a real path Set → Get
            ("recall.value", "end.read"),
        ],
    )
    payload = {"user": "ada", "roles": ["qa", "dev"]}
    result, _ = run(f, inputs={"payload": payload})
    assert result.state == "passed"
    # D25 native rule: the single-expression template stays a dict
    assert result.end_outputs == {"forwarded": payload, "read": payload}


def test_get_of_unset_variable_fails_run():
    f = flow(
        start(),
        {"id": "recall", "type": "get", "config": {"name": "never_written"}},
        end({"name": "x", "required": False}),
        edges=[("start.out", "recall.trigger"), ("recall.value", "end.x")],
    )
    result, _ = run(f)
    assert result.state == "failed"  # never a silent null
    assert result.unhandled_errors[0]["kind"] == "variable_unset"
    assert "never_written" in result.unhandled_errors[0]["message"]


# --------------------------------------------------------------------------
# log (FR-512)


def test_log_emits_event_and_passes_through():
    f = flow(
        start({"name": "n", "type": "number"}),
        {
            "id": "show",
            "type": "log",
            "config": {"label": "checkpoint", "level": "warn"},
        },
        end({"name": "x"}),
        edges=[("start.n", "show.in"), ("show.out", "end.x")],
    )
    result, records = run(f, inputs={"n": 42})
    assert result.state == "passed"
    assert result.end_outputs == {"x": 42}  # pass-through unchanged
    event = events_of(records, "log")[0]
    assert (event["label"], event["level"]) == ("checkpoint", "warn")
    assert event["value"] == 42
    assert event["node"] == "show"


# --------------------------------------------------------------------------
# fixture (FR-514, D17)


def fixture_flow(file, format=None, wire_trigger=False):
    config = {"file": file}
    if format is not None:
        config["format"] = format
    edges = [("fx.value", "end.x")]
    if wire_trigger:
        edges.insert(0, ("start.out", "fx.trigger"))
    return flow(
        start(),
        {"id": "fx", "type": "fixture", "config": config},
        end({"name": "x", "required": False}),
        edges=edges,
    )


def test_fixture_json_auto_seeds_without_trigger(tmp_path):
    # D17/rule 6: unconnected trigger → one firing at frame start; the
    # start node here is fully unwired, so ONLY the fixture drives the
    # run (the EC08 empty-seed guard must not finalize early)
    write_fixture(tmp_path, "fixtures/users.json", '[{"name": "Ada"}]')
    result, records = run(fixture_flow("fixtures/users.json"), workspace_root=tmp_path)
    assert result.state == "passed"
    assert result.end_outputs == {"x": [{"name": "Ada"}]}
    fired = [r["node"] for r in events_of(records, "node_fired")]
    assert fired.count("fx") == 1


def test_fixture_fires_per_delivery_when_trigger_wired(tmp_path):
    write_fixture(tmp_path, "fixtures/one.json", '{"k": 1}')
    result, records = run(
        fixture_flow("fixtures/one.json", wire_trigger=True),
        workspace_root=tmp_path,
    )
    assert result.state == "passed"
    assert result.end_outputs == {"x": {"k": 1}}
    fired = [r["node"] for r in events_of(records, "node_fired")]
    assert fired.count("fx") == 1  # exactly the wired delivery, no auto-seed


def test_fixture_csv_parses_to_dicts_with_string_values(tmp_path):
    write_fixture(tmp_path, "fixtures/rows.csv", "name,age\nAda,36\nLinus,54\n")
    result, _ = run(fixture_flow("fixtures/rows.csv"), workspace_root=tmp_path)
    assert result.state == "passed"
    assert result.end_outputs == {
        "x": [{"name": "Ada", "age": "36"}, {"name": "Linus", "age": "54"}]
    }


def test_fixture_error_shapes(tmp_path):
    write_fixture(tmp_path, "fixtures/empty.csv", "")
    write_fixture(tmp_path, "fixtures/ragged.csv", "a,b\n1,2,3\n")
    write_fixture(tmp_path, "fixtures/data.unknown", "{}")
    write_fixture(tmp_path, "fixtures/bad.json", "{nope")
    for file, needle in [
        ("fixtures/empty.csv", "header row"),
        ("fixtures/ragged.csv", "more fields than the header"),
        ("fixtures/data.unknown", "cannot infer format"),
        ("fixtures/bad.json", "invalid JSON"),
        ("fixtures/missing.json", "cannot read fixture"),
    ]:
        result, _ = run(fixture_flow(file), workspace_root=tmp_path)
        assert result.state == "failed", file  # EC24: no error port
        assert result.unhandled_errors[0]["kind"] == "fixture_error"
        assert needle in result.unhandled_errors[0]["message"]


def test_fixture_cached_per_run_survives_file_deletion(tmp_path):
    # read once, cached per run (FR-514): a python node deletes the
    # file mid-run (also proving the worker cwd = workspace root pin),
    # then a second fixture node on the SAME path reads from cache
    write_fixture(tmp_path, "fixtures/data.json", "[1, 2, 3]")
    (tmp_path / "nodes.py").write_text(
        textwrap.dedent(
            """
            import os

            def nuke(rows):
                os.remove("fixtures/data.json")
                return {"out": rows}
            """
        ),
        encoding="utf-8",
    )
    f = flow(
        start(),
        {"id": "fx1", "type": "fixture", "config": {"file": "fixtures/data.json"}},
        {
            "id": "rm",
            "type": "python",
            "config": {"function": "nuke", "outputs": ["out"]},
        },
        {"id": "fx2", "type": "fixture", "config": {"file": "fixtures/data.json"}},
        end({"name": "x"}),
        edges=[
            ("fx1.value", "rm.rows"),
            ("rm.out", "fx2.trigger"),
            ("fx2.value", "end.x"),
        ],
    )
    result, _ = run(f, flow_dir=tmp_path, workspace_root=tmp_path)
    assert result.state == "passed"
    assert result.end_outputs == {"x": [1, 2, 3]}
    assert not (tmp_path / "fixtures" / "data.json").exists()
