# napflow — Flow File Schema, v0.4

Status: **adopted 2026-07-02** (2026-06-14 edge-case review applied; see
`EDGE_CASES.md` for the ledger and `DECISIONS.md` D18–D23 for rationale).
Amended 2026-07-02: execution-timeout model — universal `max_seconds`,
default-ceiling scope, run deadline (EC25–EC27, **D24**). Amended
2026-07-02 (b), senior review: native-value templating (**D25**),
flagship example corrected (create step added), loop `results` ordering,
Start-default scope, python optional-input rule (EC28–EC37).

Compatibility note (D33, 2026-07-11): `schema: napflow/v1` remains an
**experimental marker throughout the package v0.x series**. Breaking
changes are expected before package v1.0 and v0.x migration support is
best-effort. The marker becomes a stability/migration promise only when
the package reaches v1.0.

Changes from v0.3: End ports gain `required:` (default `true` — an
unreached required port fails the run, **D18**); guard `exhausted`/`expired`
reclassified as ordinary pass-through outputs, not error ports (**D19**);
`flow` node exposes an implicit `error` port and the name `error` is
reserved on End ports and python `outputs` (**D21**, E012); counter
semantics pinned to check-then-decrement (`count: N` = N passes, EC16);
node-id charset pinned (EC21, E011); loop input port + `over` evaluation
and `start.out` payload specified (EC15); templating envelope asymmetry
and `nodes.*` last-writer-wins documented (EC12/EC18); merge `all`
re-entry stall documented (EC03); request non-2xx routing stated (EC13);
Set/Get ordering caveat added (EC19); example updated (exhausted path
wired, deterministic `trigger.value` condition). On-disk YAML follows the
canonical safe profile (**D23**, `yaml-profile.md`).

## Layout on disk

```
my-workspace/
  napflow.yaml
  flows/
    main/                # the "global" canvas — just a flow, opened by default
      flow.yaml
      nodes.py
    login/
      flow.yaml
      nodes.py
  fixtures/              # test data for fixture nodes (committed)
    users.json
  envs/                  # ALL real env files gitignored
    dev.env
    example.env          # committed onboarding template (maintained manually;
                         #   future: `napf check --write-env-example`)
  .napflow/              # run history & cache, gitignored
```

**Every canvas is a flow.** `flows/main` is simply the default entry flow.

## Execution model (message-driven)

- A **message** is a `{value, meta}` envelope traveling along an edge.
- A node **fires** when a message arrives on a connected input port and all
  its other connected input ports have received at least one value in this
  run (latest value wins on re-fire). Single-input nodes fire per message.
- **Edges may form cycles** within a canvas. Safety rule, statically
  checked: *every simple edge-cycle must contain at least one guard node
  (`counter` or `timeout`)* — W101 (checked cheaply: delete guard nodes
  from the graph; any remaining cycle is exactly a guard-free cycle).
- **Subflow references remain a strict DAG** (no recursive flows); this is
  separate from edge cycles and still checked at load/edit time (E007).
- Untriggered branches end the run as `skipped` — a first-class outcome,
  not an error. When a skipped result must fail the run, declare it as a
  **required End port** (D18): silent drops then become `failed`, exit 1.
- The run finishes when no messages are in flight (quiescence).

## Node ids

Node ids match `[A-Za-z_][A-Za-z0-9_]*` — template/path-safe so
`nodes.<id>.<port>` expressions and `from: <id>.<port>` edge endpoints
parse unambiguously. E011 rejects duplicates and charset violations.
Ids are stable and human-readable — never UUIDs.

## Edge rules

- **Input ports accept exactly one edge — no exceptions** (including
  `trigger` and guard `reset` ports). Attaching a new edge in the UI
  disconnects the previous one (E004).
- **Output ports fan out freely** (one output → many inputs).
- Joining paths requires a `merge` node; its `in1..inN` inputs are each
  single-edge too — you add inputs by adding ports, never by stacking
  edges.

