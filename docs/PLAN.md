# napflow — Development Plan (v1)

Status: adopted 2026-07-02. This file sequences the stage backlog —
`REQUIREMENTS.md` stage tags define the *scope*; this defines the *order
of attack* and definition-of-done per milestone. Tick boxes as
milestones land; append course corrections, don't rewrite history.

## S1 — loader, models, `napf check`  ✅ done 2026-07-05

Deliverable: `napf init` / `napf list` / `napf check` usable in CI.

- [x] **M0 — Repo scaffolding** (landed 2026-07-04; CI run #1 green on
      ubuntu/macos/windows)
  - [x] `pyproject.toml` (uv-managed), `napflow/{core,cli}` package layout
  - [x] pytest + ruff; GitHub Actions matrix macOS/Windows/Linux from
        day one (NFR-02)
  - [x] import-linter: `core` imports nothing from cli/server (NFR-01)
  - [x] Changelog toolchain: `cliff.toml` committed + `CHANGELOG.md`
        (Keep a Changelog format via git-cliff; conventional commits
        already in use since the first commit) (NFR-11)
  - [x] Working journal live: `docs/JOURNAL.md` + the CLAUDE.md rule
        (dated entry per milestone / PR-sized commit). SessionEnd
        breadcrumb hook implemented 2026-07-04:
        `.claude/hooks/session-end-log.sh` appends per-session lines to
        gitignored `.claude/sessions.log` (the agent-written journal
        stays the load-bearing part).
  - DoD: empty package, green CI on all three OS.
