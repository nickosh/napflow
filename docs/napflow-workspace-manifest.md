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

Compatibility/current-state note (D33–D37, 2026-07-11): this is the
v0.1 manifest behavior. Package v0.x and `schema: napflow/v1` remain
experimental; v0.2 may replace capture/redaction settings as it moves to
full-fidelity blobs, soft local limits, and explicit CI/export policy.
Breaking changes are documented rather than prohibited before v1.0.
Amended 2026-07-12 for v0.2/M1: path resolution, local-request checks,
source durability, editor persistence, and flow-identity URL transport now
describe the implemented hardened behavior; later D34–D36 storage/lifecycle
changes remain future milestones.

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

codegen:                    # RESERVED: parsed, unused in current v0.x
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
6. **Workspace identities and containment (D37, v0.2/M1)** — one
   `WorkspaceResolver` owns entry flows, flow/loop references, fixtures,
   histories, source files, and clone destinations. Identities are non-empty
   workspace-relative POSIX paths: empty segments, `.`/`..`, backslashes,
   Windows drive syntax, control characters, and invalid Unicode surrogates
   are rejected; spaces and URL-reserved filename characters remain data.
   Candidates are resolved symlink-aware beneath the canonical workspace;
   clone destinations must also be lexically and canonically under
   `flows.root`. `flow.yaml`/`nodes.py` must resolve to their exact canonical
   source names, while whole-directory aliases that stay inside the workspace
   remain usable. Run ids match `YYYYmmdd-HHMMSS-xxxxxx` (lowercase hex).
   A final JSONL path must likewise resolve to that run id's exact canonical
   location, not alias another flow/run. Every violation uses stable reason
   `workspace_boundary`.
7. **`napf` walks upward** from cwd to find `napflow.yaml` (like git);
   all manifest paths are workspace-relative.
8. **Serialization** — the manifest and all flow files are read with a
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

## CLI surface (v0.1)

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

## Server surface — v0.1 API plus v0.2/M1 boundary (2026-07-12)

`napf ui [--port] [--no-browser]` serves UI + API + WebSocket on ONE
localhost port (D03) and opens the default browser (stdlib
`webbrowser`, D26 pin). The server (`napflow.server`, BlackSheep) is a
THIN adapter: run semantics live in `core/runprep.py`, shared verbatim
with `napf run` — one gate, one env-resolution rule, one stream wiring.

v0.2/M1 keeps the endpoint vocabulary but replaces the former scattered
`_safe_identity`, direct source writes, and independent UI debounce timers.
All identity-derived paths now use resolution rule 6; all source writes use
the durable path below; canvas persistence is serialized and lifecycle-aware.

- **Port**: default **6273** ("NAPF" on a phone keypad). Taken + no
  explicit `--port` ⇒ scan the next 19 (multiple open workspaces, the
  Jupyter convention). An explicit busy `--port` = error, exit 2.
- **Bind/request boundary**: `127.0.0.1` only — never a network service.
  Every HTTP/WS request must carry exactly one Host resolving to localhost or
  a loopback IP (dynamic ports allowed). Browser mutation methods and WS must
  also carry an `http(s)` Origin exactly matching scheme/host/port; a foreign
  or malformed authority is rejected before the handler/WS accept as HTTP
  403 `{error: "request_origin"}` or WS close 4403. Programmatic loopback
  clients may omit Origin. There is no auth/public bind mode (D37/EC51).
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
  D13; `?flow=` locates runs the server process didn't start; v0.2 M0
  validates the first `run_started` envelope before replay, accepts an
  unmarked v0.1 log best-effort, and returns 422 `history_format` for a
  malformed/newer format or unsupported declared feature) ·
  `POST /api/runs/{run_id}/abort` (202 aborting; on a finished run:
  200 + final state, idempotent no-op).
- Identity, run-id, or resolved-containment failures on these REST paths are
  HTTP 400 with stable `{error: "workspace_boundary", message}`; missing safe
  resources keep their existing 404/check vocabulary.