## Everything is data

A "signal" is just a message whose value you may ignore. Consequences:

- **Every non-terminal output port carries a payload.** Pass-through
  semantics: `condition`/`switch` forward the incoming message on the
  taken branch; `assert` forwards it on `passed`/`failed`; `set` forwards
  the written value; `counter`/`timeout` forward the triggering message on
  `continue`/`exhausted`/`expired`; `delay`/`log` forward unchanged.
  Nothing emits an empty pulse.
- Therefore **any output can connect to any input, including `trigger`**.
- **Trigger payloads are usable:** the message arriving on a Request's
  `trigger` is available in its config templates as `{{ trigger }}`
  (e.g. `url: "{{ trigger.value.url }}"`) — fixtures or merges can feed
  items directly into requests without Set/Get detours.
- **Error ports are data ports too.** The success/error separation is
  about *routing* (structural branching, template shape stability, and
  the unconnected-error ⇒ run-failed safety rule), not about data-ness:
  `error` outputs carry a full payload (kind, message, traceback/meta).

## Example `flow.yaml` (with a guarded retry loop)

```yaml
schema: napflow/v1

flow:
  name: create_until_ready
  description: Create a job, then re-check with retries until it's ready.

env:
  required: [API_TOKEN, BASE_URL]

nodes:
  - id: start
    type: start
    config:
      ports:
        - name: base_url
          type: string
          default: "{{ env.BASE_URL }}"

  - id: create
    type: request
    config:
      method: POST
      url: "{{ inputs.base_url }}/api/v1/job"
      headers: { Authorization: "Bearer {{ env.API_TOKEN }}" }
      body: { kind: sync_users }    # YAML mapping → sent as JSON

  - id: kick
    type: merge                     # joins "first run" and "retry" paths
    config: { mode: any }

  - id: check_job
    type: request
    config:
      method: GET
      # ghost-wire: reads create's output (create fires once, so this
      # is stable even inside the retry cycle)
      url: "{{ inputs.base_url }}/api/v1/job/{{ nodes.create.response.body.id }}"
      headers: { Authorization: "Bearer {{ env.API_TOKEN }}" }
      # implicit input port: trigger

  - id: is_ready
    type: condition
    config: { expr: "trigger.value.body.state == 'done'" }

  - id: attempts
    type: counter
    config: { count: 10 }           # allows 10 passes (retries); 11th → exhausted

  - id: wait
    type: delay
    config: { seconds: 2 }

  - id: show
    type: log
    config: { label: "job state", level: info }

  - id: end
    type: end
    config:
      ports:                          # = this flow's outputs; each is a REAL
        - name: job                   #   input port, wired by an edge
        - name: gave_up               #   (required by default, D18)
          required: false

edges:
  - { from: start.out,            to: create.trigger }
  - { from: create.response,      to: kick.in1 }
  - { from: kick.out,             to: check_job.trigger }
  - { from: check_job.response,   to: is_ready.in }
  - { from: check_job.response,   to: show.in }
  - { from: is_ready.false,       to: attempts.in }
  - { from: attempts.continue,    to: wait.in }
  - { from: wait.out,             to: kick.in2 }       # ← the cycle, guarded
  - { from: is_ready.true,        to: end.job }
  - { from: attempts.exhausted,   to: end.gave_up }    # explicit "gave up" output

layout:
  start:       [40, 200]
  create:      [180, 200]
  kick:        [320, 200]
  check_job:   [470, 200]
  is_ready:    [640, 200]
  attempts:    [640, 360]
  wait:        [470, 360]
  show:        [640, 80]
  end:         [820, 200]
```

If the job never reaches `done`, `end.job` is never written and the run is
`failed` (exit 1) — the correct CI outcome — while `gave_up` records why.
Flows that treat exhaustion as success mark `job` `required: false` instead.

## Node type catalog — v1

