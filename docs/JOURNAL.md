# napflow — Working journal

Newest first. One short entry per working session / milestone:
**done / decided / next**, 2–5 lines each. This is the cross-session
progress log — keep it lean; details live in specs, DECISIONS, and git.
Record project and engineering state, not branch/commit/PR/push/pull/merge
bookkeeping or hosting-service identifiers.

## 2026-07-22 — F1 pre-audition review queue reconciled

- Planned: unify every run-input entry path and make run-inspector port peeks
  resolve full blob-backed values lazily with localized failures.
- Decided: exactly-one Start/End boundaries may be deleted, including through
  the drag-to-delete area; E006 blocks runs and the picker restores only the
  missing type. Distinct boundary styling and auto-start cues remain planned.
- Decided: disconnected islands stay legal without disabled/inert semantics;
  normal validation still applies, and reachability accounts for auto-fixtures.
- Reconciled: F8 now permits reviewed built-in semantic adapters while generic
  presentation stays declarative; the plugin contract itself remains unscheduled.
- Next: handle the cross-platform focus-shortcut gate separately, then use this
  queue during the real-work manual audition and scope accepted F1 slices.

## 2026-07-19 — F1 Slice 2 canvas undo/redo complete

- Done: added a bounded, per-open-flow document history with structural sharing,
  semantic drag/config/tidy/delete steps, toolbar actions, and scoped shortcuts.
- Reconciled: undo/redo reuses autosave, survives self-save and code-metadata
  refreshes, clears on navigation/external flow revision, and stays locked in
  run mode.
- Verified: Ruff/import/notices, 762 pytest, 96 Vitest, production build,
  45 Playwright, and isolated wheel/sdist artifact smoke pass.
- Next: return to F3 descendant-process cleanup, then F4 render guards;
  F1 Slices 3+ remain audit-driven.

## 2026-07-19 — F1 Slice 1 frontend store split complete

- Done: replaced the 1,346-line `ui/src/store.ts` with a 24-line stable facade
  over canvas/document, persistence/session, and run-replay slice factories;
  the optional `RunPanel.tsx` split was unnecessary.
- Reconciled: one `useAppStore` and its public exports remain unchanged;
  `detail.flow` has a canvas-owned boundary for future history, persistence
  owns its autosave bridge, and parity review found no state/action/lifecycle
  drift.
- Verified: Ruff/import contracts, 762 pytest plus isolated requests+botocore
  compatibility, notices, 84 Vitest, production build, 43 Playwright, and
  release-artifact smoke all pass.
- Next: implement F3 descendant-process cleanup, then interleave F4 ahead of
  F1 Slice 2 undo/redo per rolling priority.

## 2026-07-18 — F2 server adapter split complete

- Done: pure-moved replay reads/views, live WebSocket streaming, and the D37
  request/write boundary into `server/replay.py`, `server/ws.py`, and
  `server/boundary.py`; `app.py` now owns routes and small response helpers.
- Reconciled: `app.py` is 958 lines; its retained route block was already 735,
  so the stale "~700" estimate was incompatible with the approved no-route-move
  scope. Token-normalized comparisons prove the extracted bodies are exact.
- Verified: Ruff/import contracts, 762 pytest, isolated requests+botocore
  761-pass compatibility, notices, 84 Vitest, production build, 43 Playwright,
  and release-artifact smoke all pass.
- Next: F1 Slice 1, the `ui/src/store.ts` pure-move split; interleave F3/F4
  per rolling priority.

## 2026-07-17 — F1 UI redesign slice: Nocturne design + in-card editing

- Done: applied the owner's Claude Design handoff (Nocturne, Soft, dark+light,
  bundled fonts/icons — no CDN): full-bleed canvas with floating chrome (flows
  menu, breadcrumb, ⌘K palette, zoom/minimap, bottom add/tidy/run bar,
  drag-to-trash), per-type node cards with quick fields, unified console
  (events/history/diagnostics), tidy auto-layout, restyled run visuals.
- Decided (owner): the right Inspector is dropped — full config editing lives
  in-card (selected cards grow); one Soft aesthetic + theme toggle only;
  per-type card sizes; replay scrubber stays deferred under D39.
- Verified: Vitest 80, Playwright e2e 43, pytest 762, Ruff — all green;
  scaffold layouts widened for card sizes; OFL-1.1 reviewed into notices gate.
- Next: F2 `server/app.py` pure-move split, then F1 Slice 1 (store.ts split)
  ahead of Slice 2 undo/redo.

## 2026-07-16 — Project-memory scope clarified

- Decided: memory records product behavior, engineering decisions, verified
  outcomes, blockers, and the next technical action—not delivery bookkeeping.
- Updated closeout guidance and current memory entries; durable release mechanics
  remain in `RELEASING.md`, while transient Git/hosting state stays in chat/tools.
