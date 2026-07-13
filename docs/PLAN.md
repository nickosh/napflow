# napflow — Development Plan (v0.x)

Status: v0.1 build stages adopted 2026-07-02 and completed 2026-07-09;
v0.2 plan adopted 2026-07-11. `REQUIREMENTS.md` defines testable scope;
this file defines order and definition-of-done. Tick boxes as milestones
land; append course corrections, don't rewrite history.

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
      all three `ui e2e` CI legs red since the M4 push.
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
facto release candidate; only release-prep lands between). From the
v0.1.0 tag on, work moves to feature branches + PRs (see Working
agreements).

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
**Met and adversarially revalidated 2026-07-12** (`feat/v0.2`): all four
boxes are evidenced — clean-tag v0.1.0 artifact + first-touch run; protected
and enforced history envelope plus collision-safe pre-storage contract; each
audit finding a signal-correct strict `xfail` or explicit-owner `skip`; every
named performance size measured with no deferred slot. (PR scope, owner call
2026-07-12: v0.2 lands as larger feature PRs off `feat/v0.2`, not one branch
per milestone.)

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
      **Met 2026-07-13:** private active/complete/incomplete lifecycle
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
evidence are green. Full REST paging/lazy replay remains its planned M5 owner,
not retained active state in M3.

### M4 — full-fidelity history and prepared requests

- [ ] Implement the D34 store: small JSON-compatible values inline;
      large text/binary/structured content stored once in immutable
      content-addressed blobs with hash, bytes, media type, encoding,
      and explicit reference records. Verify the stored hash round-trip.
      Deduplicate repeated appearances inside a run. (FR-1102, NFR-15)
  - [x] **Codec/store foundation** (landed 2026-07-13):
        `core/history_content.py` pins the 64 KiB inclusive threshold,
        exact UTF-8/JSON/raw-binary bytes, collision-safe literal and
        explicit omission envelopes, per-run immutable/private hash paths,
        deduplication, and strict missing/corrupt/hash-verified resolution.
        Feature activation intentionally remains below; foundation tests do
        not claim FR-1102/NFR-15/TR-16 completion.
        D39 keeps the codec, hashes, and deduplication but removes the
        permission/owner hardening in the next implementation change.
  - [ ] **Feature activation + event integration:** encode once before the
        shared JSONL/WebSocket fan-out, and enable `content-blobs/1` for both
        writer and reader in that same change. Partial field activation would
        make a later inline `$napflow` user value ambiguous and is forbidden.
    - [x] **Field-policy/redaction seam** (landed 2026-07-13): every current
          event dataclass field is classified as structure, complete content,
          keyed content, error-message content, or derived preview. Canonical
          sinks receive raw records; JSONL is currently permission-protected
          and the WebSocket is a trusted loopback-local surface. Terminal delivery and
          post-close JSON/JUnit rendering use the same schema-aware value-only
          redactor. `body_preview` and `value_preview` are explicit activation
          blockers, not silently accepted content fields. D39 retains this
          field registry and raw/presentation split while scheduling removal
          of the permission machinery next.
    - [ ] **Full-value schema + activation:** replace the two derived previews
          with the prepared request/full message contracts, apply the store
          through the registry, add lazy consumer resolution, then activate
          the writer/reader feature together.
- [ ] Apply storage policy to every persisted payload path—not only
      `request_finished.body`: message/log values, request bodies,
      response bodies, error payloads, and `run_finished.end_outputs`.
      Execution values remain complete and independent of their persisted
      representation. Remove misleading run/body valves once migration
      is complete. (EC32)
- [ ] Capture the prepared request rather than a 512-character preflight
      preview: final URL/query, effective headers/cookies, body reference,
      timing/retry metadata, and response detail. Library-generated sensitive
      headers remain raw local observations; optional declared-secret masking
      affects presentation values only. (FR-1103, EC50)
- [ ] Simplify local-history storage to the trusted local prototype model:
      raw JSONL and blobs use normal inherited OS permissions. Remove custom
      Windows DACL/SID ownership handling, forced POSIX private modes, and
      permission-based blob rejection while retaining exclusive creation,
      workspace containment, exact structural records, and hash verification.
      Terminal/JSON/JUnit masking remains optional through non-empty
      `environments.secrets`; scaffold masking as opt-in examples rather than
      an implied security guarantee. (FR-1104, D39, EC45)
  - [x] **Raw local + terminal/report views** (landed 2026-07-13): JSONL and
        local WebSocket records preserve exact values; the JSONL is private
        local storage and the WebSocket stays inside the trusted loopback UI
        boundary. Dictionary keys, identifiers, enums, and control fields are
        immutable; CLI stderr and post-close JSON/JUnit reports redact only
        classified content.
        Run directories/files force `0700`/`0600` despite the POSIX umask and
        use a protected owner/SYSTEM/Administrators DACL on Windows; an
        existing directory must belong to the current token before migration.
        An ordinary sink I/O close failure preserves the run result but
        publishes history only as an incomplete prefix; control-flow
        exceptions still propagate. This is historical landed evidence; D39
        intentionally removes its permission-specific portion next.

