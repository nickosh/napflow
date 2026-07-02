# napflow — Decision Log (ADR-style)

Why things are the way they are. Date format: 2026-06. D01–D17 decided
during initial design (June 2026); D18–D22 in the 2026-06-14 edge-case
review, confirmed 2026-07-02; D23–D25 adopted 2026-07-02. Reversing any
of these requires understanding the rationale first.

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
Note (2026-07-02): error ports are data ports too — the success/error
separation is routing, not data-ness. Merging errors into success ports
was considered and rejected: it would kill structural failure branching,
destabilize template shapes, and break the unconnected-error ⇒ run-failed
safety rule.

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
history is shareable by construction (scope precisely defined in D22).
Logs persisted too.

## D14 — Quiescence termination with sentinel
Run ends when in-flight count hits zero; the decrement that reaches zero
enqueues QUIESCENT to wake the pump. The naive `while in_flight > 0:
await queue.get()` loop deadlocks when the last task finishes without
emitting — found in spec review; the sentinel is load-bearing. The same
mechanism requires a guard for the empty-seed case — if nothing
increments `in_flight`, no decrement ever enqueues QUIESCENT; `pump`
finalizes immediately when the post-seed count is zero (EC08).

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

## D18 — Declared outputs are required by default (incomplete run = failure)
Confirmed 2026-07-02 (required-by-default over default-optional).
The message-driven engine treats "node never fired" as benign (`skipped`),
which is correct for optional branches but means a dropped *required*
result passes silently — a false green for a CI-first tool. Chosen: an
End port that holds no value at quiescence makes the run `failed`. End
ports gain `required: bool` (default `true`); set `required: false` for
genuinely conditional outputs (e.g. an `error_detail` port set only on a
failure branch). Rejected: warning-only (the v0.3 behavior — too quiet
for the headline use case); a global `--strict` flag (puts the safe
behavior behind a toggle). Load-bearing: D19's "gave up" failure and the
loop-iteration failure definition (D20) both route through this rule
rather than through special port magic.

## D19 — Guard exhaustion ports are ordinary outputs, not error ports
Wire-format v0.3 lumped `exhausted`/`expired` with `error`/`failed`
("errors travel as data"), but whether a tripped guard is a *failure*
depends entirely on intent: "retry until ready, gave up" is a failure;
"process up to N items, done" is a clean stop. Encoding failure into the
port is therefore wrong. Chosen: `exhausted`/`expired` are pass-through
outputs carrying the triggering message; unconnected = dropped, like any
non-error output. Failure for the retry case comes from D18 (the
unreached `end.job` fails the run) or from an assert the user wires on
the exhausted path. A non-failing lint W106 flags an unconnected
exhaustion port for canvas feedback. Consequence: the
`create_until_ready` example wires `attempts.exhausted` explicitly.