| type      | purpose | key config | ports |
|-----------|---------|------------|-------|
| `start`   | flow entry; defines flow inputs | `ports` | out: one per port (+ `out`) |
| `end`     | flow exit; defines flow outputs | `ports` (each: `name`, `required`) | in: one per declared port |
| `request` | HTTP call via niquests | method, url, headers, query, body, timeout, TLS, retry, http_version | in: `trigger`; out: `response`, `error` |
| `python`  | run function from `nodes.py` | `function`, `outputs` | in: from signature; out: declared (+ `error`) |
| `assert`  | declarative checks → run report | `checks[]` (`status` / `expr` / `response_time`), `mode` | out: `passed`, `failed` |
| `condition` | if/else branch | `expr` | out: `true`, `false` |
| `switch`  | multi-way branch | `expr`, `cases[]` | out: per case + `default` |
| `loop`    | run a body flow once per item | `over`, `body` (flow path), `mode`, `max_concurrency`, `on_error`, `fresh_session` | in: `trigger`; out: `results`, `errors` |
| `flow`    | run another flow (subflow) | `flow:` path | derived from target Start/End + implicit `error` (D21) |
| `set`     | write run variable (flow-scoped) | `name`, `value` | in, out |
| `get`     | read run variable | `name` | in: `trigger`; out: `value` |
| `merge`   | join paths | `mode: any \| all \| collect`; inputs `in1..inN` | in (1 edge each), out |
| `counter` | cycle guard: limited passes | `count` | in; out: `continue`, `exhausted` |
| `timeout` | cycle guard: deadline gate | `seconds` | in; out: `continue`, `expired` |
| `delay`   | wait before continuing | `seconds` (templatable) | in, out |
| `log`     | show value in UI / stderr | `label`, `level` | in, out (pass-through) |
| `fixture` | load JSON/CSV from `fixtures/` | `file`, `format` | in: `trigger` (optional); out: `value` |
| `note`    | canvas documentation (markdown) | `text` | none |

Catalog notes:
- `start.out` carries the frame's full `inputs` dict as its value — it is
  never an empty pulse, so downstream templates over it are always defined.
- The `loop` node fires on its `trigger` input; `over` is a Jinja2
  expression evaluated against that delivery (e.g. `over: trigger.value`
  or `over: nodes.fetch.list`).

### Config surface pinned at implementation (M1, 2026-07-04)

Details the catalog table left loose, pinned when the Pydantic models
landed (`core/models/`; the models are the schema source of truth):

- **`max_seconds` is a node-level key** — a sibling of `id`/`type`/
  `config`, uniform across all node types (it is engine policy, not
  per-type config). Emit order: `id, type, config, max_seconds`.
- **`request` config keys** (named to match `defaults.request` for the
  shallow merge): `method` (default `GET`), `url` (required), `headers`,
  `query`, `body`, `timeout_s`, `verify_tls`,
  `retry: {max_attempts: ≥1}`, `http_version` (`"1.1" | "2" | "3"`,
  absent = negotiate).
- **`switch.cases`**: list of `{name: <port>, equals: <value>}`;
  output ports = case names + `default`; cases are evaluated
  top-to-bottom, first equality match wins.
- **`merge`**: `count` (≥1) is required with `mode: collect` and
  rejected with `any`/`all`.
- **`loop` defaults**: `mode: sequential`, `on_error: stop`,
  `fresh_session: false`, `max_concurrency: 4` (applies in `parallel`).
- **`log`**: `level: debug | info | warn | error` (default `info`);
  `label` optional.
- **`fixture.format`**: `json | csv`, optional — inferred from the file
  extension when omitted.
- **assert `expr` checks**: `op` defaults to `present`; every other op
  requires `value` (an explicit `value: null` is legal, e.g. for
  `equals`); `present` takes no `value`.
- **Start port `default:`**: an absent key means "input required at
  BIND"; an explicit `default: null` is a null default.

