# napflow — Canonical YAML Profile (implementation notes)

Status: adopted 2026-07-02. Rationale and rejected alternatives: D23 in
`DECISIONS.md`. These notes live next to the shared serializer; the
emitter module should reference this file.

YAML is the on-disk format for flows and the manifest, but "raw" YAML is
not used — it is pinned to a fixed profile so YAML's footguns (implicit
type coercion, indentation ambiguity, anchors/aliases, arbitrary-object
loading) cannot bite. Five rules, all load-bearing:

1. Read with the configured ruamel **round-trip loader only**. Its
   `RoundTripConstructor` preserves comments, author order, and source
   positions without enabling arbitrary-object construction; a custom
   composer rejects anchors and aliases before construction. Never use the
   unsafe/full Python-object loader.
2. Emit through **the one shared canonical serializer** — no ad-hoc
   `yaml.dump`/`stringify` anywhere. Since S4/M4 there is exactly one
   emitter, Python-side (`core/loader.py`): the canvas never touches
   YAML — it PUTs model JSON and the server merges + emits (FR-1003).
3. **Force double-quoted style for non-block string values only** — mapping
   keys stay plain, literal/folded multiline values keep `|`/`>`, and ints,
   floats, bools, null stay bare so they keep their type.
4. **Validate the parsed structure against JSON Schema** (Draft 2020-12) —
   the schema, not YAML inference, is the type authority. This is *why*
   force-quoting strings is safe: types are recovered from the schema on
   load.
5. One non-failing `napf check` lint (**W107**) for the residual footgun
   that only survives in hand-edited files (below).

Note: YAML examples inside the docs are hand-written for readability and do
not consistently show value quoting. Files *emitted by napflow* double-quote
non-block string values; keys remain plain and block scalars remain blocks.

## Reading

- **Python (engine/CLI/server):** one configured `YAML(typ="rt")` instance
  (the `YAML()` default) handles every production load and emit. The
  round-trip constructor retains comments/order/line marks and does not
  construct arbitrary Python objects. Its composer rejects every anchor or
  alias event with a positioned `LoadError`; quoted `&`/`*` characters remain
  ordinary string data.
- **JS/TS (canvas):** does not parse YAML at all — the flow-detail API
  serves the validated model as JSON. (If a client-side YAML read ever
  becomes necessary: `js-yaml` v4 `load()` is safe by default.)

## Canonical emit — Python, ruamel.yaml

```python
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import (
    DoubleQuotedScalarString,
    FoldedScalarString,
    LiteralScalarString,
)

yaml = YAML(typ="rt")               # comments, author order, source positions
yaml.default_flow_style = False     # block style only
yaml.width = 1_000_000              # never wrap scalars (long URLs/bodies stay one line)
yaml.indent(mapping=2, sequence=4, offset=2)
yaml.allow_unicode = True

# Canonicalization walks mapping VALUES and sequence items; mapping keys are
# deliberately untouched. Do not use yaml.default_style='"' — that would also
# quote keys and non-string values.
def _canonical_scalar(value):
    if isinstance(value, (LiteralScalarString, FoldedScalarString)):
        return value
    if isinstance(value, str):
        return DoubleQuotedScalarString(value)
    return value
```

Exceptions to block-only style (pinned at M2, 2026-07-04 — both for
diff readability): **edges are one-line inline maps**
(`- {from: "a.out", to: "b.in"}`) and **layout coordinate pairs are
one-line inline sequences** (`start: [40, 200]`); the serializer
special-cases exactly those two spots. Additionally, literal/folded
block scalars (`|`/`>`) are preserved as-is rather than force-quoted —
multiline text carries none of the implicit-coercion risk the quoting
rule exists to prevent. Python implementation: `core/loader.py`
(`emit_document` / `save_document` — the one shared serializer).

Before dumping, `emit_document` rejects explicit anchor metadata and repeated
identity for any object ruamel could alias (including cycles), because ruamel
would otherwise synthesize an anchor/alias pair. The canonical emitter
therefore cannot produce `&name` or `*name` references.