- Next: keep future closeouts technical and evidence-based under this boundary.

## 2026-07-16 — Backend CI ANSI false-negative fixed

- Diagnosed: all four backend jobs shared one ANSI-fragmented Rich help
  assertion; product behavior and compatibility imports passed.
- Fixed: help coverage now runs in plain and Actions-style ANSI modes, strips only
  terminal control codes, and keeps first-touch init as a separate real invocation.
- Verified: 762 project tests pass (11 deselected); the isolated requests+botocore
  gate passes 761 tests (1 skipped, 11 deselected), plus Ruff/import contracts.
- Next: confirm the repair in the three-OS CI gate.

## 2026-07-15 — F7 configurable roots and deterministic init complete

- Done: configurable `flows`/`data`/environment roots, literal dotenv profiles,
  W108, minimal init, and opt-in `init --example` are implemented under D42/D44.
- Fixed: EC57 Windows xyflow measurement/retry isolation and EC58–EC59 stable
  environment/init boundaries, including complete no-write scaffold preflight.
- Verified: 760 pytest + 80 Vitest + 43 Playwright pass; Ruff and both import
  contracts pass; UI, wheel/sdist, and isolated installed-artifact smoke pass.
- Next: confirm F7 on Linux, macOS, and Windows, then resume F2.

## 2026-07-15 — F6 Windows CI portability fix

- Fixed LF-intended Git-metadata fixtures and a path-render assertion so the
  F6 tests preserve their intended semantics on Windows; runtime behavior is unchanged.
- Verified: 92 focused and 667 full tests pass (11 deselected); Ruff format/lint
  and import contracts pass. Next: confirm the three-OS gate.

## 2026-07-15 — F6-only boundary; F7 deferred

- Decided by owner: the current development slice implements F6 only; F7 remains
  planned but is skipped for now.
- Removed the unused configurable environment-root rule builder/tests; F6 now
  exposes only the fixed `envs/*.env` + `!envs/example.env` contract.
- Verified: 99 focused and 667 full tests pass (11 deselected); Ruff lint and
  format checks pass. Next: cross-platform validation, then F2.

## 2026-07-15 — F6 brownfield init Git metadata complete

- Done: D43 root-only/LF-only metadata handling adds consent-based init
  appends, safe non-TTY/CRLF behavior, explicit opt-outs, and read-only W109.
- Closed EC56 with partial/order/idempotency, unsafe-path, prompt/EOF/snapshot,
  exclusive-create, root-source, and read-only-check regressions.
- Verified: 102 focused tests and full 670-test pytest suite pass (11 deselected);
  Ruff lint and format checks pass across all Python sources/tests.
- Next: cross-platform validation, then F2.

## 2026-07-15 — rolling delivery adopted (D41) + F1–F7 backlog + D42

- Decided D41: no version-scoped milestone plans after v0.2.0; features complete
  independently, the integrated project stays releasable, and the owner cuts releases.
- Decided D42: configurable directories (`flows.root`, `environments.root`,
  future keys) never escape the workspace root — no `..`/absolute; embedded
  workspaces raise the root (manifest at host level, keys point down).
- Done: PLAN §Rolling delivery with ordered priority criteria and backlog —
  F1 UI rework track (headline; demos/screenshots wait for it; Slice 2 =
  in-memory canvas undo/redo, owner call: never a workspace file), F2 approved
  `server/app.py` pure-move split (replay/ws/boundary), F3 EC22 tree-kill,
  F4 EC27/EC35 render guards (perf-neutral by design), F5 perf drift job,
  F6 brownfield-init git-metadata prompt/append (EC56 recorded open;
  owner calls: default append on TTY, never mutate without TTY),
  F7 `environments.root` + literal-filename env profiles (breaking:
  `--env dev.env`; D33 window) + W108 not-ignored warning (verified live:
  `flows.root` + free fixture paths already work — document, don't
  rebuild; no gitignore mutation outside init).
- Updated AGENTS.md build history item 6; EC22/EC27/EC35 rows point at their
  scheduled F-entries and remain OPEN.
- Next: start F2; F7 remains deferred.

## 2026-07-15 — v0.2.0 release-memory closeout

- Done: the full three-OS release gate passed; uploaded wheel/sdist names and
  metadata are exact `0.2.0`.
- Closed: TR-22 and EC55 remote evidence; public status, plan, decisions,
  requirements, release process, edge ledger, and changelog reconciled.
- Unchanged: behavior/schema specs and compatibility notes remain accurate.
- Next: publish `v0.2.0`, then verify PyPI installation and release artifacts.

## 2026-07-15 — macOS release-gate browser fix

- Reproduced the release-gate failure: a click-added Switch stayed below the
  fitted viewport under the flow list; its serial retry obscured the root failure.
