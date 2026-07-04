"""M1: JSON Schema export — the interchange type authority (FR-206),
consumed by ajv on the canvas side."""

from napflow.core.models import (
    JSON_SCHEMA_DIALECT,
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
