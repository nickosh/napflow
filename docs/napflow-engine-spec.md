# napflow — Execution Engine Spec, v0.2

Status: **adopted 2026-07-02** (2026-06-14 edge-case review applied; see
`EDGE_CASES.md` for the ledger and `DECISIONS.md` D18–D23 for rationale).
Amended 2026-07-02: execution-timeout model — `max_seconds` default-scope
and routing, run deadline (EC25–EC27, **D24**). Amended 2026-07-02 (b),
senior review: worker stdout protocol integrity, loader write path +
diagnostics, native-value templating (**D25**), budget default, run
capture valve, Windows loop policy, trust model (EC28–EC37).

Builds on: flow schema v0.4 (message-driven, single-edge inputs,
everything-is-data), manifest v0.3, settled decisions (Jinja2, soft port
types, JSONL history, last-write-wins, macOS+Windows, canonical YAML
profile D23).

Changes from v0.1: run states redefined — required End ports (D18) and
run-wide outcome aggregation (D20); empty-seed guard in the pump (EC08);
request/loop/flow runner semantics pinned (EC13, EC06/D20, EC07/D21);
worker serialization + grandchild-process limitation documented
(EC09/EC22); templating envelope asymmetry + `nodes.*` last-writer-wins
(EC12/EC18); no-error-port evaluation failures defined (EC24); masking
algorithm + abort dangling-event note (D22/EC10, EC20); §8 renumbered
(E010 reserved, W105 moved, E012/W106/W107 added), AST-parse posture
(EC14), strict W101 scope (EC16).

## 0. Position in the architecture

```
napflow/
  core/        # THE ENGINE — zero web-framework imports, zero UI imports
    loader.py      # yaml → pydantic models (flow, manifest)
    checker.py     # napf check rules
    templating.py  # sandboxed Jinja2 environment
    engine.py      # scheduler, frames, node runners
    events.py      # event dataclasses (shared by JSONL + WebSocket)
    nodes/         # one module per node type
  cli/         # typer → core
  server/      # blacksheep → core (serves UI, streams events)
```

Hard rule: `core` is importable standalone (`from napflow.core import run_flow`)
— this is the pytest/CI/codegen surface.

Loader architecture (EC29): the loaded ruamel document (`CommentedMap`)
is the **single write source** — edits mutate it surgically and it alone
is emitted back to disk; the Pydantic models are validated *read-only
views* for checker/engine and are never serialized back (dumping a model
would silently delete comments). ruamel's line/column marks are retained
through validation so every diagnostic can point at file:line (§8).

## 1. Core objects

**Message** — the envelope on every edge:
```json
{
  "value": <any JSON-compatible>,
  "meta": {
    "msg_id": "m-000042",
    "produced_by": "check_job.response",
    "frame": "f-0/f-3",
    "ts": "2026-06-11T10:00:00.123Z"
  }
}
```

**Frame** — one invocation of one flow. The root run is frame `f-0`;
each flow-node call and each loop-body iteration opens a child frame.
A frame owns: its Set/Get variable map, its `inputs`, its node firing
counts, its guard states (counter values, timeout start stamps). Frames
are the **data** isolation unit — nothing leaks across except Start/End
port values. Outcomes are NOT isolated (D20, §2). Frame IDs are
hierarchical (`f-0/f-3/f-7`) for traceability.

**Run** — the whole execution tree rooted at one entry flow + env profile
+ bound inputs. Owns: `run.id`, the shared niquests `AsyncSession`, the
message budget, the event sink(s), the report accumulator.

## 2. Run lifecycle

```
LOAD      parse flow.yaml(s) + manifest (loader)
CHECK     full `napf check` rule set on the closure of referenced flows
BIND      validate & type-coerce inputs against Start ports; fail fast
ENV       env.required present in active profile? fail fast
EXECUTE   seed Start node, pump messages until quiescent
FINALIZE  collect End ports, close session, write report, emit run_finished
```

Run states: `pending → running → passed | failed | error | aborted`.
- `passed` — quiescent; **every required End port produced a value**
  (D18); no failed asserts and no unhandled error-port messages
  **anywhere in the frame tree** (D20).
- `failed` — completed but at least one of: a failed assert; an unhandled
  error-port message (an `error`/`failed` port with no edge receiving a
  message); **a required End port left unwritten at quiescence** (D18) —
  aggregated across all frames (D20).
