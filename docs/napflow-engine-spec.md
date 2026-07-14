# napflow — Execution Engine Spec, v0.2

Status: **adopted 2026-07-02** (2026-06-14 edge-case review applied; see
`EDGE_CASES.md` for the ledger and `DECISIONS.md` D18–D23 for rationale).
Amended 2026-07-02: execution-timeout model — `max_seconds` default-scope
and routing, run deadline (EC25–EC27, **D24**). Amended 2026-07-02 (b),
senior review: worker stdout protocol integrity, loader write path +
diagnostics, native-value templating (**D25**), budget default, run
capture valve, Windows loop policy, trust model (EC28–EC37).

Compatibility/current-state note (D33–D39): this document describes current
implemented behavior; superseded v0.1 behavior is labeled historical.
Event/history formats remain experimental during package v0.x. Fair lifecycle,
full-fidelity blob-backed history, raw local truth, and optional
terminal/report masking are implemented. Amended 2026-07-13 for v0.2/M4:
production streams activate `content-blobs/1`, full messages and prepared-wire
HTTP records replace previews, destructive capture valves are removed, and
core/report/server/UI consumers preserve lazy descriptor boundaries. Amended
2026-07-13 for v0.2/M5: versioned REST pages bound event/frame responses,
browser rows resolve one record's blobs only when expanded, and direct-child
summary pages reconstruct completed frame trees. Exports, advanced replay,
and the 100k-event performance target remain deferred.

### v0.2 compatibility and format summary

These are experimental v0.x contracts, not a migration guarantee. The
release-facing upgrade note is `release-notes-v0.2.0.md`; this section is the
normative format summary for implementers.

- Production v0.2 history begins with `run_started` at `seq: 1`,
  `format: "napflow-run/1"`, and `features: ["content-blobs/1"]`. The
  canonical sequence is consecutive and ordered by `seq`, never timestamps.
- Every declared content path carries a complete value. Values through exactly
  65,536 encoded bytes remain inline; larger values use verified store-once
  descriptors. `message_emitted.value`, prepared-request/response aggregates,
  and `frame_finished` are current; `value_preview` and `capture_warning` are
  legacy-readable but never produced.
- Canonical JSONL and the local WebSocket are raw. Declared-secret masking is a
  separate terminal/report projection and never rewrites protocol structure.
- A genuinely markerless v0.1 history (both `format` and `features` absent) is
  read best-effort. A short-lived M0 `napflow-run/1` header with no `features`
  means `[]`. Explicit null/malformed fields, a newer major, or an unknown
  feature are refused. Featureless records never interpret `$napflow` as a
  descriptor. These adapters do not make v0.1 history forward-compatible.
- Browser replay is the versioned, bounded `napflow-replay/1` projection over
  that same recording; it never changes or re-executes canonical history.

Builds on: flow schema v0.4 (message-driven, single-edge inputs,
everything-is-data), manifest v0.3, settled decisions (Jinja2, soft port
types, JSONL history, last-write-wins, macOS+Windows+Linux (D26),
canonical YAML profile D23).

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

### Public Python embedding contract (v0.2 M6; D38)

The core package exposes the same run semantics through functional and object
surfaces, with no CLI or server import:

```python
from napflow.core import load_workspace, run_flow, run_flow_async

workspace = load_workspace(path)
flow_identity = "flows/<flow-identity>"  # illustrative workspace-relative id
flow = workspace.flow(flow_identity)
result = flow.run(inputs={"username": "qa"}, env="test")

# Equivalent functional form:
result = run_flow(workspace, flow_identity, inputs={"username": "qa"}, env="test")
```

The async counterparts are `await flow.run_async(...)` and
`await run_flow_async(workspace, flow_identity, ...)`; importing
`run_flow_async` from `napflow.core` keeps the async path explicit rather than
nesting an event loop inside `run_flow`.

- A Workspace is a reusable source/configuration boundary, never a mutable run
  session. A Flow handle binds only that workspace plus a canonical identity;
  every sync or async run repeats the preparation gate and creates fresh frames,
  HTTP sessions, workers, variables, cookies, event lifecycle, and cleanup.
- `workspace.discover()` is an explicit fresh filesystem operation and yields
  an immutable tuple of runnable Flow handles. `workspace.flow(identity)` is
  the universal exact lookup. A runtime `workspace.flows` catalog maps the flow
  identity segments below the configured flows root onto nested attributes:
  conceptually, `workspace.flows.<identity segments>`. Attribute access applies
  only to exact Python-identifier segments; bracket or explicit identity lookup
  covers spaces, punctuation, reserved members, and every other legal identity.
  Catalog bracket keys are always relative to the configured flows root;
  `workspace.flow(...)` always takes the full workspace-relative identity, so a
  legal first segment equal to the root name is never ambiguous. Names are never
  silently normalized. A directory may be both a runnable flow and a namespace
  containing child flows: the returned Flow remains runnable and also exposes
  exact child lookup. Catalog objects perform fresh discovery when accessed, so
  a previously obtained catalog observes newly added flow folders.
- The public wrapper owns LOAD/CHECK/ENV/BIND, history/event setup, execution,
  and cleanup without importing CLI/server/UI. Inputs, environment/profile
  choices, overrides, deadlines, and history policy are per-run keyword options,
  so one loaded workspace/flow can safely serve independent test cases. The
  concrete v0.2 keywords are `inputs`, profile name `env`, string-to-string
  `env_overrides`, deadline `timeout`, and `history` (default `True`; `False`
  suppresses durable JSONL history). Preparation failures raise `RunPrepError`;
  a started run returns `RunResult` with the ordinary
  `passed|failed|error|aborted` state contract.
- Runtime discovery/`__dir__` may offer interactive completion but cannot make a
  type checker infer filesystem contents. Deterministic generated bindings for
  flow names and typed Start/End shapes are explicitly post-v0.2; the generic
  runtime always retains exact string lookup.

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

Completed child frames are not retained as runtime history. At quiescence,
the engine emits one canonical `frame_finished` event, rolls the child's
subtree outcome counts into its parent, and releases the child `Frame`.
Only active children remain attached to a parent. Replay reconstructs the
completed tree from events rather than live Python objects (D36/NFR-14).

**Run** — the whole execution tree rooted at one entry flow + env profile
+ bound inputs. Owns: `run.id`, the shared niquests `AsyncSession`, the
message budget, and the event sink(s). Run-level scalar outcomes remain
bounded; CLI report adapters re-read the closed durable log instead of
retaining every event in the engine or adapter.

## 2. Run lifecycle

