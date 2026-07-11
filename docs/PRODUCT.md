# napflow — Product Definition

Status: v0.x product direction, amended through the v0.2 plan on
2026-07-11. Companions: `REQUIREMENTS.md` (what to build),
the spec files (how it behaves), `DECISIONS.md` (why).

## One-liner

**napflow** is a local-first, git-friendly, node-based flow editor and
engine for complex API request/response processing — "Postman Flows, but
open, file-based, Python-powered, and composable."

## Problem

QA teams testing real APIs live in multi-step scenarios: auth chains,
create-then-poll-until-ready, data-driven batches over fixtures, response
surgery between calls. The available tools force a bad trade:

- **GUI API clients** (Postman, Insomnia): scenarios locked in
  proprietary or cloud formats; diffs unreviewable; logic in JS snippets;
  CI is an afterthought or a paid tier.
- **Pure code** (pytest + requests): full power, but the *shape* of a
  scenario is invisible — onboarding and review of flow logic is slow,
  and non-coders can't even read it.
- **General automation tools** (Node-RED, n8n): wrong domain (no run
  reports, no assert-driven exit codes), JSON diffs that bury logic in
  layout noise, JavaScript instead of the Python that QA teams already
  write.

napflow removes the trade: flows are **visual and plain files in git**,
logic is **real Python** (pytest-able), and every flow runs **headless in
CI** with assert-driven exit codes and full wire-level history.

## Target users

- **Primary:** QA automation engineers / SDETs who own API test suites —
  comfortable with Python, git, and CI pipelines.
- **Secondary:** backend developers exploring/debugging APIs; manual QA
  who need to *read* and review flows without writing them.
- **Context:** individuals and small teams, self-hosted, everything
  local. Corporate-friendly licensing is a hard requirement (D16) —
  the target user must be able to adopt it without a legal review.

## Core promises (product invariants — never compromise)

1. **Git-friendly** — a flow is one YAML file + one `nodes.py` in one
   folder; diffs are small and reviewable; layout never pollutes logic
   diffs (canonical serializer, D23).
2. **Composable** — any flow is usable as a node inside another flow
   (by reference, not copy); every canvas is a flow.
3. **Python-native** — parsing/logic in real Python functions, testable
   with pytest; the engine itself is importable
   (`from napflow.core import run_flow`).
4. **CI-first** — headless `napf run` with assert-driven exit codes is a
   first-class citizen; a failure anywhere in the frame tree — including
   a required output that was never produced — is exit 1 (D18/D20).
5. **Full observability** — complete request/response detail (headers,
   bodies, timing, retries) remains inspectable in run history without
   silent loss. v0.2 stores large content once and loads it lazily
   (D34); canonical local truth stays exact, while CI/report/export
   views apply explicit redaction (D35).
6. **Codegen-ready** (future, design-constrained today) — flows →
   standalone Python (niquests clients, Pydantic models), strictly
   one-directional.

## Primary use cases

1. **Retry-until-ready polling** (flagship): create a job → poll with a
   guarded cycle → assert final state; "gave up after N attempts" is
   exit 1 with the reason in the report.
2. **Auth/session chains**: login flow extracts a token; reused as a
   subflow node by every other flow in the workspace.
3. **Data-driven suites**: loop over a CSV/JSON fixture, one body-flow
   invocation per record, results and failures collected per iteration.
4. **CI regression gate**: `napf check && napf run` in the pipeline;
   JUnit report; exit codes gate the merge.
5. **Exploratory debugging**: run on canvas, inspect full wire detail per
   node, replay any historical run from its JSONL.
6. **Reviewable scenario library**: flows as folders; PRs show clean YAML
   diffs; manual QA reads the canvas, automation owns the Python.

## Positioning

| Alternative | What it is | Why napflow instead |
|---|---|---|
| Postman Flows | visual API flows, cloud-bound | local plain files, git diffs, real Python, CI exit codes, subflow composition |
| Node-RED | general-purpose flow automation | API-testing domain (asserts, run reports, env profiles), YAML+Python over JSON+JS, layout-free diffs |
| n8n | workflow automation, fair-code license | Apache-2.0 (no license review needed), test-focused engine, file-per-flow |
| Bruno | git-friendly API client | flows/graphs with cycles, loops, guards — not linear request collections |
| pytest + requests | code | visual composition + observability out of the box; and napflow core *is* importable in pytest, so it complements rather than replaces |

(Full build-vs-adopt analysis: D01.)

## Experimental compatibility during v0.x