- Fixed click-add to retain collision-free placement and refit after measurement;
  strengthened drag/readiness assertions and retained Playwright failure artifacts.
- Verified: typecheck/build, 76 Vitest, 43 Playwright, and 20 repeated failure-path
  cases pass locally; next rerun the full release gate.

## 2026-07-14 — v0.2 M7 release candidate prepared

- Done: prepared exact `0.2.0` metadata, changelog, compatibility notes, and
  release-note wiring; closed EC44 and fixed the newly recorded EC53/EC54 gaps.
- Verified locally: 618 pytest, 76 Vitest, 43 Playwright, Ruff/import contracts,
  production UI/notices, exact artifacts, and installed-artifact smoke pass.
- Cross-platform baseline is green on Linux, macOS, and Windows; the final
  prepared tree still needs its non-publishing release gate.
- Next: run the authoritative gates, then publish and verify exact `v0.2.0`.

## 2026-07-14 — Windows CI portability fix

- Fixed: history locking rejects planted non-regular entries before open, so
  Windows cannot create a dangling symlink's external target during fallback.
- Hardened tests: redirect failures allow the adapter's connection/timeout
  mapping, and clone symlinks compare target identity rather than path spelling.
- Verified: 593 pytest plus focused cases and Ruff check/format pass locally;
  next confirm the Windows gate.

## 2026-07-14 — v0.2 M7 reusable release-gate start

- Done: CI is reusable by the tag workflow; Vitest/notices/artifact smoke and
  tested exact tag/final-version refusal now gate publishing; dispatch stays dry.
- Hardened: the direct wheel must byte-match the no-Node sdist rebuild; cleared
  three inherited formatter failures exposed by the authoritative gate.
- Verified: 593 pytest, 76 Vitest, 43 Playwright; format, Ruff, import contracts,
  production build, notice audit, and isolated installed-artifact smoke pass.
- Next: run the release dry-run, then finish the v0.2 compatibility notes/spec
  audit and separate version/tag preparation.

## 2026-07-13 — v0.2 M6 public/package/UI contracts complete

- Done: reusable Workspace/Flow and functional APIs share isolated execution;
  D40 supports release-built artifacts and rejects raw VCS/source installs.
- Done: schema/form parity covers safety and templated typed fields; audited,
  deterministic frontend notices ship in both artifacts.
- Verified: 582 pytest, 76 Vitest, and 43 Playwright tests; Ruff, import
  contracts, typecheck/build, and release-sdist → no-Node-wheel smoke pass.
- Next: M7 wires these checks into the authoritative gate and pins exact
  tag/package version refusal before v0.2 promotion.

## 2026-07-13 — v0.2 M5 usable replay complete

- Done: `napflow-replay/1` adds frozen bounded event/frame pages, graph-sized
  projections, lazy verified detail, and honest complete/incomplete/indeterminate state.
- Proved: a 1,101-event history opens one page with a complete overlay; a real
  72 KiB child swaps to its durable canvas without re-execution or eager blob reads.
- Verified: 564 pytest, 62 Vitest, and 42 Playwright tests pass (1 pytest
  skipped, 1 expected xfail); Ruff, both import contracts, typecheck, and build pass.
- Next: M6 public Workspace/Flow API, deterministic packaging, and UI/schema parity.

## 2026-07-13 — v0.2 M4 full-fidelity history complete

- Done: scaffold masking is opt-in; `content-blobs/1` now covers every
  persisted value path; capture valves/previews are removed; prepared-wire
  request/final-redirect detail is recorded.
- Proved: a 200 KB Request→Log→End→JSON-report path keeps complete runtime data
  and one hash-verified blob; lazy report/server/UI boundaries preserve refs.
- Verified: 551 pytest, 50 Vitest, and 40 Playwright tests pass (1 pytest
  skipped, 1 expected xfail); Ruff, both import contracts, and the UI build pass.
- Next: M5 versioned paging, on-demand browser blob reads, bounded replay, and
  reconstructable frame drilldown.

## 2026-07-13 — v0.2 M4 ordinary-permission local history

- Done: removed custom Windows DACL/SID ownership, forced POSIX modes, and
  permission-only blob rejection; raw JSONL/blobs now inherit OS permissions.
- Preserved: exclusive/no-follow creation, workspace containment, exact record
  shapes, existing-digest equality, size checks, and SHA-256 verification.
- Verified: 525 pytest passed (3 skipped, 2 expected xfails); Ruff and both
  import-direction contracts pass.
- Next: make scaffold secret patterns opt-in, then land full-value schemas,
  blob activation, lazy consumers, and prepared-request capture.

## 2026-07-13 — raw-history publication warning

- Documented in README + manifest that `.napflow/runs/` may contain raw
  credentials/content and must not be committed, uploaded, or published blindly.
- Clarified that gitignore and terminal/report masking do not sanitize JSONL,
  blobs, or local UI history; next remains ACL/private-permission deletion.