M4 DoD: a large response routed through Log and End is stored in full
once, inspected byte-for-byte, and replayed without duplicated bodies;
no silent truncation remains; prepared request detail reflects the effective
wire request; a secret named/value-shaped like an event field cannot corrupt
replay; local history works without a custom OS permission contract.

### M5 — usable paged replay and frame drilldown

- [ ] Version replay APIs and page event records rather than returning one
      unbounded JSON array. A simple cursor/sequence contract is sufficient;
      no derived seek index or advanced filter matrix is required for v0.2.
      Canonical JSONL remains unchanged. (FR-1106)
- [ ] Fetch blobs lazily. The browser keeps a bounded/virtualized event
      window plus reduced node/frame summaries, not every full record.
      Opening detail resolves the blob and verifies/handles missing data.
- [ ] Reconstruct the frame tree from durable events and summaries.
      Root canvas loads first; subflow/loop iteration detail loads when
      expanded. Runtime frame compaction must be invisible to replay.
- [ ] Preserve EC20 behavior for genuinely incomplete runs while using a
      durable final summary to distinguish them from one large final line.

M5 DoD: a representative full-fidelity run opens without returning every
event or fetching every blob, keeps the active browser window bounded, and
drills into completed child frames without re-execution. Timeline playback,
checkpoints, advanced seeking, and the 100k-event replay target remain named
future work.

### M6 — public/package/UI contract completion

- [ ] Implement and document an ergonomic stable-for-v0.2
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
- [ ] Make source/Git installation honest: either a deterministic PEP 517
      frontend build, committed generated bundle, or removal of the Git
      install promise in favor of built artifacts. Test a wheel from a
      clean Git archive/sdist and execute `napf ui`. (FR-1113, EC44)
- [ ] Bring visual editing to schema parity for safety/template fields:
      universal `max_seconds`, template-aware number/boolean fields,
      request TLS/timeout, and typed Start defaults. Add schema-to-form
      coverage so drift fails a test. Fix abort-response status handling.
      (FR-1114, EC49)
- [ ] Refactor only the live/history orchestration needed by the paged/lazy
      API. Preserve pure graph/run reducers; a standalone global-store split
      is not a v0.2 deliverable unless implementation proves it necessary.
- [ ] Generate/audit third-party notices for bundled frontend code before
      public distribution.

M6 DoD: documented installation plus functional and workspace/Flow core
examples work from the built artifact; discovery/catalog tests cover exact,
nested, non-identifier, collision, and flow-plus-namespace identities without
shared run state; every authoritative editable schema field has a valid UI path
or an explicit documented YAML-only status; frontend orchestration has direct
unit coverage for persistence and run transport.

### M7 — release gates, compatibility evidence, and v0.2 promotion

- [ ] Reuse the existing required CI/release workflows and add only missing
      v0.2 product checks: Vitest in the authoritative gate, installed-wheel
      `napf ui`/`run_flow` smoke, production bundle membership, and exact
      tag/package version refusal. Do not add minimum/latest dependency or
      expanded OS/browser axes before user demand. (NFR-16)
- [ ] Close the M0 audit entries still owned by streamlined v0.2. Reassign
      placeholders for explicitly deferred export, advanced replay, or
      performance targets to the future ledger instead of treating them as
      release failures.
- [ ] Run the focused release checks that exercise current promises: full
      Python/UI suites, existing cross-platform paths, missing/corrupt blob,
      incomplete run, prepared-request capture, public API isolation, and
      clean installed-artifact smoke. Do not introduce a separate exhaustive
      adversarial or performance matrix in v0.2.
- [ ] Update engine/workspace/flow specs to implemented v0.2 behavior,
      publish format notes, close remaining v0.2-owned EC32/EC42/EC44/EC47/
      EC49/EC50 only with their tests, preserve
      M1's tested EC38/EC46/EC51 closures, record EC27's
      cooperative-scheduler half precisely, and retain EC10/EC22/EC35
      as named post-v0.2 limitations; then tag `v0.2.0`.

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

## Working agreements

- Conventional commits (`type(scope): subject`) — they feed git-cliff.
- Spec updates land in the same PR as the behavior change (AGENTS.md).
- Tick REQUIREMENTS checkboxes in the landing PR, with a test.
- Run `$napflow-closeout` after each state-changing session and milestone; add
  one dated `docs/JOURNAL.md` entry (done / decided / next) when the closeout
  produced a durable change or useful handoff.
- New edge cases → `EDGE_CASES.md` (EC52+); new decisions →
  `DECISIONS.md` (D40+).
- v0.x releases are experimental (D33), tag-driven, and require exact
  tag/package version agreement (`RELEASING.md`). Breaking changes are
  permitted with clear release notes until v1.0.
- **From the v0.1.0 tag on** (owner call 2026-07-08): no direct
  commits to `main` — feature branches + PRs, conventional commits
  feed git-cliff per release. Side benefit: per-PR CI closes the
  NFR-10 batch-push blind spot (every change gets its own CI run at
  its own HEAD).
