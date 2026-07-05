# napflow — Working journal

Newest first. One short entry per working session / milestone:
**done / decided / next**, 2–5 lines each. This is the cross-session
progress log — keep it lean; details live in specs, DECISIONS, and git.

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
