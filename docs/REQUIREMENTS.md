# napflow — Requirements (v1)

Status: adopted 2026-07-02. Every requirement is traceable to a spec:
**FS** = `napflow-flow-schema.md` v0.4 · **EN** = `napflow-engine-spec.md`
v0.2 · **WM** = `napflow-workspace-manifest.md` v0.3 · **YP** =
`yaml-profile.md` · **Dxx** = `DECISIONS.md`. Checkboxes track
implementation; tick them in the PR that lands the behavior (with a test).

Stages (from CLAUDE.md build order — each independently useful):
**S1** loader + models + `napf check` · **S2** engine core + basic nodes +
`napf run` · **S3** remaining nodes + python worker · **S4** server + UI.

## FR-1xx — Workspace & manifest

- [ ] FR-101 (S1) Parse & validate `napflow.yaml` into Pydantic models; `napf` locates it by walking upward from cwd. (WM)
- [ ] FR-102 (S1) Flow discovery: any directory under `flows.root` containing `flow.yaml` is a flow; identity = workspace-relative path; recursive nesting allowed. (WM §1)
- [ ] FR-103 (S1) Env profile discovery: every `envs/*.env` is a profile named by filename stem; no registry. (WM §2)
- [ ] FR-104 (S2) Env layering: profile file → process environment, last wins. (WM §3)
- [ ] FR-105 (S2) `defaults.request` merges shallowly into request nodes; templates there may reference only `env.*`/`run.*`. (WM §4, EC23)
- [ ] FR-106 (S2) Secret masking: values of env vars matching `environments.secrets` patterns (active profile + process env) replaced via substring scan, ≥5-char minimum, at event emission. Declared secrets only. (D22, EN §7)
- [ ] FR-107 (S1) `napf init` scaffolds: manifest, `flows/main`, `flows/example` (httpbin demo), `envs/dev.env`, `envs/example.env`, `.gitignore`, `.gitattributes` (`*.yaml`/`*.yml` `text eol=lf`), `.napflow/`. First-touch: `napf run flows/example` passes out of the box. (WM)
- [ ] FR-108 (S3) `python.interpreter` manifest key selects the worker interpreter; `null` = napflow's own. (WM, EN §5a)
- [ ] FR-109 (S1) `codegen:` manifest key is parsed and ignored (reserved). (WM)

## FR-2xx — Flow file format

- [ ] FR-201 (S1) `schema: napflow/v1` flow files parse into Pydantic models covering the full v1 node catalog. (FS)
- [ ] FR-202 (S1) Node ids: `[A-Za-z_][A-Za-z0-9_]*`, unique per flow, human-readable (never UUIDs). (FS, E011)
- [ ] FR-203 (S1) `layout:` is quarantined at the bottom of the file and never affects engine behavior. (FS, YP)
- [ ] FR-204 (S1) YAML read via safe loader only; written through the one shared canonical serializer (block style; strings force-quoted; ints/bools/null bare; no anchors; no line-wrapping; LF+UTF-8; edges as one-line inline maps; fixed schema key order). (YP, D23)
- [ ] FR-205 (S1) Round-trip: load → save preserves comments and key order (ruamel round-trip mode); golden test asserts `emit(parse(emit(x)))` byte-identical and `parse(emit(x))` deep-equals `x`. (YP)
- [ ] FR-206 (S1) Structures validated against JSON Schema Draft 2020-12 after parse (schema is the type authority). (YP)
- [ ] FR-207 (S2) Binary payload envelope: `{"__binary__": true, "content_type", "base64"}`; capture cap applies to encoded form. (FS)

## FR-3xx — Validation (`napf check`)

- [ ] FR-301 (S1) E001–E009 as specified (parse/schema, unknown type/keys, bad edge refs, multi-edge input, missing required input, start/end cardinality, flow-reference cycle with path, broken file refs, Jinja2 syntax). (EN §8)
- [ ] FR-302 (S1) E011 duplicate/invalid node id; E012 reserved port name `error` on End ports and python `outputs`; E010 permanently reserved, never reused. (EN §8, D21)
- [ ] FR-303 (S1) W101 guard analysis with the strict guarantee: delete guard nodes, test acyclicity; report any remaining cycle with its path. (EN §8, EC16)
- [ ] FR-304 (S1) W102 port-type mismatch, W103 unconnected error/failed output, W104 unreachable node, W105 env.required key in no discovered profile. (EN §8)
- [ ] FR-305 (S1) W106 unconnected guard exhaustion/timeout port. (D19)
- [ ] FR-306 (S1) W107 YAML implicit-coercion lint on unquoted scalars in string-typed fields (hand-edited files). (YP)
- [ ] FR-307 (S1) Python input ports derived by AST-parsing `nodes.py` — `check` never imports user code. (EC14)
- [ ] FR-308 (S1) `check` runs on the closure of referenced flows; errors block `napf run`, warnings print and proceed. (EN §2, §8)

