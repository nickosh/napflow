# napflow — Development Plan (v1)

Status: adopted 2026-07-02. This file sequences the stage backlog —
`REQUIREMENTS.md` stage tags define the *scope*; this defines the *order
of attack* and definition-of-done per milestone. Tick boxes as
milestones land; append course corrections, don't rewrite history.

## S1 — loader, models, `napf check`  ← current

Deliverable: `napf init` / `napf list` / `napf check` usable in CI.

- [ ] **M0 — Repo scaffolding**
  - [ ] `pyproject.toml` (uv-managed), `napflow/{core,cli}` package layout
  - [ ] pytest + ruff; GitHub Actions matrix macOS/Windows/Linux from
        day one (NFR-02)
  - [ ] import-linter: `core` imports nothing from cli/server (NFR-01)
  - [ ] Changelog toolchain: `cliff.toml` committed + `CHANGELOG.md`
        (Keep a Changelog format via git-cliff; conventional commits
        already in use since the first commit) (NFR-11)
  - [x] Working journal live: `docs/JOURNAL.md` + the CLAUDE.md rule
        (dated entry per milestone / PR-sized commit). SessionEnd
        breadcrumb hook implemented 2026-07-04:
        `.claude/hooks/session-end-log.sh` appends per-session lines to
        gitignored `.claude/sessions.log` (the agent-written journal
        stays the load-bearing part).
  - DoD: empty package, green CI on all three OS.
- [ ] **M1 — Models**: Pydantic v2 manifest + flow models covering the
      full node catalog; JSON Schema export. (FR-101/201/206)
- [ ] **M2 — Loader + write path**: safe ruamel read with position
      marks; canonical emitter; CommentedMap as single write source;
      golden round-trip corpus lands *here*, before anything builds on
      it. (FR-204/205/208, TR-7)
- [ ] **M3 — Discovery**: manifest walk-up; flow discovery; env
      profiles + dialect. (FR-102/103)
- [ ] **M4 — Checker**: E001–E012; W101 (guard-removal acyclicity)
      through W107; AST-derived python ports; closure checking;
      file:line diagnostics. (FR-301–309)
- [ ] **M5 — CLI**: `napf init` (incl. `flows/smoke` scaffold),
      `napf list`, `napf check`, exit codes. (FR-801/802/805, FR-107)

S1 DoD: every S1 checkbox in REQUIREMENTS ticked with a test; `check`
catches all E/W codes on a fixture corpus; round-trip byte-identical
across OS in CI.

## S2 — engine core + `napf run`

Scheduler, frames, budget, deadline (FR-4xx); templating incl. the
native-value rule (D25); request/condition/assert/start/end; events +
JSONL + masking; `napf run` with reports and exit codes.
DoD: a linear request→assert flow runs headless with correct exit
codes; TR-2 (quiescence) and TR-3 (required-End) green.

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