### Guard node semantics
- **counter** — allows exactly `count` passes (check-then-decrement): a
  message arriving while remaining > 0 decrements and routes to
  `continue`; once remaining is 0, every subsequent message routes to
  `exhausted`. `count: 10` ⇒ 10 `continue` emissions; the 11th message
  exhausts. Resets at the start of every run and every subflow/loop-body
  invocation.
- **timeout** — records timestamp of first message; routes `continue`
  while elapsed < `seconds`, else `expired`. Evaluated lazily on message
  arrival (no background timer — a branch sleeping in a `delay` is not
  interrupted; expiry takes effect on the next pass).
- Both have an optional **`reset` input port**: a message there restores
  the counter to `count` / clears the timeout's start timestamp, emitting
  nothing.
- **`exhausted`/`expired` are ordinary pass-through outputs** carrying the
  triggering message — not error ports (D19). Unconnected, their message
  is dropped like any non-error output; W106 lints that ("this loop exit
  produces no output"). Whether a tripped guard is a *failure* is decided
  by what you wire to it: route it to a required End port or an assert
  when "gave up" must fail the run.
- **Unguarded cycles**: not rejected — `napf check` and the canvas flag
  them ("possible infinite loop — no counter/timeout in this cycle").
  Runtime backstop: a per-run message budget (manifest
  `defaults.run.message_budget`, default 100000) aborts runaway runs.

### Execution timeouts (`max_seconds`) — D24

Guards bound *laps around a cycle*; `max_seconds` bounds *one firing*.

- **Every node accepts an optional `max_seconds`** — a hard wall-clock
  ceiling on a single firing, enforced by the engine (worker kill for
  `python`, task cancellation otherwise; engine spec §5a).
- **The manifest default** (`defaults.run.node_timeout_s`, 300) applies
  automatically to `request` and `python` only — the two potentially
  unbounded leaf firings. `delay` is self-bounded by its `seconds`;
  `loop` and `flow` are bounded transitively (every child firing is
  bounded, the message budget caps the rest), so none of the three gets a
  default ceiling — a healthy 10-minute data suite must not be killed by
  a default. An **explicit** `max_seconds` is honored on any node.
  Instant nodes (condition, switch, merge, …) accept the key; it simply
  never trips.
- On a request node, the HTTP `timeout` config (and retries) bounds
  individual transport attempts; `max_seconds` is the hard stop above
  all attempts.
- **A timeout is an error, never data on a success port** — downstream
  shapes stay stable (`response` only ever carries responses):
  - `request`, `python` → `{error_kind: "timeout", message, max_seconds}`
    on the `error` port. Wired = handled (the run can still pass);
    unwired = unhandled ⇒ run `failed`.
  - `flow` → the child frame is cancelled (state `aborted`; asserts and
    errors it already recorded still aggregate per D20) and the implicit
    `error` port (D21) fires with `{state: "aborted", error_kind:
    "timeout", ...}`.
  - `loop` → node-level failures (a tripped explicit `max_seconds`, an
    `over` evaluation error) have no outlet: unhandled node error ⇒ run
    `failed` (EC24), and the loop emits nothing. To *branch* on "suite
    too slow", wrap the loop in a subflow and set `max_seconds` on the
    `flow` node.
  - nodes without an error port (`delay`, guards, `set`/`get`, …) →
    unhandled node error ⇒ run `failed` (EC24).
- Whole-run wall-clock deadline: `defaults.run.run_timeout_s` /
  `napf run --timeout` (off by default; see manifest).

### Request node
- Has an explicit `trigger` input port — it fires when a message arrives
  there; config templating may reference any `{{ nodes.* }}` values
  already produced. Retry config handles transport-level retries; loop
  cycles handle application-level "retry until state X".
- **Non-2xx responses are valid responses** and emit on `response`; the
  `error` port carries transport-level failures only (connection refused,
  DNS, TLS, or timeout after the configured retries). Assert on
  `status` when a non-2xx must fail the run.
- `body:` may be a YAML mapping/list (templated recursively — string
  leaves render, single-expression leaves stay native per D25) or a
  single `{{ expr }}` yielding a whole structured value.

### Python node (v1 constraints)
- Functions see **only their declared inputs** — no implicit access to env,
  variables, or other nodes. Wire or template values in explicitly.
  Keeps functions pure, pytest-able, and codegen-honest.
- **Executed in a persistent worker subprocess** (engine spec §5a):
  crash-isolated, killable, per-node `max_seconds` timeout enforced.
- **Available packages = the configured interpreter's environment**
  (`python.interpreter` in napflow.yaml; default = napflow's own
  interpreter → stdlib guaranteed). Point it at your project venv to use
  third-party packages in `nodes.py`.
- Inputs and outputs must be JSON-serializable (same as the wire format).
- Functions must be synchronous top-level `def`s and may not declare
  positional-only parameters. Python-node inputs are invoked by parameter
  name, so `async def` and the `/` signature marker are positioned E008
  checker errors; the worker rejects both defensively when invoked through
  the standalone engine without the checker gate (EC48).
- **`assert` is supported**: a raised `AssertionError` routes to the
  node's `error` port with the assertion message, and is recorded in the
  run report alongside declarative assert-node results. Any other
  exception also routes to `error` with traceback in `meta`.
- Declared `outputs` may not use the name `error` — it is reserved for
  the implicit error port (E012).
- Signature parameters with **literal** defaults are optional inputs; a
  non-literal default is invisible to AST-based `napf check` (EC14) and
  is treated as required (EC36).

### Assert node checks (aligned to Jinja2 — no JMESPath)
```yaml
checks:
  - { kind: status, equals: 201 }                      # convenience
  - { kind: expr, expr: "trigger.value.body.id", op: present }
  - { kind: expr, expr: "trigger.value.body.email",
      op: matches, value: "^test\\+.*@example\\.com$" }
  - { kind: response_time, under_ms: 1500 }            # convenience
mode: report_all          # report_all | fail_fast
```
`op: present | equals | not_equals | contains | matches | gt | lt`.
`expr` is a sandboxed Jinja2 expression over the same templating context
as everywhere else.

### Settled engine decisions (input for the engine spec)
- Expressions: **Jinja2 everywhere** — `{{ }}` interpolation in config,
  bare Jinja2 expression in `expr:` and assert `expr` checks; sandboxed
  evaluation. One syntax. (JMESPath removed from earlier drafts.)
- Port types: `string | number | boolean | object | list | any`
  (default `any`); UI colors ports, warns on mismatch, never blocks.
- Edits: last-write-wins; UI watches the filesystem and reloads/prompts
  on external change.
- Run history: JSONL per run at `.napflow/runs/<flow>/<run-id>.jsonl`,
  append-only, raw, and identical to the local live WebSocket stream. JSONL
  creation is exclusive, while directories/files otherwise use ordinary
  OS/workspace permissions (POSIX umask and inherited Windows ACLs); napflow
  applies no custom owner, DACL, or forced-mode contract (D39).
  Terminal and JSON/JUnit reports apply D35's schema-aware declared-secret
  view; dictionary keys and protocol structure never change. The M4 target is
  full prepared-request/response detail (URL/query, effective headers/cookies,
  bodies, status, timing, retries); the current request event remains a
  pre-transport preview and response capture still has v0.1 valves (EC32/EC50).
  Replay-on-canvas = re-reading the file.
- Platforms: macOS, Windows, and Linux from day one (D26; pathlib
  discipline, no shell-isms); all three in the CI matrix.

### Merge node
- `any` — forward each arriving message immediately (use in front of
  `trigger` to join first-run and retry paths).
- `all` — wait until every connected input has a value, emit combined
  dict, **clear all slots**. `all` is a one-shot rendezvous: re-entering
  it inside a cycle with only a partial input set stalls the merge (the
  node then reports as never-fired/skipped at quiescence). To rejoin a
  retry path, put `any` in front of a `trigger` instead.
- `collect` — gather N messages into a list, then emit (count-based in
  v1; marker-based → roadmap).

### Source nodes (get, fixture) — firing semantics
In a message-driven engine a node with no inputs would never fire, so:
- `get` has a required `trigger` input; on each trigger it forwards the
  variable's current value on `value`.
- `fixture` has an **optional** `trigger` input: unconnected → fires once
  automatically at frame start (pure source); connected → fires per
  trigger. File content is read once and cached per run either way.

### Start / End node rules
- Exactly one `start` and one `end` per flow; both may have zero ports.
- **End ports are real input ports** — wire a value to `end.<port>`; End
  accumulates the latest value per port, never emits, and at quiescence
  the accumulated values become the flow's outputs.
- **End ports take `required: bool`, default `true` (D18).** At
  quiescence a required port with no value makes the run `failed`
  (exit 1) — a declared output that was never produced can never be a
  false green. Ports marked `required: false` yield `null`, noted in the
  report. Example:
  ```yaml
    - id: end
      type: end
      config:
        ports:
          - { name: job }                      # required (default)
          - { name: error_detail, required: false }
  ```
- The End port name `error` is **reserved** for the flow node's implicit
  error port (D21) — E012 rejects it.
- Start port `default:` templates are evaluated once at BIND and see
  only `env.*` / `run.*` — the frame does not exist yet (EC36).
- No triggers — a flow runs when invoked (UI, parent flow node, `napf run`,
  later generated code), all binding values to Start ports identically.
- CLI in: `napf run <flow> -i key=value`, `--input-json`; validated and
  type-coerced against Start ports; fail-fast on unknown/missing.
- CLI out: End ports → stdout as one JSON object; logs → stderr
  (pipeable: `napf run flows/login | jq .token`).
- UI: Start ports render as an editable key-value list on the node.

### Flow node (subflow) semantics
Reference, never embedding (`flow: flows/login`); copy-paste duplicates
the reference; explicit "Clone to new flow…" forks the folder; outer
ports derived from target's Start/End; "used in N places" shown on
drill-in; drill-in is pure navigation.

UX pins (S4/M6): "Clone to new flow…" on a flow/loop node forks the
TARGET's folder and repoints that one node at the clone — the escape
hatch from shared-reference semantics; every other user of the original
keeps it untouched (owner-confirmed 2026-07-09). Drill-in (double-click
or the inspector) and clone need a statically-known target — a
templated `flow:`/`body:` resolves at run time only, same rule as
E007's static-DAG scan.

**Implicit `error` port (D21):** every flow node exposes an `error`
output that fires when the child frame ends `failed`/`error`, carrying a
summary `{state, failed_asserts, unhandled_errors}` — so a parent can
wire a fallback/cleanup branch. Unconnected, the child's failure still
fails the run via run-wide aggregation (D20): safe by default,
branchable when wired.

### Loop node semantics
Body is a flow reference executed once per item of `over`. **Convention:
the body's Start must declare an `item` port; it may declare `index`.**
`mode: sequential | parallel` (+ `max_concurrency`); `on_error: stop |
continue`; `fresh_session: true` gives each iteration its own HTTP
session (default: shared per-run session); body End outputs collected on
`results` — ordered by item index regardless of completion order
(EC36) — failures on `errors`. Parallel mode uses a fixed worker set of at
most `max_concurrency`; it does not allocate one helper task per item.
Normally completed iteration frames are summarized durably and released,
so active task/frame counts are bounded by concurrency (D36/NFR-14).

An iteration "error" is **a body frame ending `failed` or `error`** (D20):
any failed assert, unhandled error-port message, worker crash/timeout, or
unreached required End port inside the body. `on_error` governs only
whether further iterations are *scheduled*; failed iterations land on
`errors` and count toward the run state regardless of mode.

## Scoping rules
Env profiles, `defaults.request`, declared-secret presentation policy = global.
Set/Get variables, `{{ inputs.* }}`, `{{ nodes.* }}`, node IDs =
flow-scoped; data crosses flow boundaries only via Start/End ports.
Run builtins `run.id`, `run.timestamp`, `run.env_name` span the whole run.

**Data is frame-isolated; outcomes are not (D20).** Assert results,
python-asserts, and unhandled error-port messages from every frame —
subflows and loop bodies included — aggregate into the single run-level
report and exit code.

Set/Get intentionally break the data wire, so Set-before-Get ordering
holds only when a path exists from the Set to the Get's `trigger`. Frame
variables are **not** a synchronization primitive.

## Templating
`{{ env.* }}`, `{{ inputs.* }}`, `{{ run.* }}`,
`{{ nodes.<id>.<port>... }}` in any string config field, including
`defaults.request` (which sees only `env`/`run` — see manifest).
Cross-node template references render as ghost-wires — drawn node-to-
node (references name nodes, not ports), dashed and view-only, for ids
that exist in the flow (extraction is the same Jinja2 AST parse E009
runs, so string literals never false-positive).

Envelope asymmetry (EC12): `trigger` is the full `{value, meta}` envelope
— reach into it as `trigger.value.…`; `nodes.<id>.<port>` is the
**unwrapped** port value — no `.value`. Prefer `{{ trigger }}` for the
value that fired the current node; `{{ nodes.* }}` holds each node's
*latest* output and is last-writer-wins under cycles and concurrent
branches (EC18).

**Native-value rule (D25).** A config value that is exactly one
`{{ expression }}` (ignoring surrounding whitespace) keeps the
expression's native type — `body: "{{ nodes.login.response.body }}"`
passes the dict itself, not a repr string. Anything mixed
(`"Bearer {{ env.API_TOKEN }}"`) renders to a string. The config
field's schema type applies after evaluation (string-typed fields
stringify; object-typed fields reject scalars). Bare `expr:` fields are
always native.

