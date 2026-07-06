"""Loader + write path: yaml → pydantic models, and the ONE canonical
serializer (FR-204/205/208, D23 — see docs/yaml-profile.md).

Architecture (EC29): the loaded ruamel document (CommentedMap) is the
single write source — edits mutate it surgically and only it is emitted
back to disk. The Pydantic models are validated read-only views and are
never serialized back (dumping a model would delete every comment).
ruamel's line/column marks stay on the document, so every diagnostic can
point at file:line via `locate()`.

Validation: Pydantic is the Python-side JSON Schema Draft 2020-12
authority (FR-206) — the exported schema (`core.models`) is generated
from the same models ajv consumes on the canvas side; a second
`jsonschema` pass could never catch more.

Canonical emit profile (D23): block style; string VALUES double-quoted,
keys plain; ints/floats/bools/null bare (explicit `null`); no wrapping;
LF + UTF-8 + single trailing newline. Flow-style islands: `edges` items
and `layout` coordinate pairs are one-line inline. Literal/folded block
scalars are preserved as-is — multiline text carries no implicit-type
risk, which is what force-quoting exists to prevent.
"""

import io
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.constructor import DuplicateKeyError
from ruamel.yaml.error import MarkedYAMLError, YAMLError
from ruamel.yaml.scalarstring import (
    DoubleQuotedScalarString,
    FoldedScalarString,
    LiteralScalarString,
)

from napflow.core.models import FlowFile, Manifest

# --------------------------------------------------------------------------
# Diagnostics


@dataclass(frozen=True)
class LoadDiagnostic:
    """One problem, positioned. `loc` is the pydantic-style path into the
    document; line/column are 1-based, best-effort (None when unknown)."""

    message: str
    loc: tuple[int | str, ...] = ()
    line: int | None = None
    column: int | None = None
    # pydantic error type ("extra_forbidden", "union_tag_invalid", ...) or
    # "parse" for YAML-level failures — lets the checker map to E-codes
    kind: str | None = None

    def render(self, path: Path) -> str:
        pos = f":{self.line}" if self.line is not None else ""
        pos += f":{self.column}" if self.line is not None and self.column else ""
        where = ".".join(str(s) for s in self.loc)
        prefix = f"{path}{pos}: "
        return (
            f"{prefix}{where}: {self.message}" if where else f"{prefix}{self.message}"
        )


class LoadError(Exception):
    """YAML parse failure or model validation failure (the E001/E002
    surface — the checker maps diagnostics to codes)."""

    def __init__(self, path: Path, diagnostics: list[LoadDiagnostic]):
        self.path = path
        self.diagnostics = diagnostics
        super().__init__("\n".join(d.render(path) for d in diagnostics))


def locate(doc: Any, loc: Sequence[int | str]) -> tuple[int, int] | None:
    """Best-effort (line, column), 1-based, for a pydantic error `loc`
    inside a round-trip-loaded document.

    Walks the document by keys/indices; segments that don't resolve
    (discriminated-union tags, missing keys) are skipped, keeping the
    deepest position found so far.
    """
    best: tuple[int, int] | None = None
    node = doc
    if hasattr(node, "lc"):
        best = (node.lc.line + 1, node.lc.col + 1)
    for seg in loc:
        if isinstance(node, Mapping) and seg in node:
            if hasattr(node, "lc"):
                try:
                    line, col = node.lc.key(seg)
                    best = (line + 1, col + 1)
                except (KeyError, AttributeError, TypeError):
                    pass
            node = node[seg]
        elif (
            isinstance(seg, int)
            and isinstance(node, Sequence)
            and not isinstance(node, str)
            and 0 <= seg < len(node)
        ):
            if hasattr(node, "lc"):
                try:
                    line, col = node.lc.item(seg)
                    best = (line + 1, col + 1)
                except (KeyError, AttributeError, TypeError):
                    pass
            node = node[seg]
        # else: union tag or absent key — keep the parent position
    return best


# --------------------------------------------------------------------------
# Reading

_yaml = YAML()  # round-trip: comments, key order, positions (FR-205/208)
_yaml.preserve_quotes = True
_yaml.default_flow_style = False
_yaml.width = 1_000_000  # never wrap scalars (long URLs stay one line)
_yaml.indent(mapping=2, sequence=4, offset=2)
_yaml.allow_unicode = True


def _represent_none(representer: Any, _data: None) -> Any:
    return representer.represent_scalar("tag:yaml.org,2002:null", "null")


_yaml.representer.add_representer(type(None), _represent_none)