## 2026-07-13 — v0.2 prototype-first replan

- Decided D39: keep full-fidelity blobs/prepared requests and the runtime
  Workspace/Flow catalog; use ordinary OS permissions with optional masking.
- Deferred: secure history/export bundles, advanced replay indexes, timeline
  playback/checkpoints, and the 100k-event replay gate remain future items.
- Replanned M4–M7 and reconciled product/requirements/spec target notes; this
  session changes documentation only, not current permission behavior.
- Next: remove the ACL/DACL, owner, forced-mode, and private-blob checks with
  focused tests, then update current-behavior specs through closeout.

## 2026-07-13 — v0.2 M4 raw/redacted event seam

- Done: exhaustive event-field policies now separate immutable structure,
  content/map values, error messages, and the two lossy preview blockers.
- Hardened: raw JSONL validates ownership + POSIX/Windows privacy; redacted
  views preserve protocol, and ordinary close failures become incomplete.
- Verified: 521 pytest passed (4 skipped, 2 expected xfails); the Windows DACL
  inspection is locally skipped pending Windows CI. Ruff/import contracts pass.
- Next: land prepared-request/full-message fields, then encode through the
  registry and activate `content-blobs/1` with lazy consumers in one change.

## 2026-07-13 — v0.2 M4 content-store foundation

- Done: added the strict D34 persisted-value codec and per-run immutable,
  private, content-addressed store; `content-blobs/1` remains disabled.
- Hardened: exact JSON/binary fidelity, literal/omission envelopes, dedupe,
  directory/reparse defenses, size-before-read, hash checks, and typed failures.
- Verified: 502 pytest passed (3 skipped, 4 expected xfails), including 39
  focused cases; Ruff and both import-direction contracts passed.
- Next: finalize the exhaustive event payload registry and protocol-safe
  redaction boundary, then encode before fan-out and activate the feature once.

## 2026-07-13 — v0.2 M3 bounded execution + history complete

- Done: fixed loop workers/frame summaries, bounded live/durable WS catch-up,
  scalar server/browser windows, streaming CLI reports, and whole-unit retention.
- Hardened: canonical-tail/order markers, cross-process reader leases,
  no-follow locks/reports, resumable tombstones, large/partial tails, XML safety.
- Verified: 463 pytest passed (3 skipped, 4 expected xfails), 48 Vitest, UI
  build, Ruff/import contracts, plus the 100k loop gate (16 helpers/Frames).
- Next: M4 full-fidelity blobs, prepared requests, and raw/redacted views.

## 2026-07-13 — v0.2 M3 bounded loop/frame start

- Done: parallel loops now use a fixed `max_concurrency` worker set; normally
  quiescent children emit canonical `frame_finished` and release live Frames.
- Preserved: item ordering and nested D20/D21 outcomes; cancelled frames stay
  concurrency-bounded and defer release to cleanup so abort cannot fake D18.
- Verified: 427 pytest passed (3 skipped, 6 expected xfails), 47 Vitest,
  production UI build, Ruff, and both import-architecture contracts.
- Next: stream CLI reports from durable JSONL, add robust last-record reading,
  then land post-completion whole-run retention and bounded live subscribers.

## 2026-07-13 — public Python workspace/flow API direction

- Decided D38: public embedding is reusable Workspace → bound Flow → isolated
  Run, with functional `run_flow` kept equivalent and sync/async paths.
- Planned M6: fresh runnable discovery, exact lookup, and a nested runtime
  `workspace.flows.<identity segments>` catalog without lossy normalization.
- Deferred: generated flow-name plus typed Start/End bindings and stale checks
  stay explicitly after v0.2; runtime attributes do not claim static typing.
- Verified: D38/FR-1112/EC42 references, Markdown fences, trailing whitespace,
  and the full documentation diff; docs-only change, so no test suite required.
- Next: preserve this contract through M3–M5, then implement/test it in M6.

## 2026-07-13 — v0.2 M2 fair lifecycle + worker safety

- Done: 128-delivery fair pump, monotonic deadline/abort checks, and one
  shielded cleanup owner across tasks, HTTP, workers, streams, and shutdown.
- Done: immediate reap-before-replace worker teardown, graceful normal EOF,
  16 MiB stdlib JSON-lines, callable agreement, and strict binary requests.
- Verified: 425 pytest; Ruff/import contracts; real-uvicorn path; M2 audit and
  cancellation matrices; 41.3k guarded laps/s and 1 KiB/100 KiB/10 MiB workers.
- Carry: EC22 descendants and EC35 sync-Jinja preemption stay post-v0.2;
  next is M3 bounded loop/frame tasks plus durable history lifecycle.

## 2026-07-12 — v0.2 M1 workspace boundary + durable editing