- `error`  — engine-level failure (validation, env missing, budget hit,
  run deadline expired (D24), internal exception)
- `aborted`— user cancelled

**Aggregation (D20).** Frames isolate data, not outcomes. Asserts,
python-asserts, and unhandled error-port messages from subflow and
loop-body frames roll into the run-level report. Run state is the worst
outcome anywhere in the tree (`error` > `failed` > `passed`). A loop
iteration "error" = a body frame ending `failed`/`error`.

Note the env-validation split (EC17): W105 warns at `napf check` time
when an `env.required` key is missing from *all* discovered profiles; the
ENV lifecycle step errors at `napf run` time when a key is missing from
the *active* profile. `napf check` can pass while `napf run --env staging`
fails — by design, not contradiction.

CLI exit codes: 0 passed · 1 failed · 2 error · 130 aborted.

## 3. Scheduler

Single asyncio event loop per run. Pseudocode:

```python
queue: asyncio.Queue[Delivery | QUIESCENT]   # Delivery = (edge, message)
in_flight = 0    # queued deliveries + running node tasks, atomically tracked

def dec_in_flight():
    global in_flight
    in_flight -= 1
    if in_flight == 0:
        queue.put_nowait(QUIESCENT)     # wake the pump — avoids the race
                                        # where the last task finishes
                                        # while pump sleeps on an empty queue

async def pump():
    seed_sources()                      # start node + unconnected fixtures
    if in_flight == 0:                  # nothing seeded (e.g. Start.out unwired,
        finalize()                      # no auto-fixture, note-only flow). Without
        return                          # this, QUIESCENT is never enqueued and the
                                        # pump blocks forever on queue.get(). (EC08)
    while True:
        delivery = await queue.get()
        if delivery is QUIESCENT:
            break
        node = delivery.edge.target_node
        node.absorb(delivery)           # update slots / merge / guard state
        if node.ready():                # firing rule, see §4
            in_flight += 1              # task begins
            create_task(fire(node, frame))
        dec_in_flight()                 # delivery consumed
    finalize()

async def fire(node, frame):
    try:
        outputs = await node.run(inputs, ctx)   # may take long (request, delay)
        for port, value in outputs:
            for edge in port.edges:             # fan-out
                budget.tick()                   # message budget, abort on 0
                in_flight += 1
                queue.put_nowait(Delivery(edge, envelope(value)))
        emit(NodeSucceeded)
    except NodeError as e:
        route_to_error_port_or_record_unhandled(e)
    finally:
        dec_in_flight()
```

The QUIESCENT sentinel is the load-bearing detail: termination is detected
by whichever decrement reaches zero, never by the pump polling. Single
event loop → increments/decrements need no lock, but every emission MUST
increment before enqueueing. The same mechanism requires the empty-seed
guard above — if nothing increments `in_flight`, no decrement ever
enqueues QUIESCENT; `pump` finalizes immediately when the post-seed count
is zero (D14).

Properties:
- **Quiescence = termination**: run ends when `in_flight == 0`. No global
  ordering, no toposort.
- **Parallelism is free**: two messages to two nodes = two concurrent
  tasks. Long awaits (HTTP, delay) don't block siblings.
- **Budget**: every emitted message ticks the per-run budget
  (`defaults.run.message_budget`, default 100000 — runaway protection,
  not resource accounting; counts run-wide including child frames);
  exhaustion → run `error` with the hot edge identified in the event.
- **Deadline (D24)**: optional run-level wall clock
  (`defaults.run.run_timeout_s`, default null = off; CLI `--timeout`
  overrides). Expiry cancels in-flight work like an abort but finalizes
  with state `error` (`error_reason: run_timeout`) — the report and
  JSONL are still written, unlike a CI-runner SIGKILL.
- **Cancellation**: abort flips a token; tasks are cancelled, niquests
  session closed, run state `aborted`.

## 4. Firing rules (per node, per frame)

1. **Single connected input** → fire on every delivery. (Most nodes.)
2. **Multiple connected inputs** (python with several params, end with
   several ports): maintain a latest-value slot per connected input; fire
   when all slots are filled; later deliveries overwrite their slot and
   re-fire immediately. Unconnected optional inputs use defaults;
   unconnected required inputs = check error (E005).