## FR-4xx — Engine core

- [ ] FR-401 (S2) Message-driven scheduler: single asyncio loop per run, `in_flight` accounting, QUIESCENT sentinel enqueued by the zero-reaching decrement. (EN §3, D14)
- [ ] FR-402 (S2) Empty-seed guard: finalize immediately when post-seed `in_flight == 0`. (EC08)
- [ ] FR-403 (S2) Firing rules 1–6 exactly as specified — including merge `all`/`collect` slot-clearing vs rule-2 latest-value retention. (EN §4)
- [ ] FR-404 (S2) Frames: per-frame variables, inputs, firing counts, guard state; hierarchical frame ids; data crosses only via Start/End. (EN §1)
- [ ] FR-405 (S2) Outcome aggregation: asserts, python-asserts, unhandled error-port messages roll up run-wide; run state = worst outcome in the frame tree. (D20)
- [ ] FR-406 (S2) Run states `passed|failed|error|aborted` per EN §2 definitions, incl. required-End-port failure (D18); exit codes 0/1/2/130.
- [ ] FR-407 (S2) Message budget (`defaults.run.message_budget`, default 10000): tick per emission, `budget_warning` at 10%, exhaustion → run `error` naming the hot edge. (EN §3)
- [ ] FR-408 (S2) Abort: cancel tasks, close session, state `aborted`; events already written stay valid (dangling `request_started` tolerated). (EN §3, EC20)
- [ ] FR-409 (S2) Unhandled error-port message ⇒ run `failed`; nodes without an error port surface evaluation errors as unhandled node errors. (EN §2/§6, EC24)
- [ ] FR-410 (S2) Per-firing `max_seconds` settable on any node; the manifest default (`node_timeout_s` 300) auto-applies to `request`/`python` only — `delay`/`loop`/`flow` exempt from the default, explicit value honored. Tripped ceiling → `{error_kind: "timeout"}` on the node's error port; `flow` → child frame `aborted` + implicit error port payload; `loop`/port-less nodes → unhandled node error ⇒ `failed`. (D24)
- [ ] FR-411 (S2) Run deadline: `defaults.run.run_timeout_s` (null = off) + `napf run --timeout N`; expiry cancels in-flight work, finalizes state `error` (exit 2) with `error_reason: run_timeout`, report and JSONL written. (D24)

## FR-5xx — Node types