```
LOAD      parse flow.yaml(s) + manifest (loader)
CHECK     full `napf check` rule set on the closure of referenced flows
PROFILE   select/parse the env profile and layer process env (run preparation)
START     open history/event sinks and emit run_started
ENV       env.required present in the selected layered env? fail this run
BIND      render defaults, validate, and type-coerce root Start inputs
DEADLINE  arm the optional cooperative execution deadline after ENV/BIND
EXECUTE   seed Start node, pump messages until quiescent
CLEANUP   cancel/drain firing tasks; close HTTP sessions and workers
FINALIZE  collect End ports, emit run_finished, close the event stream
```

`FlowRun.execute()` owns every runtime resource, including the event
stream, once execution starts. Normal completion, user abort, run timeout,
server shutdown, and external coroutine cancellation share one `finally`
cleanup path. External cancellation is not relabelled as user abort: its
`CancelledError` escapes only after cleanup, and the closed JSONL may be a
valid incomplete prefix (EC20) without `run_finished`. CLI report rendering
is an adapter step after a completed `RunResult`, not an engine-owned file.
The run stays protected by its internal active-history marker until that
adapter step finishes; only then is it published complete and whole-unit
retention applied. A failed/interrupted adapter closeout publishes an
incomplete marker instead, so retention never guesses that the unit is safe
to delete. Event-stream close attempts every sink and remembers the first
failure; engine cleanup contains an ordinary `Exception` so it cannot replace
a completed run result. Control-flow `BaseException`s still propagate after
cleanup. CLI/server adapters observe an ordinary failure on their idempotent
close, remove the active marker, publish `.incomplete`, and never publish the
unit complete. The CLI also skips report generation while preserving its
result/exit/stdout contract; server finished status preserves the result.
A fresh control-flow exception during the adapter reclose is delayed only long
enough to abandon the history unit, then re-raised (CLI `KeyboardInterrupt` is
mapped to exit 130).
Server
REST/WebSocket readers publish filesystem-visible,
cross-process reader leases; directory-locked retention skips those units, so
a newer server or CLI completion cannot remove an older run while it is being
replayed or caught up.

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

Pins made at S2/M3 (2026-07-05, `core/engine.py`):
- **`error_reason` vocabulary** (state `error`): `bind_error` |
  `env_missing` | `unsupported_node_type` | `budget_exhausted` |
  `run_timeout` | `internal_error`. BIND/ENV failures still emit
  `run_started` + `run_finished` — every run leaves a JSONL.
- **`unhandled_errors` entry shape** (report + `run_finished`):
  `{frame, node, port, kind, message}`. Required-End misses are
  recorded here as `kind: required_end_unwritten` (D18); fatal reasons
  append one entry too, with the budget's hot edge in `port`.
- **assert.failed unconnected** records an `unhandled_error_port` entry
  *in addition to* the failed-assert tally — the letter of §2's
  "error/failed port with no edge" rule; one logical failure may thus
  appear in both categories.
- **`op: present` on an undefined path is a failed check, not a node
  error** (actual recorded as null) — "is it there?" is the question
  the op asks. Other ops raise node errors on evaluation failure.
- **assert `check` label** in events: the kind name for
  status/response_time, the expression text for expr checks.
- **`nodes_never_fired`** excludes `end` (never fires by design, §4
  rule 5) and `note`; `start` counts as fired via seeding.
- **`run_started.inputs`** = caller-supplied bindings, pre-coercion
  (BIND may still fail; effective inputs appear as `start.out`).
- **`message_emitted.value` (v0.2/M4):** the complete logical message value.
  Store-backed streams keep it inline or substitute a typed blob reference;
  featureless legacy readers may still consume historical `value_preview`.
- **`delay` runs in S2** — pulled forward from the S3 node set because
  TR-2's sentinel-race tests need an async node. Other S3/S4 node
  types in a flow ⇒ run `error` (`unsupported_node_type`), never a
  crash.

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
    stopping = False
    while not stopping:
        for _ in range(128):            # bounded ready batch (D36)
            delivery = await queue.get()
            if delivery is QUIESCENT:
                check_deadline_before_accepting_quiescence()
                stopping = True
                break
            if delivery is FATAL:
                stopping = True
                break
            node = delivery.edge.target_node
            node.absorb(delivery)       # update slots / merge / guard state
            if node.ready():            # firing rule, see §4
                in_flight += 1          # task begins
                create_task(fire(node, frame))
            dec_in_flight()             # exactly once: delivery consumed
        if stopping:
            break
        await asyncio.sleep(0)          # hot inline queue must yield
        stopping = check_abort_token_and_monotonic_deadline()
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

**Fairness/control checkpoint (D36).** `Queue.get()` does not suspend when
an inline merge/guard cycle keeps the queue non-empty. The pump therefore
handles at most **128** ready deliveries, yields with `asyncio.sleep(0)`,
then checks the abort token and one precomputed `loop.time()` deadline.
It also checks the deadline before accepting `QUIESCENT`, preventing a
just-late final delivery from racing the timeout callback and reporting
`passed`. The timing contract is cooperative: deadline observation is
bounded by one batch plus event-loop scheduling delay, not a hard CPU
preemption guarantee. Synchronous Jinja rendering remains EC35 after v0.2.
Batch boundaries neither tick the message budget nor participate in
quiescence; every consumed delivery retains its existing exactly-once
decrement.

Properties:
- **Quiescence = termination**: run ends when `in_flight == 0`. No global
  ordering, no toposort.
- **Parallelism is free**: two messages to two nodes = two concurrent
  tasks. Long awaits (HTTP, delay) don't block siblings.
- **Budget**: every emitted message ticks the per-run budget
  (`defaults.run.message_budget`, default 100000 — runaway protection,
  not resource accounting; counts run-wide including child frames);
  exhaustion → run `error` with the hot edge identified in the event.
- **Execution deadline (D24)**: optional cooperative execution clock
  (`defaults.run.run_timeout_s`, default null = off; CLI `--timeout`
  overrides). It is armed after LOAD/CHECK/profile selection and the root
  ENV/BIND step, immediately before the pump. Expiry cancels in-flight work
  like an abort but finalizes
  with state `error` (`error_reason: run_timeout`) — the report and
  JSONL are still written, unlike a CI-runner SIGKILL. Bounded dispatch makes
  inline cycles observe it, but synchronous Start-default or node-template
  rendering is not preemptible (EC27/EC35); this is not an end-to-end hard
  deadline over preparation or arbitrary trusted code.
- **Cancellation**: abort flips a token and enqueues `FATAL` to wake an
  empty pump; the batch checkpoint reads the token directly because the
  sentinel may sit behind a hot ready queue. Firing tasks are cancelled
  and drained before HTTP sessions/workers close; user abort finalizes
  `aborted`. External task cancellation follows the same cleanup but
  re-raises `CancelledError` and only promises the valid event prefix.

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

