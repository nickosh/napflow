# napflow — Working journal

Newest first. One short entry per working session / milestone:
**done / decided / next**, 2–5 lines each. This is the cross-session
progress log — keep it lean; details live in specs, DECISIONS, and git.

## 2026-07-08 — S4/M4 leftovers closed + CI unbroken

- Done: all five M4 leftovers — code editor upgrade (CodeMirror 6, see
  Decided), drag-from-palette (drop at `screenToFlowPosition`),
  structured row editors for assert checks / switch cases
  (`StructuredRows.tsx`, native-typed value cells), type-aware
  Start-port defaults (red-border local-only on mismatch), live W102
  connect hint (`ConnectHint.tsx` via `useConnection`, soft/never
  blocks). Also fixed the 3-OS `ui e2e` CI red: the M3 canvas spec
  predated the M4 "check E-codes don't 400" pin — split into
  editable-with-E-codes + unloadable-shows-error-view (new fixture).
  22 Playwright (7 new) + 8 Vitest (1 new) green locally; 305 pytest.
- Decided: **D27** — CodeMirror 6 over Monaco for nodes.py (built and
  measured BOTH: ~458KB lazy chunk vs ~3.6MB+worker+font; Monaco's
  EditContext input needed a test seam + compat flag, CM6's
  contenteditable needs nothing — that fragility matters in the
  default-browser, not-Chromium-pinned world).
- Next: S4/M5 — run on canvas + history (FR-1005) over the M1 WS;
  verify this push's job-level CI results first (NFR-10 lesson).

## 2026-07-07 — S4/M4 editing + write path

- Done: `merge_flow_document` (surgical ruamel merge — no-op saves
  byte-identical, layout-only drags diff only `layout:`, comments
  survive; 10 tests) + `checker.python_functions`; write endpoints
  (PUT flows/code, GET etags) with content-hash etag concurrency +
  path-traversal guard (10 server tests); editable canvas — drag,
  connect w/ E004 auto-replace, add/delete, per-type config forms,
  Start/End port editors, nodes.py editor w/ AST syntax report,
  debounced ~1s autosave, ~2s etag poll for external changes;
  8 Playwright editing e2e.
- Decided (pins): flow-detail GET no longer 400s on check E-codes
  (mid-edit flows stay editable; only unloadable files 400) and dumps
  `exclude_unset`; FR-1004 v1 = etag polling, not a native FS watcher;
  broken nodes.py still saves (last-write-wins, error reported).
- Deferred from the M4 plan (tracked in PLAN "M4 leftovers"):
  **Monaco** (npm was sandbox-blocked; textarea shipped behind the same
  GET/PUT contract — sandbox allowlist since fixed in
  `.claude/settings.json`, install works next session);
  **drag-from-palette** (click-to-add shipped); **structured editors
  for assert `checks` / switch `cases`** (raw-JSON textareas shipped);
  **typed Start-port defaults** (the default cell writes strings only —
  numbers/objects need the YAML for now); **live W102 type-mismatch
  hint at connect time** (W102 surfaces post-save via diagnostics).
- Verification gap: the sandbox blocked socket binds, so the 8
  Playwright editing e2e + the uvicorn/port pytest cases ran ONLY in
  CI for this commit — check job-level results on the M4 push before
  trusting the FR ticks (NFR-10 lesson).
- Next: S4/M5 — run on canvas + history (FR-1005) over the M1 WS.

## 2026-07-06 — S4/M3 read-only canvas

- Done: `checker.node_surfaces` public + flow-detail `ports` payload
  (AST python ports stay server-side, EC14; `null` = broken ref);
  canvas renders real flows — layout: coords + BFS fallback, labeled
  D11-colored handles, required markers, E/W badges, flow list with
  `main:` default + pathname deep links (SPA), read-only inspector,
  diagnostics panel; broken flows show E-codes instead of a canvas.
  19 pytest server tests, 5 Vitest, 8 Playwright (incl. W103 badge +
  E004 error-view against generated fixture flows). Screenshot-checked.
- Decided (WM pin): `ports` shape in the flow-detail payload; wired-
  but-undeclared ports get "any" handles (merge growth, null surfaces)
  so edges never orphan visually.
- Next: S4/M4 — editing + write path (connect rules E004, node
  add/delete, config forms + Monaco, Start/End port editing, save via
  server-side canonical serializer, golden canvas-diff test, FS watch).

