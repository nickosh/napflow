"""JSON Schema export and visual-form coverage (FR-206/FR-1114)."""

import json
from pathlib import Path

from pydantic import TypeAdapter

from napflow.core.models import (
    JSON_SCHEMA_DIALECT,
    AssertConfig,
    AssertNode,
    ConditionConfig,
    ConditionNode,
    CounterConfig,
    CounterNode,
    DelayConfig,
    DelayNode,
    Edge,
    EndConfig,
    EndNode,
    EndPort,
    ExprCheck,
    FixtureConfig,
    FixtureNode,
    FlowEnv,
    FlowFile,
    FlowMeta,
    FlowNode,
    FlowRefConfig,
    GetConfig,
    GetNode,
    LogConfig,
    LogNode,
    LoopConfig,
    LoopNode,
    MergeConfig,
    MergeNode,
    NoteConfig,
    NoteNode,
    PythonConfig,
    PythonNode,
    RequestConfig,
    RequestNode,
    ResponseTimeCheck,
    RetryConfig,
    SetConfig,
    SetNode,
    StartConfig,
    StartNode,
    StartPort,
    StatusCheck,
    SwitchCase,
    SwitchConfig,
    SwitchNode,
    TimeoutConfig,
    TimeoutNode,
    flow_json_schema,
    manifest_json_schema,
)

CATALOG = {
    "start", "end", "request", "python", "assert", "condition", "switch",
    "loop", "flow", "set", "get", "merge", "counter", "timeout", "delay",
    "log", "fixture", "note",
}  # fmt: skip


def test_flow_schema_shape() -> None:
    schema = flow_json_schema()
    assert schema["$schema"] == JSON_SCHEMA_DIALECT

    node_items = schema["properties"]["nodes"]["items"]
    mapping = node_items["discriminator"]["mapping"]
    assert set(mapping) == CATALOG

    edge = schema["$defs"]["Edge"]
    assert set(edge["required"]) == {"from", "to"}  # alias, not from_


def test_manifest_schema_shape() -> None:
    schema = manifest_json_schema()
    assert schema["$schema"] == JSON_SCHEMA_DIALECT
    assert "schema" in schema["required"]
    run_defaults = schema["$defs"]["RunDefaults"]["properties"]
    assert run_defaults["message_budget"]["default"] == 100_000


CONFIG_MODELS = {
    "start": StartConfig,
    "end": EndConfig,
    "request": RequestConfig,
    "python": PythonConfig,
    "assert": AssertConfig,
    "condition": ConditionConfig,
    "switch": SwitchConfig,
    "loop": LoopConfig,
    "flow": FlowRefConfig,
    "set": SetConfig,
    "get": GetConfig,
    "merge": MergeConfig,
    "counter": CounterConfig,
    "timeout": TimeoutConfig,
    "delay": DelayConfig,
    "log": LogConfig,
    "fixture": FixtureConfig,
    "note": NoteConfig,
}

NODE_MODELS = (
    StartNode,
    EndNode,
    RequestNode,
    PythonNode,
    AssertNode,
    ConditionNode,
    SwitchNode,
    LoopNode,
    FlowNode,
    SetNode,
    GetNode,
    MergeNode,
    CounterNode,
    TimeoutNode,
    DelayNode,
    LogNode,
    FixtureNode,
    NoteNode,
)

NESTED_MODELS = {
    model.__name__: model
    for model in (
        StartPort,
        EndPort,
        RetryConfig,
        StatusCheck,
        ExprCheck,
        ResponseTimeCheck,
        SwitchCase,
    )
}


def _annotation_json_types(annotation: object) -> set[str]:
    schema = TypeAdapter(annotation).json_schema()
    definitions = schema.get("$defs", {})
    seen_refs: set[str] = set()

    def visit(fragment: object) -> set[str]:
        if not isinstance(fragment, dict):
            return set()
        found: set[str] = set()
        type_ = fragment.get("type")
        if isinstance(type_, str):
            found.add(type_)
        elif isinstance(type_, list):
            found.update(item for item in type_ if isinstance(item, str))
        ref = fragment.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/") and ref not in seen_refs:
            seen_refs.add(ref)
            found.update(visit(definitions.get(ref.removeprefix("#/$defs/"))))
        for keyword in ("anyOf", "oneOf", "allOf"):
            choices = fragment.get(keyword)
            if isinstance(choices, list):
                for choice in choices:
                    found.update(visit(choice))
        return found

    return visit(schema) - {"null"}


def _assert_form_kind_matches_annotation(annotation: object, kind: str) -> None:
    types = _annotation_json_types(annotation)
    match kind:
        case "string" | "text" | "function" | "select":
            assert types == {"string"}
        case "number":
            assert types and types <= {"integer", "number"}
        case "boolean":
            assert types == {"boolean"}
        case "templatable-number":
            assert "string" in types
            assert types - {"string"} and types <= {"string", "integer", "number"}
        case "templatable-boolean":
            assert types == {"string", "boolean"}
        case "checks" | "cases":
            assert types == {"array"}
        case "json":
            pass  # JSON cells deliberately cover any JSON-shaped annotation.
        case _:
            raise AssertionError(f"unknown config form kind {kind!r}")


def test_visual_form_coverage_tracks_every_flow_schema_field() -> None:
    """The JSON contract is the cross-language drift tripwire.

    Pytest proves it names every Pydantic field; Vitest separately proves each
    config entry maps to an implemented form kind or dedicated editor. YAML-only
    is therefore an explicit policy, never a field silently missed by the UI.
    """
    path = Path(__file__).parents[1] / "ui" / "src" / "form-coverage.json"
    coverage = json.loads(path.read_text(encoding="utf-8"))

    assert set(coverage) == {
        "flow",
        "flow_meta",
        "flow_env",
        "edge",
        "node",
        "configs",
        "nested",
    }
    assert set(coverage["flow"]) == set(FlowFile.model_fields)
    assert set(coverage["flow_meta"]) == set(FlowMeta.model_fields)
    assert set(coverage["flow_env"]) == set(FlowEnv.model_fields)
    assert set(coverage["edge"]) == set(Edge.model_fields)
    assert set(coverage["configs"]) == set(CONFIG_MODELS)
    for node_type, model in CONFIG_MODELS.items():
        assert set(coverage["configs"][node_type]) == set(model.model_fields)
        for field_name, form_kind in coverage["configs"][node_type].items():
            if form_kind in {"start-ports", "end-ports"}:
                continue
            _assert_form_kind_matches_annotation(
                model.model_fields[field_name].annotation, form_kind
            )
    for model in NODE_MODELS:
        assert set(coverage["node"]) == set(model.model_fields)
        _assert_form_kind_matches_annotation(
            model.model_fields["max_seconds"].annotation, "number"
        )
    assert set(coverage["nested"]) == set(NESTED_MODELS)
    for name, model in NESTED_MODELS.items():
        assert set(coverage["nested"][name]) == set(model.model_fields)
        for field_name, form_kind in coverage["nested"][name].items():
            if form_kind == "templatable-number":
                _assert_form_kind_matches_annotation(
                    model.model_fields[field_name].annotation, form_kind
                )
