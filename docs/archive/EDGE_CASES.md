# napflow — Edge Cases & Undefined Behavior (Review 2026-06-14)

Intended location: `docs/EDGE_CASES.md`

Catalog of edge cases and undefined/contradictory behavior found reviewing
flow-schema v0.3, manifest v0.2, engine-spec v0.1, and DECISIONS. Each entry
cites the spec it touches and proposes a resolution. Concrete paste-ready
spec edits and new ADR entries are in `docs/spec-deltas-2026-06-14.md`.

Status legend:
- **DECISION** — semantics are genuinely undefined or self-contradictory; a
  fork must be chosen before implementation.
- **FIX** — behavior is defined but wrong / will hang / will mislead.
- **DOCUMENT** — behavior is acceptable but users (or `napf check`) will be
  surprised; needs an explicit sentence in the spec, not a code change.

| ID   | Title                                                        | Status   | Pri | Touches |
|------|--------------------------------------------------------------|----------|-----|---------|
| EC01 | Unreached declared End port → run reports `passed`           | DECISION | P1  | engine §2; flow End rules |
| EC02 | Unconnected `exhausted`/`expired` semantics contradictory    | DECISION | P1  | flow wire-format; engine §2,§8 |
| EC03 | `merge: all` in a cycle with one re-firing input → stall     | DOCUMENT | P2  | flow Merge; engine §4 |
| EC04 | Multi-input node with a never-arriving input → silent skip   | DOCUMENT | P2  | engine §4 |
| EC05 | Cross-frame aggregation of asserts/errors is unstated        | DECISION | P1  | engine §2,§5 |
| EC06 | Loop `on_error`/`errors`: "iteration error" undefined        | DECISION | P1  | flow Loop; engine §5 |
| EC07 | `flow` node has no error port                                | DECISION | P2  | flow Flow node; engine §5 |
| EC08 | Empty/degenerate seed → pump deadlocks (no QUIESCENT)        | FIX      | P1  | engine §3 |
| EC09 | Python worker concurrency unspecified; stuck firing blocks   | DOCUMENT | P2  | engine §5a |
| EC10 | Runtime-acquired secrets unmasked; masking algo unspecified  | DECISION | P1  | engine §7; manifest secrets |
| EC11 | `napf check` code numbering: E010 missing, W105 misplaced    | FIX      | P3  | engine §8; CLAUDE.md |
| EC12 | Templating asymmetry: `trigger` envelope vs `nodes.*` value  | DOCUMENT | P2  | flow Templating; engine §6 |
| EC13 | request `error` vs `response` for non-2xx unstated           | DOCUMENT | P2  | flow Request; engine §5 |
| EC14 | `napf check` import-vs-AST of `nodes.py` undefined           | DECISION | P2  | engine §8 |
| EC15 | `loop` input port + `over` evaluation, `start.out` payload   | DOCUMENT | P2  | flow catalog |
| EC16 | counter off-by-one + guard-cycle check scope (cycle vs SCC)  | DOCUMENT | P3  | flow Guards; engine §8 |
| EC17 | env validation split: W105 (check) vs ENV (run)              | DOCUMENT | P3  | engine §2,§8; manifest |
| EC18 | `nodes.*` is last-writer-wins under cycles/concurrency       | DOCUMENT | P3  | engine §6 |
| EC19 | Set/Get reintroduce ordering hazards wires avoid             | DOCUMENT | P3  | flow Scoping |
| EC20 | Abort mid-request leaves dangling `request_started` in JSONL | DOCUMENT | P3  | engine §3,§7 |
| EC21 | Node-id charset constraints unspecified                      | DOCUMENT | P3  | flow file-format |
| EC22 | Grandchild processes from python nodes not killed            | DOCUMENT | P3  | engine §5a |
| EC23 | `defaults.request` may only reference `env`/`run`            | DOCUMENT | P3  | manifest defaults |

---

## P1 — these affect "works as expected", especially the CI-first promise

### EC01 — Unreached declared End port reports `passed` (false green)
**Touches:** engine §2 (run states), flow-schema *Start / End node rules*.

**Current:** an End port that is never written yields `null` plus *a warning in
the report*. Run state `passed` is defined as "quiescent, no failed asserts, no
unhandled errors." A warning is none of those.

**Problem:** any flow where a required output is silently dropped — an upstream
branch never fired, a guard exhausted, a `merge: all` stalled — completes as
`passed`, exit 0. For a tool whose headline is assert-driven CI exit codes,
this is a false green. There is currently **no run-state for "I declared this
output and never produced it."**