3. **Merge** (the only node with growable inputs):
   - `any`     → forward every delivery immediately, independently.
   - `all`     → slots like rule 2; on full, emit `{in1: v1, ...}` and
                 CLEAR slots (strict rendezvous, no stale re-fire).
   - `collect` → append values until `count` reached, emit list, clear.
4. **Guards** consult/update frame-local state (counter value, timeout
   start). `reset` deliveries update state and emit nothing.
5. **End** ports are real inputs: accumulate latest value per port, never
   fire, never emit; values read at FINALIZE. **Note** has no ports.
6. **Sources**: `start` is seeded once at frame start (its `out` value is
   the frame's full `inputs` dict); `fixture` with an unconnected
   `trigger` is seeded once at frame start; `get` fires only on its
   `trigger` input.

The clearing semantics in 3 (`all`/`collect`) vs latest-value in rule 2 is
deliberate: plain multi-input nodes are *functions over current state*;
merge `all` is a *rendezvous of events*. This distinction is the single
most test-worthy piece of the engine.

User-facing consequences of rules 2–3 (EC03/EC04):
- A node with a connected input that never receives a message **never
  fires** — reported in `nodes_never_fired` ("skipped"). Skipped is a
  first-class outcome, not an error; when skipped work must fail the run,
  route its result to a required End port (D18).
- `merge: all` re-entered inside a cycle with only a partial input set
  **stalls** (slots were cleared on emit). Use `any` to rejoin retry
  paths; `all` is a one-shot rendezvous.
- Firings of the **same node may overlap in time** when the node is
  async (`request`, `delay`, `python`, `flow`, `loop`) and a new message
  arrives while a firing is in flight (e.g. fan-in through `merge: any`)
  — `nodes.*` then follows last-writer-wins (EC18). Sync nodes are
  serialized naturally by the event loop; python firings serialize at
  the worker (§5a). (EC36)

## 5. Node runners

- **request** — shared per-run `niquests.AsyncSession` (connection pooling,
  HTTP/2/3 negotiation). Engine-level retry loop per node config
  (attempts/backoff/conditions) so behavior is identical in future
  generated code. Emits `RequestStarted` + `RequestFinished` events with
  full wire detail (§7). Response value: `{status, headers, body,
  elapsed_ms, url, http_version, attempt}`. **Non-2xx responses are valid
  responses and emit on `response`; the `error` port carries
  transport-level failures only** (connection/DNS/TLS, or timeout after
  the configured retries) (EC13).
- **python** — executed in a **persistent worker subprocess** (§5a), one
  per flow module. `AssertionError` → `error` port + recorded in report as
  a python-assert; other exceptions → `error` port with traceback in meta;
  timeout/crash → `error` port with `error_kind: timeout | worker_crash`.
- **delay** — `await asyncio.sleep(seconds)`; cancellable.
- **assert** — evaluates checks against the incoming message; emits
  `AssertResult` events; forwards message on `passed`/`failed`.
- **condition / switch** — sandboxed Jinja2 expression against the
  templating context (§6); forward incoming message on taken port.
- **loop** — fires on its `trigger` input; `over` is a Jinja2 expression
  evaluated against that delivery. Opens a child frame per item; binds
  `item`, `index` to body Start; `sequential` = await each; `parallel` =
  `asyncio.Semaphore(max_concurrency)`; collects body End dicts to
  `results` — ordered by item index, not completion order (EC36) —
  failures to `errors`. An iteration "error" is a body frame
  ending `failed`/`error` (D20). `on_error: stop` halts scheduling of
  remaining iterations; failed iterations are routed to `errors` and
  counted toward the run regardless of mode (EC06). Node-level loop
  failures — a tripped explicit `max_seconds`, an `over` evaluation
  error — have no outlet: unhandled node error ⇒ run `failed` (EC24),
  the loop emits nothing, in-flight body frames are cancelled
  (`aborted`; outcomes they already recorded still aggregate). Branchable
  alternative: wrap the loop in a subflow, set `max_seconds` on the
  `flow` node (D24).
- **flow** — child frame, bind input ports → target Start, await
  quiescence of child frame, emit target End values on derived ports.
  Exposes an implicit `error` port (D21) that fires when the child frame
  ends `failed`/`error`, carrying `{state, failed_asserts,
  unhandled_errors}`; unconnected, the failure contributes to run failure
  per D20. A tripped `max_seconds` cancels the child frame (state
  `aborted`; asserts/errors it already recorded still aggregate per D20)
  and fires the same port with `{state: "aborted", error_kind:
  "timeout"}` (D24).
- **set/get** — frame variable map; set forwards written value.
- **fixture** — file read at first fire, cached per run; json/csv
  (csv → list of dicts, header row required).
- **log** — emits `LogEvent` (masked); forwards unchanged.

## 5a. Python worker subprocess (v1)

One worker process per flow module, spawned lazily at first use, killed
at FINALIZE.

```
engine ──spawn──▶ worker (configured interpreter)
        stdin :  {"task_id", "function", "inputs"}        one JSON per line
        stdout:  {"task_id", "outputs"} | {"task_id", "error", "traceback"}
                 (fd reserved for the protocol — see EC28 below)
        user stdout/stderr: captured → forwarded as log events
```

- **Protocol integrity (EC28)**: `print()` writes to stdout — the
  protocol channel. So at startup the worker `dup()`s the real stdout fd
  and keeps the duplicate exclusively for protocol lines, then rebinds
  `sys.stdout` (and `sys.stderr`) to capture streams forwarded as log
  events. A stray `print()` in `nodes.py` therefore cannot corrupt the
  protocol.
- Worker imports the flow's `nodes.py` once at startup; per-firing cost is
  a pipe round-trip (negligible vs any HTTP call).
- Multi-flow runs (subflows with their own `nodes.py`): one worker per
  flow module, spawned lazily on first use, capped pool.
- **Serial execution (EC09)**: the worker processes one task at a time
  (request→response per pipe line). Consequences: python firings within a
  flow module are serialized — a `mode: parallel` loop whose body hits
  python nodes does not gain CPU parallelism there — and a single stuck
  firing blocks the module's python until its `max_seconds` kill, after
  which the worker is respawned lazily. Acceptable under the "pipe
  round-trip ≪ HTTP" assumption; a per-module worker pool is deferred (it
  would complicate shared module state).