- [x] **M1 — Models** (landed 2026-07-04): Pydantic v2 manifest + flow
      models covering the full node catalog (`core/models/`); JSON Schema
      export. Loose catalog details pinned in the schema doc ("Config
      surface pinned at implementation"). (FR-101/201/206)
- [x] **M2 — Loader + write path** (landed 2026-07-04): safe ruamel read
      with position marks (`locate()` threads pydantic error locs to
      file:line); canonical emitter in `core/loader.py`; CommentedMap as
      single write source; golden round-trip corpus + checked-in
      canonical golden file. (FR-204/205/206/208, TR-7)
- [x] **M3 — Discovery** (landed 2026-07-04): manifest walk-up; flow
      discovery (identity = workspace-relative posix path); env profiles
      + dialect — `core/workspace.py`. (FR-101/102/103)
- [x] **M4 — Checker** (landed 2026-07-04): E001–E012; W101
      (guard-removal acyclicity) through W107; AST-derived python ports;
      closure checking; file:line diagnostics + hints —
      `core/checker.py` (+ `core/templating.py` syntax half).
      Rule-scope pins recorded in engine spec §8. (FR-301–309)
- [x] **M5 — CLI** (landed 2026-07-05): `napf init` (incl. `flows/smoke`
      + `fixtures/smoke.json` scaffold, written through the canonical
      serializer, checks clean out of the box), `napf list`,
      `napf check` with exit codes 0/1/2. (FR-801/802/805, FR-107)

S1 DoD: every S1 checkbox in REQUIREMENTS ticked with a test; `check`
catches all E/W codes on a fixture corpus; round-trip byte-identical
across OS in CI.

## S2 — engine core + `napf run`  ✅ done 2026-07-05

Scheduler, frames, budget, deadline (FR-4xx); templating incl. the
native-value rule (D25); request/condition/assert/start/end; events +
JSONL + masking; `napf run` with reports and exit codes.
DoD: a linear request→assert flow runs headless with correct exit
codes; TR-2 (quiescence) and TR-3 (required-End) green.

Milestone breakdown (adopted 2026-07-05) — bottom-up by dependency:
templating feeds everything, the engine consumes events, request
consumes the engine, `napf run` wires it all.

- [x] **M1 — Templating render** (landed 2026-07-05): native-value rule
      (D25) with structural single-expression detection, native vs
      string render paths, bare-expr evaluation, recursive config
      rendering, post-eval type coercion (`coerce_value` /
      `stringify_native`), `TemplateEvaluationError` (routing lands M3);
      env layering `layer_env` — `core/templating.py`,
      `core/workspace.py`. (FR-104/601/604; TR-10 core)
- [x] **M2 — Events + masking** (landed 2026-07-05): `core/events.py`
      — EN §7 vocabulary (13 dataclasses) with common fields + `seq`,
      `EventStream` (stamps + masks + fans out), JSONL sink at
      `.napflow/runs/<flow>/<run-id>.jsonl`, retention per
      `defaults.run.history`, `SecretMasker` at emission (D22).
      (FR-106/701/702; FR-704 ticks at M3 when the engine emits
      `run_finished` for real)
- [x] **M3 — Scheduler + frames** (landed 2026-07-05): pump + QUIESCENT
      sentinel + empty-seed guard (EC08), rule-1/5 firing, frames,
      run-level outcome aggregation (D18/D20), message budget, abort,
      run deadline, `max_seconds` cancellation; start/end/condition/
      assert runners + delay (pulled forward for TR-2's async-race
      tests) — `core/engine.py`. TR-2 green; TR-3 root-frame half
      green. Left open by design: FR-403 rules 2–4 (python/merge/guards,
      S3), FR-404 hierarchical frames (S3), FR-408 session close (M4),
      FR-410 error-port routing (M4/S3), FR-411 `--timeout` flag (M5).
- [x] **M4 — request node** (landed 2026-07-05): niquests behind
      `core/httpclient.py` (NFR-09, guarded by test), engine-level
      retry, non-2xx-is-data (EC13), `defaults.request` merge (EC23),
      capture valves, timing fields, timeout→error-port routing, binary
      envelope; NFR-10 compat CI job; local-server test suite — no
      external network. (FR-105/207/503/703/705/706 + FR-408 session
      close; NFR-09/10; TR-8 request paths, TR-10 complete)
- [x] **M5 — `napf run`** (landed 2026-07-05): LOAD/CHECK gate
      (`check_flow`, E-codes → exit 2), env profile resolution +
      layering, `--env` / `-i` / `--input-json` / `--timeout`, JSONL
      sink + retention wiring, End outputs → stdout JSON (unmasked —
      functional output, pinned in WM), logs → stderr, junit/json
      reports (`cli/report.py`), Ctrl-C abort, exit codes 0/1/2/130.
      (FR-411/803/804)

S2 DoD verified: `test_s2_dod_request_assert_headless` runs a linear
request→assert flow via `napf run` against a local server — exit 0 on
pass, exit 1 on a failing assert; TR-2 green; TR-3 root-frame green
(cross-frame half completes with S3 subflow/loop frames).

## S3 — full node set + python worker  ✅ done 2026-07-06

python + worker subprocess (protocol integrity per EC28), merge,
guards, loop, flow, set/get, switch, delay, log, fixture, note.
DoD: flagship retry example runs; `napf run flows/smoke` passes offline
(first-touch, EC34); TR-1/4/5/6/8/10 green (TR-9's protocol-integrity
half lands here; its through-the-server half completes in S4).

Milestone breakdown (adopted 2026-07-06) — worker-first: the python
worker is the riskiest chunk (subprocess lifecycle, Windows), so it
lands right after its one dependency (rule-2 slot firing); simple nodes
and guards follow; container frames close the stage.

- [x] **M1 — firing rules 2–3 + merge** (landed 2026-07-06): pump
      dispatch grew the latest-value slot machinery (rule 2: fire when
      all connected inputs filled, later deliveries overwrite +
      re-fire, snapshot semantics pinned in EN §4) and merge
      `any`/`all`/`collect` fired inline (rule 3: `all` clears slots on
      emit, `collect` count-based, leftovers dropped). TR-1 green in
      full — rule-2 retention proved with a fabricated two-input
      condition node (engine trusts the checker), so it didn't have to
      wait for M2's python nodes. (FR-403 rules 2–3, FR-508)
- [x] **M2 — python worker + python node** (landed 2026-07-06):
      `core/worker_main.py` (stdlib-only child, EC28 fd-dup + fd1→fd2
      for raw writers) + `core/worker.py` (per-module pool, serial
      lock, timeout-kill-respawn, crash isolation, CREATE_NO_WINDOW) +
      engine `_run_python` (dict-keyed outputs, python-assert →
      asserts_failed + assert_result + python_error events, FR-506);
      `python.interpreter` resolution (FR-108). TR-6 green; TR-9
      print-flood half green; rule-2 re-fire proven through a real
      multi-param function. Engine/CLI now thread `flow_dir` +
      `workspace_root`; unwired-error-port reports carry the payload's
      cause. (FR-108/506/901–906)
- [x] **M3 — simple frame-local nodes** (landed 2026-07-06): switch
      (first-match), set/get (unset get = `variable_unset` error, never
      null), log (+ live stderr echo in `napf run`), fixture (per-run
      cache keyed by path, CSV pins, rule-6 auto-seed through the
      normal firing path), note runtime no-op. `napf run flows/smoke`
      passes offline on a fresh `napf init` → EC34 first-touch test
      green. Semantics pinned in EN §5. (FR-507/512/513/514/517)
- [x] **M4 — guards** (landed 2026-07-06): counter (EC16
      check-then-decrement, `count: 0` exhausts everything) + timeout
      (lazy monotonic deadline), `reset` absorbed silently (rule 4),
      frame-local guard state, `guard_tripped` events. TR-4 green;
      FR-403 complete (all six firing rules); flagship
      retry-until-ready runs against a local server in both the
      polls-until-ready and gives-up variants — the S3 DoD's "flagship
      example runs" is done. (FR-509/510)
- [x] **M5 — subflows + loops** (landed 2026-07-06): hierarchical
      frames — one pump/budget/quiescence detector, per-frame
      `in_flight` + `done` completion, path ids (FR-404); flow node
      with subtree-computed implicit error payload (FR-516, D21); loop
      node with index-ordered results, EC06 error entries,
      fresh_session cookie isolation (FR-515); `napf run` gate
      deepened to the reference closure (`check_run_closure`).
      Completed TR-3 (cross-frame), TR-5, TR-8, FR-405/410. Stage
      close: 0.1.0.dev3. (D20/D21/D24)

S3 DoD verified: flagship retry example runs (M4); `napf run
flows/smoke` passes offline on a fresh `napf init` (M3, EC34);
TR-1/4/5/6/8/10 green + TR-9's protocol-integrity half (the
through-the-server half is S4's, per the S3 DoD note above).

## S4 — server + UI canvas  ← current

FR-10xx; canvas edits keep golden diffs clean.
DoD: `napf ui` end-to-end on macOS + Windows + Linux (incl. TR-9).
v1 `napf ui` opens the default browser (stdlib `webbrowser`, no GUI
deps); Chromium app-mode `--app` is a v1.1 candidate (PRODUCT roadmap).
Per PRODUCT.md: S1–S3 is a shippable CLI-only product — S4 must not
block a first release. Owner call 2026-07-06: the first release ships
AFTER S4 — hand-authoring YAML is too much friction for a fresh
release; the canvas is the on-ramp.

Milestone breakdown (adopted 2026-07-06) — server-first: the BlackSheep
adapter is pytest-testable with zero frontend and surfaces the TR-9
Windows risk (Proactor + worker pipes + uvicorn coexistence)
immediately; the wheel walking-skeleton de-risks NFR-03 before real UI
work exists; the canvas then grows read → edit → run. Playwright
browser tests grow per milestone alongside the canvas (owner call —
harness lands M2, suite grows M3–M6).

- [x] **M1 — server: API + runs + WS + `napf ui`** (landed 2026-07-06):
      `core/runprep.py` (gate + env + stream wiring shared verbatim
      with `napf run`), `napflow/server` (REST + run registry +
      WebSocket; frames = JSONL lines via one `encode_record`, D13),
      `napf ui` (port 6273 + scan, localhost-only, `--no-browser`).
      TR-9 through-the-server test on the 3-OS matrix; surface pinned
      in WM "Server surface". (FR-806/1001 server halves; TR-9; NFR-04)
- [x] **M2 — UI scaffold + packaging walking skeleton** (landed
      2026-07-06): `ui/` (Vite 8 + React 19 + TS + Zustand +
      @xyflow/react 12, npm, Node 22 dev-only) hello-canvas fed by the
      real API; vite builds into `src/napflow/server/static`, hatchling
      `artifacts` forces the gitignored bundle into sdist+wheel; CI `ui`
      job (3-OS: build + wheel gate + Playwright chromium e2e) and
      release.yml bundle gate; Playwright harness (`e2e/serve.mjs`
      scaffolds a fresh `napf init` workspace) + 2 smokes. (NFR-03;
      FR-806/1001 complete)
- [x] **M3 — read-only canvas** (landed 2026-07-06): flow-detail API
      grew per-node port surfaces (`checker.node_surfaces`, AST-derived
      python ports server-side, EC14); `ui/src/graph.ts` pure mapping
      (layout: coords, BFS-column fallback, handles grown from wired
      edges for merge/null surfaces) + custom node (labeled D11-colored
      handles, required markers, E/W badges), flow-list sidebar with
      `main:` default + pathname deep links, read-only inspector,
      diagnostics panel; E-code flows render diagnostics instead of a
      canvas. Vitest (5) + Playwright grown to 8. (FR-1002 render half;
      FR-1006 check half)
- [x] **M4 — editing + write path** (landed 2026-07-07):
      `loader.merge_flow_document` — surgical merge of the validated
      FlowFile dump into the ruamel doc (nodes by id, edges by
      (from,to); no-op saves byte-identical, layout-only moves diff
      only `layout:` — the golden canvas-diff test, comments survive);
      write endpoints (`PUT /api/flows|code/*`, `GET /api/etags/*`)
      with sha256-prefix etag concurrency (409 → reload/overwrite,
      last-write-wins); editable canvas — drag/connect (E004
      auto-replace)/delete/add + debounced ~1s autosave (owner fork);
      per-type config form descriptors (`forms.ts`), Start/End port
      editors, whole-file nodes.py editor (syntax reported via AST,
      saves anyway; plain textarea in v1 — Monaco deferred, see
      JOURNAL); external-change etag polling (~2s) instead of a native
      FS watcher. 10 merge tests + 10 server write tests + 7 Vitest +
      8 Playwright editing e2e. (FR-1002 edit half; FR-1003/1004/1006;
      FR-203 canvas enforcement)
- [ ] **M5 — run on canvas + history**: run button + live event overlay
      over the M1 WebSocket, per-node status + full wire detail, run
      history browser replays any JSONL (EC20 dangling
      `request_started` tolerated). (FR-1005)
- [ ] **M6 — subflow UX + stage close**: drill-in navigation, "used in
      N places", clone-to-new-flow, ghost-wires for cross-node template
      references; 3-OS DoD sweep; version 0.1.0.dev4. (FR-1007)

## Working agreements

- Conventional commits (`type(scope): subject`) — they feed git-cliff.
- Spec updates land in the same PR as the behavior change (CLAUDE.md).
- Tick REQUIREMENTS checkboxes in the landing PR, with a test.
- One dated journal entry per milestone: `docs/JOURNAL.md`
  (done / decided / next).
- New edge cases → `EDGE_CASES.md` (EC38+); new decisions →
  `DECISIONS.md` (D26+).
- Version bumps `0.1.0.devN` in the commit that completes a stage;
  releases are tag-driven (`RELEASING.md`, adopted 2026-07-05).