## 2026-07-06 — S4/M2 UI scaffold + wheel walking skeleton

## 2026-07-06 — S4/M2 UI scaffold + wheel walking skeleton

- Done: `ui/` (Vite 8 + React 19 + TS 6 + Zustand + @xyflow/react 12;
  npm; Node 22 pinned dev-only) — hello-canvas rendering one node per
  discovered flow from the real API; vite builds into
  `src/napflow/server/static`; hatchling `artifacts` forces the
  gitignored bundle into sdist+wheel (NFR-03 gated in CI `ui` job AND
  release.yml — a release can't ship a UI-less wheel); Playwright
  harness (fresh `napf init` per run via `e2e/serve.mjs`) + 2 smokes
  green locally; static tests made bundle-independent (monkeypatched
  STATIC_DIR). NFR-03 + FR-806 + FR-1001 ticked. Also: M1's CI red was
  a missed `ruff format` — fixed; TR-9's Windows leg rides this push.
- Decided: e2e = chromium-only in v1 (default-browser UX, engine
  matrix is overkill); e2e always runs against the BUILT bundle
  through the real server (the wheel-user path, never vite dev);
  README run-row unstuck (was 🚧 S2).
- Next: S4/M3 — read-only canvas (flow list, `main:` default open,
  nodes/edges/layout render, D11 port coloring, E/W on canvas;
  FR-1002 render half). Verify the ui job + TR-9 Windows leg on this
  push's CI first.
- [Update, same day: CI run 28785741251 all 7 jobs green — TR-9
  Windows leg + NFR-03 ui job both CONFIRMED, ticks finalized. The
  windows Playwright install stalled minutes in `--with-deps` (known
  hosted-runner behavior, it installs nothing useful there) before
  recovering; fixed to Linux-only deps + 20-min job timeout, and Node
  pinned to 24 LTS (engines floor stays 22.12 = Vite 8's own).]

## 2026-07-06 — S4 adopted + S4/M1 server

- Done: S4 breakdown adopted in PLAN (M1 server → M2 UI scaffold+wheel
  → M3 read canvas → M4 edit → M5 run overlay → M6 subflow UX). M1
  landed: `core/runprep.py` (run gate/env/stream shared verbatim with
  `napf run`), `napflow/server` (REST + run registry + WS; frames =
  JSONL lines via one `encode_record`, D13 by construction), `napf ui`
  (port 6273 "NAPF", scan, `--no-browser`). 282 tests (17 new) incl.
  TR-9 through-the-server on real uvicorn. Surface pinned in WM.
- Decided (owner): first release ships AFTER S4 — hand-authoring YAML
  is too much friction for a fresh release; Playwright browser suite
  grows per milestone (harness at M2). WM pins: no reports for server
  runs in v1; end outputs reach the UI only via the masked
  `run_finished` event; registry buffers drop at run end.
- Next: S4/M2 — UI scaffold (`ui/`: Vite+React+TS+Zustand+xyflow) +
  wheel walking-skeleton (hatchling force-include, CI Node build,
  Playwright harness + first smoke). Confirm TR-9's WINDOWS CI leg on
  this push before treating the tick as final (NFR-10 lesson).

## 2026-07-06 — S3/M5 hierarchical frames — STAGE S3 COMPLETE

- Done: flow + loop nodes on hierarchical frames — one pump/budget/
  quiescence, per-frame `in_flight`+`done` completion (the QUIESCENT
  trick per frame), path ids, subtree outcome sums (D21 payloads),
  flow-timeout-aborts-child (TR-8), loop results/errors (EC06/EC36),
  fresh_session cookie isolation, `check_run_closure` run gate.
  TR-3/5/8 + FR-404/405/410/515/516 ticked. 265 tests (15+1 new).
  Version 0.1.0.dev3 (stage-closing commit). Full node catalog
  runnable — SUPPORTED_NODE_TYPES gate removed as dead code.
- Decided (EN §5 pins): flow emits optional-unwritten as null, skips
  required-unwritten (child failed instead); loop errors emit only
  when non-empty, unscheduled iterations absent from both outputs;
  worker pool bound = one worker per distinct nodes.py in the closure
  (no eviction — module state preserved); nodes_never_fired is
  root-frame only; runtime recursion = budget backstop (E007 gates).
- Next: S4 — server + UI canvas (or first release from the CLI-only
  product per PRODUCT.md; S1–S3 is shippable).
- [Update, same day: Windows CI caught a Proactor race — pipe EOF
  lands before exit-status collection, so crash messages read "exit
  code unknown". Fixed: the protocol reader reaps (`proc.wait()`,
  bounded) before composing the death message. This is exactly the
  class of bug the worker-first order + 3-OS matrix existed to catch.]
- [Update 2: compat-job post-mortem. The NFR-10 job was born broken —
  test_architecture.py's module-level import-linter import breaks
  collection in the dev-less venv. It never had a green run: S2/M2–M5
  were pushed as ONE batch, so the job (added at M4) first executed at
  CI run #10 and failed there; the 2026-07-05 NFR-10 tick was
  premature (amended in REQUIREMENTS). Fixed via pytest.importorskip;
  run #19 = all four legs green. Lesson: batched pushes mean a
  mid-batch CI change is never tested standalone — verify job-level
  results, not just the run badge, when ticking CI-backed NFRs.]

## 2026-07-06 — S3/M4 guards + flagship retry example

- Done: `_deliver_guard` — counter (EC16 boundary, count 0 = exhaust
  all) + timeout (lazy monotonic clock), silent absorbed `reset`,
  frame-local state, `guard_tripped` events; flagship retry-until-ready
  (merge any → request → condition → counter → delay cycle) green
  against a local server in ready and gave-up variants. FR-509/510 +
  TR-4 ticked; FR-403 all six rules complete. 250 tests (8 new).
- Decided (EN §4 pins): guards are instant/inline; reset = absorbed
  delivery (no firing, no events); timeout boundary elapsed == seconds
  ⇒ expired; guard_tripped emits alongside the pass-through (D19:
  tripping is data).
- Next: S3/M5 — hierarchical frames + flow/loop nodes (FR-404/515/516,
  D20/D21/D24); completes TR-3/5/8; stage close → 0.1.0.dev3.

## 2026-07-06 — S3/M3 simple frame-local nodes + first-touch green

- Done: switch/set/get/log/fixture runners + rule-6 fixture auto-seed
  (synthetic `__seed__` trigger through the normal firing path) +
  per-run fixture cache + `_LogEcho` live stderr echo in `napf run`.
  EC34 first-touch DoD green: fresh `napf init` → `napf run
  flows/smoke` exits 0 offline. FR-507/512/513/514/517 ticked; FR-403
  now only awaits rule 4. 242 tests green (13 new).
- Decided (EN §5 pins): switch first-match native equality; get of an
  unset variable = `variable_unset` node error (never silent null,
  EC19); fixture `file`/`format` literal in v1, cache keyed by resolved
  path, CSV values stay strings, ragged-long rows error; log events
  echo to stderr live, masked.
- Next: S3/M4 — guards (counter EC16 check-then-decrement, timeout,
  `reset` inputs, rule 4) + flagship retry-until-ready example (TR-4).

## 2026-07-06 — S3/M2 python worker + python node

- Done: `core/worker_main.py` (stdlib-only child; EC28 dup + fd1→fd2,
  stream capture → log events) + `core/worker.py` (lazy per-module
  workers, serial lock, terminate→2s→kill, pre-send retry, stderr tail)
  + engine `_run_python`/`_resolve_interpreter` + CLI `flow_dir`/
  `workspace_root` threading. FR-108/506/901–906 + TR-6 ticked; TR-8/9
  half-annotated. 229 tests green (17 new, all real subprocesses).
- Decided (EN §5a pins): protocol ready/fatal/stream extensions;
  dict-keyed return convention (missing key = python_error, `outputs:
  []` discards); worker inherits process env (profile NOT injected),
  cwd = workspace root; interpreter resolution rule; timeout payloads
  now carry `max_seconds` (request too); pool cap deferred to M5.
- Next: S3/M3 — simple frame-local nodes (switch, set/get, log,
  fixture, note) → `napf run flows/smoke` passes offline (EC34).

## 2026-07-06 — S3/M1 firing rules 2–3 + merge

- Done: pump dispatch refactor in `core/engine.py` — rule-2 latest-value
  slots (fire on full set, overwrite re-fires, decision-time snapshot
  plumbed to runners for M2's python) + merge `any`/`all`/`collect`
  fired inline (`all` clears on emit, collect leftovers dropped). TR-1
  + FR-508 ticked; 212 tests green (7 new). EN §4 pin block added.
- Decided: S3 milestone order adopted worker-first (owner call, PLAN):
  M1 rules/merge → M2 python worker → M3 simple nodes → M4 guards →
  M5 loop/flow — subprocess risk surfaces earliest after its one
  dependency.
- Next: S3/M2 — python worker subprocess (FR-901–906, EC28 fd-dup
  protocol integrity) + python node runner (FR-506) + FR-108.

## 2026-07-06 — Linux first-class (D26) + S4 UI shell pinned

- Done: verified zero platform-conditional code in src/tests and 3-OS CI
  green since M0; updated CLAUDE.md, PRODUCT, NFR-02, PLAN S4 DoD, flow
  schema, engine spec header; added OS classifiers to pyproject.
- Decided: D26 — Linux same tier as macOS/Windows (owner call; "Linux
  via CI" hedge dropped since CI already proves it on every commit).
- Decided: S4 `napf ui` opens the default browser (stdlib `webbrowser`);
  no pywebview (Linux webview system deps would break the one-wheel
  promise; three render engines to test). Chromium app-mode
  `napf ui --app` deferred as a v1.1 candidate (owner call).
- Next: unchanged — S3 python worker + remaining nodes.

## 2026-07-05 — S2/M5 `napf run` — stage S2 complete

- Done: `cli/main.py run` + `cli/report.py` — LOAD/CHECK gate
  (`check_flow`, E-codes exit 2 before anything executes), env profile
  resolution + layering, `-i`/`--input-json`/`--timeout`, JSONL sink +
  retention wiring, stdout End-outputs JSON (unmasked, pipeable),
  stderr summary, junit/json reports, Ctrl-C abort, exit codes
  0/1/2/130. S2 DoD test: request→assert via `napf run` against a
  local server, exit 0/1. 205 tests green (31 new). Version bumped
  `0.1.0.dev2` (stage-closing commit per RELEASING.md).
- Decided (WM pins): run gate = single-flow check until S3; stdout NOT
  masked (functional output); `--input-json` first, `-i` overrides;
  missing default profile = note + process env; report files next to
  the JSONL.
- Next: S3 — full node set + python worker (worker subprocess protocol
  EC28, merge, guards, loop, flow, set/get, switch, log, fixture;
  TR-1/4/5/6/8/9 remainder; `flows/smoke` first-touch run).

## 2026-07-05 — S2/M4 request node + niquests adapter

- Done: `core/httpclient.py` (the ONLY niquests import, NFR-09 — guard
  test) + `_run_request` in the engine: shared per-run sessions (one
  per http_version option), engine-level retry (transport-only,
  immediate), non-2xx-is-data (EC13), defaults.request shallow merge
  (EC23), body codec incl. binary envelope (FR-207), capture valves
  (event copy only), best-effort timing, timeout→error-port (TR-8
  request paths); NFR-10 compat CI job. Tests run against a local
  threaded HTTP server — zero external network. 192 tests (17 new).
  FR-105/207/408/503/703/705/706 + TR-10 + NFR-09/10 ticked.
- Decided (EN §5 pins): retry = immediate re-attempt, max_attempts
  only; retries_total = attempts−1; decode json→native / text →
  str / else envelope, empty→null; valves cap the event copy, port
  values stay full; header/query values stringify post-render.
- Next: S2/M5 — `napf run` CLI (BIND inputs, reports, --timeout, exit
  codes) → S2 done.

## 2026-07-05 — S2/M3 engine scheduler + frames + first runners

- Done: `core/engine.py` — `FlowRun` (pump/QUIESCENT/empty-seed per EN
  §3, budget + warning, run deadline, abort, `max_seconds` via
  asyncio.timeout with D24 default scoping), BIND/ENV lifecycle,
  root-frame + run-level outcome aggregation (D18/D20), runners for
  start/end/condition/assert + delay (pulled forward — TR-2 needs an
  async node). FR-401/402/406/407/409/501/502/504/505/511/602/603/704
  ticked; TR-2 green, TR-3 root-frame half; 175 tests (26 new).
- Decided (EN §2 pins): error_reason vocabulary; unhandled_errors shape
  `{frame,node,port,kind,message}` incl. required-End misses;
  `op: present` on undefined path = failed check, not node error;
  never-fired excludes end/note; value_preview ≤512 chars; unsupported
  node types ⇒ run `error`, never a crash.
- Next: S2/M4 — request node + niquests adapter (NFR-09), retry,
  capture valves, timeout→error-port routing.

## 2026-07-05 — S2/M2 events + JSONL + masking

- Done: `core/events.py` — EN §7 vocabulary as 13 kw-only dataclasses,
  `EventStream` (stamps run_id/ts/seq, masks at emission, fans out to
  sinks), `JsonlSink` + `run_log_path` + `apply_retention`,
  `SecretMasker` (D22). FR-106/701/702 ticked; 149 tests green (17 new).
- Decided (EN §7 pins): run id `YYYYmmdd-HHMMSS-xxxxxx` UTC (sortable,
  Windows-safe, `x`-mode open); seq 1-based; ts UTC-ms-`Z`; optional
  (default-None) fields omitted when unset; compact UTF-8 JSONL flushed
  per line; mask token `***`, longest-first, keys scanned too.
- Next: S2/M3 — scheduler + frames + start/end/condition/assert
  (`core/engine.py`; TR-2, TR-3).

## 2026-07-05 — S2/M1 templating render (+ S2 milestone breakdown)

- Done: render half of `core/templating.py` — `Renderer` (sandboxed
  string + native envs, StrictUndefined), native-value rule (D25) with
  structural single-expression detection, bare-expr evaluation,
  recursive config rendering, post-eval coercion (`coerce_value` /
  `stringify_native`), `TemplateEvaluationError`; `layer_env` (FR-104).
  FR-104/601/604 ticked; 132 tests green (34 new).
- Decided: S2 milestones M1–M5 adopted in PLAN.md (templating → events
  → scheduler → request → run). Pins (EN §6 / WM §3): tag-bearing
  templates always string-render; string-typed fields stringify natives
  as JSON, never repr; number/boolean coercion accepts env-file string
  forms; full process env visible in `env.*`.
- Next: S2/M2 — events + JSONL sink + masking (`core/events.py`).

## 2026-07-05 — S1 closeout: README, versioning, release flow, rename

- Done: real README (status table, install-from-git, flow example);
  repo renamed `napflow-prototype` → `napflow` on GitHub (remote
  updated; old URLs redirect); version bumped `0.1.0.dev1` +
  `[project.urls]`; docs consistency pass (RELEASING.md wired into
  CLAUDE.md/PLAN; "prototype" wording → "v1" in WM).
- Decided (`docs/RELEASING.md`, adopted): `0.1.0.devN` bumps in the
  stage-closing commit; releases are tag-driven via `release.yml`
  (gate → build → git-cliff notes → GitHub Release; inert until first
  tag); PyPI via trusted publishing deferred with a written checklist.
- Next: S2/M-first — engine scheduler + frames (FR-401..), templating
  render + native-value rule (D25).

## 2026-07-05 — S1/M5 CLI — stage S1 complete

- Done: `cli/main.py` + `cli/scaffold.py` — `napf init/list/check`
  (`napf` script entry, typer dep). Scaffold written through the
  canonical serializer and checks clean out of the box; the checker
  caught a scaffold bug (smoke flow missing its start node, E006) —
  the tool works. 98 tests green. FR-107/203/801/802/805 + NFR-06
  ticked; every S1 requirement now closed.
- Decided (WM amended): `fixtures/smoke.json` added to the init listing
  (E008 requires it); check exit codes 0/1/2; init never overwrites.
- Next: S2 — engine core + `napf run` (scheduler, frames, templating
  render + native-value rule, request/condition/assert nodes, events).

## 2026-07-04 — S1/M4 checker

- Done: `core/checker.py` — all of E001–E012 + W101–W107 with per-code
  tests (86 total green); positioned diagnostics with node id + fix hint
  (FR-309); AST-only python ports (EC14); closure checking incl. refs
  outside flows.root; `core/templating.py` (sandboxed env + syntax half).
- Decided (EN §8 amended): E005 covers unwired required End ports; E008
  covers missing nodes.py/function, loop body without `item`, templated
  refs; implicit input ports named `in`/`trigger`, merge `in[1-9][0-9]*`;
  W105 also flags unparseable profiles; W107 scoped to YAML-1.2 reality.
- Next: S1/M5 — CLI (`napf init/list/check`, exit codes) → S1 done.

## 2026-07-04 — S1/M3 discovery (+ CI fix)

- Done: `core/workspace.py` — manifest walk-up, flow discovery (identity
  = workspace-relative posix path, sorted), env profile discovery +
  strict `.env` dialect parser. FR-101/102/103 ticked; 61 tests green.
  Fixed red CI from M1/M2: `setup-uv@v8` doesn't exist as a moving tag —
  pinned `v8.2.0` (checkout@v7 was fine).
- Decided (WM §2 amended): full-line comments only; malformed lines /
  invalid keys (incl. `export`) fail fast with file:line; duplicate keys
  last-wins; one matching quote pair stripped.
- Next: S1/M4 — checker (E001–E012, W101–W107, AST-derived python
  ports, closure checking, file:line diagnostics).

## 2026-07-04 — S1/M2 loader + write path

- Done: `core/loader.py` — round-trip read with positioned diagnostics
  (`LoadError`/`locate()` map pydantic error locs to file:line), canonical
  emitter per D23, golden round-trip corpus (3 files + checked-in
  canonical golden); `.gitattributes` pins YAML to LF for cross-OS byte
  identity. FR-204/205/206/208 ticked; 45 tests green.
- Decided (yaml-profile amended): layout coordinate pairs join edges as
  the second flow-style island; literal/folded blocks preserved (no
  coercion risk); Python-side JSON Schema validation = Pydantic itself
  (the exported schema is generated from the same models).
- Next: S1/M3 — discovery (manifest walk-up, flow discovery, env
  profiles + dialect).

## 2026-07-04 — S1/M1 Pydantic models

- Done: `core/models/` (common/flow/manifest) — full 18-type node catalog,
  discriminated unions, frozen read-only views, manifest defaults = the
  documented built-ins; JSON Schema 2020-12 export; 27 tests incl. both
  spec examples parsed verbatim. FR-201/FR-109 ticked.
- Decided: config details the catalog left loose pinned in the schema doc
  ("Config surface pinned at implementation") — node-level `max_seconds`,
  switch `{name, equals}` cases, loop defaults, `report` default `none`.
- Next: S1/M2 — loader + write path (safe ruamel read with position
  marks, canonical emitter, golden round-trip corpus).

## 2026-07-04 — S1/M0 repo scaffolding

- Done: pyproject (hatchling, src layout, `napflow/{core,cli}`), pytest +
  ruff + import-linter contract test (NFR-01 green), 3-OS GitHub Actions
  workflow, cliff.toml + generated CHANGELOG.md (NFR-11), README, LICENSE
  + NOTICE (NFR-07), uv.lock committed; wheel builds clean.
- Decided: hatchling as build backend (S4 will need to force-include the
  pre-built UI in the wheel); dev on 3.12 via `.python-version`.
- Next: S1/M1 (Pydantic models). [Update, same day: CI run #1 green on
  all three OS in 33s — M0 closed.]

## 2026-07-04 — Session tooling

- Done: SessionEnd breadcrumb hook (`.claude/hooks/session-end-log.sh` →
  gitignored `.claude/sessions.log`), project `.claude/settings.json`,
  initial `.gitignore`; S1 milestones mirrored into the native task list.
- Decided: Typer stays as the CLI library (Typer wraps click so its
  ecosystem comes along; tyro's config-object model doesn't fit a
  command-tree CLI like `napf`).
- Next: S1/M0 repo scaffolding.

## 2026-07-02 — Documentation phase closed

- Done: full doc set adopted and internally consistent — specs
  (flow-schema v0.4, engine v0.2, manifest v0.3), D01–D25, EC01–EC37,
  PRODUCT, REQUIREMENTS, yaml-profile, PLAN; five commits on `main`.
- Decided: tech stack stays as-is (niquests/BlackSheep, with adapter
  seam + compat CI as insurance); endpoint collections and
  Postman/OpenAPI import parked for v2; tooling = native tasks +
  built-in memory + git-cliff changelog at M0.
- Next: S1/M0 — repo scaffolding (pyproject, CI matrix, import-linter,
  changelog toolchain).
