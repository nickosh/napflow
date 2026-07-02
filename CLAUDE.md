# CLAUDE.md — napflow

## What this project is

**napflow** — local-first, git-friendly, node-based flow editor and engine
for complex API request/response processing. Think "Postman Flows, but
open, file-based, Python-powered, and composable." Built by a solo QA
Automation Engineer; primary audience is QA/dev teams testing APIs.

Core promises (never compromise these):
1. **Git-friendly**: a flow is one YAML file + one nodes.py in one folder;
   diffs are small, reviewable, layout never pollutes logic diffs.
2. **Composable**: any flow is usable as a node inside another flow
   (reference, not copy). Every canvas is a flow — even the default one.
3. **Python-native**: response parsing/logic in real Python functions,
   testable with pytest; engine importable (`from napflow.core import run_flow`).
4. **CI-first**: headless `napf run` with assert-driven exit codes is a
   first-class citizen, not an afterthought.
5. **Full observability**: complete request/response detail (headers,
   bodies, timing, retries) always captured in run history.
6. (Future, design-constrained today): generate standalone Python code
   (niquests clients, Pydantic models) from flows. One-directional:
   flows → code, never code → flows.

## Authoritative specs — read before changing behavior

- `docs/napflow-flow-schema.md`     — flow.yaml format, node catalog, rules
- `docs/napflow-workspace-manifest.md` — napflow.yaml, CLI surface, env model
- `docs/napflow-engine-spec.md`     — scheduler, frames, firing rules, events
- `docs/yaml-profile.md`            — canonical YAML read/emit profile (D23)
- `docs/DECISIONS.md`               — why each major decision was made (D01–D25)
- `docs/EDGE_CASES.md`              — resolution ledger; append new cases here
- `docs/PRODUCT.md` / `docs/REQUIREMENTS.md` — vision/scope; FR/NFR checklist
  (tick requirements in the PR that lands them, with a test)

Specs are hypotheses: when implementation proves one wrong, UPDATE THE SPEC
in the same PR as the code change.

## Architecture (dependency direction: down only)

```
cli/      typer  ──┐
server/ blacksheep ┼──▶  core/   ◀── NEVER imports cli/server/UI anything
ui/       react   ─┘     loader.py / checker.py / templating.py /
(built → wheel)          engine.py / events.py / nodes/
```

- `core` must stay importable standalone — it is the pytest/CI/codegen surface.
- Distribution: single pip-installable wheel with pre-built UI inside;
  `uv tool install napflow`; `napf ui` serves everything on localhost.
  No Docker, no Node at runtime.

## Tech stack (and the one-line why)

- Python 3.12+, **Pydantic v2** (schema = models), **ruamel.yaml**
  (round-trip, comment-preserving), **Jinja2 SandboxedEnvironment**
  (the ONLY expression/template language — no JMESPath, no eval),
  **niquests** AsyncSession (requests-compatible, HTTP/1.1/2/3, multiplex),
  **BlackSheep** + uvicorn (Pydantic-friendly ASGI; thin adapter only),
  **Typer** CLI. Frontend: React + TS + **@xyflow/react** + Zustand +
  Monaco + Vite (dev-time only; ships as static files).
- Platforms: macOS + Windows from day one → pathlib everywhere, no
  shell-isms, subprocess spawn semantics, CREATE_NO_WINDOW.
- License: Apache-2.0 + NOTICE file (NOTICE is the attribution lever).
  No CLA (Apache §5 inbound=outbound suffices); DCO later if external
  contributors appear.

## Execution model — the invariants

- **Message-driven, not DAG**: nodes fire on message arrival; edge cycles
  are LEGAL if guarded (counter/timeout in every cycle — checked, W101).
  Flow REFERENCES are a strict DAG (no recursive flows — E007).
- **Single edge per input port, no exceptions** (E004). Outputs fan out
  freely. Joining paths = merge node only (`any | all | collect`).
- **Everything is data**: every non-terminal output carries a payload;
  pass-through semantics per node; any output can feed any input incl.
  `trigger`; trigger payload available as `{{ trigger }}`.
