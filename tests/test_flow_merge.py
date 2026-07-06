"""merge_flow_document — the canvas write path (FR-1003, EC29).

The contract under test: a no-op merge re-emits byte-identical files, a
layout-only move diffs only the `layout:` block, and comments survive
every edit that doesn't delete their anchor.
"""

import difflib
from pathlib import Path
from typing import Any

import pytest

from napflow.core.loader import (
    emit_document,
    load_document,
    load_flow,
    merge_flow_document,
)

DATA_DIR = Path(__file__).resolve().parent / "data"


def _model_json(path: Path) -> dict[str, Any]:
    """The PUT payload shape: exclude_unset keeps defaults out of files."""
    return load_flow(path).model.model_dump(
        mode="json", by_alias=True, exclude_unset=True
    )


def _merged_text(path: Path, updated: dict[str, Any]) -> str:
    doc = load_document(path)
    merge_flow_document(doc, updated)
    return emit_document(doc)


def _changed_lines(before: str, after: str) -> list[str]:
    return [
        line
        for line in difflib.unified_diff(
            before.splitlines(), after.splitlines(), lineterm="", n=0
        )
        if line[:1] in {"+", "-"} and line[:3] not in {"+++", "---"}
    ]


@pytest.mark.parametrize("name", ["commented_flow.yaml", "create_until_ready.yaml"])
def test_noop_merge_is_byte_identical(name: str) -> None:
    path = DATA_DIR / name
    canonical = emit_document(load_document(path))
    assert _merged_text(path, _model_json(path)) == canonical


def test_layout_only_move_diffs_only_layout_block() -> None:
    """The golden canvas-diff (FR-1003): dragging a node touches nothing
    but the `layout:` block."""
    path = DATA_DIR / "commented_flow.yaml"
    before = emit_document(load_document(path))
    updated = _model_json(path)
    updated["layout"]["greet"] = [260.0, 40.0]

    after = _merged_text(path, updated)

    changed = _changed_lines(before, after)
    assert changed == ["-  greet: [100, 0]", "+  greet: [260, 40]"]


def test_config_edit_preserves_comments_elsewhere() -> None:
    path = DATA_DIR / "commented_flow.yaml"
    updated = _model_json(path)
    greet = next(n for n in updated["nodes"] if n["id"] == "greet")
    greet["config"]["label"] = "renamed"

    after = _merged_text(path, updated)

    assert "# top-of-file comment — must survive round-trip" in after
    assert "# trailing comment survives" in after
    assert "# the entry node" in after
    assert 'label: "renamed"' in after
    assert "text: |" in after  # untouched literal block keeps its style


def test_node_add_and_delete_by_id() -> None:
    path = DATA_DIR / "commented_flow.yaml"
    updated = _model_json(path)
    updated["nodes"] = [n for n in updated["nodes"] if n["id"] != "docs"]
    updated["nodes"].append(
        {"id": "extra", "type": "log", "config": {"label": "added"}}
    )
    updated["layout"]["extra"] = [300.0, 0.0]

    after = _merged_text(path, updated)

    assert "Multi-line note." not in after  # docs node gone
    assert 'id: "extra"' in after
    assert "extra: [300, 0]" in after
    assert "# the entry node" in after  # comment on a surviving node


def test_edge_rewire_keeps_surviving_edge_objects() -> None:
    path = DATA_DIR / "commented_flow.yaml"
    before = emit_document(load_document(path))
    updated = _model_json(path)
    # replace greet.out→end.out with greet.error→end.out (E004 rewire)
    updated["edges"] = [e for e in updated["edges"] if e["from"] != "greet.out"] + [
        {"from": "greet.error", "to": "end.out"}
    ]

    after = _merged_text(path, updated)

    changed = _changed_lines(before, after)
    assert changed == [
        '-  - {from: "greet.out", to: "end.out"}',
        '+  - {from: "greet.error", to: "end.out"}',
    ]


def test_multiline_string_becomes_literal_block() -> None:
    path = DATA_DIR / "commented_flow.yaml"
    updated = _model_json(path)
    docs = next(n for n in updated["nodes"] if n["id"] == "docs")
    docs["config"]["text"] = "Edited note.\nStill a block.\n"

    after = _merged_text(path, updated)

    assert "text: |" in after
    assert "Edited note." in after
    assert "\\n" not in after  # never an escaped one-liner


def test_config_key_removal_and_type_change() -> None:
    path = DATA_DIR / "commented_flow.yaml"
    updated = _model_json(path)
    greet = next(n for n in updated["nodes"] if n["id"] == "greet")
    del greet["config"]["level"]

    after = _merged_text(path, updated)

    assert "level:" not in after
    assert 'label: "single quoted becomes double"' in after


def test_new_top_level_key_lands_above_layout() -> None:
    path = DATA_DIR / "commented_flow.yaml"
    updated = _model_json(path)
    updated["env"] = {"required": ["API_TOKEN"]}

    after = _merged_text(path, updated)

    assert after.index("env:") < after.index("layout:")
    assert '- "API_TOKEN"' in after


def test_bool_int_confusion_is_not_equality() -> None:
    path = DATA_DIR / "commented_flow.yaml"
    updated = _model_json(path)
    end = next(n for n in updated["nodes"] if n["id"] == "end")
    # required: false → stays false; flipping must actually rewrite
    end["config"]["ports"][0]["required"] = True

    after = _merged_text(path, updated)

    assert "required: true" in after
