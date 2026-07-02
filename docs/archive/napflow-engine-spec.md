# napflow — Execution Engine Spec, Draft v0.1

Builds on: flow schema v0.3 (message-driven, single-edge inputs,
everything-is-data), manifest v0.2, settled decisions (Jinja2, soft port
types, JSONL history, last-write-wins, macOS+Windows).

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
are the isolation unit — nothing leaks across except Start/End port
values. Frame IDs are hierarchical (`f-0/f-3/f-7`) for traceability.

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
- `passed` — quiescent, no failed asserts, no unhandled node errors
- `failed` — completed but ≥1 failed assert OR ≥1 unhandled error-port
  message (error port with no edge attached)
- `error`  — engine-level failure (validation, env missing, budget hit,
  internal exception)
- `aborted`— user cancelled

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
increment before enqueueing.

Properties:
- **Quiescence = termination**: run ends when `in_flight == 0`. No global
  ordering, no toposort.
- **Parallelism is free**: two messages to two nodes = two concurrent
  tasks. Long awaits (HTTP, delay) don't block siblings.
- **Budget**: every emitted message ticks the per-run budget
  (`defaults.run.message_budget`, default 10000); exhaustion →
  run `error` with the hot edge identified in the event.
- **Cancellation**: abort flips a token; tasks are cancelled, niquests
  session closed, run state `aborted`.

## 4. Firing rules (per node, per frame)

1. **Single connected input** → fire on every delivery. (Most nodes.)
2. **Multiple connected inputs** (python with several params, end with
   several `from:`s resolved via ports): maintain a latest-value slot per
   connected input; fire when all slots are filled; later deliveries
   overwrite their slot and re-fire immediately. Unconnected optional
   inputs use defaults; unconnected required inputs = check error.
3. **Merge** (the only node with growable inputs):
   - `any`     → forward every delivery immediately, independently.
   - `all`     → slots like rule 2; on full, emit `{in1: v1, ...}` and
                 CLEAR slots (strict rendezvous, no stale re-fire).
   - `collect` → append values until `count` reached, emit list, clear.
4. **Guards** consult/update frame-local state (counter value, timeout
   start). `reset` deliveries update state and emit nothing.
5. **End** ports are real inputs: accumulate latest value per port, never
   fire, never emit; values read at FINALIZE. **Note** has no ports.
6. **Sources**: `start` is seeded once at frame start; `fixture` with an
   unconnected `trigger` is seeded once at frame start; `get` fires only
   on its `trigger` input.

The clearing semantics in 3 (`all`/`collect`) vs latest-value in rule 2 is
deliberate: plain multi-input nodes are *functions over current state*;
merge `all` is a *rendezvous of events*. This distinction is the single
most test-worthy piece of the engine.

## 5. Node runners

- **request** — shared per-run `niquests.AsyncSession` (connection pooling,
  HTTP/2/3 negotiation). Engine-level retry loop per node config
  (attempts/backoff/conditions) so behavior is identical in future
  generated code. Emits `RequestStarted` + `RequestFinished` events with
  full wire detail (§7). Response value: `{status, headers, body,
  elapsed_ms, url, http_version, attempt}`.
- **python** — executed in a **persistent worker subprocess** (§5a), one
  per run. `AssertionError` → `error` port + recorded in report as a
  python-assert; other exceptions → `error` port with traceback in meta;
  timeout/crash → `error` port with `error_kind: timeout | worker_crash`.
- **delay** — `await asyncio.sleep(seconds)`; cancellable.
- **assert** — evaluates checks against the incoming message; emits
  `AssertResult` events; forwards message on `passed`/`failed`.
- **condition / switch** — sandboxed Jinja2 expression against the
  templating context (§6); forward incoming message on taken port.
- **loop** — opens a child frame per item over `over`; binds `item`,
  `index` to body Start; `sequential` = await each; `parallel` =
  `asyncio.Semaphore(max_concurrency)`; collects body End dicts to
  `results`, failures to `errors` per `on_error`.
- **flow** — child frame, bind input ports → target Start, await
  quiescence of child frame, emit target End values on derived ports.
- **set/get** — frame variable map; set forwards written value.
- **fixture** — file read at first fire, cached per run; json/csv
  (csv → list of dicts, header row required).
- **log** — emits `LogEvent` (masked); forwards unchanged.

## 5a. Python worker subprocess (v1)

One worker process per run, spawned at EXECUTE, killed at FINALIZE.

```
engine ──spawn──▶ worker (configured interpreter)
        stdin :  {"task_id", "function", "inputs"}        one JSON per line
        stdout:  {"task_id", "outputs"} | {"task_id", "error", "traceback"}
        stderr:  user print()/logging → forwarded as log events
```

- Worker imports the flow's `nodes.py` once at startup; per-firing cost is
  a pipe round-trip (negligible vs any HTTP call).
- Multi-flow runs (subflows with their own `nodes.py`): one worker per
  flow module, spawned lazily on first use, capped pool.
- **Timeout enforcement**: engine awaits the task with the node's
  `max_seconds` (default `defaults.run.node_timeout_s`, 300). On expiry:
  `terminate()` → grace 2s → `kill()`; emit error to the node's `error`
  port; respawn worker lazily on next python firing. Same ceiling applies
  to all node types (cancellation for delay/request/flow/loop).
- **Crash isolation**: worker death (segfault, OOM, sys.exit) becomes a
  node error, never an engine failure.
- **Interpreter**: `python.interpreter` in napflow.yaml (manifest key now
  active); default = the interpreter running napflow. Pointing it at a
  project venv makes that environment's third-party packages available to
  `nodes.py` — the stdlib-only rule is therefore lifted to "whatever the
  configured interpreter provides".
- Values crossing the pipe must be JSON-serializable — same constraint as
  the wire format, so nothing new for users.
- Windows note: spawn semantics, `CREATE_NO_WINDOW`, terminate() reliable.

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

Undefined variable → node error (StrictUndefined), routed to error port —
never silently empty strings into URLs.

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
run_finished     {state, duration_ms, asserts: {passed, failed},
                  unhandled_errors, end_outputs(masked),
                  nodes_never_fired: [node_ids]}   # "skipped" for UI/report
```

Rules: secrets masked at emission (events are born masked — history can
be shared safely); `value_preview` truncates large bodies in stream
events, but `request_started`/`request_finished` store **complete request
and response bodies** in JSONL — full wire detail always. A
disk-protection ceiling (`defaults.run.body_capture_mb`, default 10) caps
pathological payloads only, marking `truncated: true` when hit. Timing
fields included where niquests exposes them, else omitted. UI replay =
re-read the JSONL.

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
W105 env.required key missing from ALL discovered profiles
E011 duplicate node id within a flow
W101 edge-cycle without counter/timeout guard
W102 port type mismatch on edge
W103 unconnected error/failed output (failures will mark run failed)
W104 unreachable node (no path from start)
```

Errors block `napf run`; warnings print and proceed (UI shows both on
canvas).

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

## 10. Roadmap notes (v1.1 candidates)

- `poll` node (sugar over merge/condition/counter/delay).
- **`duplicate` node** — split a message into N identical parallel
  copies without a body flow. Largely covered today by output fan-out
  (concurrent by construction) and parallel loop over a range; add only
  if real flows show the workaround is clumsy.
- Per-node execution timeout, landing together with subprocess isolation —
  **moved into v1** (§5a).
- Inline loop bodies.
