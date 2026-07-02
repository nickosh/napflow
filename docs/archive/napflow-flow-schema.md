# napflow — Flow File Schema, Draft v0.3

Changes from v0.2: **message-driven execution model** (cycles legal, guarded);
single edge per input port; `trigger` port on Request; counter & timeout
guard nodes; merge node redefined; fixture & note promoted to v1;
Start/End CLI binding (from v0.2) unchanged.

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
  checked: *every edge-cycle must contain at least one guard node
  (`counter` or `timeout`)* — `napf check` fails otherwise.
- **Subflow references remain a strict DAG** (no recursive flows); this is
  separate from edge cycles and still checked at load/edit time.
- Untriggered branches end the run as `skipped`; the run finishes when no
  messages are in flight.

## Edge rules

- **Input ports accept exactly one edge — no exceptions** (including
  `trigger` and guard `reset` ports). Attaching a new edge in the UI
  disconnects the previous one.
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

  - id: kick
    type: merge                     # joins "first run" and "retry" paths
    config: { mode: any }

  - id: check_job
    type: request
    config:
      method: GET
      url: "{{ inputs.base_url }}/api/v1/job/{{ run.id }}"
      headers: { Authorization: "Bearer {{ env.API_TOKEN }}" }
      # implicit input port: trigger

  - id: is_ready
    type: condition
    config: { expr: "nodes.check_job.response.body.state == 'done'" }

  - id: attempts
    type: counter
    config: { count: 10 }           # ticks down; 0 → exhausted

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

edges:
  - { from: start.out,            to: kick.in1 }
  - { from: kick.out,             to: check_job.trigger }
  - { from: check_job.response,   to: is_ready.in }
  - { from: check_job.response,   to: show.in }
  - { from: is_ready.false,       to: attempts.in }
  - { from: attempts.continue,    to: wait.in }
  - { from: wait.out,             to: kick.in2 }       # ← the cycle, guarded
  - { from: is_ready.true,        to: end.job }

layout:
  start:       [40, 200]
  kick:        [200, 200]
  check_job:   [360, 200]
  is_ready:    [560, 200]
  attempts:    [560, 360]
  wait:        [360, 360]
  show:        [560, 80]
  end:         [760, 200]