**Resolution (DECISION):** a declared End port that holds no value at quiescence
makes the run `failed`, unless the port is explicitly `required: false`. Add a
`required` field to End ports, default `true`. (Alternative: a global
`defaults.run.require_end_ports` / `--strict` toggle — weaker, because the safe
behavior should be the default.) This is the load-bearing fix: EC02 depends on
it. See proposed **D18**.

### EC02 — Unconnected `exhausted`/`expired` is self-contradictory
**Touches:** flow-schema *Wire format* vs engine §2/§8 (W103, run-failed rule).
Demonstrated by the `create_until_ready` example, whose `attempts.exhausted`
port is unwired.

**Current:** *Wire format* says "errors travel as data via
`error`/`failed`/`exhausted`/`expired` ports" — i.e. exhaustion is error-like.
But W103 and the `failed` definition only mention `error`/`failed`. So when the
retry counter runs out in the flagship example, it is undefined whether the run
fails or silently drops the message and ends. As written, "retried 10×, never
ready" most likely terminates as **`passed`** (via EC01) — clearly not intended.

**Problem:** the two specs disagree, and the canonical example sits exactly on
the disagreement.

**Resolution (DECISION):** make `exhausted`/`expired` **ordinary pass-through
outputs**, not error ports. Unconnected = dropped, like any non-error output.
Remove them from the *Wire format* "errors travel as data" sentence. "Gave up"
becomes a failure through EC01 (the unreached `end.job` fails the run) or
through an assert the user wires on the exhausted path — not through magic guard
semantics. Add a non-failing lint **W106** ("guard exhaustion/timeout port
unconnected — this loop exit produces no output") for canvas feedback. Update
the example to wire `attempts.exhausted` to an `end` port or an `assert` so
"gave up" is explicit in the report. See proposed **D19**.

### EC05 — Assert/error aggregation across frames is unstated
**Touches:** engine §2 (run states), §5 (loop/flow runners).

**Current:** frames are the isolation unit and "nothing leaks across except
Start/End port values." Run owns the report accumulator. The spec never says
that assert results, python-asserts, and unhandled error-port messages from
**subflow and loop-body frames** roll up into that run-level report.

**Problem:** if they don't roll up, a failed assert three frames deep produces
exit 0 — the CI promise is hollow. If they do (almost certainly the intent), it
must be written, because "data is frame-isolated" reads as if *everything* is.

**Resolution (DECISION):** state explicitly that **results aggregate run-wide
across the entire frame tree; only data is frame-isolated.** Run state = the
worst outcome anywhere in the tree (`error` > `failed` > `passed`). See proposed
**D20**.

### EC06 — Loop `on_error` / `errors` port: "iteration error" is undefined
**Touches:** flow-schema *Loop node semantics*, engine §5 (loop runner).

**Current:** "`on_error: stop | continue`; body End outputs collected on
`results`, failures on `errors`." What counts as a failure is never defined.

**Problem:** does a failed *assert* in a body iteration route to `errors` and
stop the loop? Or only a raised exception? Or only an unhandled error-port
message? Each gives a different loop behavior and a different run outcome.

**Resolution (DECISION):** define an iteration "error" as **the body frame
ending `failed` or `error`** (per EC01/EC02/EC05 — any unhandled error-port
message, a worker crash/timeout, or an unreached required End port). `on_error`
governs only whether further iterations are *scheduled*; failed iterations land
on `errors` and count toward the run state regardless. See proposed **D20**.

### EC10 — Runtime-acquired secrets are unmasked; masking algorithm unspecified
**Touches:** engine §7 (masking), manifest *secrets*, contradicts D13's
"shareable by construction."

**Current:** masking matches "values" of env vars whose names match the secret
patterns. Two things are undefined: (a) match semantics — exact token vs
substring scan — and how a token embedded inside a URL or body is handled;
(b) the scope: a token *acquired at runtime* (e.g. a bearer token returned in a
login response body, then reused) is not an env var, so it is **stored in full**
in JSONL.

**Problem:** D13 promises run history is shareable by construction. That is true
only for env-declared secrets. A login flow's response body containing a bearer
token leaks into committed-adjacent run history. For an API-testing tool this is
the common case, not a corner.

**Resolution (DECISION):** (1) specify the algorithm — mask the *values* of
secret-pattern env vars in the active profile + process env, using a substring
scan with a minimum length (e.g. ignore values shorter than 5 chars) so
embedded-in-URL/body tokens are caught without over-masking; (2) document the
scope honestly: only declared secrets are masked, runtime tokens are not; (3)
roadmap a runtime-redaction mechanism (`set ... secret: true`, or a response
field-path redaction directive) so login tokens can opt into masking. See
proposed **D22**.

### EC08 — Empty/degenerate seed deadlocks the pump
**Touches:** engine §3 (scheduler).

**Current:** `QUIESCENT` is enqueued only by `dec_in_flight()` when `in_flight`
transitions to 0. A flow that seeds nothing — Start's `out` unwired and no
auto-firing fixture, or a flow that is only a `note` — never increments
`in_flight`, so no decrement ever fires, and `pump` blocks forever on the first
`await queue.get()`.

**Problem:** a real hang on degenerate but legal flows (the schema allows a
zero-port Start, and `out` may be unwired).

**Resolution (FIX):** after `seed_sources()`, if `in_flight == 0`, enqueue
`QUIESCENT` (or call `finalize()` directly). One line. Optionally add a
non-failing lint: Start has no outgoing edges and no auto-firing source ⇒ "this
flow does nothing." Fold the rationale into D14.

---

## P2 — defined-ish behavior users will hit and be surprised by

### EC03 — `merge: all` in a cycle with a single re-firing input stalls
**Touches:** flow-schema *Merge node*, engine §4 rule 3.

`all` waits for every input then clears slots. In a loop where only `in1`
re-fires each pass (the other input came from a one-time upstream), the second
pass waits forever for `in2`. The node is then marked never-fired at quiescence
and everything downstream is skipped (→ EC01 territory). CLAUDE.md already names
this the most test-worthy engine code; it also needs to be **documented as
user-facing behavior**: "use `any` in front of a `trigger` to rejoin a retry
path; `all` is a one-shot rendezvous and will stall if re-entered with a partial
input set."

### EC04 — Multi-input node with a never-arriving input → silent skip
**Touches:** engine §4 rules 2 & 5.

The general case behind EC01/EC03: rule 2 fires only when *all* connected input
slots are filled. If one upstream branch is never triggered, the node never
fires and is reported under `nodes_never_fired` ("skipped"). This is correct for
intentional joins but is the mechanism by which required work silently
disappears. Document it next to the firing rules so "skipped" is understood as a
first-class outcome, not an error — and cross-reference EC01 for when skipped
output must escalate to failure.

### EC07 — `flow` node has no error port
**Touches:** flow-schema *Flow node semantics*, engine §5 (flow runner).

A flow node's ports are "derived from target Start/End," so a subflow that ends
`failed`/`error` has no dedicated outlet — unlike `request`/`python` (`error`)
or `loop` (`errors`). Two options: (a) rely solely on run-wide aggregation
(EC05) — failures contribute to run state but cannot be branched on; or
(b) give the flow node an implicit `error` port (consistent with "errors are
data") that fires when the child frame ends `failed`/`error`, carrying a
summary; unconnected → contributes to run failure per EC05. Recommend (b);
flagged as a decision because it changes port derivation.

### EC09 — Python worker concurrency is unspecified
**Touches:** engine §5a.

One worker per flow module, JSON-lines over pipes. Unstated: whether the worker
processes `task_id`s concurrently or serially. A simple stdin-line loop
serializes them, which means: parallel-loop iterations that hit python nodes are
**secretly serialized at the worker**, and **one stuck firing blocks all python
in that module** until its `max_seconds` kill (then respawn). Acceptable for the
"pipe round-trip is negligible vs HTTP" model, but it must be documented so
users don't expect CPU parallelism from `mode: parallel` + python. (A
multi-worker pool is the alternative but breaks shared module state — defer.)

### EC12 — Templating asymmetry: `trigger` is the envelope, `nodes.*` is the value
**Touches:** flow-schema *Templating*, engine §6.

`trigger` is the full `{value, meta}` envelope (`{{ trigger.value.body.id }}`,
`{{ trigger.value.url }}`), but `nodes.<id>.<port>` is the **unwrapped** value
(`{{ nodes.check_job.response.body.state }}` — no `.value`). Both are internally
consistent but asymmetric, and the difference is invisible until an expression
silently misbehaves. Document the rule plainly. Note that the flagship example's
`is_ready` condition reads the racier `nodes.*` form where `trigger.value` would
be deterministic (see EC18) — worth switching in the example for teaching value.

### EC13 — request `error` vs `response` for non-2xx is unstated
**Touches:** flow-schema *Request node*, engine §5 (request runner).

Implied by `assert kind: status` but never said: a non-2xx HTTP response is a
valid response and emits on **`response`**; the `error` port is for
**transport-level** failures only (connection refused, DNS, TLS, timeout after
retries). One sentence prevents a class of "why didn't my 500 hit the error
port" confusion.

### EC14 — Does `napf check` import `nodes.py` or AST-parse it?
**Touches:** engine §8, build order (check is the CI pre-gate).

Python node input ports are derived from the function signature, and edges into
them are validated by E003/E005. To validate statically, `napf check` must know
the signature — by **importing** `nodes.py` (runs import-time side effects;
malformed module breaks check) or by **AST-parsing** it (safe, but can't resolve
dynamically-built signatures). Pick one and state it: a CI pre-gate that imports
arbitrary user code has different failure modes than one that parses it.
Recommend AST-parse for `check`; the worker imports for real at run time.

### EC15 — `loop` input port, `over` evaluation, and `start.out` payload
**Touches:** flow-schema *Node type catalog* (loop, start rows), engine §5.

Two undocumented bits of the port surface:
- The `loop` catalog row lists only outputs (`results`, `errors`). A
  message-driven node must fire on *some* input. Specify the loop's input
  port(s) and **when/where `over` is evaluated** (presumably an `in`/`trigger`
  input, with `over` a Jinja2 expression over that delivery).
- `start.out` must carry *something* ("nothing emits an empty pulse"). Specify
  its payload — the full `inputs` dict is the natural choice — so downstream
  templates referencing it are defined.

---

## P3 — hygiene and clarity (cheap, prevents bugs)

### EC11 — `napf check` code numbering
§8 lists E001–E009, then **W105**, then E011 — E010 is absent and a warning sits
in the E-block. CLAUDE.md promises "E001–E011, W101–W105." Resolution: keep codes
stable (don't renumber live references); in §8 mark E010 as reserved/retired and
move W105 into the W-listing; correct CLAUDE.md to "E001–E009, E011 (E010
reserved), W101–W106."

### EC16 — counter off-by-one and guard-cycle check scope
- counter: "decrements per passing message; `continue` while > 0, `exhausted` at
  zero." Pin down whether `count: 10` means 10 retries or 10 total passes
  (check-then-decrement vs decrement-then-check) — the flagship loop's attempt
  count depends on it.
- W101 "every edge-cycle contains a guard": specify scope. *Every simple cycle*
  (correct, stricter, can be exponential to enumerate) vs *one guard per SCC*
  (cheaper, but a sub-cycle bypassing the guard slips through). State which
  guarantee the checker actually provides.

### EC17 — env validation split
There are two env checks: **W105** (warning, at `napf check` time, key missing
from *all* profiles) and the **ENV** lifecycle step (error, at `napf run` time,
key missing from the *active* profile). So `napf check` can pass while
`napf run --env staging` errors. Reasonable, but document the split so it isn't
read as a contradiction.

### EC18 — `nodes.*` is last-writer-wins
The `nodes.<id>.<port>` map holds the *latest* output per node in the frame.
Under cycles and concurrent branches this is order-dependent. The single asyncio
loop removes true data races, but the *logical* value seen by a reader depends on
scheduling. Document: prefer `{{ trigger }}` for the value that fired you;
`{{ nodes.* }}` is "last writer wins" and can surprise across cycles/fan-in.

### EC19 — Set/Get reintroduce ordering hazards wires avoid
The "wire everything explicitly" model makes ordering follow data flow. Set/Get
deliberately break the wire, so Set-then-Get ordering is only guaranteed if a
path exists from the Set to the Get's `trigger`. Document so users don't treat
frame variables as a synchronization primitive.

### EC20 — Abort leaves dangling `request_started`
On abort, in-flight requests are cancelled; their `request_started` is already in
JSONL with no matching `request_finished`. Replay should tolerate a dangling
start. One sentence in §7.

### EC21 — Node-id charset constraints
Ids are "stable human-readable" and appear in `nodes.<id>.<port>` paths and edge
endpoints. Constrain them to template/path-safe characters (no dots, spaces,
leading digits) and have E001/E011 enforce it, so an id can't break expression
parsing or `from: a.b.c` endpoint splitting.

### EC22 — Grandchild processes aren't killed
`terminate()`/`kill()` on the worker does not reap subprocesses the user's python
spawned (`subprocess.Popen` in `nodes.py`). Out of scope to fully solve, but
document the limitation (and consider a process-group kill on POSIX /
`CREATE_NEW_PROCESS_GROUP` on Windows later).

### EC23 — `defaults.request` template scope
The manifest blesses `{{ env.* }}` and `{{ run.* }}` in `defaults.request`.
`inputs`/`nodes` are frame-scoped and would be `StrictUndefined` → a node error
on *every* request that inherits the default. State that only `env`/`run` are
available there.