- **Native-value templating (D25)**: a config value that is exactly one
  `{{ expr }}` evaluates to the native value (dicts stay dicts, never
  repr strings); mixed content renders to string; bare `expr:` always
  native; field schema type applies post-evaluation.
- **Frames** isolate each flow invocation (variables, guard state,
  node outputs); data crosses only via Start/End ports. Outcomes are NOT
  isolated: asserts/errors aggregate run-wide, worst state wins (D20).
- **Quiescence = termination**, detected via the QUIESCENT sentinel
  pattern (see engine spec §3 — the last decrement wakes the pump; do
  not "simplify" this, it prevents a real deadlock).
- **Errors are data**: error/failed ports, never hidden exceptions.
  Unconnected error port receiving a message ⇒ run `failed`. Unreached
  REQUIRED End port at quiescence ⇒ run `failed` (D18). Guard exhaustion
  ports (`exhausted`/`expired`) are ordinary outputs, NOT error ports
  (D19). `flow` nodes expose an implicit `error` port (D21; the name
  `error` is reserved on End ports and python outputs — E012).
- Run states passed|failed|error|aborted → exit codes 0|1|2|130.
- Python nodes: persistent worker subprocess (JSON-lines over pipes),
  per-node `max_seconds` (default 300) enforced by worker kill;
  functions see ONLY declared inputs; JSON-serializable I/O.
- Safety rails: message budget (100000); per-firing `max_seconds` — the
  default (300) auto-applies to request/python ONLY, delay/loop/flow are
  exempt from the default (D24); timeouts route to ERROR ports, never
  data ports; optional run deadline (`run_timeout_s`/`--timeout` ⇒ run
  `error`, report still written); body capture valves (10MB/body +
  500MB/run, truncated markers); secrets masked AT EMISSION (events
  born masked).

## File format invariants

- Stable human-readable node ids (never UUIDs; charset
  `[A-Za-z_][A-Za-z0-9_]*`, E011); `layout:` quarantined at file bottom;
  edges as one-line inline maps; deterministic key order.
- YAML pinned to the safe canonical profile (D23, `docs/yaml-profile.md`):
  safe loader only; ONE shared serializer (canvas + CLI); strings
  force-quoted, ints/bools/null bare; no anchors; round-trip golden test
  guards byte-identical emit.
- End ports are REAL input ports (wire to `end.<port>`); Start ports
  define flow inputs, editable as key-value list in UI, bindable via
  `napf run -i key=value` (validated + type-coerced), End → stdout JSON.
- Env: all real `envs/*.env` gitignored; profiles auto-discovered
  (filename stem = profile name); process env overrides files;
  `env.required` per flow fails fast.

## Build order (each stage independently useful)

1. `core/loader.py` + Pydantic models + `napf check`
   (E001–E009, E011–E012 — E010 reserved; W101–W107)
2. `core/engine.py` scheduler + request/condition/assert/start/end
   + `napf run` (JSONL events from day one)
3. Remaining nodes (python + worker subprocess, merge, guards, loop,
   flow, set/get, switch, delay, log, fixture, note)
4. server/ + UI canvas (last)

## Testing priorities (in order of bug-risk)

1. Merge semantics under fast cycles (`all` clears slots; rule-2 nodes
   keep latest — this distinction is the most test-worthy engine code)
2. Quiescence detection (sentinel race + empty-seed finalize, EC08)
3. Required-End-port failure path (D18): unreached required output ⇒ run
   `failed` ⇒ exit 1, across subflow/loop frames (D20)
4. Guard exhaustion routing (D19): `exhausted`/`expired` as pass-through
   outputs; W106 lint; counter N-passes boundary (EC16)
5. Guard reset/per-frame isolation in loops & subflows
6. Worker lifecycle: timeout-kill-respawn, crash isolation, Windows
7. Loader round-trip: load → save preserves comments & key order (ruamel)
8. Timeout routing across node shapes (D24, TR-8) + native-value
   templating detection & post-eval coercion (D25, TR-10)

## Deferred by decision (do NOT implement, DO keep compatible)

- Codegen (`codegen:` manifest key parsed, unused)
- poll node, duplicate node, inline loop bodies, marker-based collect
- timer/webhook triggers (would require daemon — out of core scope)
- Conflict resolution beyond last-write-wins + reload prompt