- [ ] FR-501 (S2) `start` — seeded once per frame; `out` carries the full `inputs` dict; ports define the flow's input interface. (FS, EN §4)
- [ ] FR-502 (S2) `end` — real input ports; accumulates latest value per port; `required: bool` default `true`; required-unwritten ⇒ run failed; `required: false` ⇒ `null`, noted in report; port name `error` rejected (E012). (D18, FS)
- [ ] FR-503 (S2) `request` — niquests shared per-run AsyncSession; `trigger` input; config templating; engine-level retry per node config; non-2xx on `response`, transport failures on `error`; full-detail events. (FS, EN §5, EC13)
- [ ] FR-504 (S2) `condition` — sandboxed Jinja2 expr; forwards incoming message on `true`/`false`. (FS)
- [ ] FR-505 (S2) `assert` — check kinds `status`/`expr`/`response_time`, ops `present|equals|not_equals|contains|matches|gt|lt`, `mode: report_all|fail_fast`; emits `assert_result` events; forwards on `passed`/`failed`. (FS)
- [ ] FR-506 (S3) `python` — declared inputs only; JSON-serializable I/O; `AssertionError` → error port + report as python-assert; other exceptions → error port with traceback; declared outputs may not be named `error`. (FS, E012)
- [ ] FR-507 (S3) `switch` — expr + cases, `default` port, pass-through. (FS)
- [ ] FR-508 (S3) `merge` — `any` (immediate forward), `all` (rendezvous, clear on emit), `collect` (count-based list). (FS, EN §4)
- [ ] FR-509 (S3) `counter` — check-then-decrement: exactly `count` passes on `continue`, then every message → `exhausted`; per-frame reset; optional `reset` input restores count silently. (FS, EC16)
- [ ] FR-510 (S3) `timeout` — first-message timestamp; lazy evaluation on arrival; `continue`/`expired`; `reset` clears. (FS)
- [ ] FR-511 (S3) `delay` — templatable seconds, cancellable sleep, pass-through. (FS)
- [ ] FR-512 (S3) `log` — emits masked `log` event, persisted to JSONL, pass-through. (FS, D13)
- [ ] FR-513 (S3) `set`/`get` — frame variable map; `set` forwards written value; `get` fires only on `trigger`. (FS, D17)
- [ ] FR-514 (S3) `fixture` — json/csv from `fixtures/` (csv → list of dicts, header required); read once, cached per run; unconnected `trigger` auto-fires once at frame start. (FS, D17)
- [ ] FR-515 (S3) `loop` — fires on `trigger`; `over` evaluated against that delivery; child frame per item binding `item`/`index`; `sequential`/`parallel` with `max_concurrency`; iteration error = body frame `failed`/`error`; `on_error` gates scheduling only; `fresh_session` opt-out; `results`/`errors` outputs. (FS, EN §5, D20)
- [ ] FR-516 (S3) `flow` — child frame; ports derived from target Start/End; implicit `error` port with `{state, failed_asserts, unhandled_errors}`; reference-only semantics (E007 DAG). (D21, FS)
- [ ] FR-517 (S3) `note` — markdown, no ports, no runtime behavior. (FS)

## FR-6xx — Templating

- [ ] FR-601 (S2) Jinja2 `SandboxedEnvironment` + `StrictUndefined` is the only expression/template language, for `{{ }}` config strings and bare `expr:` alike. (D10)
- [ ] FR-602 (S2) Context: `env`, `inputs`, `run` (`id`/`timestamp`/`env_name`), `nodes` (frame-local latest, unwrapped), `trigger` (full envelope), `item`/`index` in loop bodies. (EN §6)
- [ ] FR-603 (S2) Undefined variable → node error to the error port; port-less nodes → unhandled node error, run failed. (EN §6, EC24)

## FR-7xx — Observability

- [ ] FR-701 (S2) JSONL per run at `.napflow/runs/<flow>/<run-id>.jsonl`, append-only, objects identical to the live WebSocket stream; retention per `defaults.run.history`. (D13)
- [ ] FR-702 (S2) Event vocabulary exactly per EN §7 (types, common fields, `seq`).
- [ ] FR-703 (S2) Full request/response bodies always stored; `defaults.run.body_capture_mb` (10) valve with `truncated: true` marker; `value_preview` truncation in stream-only fields. (D13, EN §7)
- [ ] FR-704 (S2) Events are born masked (FR-106); `run_finished` carries state, durations, assert tallies, unhandled errors, masked end outputs, `nodes_never_fired`. (EN §7)
- [ ] FR-705 (S2) Timing breakdown captured where niquests exposes it; fields omitted otherwise. (EN §7)

## FR-8xx — CLI

- [ ] FR-801 (S1) `napf check` — full rule set over all discovered flows; non-zero exit on E-codes. (WM)
- [ ] FR-802 (S1) `napf list` — discovered flows with their Start/End ports. (WM)
- [ ] FR-803 (S2) `napf run <flow> [--env NAME] [-i k=v ...] [--input-json] [--timeout N]` — inputs validated & type-coerced against Start ports, fail-fast on unknown/missing; End outputs → stdout as one JSON object; logs → stderr; exit codes 0/1/2/130. (FS, EN §2, D24)
- [ ] FR-804 (S2) Report formats `none|junit|json` per `defaults.run.report`. (WM)
- [ ] FR-805 (S1) `napf init [dir]` per FR-107. (WM)
- [ ] FR-806 (S4) `napf ui [--port]` — serve UI + API + WebSocket on one localhost port, open browser. (WM, D03)

## FR-9xx — Python worker

