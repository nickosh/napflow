# napflow — Working journal

Newest first. One short entry per working session / milestone:
**done / decided / next**, 2–5 lines each. This is the cross-session
progress log — keep it lean; details live in specs, DECISIONS, and git.

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
