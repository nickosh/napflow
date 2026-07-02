# napflow — Spec Deltas (Review 2026-06-14)

Intended location: `docs/spec-deltas-2026-06-14.md`

Paste-ready edits to resolve the edge cases in `docs/EDGE_CASES.md`, in the
"update the spec in the same PR as the code" style. Organized as: new ADR
entries (genuine forks), then targeted edits per spec file. Recommendations are
opinionated; the three marked **(confirm)** are judgment calls worth a second
look before you commit them.

Resolution map: D18→EC01 · D19→EC02 · D20→EC05/EC06/EC07 · D22→EC10.
EC08 is a fix folded into engine §3 + D14 (no new ADR). Everything else is a
documentation edit, listed under the per-spec sections.

---

## 1. New `DECISIONS.md` entries (append after D17)

### D18 — Declared outputs are required by default (incomplete run = failure)
**(confirm: default-required vs default-optional.)**
The message-driven engine treats "node never fired" as benign (`skipped`),
which is correct for optional branches but means a dropped *required* result
passes silently — a false green for a CI-first tool. Chosen: an End port that
holds no value at quiescence makes the run `failed`. End ports gain
`required: bool` (default `true`); set `required: false` for genuinely
conditional outputs (e.g. an `error_detail` port set only on a failure branch).
Rejected: warning-only (the v0.3 behavior — too quiet for the headline use
case); a global `--strict` flag (puts the safe behavior behind a toggle).
Load-bearing: D19's "gave up" failure and the loop-iteration failure definition
(D20) both route through this rule rather than through special port magic.

