"""FR-201: flow.yaml parses into Pydantic models covering the full v1
node catalog. Graph-level rules (E003–E007, E011–E012) are checker
territory (M4) and deliberately NOT validated here."""

import pytest
from pydantic import ValidationError

from napflow.core.models import (
    CounterNode,
    EndNode,
    FlowFile,
    MergeConfig,
    RequestNode,
    StartNode,
)

# --------------------------------------------------------------------------
# The spec's own example


def test_spec_example_parses(load_yaml) -> None:
    flow = FlowFile.model_validate(load_yaml("create_until_ready.yaml"))

    assert flow.schema_ == "napflow/v1"
    assert flow.flow.name == "create_until_ready"
    assert flow.env is not None and flow.env.required == ["API_TOKEN", "BASE_URL"]
    assert len(flow.nodes) == 9
    assert len(flow.edges) == 10
    assert flow.layout is not None and flow.layout["start"] == (40, 200)

    by_id = {node.id: node for node in flow.nodes}
    start = by_id["start"]
    assert isinstance(start, StartNode)
    assert start.config.ports[0].type == "string"
    assert "default" in start.config.ports[0].model_fields_set

    create = by_id["create"]
    assert isinstance(create, RequestNode)
    assert create.config.method == "POST"
    assert create.config.body == {"kind": "sync_users"}

    attempts = by_id["attempts"]
    assert isinstance(attempts, CounterNode)
    assert attempts.config.count == 10

    end = by_id["end"]
    assert isinstance(end, EndNode)
    required = {p.name: p.required for p in end.config.ports}
    assert required == {"job": True, "gave_up": False}

    assert flow.edges[0].from_ == "start.out"
    assert flow.edges[0].to == "create.trigger"


# --------------------------------------------------------------------------
# Full catalog coverage (the types the example doesn't touch)


def _node(id_: str, type_: str, config: dict | None = None, **extra) -> dict:
    node: dict = {"id": id_, "type": type_}
    if config is not None:
        node["config"] = config
    node.update(extra)
    return node


KITCHEN_SINK_NODES = [
    _node("start", "start"),  # config omitted entirely — zero ports
    _node("fetch", "python", {"function": "fetch_users", "outputs": ["users"]}),
    _node(
        "verify",
        "assert",
        {
            "checks": [
                {"kind": "status", "equals": 201},
                {"kind": "expr", "expr": "trigger.value.body.id", "op": "present"},
                {
                    "kind": "expr",
                    "expr": "trigger.value.body.email",
                    "op": "matches",
                    "value": "^test\\+.*@example\\.com$",
                },
                {"kind": "response_time", "under_ms": 1500},
            ],
            "mode": "fail_fast",
        },
    ),
    _node(
        "route",
        "switch",
        {
            "expr": "trigger.value.body.state",
            "cases": [
                {"name": "paid", "equals": "PAID"},
                {"name": "void", "equals": "VOID"},
            ],
        },
    ),
    _node(
        "each_user",
        "loop",
        {
            "over": "nodes.fetch.users",
            "body": "flows/enroll",
            "mode": "parallel",
            "max_concurrency": 8,
            "on_error": "continue",
            "fresh_session": True,
        },
        max_seconds=600,  # node-level key, valid on any node (D24)
    ),
    _node("login", "flow", {"flow": "flows/login"}),
    _node("remember", "set", {"name": "token", "value": "{{ trigger.value }}"}),
    _node("recall", "get", {"name": "token"}),
    _node("gather", "merge", {"mode": "collect", "count": 3}),
    _node("deadline", "timeout", {"seconds": 30.5}),
    _node("pause", "delay", {"seconds": "{{ inputs.wait_s }}"}),  # templatable
    _node("trace", "log", {}),  # label/level fully defaulted
    _node("users", "fixture", {"file": "fixtures/users.json"}),
    _node("docs", "note", {"text": "## Retry pattern\nSee spec."}),
    _node("end", "end"),
]