def load_document(path: Path) -> Any:
    """Read one YAML document, round-trip mode (safe: no arbitrary object
    construction). Parse failures raise LoadError with file:line."""
    try:
        with path.open(encoding="utf-8") as f:
            return _yaml.load(f)
    except (YAMLError, DuplicateKeyError) as e:
        line = col = None
        if isinstance(e, MarkedYAMLError) or hasattr(e, "problem_mark"):
            mark = getattr(e, "problem_mark", None)
            if mark is not None:
                line, col = mark.line + 1, mark.column + 1
        message = " ".join(str(e).split())  # ruamel messages are multi-line
        raise LoadError(
            path, [LoadDiagnostic(message=message, line=line, column=col, kind="parse")]
        ) from e


def _validate[M: BaseModel](doc: Any, model_cls: type[M], path: Path) -> M:
    try:
        return model_cls.model_validate(doc)
    except ValidationError as e:
        diagnostics = []
        for err in e.errors(include_url=False):
            loc = tuple(err["loc"])
            pos = locate(doc, loc)
            diagnostics.append(
                LoadDiagnostic(
                    message=err["msg"],
                    loc=loc,
                    line=pos[0] if pos else None,
                    column=pos[1] if pos else None,
                    kind=err["type"],
                )
            )
        raise LoadError(path, diagnostics) from e


@dataclass(frozen=True)
class LoadedFlow:
    """A flow.yaml: `doc` is the write source, `model` the read-only view."""

    path: Path
    doc: Any = field(repr=False)
    model: FlowFile


@dataclass(frozen=True)
class LoadedManifest:
    """A napflow.yaml: `doc` is the write source, `model` the view."""

    path: Path
    doc: Any = field(repr=False)
    model: Manifest


def load_flow(path: Path) -> LoadedFlow:
    doc = load_document(path)
    return LoadedFlow(path=path, doc=doc, model=_validate(doc, FlowFile, path))


def validate_flow_payload(payload: Any, path: Path) -> FlowFile:
    """Validate a canvas PUT payload as a FlowFile — the write-path gate
    (FR-1003). Raises LoadError with pydantic diagnostics; positions are
    absent (the payload is JSON, not the file at `path`)."""
    return _validate(payload, FlowFile, path)


def load_manifest(path: Path) -> LoadedManifest:
    doc = load_document(path)
    return LoadedManifest(path=path, doc=doc, model=_validate(doc, Manifest, path))


# --------------------------------------------------------------------------
# Canonical emit (the ONE serializer — FR-204)


def _canonical_scalar(value: Any) -> Any:
    if isinstance(value, LiteralScalarString | FoldedScalarString):
        return value  # multiline blocks stay readable; no coercion risk
    if isinstance(value, str):
        return DoubleQuotedScalarString(value)
    return value  # ints/floats/bools/null/dates stay bare


def _canonicalize(node: Any) -> Any:
    """Normalize styles in place: block everywhere, string values
    double-quoted. Returns the (possibly replaced) node."""
    if isinstance(node, Mapping):
        if not isinstance(node, CommentedMap):
            node = CommentedMap(node)
        node.fa.set_block_style()
        for key in list(node):
            node[key] = _canonicalize(node[key])
        return node
    if isinstance(node, Sequence) and not isinstance(node, str | bytes):
        if not isinstance(node, CommentedSeq):
            node = CommentedSeq(node)
        node.fa.set_block_style()
        for i, item in enumerate(node):
            node[i] = _canonicalize(item)
        return node
    return _canonical_scalar(node)


def _apply_flow_islands(doc: Any) -> None:
    """`edges` items and `layout` coordinate pairs are the only one-line
    inline (flow-style) structures — a diff-readability exception (D23)."""
    if not isinstance(doc, Mapping):
        return
    edges = doc.get("edges")
    if isinstance(edges, CommentedSeq):
        for item in edges:
            if isinstance(item, CommentedMap):
                item.fa.set_flow_style()
    layout = doc.get("layout")
    if isinstance(layout, CommentedMap):
        for pair in layout.values():
            if isinstance(pair, CommentedSeq):
                pair.fa.set_flow_style()


def emit_document(doc: Any) -> str:
    """Serialize through the canonical profile. Normalizes the document's
    styles in place (the canonical style IS the document's state);
    comments and key order are preserved (FR-205)."""
    doc = _canonicalize(doc)
    _apply_flow_islands(doc)
    buf = io.StringIO()
    _yaml.dump(doc, buf)
    return buf.getvalue()


def save_document(doc: Any, path: Path) -> None:
    """Write the document to disk: UTF-8, LF, single trailing newline."""
    text = emit_document(doc)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)


# --------------------------------------------------------------------------
# Surgical merge (canvas write path — FR-1003, EC29)


def _scalar_equal(old: Any, new: Any) -> bool:
    # Python's `True == 1` would silently rewrite ints as bools (and
    # vice versa) — in YAML those are different values
    if isinstance(old, bool) != isinstance(new, bool):
        return False
    return bool(old == new)


