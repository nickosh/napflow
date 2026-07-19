# napflow — Development Plan (v0.x)

Status: v0.1 build stages adopted 2026-07-02 and completed 2026-07-09;
v0.2 plan adopted 2026-07-11 and shipped as `v0.2.0` on 2026-07-15.
From 2026-07-15 planning is **rolling** (D41): see the
"Rolling delivery" section below — features are prioritized and delivered
independently; the owner cuts releases when accumulated value warrants.
`REQUIREMENTS.md` defines testable scope; this file defines order and
definition-of-done. Tick boxes as work lands; append course corrections,
don't rewrite history.

## S1 — loader, models, `napf check`  ✅ done 2026-07-05

Deliverable: `napf init` / `napf list` / `napf check` usable in CI.

- [x] **M0 — Repo scaffolding** (landed 2026-07-04; CI green on
      ubuntu/macos/windows)
  - [x] `pyproject.toml` (uv-managed), `napflow/{core,cli}` package layout
  - [x] pytest + ruff; GitHub Actions matrix macOS/Windows/Linux from
        day one (NFR-02)
  - [x] import-linter: `core` imports nothing from cli/server (NFR-01)
  - [x] Changelog toolchain: `cliff.toml` committed + `CHANGELOG.md`
        (Keep a Changelog format via git-cliff; conventional commits
        already in use since the first commit) (NFR-11)
  - [x] Working journal live: `docs/JOURNAL.md` + the CLAUDE.md rule
        (dated entry per milestone / useful development slice). SessionEnd
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
- [x] **M5 — CLI** (landed 2026-07-05): `napf init` (the historical
      `flows/smoke` + fixture scaffold now lives behind `--example`, D44;
      written through the canonical
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
      passes offline on a fresh `napf init --example` → EC34 first-touch test
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
flows/smoke` passes offline on a fresh `napf init --example` (M3, EC34);
TR-1/4/5/6/8/10 green + TR-9's protocol-integrity half (the
through-the-server half is S4's, per the S3 DoD note above).

## S4 — server + UI canvas  ✅ done 2026-07-09 (0.1.0.dev4)

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
      scaffolds a fresh `napf init --example` workspace) + 2 smokes. (NFR-03;
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

      **M4 leftovers** — planned for M4, consciously deferred; all
      resolved 2026-07-08 (leftovers session):
      - [x] **Code editor upgrade**: the M4 plan said Monaco; both
        Monaco and CodeMirror 6 were built and measured — CodeMirror
        won on shipped bytes (~458KB lazy chunk vs ~3.6MB+worker+font)
        and default-browser input reliability (D27).
        `CodeMirrorPane.tsx` behind the unchanged `/api/code`
        GET/PUT+etag contract.
      - [x] **Drag-from-palette**: palette entries draggable onto the
        canvas, node lands at `screenToFlowPosition` drop point
        (dataTransfer `application/x-napflow-node-type`); click-to-add
        kept.
      - [x] **Structured editors for `assert.checks` /
        `switch.cases`**: `StructuredRows.tsx` row editors (kind/op
        selects, per-kind fields, native-typed value cells,
        min_length=1 enforced by disabling the last remove).
      - [x] **Typed Start-port defaults**: the default cell parses per
        the declared port type (number/boolean/object/list native,
        `any` = JSON-with-string-fallback); unparseable text stays
        local with a red border, never saved.
      - [x] **W102 hint at connect time**: `ConnectHint.tsx` — live
        panel during a connection drag when both port types are known
        and differ (`typeMismatch` in graph.ts); soft, never blocks
        (D11). FR-1002's W102-hints clause now met in spirit too.
      Also fixed here: the M3 canvas e2e broken by the M4 "E-codes
      don't 400" pin (split into editable-with-E-codes +
      unloadable-shows-error-view against a new fixture), which had
      all three `ui e2e` CI legs red after M4.
- [x] **M5 — run on canvas + history** (landed 2026-07-08): RUN MODE
      (D29, owner fork) — editing locks, the canvas animates off the
      M1 WebSocket (JSONL lines verbatim): `runview.ts` pure reducer
      (records → per-node status/firing counts, per-edge travel
      pulses, live log values; root-frame scope, child frames roll up
      to their container node), breathing/flash node animations +
      travelling-dot wires (`RunEdge`), run controls with env dropdown
      + hybrid inputs popover (immediate when no Start ports,
      prefilled typed popover otherwise), bottom run panel (state
      chip, live assert tallies, event stream with expandable full
      wire detail, abort, node-click filtering), history tab replaying
      any JSONL incl. `napf run`'s (EC20 dangling `request_started` →
      `incomplete`, settled). 14 Vitest reducer tests + 5 Playwright
      e2e (live pass, fail + live log, input override flips outcome,
      abort mid-delay, history replay + EC20). Server untouched — the
      M1 surface carried the whole milestone. (FR-1005)
- [x] **M5.5 — run-mode inspection polish** (adopted 2026-07-08, owner
      request after driving M5; landed 2026-07-09): small/medium
      follow-ups, all pure UI over data the events already carry — no
      engine or server change. Pins made while landing: an ARRIVAL
      does not flash the target node (input-port paint skips the
      lastSeq bump — arrival is not a firing); a port's click target
      is the label + handle only, so a node's empty middle stays a
      node click; retry errors stay visible on the request summary
      while the next attempt runs. Tests: 6 new Vitest reducer cases
      (`runview.test.ts` — port traffic both ends, ring cap, request
      summary, matchesTraffic) + extended run e2e (`run.spec.ts`
      passcase/failcase).
  - [x] **Port traffic painting**: input/output handles that carried
        data glow in run mode; tooltip shows the last value that
        crossed (from `message_emitted` — `from_port`/`to_port` name
        both ends; `value_preview` is the NATIVE value up to 512 chars
        of compact JSON, truncated marker beyond).
  - [x] **Wire/port click → crossed messages**: selecting a wire (or a
        port handle) in run mode lists the messages that traversed it
        (value, ts, msg_id, count) — the wire-level twin of the
        node-click event filter.
  - [x] **Log nodes append**: a capped ring (last 50, `LOG_RING`) per
        log node instead of latest-only — the node shows newest +
        count, the full accumulated list in the run inspector on
        click. The loop-debugging view.
  - [x] **Run-mode inspector**: the right panel returns during run
        mode (`RunInspector.tsx`) showing the selected node's run data
        (per-port last values, log history, request summary, firing
        count) instead of disappearing entirely; no selection shows
        the run summary.
- [x] **M6 — subflow UX + stage close** (landed 2026-07-09): drill-in
      (double-click / inspector button on flow+loop nodes; pure
      navigation, popstate returns; statically-known targets only),
      "used in N places" (`used_by` in the flow-detail payload, links
      on the flow-header inspector), clone-to-new-flow
      (`POST /api/flows/clone` folder fork + the invoking node
      repoints to the clone — D31), ghost-wires
      (`templating.referenced_nodes` Jinja2-AST extraction →
      `template_refs` → dashed view-only edges with conditional
      invisible anchors). Playwright grew `subflow.spec.ts` (owns
      flows/parent+child+ghostcase). 3-OS DoD sweep + 0.1.0.dev4 in
      the stage-close commit. (FR-1007)

S4 → release path (owner call 2026-07-08): `0.1.0.dev4` at stage
close, a manual-testing window on the dev4 checkpoint, then the SAME
scope promotes to **v0.1.0** via the RELEASING flow (dev4 is the de
facto release candidate; only release preparation lands between). After
v0.1.0, work moves to independently verified feature delivery.

## v0.2.0 — usable full-fidelity prototype

Adopted 2026-07-11 after the first working-version architecture review.
`v0.1.0` is deliberately allowed to ship first as the working milestone.
Replanned 2026-07-13 after M4 review: v0.2 remains a correctness-focused,
full-fidelity prototype release, while security-grade local storage, run
bundles, advanced replay, and expanded performance gates move to the future
ledger. Details and rationale: D33–D39. Requirements: the v0.2 subset of
FR-11xx, NFR-12–17, and TR-11–22 below.

### Outcome

v0.2 keeps the current product and technology stack, but makes its
boundaries honest and composable:

- full request/response fidelity without copying large data through
  every layer or silently truncating it;
- replay that remains the same durable recording while paging, loading
  content lazily, and drilling into frames;
- deadlines, aborts, workers, loops, and cleanup that remain correct
  under adversarial inputs;
- one workspace containment policy and durable, conflict-safe editing;
- local-first clarity: raw local history with optional declared-secret
  masking for terminal/report presentation;
- a public functional + Workspace/Flow API and deterministic installable
  artifact suitable for real prototype use before more hardening is justified.

### Scope rules

1. **No rewrite.** Keep Python/asyncio, BlackSheep, niquests, JSONL,
   React, xyflow, Zustand, CodeMirror, and the single-wheel product.
   Refactor ownership boundaries, not technology for its own sake.
2. **No silent data loss.** Execution values stay semantically complete;
   persisted large content is stored once and referenced. The prototype
   does not add a separate omission/export policy before real demand.
3. **Replay remains a recording.** Never re-execute a historical run.
   JSONL order is canonical; blobs/indexes are attached/derived data.
4. **v0.x is experimental.** v0.2 may break v0.1 flow/event/API shapes;
   document the break and prefer a simple read-only adapter where cheap,
   but do not preserve a faulty design at the cost of the next one.
5. **Every v0.2-owned review reproduction becomes a regression test before
   or with its fix.** Deferred targets stay named in the future ledger rather
   than remaining artificial release blockers. No checkbox closes from code
   inspection alone.

### Former post-v0.1 backlog disposition — no item dropped

The earlier R1–R6 block was replaced by the sequenced plan below, not
discarded. This mapping is the continuity ledger:

| Former item | v0.2 disposition | Reason |
|---|---|---|
| **R1 timeline scrubber replay** | **After v0.2; retained in the future ledger.** Real `ts` deltas, speed multipliers, deterministic prefix folding, and derived checkpoints remain the intended feature. | Basic paged/lazy replay lands first; the richer playback UX belongs to a later product stage. |
| **R2 pause/resume + step** | **After v0.2, item 1; D30 remains authoritative.** | The fair M2 dispatch lifecycle is its prerequisite, but control-plane semantics should not expand the hardening release. |
| **R3 wire breakpoints** | **After v0.2, item 2; D30 remains authoritative.** | It still rides R2's runtime wire-hold gate and never enters `flow.yaml`. |
| **R4 opt-in full-payload capture** | **Superseded by the stronger M4 / D34 design, not lost.** The 512-character preview limitation is removed through prepared-request capture and store-once full-fidelity blobs; M0 still measures thresholds first. | Owner chose content reliability as the local default. A general hard-limit/omission policy is future D39 work rather than a v0.2 branch. |
| **R5 pack selection to new flow** | **After v0.2, item 3; D31 remains authoritative.** | Boundary inference, Python splitting, template refs, and variable rewrites remain a separate refactoring feature. |
| **R6 performance guard suite** | **After v0.2; baselines retained.** The 100k-event replay target and expanded storage/replay performance gate remain explicit future candidates. Existing bounded-loop evidence stays valid. | Prototype correctness and a usable installed path matter first; replay scale is re-evaluated when real histories justify the target. |

### M0 — release boundary, format decisions, and regression harness

- [x] Release `v0.1.0` as the first working developer-preview milestone:
      package version/tag match exactly; release notes state trusted
      workspaces, localhost-only operation, experimental `napflow/v1`
      and event formats, and no v0.x compatibility guarantee. This is
      release preparation, not a v0.2 blocker or feature backport. (D33)
      — shipped 2026-07-11 via release.yml: tag↔version hard gate, PyPI
      trusted publishing, and `docs/release-notes-preamble-v0.md`
      prepended to every v0.x release's notes (the required wording,
      automated). Reproduced again from a clean `git archive v0.1.0` on
      2026-07-12: UI build + sdist/wheel, exact `0.1.0` metadata/tag, UI
      bundle membership, isolated install, `napf init`, and smoke run green.
- [x] Add a version to the run-history envelope/manifest before changing
      storage. Pin canonical event ordering, blob-reference shape,
      inline threshold semantics, byte/hash rules, and the disposable
      index contract in the engine spec. (FR-1101, D34) — `run_started`
      is now the versioned envelope header (`format: "napflow-run/1"`,
      `HISTORY_FORMAT`). Structural envelope fields bypass secret masking;
      malformed/newer formats fail through REST and finished-run WebSocket
      replay; the parser accepts only ASCII majors and raises one stable
      `HistoryFormatError` family. The envelope feature registry is empty in
      M0 and rejects future `content-blobs/1` histories until M4 can resolve
      them. Engine spec §7a pins canonical order, collision-safe `$napflow`
      blob/literal/omitted envelopes, exact byte codecs/hash/threshold rules,
      retention units, and disposable indexes.
      FR-1101 itself stays open — blobs/indexes become real in M3–M5.
- [x] Turn the audit probes into failing tests: public import; clean-tree
      wheel; entry/ref/fixture/history traversal and symlinks; inline
      deadline/abort; 70KB worker response; late timeout side effect;
      external cancellation cleanup; capture bypass; secret value
      `passed`; >64KB final event; same-second retention; immediate
      navigation/close during autosave; overlapping saves. (TR-11–22)
      — `tests/test_v02_audit.py`: 9 strict-xfail cases cover public import,
      entry symlink escape, 70KB worker failure, external-cancel cleanup,
      Log persistence, `passed`/`error` protocol corruption, >64KB final
      event, and same-second retention. Every probe constrains its expected
      failure; the cancellation probe synchronizes with the child and always
      reaps it. Twelve explicit-owner skips name the remaining M1/M2/M4/M6
      boundary, lifecycle, blob-round-trip/collision, distribution, and
      autosave cases, so none can disappear from the ledger silently.
- [x] Record before-change performance behavior for guarded inline message
      throughput; worker results at 1KB/100KB/10MB (successful timings where
      supported, otherwise an explicit bounded failure baseline); parallel
      loops at 100/10k/100k with uninstrumented timing separate from peak
      heap; and 10MB/100MB server replay plus built-browser first-render/
      retained memory. Baselines inform batching and inline-blob thresholds;
      they are not correctness gates. (NFR-08, future NFR-18 after D39)
      — `tests/test_perf_baselines.py` + `npm run perf:history`, both opt-in
      and ordinary-CI-excluded; full numbers in `docs/perf-baselines.md`.
      Before headlines: ~44.6k guarded laps/s; teardown-inclusive 1KB worker
      21.5ms, with 100KB/10MB reader-limit failure lifecycles at 22.2ms/4.04s;
      100k loop 3.41s and 485.1MB peak; 100MB server replay 0.204s/194.4MB
      peak; 100MB browser first render 810ms median/+99.8MB retained heap. NFR-08/18
      remain recorded; D39 moves the expanded comparison and replay-scale
      target out of the v0.2 release gate.

M0 DoD: v0.1 can be reproduced from its tag; v0.2 formats and invariants
are written before implementation; every confirmed critical/high audit
finding has a named failing test or an explicit later milestone owner.
**Met and adversarially revalidated 2026-07-12**: all four
boxes are evidenced — clean-tag v0.1.0 artifact + first-touch run; protected
and enforced history envelope plus collision-safe pre-storage contract; each
audit finding a signal-correct strict `xfail` or explicit-owner `skip`; every
named performance size measured with no deferred slot. (Owner call 2026-07-12:
v0.2 lands as larger independently verifiable slices, not one slice per
milestone.)

### M1 — workspace boundary and durable editing  ✅ done 2026-07-12

- [x] Introduce `WorkspaceResolver` as the sole path
      authority for entry flows, flow/loop references, fixtures, run
      logs, source reads/writes, and clone destinations. Validate lexical
      identity and run-id format, resolve symlinks, and require resolved
      containment. Remove scattered raw `root / Path(user_value)` joins
      and `_safe_identity` as a partial policy. Rejections crossing this
      boundary use the stable `workspace_boundary` preparation/API reason.
      Implemented in `core/workspace.py` and threaded through checker,
      engine, CLI, history, and server paths. (FR-1107, D37, EC38)
- [x] Keep the server loopback-only; validate loopback Host and same-origin
      mutation/WebSocket requests. Do not add users, sessions, OAuth,
      capability-token lifecycle, or a public bind flag. (FR-1108, D37,
      EC51) Loopback IPv4/IPv6/localhost authorities pass; foreign or
      malformed Host/Origin is rejected before handlers/WS accept.
- [x] Add one atomic-write primitive: same-directory temporary file,
      UTF-8/LF emit, flush, atomic replace, preserved permissions, and
      cleanup. Serialize ETag-check + write per file so two accepted
      requests cannot race. Use it for flow YAML and nodes.py. (FR-1109)
      The shared primitive covers serializer/scaffold/editor/clone source
      writes; failed clones remove their unaccepted destination.
- [x] Replace independent debounce timers with a serialized save
      coordinator for canvas and code. Edits made during a save queue
      behind it; flow navigation, editor close, and `beforeunload` flush
      or visibly prompt; stale responses cannot overwrite current state.
      Split persistence/navigation from the global Zustand store enough
      to unit-test it. (FR-1110, EC46)
- [x] Define a portable flow-identity grammar or encode/decode every URL
      segment consistently; cover spaces, `#`, `%`, `?`, nested flows,
      Windows drive syntax, and browser back/forward. (FR-1111) Canvas
      deep links use `/flow/<encoded-identity>` so valid `api/*` and
      `assets/*` identities cannot collide with server routes.

M1 DoD: no API/checker/engine path can escape the selected workspace,
including through symlinks; crash/interruption cannot leave a truncated
source file; navigating or closing within the debounce window loses no
accepted edit; the existing comment-preserving golden diffs stay green.
**Met 2026-07-12:** the resolver/path matrix, atomic interruption and
concurrent-ETag tests, persistence unit tests, and real-browser navigation,
close, unload, conflict, reserved-identity, and back/forward cases pass;
the full Python/Vitest/Playwright suites and canonical round-trip tests are
green. The trusted-workspace model still does not claim protection from a
separate malicious local process racing filesystem entries (D37).

### M2 — fair scheduler, cancellation, and worker lifecycle  ✅ done 2026-07-13

- [x] Process ready inline deliveries in bounded batches (initial tuning
      range 64–256), then cooperatively yield and check an explicit
      monotonic run deadline/abort flag. Preserve the quiescence sentinel
      and message-budget semantics. A tight guarded cycle must not freeze
      the ASGI loop or complete past a deadline as `passed`. (NFR-12)
- [x] Put cancellation/cleanup ownership in `try/finally`: firing tasks,
      HTTP sessions, event streams, and workers close before external
      `CancelledError` escapes. Server shutdown and caller cancellation
      use the same cleanup path. (NFR-13)
- [x] Separate normal worker shutdown (EOF) from timeout/crash teardown.
      Timeout immediately terminates, waits one grace interval, then
      kills; replacement cannot start while the old worker can still
      commit work. This milestone owns the worker process itself;
      descendant process-group/Job-Object cleanup remains EC22 after
      v0.2. (D36, EC09, EC43)
- [x] Preserve D32's stdlib JSON-lines protocol. Configure and document an
      adequate line/reader limit (or explicit maximum message with a
      clean protocol error), handle `LimitOverrunError`/reader failure,
      and test large results and stderr without hanging finalization.
- [x] Make checker and worker callable surfaces agree: either implement
      async functions and positional-only arguments or reject them with
      positioned diagnostics. Strictly validate binary envelopes/base64
      and route encoding failures through the request error port. (EC48)

M2 DoD: deadlines and aborts interrupt inline cycles; external
cancellation leaves no task/session/worker behind; a valid 70KB+ Python
result completes or fails as a documented node error; a timed-out worker
cannot produce a late side effect or overlap its replacement.
**Met 2026-07-13:** the pump yields every 128 ready deliveries and checks
one monotonic deadline; one shielded cleanup task owns firing tasks, HTTP,
workers, and event streams across abort, timeout, server shutdown, caller
cancellation, cleanup, and final emission. Worker normal EOF is separate
from immediate abnormal terminate→grace→kill; replacement waits for reap;
the stdlib wire has a tested 16 MiB record ceiling. Checker/worker reject
async and positional-only callables consistently, and malformed binary
envelopes route once as request errors. The five M0 M2 probes are ordinary
passing tests; the TR-13–15 matrix, full 425-test Python suite, Ruff/import
contracts, real-uvicorn worker path, and 1 KiB/100 KiB/10 MiB worker probes
are green. EC22 descendant cleanup and EC35 synchronous-Jinja preemption
remain explicitly after v0.2.

### M3 — bounded execution state and history lifecycle  ✅ done 2026-07-13

- [x] Replace parallel-loop `gather(one task per item)` with a bounded
      producer/fixed task set while preserving index-ordered results and
      `max_concurrency`. Compact finished child frames after emitting a
      reconstructable frame summary (parent, target flow, loop index,
      timing, state, outputs/errors/assert counts). (NFR-14, D36)
      **Met 2026-07-13:** fixed workers and normal-quiescence frame release
      emit canonical `frame_finished` records with nested outcome rollups.
      The 200-item regression proves ordering/detail; the opt-in 100k gate
      proves exactly 16 helpers and at most 16 live Frames; abort coverage
      proves only active-concurrency children exist and no false D18 summary
      is emitted.
- [x] Bound live subscriber queues and disconnect/resync slow consumers;
      replay late subscribers from the durable log instead of retaining
      an unlimited prefix in RAM. Keep running-run summaries bounded.
      **Met 2026-07-13:** the registry keeps only scalar `last_seq`
      plus at most eight 256-record subscriber queues. Synchronous cutoffs and
      `last_sent_seq` disk catch-up prevent gaps/duplicates; sends time out,
      cross-process filesystem leases exclude active readers from whole-unit
      retention, and finished WebSockets stream from disk. Server summaries
      are scalar; browser pending records and the retained event window are
      bounded. Exact overflow/catch-up and older-reader/newer-retention races
      are regression-tested.
- [x] Remove the CLI's unconditional all-event `ListSink`: no report means
      no report buffer; JSON/JUnit retain only their required event
      classes or stream from the durable log.
      **Met 2026-07-13:** the CLI no longer installs `ListSink`; JSON
      retains only the final summary and JUnit streams assertion cases from
      the closed JSONL with bounded counters; a 100k-assertion disk-backed
      memory regression covers the former growth path. XML attributes are
      sanitized and reports publish atomically without following symlinks.
- [x] Make run retention operate after completion on whole run units,
      never active files. Use truly chronological metadata/IDs; delete
      JSONL, blobs, indexes, `.report.json`, and `.junit.xml` together.
      Replace the fixed-64KB tail assumption with a summary/index or a
      robust backward record reader. (EC47)
      **Met 2026-07-13:** internal active/complete/incomplete lifecycle
      metadata now gates post-adapter retention; a locked monotonic order is
      immune to same/equal/backward clocks. Canonical-tail revalidation,
      exact symlink-safe companion cleanup/publication, resumable deletion
      claims with registry eviction, legacy fallback, and arbitrary-size
      backward record reading are covered.

M3 DoD: task/frame/RAM growth is proportional to configured concurrency
and active presentation windows rather than total loop/history size;
slow WebSocket clients cannot grow the server without bound; retention
never deletes an active/newer run or leaves companion artifacts.
**Met 2026-07-13:** fixed loop workers/frame compaction, bounded server/CLI/
browser presentation state, exact durable catch-up with per-send ceilings,
filesystem-reader-leased whole-unit retention, and the TR-19 100k/abort
evidence are green. Full REST paging/lazy replay was deliberately left to M5,
not retained active state in M3; M5 below now closes that remaining surface.

### M4 — full-fidelity history and prepared requests ✅ done 2026-07-13

- [x] Implement the D34 store: small JSON-compatible values inline;
      large text/binary/structured content stored once in immutable
      content-addressed blobs with hash, bytes, media type, encoding,
      and explicit reference records. Verify the stored hash round-trip.
      Deduplicate repeated appearances inside a run. (FR-1102, NFR-15)
  - [x] **Codec/store foundation** (landed 2026-07-13):
        `core/history_content.py` pins the 64 KiB inclusive threshold,
        exact UTF-8/JSON/raw-binary bytes, collision-safe literal and
        explicit omission envelopes, per-run immutable hash paths,
        deduplication, and strict missing/corrupt/hash-verified resolution.
        At that foundation slice, feature activation intentionally remained
        below and its tests did not claim FR-1102/NFR-15/TR-16 completion.
        D39 keeps the codec, hashes, and deduplication; the permission/owner
        hardening was removed in the focused slice below.
  - [x] **Feature activation + event integration:** encode once before the
        shared JSONL/WebSocket fan-out, and enable `content-blobs/1` for both
        writer and reader in that same change. Partial field activation would
        make a later inline `$napflow` user value ambiguous and is forbidden.
    - [x] **Field-policy/redaction seam** (landed 2026-07-13): every
          then-current event dataclass field was classified as structure,
          complete content, keyed content, error-message content, or derived preview. Canonical
          sinks receive raw records; JSONL uses ordinary OS/workspace
          permissions and the WebSocket is a trusted loopback-local surface.
          Terminal delivery and post-close JSON/JUnit rendering use the same
          schema-aware value-only redactor. At this intermediate seam,
          `body_preview` and `value_preview` were explicit activation blockers,
          not silently accepted content fields; the completed slice below
          removes them. D39 retains the field registry and raw/presentation
          split.
    - [x] **Full-value schema + activation** (landed 2026-07-13): replace the
          two derived previews with prepared-request/full-message contracts, apply the store
          through the registry, add lazy consumer resolution, then activate
          the writer/reader feature together. Store-backed production streams
          now advertise `content-blobs/1`; featureless legacy values remain
          literal, and report/server/UI consumers resolve or retain references
          only at their actual presentation boundary.
- [x] Apply storage policy to every persisted payload path—not only
      `request_finished.body`: message/log values, request bodies,
      response bodies, error payloads, and `run_finished.end_outputs`.
      Execution values remain complete and independent of their persisted
      representation. Remove misleading run/body valves once migration
      is complete. (EC32) The full response object is reused across request,
      message, Log, and End records so one canonical JSON blob/hash serves all
      appearances; the old manifest valves and engine truncation path are gone.
- [x] Capture the prepared request rather than a 512-character preflight
      preview: final URL/query, effective headers/cookies, body reference,
      timing/retry metadata, and response detail. Library-generated sensitive
      headers remain raw local observations; optional declared-secret masking
      affects presentation values only. (FR-1103, EC50) The adapter snapshots
      initial and final redirect-aware niquests requests, including encoded
      query, defaults, cookies, exact body bytes, retry/redirect totals, and
      response timing.
- [x] Simplify local-history storage to the trusted local prototype model:
      raw JSONL and blobs use normal inherited OS permissions. Remove custom
      Windows DACL/SID ownership handling, forced POSIX private modes, and
      permission-based blob rejection while retaining exclusive creation,
      workspace containment, exact structural records, and hash verification.
      Terminal/JSON/JUnit masking remains optional through non-empty
      `environments.secrets`; scaffold masking as opt-in examples rather than
      an implied security guarantee. (FR-1104, D39, EC45)
  - [x] **Permission contract removal** (landed 2026-07-13): JSONL and blob
        creation now follows POSIX umask/inherited Windows ACLs; custom DACL/
        SID ownership and chmod/fchmod paths plus permission-only blob
        rejection are deleted. Focused regressions preserve JSONL/blob
        exclusivity, workspace containment, exact records/descriptors,
        no-follow/type checks, existing-digest byte equality, size checks, and
        SHA-256 verification.
  - [x] **Raw local + terminal/report views** (landed 2026-07-13): JSONL and
        local WebSocket records preserve exact values; JSONL uses ordinary
        OS/workspace permissions and the WebSocket stays inside the trusted
        loopback UI boundary. Dictionary keys, identifiers, enums, and control
        fields are immutable; CLI stderr and post-close JSON/JUnit reports
        redact only classified content.
        An ordinary sink I/O close failure preserves the run result but
        publishes history only as an incomplete prefix; control-flow
        exceptions still propagate.
  - [x] **Scaffold opt-in examples** (landed 2026-07-13): new manifests write
        `secrets: []` and retain commented pattern examples plus the raw-history
        warning; no presentation masking is active until the user opts in.

M4 DoD: a large response routed through Log and End is stored in full
once, inspected byte-for-byte, and replayed without duplicated bodies;
no silent truncation remains; prepared request detail reflects the effective
wire request; a secret named/value-shaped like an event field cannot corrupt
replay; local history works without a custom OS permission contract.
**Met 2026-07-13:** `test_large_response_is_stored_once_through_request_log_end_and_report`
proves the 200 KB Request → Log → End → JSON-report round trip uses one
hash-verified blob while runtime data remains complete. Adapter regressions pin
effective prepared requests and redirects; schema/redaction/replay tests pin
feature gating, lazy consumers, marker collisions, and structural integrity.

### M5 — usable paged replay and frame drilldown

- [x] Version replay APIs and page event records rather than returning one
      unbounded JSON array. A simple cursor/sequence contract is sufficient;
      no derived seek index or advanced filter matrix is required for v0.2.
      Canonical JSONL remains unchanged. (FR-1106)
- [x] Fetch blobs lazily. The browser keeps a bounded/virtualized event
      window plus reduced node/frame summaries, not every full record.
      Opening detail resolves the blob and verifies/handles missing data.
- [x] Reconstruct the frame tree from durable events and summaries.
      Root canvas loads first; subflow/loop iteration detail loads when
      expanded. Runtime frame compaction must be invisible to replay.
- [x] Preserve EC20 behavior for genuinely incomplete runs while using a
      durable final summary to distinguish them from one large final line.

M5 DoD: a representative full-fidelity run opens without returning every
event or fetching every blob, keeps the active browser window bounded, and
drills into completed child frames without re-execution. Timeline playback,
checkpoints, advanced seeking, and the 100k-event replay target remain named
future work.

**Met 2026-07-13:** `test_replay_api.py` pins bounded/versioned pages,
full-snapshot node/edge projections, scalar frame pages, frozen lifecycle
boundaries, strict sequence/UTF-8 validation, lazy typed content failures,
large finals, and EC20 prefixes. Store/Vitest coverage proves a 1,101-event
history opens with one event/frame page and explicit continuation; Playwright
opens a real 72 KiB child result, swaps to its completed frame canvas without
re-execution, and performs exactly one detail read only after expansion.

### M6 — public/package/UI contract completion  ✅ done 2026-07-13

- [x] Implement and document an ergonomic stable-for-v0.2
      Python embedding surface. `from napflow.core import run_flow` remains
      the functional entry point; `load_workspace(path)` exposes immutable,
      reusable workspace-bound Flow handles through `workspace.flow(identity)`,
      fresh discovery, and a runtime `workspace.flows` catalog so identifier-safe
      identity segments become attribute chains below the configured flows root
      (`workspace.flows.<identity segments>`). Exact identity lookup remains
      available for every legal name and collision.
      `Flow.run(...)` and its async counterpart create a fresh isolated run each
      time and delegate to the same preparation/execution/cleanup path as
      `run_flow`; no server/CLI import or shared mutable run state. Cover multiple
      workspaces, multiple flows per test, nested names, attribute-invalid names,
      flow/namespace overlap, env/input isolation, cancellation, and installed-
      wheel pytest use. Runtime discovery may improve `dir()` completion but does
      not claim filesystem-derived static typing. (D38, FR-1112, EC42)
- [x] Make installation honest by supporting release-built PyPI/GitHub wheel
      and sdist artifacts, not direct VCS installs or PEP 517 builds from a raw
      checkout. The generated UI remains uncommitted. Test the release sdist →
      no-Node wheel path, install it in isolation, and execute both the public
      API and `napf ui`. (D40, FR-1113, EC44)
- [x] Bring visual editing to schema parity for safety/template fields:
      universal `max_seconds`, template-aware number/boolean fields,
      request TLS/timeout, and typed Start defaults. Add schema-to-form
      coverage so drift fails a test. Fix abort-response status handling.
      (FR-1114, EC49)
- [x] Refactor only the live/history orchestration needed by the paged/lazy
      API. Preserve pure graph/run reducers; a standalone global-store split
      is not a v0.2 deliverable unless implementation proves it necessary.
      **Landed with M5:** one-page store orchestration keeps reducers pure and
      uses generation guards without a global-store rewrite.
- [x] Generate/audit third-party notices for bundled frontend code before
      public distribution.

M6 DoD: documented installation plus functional and workspace/Flow core
examples work from the built artifact; discovery/catalog tests cover exact,
nested, non-identifier, collision, and flow-plus-namespace identities without
shared run state; every authoritative editable schema field has a valid UI path
or an explicit documented YAML-only status; frontend orchestration has direct
unit coverage for persistence and run transport.

**Met 2026-07-13:** public API tests cover exact/fresh catalog lookup,
sync/async isolation, cancellation, inputs/envs, and multiple workspaces. A
release sdist builds a wheel with Node commands blocked; the isolated install
runs both API forms and serves every packaged/referenced compiled UI asset.
Pydantic-to-form coverage,
Vitest, and Playwright pin the editor contract, while the lockfile-derived
audited notice is present in both sdist and wheel.

### M7 — release gates, compatibility evidence, and v0.2 promotion

- [x] Reuse the existing required CI/release workflows and add only missing
      v0.2 product checks: Vitest in the authoritative gate, installed-wheel
      `napf ui`/`run_flow` smoke, production bundle membership, and exact
      tag/package version refusal. Do not add minimum/latest dependency or
      expanded OS/browser axes before user demand. (NFR-16) — one reusable
      workflow plus independently tested exact-tag/`.dev` refusal; every
      existing Linux/macOS/Windows Python/UI job passed, 2026-07-14
- [x] Close the M0 audit entries still owned by streamlined v0.2. Reassign
      placeholders for explicitly deferred export, advanced replay, or
      performance targets to the future ledger instead of treating them as
      release failures. — every v0.2-owned probe is an ordinary regression;
      the sole conditional symlink skip is a platform capability check, while
      export/performance/playback remain named future requirements
- [x] Run the focused release checks that exercise current promises: full
      Python/UI suites, existing cross-platform paths, missing/corrupt blob,
      incomplete run, prepared-request capture, public API isolation, and
      clean installed-artifact smoke. Do not introduce a separate exhaustive
      adversarial or performance matrix in v0.2. — prepared `0.2.0` tree:
      618 pytest, 76 Vitest, 43 Playwright; Ruff/import contracts, production
      build, notices, exact tag check, and release-sdist → no-Node-wheel →
      installed public API/real `napf ui` smoke all pass, 2026-07-14
- [x] Update engine/workspace/flow specs to implemented v0.2 behavior,
      publish format notes, preserve M4–M6's tested EC32/EC42/EC47/EC49/EC50
      closures, finish EC44 only with its release-gate tests, preserve M1's
      tested EC38/EC46/EC51 closures, record EC27's
      cooperative-scheduler half precisely, and retain EC10/EC22/EC35
      as named post-v0.2 limitations. — the audit also reproduced and fixed
      anchor/alias enforcement (EC53) and report/replay envelope drift (EC54)
- [x] Promote the exact prepared release: `v0.2.0` metadata, generated
      changelog, compatibility notes, and EC55 passed the reusable three-OS
      release gate, including the macOS browser path and exact artifact smoke.
      Published wheel/sdist names and metadata are exact `napflow-0.2.0`.

v0.2 DoD: every requirement still assigned to v0.2 is green; no
v0.2-targeted correctness case remains merely documented; deferred product,
security, export, and performance targets remain explicit; release artifacts
are reproducible and their version matches the tag; full-fidelity history,
basic lazy replay, the runtime flow catalog, and the installed user path work.

### Explicitly after v0.2

These remain compatible but are deliberately excluded so the prototype
release stays bounded:

1. **Pause/resume + step** (former R2, D30): dispatch gate, paused-time
   clock offsets, server/UI controls.
2. **Wire breakpoints** (former R3, D30): runtime wire holds riding the
   pause gate, never flow-file content.
3. **Pack selection to new flow** (former R5, D31): extract-to-subflow
   refactor with boundary inference and Python/template rewrites.
4. **Timeline scrubber and playback** (former R1): slider, play/pause, real
   event-time deltas, speed multipliers, deterministic prefix folding, and
   measured/rebuildable checkpoints. Keep this for a later product stage.
5. **100k-event replay performance target and expanded perf suite** (former
   R6): bounded server/browser memory, seek/first-render measurements, and
   storage/replay comparisons against the preserved M0 baselines. Re-evaluate
   the exact target when real histories exist; it is not a v0.2 gate.
6. **Run export/import and redacted bundles** (FR-1105): self-contained
   JSONL+blob archives, read-only import, raw/redacted choice, referenced-blob
   rewriting, and explicit hard-limit omission metadata.
7. **Advanced replay indexing and filters**: rebuildable byte/sequence
   indexes, timeline checkpoints, and broad frame/node/event seek filters.
8. **Fine-grained runtime-secret redaction** (EC10): register derived
   values or response field paths when safe exports become a real feature.
9. **Descendant-process cleanup** (EC22): owned POSIX process groups and
   Windows Job Objects/equivalent, with timeout/abort/shutdown tree tests.
10. **Preemptible template execution / hard deadline semantics**
   (EC27/EC35): measure first, then isolate synchronous Jinja work in a
   killable boundary or explicitly retain a cooperative trusted-code
   deadline contract with tests and precise documentation.
11. Poll/duplicate nodes, inline loop bodies, marker collect,
   `napf check --write-env-example`, per-module worker-pool expansion,
   app-mode UI, endpoint catalogs/imports, general codegen, remote
   hosting/authentication, and collaborative editing.
12. **Generated typed workspace bindings** (D38): deterministically derive a
   Python module/stub from flow discovery plus Start/End interfaces so IDEs and
   type checkers know the catalog's discovered attributes, nested flow names,
   input shapes, and output shapes. Include a stale-binding CI check and
   exact-name fallback. Do not require an editor-specific type-checker plugin
   or pretend runtime
   `__getattr__` can provide static filesystem-derived types.
13. **Separate secure-history implementation if demanded by users**: custom
    ACL/DACL policy, forced private modes, authentication/authorization,
    encryption/key management, and secure sharing belong to one explicit
    future security design. v0.2 relies on ordinary OS permissions and makes
    no secure-storage or safe-export guarantee.

## Rolling delivery — from 2026-07-15 (D41)

`v0.2.0` shipped 2026-07-15 (tag + PyPI). From this point the plan is not
version-scoped: features are planned and prioritized here, completed as
independently verifiable slices, and the owner cuts a release whenever
accumulated value warrants one — the existing tag-driven
gate (`RELEASING.md`, D33/D40) is unchanged. M-numbered version-scoped
milestone blocks end at `v0.2.0`.

**The invariant replacing "milestone done": the integrated project stays
releasable.** The full verification gate is the completion bar; anything too
large for one green slice must be divided into increments that each preserve
that invariant.

### Priority criteria (ordered; earlier criterion wins ties)

1. **Real-use pull** — removes something that blocks or embarrasses real
   daily use (the owner's own QA work first, then early adopters).
2. **Trust protection** — prevents a silent failure or foot-gun (hangs,
   orphaned processes, data loss) that would burn a user's trust once.
3. **Enabler leverage** — makes already-planned work cheaper or cleaner;
   structural splits land before the features that would pile onto them.
4. **Cost fit** — prefer slices that complete within days; large features
   must be sliceable per the invariant above.
5. **Compat window** — wanted format/API breaks are cheapest early in
   v0.x (D33); prefer sooner over later.

Demos, screenshots, and README media wait until F1 ships (owner call
2026-07-15).

F6 was selected by the owner as the first rollout implementation and completed
on 2026-07-15. Owner direction then pulled **F7** forward; it is implemented.
**F2** completed on 2026-07-18 and **F1 Slice 1** on 2026-07-19. Owner
direction then pulled **F1 Slice 2** ahead of the queued trust work; it
completed on 2026-07-19. The current order returns to **F3**, then **F4**.
**F5** remains unscheduled/low.

### F1 — UI rework for real use + visual styling (headline track)

Owner direction 2026-07-15: adapt the canvas UI for real daily use and
apply a coherent visual style. Proceeds as independent development slices
(invariant above); Slice 0 produces the authoritative slice list — the
slices named here are the expected shape, not commitments.

- [ ] Slice 0 — UX audit + design foundation: walk every surface (canvas,
      node config forms, run panel, replay drilldown, in-browser
      `nodes.py` editor, flow navigation) through a real API-testing
      task; record friction; choose design tokens (typography, spacing,
      color, dark/light) and component conventions; produce the concrete
      slice list for owner sign-off. Output is a short doc + tokens,
      minimal behavior change.
- [x] Slice 1 — split `ui/src/store.ts` (1,346 lines at split time, one Zustand store)
      into slices (canvas / persistence / run-replay) as a pure-move
      enabler, mirroring F2 on the frontend, before feature slices pile
      onto it. `RunPanel.tsx` (834 lines at review time) may split along
      the same seams.
- [x] Slice 2 — canvas undo/redo (owner request 2026-07-15). In-memory,
      per-open-canvas bounded snapshot stack over the *document* slice
      only (nodes/edges/config/layout/Start/End ports) — depends on
      Slice 1's document/session state boundary; zundo-or-equivalent
      temporal middleware. Bounds: stack cap (~100 steps) + coalescing
      (drag commits on release; config typing groups; multi-delete is
      one step); memory is per-step deltas via immutable structural
      sharing — no serialization or history-specific server round-trips.
      Guards: hidden/disabled in run mode (D29); an external flow-document
      reload clears the stack, while code-only metadata refresh does not;
      autosave needs no special handling (an undo is an ordinary state
      change through the save coordinator). Shortcuts scoped by
      focus — CodeMirror keeps its own native text undo for `nodes.py`
      and config editors. **Owner call 2026-07-15: undo history is
      never a workspace file** (git-friendliness + conflict semantics;
      git is the durable history). Snapshots stay JSON-serializable by
      construction, so optional future persistence — browser IndexedDB
      or a `.napflow/` local-history seam, never `flows/` — remains a
      cheap additive extension, not a redesign.
- [ ] Slices 3+ — per the audit, expected order of daily-use value:
      canvas interaction + node config forms; run panel + replay
      drilldown; workspace/flow navigation; `nodes.py` editing ergonomics.
- [ ] Every slice keeps Vitest + Playwright green (snapshot/assertion
      updates are deliberate and named in the change); production build and the
      frontend notices audit stay in the gate.
- [ ] Every slice keeps per-type UI behavior registry-driven — icon,
      category, description, quick fields, and card width come from
      `NODE_META` (`ui/src/catalog.ts`) and field editors from
      `CONFIG_FORMS` (`ui/src/forms.ts`); no per-type rendering
      hardcoded in components. This is F8's plugin seam, guarded by
      `ui/src/catalog.test.ts` registry coverage.

Progress 2026-07-17: the owner supplied a complete visual design as a
Claude Design handoff (Nocturne design system; owner picked the Soft
aesthetic, dark+light, reduced card shadows) — it supersedes Slice 0's
token-selection half. Landed as one slice: bundled Inter/JetBrains
Mono/Phosphor icons (local-first, no CDN; OFL-1.1 reviewed into the
notices gate), full-bleed canvas with floating chrome (flows menu +
breadcrumb, ⌘K palette, zoom cluster, minimap, bottom add/tidy/run bar,
drag-to-trash), per-type node cards (sizes, icons, always-editable quick
fields) with the FULL config editor in-card — owner call: the right
Inspector is dropped; a selected card grows to host its editors — plus a
unified console (events/history/diagnostics tabs), tidy auto-layout, and
restyled run visuals. Replay scrubber explicitly kept deferred (D39).
Scaffold layouts widened for the new card sizes. Remaining F1 slices
(undo/redo and audit-driven ergonomics) unchanged.

Slice 1 completed 2026-07-19: `store.ts` is a 24-line stable facade over
canvas/document, persistence/session, and run-replay slice factories. One
Zustand store and all existing public imports remain unchanged; `detail` has
one canvas-owned initialization, document mutations cross one injected
autosave bridge, and flow navigation applies the run reset in its original
single state update. Independent parity review found defaults, generation
ordering, socket lifecycle, reset patches, and action bodies unchanged.
`RunPanel.tsx` stayed intact because the store boundary did not require its
optional split. The full Python/frontend/package gate is green.

Slice 2 completed 2026-07-19: a store-local controller retains at most 100
immutable `FlowModel` roots and relies on path-copying actions for structural
sharing. One edit bridge records document changes only; drag release, keyed
config focus bursts, tidy, and mixed deletion form semantic steps. Toolbar and
macOS/Windows/Linux shortcuts restore through the existing autosave coordinator,
leave focused config/CodeMirror undo alone, hide and guard in run/replay mode,
and reset on flow navigation, conflict reload, or an external flow revision.
Post-save and code-only detail refreshes preserve both history and accepted root
identity. Focused temporal/canvas/persistence tests plus the full
Python/frontend/package gate are green.

Exit: the owner completes a real API-testing task in the UI without
dropping to hand-editing YAML for routine operations; only then do README
demos/screenshots land (deferred owner call above).

### F2 — `server/app.py` split by pure moves (approved 2026-07-15)

The pre-split `server/app.py` was 1,821 lines mixing four separable concerns.
It was split by pure moves — no behavior, route, contract, or logic changes —
so later work (F1 server touches, D30 controls) lands in small files.

- [x] `server/replay.py` — the replay read/view layer:
      `ReplayQueryError`, `ReplayMetadata`, `ReplaySnapshot`,
      `ReplayViewBuilder`; replay record iteration and paging
      (`iter_records`, `read_records`, `iter_replay_records`,
      `parse_replay_integer`, `parse_replay_page_query`, `parse_frame_query`,
      `metadata_from_header`, `read_replay_page`, `read_replay_event`,
      `replay_envelope`, `replay_run_summary`, `replay_frame_summary`,
      `capture_replay_snapshot`, `replay_history_state`,
      `has_external_active_marker`).
- [x] `server/ws.py` — live websocket streaming: `ws_close_reason`,
      `SlowSubscriber`, `send_ws_record`, `send_history_range`,
      `close_ws`, `stream_run_websocket`, plus the `WS_*` constants
      they own.
- [x] `server/boundary.py` — the local-request trust boundary (D37) and
      write serialization: `Authority`, `request_scheme`,
      `parse_authority`, `is_loopback_host`, `request_authority`,
      `origin_matches`, `LocalRequestBoundary`,
      `SourceWriteCoordinator`.
- [x] `app.py` keeps `build_app`, route handlers, and small response
      helpers (`_diag_payload`, `_prep_error`, `_etag`, `_bad_request`,
      `_json_object`, …).

Rules and definition of done:

- Pure moves + import updates only. Moved names drop the leading
  underscore (the module boundary now provides the privacy); no other
  renames, no signature or logic changes.
- Import direction unchanged: new server modules import `core` and each
  other downward only; import-linter contracts stay green.
- `tests/test_server.py` imports the moved helpers/constants from their owning
  modules, and `tests/test_perf_baselines.py` imports `read_records` from
  `server.replay`; those updates are mechanical — no test logic changes.
- DoD completed 2026-07-18: `app.py` is route-focused at 958 physical lines.
  Its retained `build_app`/route block alone was already 735 lines, so the
  planned "~700" estimate was incompatible with the approved pure-move
  boundary; no extra route split was invented to chase it. Token-normalized
  comparisons confirm the three extracted bodies are exact moves after the
  approved public renames, and the full local gate is green with no behavior
  diff.
- Optional follow-up change (not part of this slice): split
  `tests/test_server.py` (2,160 lines) along the same seams.

### F3 — EC22 descendant-process cleanup

Python-node workers must own their whole process tree so timeout, abort,
and shutdown cannot leak grandchildren. QA-authored nodes shell out
(subprocess calls are normal in this audience); orphaned processes in CI
are a trust burn. This is the cheapest fix in the ledger relative to its
risk. Closure bar is EC22's stated one: cross-platform child + grandchild
tree-kill tests.

- [ ] POSIX: spawn workers with `start_new_session=True` (own process
      group); replace single-PID kill with `os.killpg` on the worker's
      group at timeout-kill, cancellation cleanup, and lifecycle
      shutdown. Guard: never signal the server's own group.
- [ ] Windows: assign the worker to a Job Object with
      `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` via ctypes (no new
      dependency); close the job handle on the same three paths.
      Breakaway-flag children are best-effort — document that boundary.
- [ ] Tests: a python node spawns a grandchild that heartbeats to a temp
      file; assert child and grandchild both die on (a) per-node timeout,
      (b) external cancellation, (c) shutdown — green on all three OS
      CI jobs.
- [ ] Close EC22 in `EDGE_CASES.md` in the implementation change; until then its
      "only the worker process is covered" wording stays accurate.

### F4 — EC27/EC35 template render guards (narrows, does not close)

Cooperative deadlines cannot preempt a synchronous Jinja render, so a
pathological template still produces the worst failure shape: a hung CI
job with no report. Land the cheap guards that convert most hang shapes
into clear errors; full preemptible rendering stays in the unscheduled
backlog under EC35's stated bar.

Performance position (evaluated 2026-07-15): the guards are a pre-render
size check plus periodic checks *between* output chunks — not per-opcode
instrumentation — so the cost is O(1) per check and must be invisible
next to render cost itself. The full killable-boundary isolation is the
expensive design and is exactly what stays deferred.

- [ ] Pre-render input budget: refuse to render when the combined size of
      the template's declared inputs exceeds a generous cap; emit a clear
      templating error routed by existing error semantics (error ports,
      never data ports).
- [ ] Chunked rendering: render via `Template.generate()` instead of one
      blocking `render()`; every N chunks (batched, e.g. 256) check the
      monotonic deadline/abort state and an output-size cap.
- [ ] Perf evidence in the implementation change: the guarded inline throughput
      baseline
      (≈44.6k laps/s, `docs/perf-baselines.md`) must not measurably
      regress — record a before/after opt-in perf run.
- [ ] Honesty: a template loop that emits no output between iterations
      still cannot be preempted. EC27/EC35 stay OPEN with narrowed
      wording; the ledger update lands in the same change.

### F5 — perf drift trend job (unscheduled, low)

Not a gate. A scheduled (weekly) GitHub Actions workflow runs the opt-in
`-m perf` suite and the UI perf harness on one OS and uploads results as
workflow artifacts, so the future D39 100k-event evaluation has a trend
line instead of two points. Manual inspection only; thresholds/alerts
only if drift is actually observed.

### F6 — `napf init` git-metadata handling for existing files ✅ done 2026-07-15 (EC56; D43)

Completion evidence: 99 focused tests and 667 full-suite tests passed locally
(11 deselected), with Ruff lint/format and diff hygiene clean; the Python matrix
passed on all three OSes. The later Windows browser flake is separately
reproduced/fixed as EC57.

Brownfield init (target directory already has `.gitignore` or
`.gitattributes`, no `napflow.yaml`) previously skipped both files with a
one-line `exists` notice, leaving then-current `envs/*.env`, `.napflow/`, and YAML
`eol=lf` rules absent — committable credentials/raw history (EC56).
`napf init`'s refusal when `napflow.yaml` exists stays exactly as is.

Owner-decided and implemented behavior (2026-07-15):

- [x] Missing files: created silently — unchanged, including when existing-file
      inspection is disabled.
- [x] Coverage authority is only the canonical lines in workspace-root
      `.gitignore` and `.gitattributes`. Parent files, `.git/info/*`, global
      configuration, and a Git executable never count. The evaluator also
      checks exact fixed rules; arbitrary user patterns remain user policy.
      F7 later narrows the fixed ignore line to `.napflow/` and assigns actual
      environment-profile coverage to semantic W108.
- [x] Existing LF file with all canonical napflow rules covered: report
      `exists (rules covered)`; no prompt, no change.
- [x] Existing LF file missing rules, interactive TTY: per-file prompt
      showing exactly the lines that would be added; **default =
      append**. Decline ⇒ `skipped` + the warning below.
- [x] Existing file missing rules, no TTY (CI/scripts): **never
      mutate** — report `skipped` plus a loud warning listing the exact
      missing lines; `--git-meta append|skip` makes the choice explicit
      and also suppresses/forces the prompt interactively.
- [x] Existing file containing any CR/CRLF is never appended or normalized,
      even under `--git-meta append`: leave its bytes exact and warn. Invalid
      UTF-8, unreadable, symlink, and non-regular paths are likewise untouched.
- [x] Append mechanics: one clearly marked `# napflow` block containing
      only the missing rules; idempotent (re-append adds nothing);
      LF-only whole-file atomic rewrite via `atomic_write_text` with existing
      mode and user content preserved.
- [x] `--no-git-meta-check` on init bypasses inspection/prompt/warnings for
      existing files; `--git-meta append` conflicts with it, while explicit
      `skip` may be redundant. `napf check --no-git-meta-check` suppresses the
      advisory read-only metadata diagnostics.
- [x] `napf check` emits W109 for missing canonical root rules, non-LF or
      invalid metadata, and missing/non-regular files. It never prompts or
      writes; only `napf init` can offer or perform an append.
- [x] Status vocabulary extends to created / exists / appended /
      skipped; `scaffold_workspace`'s "never overwrites" contract keeps
      holding — append never rewrites user content, only adds the block.
- [x] Same-change docs: workspace-manifest/checker specs, D43, requirements,
      README raw-history wording, and EC56 closure reconciled.
- [x] Tests: block construction + idempotency + partial/order cases;
      CliRunner prompt flows (accept/decline/default); no-TTY skip+warn;
      `--git-meta` both values; root-only authority; both opt-outs; CRLF
      refusal; invalid/non-regular paths; read-only W109. The implementation
      passed the macOS/Windows/Linux Python matrix.

### F7 — configurable source roots + dotenv-style profiles ✅ done 2026-07-15 (D42/D44)

Owner scope expanded during implementation: all user source categories need
clear configurable roots, default init must be immediately usable without
demo clutter, and the richer reference workspace belongs behind `--example`.

- [x] `flows.root` remains a required proper workspace subdirectory;
      `data.root` is added with default `data` and the same proper-subdirectory
      rule. Fixture-node `file:` values now resolve relative to `data.root`.
      `environments.root` defaults to `.` and uniquely accepts `.`/`./`.
      Every root uses D42 lexical + symlink-aware containment.
- [x] Environment discovery is non-recursive and uniform for every root:
      collect `.env`, `.env.*`, and `*.env`; literal filename is the id in
      manifest, CLI, server, and UI. Valid foreign-created files are first-class.
- [x] Invalid/unreadable/non-regular candidates are skipped with W105/operator
      notes; explicitly/default-selected invalid or missing filenames are hard
      run-preparation errors. Process environment overrides remain unchanged.
- [x] W108 uses Git-compatible pattern semantics over only the workspace-root
      `.gitignore`. F6/W109 retains only fixed `.napflow/` + YAML attributes.
      `--example` adds one exact anchored ignore for its configured `.env`;
      `.env.example` stays visible and root changes never mutate metadata.
- [x] Basic init creates the manifest, empty Start/End `flows/main`, empty
      `nodes.py`, empty `data/`, `.napflow/`, and Git metadata—no env files,
      smoke, fixture data, or HTTP demo. `napf init --example` creates the
      complete offline/HTTP reference surface used by tests.
- [x] Init accepts `--flows-root`, `--data-root`, and
      `--environments-root`; validation and required-directory collision
      preflight happen before writes. Existing directories/files are reused
      without overwrite; non-directory/symlink/junction collisions and any
      directory role that overlaps a planned scaffold file fail before writes.
      Existing scaffold sources are preserved only when regular files; F6's
      metadata-specific inspect/skip policy remains unchanged.
- [x] Specs, README, D42/D44, requirements, EC34/EC56/EC58/EC59, and a dedicated
      embedding guide describe the break and root-ownership policy.
- [x] Tests cover defaults/custom/nested roots, root containment, literal
      patterns, invalid selection, W108 semantics/opt-out, minimal vs example
      scaffold, brownfield reuse/collisions, planned file/directory role
      conflicts, and release/server/browser use of explicit `--example`.
      Three-OS confirmation remains pending after the EC60 test-harness repair.

F7 also closes EC57, the linked Windows Playwright failure: same-flow
xyflow rebuilds preserve stable-node measurements, the E2E assertion checks
real post-Fit-View geometry, and every retry worker restores immutable editing
seeds before the serial suite.

### F8 — custom block (plugin) system (unscheduled; investigation recorded 2026-07-17)

Owner intent (2026-07-17, during the F1 redesign): let users add their
own blocks — Python behavior plus custom card presentation — within
napflow's functionality, eventually shareable between workspaces. The
`python` node already covers the *behavior* half; the gap is a reusable,
declared block: named ports + config schema + picker/card presentation,
referenced from flows as a first-class citizen. Recorded now because the
F1 redesign deliberately shaped the UI seams for it; **not scheduled** —
promote via the priority criteria with an owner decision.

Expected shape (hypothesis, not commitment):

- A block is a folder, git-friendly like flows: `blocks/<name>/` holding
  `block.yaml` + `impl.py`. `block.yaml` declares display name,
  description, picker category, icon (a NAME into the bundled Phosphor
  set — assets stay in the wheel, nothing remote), declared input/output
  ports with soft types (D11), config field descriptors (the same
  vocabulary as `ui/src/forms.ts` `CONFIG_FORMS`: kind/label/options/
  placeholder/templatability), quick-field keys, and card width.
  `impl.py` is an ordinary python-node-style function run under the
  EXISTING worker-subprocess contract (JSON-serializable I/O, declared
  inputs only, `max_seconds`, error port routing) — blocks add zero new
  execution semantics.
- Flow reference: additive, like `flow:` — e.g. `type: block` +
  `config.block: blocks/retry_probe` (or a namespaced `type:`; needs an
  owner decision + flow-schema spec update). Additive keys avoid a
  schema-marker change; cheapest inside the v0.x compat window (D33).
- UI: presentation stays DATA-driven. The server ships the block
  catalog (name, category, icon name, description, field descriptors,
  quick keys, width) with the workspace payload; the UI's per-type
  registries — `ui/src/catalog.ts` `NODE_META` and `CONFIG_FORMS` —
  fall back to server-provided metadata for unknown types. **Boundary:
  "custom design logic" means declarative presentation (icon, category,
  quick fields, width, field editors), never plugin-supplied JS in the
  browser** — that would break the built-wheel integrity and the D37
  local trust boundary.

Prerequisites, in order, before an implementation slice makes sense:

1. **F2** server split — complete 2026-07-18 (a catalog endpoint now has a
   small-module seam rather than adding to the former monolith).
2. **F1 Slice 1** `ui/src/store.ts` split — complete 2026-07-19 (the UI-side
   registry fallback now has separate store/detail seams to thread through).
3. Keep the F1 invariant that already landed: all per-type UI behavior
   flows through `NODE_META` + `CONFIG_FORMS` as *data* — no per-type
   rendering hardcoded in components. This is the plugin seam; guard it
   during F1 follow-up slices.
4. Core loader work: block discovery under a configurable root with D42
   containment; checker coverage (new E/W codes: missing/invalid block
   manifest, port-name collisions incl. the reserved `error` name,
   E011-style id rules); port surface derivation from the manifest with
   the python-node AST fallback.
5. Owner decisions to take at promotion time: reference syntax
   (additive key vs namespaced type), whether block config schemas can
   declare NEW field kinds or only compose existing ones, and
   sharing/distribution story (copy-the-folder first; anything richer
   is a separate feature).
6. Design constraint to preserve now: future codegen (core promise 6)
   must be able to emit a block's `impl.py` as a plain function — keep
   block behavior expressible as ordinary Python with declared I/O.

### Unscheduled backlog

The "Explicitly after v0.2" list above is the unscheduled backlog. Items
promote into F-numbered entries here (F9+) when prioritized by the
criteria; nothing is dropped by not being scheduled.

## Working agreements

- Conventional commits (`type(scope): subject`) — they feed git-cliff.
- Spec updates land in the same change as the behavior change (AGENTS.md).
- Tick REQUIREMENTS checkboxes in the implementation change, with a test.
- Run `$napflow-closeout` after each state-changing session and milestone; add
  one dated `docs/JOURNAL.md` entry (done / decided / next) when the closeout
  produced a durable change or useful handoff.
- New edge cases → `EDGE_CASES.md` (EC57+); new decisions →
  `DECISIONS.md` (D44+).
- v0.x releases are experimental (D33), tag-driven, and require exact
  tag/package version agreement (`RELEASING.md`). Breaking changes are
  permitted with clear release notes until v1.0.