- **Timeout enforcement (D24)**: engine awaits the task with the node's
  `max_seconds`. On expiry: `terminate()` → grace 2s → `kill()`; emit
  `{error_kind: "timeout"}` to the node's `error` port; respawn worker
  lazily on next python firing. `max_seconds` is settable on **any**
  node (cancellation for non-python nodes), but the *default*
  (`defaults.run.node_timeout_s`, 300) auto-applies only to `request`
  and `python` — the potentially-unbounded leaf firings. `delay`
  (self-bounded by config) and `loop`/`flow` (bounded transitively by
  their children + budget) have no default ceiling. Routing per node:
  §5; nodes without an error port: §6/EC24. Timeouts are error-port
  data, never success-port payloads.
- **Crash isolation**: worker death (segfault, OOM, sys.exit) becomes a
  node error, never an engine failure.
- **Grandchild processes (EC22)**: killing the worker does not reap
  subprocesses the user's python spawned (`subprocess.Popen` in
  `nodes.py`). Known v1 limitation — documented, not solved. Candidate
  later: process-group kill on POSIX / `CREATE_NEW_PROCESS_GROUP` on
  Windows.
- **Interpreter**: `python.interpreter` in napflow.yaml; default = the
  interpreter running napflow. Pointing it at a project venv makes that
  environment's third-party packages available to `nodes.py` — the
  stdlib-only rule is therefore lifted to "whatever the configured
  interpreter provides".
- Values crossing the pipe must be JSON-serializable — same constraint as
  the wire format, so nothing new for users.
- Windows notes: spawn semantics, `CREATE_NO_WINDOW`, `terminate()`
  reliable. asyncio subprocess pipes require the **Proactor** event
  loop: `napf run` uses Python's default (Proactor since 3.8), but the
  `napf ui` server stack must not switch the policy to Selector (some
  ASGI/WebSocket setups do) — a Windows integration test runs a
  python-node flow *through the server* to lock this in (EC33, TR-9).

## 6. Templating context

Sandboxed Jinja2 (`SandboxedEnvironment`), per-frame context:

```
env.*      active profile (post process-env override)
inputs.*   this frame's Start bindings
run.*      id, timestamp, env_name        (run-wide)
nodes.*    latest output values of nodes IN THIS FRAME, by id.port
trigger    the message that fired this node (request: the trigger delivery)
item/index inside loop-body frames (also bound as inputs)
```

