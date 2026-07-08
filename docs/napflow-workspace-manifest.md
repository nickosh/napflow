# `napflow.yaml` — Workspace Manifest, v0.3

Status: **adopted 2026-07-02** (2026-06-14 edge-case review applied).

Changes from v0.2: secret-masking rule made precise (algorithm + scope,
D22/EC10); `defaults.request` template scope stated (EC23); runtime
secret redaction added to the roadmap; on-disk YAML follows the canonical
safe profile (D23, `yaml-profile.md`); `napf init` also writes
`.gitattributes` and `envs/example.env`. Amended 2026-07-02:
`defaults.run.run_timeout_s` + `napf run --timeout` (run deadline);
`node_timeout_s` default scope pinned to request/python (D24). Amended
2026-07-02 (b), senior review: `message_budget` default 100000,
`run_capture_mb` valve, `.env` dialect pinned, offline `flows/smoke` as
the first-touch check (EC28–EC37).

## Full example

```yaml
schema: napflow/v1

workspace:
  name: qa-api-flows
  description: API flows for the payments and user services.

flows:
  root: flows
  main: flows/main          # canvas the UI opens by default — just a flow

environments:
  default: dev              # profiles auto-discovered from envs/*.env;
  secrets:                  # picker shows filename stem (dev, staging, ...)
    - API_TOKEN
    - "*_PASSWORD"          # glob patterns; masked in UI, logs, run history

defaults:
  request:                  # templating: ONLY {{ env.* }} and {{ run.* }}
    timeout_s: 30
    verify_tls: true
    retry:
      max_attempts: 1
    headers:
      User-Agent: "napflow/0.1 ({{ env.TEAM_TAG }})"
  run:
    history: 20             # runs kept per flow in .napflow/runs/
    report: junit           # none | junit | json (built-in default: none)
    message_budget: 100000  # runaway protection, NOT resource accounting —
                            #   counts every emitted message run-wide incl.
                            #   child frames; sized so data-driven loops
                            #   don't trip it (EC31)
    node_timeout_s: 300     # default max_seconds per firing — auto-applies
                            #   to request/python only; delay/loop/flow are
                            #   exempt from the DEFAULT but honor an
                            #   explicit per-node max_seconds (D24)
    run_timeout_s: null     # wall-clock run deadline; null = off (CI job
                            #   timeout is the outer backstop). Expiry →
                            #   run `error` (exit 2), report still written
    body_capture_mb: 10     # per-body JSONL disk valve (full detail under cap)
    run_capture_mb: 500     # per-RUN total body-capture valve — a big loop
                            #   must not write gigabytes of JSONL; excess
                            #   bodies truncated with marker (EC32)

python:
  interpreter: null         # path to python executable for the nodes.py
                            # worker subprocess (engine spec §5a);
                            # null = napflow's own interpreter.
                            # Point at a project venv to enable its
                            # third-party packages in python nodes.

codegen:                    # RESERVED: parsed, unused in v1
  output: generated/
  client_style: niquests
```

## Resolution rules

1. **Flow discovery** — any directory under `flows.root` containing
   `flow.yaml` is a flow; identity = workspace-relative path. Recursive,
   so grouping like `flows/payments/refund/` is free.
2. **Env discovery** — every `envs/*.env` file is a profile; profile name =
   filename stem. All env files are gitignored by `napf init`. No file
   registry in the manifest — drop a file in `envs/`, it appears in the UI.
   Dialect (EC36): `KEY=VALUE` per line; `#` comments and blank lines
   ignored; optional single/double quotes stripped from values; no
   `export` prefix, no variable interpolation — values are literal
   strings (types recovered by whoever consumes them).
   Pinned at M3 (2026-07-04, `core/workspace.py`): comments are
   full-line only (values are literal, so `V=x # y` keeps the `# y`);
   a line without `=`, or a key outside `[A-Za-z_][A-Za-z0-9_]*`
   (catches stray `export`), is an error with file:line — profiles are
   CI-gate inputs and fail fast; exactly one matching quote pair is
   stripped; duplicate keys — last wins.
3. **Env layering** — lookup order, last wins:
   profile file → process environment. Process env winning makes CI
   overrides trivial: `API_TOKEN=$CI_SECRET napf run flows/login`.
   Pinned at S2/M1 (2026-07-05, `layer_env`): the whole process
   environment participates in the lookup — a key absent from the
   profile but present in process env is still visible as
   `{{ env.KEY }}`; masking (rule 5) already scans both sources.
4. **Request defaults merge shallowly** — node-level `retry:` replaces the
   whole block; no deep-merge surprises. Only `{{ env.* }}` and
   `{{ run.* }}` are in scope in `defaults.request` — `inputs`/`nodes`
   are frame-scoped and would be `StrictUndefined` (a node error on every
   inheriting request) (EC23).
5. **Secret masking (D22)** replaces the *values* of env vars matching
   `environments.secrets` (active profile + process env) wherever they
   appear, via substring scan with a 5-char minimum length — catching
   tokens embedded in URLs/bodies without masking short common strings.
   Only declared secrets are masked; runtime-acquired tokens (e.g. a
   bearer token in a login response body) are not — see roadmap. Masking
   applies in UI, logs, and stored runs alike, at emission (events are
   born masked).