- Done: centralized lexical/symlink-aware workspace resolution and stable
  boundary errors across checker/engine/CLI/server; added loopback Host/Origin.
- Done: atomic source writes + serialized ETag locks, revisioned canvas/code
  persistence, lifecycle save barriers, and `/flow/` segment-encoded routes.
- Verified: 395 pytest + 47 Vitest + 40 Playwright; production UI build,
  Ruff, import contracts, atomic interruption/concurrency, and goldens green.
- Carry: trusted workspaces are not an OS sandbox against a malicious local
  filesystem racer; next is M2 scheduler fairness/cancellation/worker lifecycle.

## 2026-07-12 — repo-scoped closeout skill + Claude discovery

- Done: added `.agents/skills/napflow-closeout` with session, milestone, and
  no-update modes plus an evidence-driven memory reconciliation matrix; linked
  `.claude/skills` → `../.agents/skills` so both agents share one canonical copy.
- Decided: closeout audits every memory surface but edits only affected files;
  milestone and requirement completion require implementation + tests.
- Updated: AGENTS and PLAN now invoke the skill after state-changing sessions
  and use the journal's actual newest-first/prepend convention.
- Verified: skill validation, symlink resolution through Claude's path, metadata
  inspection, and diff checks.
- Next: use `$napflow-closeout` after the next v0.2 M1 work session and refine
  only if real usage exposes friction.

## 2026-07-12 — v0.2 M0 session handoff

- Completed M0 code/tests; no implementation work remains.
- Done: versioned/enforced history envelope, signal-correct audit ledger, exact
  Python/browser baselines, malformed-history regressions, and tested e2e cleanup.
- Verified: 354 pytest passes + 35 Vitest + 32 Playwright; 10 Python perf + 2
  browser perf; build, Ruff, import contracts, artifact/tag reproduction green.
- Carry: 9 strict-xfail cases + 12 explicit-owner skips move through M1–M6;
  M7 promotion requires converting the whole M0 ledger to normal passing tests.
- Next: M1 — central resolver/identity boundary, Host/Origin, atomic writes,
  serialized ETag/save coordination; first target is the symlink xfail.

## 2026-07-12 — v0.2 M0 adversarial closeout

- Done: protected/enforced `napflow-run/1` across emission + REST/WS replay;
  pinned collision-safe persisted-value/byte rules; hardened all audit probes.
- Verified: clean-tag v0.1 wheel/install/smoke; 352 pytest passes + 31 Vitest + 32
  Playwright; 10 Python perf + 2 browser perf; Ruff/import contracts green.
- Recorded: exact 1KB/100KB/10MB worker, 100/10k/100k loop, and 10/100MB
  server/browser baselines; FR-1101 and NFR-08/18 remain open until M3–M7.
- Next: M1 — central workspace boundary, Host/Origin, atomic writes, save queue.

## 2026-07-12 — v0.2 M0 baseline (format marker + audit probes + perf)

- Done: **Box 2** — run-history is version-marked
  before storage changes: `run_started` is the envelope header carrying
  `format: "napflow-run/1"` (`HISTORY_FORMAT`), reader gate
  (`parse_history_format`/`is_supported`/`HistoryFormatError`) + tests;
  full format contract (ordering, blob-ref shape, inline threshold,
  byte/hash, disposable indexes) pinned in engine spec §7a. **Box 3** —
  `tests/test_v02_audit.py`: 8 confirmed critical/high findings reproduced
  as strict `xfail` (public `run_flow` import, symlink escape past
  `_safe_identity`, 70KB worker result crashes the 64KB StreamReader,
  external-cancel leaks the worker, Log bypasses the capture valve,
  secret value `passed`/`error` rewrites state/keys, >64KB final event
  drops from `_tail_record`, same-second retention deletes the newer
  run); 5 non-headless findings routed as explicit-owner `skip`s. **Box 4**
  — `tests/test_perf_baselines.py` (opt-in `perf` marker, CI-excluded via
  `-m "not perf"`) + `docs/perf-baselines.md`. 320 passed / 5 skipped /
  6 deselected / 8 xfailed; ruff clean.
- Decided (owner): v0.2 ships as **larger independently verifiable feature
  slices**, not one slice per milestone. FR-1101 and NFR-08/18 stay open (they
  close in M3–M5 / M7 with their real implementations); no REQUIREMENTS
  tick lands from a baseline or a failing test.
- Baseline headlines: ~42k guarded laps/s; ~20ms spawn-dominated worker
  round trip; 100k parallel loop **489 MB** peak heap (the M3 target);
  10MB replay read 19 MB peak (M5 target).
- Next: M1 — `WorkspaceResolver` symlink-aware boundary + loopback
  Host/Origin + atomic write + serialized save coordinator (flips the
  symlink `xfail` green, TR-12/TR-20).

## 2026-07-11 — v0.1.0 release prep