`trigger` is the full `{value, meta}` envelope — reach into it as
`trigger.value.…` (e.g. `{{ trigger.value.body.id }}`). `nodes.<id>.<port>`
is the **unwrapped** port value — `{{ nodes.req.response.body }}`, no
`.value`. Prefer `{{ trigger }}` for the value that fired the current
node; `{{ nodes.* }}` holds each node's *latest* output and is
last-writer-wins under cycles and concurrent branches (EC12/EC18).

Undefined variable → node error (StrictUndefined), routed to the node's
error port — never silently empty strings into URLs. **Nodes without an
error port** (condition, switch, merge, guards, set/get, delay, log,
fixture — e.g. a runtime CSV parse error) surface evaluation errors as
*unhandled node errors*: recorded in the report, run marked `failed` —
same outcome as a message into an unconnected error port (EC24).

**Native-value rule (D25).** A config value that is exactly one
`{{ expression }}` (ignoring surrounding whitespace) evaluates to the
expression's **native value** — dicts, lists, numbers, booleans, null
keep their type (`body: "{{ nodes.login.response.body }}"` passes the
dict itself, not a repr string). Any mixed content renders to a string.
After evaluation, the config field's schema type applies: string-typed
fields stringify the result, object-typed fields reject scalars. Bare
`expr:` fields are always native.

Start-port `default:` templates are evaluated once at BIND with only
`env.*`/`run.*` in scope — the frame does not exist yet; same
restriction as `defaults.request` (EC36).

## 7. Event vocabulary (JSONL file ≡ WebSocket stream)

One JSON object per line/frame. Common fields:
`{event, run_id, frame, node, ts, seq}`. Types:

```
run_started      {flow, env_name, inputs(masked), engine_version}
node_fired       {firing_no}
request_started  {method, url, headers(masked), body_preview, attempt}
request_finished {status, http_version, headers(masked), body, size_bytes,
                  timing: {dns_ms?, connect_ms?, tls_ms?, ttfb_ms, total_ms},
                  attempt, retries_total}
request_failed   {error_kind, message, attempt, will_retry}
message_emitted  {from_port, to_node, to_port, msg_id, value_preview}
assert_result    {check, op, expected, actual, passed}
python_error     {function, error_type, message, traceback}
log              {label, level, value(masked)}
guard_tripped    {kind: counter|timeout, port: exhausted|expired}
budget_warning   {remaining}            # at 10% left
capture_warning  {remaining_mb}         # run capture budget at 10% left
run_finished     {state, duration_ms, asserts: {passed, failed},
                  unhandled_errors, end_outputs(masked),
                  nodes_never_fired: [node_ids],   # "skipped" for UI/report
                  error_reason?}                   # when state=error:
                                                   # budget_exhausted |
                                                   # run_timeout | ...
```

Rules:
- **Masking (D22)**: secrets are masked at emission — events are born
  masked. The *values* of env vars matching `environments.secrets`
  (active profile + process env) are replaced wherever they appear, via
  substring scan with a 5-char minimum length. Only declared secrets are
  masked — tokens acquired at runtime (e.g. in a login response body) are
  stored in full; history shareability (D13) is scoped to declared
  secrets. Runtime redaction is a roadmap item (manifest).
- `value_preview` truncates large bodies in stream events, but
  `request_started`/`request_finished` store **complete request and
  response bodies** in JSONL — full wire detail always. A disk-protection
  ceiling (`defaults.run.body_capture_mb`, default 10) caps pathological
  payloads only, marking `truncated: true` when hit. A run-level ceiling
  (`defaults.run.run_capture_mb`, default 500) additionally caps total
  captured body bytes per run — a big loop against a fat endpoint must
  not write gigabytes of JSONL; once exceeded, further bodies are
  truncated with the same marker (`capture_warning` fires at 10%
  remaining) (EC32).
- Timing fields included where niquests exposes them, else omitted.
- On abort, an in-flight request leaves a `request_started` with no
  matching `request_finished`; replay tolerates a dangling start (EC20).
- UI replay = re-read the JSONL.

## 8. `napf check` rules (v1)