- [ ] FR-901 (S3) Persistent worker subprocess per flow module, lazy spawn, capped pool; JSON-lines protocol over stdin/stdout; worker imports `nodes.py` once at startup. (EN §5a)
- [ ] FR-902 (S3) Serial task processing (documented limitation: no CPU parallelism through python nodes; stuck firing blocks the module until kill). (EC09)
- [ ] FR-903 (S3) Timeout: await with node `max_seconds` → `terminate()` → 2s grace → `kill()` → error to node's error port (`error_kind: timeout`) → lazy respawn. (EN §5a)
- [ ] FR-904 (S3) Crash isolation: worker death = node error (`worker_crash`), never an engine failure. (EN §5a)
- [ ] FR-905 (S3) Worker stderr (user print/logging) forwarded as log events. (EN §5a)
- [ ] FR-906 (S3) Windows: spawn semantics, `CREATE_NO_WINDOW`, reliable terminate. Grandchild processes documented as not reaped (EC22).

## FR-10xx — Server & UI (S4)

- [ ] FR-1001 BlackSheep server is a thin adapter over core: serves static UI bundle, REST for flows/runs, WebSocket for live events; core never imports it. (D03/D04)
- [ ] FR-1002 Canvas (@xyflow/react): render/edit nodes+edges; single-edge-input enforcement on connect; output fan-out; soft port-type coloring + W102 hints. (FS, D11)
- [ ] FR-1003 Canvas writes flow.yaml through the shared canonical serializer; layout changes touch only the `layout:` block. (YP)
- [ ] FR-1004 Filesystem watch: external change → reload or prompt (last-write-wins). (FS)
- [ ] FR-1005 Run on canvas with live event overlay; run history browser replays any JSONL (dangling `request_started` tolerated). (D13, EC20)
- [ ] FR-1006 Start ports editable as key-value list; End required flags editable; check errors/warnings (E/W codes) surfaced on canvas. (FS)
- [ ] FR-1007 Subflow UX: drill-in navigation, "used in N places", clone-to-new-flow action; ghost-wires for cross-node template references. (FS, D09)

## NFR — Non-functional requirements

- [ ] NFR-01 `napflow.core` importable standalone — zero cli/server/UI imports; enforced by an import-linter test. (EN §0)
- [ ] NFR-02 macOS + Windows from day one, Linux via CI: pathlib everywhere, no shell-isms, spawn-safe subprocesses. (CLAUDE.md)
- [ ] NFR-03 Distribution: one pip wheel containing the pre-built UI; no Docker, no Node at runtime; installable via `uv tool install napflow`. (D03)
- [ ] NFR-04 Python 3.12+; Pydantic v2; ruamel.yaml; Jinja2 sandbox; niquests; BlackSheep + uvicorn; Typer. (CLAUDE.md stack)
- [ ] NFR-05 Security posture: sandboxed Jinja2 only (no eval), safe YAML loading only, secrets masked at emission, worker subprocess isolation with kill ceiling. (D10/D12/D22/D23)
- [ ] NFR-06 Determinism: identical logical flow ⇒ byte-identical emitted file, cross-platform (golden round-trip test in CI). (YP)
- [ ] NFR-07 Apache-2.0 + NOTICE; no CLA; DCO when external contributors appear. (D16)
- [ ] NFR-08 Engine overhead assumptions hold: pipe round-trip and scheduling negligible vs HTTP; parallel loops bounded by `max_concurrency`. (EN §5a)

## Test requirements (priority order — highest bug-risk first)

- [ ] TR-1 Merge semantics under fast cycles: `all` clears slots vs rule-2 latest-value retention. (EN §4)
- [ ] TR-2 Quiescence detection: sentinel race + empty-seed finalize. (D14, EC08)
- [ ] TR-3 Required-End-port failure path: unreached required output ⇒ `failed` ⇒ exit 1, across subflow and loop frames. (D18, D20)
- [ ] TR-4 Guard exhaustion routing: `exhausted`/`expired` as pass-through outputs; W106; counter N-passes boundary (Nth vs N+1th message). (D19, EC16)
- [ ] TR-5 Guard reset / per-frame isolation in loops & subflows. (EN §4)
- [ ] TR-6 Worker lifecycle: timeout-kill-respawn, crash isolation, Windows semantics. (EN §5a)
- [ ] TR-7 Loader round-trip: comments & key order preserved; golden byte-identity corpus. (YP)
- [ ] TR-8 Timeout routing: request/python timeout → error port (wired = run passes, unwired = failed); flow timeout → aborted child + implicit error payload, recorded child asserts still aggregate; loop timeout → run failed via EC24; run deadline → `error`/exit 2 with report written; container nodes NOT killed by the default ceiling. (D24)