- Done: version 0.1.0, CHANGELOG regenerated (`--tag v0.1.0`), README
  status/install flipped to PyPI, v0.x honest-notes preamble added
  (`docs/release-notes-preamble-v0.md`, wired into release.yml) —
  PLAN M0 box 1 ticked. Owner: PyPI pending publisher registered,
  dry-run green.
- Next: complete the three-OS gate, publish v0.1.0, verify
  `uv tool install napflow` + first-touch, then begin M0 baseline work.

## 2026-07-11 — release workflow: PyPI trusted publishing + dry-run

- Done: `release.yml` restructured into build → pypi → github-release
  jobs; automated tag↔version hard gate; `workflow_dispatch` dry-run
  (build + checks, no publish); `v0.*` Releases auto-marked pre-release
  (D33). RELEASING.md updated in the same change.
- Decided (owner): repo flips public AT the v0.1.0 tag; validation is
  dry-run only (no TestPyPI rc) — first real publish is v0.1.0 itself.
- Verified: `napflow` name free on PyPI as of today; trusted publishing
  needs no repo-visibility change and no tokens.
- Next: owner PyPI account + pending publisher (`napflow`/`nickosh`/
  `release.yml`/env `pypi`), green dry-run, release preparation, then
  publish v0.1.0.

## 2026-07-11 — first-working-version review → v0.2 plan

- Decided (owner): ship v0.1.0 first as the private working milestone;
  all v0.x formats, including `napflow/v1`, remain experimental (D33).
- Decided: v0.2 is the full-fidelity hardening/replay release—store large
  content once as blobs, preserve raw local truth, redact CI/export,
  compact runtime frames but reconstruct them from durable events
  (D34–D36).
- Decided: keep local security simple—loopback + Host/Origin and one
  symlink-aware workspace boundary; no remote auth system (D37).
- Planned: PLAN M0–M7 + FR-11xx/NFR-12–18/TR-11–22; EC09/EC10/EC22/
  EC27/EC32/EC35/EC38 reopened and EC42–EC51 added. Several accepted
  limitations now have explicit post-v0.2 closure conditions instead of
  being called resolved.
- Next: v0.1 release-prep/version/tag, then v0.2 M0 regression and format
  baseline before implementation refactors.

## 2026-07-10 — D32: the stdlib-only worker promise, pinned

- Decided (owner): **D32** — python nodes run in the USER's
  interpreter (FR-108) and the worker child stays stdlib-only forever;
  the wire protocol stays stdlib-readable JSON lines. msgspec
  evaluated and rejected for now (child side: breaks the promise;
  parent side: unmeasured bottleneck — folded into NFR-08/R6 as a
  pre-analyzed branch).
- Noted (owner, future): explore napflow flows as citizens of other
  frameworks — e.g. pytest integration over the importable engine
  (`from napflow.core import run_flow`). No design yet; parked.
- Next: unchanged — three-OS CI sweep, then the manual dev4 window.

## 2026-07-10 — NFR-08 resolution plan + R6

- Decided (owner): NFR-08's unmeasured half ("overhead negligible vs
  HTTP") gets a MANUAL measurement during the dev4 testing window —
  that ticks the box; a pytest-marked perf suite, EXCLUDED from CI by
  default, automates the method later → backlog **R6** (also gains a
  `max_concurrency` peak-bound counting assertion — the semaphore
  bound is true by construction but nothing counts it today).
- Next: unchanged — three-OS CI sweep, manual window, v0.1.0 promotion.

## 2026-07-09 — dev4 wrap-up: README + checklist audit

- Done: README refreshed for the RC state (status paragraph, `napf ui`
  ✅ row, try-it now runs `napf run flows/smoke` + `napf ui`); v1
  checklist audited — NFR-05 and TR-7 ticked (oversights: every clause
  had landed with tests), CHANGELOG left stale-by-design (regenerates
  during v0.1.0 release preparation, RELEASING).
- Open: **NFR-08 is the single unticked v1 box** — "overhead
  negligible vs HTTP" was never measured; candidate for the
  manual-testing window.
- Next: three-OS CI sweep, manual testing on dev4, then v0.1.0 release
  preparation and publication.

## 2026-07-09 — S4 stage close: 0.1.0.dev4

- Done: version bumped at stage completion (RELEASING);
  PLAN S4 header flipped. All S4 FRs (1001–1007) ticked with tests.
- Next: the three-OS CI sweep confirms the DoD; then the manual-testing
  window on the dev4 checkpoint and v0.1.0 promotion (tag-driven,
  RELEASING).

## 2026-07-09 — S4/M6 subflow UX (FR-1007)

