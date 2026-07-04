# napflow — Canonical YAML Profile (implementation notes)

Status: adopted 2026-07-02. Rationale and rejected alternatives: D23 in
`DECISIONS.md`. These notes live next to the shared serializer; the
emitter module should reference this file.

YAML is the on-disk format for flows and the manifest, but "raw" YAML is
not used — it is pinned to a fixed profile so YAML's footguns (implicit
type coercion, indentation ambiguity, anchors/aliases, arbitrary-object
loading) cannot bite. Five rules, all load-bearing:

1. Read with a **safe loader only**.
2. Emit through **one shared canonical serializer** per ecosystem — no
   ad-hoc `yaml.dump`/`stringify` anywhere.
3. **Force double-quoted style for string scalars only** — ints, floats,
   bools, null stay bare so they keep their type.
4. **Validate the parsed structure against JSON Schema** (Draft 2020-12) —
   the schema, not YAML inference, is the type authority. This is *why*
   force-quoting strings is safe: types are recovered from the schema on
   load.
5. One non-failing `napf check` lint (**W107**) for the residual footgun
   that only survives in hand-edited files (below).

Note: YAML examples inside the docs are hand-written for readability and
do not show the force-quoted style; files *emitted by napflow* always do.

## Reading — both ecosystems

- **Python (engine/CLI):** `YAML(typ='safe')` for pure loads; ruamel
  round-trip mode only where preserving a human's comments on re-write
  matters. Never PyYAML `yaml.load` without `SafeLoader`.
- **JS/TS (canvas):** `js-yaml` v4 `load()` is safe by default (the unsafe
  loader was removed in v4) — fine as-is.

## Canonical emit — Python, ruamel.yaml

```python
from ruamel.yaml import YAML

yaml = YAML()                       # round-trip: preserves comments on edited files
yaml.default_flow_style = False     # block style only
yaml.width = 1_000_000              # never wrap scalars (long URLs/bodies stay one line)
yaml.indent(mapping=2, sequence=4, offset=2)
yaml.allow_unicode = True

# Force double-quoted style for STRINGS ONLY. Do NOT use yaml.default_style='"' —
# that quotes ints/bools too (port: "8080"), corrupting their type on reload.
def _quote_str(rep, data):
    return rep.represent_scalar("tag:yaml.org,2002:str", data, style='"')
yaml.representer.add_representer(str, _quote_str)
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

## Canonical emit — canvas, js-yaml

```js
import yaml from 'js-yaml';

const text = yaml.dump(flow, {
  forceQuotes: true,   // quotes strings only; numbers/bools stay bare
  quotingType: '"',
  noRefs: true,        // never emit anchors/aliases
  lineWidth: -1,       // never wrap scalars
  indent: 2,
  sortKeys: false,     // preserve the fixed schema key order (below)
});
```

Alternative (eemeli `yaml`): `stringify(flow, { defaultStringType:
'QUOTE_DOUBLE', defaultKeyType: 'PLAIN', lineWidth: 0,
aliasDuplicateObjects: false })`.

## Write path — the document is the single write source (EC29)

Round-trip preservation is an *architecture* rule, not a serializer
flag: the loaded ruamel document (`CommentedMap`) is the only thing
ever written back to disk. Edits — canvas or CLI — mutate the loaded
document surgically; the Pydantic models are validated **read-only
views** for the checker/engine and are never serialized back (dumping a
model would silently delete every comment). The loader also retains
ruamel's line/column marks through validation so every `napf check`
diagnostic points at file:line (engine spec §8).

## Key order & determinism

Determinism comes from the *emitter*, applied identically on both sides:
the canvas and the loader always build node/edge objects in a fixed
schema-defined field order (`id`, `type`, `config`, …; edges `from`,
`to`) and the serializer preserves it (`sortKeys: false`). Readable *and*
stable. (Fallback if this ever proves fragile: `sortKeys: true` —
deterministic but reads worse in review.)

File conventions: LF + UTF-8 + single trailing newline; `layout:`
quarantined at the bottom of the file.

## Validation

Parse, then validate the resulting structure against JSON Schema
(Draft 2020-12): Pydantic in Python (amended at M2: the models ARE the
schema — the exported document is generated from them, so a separate
`jsonschema` pass could never catch more), `ajv` against the exported
schema in JS. The schema carries the port/config types — the half that
makes the force-quote rule safe.

## Lint — W107 (hand-edited files only)

Warn on any **unquoted** scalar that both (a) lands in a field the schema
types as `string` and (b) matches YAML's implicit-resolver danger set:

- booleans: `y|Y|yes|Yes|YES|n|N|no|No|NO|true|True|false|False|on|On|ON|off|Off|OFF`
- null: `~|null|Null|NULL` or empty
- numbers: leading-zero ints, `0x…`, `0o…`, floats, sexagesimal `\d+:\d+(:\d+)?`
- date/time-ish ISO tokens

Because the canvas force-quotes everything, this can only ever fire on
hand-edited files — exactly the gap. Non-failing (W-class).

## Round-trip golden test (CI)

For a corpus of representative flows, assert:
1. `emit(parse(emit(flow)))` is **byte-identical** to `emit(flow)`.
2. `parse(emit(flow))` deep-equals `flow`.

Wire both into CI so the emitter config cannot drift and quietly bring
back noisy diffs. `napf init` writes `*.yaml text eol=lf` (and `*.yml`)
into `.gitattributes` so line endings stay clean cross-platform.

## Consequences

- The canvas and the CLI must both emit through the shared serializer; a
  divergent emitter silently reintroduces noisy diffs.
- Revisit the format choice if HUML reaches ~1.0 with maintained
  Python + JS parsers, or if hand-editing proves rare enough that JSON5's
  simpler canonicalization wins (D23).
