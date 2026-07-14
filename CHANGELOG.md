# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-07-14

### Added

- Complete M6 public packaging and UI contracts
- *(history)* Complete paged replay and frame drilldown
- *(history)* Complete full-fidelity M4
- *(history)* Use ordinary filesystem permissions
- *(history)* Add raw and redacted event views
- *(history)* Add content store foundation
- *(v0.2)* Complete M3 bounded history lifecycle
- *(v0.2)* Bound loop execution state
- *(v0.2)* Complete M2 lifecycle hardening
- *(v0.2)* Harden workspace and editing boundaries
- *(events)* Version-stamp the run-history envelope (FR-1101)

### Fixed

- *(ci)* Resolve Windows portability failures
- *(v0.2)* Close M0 audit findings

### Documentation

- *(plan)* Streamline v0.2 prototype scope
- *(api)* Define workspace flow embedding contract
- *(v0.2)* Record M0 session handoff

### Internal

- *(release)* Add reusable v0.2 gate
- *(agents)* Add project closeout skill
- *(v0.2)* Cover M0 replay and harness edges
- *(v0.2)* M0 audit-probe xfails + perf baselines

## [0.1.0] - 2026-07-11

### Added

- S4/M6 subflow UX — drill-in, used-in-N-places, clone, ghost-wires
- *(ui)* S4/M5.5 run-mode inspection polish — port traffic, wire messages, log ring, run inspector
- *(ui)* Explicit follow toggle for the live event tail
- *(ui)* S4/M5 run on canvas + history — live-animated run mode (FR-1005)
- *(ui)* Close S4/M4 leftovers — CodeMirror 6, drag-from-palette, structured editors, typed defaults, live W102 hint
- *(ui)* S4/M4 canvas editing + write path — flows save through the one serializer
- *(ui)* S4/M3 read-only canvas — real flows render with D11 port coloring
- *(ui)* S4/M2 UI scaffold + wheel walking skeleton — the one-wheel promise is now gated
- *(server)* S4/M1 BlackSheep server + napf ui — REST/WS thin adapter over core
- *(engine)* S3/M5 hierarchical frames — flow + loop nodes; stage S3 complete
- *(engine)* S3/M4 counter + timeout guards — flagship retry runs
- *(engine)* S3/M3 switch, set/get, log, fixture — first-touch green
- *(engine)* S3/M2 python worker subprocess + python node
- *(engine)* S3/M1 firing rules 2-3 + merge node
- *(cli)* S2/M5 napf run — headless runs, reports; stage S2 complete
- *(core)* S2/M4 request node — niquests adapter, retry, capture valves
- *(core)* S2/M3 engine — scheduler, frames, start/end/condition/assert(+delay)
- *(core)* S2/M2 events — EN §7 vocabulary, JSONL sink, secret masking
- *(core)* S2/M1 templating render — native-value rule (D25), env layering
- *(cli)* S1/M5 — napf init/list/check; stage S1 complete
- *(core)* S1/M4 checker — E001-E012, W101-W107, closure checking
- *(core)* S1/M3 discovery — manifest walk-up, flows, env profiles
- *(core)* S1/M2 loader + write path — positioned reads, canonical emit
- *(core)* S1/M1 Pydantic models — full node catalog + JSON Schema export
- *(docs)* Initial project docs and requirements

### Fixed

- *(e2e)* Run specs own their flows — smoke race broke ui e2e on all 3 OS
- *(ui)* Event stream follows the live tail only while at the bottom
- *(ci)* Install Playwright deps on Linux only — --with-deps hangs on hosted Windows runners
- *(tests)* Skip the import-linter contract where it isn't installed
- *(worker)* Reap exit status before composing crash messages (Windows race)

### Documentation

- Adopt v0.2 replan — D33–D37, PLAN M0–M7, FR-1101–1114/NFR-12–18/TR-11–22
- D32 — stdlib-only worker child / user-interpreter promise pinned
- NFR-08 resolution plan — manual measurement now, perf suite as R6
- Dev4 wrap-up — README RC status, v1 checklist audit
- Adopt run-debugging/replay plan (M5.5 + post-v0.1.0 R1–R4, D30) + v0.1.0 promotion path
- Record D28 (chromium-only e2e, built-bundle path) + EC38–EC41 from S4
- Sync docs with S4 reality — CodeMirror in stack line, no canvas YAML emitter, README status S1→S4
- Record S4/M4 leftovers + sandbox npm allowlist
- Finalize TR-9 + NFR-03 ticks against CI run 28785741251 (all 3 OS green)
- Correct NFR-10 record — compat job was red from its first run
- Pin S4 UI shell — default browser in v1, app-mode --app deferred
- Promote Linux to first-class v1 platform (D26)
- Record owner-confirmed stdout masking boundary (D22 amendment)
- Add development plan, working journal, changelog requirement
- Park endpoint collections + Postman/OpenAPI import for v2
- Consistency pass across amendment rounds
- *(review)* Apply senior-review fixes (D25, EC28-EC37)

### Internal

- *(release)* V0.1.0
- Simplify package short description
- *(release)* PyPI trusted publishing, dry-run path, tag-version hard gate
- *(release)* Close S4 — version 0.1.0.dev4
- Add Codex project guidance
- *(ui)* Pin Node 24 (active LTS) for CI/nvmrc; engines floor >=22.12
- Ruff-format the S4/M1 files — CI's format gate caught what local checks missed
- S1 closeout — README, dev versioning, release flow, repo rename
- Fix Windows path assertion in closure test
- Pin setup-uv to v8.2.0 — no moving v8 major tag exists
- Close out M0 — tick boxes, bump CI actions to Node 24 majors
- Scaffold repo for S1/M0 — packaging, CI, contracts, changelog
- Add SessionEnd session-breadcrumb hook