## Wire format
JSON-compatible `{value, meta}` envelope; errors travel as data via
`error`/`failed` ports. Guard ports `exhausted`/`expired` are **ordinary
pass-through outputs** carrying the triggering message, not error ports —
unconnected, their message is dropped (W106 lints this); whether a
tripped guard is a failure is decided by what you wire to it (D19).
**Binary payloads** (e.g. non-text response bodies) are represented as
`{"__binary__": true, "content_type": "...", "base64": "..."}` — the
body-capture size cap applies to the encoded form. An inbound request-body
envelope must have exactly those three fields, a non-empty string
`content_type`, and canonical standard base64. A malformed envelope is a
non-retryable `request_encoding` error on the request node's `error` port,
never an internal engine error (EC48).

## v1.1 candidates (kept on the roadmap)
- **`poll`** — request + success expression + interval + timeout in one
  node; sugar over the merge/condition/counter/delay pattern above.
- Inline loop bodies (loop without a separate flow folder).
- `webhook`/`timer` flow invocation via a `napf daemon` (out of scope for
  the core engine; flows themselves stay trigger-free).

## Resolved in review
2026-06-11:
- `run.*` builtins finalized: `run.id`, `run.timestamp`, `run.env_name`.
- `merge mode: collect` is count-based in v1 (marker-based → roadmap).
- `log` payloads ARE persisted into raw local JSONL run history; CLI/report
  presentation is separately redacted — consistent with D34/D35.

2026-06-14 → 2026-07-02 (edge-case review; see `EDGE_CASES.md`):
- D18 required End ports, D19 guard outputs, D21 flow `error` port +
  E012, counter = N passes, node-id charset, loop/start port surface,
  templating asymmetry — all folded into the sections above.
