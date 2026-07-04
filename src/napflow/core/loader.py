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
from collections.abc import Mapping, Sequence
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