def _incoming_value(value: Any) -> Any:
    """Normalize freshly-arriving JSON values recursively."""
    if isinstance(value, Mapping):
        return {k: _incoming_value(v) for k, v in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [_incoming_value(v) for v in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)  # JSON floats: layout stays `[40, 200]`, never 40.0
    if isinstance(value, str) and "\n" in value:
        return LiteralScalarString(value)  # multiline stays a readable block
    return value


def _merged_value(old: Any, new: Any) -> Any:
    """Merge `new` into `old`, keeping `old`'s objects (comments, styles)
    wherever the value is unchanged. Returns the value to store."""
    if isinstance(old, MutableMapping) and isinstance(new, Mapping):
        _merge_mapping(old, new)
        return old
    if (
        isinstance(old, MutableSequence)
        and not isinstance(old, str | bytes)
        and isinstance(new, Sequence)
        and not isinstance(new, str | bytes)
    ):
        _merge_sequence(old, new)
        return old
    if not isinstance(new, Mapping | list) and _scalar_equal(old, new):
        return old
    return _incoming_value(new)


def _merge_mapping(old: MutableMapping, new: Mapping) -> None:
    for key in [k for k in old if k not in new]:
        del old[key]
    for key, value in new.items():
        if key in old:
            merged = _merged_value(old[key], value)
            if merged is not old[key]:
                old[key] = merged
        else:
            old[key] = _incoming_value(value)


def _merge_sequence(old: MutableSequence, new: Sequence) -> None:
    for i in range(min(len(old), len(new))):
        merged = _merged_value(old[i], new[i])
        if merged is not old[i]:
            old[i] = merged
    while len(old) > len(new):
        del old[-1]
    for item in new[len(old) :]:
        old.append(_incoming_value(item))


def _merge_keyed_seq(seq: Any, new_items: Sequence[Any], key_of: Any) -> Any:
    """Merge a list whose items have identity (nodes by `id`, edges by
    `(from, to)`). Surviving items keep their ruamel objects; the original
    CommentedSeq is mutated in place — seq-level comments (the blank line
    before the next section lives in `ca.end`) die with the object."""
    if not isinstance(seq, MutableSequence) or isinstance(seq, str | bytes):
        return list(new_items)
    old_by_key: dict[Any, Any] = {}
    for item in seq:
        if isinstance(item, MutableMapping):
            key = key_of(item)
            if key is not None and key not in old_by_key:
                old_by_key[key] = item
    merged: list[Any] = []
    for new_item in new_items:
        key = key_of(new_item) if isinstance(new_item, Mapping) else None
        old_item = old_by_key.pop(key, None) if key is not None else None
        if old_item is not None:
            _merge_mapping(old_item, new_item)
            merged.append(old_item)
        else:
            merged.append(_incoming_value(new_item))
    same = len(merged) == len(seq) and all(
        a is b for a, b in zip(merged, seq, strict=True)
    )
    if not same:
        while len(seq):
            del seq[-1]
        seq.extend(merged)
    return seq


def _edge_key(edge: Mapping) -> Any:
    return (edge.get("from"), edge.get("to"))


def _node_key(node: Mapping) -> Any:
    return node.get("id")


def merge_flow_document(doc: Any, updated: Mapping[str, Any]) -> None:
    """Mutate a round-trip-loaded flow document in place so it carries
    `updated` — a validated FlowFile dump (``mode="json"``,
    ``by_alias=True``, ``exclude_unset=True`` — exclude_unset keeps
    model defaults OUT of the file; materializing them would bloat every
    diff).

    This is the canvas write path (FR-1003): the UI never emits YAML —
    it PUTs the model JSON, the server merges it here and emits through
    the one canonical serializer (D23). Nodes match by ``id``, edges by
    ``(from, to)``; unchanged values keep their ruamel objects, so an
    untouched file re-emits byte-identical and a layout-only move diffs
    only the ``layout:`` block. Comment preservation is best-effort under
    restructuring: comments anchored to deleted items go with them.
    """
    if not isinstance(doc, MutableMapping):
        raise TypeError("flow document is not a mapping — reload before merging")
    for key in [k for k in doc if k not in updated]:
        del doc[key]
    for key, value in updated.items():
        if key == "nodes":
            merged = _merge_keyed_seq(doc.get(key), value, _node_key)
        elif key == "edges":
            merged = _merge_keyed_seq(doc.get(key), value, _edge_key)
        elif key in doc:
            merged = _merged_value(doc[key], value)
        else:
            merged = _incoming_value(value)
        if key in doc:
            if merged is not doc[key]:
                doc[key] = merged
        elif key != "layout" and isinstance(doc, CommentedMap) and "layout" in doc:
            # layout: stays quarantined at the file bottom (FR-203)
            doc.insert(list(doc).index("layout"), key, merged)
        else:
            doc[key] = merged