- **Write path** (S4/M4 + v0.2/M1, FR-1003/1109):
  `PUT /api/flows/<identity>` `{flow, base_etag?, force?}` — validate
  the FlowFile JSON (400 `validation` + pydantic diagnostics, nothing
  written) → etag gate (`base_etag` ≠ current ⇒ 409 `{error:
  "etag_conflict", etag}` unless `force`; last-write-wins is the v0.1
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
  Both paths serialize the per-canonical-file ETag check + write, so requests
  carrying one base ETag cannot both be accepted. `save_document` and
  nodes.py writes emit UTF-8/LF into a same-directory temporary file, flush +
  `fsync`, preserve existing permission bits, atomically replace, and clean
  the temporary on failure; a failed write returns 507 `write_failed` without
  truncating the live source. Scaffold and clone source creation use the same
  primitive; a failed clone removes its unaccepted destination. JSONL run
  histories remain streaming files and deliberately do not use atomic replace.
  `GET /api/etags/<identity>` → `{identity, etag, code_etag}` — cheap
  poll target; FR-1004's v0.1 shape is polling (~2s), not a native FS
  watcher: external change while the canvas is clean ⇒ silent reload;
  while dirty ⇒ the PUT's 409 raises the reload/overwrite prompt.
- **Editor persistence/identity transport** (v0.2/M1, FR-1110/1111): one
  revisioned coordinator per mounted flow/code file debounces but never
  overlaps writes; edits accepted during a request queue behind it and use the
  returned ETag. Flow navigation, code-editor close, and run start flush every
  mounted coordinator; conflict/error blocks the transition, while
  `beforeunload` visibly prompts whenever work is pending. Resource/navigation
  generations prevent late GET/PUT responses from replacing newer state.
  Browser routes are `/flow/<identity>`; every identity segment is encoded
  exactly once, keeping `/` as hierarchy and spaces/`#`/`%`/`?` as data.
  Namespacing prevents valid identities such as `api/workspace` or
  `assets/canvas` from colliding with REST/static paths. Back/forward crosses
  the same save barrier and restores a blocked history transition.
- **Subflow UX** (S4/M6, FR-1007): the flow-detail payload also carries
  `template_refs: {node: [node_ids]}` — cross-node `nodes.<id>`
  references, AST-derived (the same Jinja2 parse E009 runs) from
  `{{ }}`/`{% %}` config strings and bare expression fields, filtered
  to ids that exist in the flow; the canvas draws these as ghost-wires
  — and `used_by: [{identity, nodes}]` — flows whose flow/loop nodes
  reference this one, D09's "used in N places" (a place = a
  referencing node). `POST /api/flows/clone` `{source, dest}` → 201
  `{identity}`: forks the flow FOLDER (flow.yaml + nodes.py +
  anything else in it — D09's explicit "Clone to new flow…"). Guards:
  both identities cross the central workspace boundary (400
  `workspace_boundary`), dest must sit lexically and canonically under
  `flows.root` (a clone discovery can't see would be invisible in the
  sidebar and `napf list`), must not be or nest inside the source (400),
  and must not already exist (409 `dest_exists`); unknown source 404.
  Concurrent attempts at one destination serialize. Nested symlinks are
  preserved as links rather than dereferenced; source files use the durable
  write primitive and an interrupted clone destination is removed.
- **WebSocket** `/ws/runs/{run_id}`: text frames are the JSONL lines
  VERBATIM (one `encode_record` — identical by construction, D13).
  Live run: replay the buffered prefix, then stream; server closes
  normally after `run_finished`. Finished run: validate the history
  envelope, replay the file, close; malformed/newer/unsupported format:
  close `4409`.
  Unknown run: close `4404`; malformed run id/boundary: `4400`; rejected
  Host/Origin before accept: `4403`.
- **Run registry**: runs the server started, in memory — live buffers
  drop at run end (JSONL is the durable record), finished summaries
  capped at 32. Server shutdown aborts running flows (clean JSONL
  prefix, EC20). Reports (`defaults.run.report`) are NOT written for
  server runs in v0.1 — they stay a `napf run`/CI concern (revisited at
  S4/M5: still deferred, D29 — the canvas gets full wire detail live
  over the WebSocket plus the JSONL history browser).
- **Static UI**: the pre-built bundle ships inside the wheel and is
  served at `/` with an SPA fallback (S4/M2, NFR-03). Canvas deep links
  live only below `/flow/`; API and `/assets/` retain their own namespaces.
  Until the bundle exists, `/` is a plain placeholder page.

## Roadmap / reserved

- `codegen:` key — parsed, unused in current v0.x (design-constrained
  today; see PRODUCT.md).
- **Runtime secret redaction (D22)** — `set ... secret: true` or a
  response field-path redaction directive, so login-acquired tokens can
  opt into masking. This v0.1 note is superseded for v0.2 by D35; until
  then the shareability guarantee is scoped to declared secrets.
- `napf check --write-env-example`.
