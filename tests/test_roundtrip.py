"""TR-7 / FR-205: the golden round-trip corpus.

For every corpus file: emit(parse(emit(x))) is byte-identical to
emit(parse(x)), and parse(emit(x)) deep-equals parse(x). Plus shape
checks that pin the canonical profile (D23, docs/yaml-profile.md).
"""

from datetime import date
from pathlib import Path

import pytest
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from napflow.core.loader import emit_document, load_document

DATA_DIR = Path(__file__).resolve().parent / "data"

CORPUS = ["create_until_ready.yaml", "commented_flow.yaml", "napflow.yaml"]


def _reemit(text: str, tmp_path: Path) -> str:
    scratch = tmp_path / "scratch.yaml"
    scratch.write_text(text, encoding="utf-8", newline="\n")
    return emit_document(load_document(scratch))


@pytest.mark.parametrize("name", CORPUS)
def test_roundtrip_idempotent(name: str, tmp_path: Path) -> None:
    first = emit_document(load_document(DATA_DIR / name))
    second = _reemit(first, tmp_path)
    assert second == first  # byte-identical after one canonicalization


@pytest.mark.parametrize("name", CORPUS)
def test_roundtrip_preserves_data(name: str, tmp_path: Path) -> None:
    reference = load_document(DATA_DIR / name)  # fresh, unmutated parse
    canonical = emit_document(load_document(DATA_DIR / name))
    scratch = tmp_path / "scratch.yaml"
    scratch.write_text(canonical, encoding="utf-8", newline="\n")
    assert load_document(scratch) == reference  # equality ignores styles


def test_canonical_golden() -> None:
    """The exact canonical bytes are pinned — emitter drift shows up as
    a reviewable diff on this file (requires *.yaml eol=lf in
    .gitattributes to hold byte-identity on Windows checkouts)."""
    emitted = emit_document(load_document(DATA_DIR / "create_until_ready.yaml"))
    golden = DATA_DIR / "golden" / "create_until_ready.canonical.yaml"
    assert emitted == golden.read_bytes().decode("utf-8")


def test_canonical_profile_shape() -> None:
    text = emit_document(load_document(DATA_DIR / "create_until_ready.yaml"))

    assert text.endswith("\n") and not text.endswith("\n\n")
    assert "\r" not in text
    assert 'method: "POST"' in text  # string values double-quoted
    assert "count: 10" in text  # ints bare
    assert "required: false" in text  # bools bare
    assert "start: [40, 200]" in text  # layout pairs stay one-line


def test_comments_and_blocks_survive() -> None:
    text = emit_document(load_document(DATA_DIR / "commented_flow.yaml"))

    assert "# top-of-file comment — must survive round-trip" in text
    assert "# trailing comment survives" in text
    assert "# the entry node" in text
    assert "text: |" in text  # literal block preserved
    assert 'label: "single quoted becomes double"' in text
    # hand-written flow-style config is normalized to block style
    assert "config: {ports:" not in text


def test_null_emitted_bare() -> None:
    text = emit_document(load_document(DATA_DIR / "napflow.yaml"))
    assert "interpreter: null" in text
    assert "run_timeout_s: null" in text


@pytest.mark.parametrize(
    "shared",
    [CommentedSeq(["one"]), date(2026, 7, 14)],
    ids=["container", "yaml_native_scalar"],
)
def test_canonical_emit_rejects_shared_objects_that_would_create_aliases(
    shared: object,
) -> None:
    doc = CommentedMap({"first": shared, "second": shared})

    with pytest.raises(ValueError, match="anchors and aliases are not supported"):
        emit_document(doc)


def test_canonical_emit_rejects_explicit_anchor_metadata() -> None:
    anchored = CommentedSeq(["one"])
    anchored.yaml_set_anchor("shared", always_dump=True)

    with pytest.raises(ValueError, match="anchors and aliases are not supported"):
        emit_document(CommentedMap({"value": anchored}))