```

## Node type catalog — v1

| type      | purpose | key config | ports |
|-----------|---------|------------|-------|
| `start`   | flow entry; defines flow inputs | `ports` | out: one per port (+ `out`) |
| `end`     | flow exit; defines flow outputs | `ports` | in: one per declared port |
| `request` | HTTP call via niquests | method, url, headers, query, body, timeout, TLS, retry, http_version | in: `trigger`; out: `response`, `error` |
| `python`  | run function from `nodes.py` | `function`, `outputs` | in: from signature; out: declared (+ `error`) |
| `assert`  | declarative checks → run report | `checks[]` (`status` / `expr` / `response_time`), `mode` | out: `passed`, `failed` |
| `condition` | if/else branch | `expr` | out: `true`, `false` |
| `switch`  | multi-way branch | `expr`, `cases[]` | out: per case + `default` |
| `loop`    | run a body flow once per item | `over`, `body` (flow path), `mode`, `max_concurrency`, `on_error`, `fresh_session` | out: `results`, `errors` |
| `flow`    | run another flow (subflow) | `flow:` path | derived from target Start/End |
| `set`     | write run variable (flow-scoped) | `name`, `value` | in, out |
| `get`     | read run variable | `name` | in: `trigger`; out: `value` |
| `merge`   | join paths | `mode: any \| all \| collect`; inputs `in1..inN` | in (1 edge each), out |
| `counter` | cycle guard: limited passes | `count` | in; out: `continue`, `exhausted` |
| `timeout` | cycle guard: deadline gate | `seconds` | in; out: `continue`, `expired` |
| `delay`   | wait before continuing | `seconds` (templatable) | in, out |
| `log`     | show value in UI / stderr | `label`, `level` | in, out (pass-through) |
| `fixture` | load JSON/CSV from `fixtures/` | `file`, `format` | in: `trigger` (optional); out: `value` |
| `note`    | canvas documentation (markdown) | `text` | none |

### Guard node semantics
- **counter** — starts at `count`, decrements per passing message; routes
  `continue` while > 0, `exhausted` at zero. Resets at the start of every
  run and every subflow/loop-body invocation.
- **timeout** — records timestamp of first message; routes `continue`
  while elapsed < `seconds`, else `expired`. Evaluated lazily on message
  arrival (no background timer — a branch sleeping in a `delay` is not
  interrupted; expiry takes effect on the next pass).
- Both have an optional **`reset` input port**: a message there restores
  the counter to `count` / clears the timeout's start timestamp, emitting
  nothing.
- **Unguarded cycles**: not rejected — `napf check` and the canvas flag
  them ("possible infinite loop — no counter/timeout in this cycle").
  Runtime backstop: a per-run message budget (manifest
  `defaults.run.message_budget`, default 10000) aborts runaway runs.

### Request node
- Has an explicit `trigger` input port — it fires when a message arrives
  there; config templating may reference any `{{ nodes.* }}` values
  already produced. Retry config handles transport-level retries; loop
  cycles handle application-level "retry until state X".

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
- **`assert` is supported**: a raised `AssertionError` routes to the
  node's `error` port with the assertion message, and is recorded in the
  run report alongside declarative assert-node results. Any other
  exception also routes to `error` with traceback in `meta`.

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
  append-only, identical objects to the live WebSocket stream, secrets
  masked. Request/response events carry **full detail**: URL, method,
  negotiated HTTP version, request & response headers, bodies, status,
  timing breakdown (DNS/connect/TLS/TTFB/total where niquests exposes it),
  retries attempted. Replay-on-canvas = re-reading the file.
- Platforms: macOS and Windows from day one (pathlib discipline, no
  shell-isms); Linux implicitly via CI.

### Merge node
- `any` — forward each arriving message immediately (use in front of
  `trigger` to join first-run and retry paths).
- `all` — wait until every connected input has a value, emit combined dict.
- `collect` — gather N messages into a list, then emit.

### Source nodes (get, fixture) — firing semantics
In a message-driven engine a node with no inputs would never fire, so:
- `get` has a required `trigger` input; on each trigger it forwards the
  variable's current value on `value`.
- `fixture` has an **optional** `trigger` input: unconnected → fires once
  automatically at frame start (pure source); connected → fires per
  trigger. File content is read once and cached per run either way.

### Start / End node rules
(unchanged from v0.2)
- Exactly one `start` and one `end` per flow; both may have zero ports.
- **End ports are real input ports** — wire a value to `end.<port>`; End
  accumulates the latest value per port, never emits, and at quiescence
  the accumulated values become the flow's outputs (unwired/never-reached
  ports yield null + a warning in the report).
- No triggers — a flow runs when invoked (UI, parent flow node, `napf run`,
  later generated code), all binding values to Start ports identically.
- CLI in: `napf run <flow> -i key=value`, `--input-json`; validated and
  type-coerced against Start ports; fail-fast on unknown/missing.
- CLI out: End ports → stdout as one JSON object; logs → stderr
  (pipeable: `napf run flows/login | jq .token`).
- UI: Start ports render as an editable key-value list on the node.

### Flow node (subflow) semantics
(unchanged) Reference, never embedding (`flow: flows/login`); copy-paste
duplicates the reference; explicit "Clone to new flow…" forks the folder;
outer ports derived from target's Start/End; "used in N places" shown on
drill-in; drill-in is pure navigation.

### Loop node semantics
Body is a flow reference executed once per item of `over`. **Convention:
the body's Start must declare an `item` port; it may declare `index`.**
`mode: sequential | parallel` (+ `max_concurrency`); `on_error: stop |
continue`; `fresh_session: true` gives each iteration its own HTTP
session (default: shared per-run session); body End outputs collected on
`results`, failures on `errors`.

## Scoping rules
(unchanged) Env profiles, `defaults.request`, secret masking = global.
Set/Get variables, `{{ inputs.* }}`, `{{ nodes.* }}`, node IDs =
flow-scoped; data crosses flow boundaries only via Start/End ports.
Run builtins `run.id`, `run.timestamp`, `run.env_name` span the whole run.

## Templating
(unchanged) `{{ env.* }}`, `{{ inputs.* }}`, `{{ run.* }}`,
`{{ nodes.<id>.<port>... }}` in any string config field, including
`defaults.request`. Cross-node template references render as ghost-wires.

## Wire format
JSON-compatible `{value, meta}` envelope; errors travel as data via
`error`/`failed`/`exhausted`/`expired` ports. **Binary payloads** (e.g.
non-text response bodies) are represented as
`{"__binary__": true, "content_type": "...", "base64": "..."}` — the
body-capture size cap applies to the encoded form.

## v1.1 candidates (kept on the roadmap)
- **`poll`** — request + success expression + interval + timeout in one
  node; sugar over the merge/condition/counter/delay pattern above.
- Inline loop bodies (loop without a separate flow folder).
- `webhook`/`timer` flow invocation via a `napf daemon` (out of scope for
  the core engine; flows themselves stay trigger-free).

## Resolved in review (2026-06-11)
- `run.*` builtins finalized: `run.id`, `run.timestamp`, `run.env_name`.
- `merge mode: collect` is count-based in v1 (marker-based → roadmap).
- `log` payloads ARE persisted into JSONL run history (masked) —
  consistent with full-capture philosophy.
