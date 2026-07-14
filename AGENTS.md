# AGENTS.md — napflow

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
   bodies, timing, retries) remains inspectable without silent loss;
   v0.2 stores large content once and loads it lazily (D34).
6. (Future, design-constrained today): generate standalone Python code
   (niquests clients, Pydantic models) from flows. One-directional:
   flows → code, never code → flows.

## Authoritative specs — read before changing behavior

- `docs/napflow-flow-schema.md`     — flow.yaml format, node catalog, rules
- `docs/napflow-workspace-manifest.md` — napflow.yaml, CLI surface, env model
- `docs/napflow-engine-spec.md`     — scheduler, frames, firing rules, events
- `docs/yaml-profile.md`            — canonical YAML read/emit profile (D23)
- `docs/DECISIONS.md`               — why each major decision was made (D01–D40)
- `docs/EDGE_CASES.md`              — resolution ledger; append new cases here
- `docs/PRODUCT.md` / `docs/REQUIREMENTS.md` — vision/scope; v0.1 + v0.2 FR/NFR checklist
  (tick requirements in the PR that lands them, with a test)
- `docs/PLAN.md` / `docs/JOURNAL.md`    — milestone sequencing + working journal
- `docs/RELEASING.md`               — experimental-v0.x policy (D33) +
  tag-driven release flow (release.yml)

Specs are hypotheses: when implementation proves one wrong, UPDATE THE SPEC
in the same PR as the code change.

Documentation workflow:
- Before non-trivial implementation work, read `docs/PLAN.md`,
  `docs/JOURNAL.md`, and the relevant authoritative specs above.
- At the end of each state-changing session and after each milestone or PR-sized
  change, use the repo-scoped `$napflow-closeout` skill in
  `.agents/skills/napflow-closeout/` to reconcile project memory from evidence.
- After behavior or schema changes, update the matching spec in the same
  change.
- For each session or milestone closeout, prepend a dated entry to
  `docs/JOURNAL.md` (done / decided / next) — it is the cross-session
  progress log; keep entries to 2–5 lines.
- Update `docs/REQUIREMENTS.md` only when a requirement actually lands with a
  test.
- Update `docs/EDGE_CASES.md` when recording a reproduced edge case or
  landing its tested resolution; OPEN means planned, never “doc fixed.”

Git workflow:
- Use Conventional Commits for commit messages (`type(scope): subject`);
  include a scope only when it adds clarity. Common types: `feat`, `fix`,
  `docs`, `test`, `refactor`, `chore`, `ci`, and `build`.

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
  CodeMirror 6 (D27) + Vite (dev-time only; ships as static files).
- Platforms: macOS + Windows + Linux from day one (D26) → pathlib
  everywhere, no shell-isms, subprocess spawn semantics, CREATE_NO_WINDOW.
- License: Apache-2.0 + NOTICE file (NOTICE is the attribution lever).
  No CLA (Apache §5 inbound=outbound suffices); DCO later if external
  contributors appear.
- Python project workflow: use **uv** for environment, dependency, test, lint,
  and build commands inside this repo (`uv sync`, `uv run pytest`,
  `uv run ruff check`, `uv build`). Avoid direct `pip`, `python`, or `pytest`
  invocations unless intentionally debugging outside the project environment.

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
  `error`, report still written); store-once full-fidelity content blobs
  across every persisted payload path; raw canonical local history using
  ordinary OS/workspace permissions; optional schema-aware declared-secret
  terminal/report presentation.

Current v0.2 development behavior under D34–D36/D39 has no destructive body/
run valves and never masks canonical events. It includes hash-verified lazy
resolution, prepared-wire request capture, bounded cooperative lifecycle,
cleanup, tasks/frames, subscriber windows, frozen versioned replay pages,
graph-sized view/frame projections, and on-demand browser blob reads. Never
rebuild the v0.1 valves/mask-everywhere approach or ACL/export hardening as if
either were the accepted v0.2 design.

## Version and compatibility policy

- `v0.1.0` is the first working milestone. All package v0.x releases,
  including the current `schema: napflow/v1`, are experimental (D33).
- Breaking flow/event/API/UI changes are allowed before package v1.0
  when documented in release notes. Best-effort readers/migrations are
  welcome but must not preserve faulty behavior at the expense of v0.2.
- v1.0 is when selected formats become stable and incompatible schema
  changes require a new marker or migration policy.

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

## Build history and current order

1. `core/loader.py` + Pydantic models + `napf check`
   (E001–E009, E011–E012 — E010 reserved; W101–W107)
2. `core/engine.py` scheduler + request/condition/assert/start/end
   + `napf run` (JSONL events from day one)
3. Remaining nodes (python + worker subprocess, merge, guards, loop,
   flow, set/get, switch, delay, log, fixture, note)
4. server/ + UI canvas (last)
5. **Current: v0.2 usable full-fidelity prototype**, sequenced only
   by `docs/PLAN.md` M0–M7: regression/format baseline → workspace and
   durable saves → lifecycle/worker → bounded execution/history →
   full-fidelity blobs/prepared requests → basic paged/lazy replay →
   public Workspace/Flow catalog, packaging, and UI contracts → focused
   release gate. M0/M1 completed 2026-07-12 and M2/M3/M4/M5/M6 completed
   2026-07-13. M7's reusable gate, exact version contract, compatibility
   notes, and local release preparation are implemented; the fresh remote
   PR/release dry-run, merge, and exact `v0.2.0` tag remain the promotion
   boundary.
   Timeline playback, 100k-event replay performance, run bundles, and
   security-grade history are future work (D39).

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
9. v0.2 correctness priorities (TR-11–22): path/symlink containment;
   inline-cycle deadline/abort; worker large-line/late-side-effect;
   cancellation cleanup; full-fidelity hash round-trip; prepared requests;
   protocol-safe optional redaction; basic paged/lazy replay and bounded
   loops; autosave/atomic durability.

## Deferred by decision (do NOT implement, DO keep compatible)

- Codegen (`codegen:` manifest key parsed, unused)
- poll node, duplicate node, inline loop bodies, marker-based collect
- timer/webhook triggers (would require daemon — out of core scope)
- Conflict resolution beyond last-write-wins + reload prompt
- Endpoint collections + Postman/OpenAPI import (one-directional,
  generation not sync; `endpoint:` = future additive request-config
  key — nothing to reserve in v0.x; see PRODUCT.md roadmap)
- `napf ui --app` Chromium app-mode window (current UI opens the default
  browser via stdlib `webbrowser`; no pywebview — see PRODUCT.md roadmap)
- Pause/resume/step and wire breakpoints (D30), extract-to-subflow (D31),
  remote hosting/user authentication, and encryption/key management are
  explicitly after v0.2. Keep seams compatible; do not add them to v0.2
  without a new owner decision.
- Also after v0.2 under D39: timeline scrubber/playback/checkpoints; the
  100k-event replay performance gate and expanded perf suite; advanced replay
  indexes/filters; run export/import and redacted bundles; explicit hard-limit
  omission metadata; and any separate secure-history ACL/DACL/forced-mode
  implementation. The runtime `workspace.flows` catalog remains in v0.2.
- Also explicitly after v0.2: fine-grained runtime-token redaction
  (EC10), descendant process-tree cleanup (EC22), and preemptible Jinja
  rendering / a final hard-deadline contract (EC27/EC35). These are OPEN
  limitations, not “resolved by documentation.”
