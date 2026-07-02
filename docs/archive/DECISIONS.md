# napflow — Decision Log (ADR-style)

Why things are the way they are. Date format: 2026-06. All decided during
initial design (June 2026) unless noted. Reversing any of these requires
understanding the rationale first.

## D01 — Build new instead of adopting existing tools
No OSS project combines: node-based API flows + Python processing +
git-friendly single-file flows + nested subflows + Python codegen.
Closest misses: Node-RED (JS functions, ugly JSON diffs, no codegen),
n8n (fair-code, not git-friendly), Ryven (experimental, not API-focused),
Bruno (git-friendly but request-collections, not flows), Flyde/Langflow
(TS codebases / LLM pipelines).

## D02 — YAML as source of truth, Python alongside (not code-first)
GUI must write back to source of truth. Writing YAML back is trivial;
round-tripping arbitrary Python (AST editing, libcst) is fragile and
effectively impossible for unrestricted code. Chosen hybrid: flow.yaml
holds the graph; python nodes reference functions in sibling nodes.py
(real, lintable, pytest-able file). Codegen stays one-directional
(flows → code) — generation is easy, reverse parsing is the trap.

## D03 — Engine in Python, single-wheel distribution
Python execution + Python codegen are first-class ⇒ Python-native engine.
Ship like Jupyter/Streamlit: pre-built React UI bundled as static files
inside the wheel; BlackSheep serves UI+API+WS on one localhost port;
`uv tool install napflow` → `napf ui`. No Docker/Node for users.

## D04 — BlackSheep + niquests (over FastAPI + httpx)
User preference, validated: BlackSheep has first-class Pydantic binding
(whole schema is Pydantic anyway); niquests is requests-compatible with
AsyncSession, HTTP/2/3, multiplexing — and generated clients will read
like familiar requests code. Web layer is a thin adapter; core never
imports it, so the choice is low-lock-in.

## D05 — Message-driven execution, not DAG (pivotal)
Retry-until patterns ("assert → if/else → delay → back to request") are
cycles, inexpressible in a DAG. Chosen: Node-RED-style message
propagation; cycles legal; safety restored by guard nodes (counter,
timeout — both with reset inputs) + static rule "every cycle contains a
guard" (W101) + runtime message budget. Subflow REFERENCES stay a strict
DAG (E007) — recursion in wiring yes, in flow references never.

## D06 — Single edge per input; fan-out outputs; merge-only joins
Multi-edge inputs create semantic ambiguity (race? wait? overwrite?) —
merge node makes the choice explicit (any|all|collect). Multi-edge
OUTPUTS have exactly one meaning (copy to all subscribers) → allowed.
Considered and rejected: strict one-edge everywhere + Duplicate node —
would tax the most common pattern (response → several consumers) for
zero ambiguity gain; no mainstream node system does it; fan-out maps to
natural generated code (use a variable twice).
Mental model: inputs = single-subscription mailboxes, outputs = broadcasts.

## D07 — Everything is data
Signals are messages whose value you ignore. Every non-terminal output
carries a payload (pass-through semantics specced per node); any output
can drive any input including request `trigger`; trigger payload usable
as {{ trigger }}. Consequence: no empty-pulse concept, one wire type.

## D08 — Every canvas is a flow; Start/End nodes ARE the interface
No special "workspace canvas" — flows/main is just a flow. Start ports =
flow inputs; End ports = flow outputs (real input ports, wired by edges
— the from:-reference variant was rejected as a second source of truth).
One calling convention everywhere: UI run, subflow node, `napf run -i`,
pytest run_flow(), future generated functions. Start triggers (timer etc.)
were considered and REMOVED — they'd make napflow a daemon; cron +
napf run covers it.

## D09 — Flow nodes are references, never embeds
flow: flows/login (workspace-relative path = identity). Copy-paste
duplicates the reference; explicit "Clone to new flow…" forks the folder.
Embedding would balloon files, break shared-fix semantics, kill diffs.
Scope isolation = frames (variables, guard state), NOT nested manifests;
env + request defaults stay global.

## D10 — Jinja2 everywhere; no second expression language
{{ }} interpolation in any config string; bare Jinja2 expressions in
condition/switch/assert `expr`. SandboxedEnvironment + StrictUndefined
(undefined → node error, never silent empty string in a URL). JMESPath
appeared in early drafts and was removed for one-syntax consistency.

## D11 — Soft port types
string|number|boolean|object|list|any (default any). UI colors and warns
on mismatch (W102), never blocks — API payloads are too dynamic for
strict typing; zero typing loses canvas feedback.

## D12 — Python worker subprocess + per-node timeout IN v1
Originally deferred; pulled into v1 because a stuck python firing was the
engine's only unrecoverable hang (threads can't be killed; budget never
ticks on a single stuck task). Persistent worker per flow module
(JSON-lines over pipes; spawn-per-firing rejected: 50–200ms tax),
terminate→grace→kill→lazy respawn, crash isolation free. Side effect
consciously accepted: stdlib-only rule lifted — packages = configured
interpreter's env (python.interpreter manifest key, default = napflow's
own interpreter).

## D13 — Full observability, always
JSONL per run (.napflow/runs/), append-only, events identical to the live
WebSocket stream (replay = re-read file). COMPLETE request/response
bodies always stored (10MB/body valve, truncated marker) — explicit user
requirement; partial capture rejected. Secrets masked at emission so
history is shareable by construction. Logs persisted too.

## D14 — Quiescence termination with sentinel
Run ends when in-flight count hits zero; the decrement that reaches zero
enqueues QUIESCENT to wake the pump. The naive `while in_flight > 0:
await queue.get()` loop deadlocks when the last task finishes without
emitting — found in spec review; the sentinel is load-bearing.

## D15 — Env model
All real envs/*.env gitignored; profiles auto-discovered (filename stem);
process env overrides files (CI overrides need no file edits);
env.required per flow fails fast; secret masking by name pattern at
workspace level. napf sync was designed then DROPPED — no registry
exists, so nothing needs syncing; napf check covers broken references.

## D16 — Licensing: Apache-2.0, NOTICE, no CLA
Priority: adoption by QA teams inside companies (AGPL ban-lists would
block exactly the target users). Relabel-protection accepted as weak —
that's trademark territory, not license territory; NOTICE file is the
one enforceable attribution lever. No CLA (Apache §5 inbound=outbound
covers redistribution; CLA friction deters contributors; relicensing
flexibility irrelevant under permissive). DCO deferred until external
contributors appear (not retroactive, cheap to add later).

## D17 — Source nodes need firing semantics (review finding)
In message-driven engines, port-less nodes never fire. get → required
trigger input; fixture → optional trigger (unconnected = auto-fire once
at frame start). Found in final spec review along with: End from:/in
duplication (resolved → real input ports), assert JMESPath remnant
(→ Jinja2), binary bodies (→ {__binary__, content_type, base64}),
loop body convention (Start must declare `item`, may declare `index`).

## Known open risks (watch during implementation)
- Merge `all` clear-slots vs rule-2 latest-value under fast cycles —
  most test-worthy engine code.
- Ghost-wires for template references — elegant on paper, may be noisy
  on dense canvases; let real usage decide.
- niquests timing granularity (dns/connect/tls) — fields optional in
  events for a reason; verify what it actually exposes.
- PyPI name "napflow" availability — check before attachment.