## D20 — Results aggregate run-wide; only data is frame-isolated
Frames isolate *data* (variables, guard state, node outputs cross only
via Start/End). They do **not** isolate *outcomes*. Assert results,
python-asserts, and unhandled error-port messages from every frame —
subflows and loop bodies included — aggregate into the single run-level
report and exit code. Run state is the worst outcome anywhere in the
frame tree (`error` > `failed` > `passed`). A loop "iteration error"
(routed to the loop's `errors` port and counted in the run) is defined as
a body frame ending `failed`/`error`; `on_error: stop` halts scheduling
of further iterations but does not change how already-failed iterations
score. Without this, assert-driven exit codes (the whole point of
`napf run`) are meaningless for any flow that uses subflows or loops.

## D21 — `flow` node exposes an implicit `error` port
Confirmed 2026-07-02 (add the port, over aggregation-only). A subflow
that ends `failed`/`error` had no outlet on the `flow` node (ports are
derived from target Start/End), unlike `request`/`python`/`loop`. Chosen:
the `flow` node gains an implicit `error` port (consistent with "errors
are data") that fires when the child frame ends `failed`/`error`,
carrying a summary `{state, failed_asserts, unhandled_errors}`;
unconnected → contributes to run failure per D20, so behavior is safe by
default and branchable when wired. Rejected: aggregation-only (failures
count toward the run but can't drive a fallback branch — too weak given
subflows are a core composition unit). Rejected (user challenge,
2026-07-02): emitting errors on the same port as data — see D07 note.
Consequence: the name `error` is **reserved** on End ports and on python
`outputs` (both would collide with an implicit error port) — E012.

## D22 — Secret masking: declared values only, substring scan, runtime tokens deferred
D13 promised "history is shareable by construction." True only for
env-declared secrets. Specifying the gap rather than overclaiming:
(1) masking replaces the *values* of env vars whose names match
`environments.secrets` patterns (active profile + process env), via
substring scan with a minimum length (ignore values shorter than 5
chars) to catch tokens embedded in URLs/bodies without over-masking
short common strings; (2) secrets *acquired at runtime* (a bearer token
in a login response body) are **not** env vars and are stored in full —
the shareability guarantee is scoped to declared secrets and stated as
such; (3) runtime redaction (a `set ... secret: true`, or a response
field-path redaction directive) is a roadmap item, not v1. Rejected:
silent partial coverage behind an absolute-sounding promise.

## D23 — On-disk format is YAML, pinned to a safe, canonical profile
YAML stays the serialization format for flows and the manifest — but
"raw" YAML is not used. It is constrained to a fixed profile so the
footguns that make YAML dangerous cannot bite: implicit type coercion
(the `no`/`on`/`off`/leading-zero/`HH:MM` class — all common in HTTP
headers, params, and bodies), indentation ambiguity, anchors/aliases,
and arbitrary-object loading.

The choice is driven by two constraints specific to napflow rather than
by format aesthetics: parsers must be mature in **both** Python (engine)
and JS/TS (canvas), and files are **machine-written first, hand-edited
second**, so deterministic, diff-clean output dominates. YAML is the only
candidate that clears both bars while keeping comments, readability, and
JSON Schema validation.

**The profile (all five are part of the decision):** (1) safe loader
only; (2) one shared canonical serializer per ecosystem — block style,
no anchors, 2-space indent, scalars never wrapped, LF + UTF-8; (3)
double-quoted style forced for string scalars only — ints/floats/bools/
null stay bare; (4) parsed structure validated against JSON Schema (the
schema, not YAML inference, is the type authority — this is why
force-quoting strings is safe); (5) non-failing lint W107 for the
residual hand-edit footgun. Implementation notes: `yaml-profile.md`.

**Rejected.**
- *HUML* — aimed squarely at this problem, but v0.1.0/experimental with
  parsers only in Rust, Go, and OCaml. Betting a foundational,
  expensive-to-change layer on a pre-1.0 format is unjustified.
- *NestedText* — the best philosophical fit (no type coercion at all)
  and the strongest *future* candidate, but its TS parser is unproven.
  Held as a possible spike, not a commitment.
- *JSON5 / JSONC* — viable machine format with trivial canonicalization,
  but weaker for hand-editing nested graphs. The natural fallback if
  telemetry ever shows hand-editing is rare.
- *TOML* — good for the manifest, poor for the flow graph (nested,
  heterogeneous arrays-of-tables get ugly). Splitting formats is its own
  cost.
- *KDL / StrictYAML* — node-tree-native and footgun-free respectively,
  but both lose dual-ecosystem parser + schema maturity. KDL also buys
  less than it looks like: a cyclic graph still encodes edges as ID
  references, not tree structure.

Meta-rationale: a serialization format is load-bearing and costly to
migrate; novelty is a cost here, not a feature. Boring-but-ubiquitous
wins, and YAML's danger is fully containable by construction.

**Consequences.** Canvas and CLI must both emit through the shared
serializer (a divergent emitter silently reintroduces noisy diffs); a
round-trip golden test (`emit → parse → emit` byte-identical,
`parse(emit(x))` deep-equals `x`) guards the clean-diff promise in CI.
Revisit if HUML reaches ~1.0 with maintained Python + JS parsers, or if
hand-editing proves rare enough that JSON5 wins.

## D24 — Timeout model: bounded leaf work by default; timeouts are error-port data
(2026-07-02, closing EC25–EC27; owner-confirmed model: global default +
per-node override.) Three layers, one rule each:

1. **Per-firing ceiling.** `max_seconds` is settable on ANY node; the
manifest default (`defaults.run.node_timeout_s`, 300) auto-applies only
to `request` and `python` — the two potentially-unbounded leaf firings.
`delay` is self-bounded by its config; `loop`/`flow` are bounded
transitively (every child firing is bounded, budget caps the rest), and
a default that kills a healthy 10-minute data suite would manufacture
false REDS — the inverse of the D18 false-green problem. Explicit
`max_seconds` is honored anywhere.

2. **Routing.** A tripped ceiling is a node error — `{error_kind:
"timeout"}` on the node's **error port, never a success port** (shape
stability for downstream templates; preserves the W103 unwired-error ⇒
run-failed safety net). Per node shape: `request`/`python` → `error`
port (wired = handled, run can pass); `flow` → child frame ends
`aborted` (outcomes it already recorded still aggregate per D20) +
implicit error port (D21) fires with `{state: "aborted", error_kind:
"timeout"}`; `loop` node-level timeout has no outlet → unhandled node
error ⇒ run `failed` (EC24) — branchable by wrapping the loop in a
subflow with `max_seconds` on the `flow` node; port-less nodes → EC24.
Rejected: timeout objects on success ports (every consumer must defend
against two shapes); an implicit `error` port on loop (an `error`
beside `errors` on one node is a naming trap).

3. **Run deadline.** `defaults.run.run_timeout_s` (default null = off;
the CI job timeout is the outer backstop) + `napf run --timeout N`
override. Expiry is a safety rail like the budget: run `error` (exit 2),
`error_reason: run_timeout`, in-flight work cancelled, report and JSONL
still written — unlike a CI SIGKILL, which loses the report. Rejected:
state `aborted` (reserved for user cancellation, exit 130); a non-null
default (would surprise legitimately long suites).

## D25 — Native-value templating: a single-expression config value keeps its type
(2026-07-02, senior review, EC37.) `{{ }}` rendering produces strings, so
`body: "{{ nodes.login.response.body }}"` would emit a Python-repr'd dict
(`{'a': 1}` — not JSON) — yet passing structured data between nodes is
the core operation of an API tool. Chosen (the Ansible/GitHub-Actions
rule): a config value that consists of **exactly one `{{ expression }}`**
(ignoring surrounding whitespace) is evaluated *natively* — dicts, lists,
numbers, booleans, null keep their type; any mixed template renders to a
string. Bare `expr:` fields were always native. After evaluation the
config field's JSON-Schema type still applies (D23: the schema is the
type authority) — a string-typed field stringifies the native result, an
object-typed field rejects a scalar. Rejected: always-string (breaks
structured passing); a second syntax for native mode (`!expr` tags —
violates D10's one-language rule); auto-JSON-parsing of rendered strings
(heuristic, silently wrong on JSON-looking text).

## Known open risks (watch during implementation)
- Merge `all` clear-slots vs rule-2 latest-value under fast cycles —
  most test-worthy engine code.
- Ghost-wires for template references — elegant on paper, may be noisy
  on dense canvases; let real usage decide.
- niquests timing granularity (dns/connect/tls) — fields optional in
  events for a reason; verify what it actually exposes.
- niquests pulls urllib3-future — watch for dependency conflicts in
  users' shared venvs (`core` gets installed into pytest envs next to
  requests/botocore). Mitigations specced: internal HTTP adapter seam
  (NFR-09) + alongside-install compat CI job (NFR-10). Tech stack itself
  confirmed as-is by owner, 2026-07-02.
- PyPI name "napflow" availability — check before attachment.
- Default-required End ports (D18) may annoy flows with conditional
  outputs — watch whether `required: false` becomes boilerplate; if so,
  reconsider the default.
- W103 (unconnected error port) fires on nearly every minimal flow (the
  flagship example itself trips it via `check_job.error`) — unconnected
  error ports are the *safe* default, so the warning may be noise.
  Consider demoting it to a canvas-only hint if CI output gets chatty.