Pins made at S3/M1 (2026-07-06, engine pump dispatch):
- **Rule-2 trigger + snapshot**: the delivery that completes (or later
  overwrites) the slot set is that firing's `trigger`; the firing sees
  a snapshot of slot values taken at decision time — a later overwrite
  re-fires with a new snapshot, it never mutates an in-flight firing.
- **merge `all` emit order**: dict keys follow the edge-declaration
  order of the connected inputs (deterministic per file; don't rely on
  it beyond determinism).
- **merge is instant**: fired inline by the pump — no task, no ceiling
  (an explicit `max_seconds` is accepted and never trips, FS catalog).
- **Absorbed deliveries emit nothing**: a rule-2/`all` slot fill short
  of rendezvous or a `collect` append short of `count` updates state
  only — the edge's `message_emitted` already recorded the arrival.
  `collect` leftovers at quiescence are dropped (the node simply never
  fires again; skipped-node reporting applies if it never fired).

Pins made at S3/M4 (2026-07-06, engine `_deliver_guard`):
- **Guards are instant nodes**, fired inline like merge (rule 4): a
  `reset` delivery is ABSORBED — no firing count, no `node_fired`, no
  output — it pops the frame-local state so the next `in` delivery
  sees a pristine guard (counter back to `count`; timeout clock
  restarted by that delivery).
- **Counter state** = remaining passes (absent key ⇒ `count`);
  check-then-decrement per EC16 — `count: 0` exhausts every message.
  **Timeout state** = the monotonic timestamp of the first `in`
  delivery; `continue` while `elapsed < seconds`, else `expired`
  (elapsed exactly equal ⇒ expired).
- **`guard_tripped` events** fire once per exhausted/expired emission
  (kind = node type, port = the outlet), alongside the ordinary
  pass-through `message_emitted` — D19: tripping is data, not failure.

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
  a fixed set of at most `max_concurrency` workers claiming successive
  indexes from the evaluated list; collects body End dicts to
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
- Pins made at S2/M4 (2026-07-05, `core/httpclient.py` + engine):
  - **Adapter seam (NFR-09)**: `core/httpclient.py` is the only module
    importing niquests, guarded by a test. `http_version` pinning uses
    one cached session per version option (session-level disable flags;
    best-effort where niquests lacks a flag).
  - **Retry** = immediate re-attempt (no backoff in current v0.2 — the config
    surface pinned at M1 has only `max_attempts`), transport failures
    only, never status-based. `retries_total` in `request_finished` =
    attempts performed − 1.
  - **Body decode**: JSON mime → native value; `text/*` and XML/form
    mimes → text; anything else → the binary envelope (FR-207); empty
    body → null. `size_bytes` is the exact received body byte count; a
    binary envelope's base64 display length does not replace wire size.
    **Body encode**: dict/list → JSON; str → UTF-8
    raw with no implicit content-type; other scalars →
    `stringify_native`; an inbound envelope sends its decoded bytes
    (its `content_type` applies when no header is set). The envelope is
    strict: exactly `__binary__`, `content_type`, and `base64`; non-empty
    string content type; canonical validated standard base64. A malformed
    envelope fails once as `request_encoding` through the request error
    port and is not retried (EC48).
  - **Header/query values stringify post-render** (D25 Scalar pin) —
    `n: 3` arrives as `"3"`.
  - **Prepared/full-fidelity observation (v0.2/M4):** niquests' pre-request
    hook snapshots the effective method, encoded URL/query, library/session
    headers and cookies, exact body/no-body distinction, and byte size before
    I/O. A redirect-aware final snapshot is attached to finish/failure.
    Request output and history share one complete response object containing
    status, headers, decoded body, size, URL/version, elapsed/timing, attempt,
    retry count, and redirect count. Persistence never changes that runtime
    value and no request/run capture valve remains.
  - **Timing** best-effort per §7: `total_ms` always; dns/connect/tls/
    ttfb only where `conn_info` exposes the latency.
- **set/get** — frame variable map; set forwards written value.
- **fixture** — file read at first fire, cached per run; json/csv
  (csv → list of dicts, header row required).
- **log** — emits a raw canonical `LogEvent`; forwards unchanged. CLI
  presentation uses the D35 redacted view.