### D19 — Guard exhaustion ports are ordinary outputs, not error ports
Wire-format v0.3 lumped `exhausted`/`expired` with `error`/`failed` ("errors
travel as data"), but whether a tripped guard is a *failure* depends entirely on
intent: "retry until ready, gave up" is a failure; "process up to N items,
done" is a clean stop. Encoding failure into the port is therefore wrong.
Chosen: `exhausted`/`expired` are pass-through outputs carrying the triggering
message; unconnected = dropped, like any non-error output. Failure for the
retry case comes from D18 (the unreached `end.job` fails the run) or from an
assert the user wires on the exhausted path. A non-failing lint W106 flags an
unconnected exhaustion port for canvas feedback. Consequence: the
`create_until_ready` example must wire `attempts.exhausted` somewhere explicit.

### D20 — Results aggregate run-wide; only data is frame-isolated
Frames isolate *data* (variables, guard state, node outputs cross only via
Start/End). They do **not** isolate *outcomes*. Assert results, python-asserts,
and unhandled error-port messages from every frame — subflows and loop bodies
included — aggregate into the single run-level report and exit code. Run state
is the worst outcome anywhere in the frame tree (`error` > `failed` > `passed` >
n/a). A loop "iteration error" (routed to the loop's `errors` port and counted
in the run) is defined as a body frame ending `failed`/`error`; `on_error: stop`
halts scheduling of further iterations but does not change how already-failed
iterations score. Without this, assert-driven exit codes (the whole point of
`napf run`) are meaningless for any flow that uses subflows or loops.

### D21 — `flow` node exposes an implicit `error` port
**(confirm: add the port vs rely on D20 aggregation only.)**
A subflow that ends `failed`/`error` had no outlet on the `flow` node (ports are
derived from target Start/End), unlike `request`/`python`/`loop`. Chosen: the
`flow` node gains an implicit `error` port (consistent with "errors are data")
that fires when the child frame ends `failed`/`error`, carrying a summary
`{state, failed_asserts, unhandled_errors}`; unconnected → contributes to run
failure per D20, so behavior is safe by default and branchable when wired.
Rejected: aggregation-only (failures count toward the run but can't drive a
fallback branch — too weak given subflows are a core composition unit).

### D22 — Secret masking: declared values only, substring scan, runtime tokens deferred
D13 promised "history is shareable by construction." True only for env-declared
secrets. Specifying the gap rather than overclaiming: (1) masking replaces the
*values* of env vars whose names match `environments.secrets` patterns (active
profile + process env), via substring scan with a minimum length (ignore values
shorter than 5 chars) to catch tokens embedded in URLs/bodies without
over-masking short common strings; (2) secrets *acquired at runtime* (a bearer
token in a login response body) are **not** env vars and are stored in full —
the shareability guarantee is scoped to declared secrets and stated as such;
(3) runtime redaction (a `set ... secret: true`, or a response field-path
redaction directive) is a roadmap item, not v1. Rejected: silent partial
coverage behind an absolute-sounding promise.

### Also update `DECISIONS.md` "Known open risks"
Append:
- Default-required End ports (D18) may annoy flows with conditional outputs —
  watch whether `required: false` becomes boilerplate; if so, reconsider the
  default.
- W101 cycle-guard analysis scope (every simple cycle vs per-SCC) — pick before
  the checker ships; affects the strength of the safety guarantee.

---

## 2. `napflow-engine-spec.md` edits

### §2 Run lifecycle — replace the run-state bullets
Replace the `passed`/`failed` definitions with:

> - `passed` — quiescent; **every required End port produced a value** (D18);
>   no failed asserts and no unhandled error-port messages **anywhere in the
>   frame tree** (D20).
> - `failed` — completed but at least one of: a failed assert; an unhandled
>   error-port message (an `error`/`failed` port with no edge receiving a
>   message); **a required End port left unwritten at quiescence** (D18) —
>   aggregated across all frames (D20).

Add after the run-state list:

> **Aggregation (D20).** Frames isolate data, not outcomes. Asserts, python-
> asserts, and unhandled error-port messages from subflow and loop-body frames
> roll into the run-level report. Run state is the worst outcome anywhere in the
> tree. A loop iteration "error" = a body frame ending `failed`/`error`.

### §3 Scheduler — fix the empty-seed deadlock (EC08)
In `pump()`, immediately after `seed_sources()`:

```python
async def pump():
    seed_sources()
    if in_flight == 0:          # nothing seeded (e.g. Start.out unwired,
        finalize()              # no auto-fixture, note-only flow). Without
        return                  # this, QUIESCENT is never enqueued and the
                                # pump blocks forever on queue.get().
    while True:
        ...
```

Add a sentence to D14's rationale: "The same sentinel mechanism requires a
guard for the empty-seed case — if nothing increments `in_flight`, no decrement
ever enqueues QUIESCENT; `pump` finalizes immediately when the post-seed count
is zero."

### §5 Node runners — request, loop, flow
- **request:** append "Non-2xx responses are valid responses and emit on
  `response`; the `error` port carries transport-level failures only
  (connection/DNS/TLS, or timeout after the configured retries)." (EC13)
- **loop:** append "An iteration 'error' is a body frame ending `failed`/`error`
  (D20). `on_error: stop` halts scheduling of remaining iterations; failed
  iterations are routed to `errors` and counted toward the run regardless of
  mode." (EC06)
- **flow:** append "Exposes an implicit `error` port (D21) that fires when the
  child frame ends `failed`/`error`, carrying `{state, failed_asserts,
  unhandled_errors}`; unconnected contributes to run failure per D20." (EC07)

### §5a Python worker — concurrency note (EC09)
Append:

> The worker processes one task at a time (request→response per pipe line).
> Consequences: python firings within a flow module are serialized — a
> `mode: parallel` loop whose body hits python nodes does not gain CPU
> parallelism there — and a single stuck firing blocks the module's python until
> its `max_seconds` kill, after which the worker is respawned lazily. This is
> acceptable under the "pipe round-trip ≪ HTTP" assumption; a per-module worker
> pool is deferred (it would complicate shared module state).

### §6 Templating context — envelope asymmetry (EC12, EC18)
Append to the context description:

> `trigger` is the full `{value, meta}` envelope — reach into it as
> `trigger.value.…` (e.g. `{{ trigger.value.body.id }}`). `nodes.<id>.<port>`
> is the **unwrapped** port value — `{{ nodes.req.response.body }}`, no
> `.value`. Prefer `{{ trigger }}` for the value that fired the current node;
> `{{ nodes.* }}` holds each node's *latest* output and is last-writer-wins
> under cycles and concurrent branches.

### §7 Event vocabulary — masking + abort (EC10, EC20)
- Replace the masking sentence with: "Secrets are masked at emission: the
  *values* of env vars matching `environments.secrets` (active profile + process
  env) are replaced wherever they appear, via substring scan with a 5-char
  minimum length. Only declared secrets are masked — tokens acquired at runtime
  (e.g. in a login response body) are stored in full; history shareability (D13)
  is scoped to declared secrets (D22)."
- Append to the rules: "On abort, an in-flight request leaves a `request_started`
  with no matching `request_finished`; replay tolerates a dangling start."

### §8 `napf check` rules — numbering + new lint (EC11, EC02)
- Mark E010 reserved and move W105 into the warning block:

```
E001..E009  (unchanged)
E011 duplicate node id within a flow        # E010 retired/reserved — do not reuse
W101 edge-cycle without counter/timeout guard
W102 port type mismatch on edge
W103 unconnected error/failed output (failures mark run failed)
W104 unreachable node (no path from start)
W105 env.required key missing from ALL discovered profiles
W106 guard exhaustion/timeout port unconnected (loop exit produces no output)
```

- Decide and state the static-analysis posture: "`napf check` derives python
  input ports by **AST-parsing** `nodes.py` (no import side effects); the worker
  imports it for real at run time." (EC14)
- State the W101 scope: **(confirm)** "guard analysis requires a guard on every
  *simple* cycle, not merely one per strongly-connected component."

---

## 3. `napflow-flow-schema.md` edits

### *Start / End node rules* — required outputs (D18)
Add:

> End ports take an optional `required: bool` (default `true`). At quiescence a
> required port with no value fails the run (D18); `required: false` ports yield
> `null` with a report warning. Example:
> ```yaml
>   - id: end
>     type: end
>     config:
>       ports:
>         - { name: job }                      # required (default)
>         - { name: error_detail, required: false }
> ```

### *Wire format* — drop the error grouping for guards (D19)
Replace "errors travel as data via `error`/`failed`/`exhausted`/`expired`
ports" with:

> Errors travel as data via `error`/`failed` ports. Guard ports
> `exhausted`/`expired` are **ordinary pass-through outputs** carrying the
> triggering message, not error ports — unconnected, their message is dropped
> (W106 lints this). Whether a tripped guard is a failure is decided by what you
> wire to it (D19).

### Example `flow.yaml` — wire the exhausted path (D18/D19)
The `create_until_ready` example currently drops `attempts.exhausted`. Make
"gave up" explicit so the run fails correctly. Add an end port and an edge:

```yaml
  - id: end
    type: end
    config:
      ports:
        - name: job
        - name: gave_up
          required: false
edges:
  ...
  - { from: attempts.exhausted, to: end.gave_up }   # explicit "gave up" output
```

With D18, if the job never reaches `done`, `end.job` is never written and the
run is `failed` (exit 1) — the correct CI outcome — while `gave_up` records
why. **(confirm)** that "retries exhausted" should be exit 1 for your use case;
if some flows treat exhaustion as success, mark `job` `required: false` there.

Also switch `is_ready` to read the deterministic trigger value (EC12):

```yaml
  - id: is_ready
    type: condition
    config: { expr: "trigger.value.body.state == 'done'" }
```

### *Node type catalog* — loop input + start payload (EC15)
- loop row, ports column: change to
  `in: trigger; out: results, errors`, and add to *Loop node semantics*:
  "The loop fires on its `trigger` input; `over` is a Jinja2 expression
  evaluated against that delivery (e.g. `over: trigger.value` or
  `over: nodes.fetch.list`)."
- start row: add to a note under the catalog: "`start.out` carries the frame's
  full `inputs` dict as its value (so it is never an empty pulse and downstream
  templates over it are defined)."

### File-format / node ids (EC21)
Add to the file-format invariants: "Node ids match `[A-Za-z_][A-Za-z0-9_]*` —
template/path-safe so `nodes.<id>.<port>` and `from: <id>.<port>` parse
unambiguously; E011 also rejects ids that violate this."

### *Scoping rules* — Set/Get ordering caveat (EC19)
Append: "Set/Get intentionally break the data wire, so Set-before-Get ordering
holds only when a path exists from the Set to the Get's `trigger`. Frame
variables are not a synchronization primitive."

---

## 4. `napflow-workspace-manifest.md` edits

### *Resolution rules* — masking precision (D22, EC10)
Replace rule 5 with:

> 5. **Secret masking** replaces the *values* of env vars matching
>    `environments.secrets` (active profile + process env) wherever they appear,
>    via substring scan with a 5-char minimum length — catching tokens embedded
>    in URLs/bodies without masking short common strings. Only declared secrets
>    are masked; runtime-acquired tokens are not (D22). Masking applies in UI,
>    logs, and stored runs alike.

### *defaults.request* template scope (EC23)
Add a sentence under the `defaults.request` block: "Only `{{ env.* }}` and
`{{ run.* }}` are in scope here; `inputs`/`nodes` are frame-scoped and would be
`StrictUndefined` (a node error on every inheriting request)."

### roadmap (D22)
Add to a roadmap/reserved note: "Runtime secret redaction — `set ... secret:
true` or a response field-path redaction directive — so login-acquired tokens
can opt into masking. Deferred from v1."

---

## 5. `CLAUDE.md` edits

- *Execution model invariants* / `napf check` reference: change "E001–E011,
  W101–W105" to "E001–E009, E011 (E010 reserved), W101–W106" (EC11).
- *Testing priorities* — add two rows, both now first-class behaviors:
  - "Required-End-port failure path (D18): unreached required output ⇒ run
    `failed` ⇒ exit 1, across subflow/loop frames (D20)."
  - "Guard exhaustion routing (D19): `exhausted`/`expired` as pass-through
    outputs; W106 lint; empty-seed quiescence (engine §3)."
- *Execution model invariants* — the "Errors are data" line currently says
  "Unconnected error port receiving a message ⇒ run `failed`." Add: "Unreached
  required End port at quiescence ⇒ run `failed` (D18). Guard exhaustion ports
  are not error ports (D19)."
