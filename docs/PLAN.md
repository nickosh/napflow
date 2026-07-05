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

## S2 — engine core + `napf run`  ← current

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
- [ ] **M4 — request node**: niquests behind the internal adapter
      module (NFR-09), engine-level retry, non-2xx-is-data (EC13),
      `defaults.request` merge (EC23), capture valves, timing fields,
      timeout routing, binary envelope. (FR-105/207/503/703/705/706;
      NFR-10; TR-8 request paths, TR-10 round-trip)
- [ ] **M5 — `napf run`**: BIND/ENV lifecycle steps, `--env` / `-i` /
      `--input-json` / `--timeout`, End outputs → stdout JSON, logs →
      stderr, junit/json reports, exit codes 0/1/2/130. (FR-803/804;
      stage DoD check)

## S3 — full node set + python worker

python + worker subprocess (protocol integrity per EC28), merge,
guards, loop, flow, set/get, switch, delay, log, fixture, note.
DoD: flagship retry example runs; `napf run flows/smoke` passes offline
(first-touch, EC34); TR-1/4/5/6/8/9/10 green.

## S4 — server + UI canvas

FR-10xx; canvas edits keep golden diffs clean.
DoD: `napf ui` end-to-end on macOS + Windows (incl. TR-9).
Per PRODUCT.md: S1–S3 is a shippable CLI-only product — S4 must not
block a first release.

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