- Pins made at S3/M5 (2026-07-06, hierarchical frames — engine
  `_run_flow_node` / `_run_loop` / `_spawn_frame`):
  - **Frame completion**: one pump, one budget, one run-level
    QUIESCENT; each frame ALSO counts its own `in_flight`, and the
    frame's last decrement sets its `done` event — the container node
    awaiting it is still in flight in the parent frame, so the global
    counter cannot reach zero early. Frame ids are paths
    (`parent-id/f-N`, N = a run-wide spawn counter).
  - **flow node outputs**: child End values on derived ports —
    optional-unwritten ports emit null (mirrors FR-502);
    required-unwritten ports emit NOTHING (the child failed per D18,
    recorded with the child's frame id — TR-3 cross-frame). The
    implicit `error` payload is `{state, failed_asserts,
    unhandled_errors}` computed over the child SUBTREE; child
    bind/env failures fire it with `{error_kind: bind_error |
    env_missing}` and no child frame is spawned. A wired error port
    is branching, never absolution — outcomes aggregate regardless
    (D20).
  - **loop outputs**: `results` = End-value dicts of PASSED iterations
    only, item-index-ordered (EC36); `errors` emits ONLY when
    non-empty — entries `{index, state, failed_asserts,
    unhandled_errors}` (or `{index, state: "error", message}` for an
    iteration that could not bind); iterations never scheduled under
    `on_error: stop` appear in neither output. A loop-node failure
    (non-list `over`, load failure, tripped explicit ceiling) emits
    nothing: unhandled node error (EC24), in-flight iteration
    subtrees cancelled.
  - **fresh_session**: each iteration gets its own HTTP session,
    closed when the iteration completes (cancellation leftovers close
    at run cleanup); the default shares the run session — cookies
    persist across iterations by design.
  - **Child flow loading**: referenced flows load once per run
    (cached by reference); runtime recursion is caught by the message
    budget — E007 in the checker is the real gate, and `napf run`'s
    LOAD/CHECK gate now covers the entry flow's whole reference
    closure (`check_run_closure`).
  - **Worker pool bound**: one worker per distinct nodes.py in the
    run's flow closure — statically bounded by the E007 DAG, no
    eviction (module state persists for the run). Replaces the
    deferred "capped pool" note in §5a.
  - **`nodes_never_fired`** reports the ROOT frame only; child-frame
    skips are visible in the event stream per frame.
- v0.2 M3 bounded-frame pins (2026-07-13, engine `_run_loop` /
  `_finish_child`):
  - A parallel loop creates `min(max_concurrency, len(items))` helper
    workers for the whole firing, never one task per item. A shared cursor
    claim has no await and is serialized by the run's event loop; results
    and error entries retain their existing item-index order.
  - A normally quiescent flow/loop child emits `frame_finished` before its
    container output messages, rolls subtree assert/error counts into the
    parent, and is detached. The event carries child/parent identity,
    invoking node and kind, target flow, nullable loop index, duration,
    subtree state/asserts/errors, and End outputs.
  - Cancelled children are not compacted through this path: queued
    deliveries may still reference them, and abort must not synthesize a
    D18 required-End failure. They remain bounded by active concurrency and
    are released with the run lifecycle. No durable completion summary is
    invented for them, so M5 drilldown intentionally covers completed frames.
- Pins made at S3/M3 (2026-07-06, engine `_run_node` / `_load_fixture`
  / `_seed`):
  - **switch**: the evaluated value is compared to each case `equals`
    by native equality; the FIRST matching case wins; no match →
    `default`. Eval errors are unhandled node errors (EC24).
  - **set/get**: `set.value` renders recursively (D25 native rule) and
    the WRITTEN value forwards on `out`. `get` of a never-set variable
    is a node error (`variable_unset`), never a silent null — the EC19
    corollary: a Get racing its Set is a missing wire, not an empty
    value.
  - **fixture**: `file`/`format` are literal in current v0.2 (not templatable);
    format infers only from `.json`/`.csv` — any other extension needs
    `format:`. The cache is per RUN, keyed by resolved path (a mid-run
    file change or deletion cannot split the data). CSV values stay
    strings (no type inference); short rows fill missing fields with
    null; a row longer than the header is an error. All failures =
    `fixture_error` via the EC24 path. Paths resolve against the
    workspace root (fallback: the flow dir outside a selected workspace).
    With a workspace, the central resolver rejects lexical traversal,
    drive/backslash forms, and resolved symlink escape before any read;
    checker/run preparation reports stable `workspace_boundary` (D37).
  - **fixture auto-seed (rule 6, D17)**: goes through the normal
    firing path with a synthetic trigger (`value: null`,
    `produced_by: "__seed__"`) — `max_seconds` and error handling
    apply as usual, and the seed keeps `in_flight` non-zero so EC08
    cannot finalize a fixture-driven flow early.
  - **log in the CLI**: `napf run` echoes `log` events live to stderr
    (`[level] label: value`, schema-aware declared-secret redaction) — log
    nodes and worker
    stdout/stderr are visible as the run executes, not just in the
    JSONL.

## 5a. Python worker subprocess (v0.2 lifecycle)

One worker process per flow module, spawned lazily at first use. Normal
cleanup sends EOF and lets an idle worker exit cleanly; in-flight timeout,
cancellation, and protocol-failure teardown immediately terminates, waits
one 2-second grace interval, then hard-kills if needed. A replacement is
not spawned until the former process has been reaped (D36/EC43).

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
  distinct module in the statically finite E007 flow-reference closure,
  spawned lazily on first use; there is no eviction.
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
- **Descendant processes (EC22, OPEN after v0.2)**: killing the worker does not reap
  subprocesses the user's python spawned (`subprocess.Popen` in
  `nodes.py`; only the direct worker is owned and reaped. Future closure needs
  an owned POSIX process group and Windows Job Object/equivalent plus
  child-and-grandchild timeout/abort/shutdown tests. Documentation alone does
  not close this limitation.
- **Interpreter**: `python.interpreter` in napflow.yaml; default = the
  interpreter running napflow. Pointing it at a project venv makes that
  environment's third-party packages available to `nodes.py` — the
  stdlib-only rule is therefore lifted to "whatever the configured
  interpreter provides".
- Values crossing the pipe must be JSON-serializable — same constraint as
  the wire format, so nothing new for users.
- **Protocol record ceiling (D32/D36)**: each UTF-8 JSON record is limited
  to **16 MiB**, excluding the newline. Both parent subprocess readers are
  configured to that value (rather than asyncio's 64 KiB default), so the
  measured 70 KiB and 10 MiB replies and long raw-stderr lines complete.
  The stdlib-only child converts an ordinary oversize task reply into a
  compact `python_error` (`error_type: WorkerProtocolError`); malformed or
  non-cooperating oversize child output becomes a stable `worker_crash`.
  Reader failures terminate/reap the child and resolve pending calls; they
  never escape from reader tasks or hang finalization.
- **Callable surface (EC48)**: functions are synchronous and invoked with
  keyword arguments. `async def` and signatures with positional-only
  parameters are unsupported; the AST checker reports positioned E008
  diagnostics and exposes no misleading port surface, while the worker
  enforces the same rule for callers that bypass run preparation.
- Windows notes: spawn semantics, `CREATE_NO_WINDOW`, `terminate()`
  reliable. asyncio subprocess pipes require the **Proactor** event
  loop: `napf run` uses Python's default (Proactor since 3.8), but the
  `napf ui` server stack must not switch the policy to Selector (some
  ASGI/WebSocket setups do) — a Windows integration test runs a
  python-node flow *through the server* to lock this in (EC33, TR-9).

Pins made at S3/M2 (2026-07-06, `core/worker.py` + `core/worker_main.py`
+ engine `_run_python`):
- **Protocol extensions** beyond the sketch above: a `{"ready": true}`
  handshake after nodes.py imports; `{"fatal", "traceback"}` then exit 1
  on import failure (surfaces as a `worker_crash` node error);
  `{"stream": "stdout"|"stderr", "text"}` messages carry captured user
  output (stdout → `log` level `info`, stderr → `warn`; `node` = the
  firing's node, `label` = `python:<node>`); error replies carry
  `error_kind` (`python_assert` | `python_error`) and `error_type`.
- **Beyond `print()` (EC28)**: after the dup, fd 1 is pointed at fd 2 —
  raw-fd writers (C extensions, user subprocesses) land in the stderr
  pipe, forwarded as `warn` log events; a 15-line stderr tail is kept
  for crash messages. Stream lines cap at 8192 chars.
- **Return convention (FR-506)**: the function returns a dict keyed by
  declared `outputs` — every declared key required (missing ⇒
  `python_error`), extra keys ignored, `outputs: []` discards the
  return. A python-assert = `asserts_failed` increment + `assert_result`
  event (`op: "python-assert"`, junit picks it up) + `python_error`
  event + error-port payload `{error_kind: "python_assert", message,
  traceback, function}`.
- **Worker env & cwd**: the subprocess inherits the parent's process
  env untouched — the active profile is NOT injected (functions see
  only declared inputs). cwd = workspace root (fallback: the flow dir).
- **Interpreter resolution (FR-108)**: `null` → the interpreter running
  napflow; a relative multi-part path resolves against the workspace
  root; a bare name resolves through PATH; spawn failure = a
  `worker_crash` node error, not a run error.
- **Queued firings on worker death**: a task not yet written to the
  pipe retries once on a fresh worker (the lazy-respawn path for
  firings queued behind a killed task); a task in flight fails as
  `worker_crash`. Cancelling a queued firing before it acquires the
  module's serial lock does not terminate the unrelated in-flight call;
  only a cancellation after the task can cross the pipe tears down the
  process.
- **Timeout payloads now carry `max_seconds`** (the FS D24 shape) —
  applied to request timeouts too.
- **Pool bound**: one persistent worker per distinct `nodes.py` in the
  statically finite E007 flow-reference closure; no unbounded dynamic
  module spawning or eviction.

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

Pins made at S2/M1 (2026-07-05, `core/templating.py`):
- **"Exactly one `{{ expression }}`" is structural**: the parsed
  template must be a single `{{ }}` output holding one expression.
  Tag-bearing templates (`{% if %}`, `{% for %}`) always string-render,
  even when they yield a single value; nothing is ever literal_eval'd
  (auto-parsing rendered strings was rejected in D25).
- **Mixed content stringifies with Jinja's own semantics** — a dict in
  mixed content renders as its Python repr; pass structured data as a
  single expression instead.
- **Post-eval coercion** (`coerce_value`): string-typed fields
  stringify non-string natives as JSON (`json.dumps`, never repr);
  number/boolean-typed fields accept the string forms env-file values
  arrive in (`"3"`, `"3.5"`, `"true"`/`"false"` case-insensitive —
  WM §2: profile values are literal strings); object/list-typed fields
  reject everything else, including scalars.
- **Undefined never leaks**: native rendering would return the Jinja
  `Undefined` object untouched for a bare `{{ missing }}`; the renderer
  traps it and raises the strict error.

## 7. Event vocabulary (JSONL file ≡ WebSocket stream)

One JSON object per line/frame. Common fields:
`{event, run_id, frame, node, ts, seq}`. Types:

```
run_started      {format, features, flow, env_name, inputs, engine_version}
node_fired       {firing_no}
request_started  {method, url, attempt,
                  request: {method, url, headers, body, size_bytes}}
request_finished {method, url, status, http_version, size_bytes,
                  timing: {dns_ms?, connect_ms?, tls_ms?, ttfb_ms, total_ms},
                  attempt, retries_total, redirects_total,
                  request: {method, url, headers, body, size_bytes},
                  response: {status, headers, body, size_bytes, timing,
                             elapsed_ms, url, http_version, attempt,
                             retries_total, redirects_total}}
request_failed   {method, url, error_kind, message, attempt, will_retry,
                  redirects_total, request?}
message_emitted  {from_port, to_node, to_port, msg_id, value}
assert_result    {check, op, expected, actual, passed}
python_error     {function, error_type, message, traceback}
log              {label, level, value}
guard_tripped    {kind: counter|timeout, port: exhausted|expired}
budget_warning   {remaining}            # at 10% left
capture_warning  {remaining_mb}         # legacy-readable; no longer emitted
frame_finished   {parent_frame, parent_node, flow, kind: flow|loop,
                  loop_index, duration_ms, state,
                  asserts: {passed, failed}, unhandled_errors, end_outputs}
run_finished     {state, duration_ms, asserts: {passed, failed},
                  unhandled_errors, end_outputs,
                  nodes_never_fired: [node_ids],   # "skipped" for UI/report
                  error_reason?}                   # when state=error:
                                                   # budget_exhausted |
                                                   # run_timeout | ...
```

Rules:

> **v0.2/M4 current:** every schema-declared content path uses the same
> store-once policy, including prepared requests, complete HTTP responses,
> messages, Log/error values, and child/root End outputs. The old previews,
> destructive body/run valves, and new `capture_warning` emission are removed
> (EC32/EC50). The legacy event type remains readable under v0.x best effort.

- **Raw local truth + redacted presentation (D35)**: `EventStream` stamps one
  raw canonical record and sends it unchanged to JSONL and the local
  WebSocket. Run directories and JSONL files use the ordinary permissions
  inherited from the user's OS/workspace: POSIX creation modes are filtered by
  umask and Windows ACLs inherit normally. Napflow applies no custom DACL,
  owner validation, chmod, or permission migration. Terminal stderr receives a
  separate declared-secret redacted projection; JSON/JUnit
  reports apply the same redactor while streaming the closed raw JSONL.
  Functional End-output stdout remains raw. Secret NAME globs match
  case-sensitively and matching values of
  at least five characters are replaced longest-first with `***` only inside
  schema-classified content values. Dictionary keys, event/field names,
  identifiers, enums, state/error vocabulary, control metadata, and error
  record structure are never rewritten. Unknown fields fail closed when a
  redacted view is requested. Runtime-acquired tokens remain EC10.
- `message_emitted.value` is complete. Prepared requests and HTTP responses
  are each classified as one logical aggregate: storing the response object as
  a unit lets the identical value on request/message/Log/End paths resolve to
  one blob/hash rather than nested duplicate body blobs. Cheap status, URL,
  size, timing, attempt, retry, and redirect summaries remain structural on
  the request event so history lists need not fetch the aggregate.
- `request_started.request` is the effective initial prepared request.
  `request_finished.request` is the final redirect-aware request;
  `request_failed.request`, when preparation occurred, is the last effective
  request. Thus a redirect may legitimately make start and finish URLs differ.
- Feature-aware consumers call the schema-gated resolver only when they need a
  content field. Reports skip unrelated events (and therefore unrelated
  missing blobs); paged REST and finished WebSocket replay pass canonical
  descriptors unchanged. A historical browser row resolves only its selected
  canonical record on expansion, surfacing missing/corrupt/omitted content.
- Timing fields included where niquests exposes them, else omitted.
- On abort, an in-flight request leaves a `request_started` with no
  matching `request_finished`; replay tolerates a dangling start (EC20).
- `frame_finished` is the canonical completion fact for a child frame,
  emitted before runtime-only state is released. Its common `frame` field is
  the child id; `parent_frame` and `parent_node` identify the invocation.
  `asserts` and `unhandled_errors` cover the completed subtree, while
  `end_outputs` is that frame's own End interface. Additive event kinds are
  safe for older v0.x readers to retain/display generically and do not
  activate a storage feature flag (D33).
- UI replay = re-read the JSONL through bounded pages; it never re-executes.

Pins made at S2/M2 (2026-07-05, `core/events.py`):
- **Run id** = `YYYYmmdd-HHMMSS-xxxxxx` (UTC + 6 hex): Windows-safe
  (no `:`) and suitable as the JSONL filename stem. The random suffix
  prevents collisions but is not a same-second chronology claim; internal
  lifecycle metadata records wall timestamps plus a locked monotonic
  per-flow creation order. The sink
  opens with `x` — a run id collision fails loudly, never overwrites.
- **Stamping**: `seq` starts at 1; `ts` is UTC, millisecond precision,
  `Z` suffix (the EN §1 message format). Optional fields — declared
  with default None, incl. `frame`/`node` — are omitted when unset;
  required nullable fields (run_started `env_name`) appear as null.
- **JSONL profile**: compact separators, `ensure_ascii=False`, UTF-8,
  LF; every line flushed as written (abort leaves a replayable prefix).
  Since v0.2/M4 the file is created exclusively from its first write, so a run
  id collision never overwrites history. Missing directory components and the
  file use ordinary OS/workspace creation permissions (POSIX umask; inherited
  Windows ACLs), with no napflow-specific owner or private-mode contract.
- **Field policy + redaction (v0.2/M4)**: every event dataclass field is
  exhaustively classified as structure, complete content, keyed content,
  error-message content, prepared request, or HTTP response. Import fails if the registry and
  vocabulary diverge. The same registry is the boundary for presentation
  redaction now and content-store substitution later; values are replaced
  longest-first, dictionary/map keys are always preserved, and only
  `unhandled_errors[*].message` is redacted inside structural error records.

## 7a. Run-history format contract (v0.2 — FR-1101, D34)

This section pins the current run-history on-disk format. The base marker
landed in M0 and `content-blobs/1` activated in M4 only after the byte codec,
immutable per-run store, exhaustive event policy, full-value schemas,
shared JSONL/WebSocket encoding, and lazy consumer resolver were present
together. Production `napflow-run/1` logs now declare
`features: ["content-blobs/1"]`; deliberately ephemeral EventStreams remain
featureless and inline. M5's paged REST/browser readers do not change the
canonical JSONL or descriptor format below.

**Envelope + version.** `run_started` is the envelope header: it is
always `seq` 1 and carries `format: "napflow-run/<major>"`
(`HISTORY_FORMAT`) plus `features: ["<name>/<version>", ...]`.
`event`, `seq`, `format`, and `features` are protocol structure, not
payload, and secret redaction must never rewrite them. A reader reads the
first non-blank record and validates the envelope before interpreting any
later record:

- a non-empty log whose first record is not `run_started` at `seq: 1`, or
  whose `format` is malformed, is invalid and fails clearly;
- equal or older major plus a supported feature set ⇒ readable (major 0 =
  a pre-versioning v0.1 log with neither envelope field, read best-effort
  per D33); explicitly present null/malformed fields are invalid;
- newer major ⇒ **refuse or open metadata-only** — never silently
  misparse (`is_supported` / `parse_history_format`, `HistoryFormatError`);
- an unknown feature ⇒ refuse or open metadata-only. Additive storage
  capabilities use this gate so an older same-major reader cannot expose a
  raw descriptor it does not understand;
- the major bumps on a breaking change to the base event/envelope rules;
  a feature's version changes when that capability's shape/semantics break.

M4's writer and reader support `content-blobs/1` together with hash
verification, literal escaping, omission errors, and lazy value resolution.
Unknown features are still refused. Without the declared feature, `$napflow`
inside an inline value remains ordinary user data; readers never guess
capabilities by scanning arbitrary payloads. Short-lived M0
`napflow-run/1` logs are read with a missing `features` field interpreted as
`[]`; an explicitly present null is invalid.

An empty prefix is valid only while a live run has not flushed its header.
It is not a readable completed history.

**Canonical ordering.** The append-only JSONL is the source of truth for
event order. `seq` is a total order starting at 1; a consumer sorts and
seeks by `seq`, never by `ts` (clocks are for display/scrubbing, not
ordering). Replay is re-reading — never re-execution (D13).

**Schema-declared field policy.** The common envelope plus every field not
named below is structural and copied exactly. Content-map keys (input/End
ports) are structural; only their values are content. `unhandled_errors`
keeps record keys/IDs/kinds structural and classifies only each `message` as
content. Prepared-request and HTTP-response objects are one logical content
value for persistence, with exact nested schemas for fail-closed presentation
redaction. This registry is exhaustive against the event dataclasses and is
the only legal persistence/redaction dispatch surface:

| Event | complete/aggregate content | keyed content |
|---|---|---|
| `run_started` | — | `inputs` values |
| `request_started` | `url`, prepared `request` | — |
| `request_finished` | `url`, prepared `request`, full `response` | — |
| `request_failed` | `url`, `message`, optional prepared `request` | — |
| `message_emitted` | `value` | — |
| `assert_result` | `check`, `expected`, `actual` | — |
| `python_error` | `message`, `traceback` | — |
| `log` | `label`, `value` | — |
| `frame_finished` | `unhandled_errors[*].message` | `end_outputs` values |
| `run_finished` | `unhandled_errors[*].message` | `end_outputs` values |

`node_fired`, `guard_tripped`, `budget_warning`, and legacy
`capture_warning` have no content fields. Methods, states, operators, error
kinds/reasons, timing/retry/redirect summaries, and every identifier remain
structural. Any new field must update the dataclass, table, and executable
registry together or import/processing fails closed.

**Persisted-value envelope and collision rule.** Storage substitution is
performed only at schema-declared payload fields (including prepared-request
and full-response aggregates, message/Log/error values, and End outputs),
never by recursively guessing that an arbitrary object inside user data is
protocol. A tagged value requires the declared `content-blobs/1` feature, has one reserved
outer key, `$napflow`, and uses one of these exact descriptor shapes:

```
{"$napflow": {"kind": "blob", "hash": "sha256:<64 lowercase hex>",
              "bytes": <non-negative int>, "media_type": "<mime>",
              "codec": "utf-8"|"json"|"binary"}}

{"$napflow": {"kind": "literal", "value": <original JSON object>}}

{"$napflow": {"kind": "omitted", "hash": "sha256:<64 lowercase hex>",
              "bytes": <non-negative int>, "media_type": "<mime>",
              "codec": "utf-8"|"json"|"binary", "reason": "<code>"}}
```

The outer object must contain **only** `$napflow`, and the descriptor must
match one known shape exactly, before a reader treats it as protocol. An
inline user object containing a top-level `$napflow` key is wrapped as
`kind: literal`; the decoder returns its `value` verbatim and does not
interpret keys inside it. Thus even a user payload that exactly imitates
a blob descriptor round-trips as data. Unknown kinds/keys are format
errors, never silently treated as either a blob or a literal.

**Inline threshold + exact bytes.** The inline threshold is measured over
the bytes that would be stored, before deciding inline versus blob:

- `utf-8`: the string encoded as UTF-8, without Unicode normalization;
- `json`: compact JSON encoded as UTF-8 with `ensure_ascii=False`,
  `allow_nan=False`, separators `(",", ":")`, and mapping iteration order
  preserved; the same profile is decoded with a normal JSON reader;
- `binary`: the exact raw bytes (not their base64 text); replay reconstructs
  napflow's JSON binary envelope at the presentation/runtime boundary.

“JSON-compatible” means the exact logical JSON data model: null, booleans,
finite numbers, strings, lists, and objects with string keys. Round-trip YAML
loader wrappers such as `ScalarInt`, quoted-string subclasses,
`CommentedSeq`, and `CommentedMap` normalize to those corresponding built-in
logical values before hashing; their spelling/comment metadata is not runtime
data. Python-only containers and keys (including tuples and non-string mapping
keys) are rejected before serialization; they are never silently reshaped or
allowed to collapse duplicate stringified keys.

`hash` is `sha256:` over exactly those stored bytes and `bytes` is their
length. A reader verifies both before decoding. Repeated identical stored
bytes within a run resolve to one blob path; logical JSON equality with a
different byte serialization is not promised to deduplicate. The
initial internal threshold is **65,536 bytes**: values of exactly that size
remain inline and only larger values become blobs. It is not a manifest
setting or a correctness boundary; it may be retuned before v0.2's measured
release gate without changing how a reader reconstructs a value. Moving a
value to a blob must not change the runtime value. Default descriptor media
types are `text/plain; charset=utf-8`, `application/json`, or the exact
binary envelope `content_type`; an explicit non-empty media type may override
them. The default binary media type preserves the envelope string exactly.
A binary value is recognized only from the exact three-field napflow
envelope (`__binary__: true`, non-empty `content_type`, canonical base64);
other JSON objects use the JSON codec. Omission `reason` values are stable
lowercase snake-case codes; readers surface even an unfamiliar code rather
than guessing content. An explicit hard-limit omission uses the
`kind: omitted` envelope with the would-be bytes' hash/size and never stores
a plausible-looking prefix. D39 leaves this collision-safe reserved codec in
place but defers a user-facing hard-limit policy until after v0.2.

**Blob layout, durability, and verification.** One run stores blobs at
`<run-id>.blobs/<64-lowercase-sha256-hex>` beside its JSONL. The directory is
created lazily using ordinary OS/workspace permissions. Non-directory,
symlink, and Windows-reparse replacements are rejected; mode bits and inherited
ACLs are not a content-validity condition. A writer creates each digest
exclusively, flushes and fsyncs its
bytes before it may
return a descriptor, and never overwrites an existing digest; an existing
regular file must already contain the identical bytes. Where directory-fd
APIs exist, reads/writes pin a verified blob-directory handle and use relative
no-follow opens; other platforms revalidate the directory identity before and
after access under D37's trusted-local-filesystem race limitation. Readers
require a stable regular file, reject a declared/filesystem size
mismatch before allocating the body, bound the read to that declared size,
then verify SHA-256 before UTF-8/JSON/binary decode. Expected filesystem and
codec failures remain in the typed content-error family. Missing, malformed,
omitted, and corrupt content are distinct explicit errors, never partial
fallbacks. A crash may leave an unreachable partial blob before any descriptor
was appended; it is non-authoritative run-unit debris and is removed with that
whole unit rather than inferred as history.

**Resolution and report consumers.** `persist_record_content(record, store)`
and `resolve_record_content(record, features, store)` are the core
schema-aware boundaries. Featureless records are copied without interpreting
marker-shaped user data; unknown features fail before content inspection.
JSON/JUnit reporting first gates the header and then resolves only event kinds
it renders. JSON reports for blob-aware runs carry the same `format`/`features`
metadata and re-persist the redacted final summary through the run store, so an
unchanged large End value retains the canonical descriptor/hash instead of
being duplicated inline. JUnit materializes only rendered assertion/error
values. An unrelated missing request blob therefore does not block a report
that never reads it.

**Paged browser readers.** The versioned `napflow-replay/1` adapter reads the
canonical file in `seq` order and returns at most 500 selected records per
response (200 by default). `after_seq` is an exclusive canonical cursor and
`next_after_seq` is the last record returned. The first later match sets
`has_more`; the reader then validates the rest of its frozen sequence snapshot
without retaining it. An optional exact-frame filter keeps root and child
event loading independent; root pages also carry the frame-less run envelope
records. Beside the page, `view_summary` is a disposable full-snapshot,
frame-scoped projection of node/edge counts, latest port/request state, and a
fixed per-node Log ring. It contains no canonical record array or run outputs,
so the canvas stays accurate without transferring the other event pages.
Direct-child frame pages, requested recursively by `parent_frame`, project
`frame_finished` into navigation/scalar fields, error counts, and End-port
names. Full errors and End values remain exclusively in the canonical event
behind its sequence-detail request. No offset index is required: rescanning is
an accepted v0.2 prototype tradeoff, while disposable indexes remain available
for a later measured need.

Every replay record must carry the next exact positive integer `seq`; a
missing, boolean, duplicate, gapped, or regressing sequence is a
`history_format` error rather than an ambiguous cursor. A malformed final
partial line remains an EC20-tolerated tail, but malformed data before a later
nonblank record is an error.

Before a scan, the adapter captures the last valid sequence plus its lifecycle
classification and reads only through that boundary. The page, full-history
projection, frame summaries, and `history_state` therefore describe one
snapshot even if another local process appends while the request is reading.
Invalid UTF-8 anywhere inside that snapshot is `history_format`, never a 500.

Each page reports `run_format`, declared `features`, `root_frame`,
`history_state`, and a bounded `run_summary` separately from the page cursor.
`run_finished` remains the only durable completion fact; `run_summary` is its
scalar state/duration/assert/error-count/skipped-count projection and never
copies End outputs or error bodies. A known server-owned live run is
`running`; an exact regular `.active` companion owned by another process is
`indeterminate`; a known-closed or markerless legacy prefix without the final
fact is `incomplete` (EC20), including when the final valid record itself
exceeds 64 KiB. Event pages never resolve descriptors. `GET .../events/{seq}` selects
one canonical record, applies `resolve_record_content`, verifies referenced
bytes, and either returns the complete event or an explicit typed content
error. The browser opens the root or a selected child with exactly one bounded
event page plus one direct-child summary page; explicit cursor navigation
loads one further page at a time. It retains only the active event/frame pages
and the bounded `view_summary`, never an expanding record array or frame tree.
Live WebSocket folding retains its separate 2,000-record tail window. Selecting
a completed child fetches its current flow definition only to draw the locked
canvas; if that source was moved or deleted, durable event/descendant replay
remains available with a localized canvas error. Only completed children have
`frame_finished` facts; cancelled/aborted active children are not invented as
drillable frames. Runtime frame compaction is therefore invisible to
completed-frame replay.

**Retention unit.** A run is one atomic retention unit: its JSONL, blobs,
indexes, and reports are created and deleted together. Retention operates
on completed runs only and never touches an active or known-incomplete run.
M3 uses internal exact-stem companions beside the JSONL:

- `<run-id>.active` records `started_ns` plus the internal creation `order`
  before the first event and protects execution plus adapter-owned report
  finalization;
- `<run-id>.complete.json` records `started_ns`, `completed_ns`, and that
  `order` only after a matching final `run_finished` record and report
  closeout;
- `<run-id>.incomplete` preserves a known-closed EC20 prefix without counting
  it against `defaults.run.history`; a hard-crash `.active` is likewise
  protected conservatively until an operator or later recovery policy
  resolves it;
- `<run-id>.deleting` is exclusive, fsynced claim metadata written before any
  companion removal. The next retention pass resumes exact-unit cleanup,
  deleting report/blob/index companions before the canonical JSONL and
  tombstone.
- `<run-id>.reader-<pid>-<token>` is an exclusive, fsynced replay lease.
  Retention and reader creation share the no-follow `.history.lock`, so every
  unit is either leased or deletion-claimed, never both; normal readers remove
  their lease and the server immediately re-applies retention, while a
  timed-out WebSocket keeps it through the reconnect window. A crash may leave
  a conservative stale lease for later recovery.

The per-flow no-follow, regular-file-validated `.history.lock` rejects an
existing non-regular path before any open, then revalidates the opened/current
regular-file identity after open. Together with the atomically replaced
`.history-order.json`, it allocates order and coordinates readers/deleters
across local processes; allocation recovers from surviving unit markers and
is immune to equal/backward wall clocks. Retention runs
after completion, revalidates the canonical JSONL's matching final record,
orders published units by that internal monotonic value, and deletes only exact
allowlisted companions. A
markerless legacy JSONL is eligible only when a robust backward record scan
finds `run_finished`; its filesystem modification time supplies best-effort
chronology. The backward reader scans fixed-size blocks without a fixed tail
window, tolerates malformed/partial trailing data, and retains at most one
arbitrarily large record. These lifecycle companions are internal housekeeping,
not authoritative replay content or a substitute for the versioned
`napflow-run` envelope.

**Disposable indexes.** Byte-offset / `seq` / frame / node indexes and
cached per-frame summary tables are DERIVED, rebuildable acceleration
structures. They may be deleted at any time and regenerated from the JSONL
and blobs; they are never authoritative and never a substitute for source
events, including canonical `frame_finished` records.

## 8. `napf check` rules (current experimental v0.x)

```
E001 yaml parse / schema validation failure
E002 unknown node type / unknown config keys
E003 edge references missing node or port
E004 multiple edges into one input port
E005 missing required input port connection
E006 exactly-one start / exactly-one end violated
E007 flow-reference cycle (with path)
E008 broken flow/fixture/python reference or unsupported python callable shape
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

Rule-scope pins made at M4 (2026-07-04, `core/checker.py`):
- **E005 includes required End ports**: a `required: true` End port with
  no inbound edge is a statically guaranteed run failure (D18) — check
  error, not a runtime surprise.
- **E008 scope**: missing/unparseable `nodes.py`, function not found
  (EC14), or an async/positional-only function the worker cannot invoke
  (EC48); loop body whose Start declares no `item` port; templated
  (non-static) flow/loop references — the reference DAG must be static.
- **Implicit input port names**: single-input nodes (`condition`,
  `switch`, `assert`, `set`, `delay`, `log`, guards) use `in`;
  `request`/`loop`/`get`/`fixture` use `trigger`; merge inputs match
  `in[1-9][0-9]*` (1-based, no upper bound).
- **W105 also reports an unparseable env profile** (its keys cannot be
  checked; still warning-class — profiles are local files).
- **W107 under YAML 1.2 reality**: number-like and date-like plain
  scalars parse as ints/dates, never strings — date-typed values are
  warned as parsed-date objects; the string-form lint fires on the
  bool/null word set and sexagesimals.

## 9. Resolved (was: open questions)

- **Body/content capture (resolved v0.2/M4):** the historical
  10MB-per-body and 500MB-per-run valves, preview fields, and truncation
  wrappers are removed. All persisted content paths use D34's store-once
  full-fidelity descriptors while runtime values stay complete (EC32,
  FR-1102/TR-16).
- **Per-node execution timeout: IN v0.1**, enforced via the worker
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

## 10. Roadmap notes (post-v0.2 candidates)

- `poll` node (sugar over merge/condition/counter/delay).
- **`duplicate` node** — split a message into N identical parallel
  copies without a body flow. Largely covered today by output fan-out
  (concurrent by construction) and parallel loop over a range; add only
  if real flows show the workaround is clumsy.
- Inline loop bodies.
- Per-module python worker pool (lifts the serial-worker limitation, §5a).
- Fine-grained runtime-secret registration/field-path rules for optional
  presentation and any future safe-export feature (D35/D39).
- Descendant-process cleanup through POSIX process groups / Windows Job
  Objects or equivalent (EC22).
- Preemptible synchronous template execution, or an explicitly
  cooperative trusted-code deadline contract backed by tests
  (EC27/EC35).
- Deterministic typed workspace bindings generated from discovered flow names
  and Start/End interfaces, with a stale-binding CI check. Runtime catalog
  attributes remain dynamic and do not require an editor-specific type-checker
  plugin (D38).

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
  code. **The v0.2 execution deadline is not a hard backstop for preparation
  or synchronous rendering**; it is armed only after root ENV/BIND and cannot
  preempt a synchronous Jinja call. EC27's cooperative-scheduler half is
  tested, while EC27/EC35's hard-deadline limitation remains open until the
  post-v0.2 decision above.
- **Workspace path containment is centralized (v0.2/M1, D37).** Entry,
  reference, fixture, source, history, and clone paths pass through one
  lexical + symlink-aware resolver and must remain below the selected
  canonical workspace (clone destinations also remain below `flows.root`).
  Boundary failures use stable `workspace_boundary`; run IDs use the
  pinned Windows-safe grammar. This prevents accidental/identity-driven
  escape, but the trusted-workspace model is not an OS sandbox against a
  separate malicious local process racing filesystem entries.
- **The server binds localhost only.** `napf ui` serves on `127.0.0.1`
  with no authentication or public bind mode. v0.2/M1 additionally requires
  one loopback Host on every request and a matching browser Origin on
  mutations/WebSockets (programmatic loopback clients may omit Origin).
  Remote/multi-user operation is out of scope.
- **Secrets (v0.2/M4 current)**: canonical local JSONL/WebSocket records are
  raw; JSONL and blobs use ordinary OS/workspace permissions, and the loopback
  UI is a trusted local inspection surface. Declared-secret terminal/report
  views never rewrite protocol fields or dictionary keys. Runtime-acquired
  tokens remain EC10; there is no v0.2 safe-export or secure-history contract.