## Canvas emit — there is none (amended at S4/M4, 2026-07-07)

The originally planned client-side js-yaml emitter was never built and
is now ruled out by design: canvas saves go through
`PUT /api/flows/*` as model JSON, and the server applies them via
`loader.merge_flow_document` + `save_document` — the same serializer as
the CLI, by construction rather than by keeping two emitters in sync
(FR-1003). The UI has no YAML dependency.

## Write path — the document is the single write source (EC29)

Round-trip preservation is an *architecture* rule, not a serializer
flag: the loaded ruamel document (`CommentedMap`) is the only thing
ever written back to disk. Edits — canvas or CLI — mutate the loaded
document surgically; the Pydantic models are validated **read-only
views** for the checker/engine and are never serialized back (dumping a
model would silently delete every comment). The loader also retains
ruamel's line/column marks through validation so every `napf check`
diagnostic points at file:line (engine spec §8).

As of v0.2/M1 (2026-07-12), `save_document` emits to a same-directory
temporary file as UTF-8/LF, flushes and `fsync`s it, preserves existing
permission bits, then atomically replaces the live path and cleans up on
failure. The canvas, CLI/scaffold, and cloned flow sources all pass through
this primitive; JSONL histories remain append-only streams and deliberately
do not. This durability layer changes no serializer bytes or key ordering.

## Key order & determinism

Determinism does not come from sorting. The round-trip document keeps the
author's order, canonicalization changes styles without reordering, and the
emitter writes that order. `merge_flow_document` mutates surviving mappings
in place, so existing keys retain their authored positions. New mapping keys
and new node/edge objects use the validated incoming payload's order; new
top-level keys are inserted immediately above `layout:` so layout remains at
the bottom. The nodes/edges sequence order follows the incoming updated list.
Readable *and* stable.

File conventions: LF + UTF-8 + single trailing newline; `layout:`
quarantined at the bottom of the file.

## Validation

Parse, then validate the resulting structure against JSON Schema
(Draft 2020-12): Pydantic in Python (amended at M2: the models ARE the
schema — the exported document is generated from them, so a separate
`jsonschema` pass could never catch more). Canvas writes are validated
by the same Pydantic models server-side before the merge
(`validate_flow_payload`) — no JS-side schema validation needed. The
schema carries the port/config types — the half that makes the
force-quote rule safe.

## Lint — W107 (hand-edited files only)

Warn on any **unquoted** scalar that both (a) lands in a field the schema
types as `string` and (b) matches YAML's implicit-resolver danger set:

- booleans: `y|Y|yes|Yes|YES|n|N|no|No|NO|true|True|false|False|on|On|ON|off|Off|OFF`
- null: `~|null|Null|NULL` or empty
- numbers: leading-zero ints, `0x…`, `0o…`, floats, sexagesimal `\d+:\d+(:\d+)?`
- date/time-ish ISO tokens

Because every napflow-written file goes through the force-quoting
serializer, this can only ever fire on hand-edited files — exactly the
gap. Non-failing (W-class).

## Round-trip golden test (CI)

For a corpus of representative flows, assert:
1. `emit(parse(emit(flow)))` is **byte-identical** to `emit(flow)`.
2. `parse(emit(flow))` deep-equals `flow`.

Wire both into CI so the emitter config cannot drift and quietly bring
back noisy diffs. `napf init` writes `*.yaml text eol=lf` (and `*.yml`)
into `.gitattributes` so line endings stay clean cross-platform.

## Consequences

- Every write — canvas or CLI — must reach disk through the one shared
  Python serializer; a divergent emitter silently reintroduces noisy
  diffs. (Since S4/M4 this is structural: the UI cannot emit YAML.)
- Revisit the format choice if HUML reaches ~1.0 with maintained
  Python + JS parsers, or if hand-editing proves rare enough that JSON5's
  simpler canonicalization wins (D23).
