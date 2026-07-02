# napflow — Design Decision: YAML serialization (safe profile)

Targets: append the **D-entry** below to `docs/DECISIONS.md`; keep the
**implementation notes** next to the shared serializer (e.g.
`docs/yaml-profile.md`, or a header comment in the emitter module).

Numbering note: this assumes the 2026-06-14 review deltas (D18–D22, W106) were
adopted; otherwise renumber to the next free D/W codes.

---

## DECISIONS.md entry

### D23 — On-disk format is YAML, pinned to a safe, canonical profile

YAML stays the serialization format for flows and the manifest — but "raw" YAML
is not used. It is constrained to a fixed profile so the footguns that make YAML
dangerous cannot bite: implicit type coercion (the `no`/`on`/`off`/leading-zero/
`HH:MM` class — all common in HTTP headers, params, and bodies), indentation
ambiguity, anchors/aliases, and arbitrary-object loading.

The choice is driven by two constraints specific to napflow rather than by
format aesthetics: parsers must be mature in **both** Python (engine) and JS/TS
(canvas), and files are **machine-written first, hand-edited second**, so
deterministic, diff-clean output dominates. YAML is the only candidate that
clears both bars while keeping comments, readability, and JSON Schema
validation.

**The profile (all five are part of the decision):**
1. Read with a **safe loader only** — never a loader that can instantiate
   arbitrary objects.
2. Emit through **one shared canonical serializer** (no ad-hoc `yaml.dump` /
   `stringify` anywhere): block style only, no flow style, no anchors, 2-space
   indent, scalars never line-wrapped, LF + UTF-8 + single trailing newline.
3. **Force double-quoted style for string scalars only** — ints, floats, bools,
   and null stay bare so they keep their type. This one rule neutralizes the
   entire coercion class in generated files.
4. **Validate the parsed structure against JSON Schema** (the schema, not YAML
   inference, is the type authority). This is *why* force-quoting strings is
   safe: types are recovered from the schema on load.
5. One non-failing `napf check` lint (**W107**) for the residual footgun that
   only survives in hand-edited files (see notes).

**Rejected.**
- *HUML* — aimed squarely at this problem and worth revisiting, but it is
  v0.1.0/experimental with parsers only in Rust, Go, and OCaml (no production
  Python or native JS/TS). Betting a foundational, expensive-to-change layer on
  a pre-1.0 format with thin tooling is unjustified.
- *NestedText* — the best philosophical fit (no type coercion at all; the
  typed-port schema becomes the sole type authority) and the strongest *future*
  candidate, but its TS parser is not yet proven. Held as a possible spike, not
  a commitment.
- *JSON5 / JSONC* — viable machine format with trivial canonicalization, but
  weaker for hand-editing nested graphs. The natural fallback if telemetry ever
  shows hand-editing is rare.
- *TOML* — good for the manifest, poor for the flow graph (nested,
  heterogeneous arrays-of-tables get ugly). Splitting formats is its own cost.
- *KDL / StrictYAML* — node-tree-native and footgun-free respectively, but both
  lose dual-ecosystem parser + schema maturity. KDL also buys less than it looks
  like: a cyclic graph still encodes edges as ID references, not tree structure.

Meta-rationale: a serialization format is load-bearing and costly to migrate;
novelty is a cost here, not a feature. Boring-but-ubiquitous wins, and YAML's
danger is fully containable by construction.

**Consequences.**
- The canvas and the CLI must both emit through the shared serializer; a
  divergent emitter silently reintroduces noisy diffs.
- A **round-trip golden test** (`emit → parse → emit` is byte-identical, and
  `parse(emit(x))` deep-equals `x`) guards the clean-diff promise in CI.
- Revisit if HUML reaches ~1.0 with maintained Python + JS parsers, or if
  hand-editing proves rare enough that JSON5's simpler canonicalization wins.

---

## Implementation notes (serializer PR)

### Reading — both ecosystems
- **Python (engine/CLI):** `YAML(typ='safe')` for pure loads; ruamel round-trip
  mode only where preserving a human's comments on re-write matters. Never
  PyYAML `yaml.load` without `SafeLoader`.
- **JS/TS (canvas):** `js-yaml` v4 `load()` is safe by default (the unsafe
  loader was removed in v4) — fine as-is.

### Canonical emit — Python, ruamel.yaml
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

### Canonical emit — canvas, js-yaml
```js
import yaml from 'js-yaml';

const text = yaml.dump(flow, {
  forceQuotes: true,   // quotes strings only; numbers/bools stay bare
  quotingType: '"',
  noRefs: true,        // never emit anchors/aliases
  lineWidth: -1,       // never wrap scalars
  indent: 2,
  sortKeys: false,     // preserve the canvas's fixed key order (see below)
});
```
Alternative (eemeli `yaml`): `stringify(flow, { defaultStringType: 'QUOTE_DOUBLE',
defaultKeyType: 'PLAIN', lineWidth: 0, aliasDuplicateObjects: false })`.

### Key order & determinism
Determinism comes from the *emitter*, so pick one and apply it identically on
both sides:
- **Preferred:** the canvas always builds node/edge objects in a fixed
  schema-defined field order (`id`, `type`, `config`, …; edges `from`, `to`) and
  the serializer preserves it (`sortKeys: false`). Readable *and* stable.
- **Fallback:** `sortKeys: true` (alphabetical) — guaranteed deterministic but
  less readable (`body` before `headers` before `method`).

Avoid alphabetizing if you can help it; schema order reads far better in review.

### Validation
Parse, then validate the resulting structure against JSON Schema (Draft
2020-12): `jsonschema` in Python, `ajv` in JS. The schema carries the port/config
types; this is the half that makes the force-quote rule safe.

### Lint — W107 (hand-edited files only)
Warn on any **unquoted** scalar that both (a) lands in a field the schema types
as `string` and (b) matches YAML's implicit-resolver danger set:
- booleans: `y|Y|yes|Yes|YES|n|N|no|No|NO|true|True|false|False|on|On|ON|off|Off|OFF`
- null: `~|null|Null|NULL` or empty
- numbers: leading-zero ints, `0x…`, `0o…`, floats, sexagesimal `\d+:\d+(:\d+)?`
- date/time-ish ISO tokens

Because the canvas force-quotes everything, this can only ever fire on
hand-edited files — exactly the gap. Non-failing (W-class).

### Round-trip golden test (CI)
For a corpus of representative flows, assert:
1. `emit(parse(emit(flow)))` is **byte-identical** to `emit(flow)`.
2. `parse(emit(flow))` deep-equals `flow`.

Wire both into CI so the emitter config cannot drift and quietly bring back noisy
diffs. Add `*.yaml text eol=lf` (and `*.yml`) to `.gitattributes` so line
endings stay clean cross-platform.