6. **`napf` walks upward** from cwd to find `napflow.yaml` (like git);
   all manifest paths are workspace-relative.
7. **Serialization** — the manifest and all flow files are read with a
   safe YAML loader and written through the shared canonical serializer
   (block style, strings double-quoted, no anchors, LF). See
   `yaml-profile.md` (D23).

## What deliberately does NOT live here
- Per-flow interface and `env.required` → in each flow's Start/End nodes
  and `flow.yaml` (flows stay individually reusable).
- Node definitions or shared graph state.
- Secret values — only name patterns for masking.

## `napf init` output

```
napf init my-workspace
  created  napflow.yaml
  created  flows/main/flow.yaml        # default canvas (start+end scaffolded)
  created  flows/main/nodes.py
  created  flows/example/flow.yaml     # request→assert demo against httpbin
  created  flows/example/nodes.py
  created  flows/smoke/flow.yaml       # fixture→python→assert — fully offline
  created  flows/smoke/nodes.py
  created  fixtures/smoke.json         # data for the smoke fixture node
                                       #   (added at M5 — E008 requires it)
  created  envs/dev.env                # BASE_URL=https://httpbin.org
  created  envs/example.env            # committed onboarding template
  created  .gitignore                  # envs/*.env (except example.env), .napflow/
  created  .gitattributes              # *.yaml / *.yml text eol=lf
  created  .napflow/
```

First-touch check (EC34): `napf run flows/smoke` must pass **offline**
out of the box — no network, no external services. `flows/example` is
the HTTP demo against httpbin (network required); it is deliberately NOT
the smoke check, so a proxy, a firewall, or httpbin having a bad day
cannot break a user's first five minutes (nor napflow's own CI).

## CLI surface (v1)

```
napf init [dir]               scaffold workspace
napf ui [--port] [--no-browser]  serve editor + engine on one localhost
                              port (default 6273), open browser
napf run <flow> [--env NAME]  headless run, exit code from asserts
     [-i key=value ...]       bind values to Start ports (validated, typed)
     [--input-json JSON]      structured inputs
     [--timeout SECONDS]      wall-clock run deadline (overrides
                              defaults.run.run_timeout_s; expiry → exit 2)
                              End outputs → stdout as JSON; logs → stderr
napf list                     discovered flows + their Start/End ports
napf check                    validate all flows (schema, edges, env.required,
                              guard analysis of cycles, subflow-reference DAG)
```

Exit codes for `napf run`: 0 passed · 1 failed · 2 error · 130 aborted.
`napf check` (pinned at S1/M5): 0 clean or warnings-only · 1 any E-code ·
2 operational error (no workspace found). `napf init` refuses a
directory that already has a `napflow.yaml` (exit 2) and never
overwrites individual files.

`napf run` pins (S2/M5, 2026-07-05):
- **Run gate** = `check_flow` on the target flow (E-codes → exit 2
  before anything executes, no JSONL; warnings print to stderr and
  proceed). The full-workspace closure gate stays `napf check`; the
  run gate deepens at S3 when flow references become runnable.
- **stdout carries ONLY the End-outputs JSON and is NOT masked** — it
  is the functional output (`napf run flows/login | jq .token` is the
  contract); masking (D22) covers UI, logs, events, and stored runs.
- **Inputs**: `--input-json` (object) is applied first, `-i KEY=VALUE`
  overrides per key; `-i` values arrive as strings and BIND coerces
  them against the port's declared type.
- **Env**: explicit `--env NAME` must exist (exit 2 otherwise); the
  manifest `environments.default` is best-effort — profiles are
  gitignored, so a fresh clone falls back to process env with a stderr
  note.
- **Reports** (`defaults.run.report`) are written next to the JSONL:
  `<run-id>.report.json` / `<run-id>.junit.xml`, built from the masked
  event records (junit: testcase per assert, errored testcase per
  unhandled error).
- **Ctrl-C** = clean abort (exit 130) where asyncio signal handlers
  exist; on Windows the KeyboardInterrupt path exits 130 and the JSONL
  keeps a valid prefix (EC20).

Dropped for now: `napf sync` — with no registry, copied folders just appear
and broken references surface in `napf check` / on canvas. Possible later
nicety: `napf check --write-env-example` to regenerate a committed
`envs/example.env` from the union of all flows' `env.required`.

`napf check` is the CI pre-gate: fails fast on broken references before
anything executes.

## Server surface (v1) — pinned at S4/M1, 2026-07-06

`napf ui [--port] [--no-browser]` serves UI + API + WebSocket on ONE
localhost port (D03) and opens the default browser (stdlib
`webbrowser`, D26 pin). The server (`napflow.server`, BlackSheep) is a
THIN adapter: run semantics live in `core/runprep.py`, shared verbatim
with `napf run` — one gate, one env-resolution rule, one stream wiring.

