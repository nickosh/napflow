# napflow — Decision Log (ADR-style)

Why things are the way they are. Date format: 2026-06. D01–D17 decided
during initial design (June 2026); D18–D22 in the 2026-06-14 edge-case
review, confirmed 2026-07-02; D23–D25 adopted 2026-07-02; D26–D32
during v0.1 implementation; D33–D37 from the 2026-07-11 v0.2 design
review; D38–D40 from 2026-07-13 API, scope, and distribution reviews. Reversing any
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
bodies always stored (valves: 10MB/body, 500MB/run per EC32; truncated
markers) — explicit user requirement; partial capture rejected. Secrets masked at emission so
history is shareable by construction (scope precisely defined in D22).
Logs persisted too.

**v0.2 amendment:** the v0.1 valve implementation does not satisfy the
word “complete” and EC32 was reopened. D34 supersedes the storage half
of this decision: large content is stored once as a full-fidelity blob,
events reference it, and limits are explicit policy rather than silent
information loss. D35 supersedes “shareable by construction”: the
canonical local record preserves the real data; redaction belongs at
presentation/export boundaries.

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

Amended 2026-07-05 (S2/M5, owner-confirmed): masking covers UI, logs,
events, stored runs, and reports — but **`napf run` stdout is NOT
masked**. stdout carries only the End-outputs JSON and is the
functional output; `napf run flows/login | jq .token` is the documented
contract, and masking it would break the pipe use case the CLI exists
for. Pinned in the WM CLI section with a test
(`test_stdout_and_local_jsonl_preserve_raw_local_truth` now covers the
superseding D35 boundary).

**Superseded for v0.2 by D35.** This remains the description of v0.1
behavior, not the target policy. The review found that recursively
masking the whole event (including keys and protocol fields) can corrupt
the replay schema, while irreversible masking of the only local record
conflicts with the owner-prioritized observability promise.

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

**Consequences.** Every write — canvas or CLI — must reach disk through
the shared serializer (a divergent emitter silently reintroduces noisy
diffs; since S4/M4 this is structural — the canvas PUTs model JSON and
the server emits, so the UI has no YAML emitter to diverge); a
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

## D26 — Linux is a first-class v1 platform

(2026-07-06, owner decision.) The original scope said "macOS + Windows
from day one, Linux implicitly via CI" — a hedge, not a technical
constraint. By S2 close the codebase has zero platform-conditional code
(pure Python, pathlib discipline, no shell-isms), and the full suite has
run green on ubuntu-latest in the 3-OS CI matrix since M0 — Linux support
already exists and is already verified on every commit. The primary
audience (QA/dev teams) runs CI and often dev boxes on Linux. Chosen:
promote Linux to the same support tier as macOS/Windows — PRODUCT success
criteria, NFR-02, and the S4 `napf ui` DoD now name all three. Windows
remains the platform needing special care (spawn semantics,
CREATE_NO_WINDOW, line-ending discipline); Linux rides the POSIX path
already exercised on macOS. Rejected: keeping the "Linux via CI" hedge
(understates what the CI matrix already proves and undersells to the
audience most likely to run napflow headless).

## D27 — CodeMirror 6 is the nodes.py editing surface (not Monaco)

(2026-07-08, owner-prompted investigation during the M4-leftovers
session.) The M4 plan said "Monaco, bundled, no CDN". Both were built
and measured behind the identical `/api/code` GET/PUT+etag contract.
Monaco 0.55: ~73MB in node_modules, a ~3.6MB minified core chunk +
python monarch + a worker chunk + a codicon webfont in the wheel, and
its new EditContext input needed a compat flag plus a
`window.__napfEditor` test seam because synthetic keyboard events don't
reach its hidden input — friction that matters when `napf ui` opens the
user's DEFAULT browser, not a pinned Chromium. CodeMirror 6
(`codemirror` + `@codemirror/lang-python`): ~2.8MB in node_modules, one
~458KB lazy chunk (~153KB gz), no worker, no webfont, and a
contenteditable input that plain keyboard events drive — the e2e needs
no seam. Feature needs for a whole-file single-language pane
(highlighting, gutter, bracket-match, undo) are fully covered by
`basicSetup`. Chosen: CodeMirror 6 via a hand-rolled ~60-line React
wrapper (`CodeMirrorPane.tsx`; no @uiw/react-codemirror — the wrapper
is trivial and third-party wrapper churn is real). Rejected: Monaco (an
IDE chassis for a one-file-one-language pane; ~8× the shipped bytes,
browser-input fragility); keeping the plain textarea (no highlighting
was the one real complaint). Revisit only if nodes.py editing grows
LSP/multi-file ambitions.