- Done: ghost-wires (`templating.referenced_nodes` Jinja2-AST
  extraction → flow-detail `template_refs` → dashed view-only edges;
  invisible anchors render only where needed — a stray handle broke
  the E004 e2e's `.react-flow__handle-right` locator), drill-in
  (double-click / inspector; `zoomOnDoubleClick` off), "used in N
  places" (`used_by` server-side, links on the flow-header inspector),
  clone-to-new-flow (`POST /api/flows/clone` folder fork + node
  repoint, D31). 318 pytest + 31 Vitest + 32 Playwright green
  (`subflow.spec.ts` owns flows/parent/child/ghostcase).
- Decided: D31 (clone repoints the invoking node; the owner's "pack
  selection to new flow" reading became backlog R5 — extract-to-
  subflow is a different feature needing its own design pass).
- Next: stage close — 0.1.0.dev4, 3-OS CI sweep, then the
  manual-testing window and the v0.1.0 promotion path (RELEASING).

## 2026-07-09 — S4/M5.5 run-mode inspection polish

- Done: all four M5.5 items, pure UI over existing events — port
  traffic painting (carried handles glow, tooltip = last
  `value_preview`), wire/port click → crossed-messages list in the
  run panel (`matchesTraffic`, the wire twin of the node filter), log
  append ring (last 50, node shows newest+count), and `RunInspector`
  (right panel returns in run mode: firings, request summary,
  per-port last values, log history). 6 new Vitest (28) + extended
  run e2e (27 total, green at `--workers=1` and parallel).
- Decided (UI pins, in PLAN): arrivals paint the input port WITHOUT
  flashing the node (not a firing); port click target = label+handle
  only so the node's middle stays a node click; retry errors stay on
  the request summary while the next attempt runs.
- Next: M6 — subflow UX + stage close (0.1.0.dev4).

## 2026-07-08 — M5 polish + run-debugging/replay plan adopted

- Done: tail-follow scroll fix + explicit follow toggle (pressed
  state, scroll releases, press re-engages; e2e in the abort spec).
  Owner drove M5 and requested the debugging/replay feature set —
  planned, not implemented.
- Decided: **D30** (pause = dispatch gate w/ pause-epoch time
  offsetting; breakpoints = runtime WIRE-holds, E004 makes wire ≡
  input-port, never flow.yaml); PLAN grew **M5.5** (port painting,
  wire/port click inspection, log append ring, run-mode inspector —
  pure UI) + **Post-v0.1.0 backlog R1–R4** (scrubber, pause/step,
  breakpoints, opt-in payload capture); RELEASING: dev4 → manual
  testing → v0.1.0 promotion, then independently verified feature work.
- Fixed the 3-OS `ui e2e` CI red during M5: run.spec ran
  flows/smoke while editing.spec (parallel worker, shared workspace)
  rewrites its nodes.py with a non-original summarize — deterministic
  on 2-core CI, masked locally. Lesson: spec files share ONE
  workspace; a spec that RUNS a flow must own it exclusively
  (flows/passcase copy) + mutating specs restore what they break.
  Reproduce CI ordering with `--workers=1` during diagnosis.
- Next: M5.5, then M6 + stage close (0.1.0.dev4).

## 2026-07-08 — S4/M5 run on canvas + history

- Done: RUN MODE over the M1 WS — `runview.ts` reducer (events →
  node/edge/log overlay; 14 Vitest incl. EC20 → `incomplete`),
  breathing/flash nodes + travelling-dot wires (`RunEdge`), live log
  values on log nodes, run controls (env dropdown + hybrid inputs
  popover), bottom run panel (event stream w/ expandable full wire
  detail, abort, node-click filter), history tab replaying any JSONL.
  5 Playwright e2e (27 total); server untouched — M1's surface held.
- Decided: **D29** (owner fork) — run mode LOCKS editing and animates
  from real events only; root-frame scope (containers pulse for their
  subtree; per-container child-frame attribution needs an engine event
  change → M6 if wanted); server-run reports stay deferred.
- Next: S4/M6 — subflow UX + stage close (0.1.0.dev4); confirm job-level
  CI evidence first (NFR-10 lesson).

## 2026-07-08 — S4 consistency audit: code clean, docs synced

- Done: full S4/M1–M4 audit — all planned deliverables incl. the five
  M4 leftovers verified present, wired, and test-backed (305 pytest +
  8 Vitest + 22 Playwright green locally; CI 28877584927 all 7 jobs
  green). Code needed nothing; docs did: AGENTS.md stack line
  Monaco→CodeMirror 6, yaml-profile.md rewritten (no canvas emitter —
  the UI PUTs JSON, the server emits; FR-1003), README status prose
  unstuck from S1, FR-203 annotation, D23 consequences.
- Decided: **D28** — e2e chromium-only in v1 + always the built bundle
  through the real server (promoted from the S4/M2 journal note,
  owner-confirmed); EDGE_CASES ledger is live during implementation —
  **EC38–EC41** recorded (path-traversal guard, E-codes don't 400 the
  detail GET, broken nodes.py still saves, undeclared-port handles).
- Next: unchanged — S4/M5 run on canvas + history (FR-1005).

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
  confirm job-level CI evidence first (NFR-10 lesson).

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
  CI; confirm job-level results before trusting the FR ticks
  (NFR-10 lesson).
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

- Done: `ui/` (Vite 8 + React 19 + TS 6 + Zustand + @xyflow/react 12;
  npm; Node 22 pinned dev-only) — hello-canvas rendering one node per
  discovered flow from the real API; vite builds into
  `src/napflow/server/static`; hatchling `artifacts` forces the
  gitignored bundle into sdist+wheel (NFR-03 gated in CI `ui` job AND
  release.yml — a release can't ship a UI-less wheel); Playwright
  harness (fresh `napf init` per run via `e2e/serve.mjs`) + 2 smokes
  green locally; static tests made bundle-independent (monkeypatched
  STATIC_DIR). NFR-03 + FR-806 + FR-1001 ticked. Also: M1's CI red was
  a missed `ruff format` — fixed; TR-9's Windows leg awaited confirmation.
- Decided: e2e = chromium-only in v1 (default-browser UX, engine
  matrix is overkill); e2e always runs against the BUILT bundle
  through the real server (the wheel-user path, never vite dev);
  README run-row unstuck (was 🚧 S2).
- Next: S4/M3 — read-only canvas (flow list, `main:` default open,
  nodes/edges/layout render, D11 port coloring, E/W on canvas;
  FR-1002 render half). Confirm the UI job + TR-9 Windows leg first.
- [Update, same day: all seven jobs passed — TR-9 Windows leg + NFR-03
  UI job both CONFIRMED, ticks finalized. The
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
  Playwright harness + first smoke). Confirm TR-9's WINDOWS CI leg before
  treating the tick as final (NFR-10 lesson).

## 2026-07-06 — S3/M5 hierarchical frames — STAGE S3 COMPLETE

- Done: flow + loop nodes on hierarchical frames — one pump/budget/
  quiescence, per-frame `in_flight`+`done` completion (the QUIESCENT
  trick per frame), path ids, subtree outcome sums (D21 payloads),
  flow-timeout-aborts-child (TR-8), loop results/errors (EC06/EC36),
  fresh_session cookie isolation, `check_run_closure` run gate.
  TR-3/5/8 + FR-404/405/410/515/516 ticked. 265 tests (15+1 new).
  Version 0.1.0.dev3 at stage close. Full node catalog
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
  collection in the dev-less venv. Batched S2/M2–M5 work meant the job
  first executed only after it had been documented as complete; the
  NFR-10 tick was premature (amended in REQUIREMENTS). Fixed via
  pytest.importorskip; all four legs passed. Lesson: verify job-level
  results, not just an aggregate gate, when ticking CI-backed NFRs.]

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

- Done: verified zero platform-conditional code in src/tests and the 3-OS CI
  matrix green since M0; updated CLAUDE.md, PRODUCT, NFR-02, PLAN S4 DoD, flow
  schema, engine spec header; added OS classifiers to pyproject.
- Decided: D26 — Linux same tier as macOS/Windows (owner call; "Linux
  via CI" hedge dropped since the continuous matrix already proves it).
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
  local server, exit 0/1. 205 tests green (31 new). Version bumped to
  `0.1.0.dev2` at stage close per RELEASING.md.
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
  repo renamed `napflow-prototype` → `napflow` (old URLs redirect);
  version bumped `0.1.0.dev1` +
  `[project.urls]`; docs consistency pass (RELEASING.md wired into
  CLAUDE.md/PLAN; "prototype" wording → "v1" in WM).
- Decided (`docs/RELEASING.md`, adopted): `0.1.0.devN` bumps in the
  stage-closing change; releases are tag-driven via `release.yml`
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
  ruff + import-linter contract test (NFR-01 green), 3-OS CI
  workflow, cliff.toml + generated CHANGELOG.md (NFR-11), README, LICENSE
  + NOTICE (NFR-07), uv.lock committed; wheel builds clean.
- Decided: hatchling as build backend (S4 will need to force-include the
  pre-built UI in the wheel); dev on 3.12 via `.python-version`.
- Next: S1/M1 (Pydantic models). [Update, same day: CI green on all
  three OS — M0 closed.]

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
  PRODUCT, REQUIREMENTS, yaml-profile, and PLAN.
- Decided: tech stack stays as-is (niquests/BlackSheep, with adapter
  seam + compat CI as insurance); endpoint collections and
  Postman/OpenAPI import parked for v2; tooling = native tasks +
  built-in memory + git-cliff changelog at M0.
- Next: S1/M0 — repo scaffolding (pyproject, CI matrix, import-linter,
  changelog toolchain).