- **Port**: default **6273** ("NAPF" on a phone keypad). Taken + no
  explicit `--port` ⇒ scan the next 19 (multiple open workspaces, the
  Jupyter convention). An explicit busy `--port` = error, exit 2.
- **Bind**: `127.0.0.1` only — never a network service. No auth in v1
  (localhost trust); anything beyond that is out of scope (PRODUCT).
- **REST** (JSON): `GET /api/workspace` (manifest summary + profiles +
  version) · `GET /api/flows` (structured `napf list`; unloadable
  flows appear `valid: false`) · `GET /api/flows/<identity>` (catch-all
  path; model dump + closure diagnostics + per-node port surfaces
  `ports: {node: {inputs, outputs, required_inputs, growable} | null}`
  — the canvas draws handles/colors from these (D11) and never derives
  them itself: python ports are AST-parsed server-side (EC14), `null`
  = unknowable broken reference; 404 unknown. S4/M4 grew the payload:
  `etag` + `code_etag` (16-hex sha256 content-hash prefixes; `null` =
  file absent), `functions` (AST fn-name list from nodes.py, `null` =
  missing/unparseable — the canvas python dropdown), and the model dump
  became `exclude_unset` — the canvas PUTs the dump back, so
  materialized defaults would bloat every saved file. M4 pin: check
  E-codes do NOT 400 this endpoint (a mid-edit flow must stay
  editable; diagnostics ride along) — only unloadable files 400) ·
  `POST /api/runs` `{flow, env?, inputs?}` → 202 `{run_id, flow,
  state, log, warnings, notes}` (gate failures: 404 `flow_not_found`,
  else 400 with `{error, message, diagnostics}`) ·
  `GET /api/runs?flow=` (history from the JSONL dir; states from each
  file's tail record, `incomplete` when it isn't `run_finished`) ·
  `GET /api/runs/{run_id}` (status; result summary when finished — end
  outputs are read from the masked `run_finished` event, NEVER from
  this endpoint: unmasked outputs are `napf run` stdout's contract
  only) · `GET /api/runs/{run_id}/events` (replay = re-read the JSONL,
  D13; `?flow=` locates runs the server process didn't start) ·
  `POST /api/runs/{run_id}/abort` (202 aborting; on a finished run:
  200 + final state, idempotent no-op).
- **Write path** (S4/M4, FR-1003; identities are `_safe_identity`-
  guarded — absolute/`..`/drive-letter tails 400, the write path never
  escapes the workspace root):
  `PUT /api/flows/<identity>` `{flow, base_etag?, force?}` — validate
  the FlowFile JSON (400 `validation` + pydantic diagnostics, nothing
  written) → etag gate (`base_etag` ≠ current ⇒ 409 `{error:
  "etag_conflict", etag}` unless `force`; last-write-wins is the v1
  conflict ceiling) → `merge_flow_document` into the round-trip doc →
  the ONE canonical serializer (D23). Returns `{identity, etag,
  diagnostics}` — check runs post-save; E-codes gate RUNS, never saves
  (work-in-progress flows must persist). The UI never emits YAML.
  `GET /api/code/<identity>` → `{identity, exists, code, etag,
  syntax_error, functions}`; `PUT /api/code/<identity>` `{code,
  base_etag?, force?}` — nodes.py verbatim (LF), same etag gate;
  a syntax error is REPORTED (`ast.parse`, EC14) but the file saves
  anyway — the editor never holds user code hostage; broken code
  surfaces as E008 until fixed. PUT creates a missing nodes.py.
  `GET /api/etags/<identity>` → `{identity, etag, code_etag}` — cheap
  poll target; FR-1004's v1 shape is polling (~2s), not a native FS
  watcher: external change while the canvas is clean ⇒ silent reload;
  while dirty ⇒ the PUT's 409 raises the reload/overwrite prompt.
- **WebSocket** `/ws/runs/{run_id}`: text frames are the JSONL lines
  VERBATIM (one `encode_record` — identical by construction, D13).
  Live run: replay the buffered prefix, then stream; server closes
  normally after `run_finished`. Finished run: replay the file, close.
  Unknown run: close `4404`.
- **Run registry**: runs the server started, in memory — live buffers
  drop at run end (JSONL is the durable record), finished summaries
  capped at 32. Server shutdown aborts running flows (clean JSONL
  prefix, EC20). Reports (`defaults.run.report`) are NOT written for
  server runs in v1 — they stay a `napf run`/CI concern (revisited at
  S4/M5: still deferred, D29 — the canvas gets full wire detail live
  over the WebSocket plus the JSONL history browser).
- **Static UI**: the pre-built bundle ships inside the wheel and is
  served at `/` with an SPA fallback (S4/M2, NFR-03); until it exists,
  `/` is a plain placeholder page.

## Roadmap / reserved

- `codegen:` key — parsed, unused in v1 (design-constrained today; see
  PRODUCT.md).
- **Runtime secret redaction (D22)** — `set ... secret: true` or a
  response field-path redaction directive, so login-acquired tokens can
  opt into masking. Deferred from v1; until then the shareability
  guarantee is scoped to declared secrets.
- `napf check --write-env-example`.