## D28 — Browser e2e: chromium-only, always against the built bundle

(Decided 2026-07-06 at S4/M2 as a journal note; promoted to a D-entry
2026-07-08, owner-confirmed.) Two pins for the Playwright suite:

1. **Chromium only in v1.** `napf ui` opens the user's DEFAULT browser
   (D26-adjacent owner call), so no single engine mirrors reality —
   pinning three engines × 3 OS would triple e2e cost for a canvas UI
   built on standard DOM/contenteditable APIs (D27 chose CodeMirror
   partly for exactly that cross-browser input robustness). Chosen:
   chromium on all three OS. Revisit after the v1 release — extend to
   firefox/webkit if real cross-browser reports appear.
2. **Always the wheel-user's path.** e2e serves the BUILT bundle
   through the real server (`e2e/serve.mjs` scaffolds a fresh
   `napf init` workspace); never `vite dev`. What CI proves is what a
   `uv tool install napflow && napf ui` user gets — dev-server-only
   breakage (missing static route, stale bundle, base-path issues)
   cannot slip through.

Rejected: full browser matrix (cost without a matching promise — v1
never claims per-engine support); e2e against vite dev (faster
iteration but tests a code path no user runs).

## D29 — Run on canvas is a locked, live-animated RUN MODE

(2026-07-08, owner fork at S4/M5 kickoff.) Starting a run (or opening
a history replay) switches the canvas into a distinct run mode rather
than overlaying status on an editable canvas:

- **Editing locks** (drag/connect/delete/palette/inspector give way)
  until the view is exited; node clicks filter the event stream
  instead. Rationale: the owner wants the canvas to "become alive" —
  a surface that animates AND edits at once shows stale overlays the
  moment a wire moves, and the frictionless-autosave model would
  persist mid-run edits that the running engine never sees.
- **The animation is driven by real events, never simulated**: nodes
  pulse from `node_fired` until their `message_emitted`, wires replay
  a travelling dot per message (edge key = the `from→to` refs carried
  verbatim by `message_emitted`), log nodes render their latest `log`
  value live, outcomes settle from `assert_result`/`request_*`/
  `python_error`, and `nodes_never_fired` dims as skipped.
- **Root-frame scope (v1)**: the overlay animates the canvas being
  viewed from its own frame (`f-0`); container flow/loop nodes pulse
  for the duration of their subtree, child-frame events appear in the
  event stream labeled with their frame path but paint no inner nodes.
  Frame ids don't encode their container node, so per-container
  attribution needs an engine event change — deferred to M6 drill-in
  if it proves wanted.
- **Inputs via hybrid popover**: Run fires immediately when the flow
  declares no Start ports; otherwise a popover opens prefilled with
  the declared defaults (same typed parsing as the Start-port editor —
  `napf run -i` parity). Env profile is a persistent header dropdown.
- **Run reports stay a `napf run`/CI concern** (the "revisit at S4/M5"
  from the WM server-surface pin resolves to: still deferred — the
  canvas has full wire detail live plus the JSONL history browser;
  junit/json reports serve CI pipelines, not canvas sessions).

Rejected: editable canvas with a status overlay (stale-overlay
problem above); a run "dialog" every time (friction on input-less
flows); simulating animation timing client-side (the WebSocket already
delivers real timing — anything else would lie).

## D30 — Run debugging: pause is a dispatch gate; breakpoints are runtime wire-holds, never flow-file content

(2026-07-08, owner-confirmed at the M5 planning session. Design
pinned now so nothing lands that conflicts; implementation is
explicitly after v0.2 — PLAN "Explicitly after v0.2" items 1–2.)

