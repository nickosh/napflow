"""FR-208/EC29: loader keeps the CommentedMap as write source, models as
read-only views, and threads position marks into every diagnostic."""

from pathlib import Path

import pytest
from ruamel.yaml.comments import CommentedMap

from napflow.core.loader import (
    LoadError,
    load_document,
    load_flow,
    load_manifest,
    locate,
    save_document,
)

DATA_DIR = Path(__file__).resolve().parent / "data"


def test_load_flow_returns_doc_and_view() -> None:
    loaded = load_flow(DATA_DIR / "create_until_ready.yaml")
    assert isinstance(loaded.doc, CommentedMap)  # the write source
    assert loaded.doc["flow"]["name"] == "create_until_ready"
    assert loaded.model.flow.name == "create_until_ready"  # the view


def test_load_manifest_returns_doc_and_view() -> None:
    loaded = load_manifest(DATA_DIR / "napflow.yaml")
    assert isinstance(loaded.doc, CommentedMap)
    assert loaded.model.environments.default == "dev"


def test_parse_error_is_positioned(tmp_path: Path) -> None:
    bad = tmp_path / "flow.yaml"
    bad.write_text("nodes: [unclosed\n", encoding="utf-8")
    with pytest.raises(LoadError) as exc:
        load_flow(bad)
    (diag,) = exc.value.diagnostics
    assert diag.line is not None
    assert str(bad) in str(exc.value)


def test_duplicate_key_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "flow.yaml"
    bad.write_text(
        "schema: napflow/v1\nschema: napflow/v1\nflow: {name: t}\nnodes: []\n",
        encoding="utf-8",
    )
    with pytest.raises(LoadError, match="[Dd]uplicate"):
        load_flow(bad)


def test_validation_error_points_at_file_line(tmp_path: Path) -> None:
    bad = tmp_path / "flow.yaml"
    bad.write_text(
        "schema: napflow/v1\n"  # line 1
        "flow:\n"  #               line 2
        "  name: t\n"  #           line 3
        "nodes:\n"  #              line 4
        "  - id: a\n"  #           line 5
        "    type: request\n"  #   line 6
        "    config:\n"  #         line 7
        "      method: GET\n",  #  line 8 — url missing
        encoding="utf-8",
    )
    with pytest.raises(LoadError) as exc:
        load_flow(bad)
    (diag,) = exc.value.diagnostics
    assert "url" in ".".join(str(s) for s in diag.loc)
    assert diag.line == 7  # deepest resolvable segment: the config key
    assert f"{bad}:7" in str(exc.value)


def test_unknown_node_type_points_at_node(tmp_path: Path) -> None:
    bad = tmp_path / "flow.yaml"
    bad.write_text(
        "schema: napflow/v1\n"
        "flow:\n"
        "  name: t\n"
        "nodes:\n"
        "  - id: a\n"  # line 5
        "    type: webhook\n"
        "    config: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(LoadError) as exc:
        load_flow(bad)
    assert any(d.line == 5 for d in exc.value.diagnostics)


def test_locate_skips_union_tags() -> None:
    doc = load_document(DATA_DIR / "create_until_ready.yaml")
    # pydantic locs include the union tag ('request') — not a real key
    pos = locate(doc, ("nodes", 1, "request", "config", "url"))
    assert pos is not None
    line, _col = pos
    assert doc["nodes"][1]["id"] == "create"
    assert line > 1


def test_save_document_lf_utf8_trailing_newline(tmp_path: Path) -> None:
    doc = load_document(DATA_DIR / "create_until_ready.yaml")
    out = tmp_path / "out.yaml"
    save_document(doc, out)
    raw = out.read_bytes()
    assert b"\r" not in raw
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")
    assert raw.decode("utf-8")  # valid utf-8