def test_kitchen_sink_covers_remaining_catalog() -> None:
    flow = FlowFile.model_validate(
        {
            "schema": "napflow/v1",
            "flow": {"name": "kitchen_sink"},
            "nodes": KITCHEN_SINK_NODES,
        }
    )
    parsed_types = {node.type for node in flow.nodes}
    example_types = {"request", "condition", "counter", "merge"}
    catalog = {
        "start", "end", "request", "python", "assert", "condition", "switch",
        "loop", "flow", "set", "get", "merge", "counter", "timeout", "delay",
        "log", "fixture", "note",
    }  # fmt: skip
    assert parsed_types | example_types == catalog

    by_id = {node.id: node for node in flow.nodes}
    assert by_id["each_user"].max_seconds == 600
    assert by_id["pause"].config.seconds == "{{ inputs.wait_s }}"
    assert by_id["trace"].config.level == "info"
    assert by_id["users"].config.format is None  # inferred from extension
    assert by_id["start"].config.ports == []
    assert flow.edges == []  # edges default


# --------------------------------------------------------------------------
# Rejections


def _minimal(nodes: list[dict], **top) -> dict:
    return {"schema": "napflow/v1", "flow": {"name": "t"}, "nodes": nodes, **top}


@pytest.mark.parametrize(
    "bad_flow",
    [
        # unknown node type (E002 surface)
        _minimal([_node("a", "webhook", {})]),
        # unknown config key on a known node type
        _minimal([_node("a", "request", {"url": "x", "verb": "GET"})]),
        # node-level unknown key
        _minimal([_node("a", "note", {"text": "hi"}, colour="red")]),
        # invalid node id charset (E011 surface)
        _minimal([_node("9lives", "note", {"text": "hi"})]),
        _minimal([_node("dash-ed", "note", {"text": "hi"})]),
        # request without url
        _minimal([_node("a", "request", {"method": "GET"})]),
        # edge endpoint without a port
        _minimal(
            [_node("a", "note", {"text": "hi"})],
            edges=[{"from": "a", "to": "b.in"}],
        ),
        # assert with zero checks
        _minimal([_node("a", "assert", {"checks": []})]),
        # set without value
        _minimal([_node("a", "set", {"name": "token"})]),
        # unknown schema version
        {"schema": "napflow/v2", "flow": {"name": "t"}, "nodes": []},
    ],
)
def test_rejected(bad_flow: dict) -> None:
    with pytest.raises(ValidationError):
        FlowFile.model_validate(bad_flow)


def test_merge_count_rules() -> None:
    with pytest.raises(ValidationError, match="requires 'count'"):
        MergeConfig.model_validate({"mode": "collect"})
    with pytest.raises(ValidationError, match="only valid with mode 'collect'"):
        MergeConfig.model_validate({"mode": "any", "count": 3})
    assert MergeConfig.model_validate({"mode": "all"}).count is None


def test_expr_check_value_rules() -> None:
    base = {"kind": "expr", "expr": "trigger.value"}
    with pytest.raises(ValidationError, match="requires a 'value'"):
        FlowFile.model_validate(
            _minimal([_node("a", "assert", {"checks": [{**base, "op": "equals"}]})])
        )
    with pytest.raises(ValidationError, match="takes no 'value'"):
        FlowFile.model_validate(
            _minimal(
                [
                    _node(
                        "a",
                        "assert",
                        {"checks": [{**base, "op": "present", "value": 1}]},
                    )
                ]
            )
        )
    # equals against an explicit null is legitimate (absent ≠ null)
    FlowFile.model_validate(
        _minimal(
            [
                _node(
                    "a", "assert", {"checks": [{**base, "op": "equals", "value": None}]}
                )
            ]
        )
    )


def test_set_value_null_is_not_absent() -> None:
    flow = FlowFile.model_validate(
        _minimal([_node("a", "set", {"name": "token", "value": None})])
    )
    assert flow.nodes[0].config.value is None


def test_models_are_frozen() -> None:
    flow = FlowFile.model_validate(_minimal([_node("a", "note", {"text": "hi"})]))
    with pytest.raises(ValidationError):
        flow.nodes[0].id = "b"  # read-only views (FR-208)