**Pause = pause request, not freeze.** The engine cannot safely
freeze a mid-flight HTTP request, a running python function, or a
ticking delay — so pause gates the pump's DISPATCH: in-flight
firings complete, queued deliveries hold, nothing new fires; run
state `paused`; `run_paused`/`run_resumed` events; abort works while
paused. Consequence that must be pinned with it: **paused time is
excluded from timeout guards, `max_seconds`, and the run deadline**
(the engine offsets its monotonic clocks by accumulated pause
epochs) — otherwise pausing at a breakpoint trips every timeout in
the flow.

**Breakpoints anchor to message deliveries, i.e. wires.** The
engine's atomic unit is a delivery along an edge, and E004 (single
edge per input) makes a wire breakpoint and an input-port breakpoint
THE SAME THING — one primitive, two UI affordances (click the wire
or the input handle). The classic "break before or after the node?"
question dissolves into edge-set sugar: before-node = all inbound
wires, after-node = all outbound wires; v1 of the feature ships
plain wire-holds (node-level sugar can come later). Semantics: a
matching message is HELD before firing its target — the travelling
dot stops mid-wire with the held value inspectable; **continue**
releases all holds, **step** releases exactly one delivery. Pause is
the same mechanism with "hold everything" instead of an edge set —
which is why R3 rides R2's gate almost for free.

**Runtime state, not file content.** Breakpoints are set in the UI
and sent with the run request (plus pause/resume/step endpoints) —
they NEVER live in flow.yaml. A committed breakpoint node would
pollute the git-friendly diffs the format exists for, and headless
`napf run`/CI would have to either hang on it or ignore it — both
wrong. Scope at first landing: edges of the flow being run
(root-frame; child-frame holds join the M6+ drill-in work).