`v0.1.0` means “first version working end to end,” not stable formats.
Breaking flow, event, API, and UI changes are expected throughout the
0.x package series when they improve the product. The current
`schema: napflow/v1` marker is experimental until package v1.0; v0.x
release notes identify breaking changes, and migrations are best-effort
rather than a compatibility promise (D33).

## Non-goals (current v0.x direction)

- **Not a daemon** — no timer/webhook triggers in core; cron +
  `napf run` covers scheduling (D08).
- **No code→flows import** — codegen is strictly one-directional (D02).
- **No cloud/SaaS component** — local-first, single wheel, localhost UI.
- **No Docker or Node.js at runtime** (D03).
- **Not a load-testing tool** — one logical scenario per run, not
  traffic generation.
- **No strict port typing** — soft types with canvas warnings only (D11).
- **No collaborative editing** — last-write-wins + reload prompt; deeper
  conflict resolution deliberately deferred.

## Success criteria (v0.1 working milestone)

- `uv tool install napflow && napf init && napf run flows/smoke` is
  green in under five minutes on macOS, Windows, and Linux — fully
  offline, no external services (httpbin demo is separate, EC34).
- The flagship retry-until-ready pattern is buildable on the canvas
  without reading the engine spec.
- A flow PR (add a node + edge) is reviewable at a glance — no layout
  noise in the diff (guarded by the round-trip golden test).
- A failed assert three subflow levels deep produces exit 1 and a report
  line naming the assert (D20).
- `from napflow.core import run_flow` works in pytest with no server
  process.

## Release roadmap

- **v0.1.0 — first working version.** The four build stages are complete:
  loader/check → engine/CLI → full nodes/worker → server/UI canvas.
  Released as a developer-preview milestone with known hardening work;
  no public announcement or stability promise is required.
- **v0.2.0 — full-fidelity hardening and replay** (D33–D37, PLAN):
  centralized workspace/durable-save boundaries; fair cancellation-safe
  engine and worker lifecycle; bounded active loop/server/browser state;
  versioned JSONL plus store-once full-fidelity blobs; raw local truth
  plus redacted CI/export views; paged/lazy replay, child-frame drilldown,
  and timeline scrubber; public `run_flow`; deterministic packaging and
  authoritative release gates. This is the next committed release.
- **After v0.2 candidates:** pause/resume/step and wire breakpoints (D30),
  pack-selection-to-subflow (D31), `poll`/`duplicate`, inline loop bodies,
  marker-based `collect`, `napf check --write-env-example`, worker-pool
  expansion, `napf ui --app` (chromeless Chromium app-mode window via
  `msedge`/`chrome --app=<url>`, falling back to plain
  `webbrowser.open` — app-like feel, zero new deps; today the UI always
  opens the default browser), fine-grained runtime-token redaction
  (EC10), descendant process-tree cleanup (EC22), and preemptible
  template/hard-deadline semantics (EC27/EC35). These stay compatible
  with the v0.2 lifecycle/replay foundations but do not delay that
  hardening release.
- **v1.0 direction:** make selected flow/event/public-API formats stable,
  publish migration policy, and remove the experimental `napflow/v1`
  qualification. Stability is an outcome of real 0.x use, not inferred
  from the current schema marker.
- **v2 direction**: codegen (flows → niquests clients + Pydantic
  models) — scoped to the *reducible subset*: linear chains and
  recognized patterns (retry cycles → `while` loops). Arbitrary cyclic,
  merge-heavy graphs would require emitting a mini-runtime, which
  defeats "reads like requests code"; no v0.x decision may assume
  full-graph codegen. Also: revisit conflict handling beyond
  last-write-wins.
- **v2 direction, imports** (parked 2026-07-02): endpoint collections —
  a workspace-level catalog of reusable endpoint definitions that
  request nodes reference (`endpoint: users.create` + per-node
  overrides) — and one-directional **Postman collection / OpenAPI
  import** generating that catalog + request-node scaffolds. Import is
  generation, never sync (same philosophy as D02/codegen). v0.x
  compatibility: `endpoint:` lands later as an additive optional
  request-config key and `napf import` as a new subcommand — nothing to
  reserve today; `defaults.request` + env profiles cover the small-scale
  need meanwhile.

## Distribution & licensing

Single pip-installable wheel with the pre-built UI inside; `napf ui`
serves everything on one localhost port. Apache-2.0 + NOTICE file (the
attribution lever); no CLA (Apache §5 inbound=outbound); DCO added only
when external contributors appear (D16). Open risk: verify PyPI name
"napflow" availability before public attachment to the name.