```
E001 yaml parse / schema validation failure
E002 unknown node type / unknown config keys
E003 edge references missing node or port
E004 multiple edges into one input port
E005 missing required input port connection
E006 exactly-one start / exactly-one end violated
E007 flow-reference cycle (with path)
E008 broken flow/fixture file reference
E009 jinja2 syntax error in any config string / expr
E011 duplicate node id, or id violating [A-Za-z_][A-Za-z0-9_]*
E012 reserved port name `error` declared (End ports, python outputs)
     # E010 retired/reserved — do not reuse

W101 edge-cycle without counter/timeout guard
W102 port type mismatch on edge
W103 unconnected error/failed output (failures mark run failed)
W104 unreachable node (no path from start)
W105 env.required key missing from ALL discovered profiles
W106 guard exhaustion/timeout port unconnected (loop exit produces no output)
W107 unquoted scalar in a string-typed field matching YAML's
     implicit-coercion danger set (hand-edited files; see yaml-profile.md)
```

Errors block `napf run`; warnings print and proceed (UI shows both on
canvas).

Diagnostic quality is product surface (EC29): every E/W message carries
the file path, line/column (ruamel source marks threaded through
validation — a day-one loader requirement, painful to retrofit), the
offending node id, and a one-line fix hint.

- **W101 scope (EC16)**: the guarantee is strict — *every simple cycle
  contains a guard*. Checked in linear time: delete guard nodes from the
  edge graph and test for acyclicity; any remaining cycle is exactly a
  guard-free cycle (reported with its path). No cycle enumeration needed.
- **Static-analysis posture (EC14)**: `napf check` derives python input
  ports by **AST-parsing** `nodes.py` (no import side effects, safe as a
  CI pre-gate); the worker imports it for real at run time. Signatures
  built dynamically at import time are invisible to `check` — write
  literal `def` signatures for node functions.
- W105 vs the ENV lifecycle step: see §2 (EC17).

## 9. Resolved (was: open questions)

- **Body capture: always full.** Complete request/response bodies in every
  run's JSONL; 10MB-per-body disk valve with `truncated: true` marker.
- **Per-node execution timeout: IN v1**, enforced via the worker
  subprocess model (§5a) for python nodes and task cancellation for all
  others. Per-node `max_seconds`, global default
  `defaults.run.node_timeout_s: 300`.
- **Loop parallel sessions: shared** per-run niquests session by default
  (connection pooling); `fresh_session: true` opt-out on the loop node
  for cookie-/state-sensitive APIs.
- **2026-07-02 review closure**: run states (D18/D20), guard outputs
  (D19), flow error port (D21), masking scope (D22), empty-seed guard
  (EC08), strict W101, AST-parse posture (EC14), counter = N passes
  (EC16), EC24 no-error-port rule.
- **2026-07-02 amendments (same day)**: timeout model (D24, EC25–EC27);
  senior-review fixes incl. native-value templating (D25, EC28–EC37).

## 10. Roadmap notes (v1.1 candidates)

- `poll` node (sugar over merge/condition/counter/delay).
- **`duplicate` node** — split a message into N identical parallel
  copies without a body flow. Largely covered today by output fan-out
  (concurrent by construction) and parallel loop over a range; add only
  if real flows show the workaround is clumsy.
- Inline loop bodies.
- Per-module python worker pool (lifts the serial-worker limitation, §5a).
- Runtime secret redaction (see manifest roadmap, D22).

## 11. Security & trust model (EC35)

- **Flows are code.** A workspace's `nodes.py` is arbitrary Python
  executed by the worker; **running a workspace = executing it**. Review
  `flow.yaml` + `nodes.py` in PRs exactly like code; never run an
  untrusted workspace. napflow does not attempt to sandbox `nodes.py`.
- **The Jinja2 sandbox is accident protection, not a security boundary.**
  Templates live in the same trust domain as `nodes.py`; the
  `SandboxedEnvironment` guards against foot-guns (attribute escapes,
  accidental mutation), not against a hostile flow author. Accepted
  risk: template rendering is synchronous on the engine loop, so a
  pathological expression can stall the run — same trust domain as user
  code; the run deadline (D24) is the backstop.
- **The server binds localhost only.** `napf ui` serves on `127.0.0.1`
  with no authentication in v1 — do not bind it to public interfaces;
  remote/multi-user operation is out of scope.
- **Secrets**: declared-secret masking at emission per D22;
  runtime-acquired tokens are stored in full until runtime redaction
  lands (manifest roadmap).