Rejected: a `breakpoint` node type (file pollution + CI ambiguity
above); freeze-style pause (unimplementable without lying about
in-flight work); breakpoints on nodes as the primitive (ambiguous
before/after semantics that edges don't have).

## D31 — Clone repoints the invoking node; "pack selection to new flow" is deferred (R5)

(2026-07-09, owner-confirmed at the M6 planning session.)

"Clone to new flow…" (D09) landed at S4/M6 with one semantic pin: the
action lives on a flow/loop node, forks the TARGET's folder, and
**repoints that one node at the clone** — the escape hatch from
shared-reference semantics ("stop sharing, give me my own fork").
Nothing else changes: the canvas keeps the same node and wires, other
users of the original are untouched, and the original folder is never
modified.

The owner's first reading of the name — select nodes + wires on the
canvas and PACK them into a new subflow (replaced by a single flow
node, boundary wires becoming Start/End ports) — is a **different
feature**: extract-to-subflow refactoring. It is deliberately deferred
to the post-v0.1.0 backlog as **R5**, not squeezed into M6, because it
needs its own design pass: boundary-port inference, edge rewriting,
moving python functions between nodes.py files, and fixing up
`{{ nodes.* }}` refs and set/get variables that cross the cut.

Rejected for v1: clone-without-repoint (leaves the user doing the
config edit the action exists to automate); a sidebar "duplicate flow"
button (same endpoint, but no owner pull yet — trivial to add when
asked).

## D32 — The python-worker child is stdlib-only, forever: python nodes run in the USER's interpreter

(2026-07-10, owner-confirmed after the FR-108 / msgspec discussion —
"let's keep this promise for sure".)

The promise: `python.interpreter` (napflow.yaml, FR-108) points python
nodes at the USER's environment — their project venv, their packages,
their models, their Python version — and the worker child
(`core/worker_main.py`) is invoked by absolute path and imports
NOTHING beyond the stdlib. The target interpreter does not need
napflow installed, or anything else at all. This is what lets flow
logic live in the same dependency world as the code under test (core
promise #3, pytest-able there too) while napflow itself stays
`uv tool install`-ed in its own isolated venv.

Consequences pinned:
- **The wire protocol stays newline-delimited JSON readable by bare
  stdlib.** Any serialization speedup — msgspec was evaluated
  2026-07-10 — may only ever land on the PARENT side
  (`core/worker.py` / the event `encode_record` path, napflow's own
  process) without changing the wire format; the child side is
  untouchable. Rejected: msgspec/msgpack on both ends (forces a
  compiled dep into every user interpreter — breaks this promise);
  adopting msgspec parent-side NOW (unmeasured bottleneck — NFR-08/R6
  measure first; JSON encode is microseconds against an expected
  multi-millisecond pipe+scheduling round trip, and `core`'s
  dependency posture is deliberately lean, NFR-10).
- The child script's own syntax/stdlib usage sets the de facto Python
  floor for user interpreters — keep it conservative; never assume
  napflow's 3.12+ floor inside `worker_main.py`.
- Known papercut, not a promise change: venv layouts differ per OS
  (`.venv/bin/python` vs `.venv/Scripts/python.exe`) and napflow.yaml
  is committed — a bin/Scripts fallback in `_resolve_interpreter` is a
  fine future issue if mixed-OS teams hit it.

## D33 — The entire v0.x series is experimental; stability begins at v1.0

(2026-07-11, owner decision after the first working-version review.)

`v0.1.0` is intentionally a historical milestone: the first version
that works end to end, not a declaration that the architecture, flow
schema, event schema, or public API is stable. Breaking and experimental
changes are allowed throughout `v0.x.x` when they materially improve
correctness, observability, or the product model. Release notes must say
so plainly.

The existing `schema: napflow/v1` marker is also experimental during the
0.x package series. It identifies the current shape; it does not create
a compatibility promise before package v1.0. Best-effort migrations are
welcome, but forward compatibility for v0.1 flow files and histories is
not guaranteed until formats carry explicit versions and migration
policy. At v1.0, changing a stable flow schema requires a new
`napflow/vN` marker or a migration; event-log compatibility receives the
same treatment.

Rejected: delaying the honest first release until every hardening item
lands (the owner wants the working milestone recorded); pretending the
`v1` text already means stable (would constrain the necessary v0.2
storage and replay redesign).

## D34 — Full-fidelity history stores large content once; replay stays canonical and lazy

(2026-07-11, owner approved after weighing clarity against local
resource use.)

Data clarity and reliable content are more important than minimizing
every byte. v0.2 therefore replaces irreversible body truncation with a
full-fidelity history model:

- JSONL remains append-only and canonical for event order (`seq`),
  frame/node identity, timing, state, and references. Live WebSocket and
  replay continue to use the same event vocabulary.
- Small values stay inline for immediate inspection. Large bodies and
  payloads are written once to immutable content-addressed blobs;
  events carry a typed reference with hash, byte size, media type, and
  encoding. Repeated appearances never duplicate the bytes.
- Runtime execution values and persisted observation are separate.
  Moving persisted content into a blob must never silently truncate or
  change the value delivered through a flow.
- Local defaults have no destructive content limit: expose size and preserve
  the value rather than silently destroying information. The codec reserves an
  explicit omission record with reason/size/hash, but D39 defers any
  user-facing hard-limit policy until after v0.2.
- Replay is streamed/paged, blobs load on demand, and disposable offset
  indexes/summaries may accelerate seeking. JSONL plus blobs remain the
  source of truth; derived indexes are rebuildable.
- Retention treats a run atomically: JSONL, blobs, indexes, and reports
  are one retention unit. A self-contained export bundle (JSONL + blobs
  + manifest, likely `.napflow-run.zip`) is the sharing surface.

This preserves D13's important invariant—replay is a recording, never
re-execution—without multiplying one large response across engine,
server, CLI, browser, and disk memory. Rejected: keeping every large
value inline; deleting old events from a retained run; UI-only row caps
that leave server/browser memory unbounded; silent truncation as the
normal local policy.

**Scope amendment (D39, 2026-07-13):** the full-fidelity store, typed
references, deduplication, and lazy loading remain v0.2 commitments.
Self-contained export/import, explicit hard-limit omission policy, advanced
seek indexes, and the 100k-event replay gate move to the future ledger.

**Implemented 2026-07-13 (M4+M5):** production streams advertise
`content-blobs/1` and apply the exhaustive field policy before the shared
JSONL/WebSocket fan-out. The same structured HTTP response is hashed once
across request, message, Log, End, and blob-aware JSON report records; public
feature-gated resolution verifies size/hash and preserves marker-shaped user
data. Destructive capture settings and previews are removed. Versioned frozen
REST pages return bounded events plus graph-sized scalar projections; browser
detail resolves one canonical record on demand and completed frame drilldown
never re-executes.

## D35 — Preserve raw local truth; redact presentation and exports, never protocol structure

(2026-07-11, owner decision: observability first, masking only where it
is concretely useful for CI/CD.)

The canonical local history preserves real values using ordinary inherited
OS/workspace permissions. Redaction is a presentation/future-export concern:

- Event names, schema keys, enums, identifiers, frame/node metadata,
  and control fields are never rewritten.
- The local UI may display complete values, with hide/reveal affordances
  as convenience rather than destructive storage behavior.
- Terminal presentation and JSON/JUnit reports apply the configured
  declared-secret policy; an empty pattern list is a raw no-op. Any future
  export surface must make its raw/redacted choice explicit.
- Runtime-acquired secrets may later be registered or selected by field
  path. Until then, documentation must not claim absolute shareability.
- Policy is explicit in configuration/CLI, not inferred only from a
  possibly-surprising `CI` environment variable.

The implementation may temporarily retain the v0.1 behavior while the
v0.2 format lands, but it must first stop masking dictionary keys and
protocol fields. Rejected: no masking anywhere (CI logs and artifacts
often leave the developer's machine); irreversible masking at event
creation (destroys the only ground truth); encryption/key management in
v0.2 (overengineering for a local-first developer tool).

Historical implementation status (2026-07-13): the first raw canonical JSONL
implementation forced POSIX private modes and a protected owner/SYSTEM/admin
DACL on Windows. The raw local WebSocket path and schema-aware terminal/JSON/
JUnit redaction also landed, with one exhaustive field-policy registry that
preserves dictionary keys and structural values.

**Target amendment (D39, 2026-07-13):** the field-policy registry, raw local
truth, and optional declared-secret terminal/report views remain. Custom
ACL/DACL, ownership migration, forced modes, and export policy are no longer
v0.2 requirements. Implemented later on 2026-07-13: the custom permission and
owner layer was removed; JSONL and blobs now use ordinary OS/workspace
permissions while exclusive creation, containment, and content verification
remain. Export policy and secure-history guarantees remain future work.

## D36 — One run lifecycle owns fairness, cancellation, resources, and frame release

(2026-07-11, accepted v0.2 architecture after adversarial engine
review.)

Timeouts, aborts, capture bytes, loop concurrency, worker teardown, and
frame retention are one lifecycle concern, not independent local
patches. v0.2 introduces an explicit run-lifecycle/resource owner with
these invariants:

- The pump processes bounded batches and yields cooperatively; every
  batch checks abort and a monotonic deadline. Tight inline guard/merge
  cycles cannot starve the event loop or ignore the run deadline.
- All engine-owned tasks, HTTP sessions, streams, and workers are driven
  through `finally` cleanup, including external coroutine cancellation. Every
  event sink is attempted; a remembered ordinary close error makes history
  incomplete without replacing the execution outcome. Control-flow
  exceptions still propagate after cleanup.
- Worker timeout means immediate terminate, then grace, then hard kill;
  graceful EOF is normal-finalization behavior only. A timed-out worker
  must not overlap a replacement and commit late side effects.
- D32 remains intact: the child stays stdlib-only and the wire remains
  newline-delimited JSON. v0.2 defines and tests an adequate protocol
  line ceiling/reader limit and routes oversize/malformed messages as
  worker errors instead of leaking reader exceptions.
- Parallel loops use a bounded producer/fixed task set, not one task per
  input. Finished frames emit reconstructable summaries, then release
  runtime-only state; the durable event tree—not live Python objects—
  supports future subflow drilldown.
- Slow subscribers and presentation layers are bounded; durable data is
  replayed from disk rather than duplicated indefinitely in RAM.

Cooperative yields may slightly reduce pathological in-memory message
throughput, but batching keeps ordinary overhead negligible and makes
the server responsive. Measurement follows correctness (v0.2 perf
suite); it does not justify retaining starvation bugs.

## D37 — Local-only stays simple, but workspace and browser boundaries are explicit

(2026-07-11, owner decision: remote use is possible but far away; do not
build user/session authentication now.)

v0.2 does not add accounts, OAuth, sessions, a remote deployment mode,
or a public bind option. It does establish two inexpensive boundaries:

1. A single workspace resolver is the only route from an identity to a
   flow, subflow, fixture, run log, source file, or clone destination. It
   validates lexical form, resolves symlinks, enforces containment, and
   validates run IDs. Entry runs and reads are held to the same boundary
   as writes.
2. The server remains loopback-only and validates loopback Host plus
   same-origin mutation/WebSocket requests. This is request-origin
   hardening, not a general authentication system. A pluggable auth seam
   may be added only when remote use becomes real.

Workspaces remain trusted code: running one executes its `nodes.py`.
Path containment is still required because “trusted workspace” does not
mean “silently access outside the selected workspace.” Rejected: relying
on scattered `_safe_identity` calls; capability/user-token machinery in
v0.2; exposing `0.0.0.0` before a real remote security design exists.

Implemented at v0.2/M1 (2026-07-12): `WorkspaceResolver` is threaded
through checker/engine/CLI/server path consumers; lexical + resolved
containment failures use `workspace_boundary`. The loopback server now
rejects non-loopback/malformed Host and foreign browser Origin before
mutation or WebSocket accept. This boundary assumes the selected local
workspace/process trust domain is not concurrently mutating path entries
maliciously; it is not an OS filesystem sandbox against another local
process, consistent with the trusted-code decision above.

## D38 — Python embedding uses Workspace → Flow → isolated Run; typed catalogs are generated later

(2026-07-13, owner-confirmed during public-integration design.)

The public Python surface will mirror napflow's domain boundary. A loaded
Workspace is reusable source/configuration, not a shared runtime session. It
provides exact flow lookup and fresh discovery; each discovered/bound Flow holds
only the workspace plus canonical identity and creates a fully isolated run on
every sync or async invocation. `run_flow(workspace, identity, ...)` remains the
equivalent functional form, and both paths share preparation, execution,
history, and cleanup semantics.

For convenient test-framework use, M6 also adds a runtime flow catalog. It maps
each flow identity relative to the configured flows root onto nested attribute
segments—conceptually `workspace.flows.<identity segments>`—when every segment
is an exact Python identifier. Exact string/bracket lookup is the permanent
escape hatch for punctuation, spaces, reserved-member collisions, and arbitrary
legal identities; names are never lossy-normalized. A catalog entry may be both
runnable and a namespace when a flow directory contains child flows. Dynamic
catalog lookup may improve interactive completion but must not be advertised as
static typing derived from the filesystem. Catalog bracket keys are always
relative to the configured flows root; `workspace.flow(...)` is the distinct
full workspace-relative form, so a legal first segment equal to the root name
cannot alias a shallower flow.

After v0.2, deterministic generated Python bindings/stubs may expose discovered
flow names plus typed Start inputs and End outputs to IDEs/type checkers, with a
stale-binding CI check. This is one-directional flows → code, consistent with
D02. Rejected: mutable workspace-level cookies/variables/workers shared across
tests; magic attribute access without exact lookup; silent identifier
normalization; an editor-specific type-checker plugin in v0.2; claiming generic
`__getattr__` can statically validate runtime filesystem contents.

## D39 — v0.2 prioritizes a usable full-fidelity prototype over security-grade storage and advanced replay

(2026-07-13, owner decision after reviewing M4 complexity and the project's
pre-adoption stage.)

v0.2 keeps the engineering that directly makes napflow work reliably, but
stops treating speculative high-security or large-scale use as a release
prerequisite:

- M4 still activates store-once content-addressed blobs across every
  persisted payload and captures the effective prepared request. Hash/size
  verification, deduplication, collision-safe descriptors, exclusive
  creation, and clear missing/corrupt errors are content-integrity behavior.
- Canonical local JSONL and blobs remain raw and use the ordinary permissions
  inherited from the user's OS/workspace. M4 removed the custom Windows
  DACL/SID owner path, forced POSIX private modes, and permission-based content
  rejection while retaining integrity and containment. A secure-history mode,
  authentication/authorization, or encryption is a separate future design if
  real users require it.
- The local UI remains a raw inspection surface. Terminal and JSON/JUnit
  reports apply declared-secret masking only when `environments.secrets` is
  non-empty; an empty list is the explicit no-redaction path. New workspaces
  present secret patterns as opt-in examples rather than implying a complete
  security boundary.
- Self-contained run export/import, raw/redacted bundle rewriting, runtime
  token registration, and hard-limit omission metadata are deferred. v0.2
  documents that raw run artifacts may contain secrets and makes no
  safe-export claim.
- M5 supplies basic versioned paging, lazy blob reads, bounded active UI
  windows, and reconstructable frame drilldown. Timeline scrubbing, playback
  speeds, checkpoints, advanced indexes/filters, and the 100k-event replay
  performance target stay explicitly in the future ledger for later stages.
- M6 retains both the functional `run_flow` entry point and the reusable
  Workspace/Flow object surface, including the runtime nested
  `workspace.flows` catalog. Only generated static bindings remain deferred.
- M7 reuses the existing CI/release coverage and adds missing installed-product
  checks; it does not expand dependency, browser, adversarial, or performance
  matrices before user demand.

Rejected: deleting the full-fidelity blob design (it fixes real silent data
loss); deleting the runtime flow catalog (small, useful Python ergonomics);
continuing a partial filesystem security boundary that adds platform risk
without securing every raw-data path; silently dropping the timeline or scale
ideas instead of preserving them as future candidates.

Implementation status: M4 and M5 completed on 2026-07-13. New scaffolds
default to an empty secret-pattern list; production history activates the full-value blob
schema; reports resolve only consumed records and retain large JSON values by
reference; and request events carry initial/final prepared-wire snapshots.
Inherited permissions, no-overwrite creation, containment, and blob
verification remain as decided. `napflow-replay/1` now supplies bounded
sequence pages, scalar frame/final and graph-sized view projections, lazy
verified event detail, and completed child-canvas drilldown. M6 now also
supplies the public Workspace/Flow API, artifact-only distribution contract,
schema/UI coverage, and audited frontend notices. M7's focused release gate is
implemented: one reusable workflow now gates PRs and tags with the existing
three-OS Python/UI paths, Vitest, notices, the installed-artifact smoke, and
tested exact tag/development-version refusal. v0.2's concrete compatibility notes are
inserted into the generated release notes. PR #2 and non-publishing release
dispatch #29352493848 then passed the full three-OS gate on the prepared
`0.2.0` tree; its uploaded wheel and sdist both carry exact `0.2.0` metadata.
The remaining promotion action is the exact `v0.2.0` tag after release-memory
closeout—not another product or hardening matrix.

## D40 — Distribution supports release-built artifacts, not arbitrary Git/source installs

(2026-07-13, owner-confirmed at M6 implementation start.)

The Git-friendly product promise applies to user flow workspaces, not to
installing napflow itself from an arbitrary repository checkout. Supported
installation paths are PyPI and GitHub Release artifacts: a wheel containing
the compiled UI, or a release sdist that already contains that same bundle and
can produce the wheel without Node.

The generated frontend remains build output and is not committed. The release
pipeline builds it once before producing artifacts; PEP 517 does not invoke a
frontend toolchain, and direct VCS (`git+https`) or raw-checkout builds are
explicitly unsupported. The UI placeholder must report this boundary honestly.
A deterministic smoke starts from the release-built sdist, blocks Node/npm/npx,
builds and installs its wheel in isolation, exercises both public Python API
forms, requires identical sdist/wheel static trees, and probes the real
`napf ui` HTML, every packaged/referenced lazy asset, and workspace API.

Rejected: committing the generated bundle; adding Node to the PEP 517 build;
continuing to imply that an arbitrary Git checkout is an install artifact. M7
wired the reusable smoke, notice check, frontend suites, and exact tag/version
refusal into the authoritative PR/tag gate.

## Known open risks (watch during implementation)
- EC10/EC22/EC27/EC35 remain open post-v0.2 limitations. EC44's distribution,
  exact-version, and authoritative-gate defect is fixed; its ledger row names
  the artifact/workflow regressions. Close the four remaining cases only with
  their stated tests.
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
- PyPI project ownership/trusted publishing are active since v0.1.0; keep the
  exact workflow/environment identity intact for later releases.
- Default-required End ports (D18) may annoy flows with conditional
  outputs — watch whether `required: false` becomes boilerplate; if so,
  reconsider the default.
- W103 (unconnected error port) fires on nearly every minimal flow (the
  flagship example itself trips it via `check_job.error`) — unconnected
  error ports are the *safe* default, so the warning may be noise.
  Consider demoting it to a canvas-only hint if CI output gets chatty.
