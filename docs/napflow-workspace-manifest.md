# `napflow.yaml` — Workspace Manifest, v0.3

Status: **adopted 2026-07-02** (2026-06-14 edge-case review applied).

Changes from v0.2: secret-masking rule made precise (algorithm + scope,
D22/EC10); `defaults.request` template scope stated (EC23); runtime
secret redaction added to the roadmap; on-disk YAML follows the canonical
safe profile (D23, `yaml-profile.md`); `napf init` also writes
`.gitattributes`. Amended 2026-07-02:
`defaults.run.run_timeout_s` + `napf run --timeout` (run deadline);
`node_timeout_s` default scope pinned to request/python (D24). Amended
2026-07-02 (b), senior review: `message_budget` default 100000,
historical `run_capture_mb` valve, `.env` dialect pinned, offline
`flows/smoke` as the first-touch check (EC28–EC37).

Compatibility/current-state note (D33–D39): this is current behavior.
Package v0.x and `schema: napflow/v1` remain experimental; breaking changes
are documented rather than prohibited before v1.0.
Amended 2026-07-12 for v0.2/M1: path resolution, local-request checks,
source durability, editor persistence, and flow-identity URL transport now
describe the implemented hardened behavior; subsequent amendments fold in
D34–D36 storage/lifecycle changes as their milestones land.
Amended 2026-07-13 for v0.2/M4: raw local full-value history activates
`content-blobs/1`, uses ordinary OS/workspace permissions, and has no
body/run capture settings. Schema-aware terminal/report redaction remains
opt-in through non-empty secret patterns; D39 defers export and secure-history
policy.
Amended 2026-07-15 for rolling feature F6/D43: brownfield init now
inspects only workspace-root Git metadata, offers LF-only canonical appends,
never changes CR/CRLF or unsafe paths, and exposes advisory read-only W109
checks with explicit CLI opt-outs.
Amended 2026-07-15 for F7/D44: flow, input-data, and environment roots are
configurable and workspace-contained; dotenv profiles use literal filenames;
W108 checks semantic root-ignore coverage; default init is minimal and
`--example` creates the complete reference workspace.

v0.2 upgrade note: there is no automatic manifest migration. Existing files
that still validate load best-effort, but `defaults.run.body_capture_mb` and
`defaults.run.run_capture_mb` are removed and now fail validation. New
scaffolds use `environments.secrets: []`, so optional terminal/report masking
is off until patterns are configured; canonical local history is raw either
way. Run/event reader compatibility is defined by the engine spec, and the
release-facing break summary is `release-notes-v0.2.0.md`.

## Full example

```yaml
schema: napflow/v1

workspace:
  name: qa-api-flows
  description: API flows for the payments and user services.

flows:
  root: flows
  main: flows/main          # canvas the UI opens by default; omitted means
                            # <flows.root>/main

data:
  root: data                # fixture-node file: values resolve below here

environments:
  root: .                   # "."/"./" means the napflow workspace root
  default: .env             # exact filename; null = process environment only
  secrets: []               # built-in/scaffold default: no presentation masking
  # Opt in by adding env-name globs such as API_TOKEN or "*_PASSWORD".
  # They redact terminal/reports, never raw history.

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
    run_timeout_s: null     # cooperative execution deadline; null = off
                            #   (CI job timeout is the outer backstop). Armed
                            #   after root ENV/BIND; expiry → run `error`
                            #   (exit 2), report still written. Synchronous
                            #   Jinja remains an explicit limitation.

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
   so grouping like `flows/payments/refund/` is free. The public Python surface
   keeps the existing `discover_flows()` metadata operation and adds fresh
   runnable discovery through `workspace.discover()`, exact binding through
   `workspace.flow(identity)`, and the dynamic catalog below `flows.root` at
   `workspace.flows`. Identifier-safe relative segments are available as exact
   attribute chains (`workspace.flows.payments.refund`); bracket lookup and
   `workspace.flow(...)` cover punctuation, keywords, member collisions, and
   arbitrary legal identities without normalization. Catalog brackets are
   flows-root-relative; `workspace.flow(...)` takes a full workspace-relative
   identity, avoiding ambiguity when a legal first segment equals the root
   name. A flow directory may also be a namespace containing child flows (D38).
   `flows.main` is likewise a full workspace-relative identity and must stay
   below `flows.root`; when omitted, it defaults to `<flows.root>/main`.
   The root remains the discovery, catalog, scaffold, and clone-destination
   boundary—not a new access sandbox: existing explicit full workspace-relative
   entry/reference identities outside it remain supported and participate in
   reference-closure checking (FR-308/D38).
2. **Data and environment roots (F7/D44)** — `data.root` defaults to the
   proper workspace subdirectory `data`; every fixture-node `file:` is
   relative to that root. Like `flows.root`, it may be nested but may not be
   `.`. `environments.root` defaults to `.` and uniquely accepts `.` or `./`
   as the workspace root because host-project dotenv files are an intended
   source. All roots reject absolute paths, `..`, backslashes, drive syntax,
   and symlink escapes (D42).

   Environment discovery is non-recursive and collects the filename union
   `.env`, `.env.*`, and `*.env` under the configured root. The **literal
   filename is the profile id**: select `.env`, `.env.staging`, or `dev.env`
   exactly in `--env`, `environments.default`, and the UI. There is no
   registry, stem mapping, collision rule, or root-specific special case.
   Regular readable UTF-8 files in the pinned dialect are selectable;
   directories and unreadable/invalid entries are omitted with W105/operator
   warnings. Explicitly selecting an omitted/invalid filename, or configuring
   it as the default, is a hard preparation error—napflow never silently runs
   without a requested profile. An unsafe literal name or escaping candidate
   retains the stable `workspace_boundary` reason; safe missing names use
   `env_not_found`, and safe invalid content uses `env_invalid`. Unselected
   invalid candidates do not block a run.

   `napf init --example` creates `.env` plus the committed `.env.example`
   template at the configured environment root. Its root `.gitignore` adds
   only the exact anchored sensitive path (`/.env` for the default), never a
   broad wildcard. Basic init creates no environment file. W108 reports each
   actual non-template profile not semantically covered by the workspace-root
   `.gitignore`; parent/global/`.git/info` rules do not count. W109 separately
   checks fixed napflow metadata. Both warnings are advisory and read-only.

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
   `{{ env.KEY }}`; redaction (rule 5) already scans both sources.
4. **Request defaults merge shallowly** — node-level `retry:` replaces the
   whole block; no deep-merge surprises. Only `{{ env.* }}` and
   `{{ run.* }}` are in scope in `defaults.request` — `inputs`/`nodes`
   are frame-scoped and would be `StrictUndefined` (a node error on every
   inheriting request) (EC23).
5. **Secret views (D35, v0.2/M4)** preserve raw canonical JSONL and local
   WebSocket records in local run files using ordinary OS/workspace
   permissions, then replace the *values* of env
   vars matching `environments.secrets` (active profile + process env) in
   terminal and JSON/JUnit report content. Matching uses substring scan with
   a 5-char minimum length and longest value first. One exhaustive event-field
   registry limits redaction to content values: dictionary keys, identifiers,
   enums, state/error vocabulary, and control metadata never change. Only
   declared secrets are recognized; runtime-acquired tokens (e.g. a bearer
   token in a login response body) are not — see roadmap. The local UI is a
   raw inspection surface; v0.2 makes no safe-export claim (D39).
   The built-in and scaffolded value is `[]`, so no masking occurs until the
   user explicitly adds one or more name patterns.

   **Raw-history warning:** `.napflow/runs/` may contain complete request and
   response headers/bodies, cookies, credentials, bearer tokens, log values,
   Python-node content, and End outputs. A greenfield scaffold creates the
   `.napflow/` ignore rule; brownfield init may append it only with consent to
   an LF root file, and W109 remains advisory. Git metadata reduces accidental
   commits but is not sanitization or access control. Do not commit,
   upload, attach, publish, or otherwise share this directory without inspecting
   its contents. Terminal/JSON/JUnit masking creates presentation views only;
   it does not rewrite the canonical JSONL, referenced blobs, or local UI data.
6. **Workspace identities and containment (D37, v0.2/M1)** — one
   `WorkspaceResolver` owns entry flows, flow/loop references, fixture data,
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

Basic scaffold:

```
napf init my-workspace
  created  napflow.yaml
  created  flows/main/flow.yaml        # empty default canvas: Start + End only
  created  flows/main/nodes.py
  created  data/
  created  .gitignore                  # .napflow/ only
  created  .gitattributes              # *.yaml / *.yml text eol=lf
  created  .napflow/
```

Complete reference scaffold:

```
napf init my-workspace --example
  created  napflow.yaml
  created  flows/main/flow.yaml
  created  flows/main/nodes.py
  created  flows/example/flow.yaml     # request→assert demo against httpbin
  created  flows/example/nodes.py
  created  flows/smoke/flow.yaml       # fixture→python→assert — fully offline
  created  flows/smoke/nodes.py
  created  data/smoke.json              # input for the smoke fixture node
  created  .env                         # BASE_URL=https://httpbin.org
  created  .env.example                 # committed onboarding template
  created  .gitignore                   # exact /.env plus .napflow/
  created  .gitattributes              # *.yaml / *.yml text eol=lf
  created  .napflow/
```

Example first-touch check (EC34): `napf run flows/smoke` must pass **offline**
after `napf init --example` — no network, no external services. `flows/example` is
the HTTP demo against httpbin (network required); it is deliberately NOT
the smoke check, so a proxy, a firewall, or httpbin having a bad day
cannot break a user's first five minutes (nor napflow's own CI).

Brownfield Git-metadata behavior (F6/D43): when the target has no
`napflow.yaml` but already contains root `.gitignore` or `.gitattributes`,
`napf init` checks only that file for napflow's canonical lines. Parent
metadata, `.git/info/*`, global configuration, and a Git executable never
count. A covered LF file reports `exists ... (rules covered)`. A missing-rule
LF file prompts per file on a TTY, displays the exact `# napflow` block, and
defaults to append; `--git-meta append|skip` makes that choice noninteractive.
Without a TTY, the default is `skipped` plus an exact warning. Appends contain
only required additions, are idempotent, preserve existing content/mode, and
use the shared atomic LF write path.

Any CR/CRLF, invalid UTF-8, unreadable, symlink, or non-regular existing
metadata file is warned about and never modified, including under
`--git-meta append`. `--no-git-meta-check` bypasses inspection, prompts, and
warnings for existing files (and conflicts with explicit append), while still
creating either metadata file when it is missing. Only `napf init` may append;
`napf check` reports the same policy as read-only W109 and can suppress it with
`--no-git-meta-check`. The statuses are `created`, `exists`, `appended`, and
`skipped`.

The three init root options write the selected paths into the manifest and
place scaffold content there:

```
napf init [dir] [--flows-root PATH] [--data-root PATH]
     [--environments-root PATH] [--example]
```

Configured roots are validated before Git metadata or scaffold content is
written. Existing directories are reused without deleting or overwriting
their contents; scaffold-owned files report `exists`. A file, symlink,
Windows junction, or other non-directory at a required directory path is an
operational error before `napflow.yaml` is created. An existing or dangling
`napflow.yaml` symlink is an existing workspace and is refused before any
write. A required directory (or one of its ancestors) must not also be a
planned scaffold file: roots such as `napflow.yaml`, `.gitignore`, or
`flows/main/flow.yaml` are rejected from the complete plan before any callback
or write. An existing non-metadata scaffold source is preserved only when it
is a regular file; a directory, symlink (including dangling), junction, or
other non-regular object in a source-file role is an equally early error.
Existing root Git metadata retains the D43 inspect/skip/warn policy and is
never mutated when unsafe. Basic init creates an empty custom environment
subdirectory, but creates no profile file.

## CLI surface (current experimental v0.x)

```
napf init [dir] [--example] [--flows-root PATH] [--data-root PATH]
     [--environments-root PATH] [--git-meta append|skip]
     [--no-git-meta-check]    scaffold workspace; existing Git metadata is
                              consent-based and LF-only
napf ui [--port] [--no-browser]  serve editor + engine on one localhost
                              port (default 6273), open browser
napf run <flow> [--env NAME]  headless run, exit code from asserts
     [-i key=value ...]       bind values to Start ports (validated, typed)
     [--input-json JSON]      structured inputs
     [--timeout SECONDS]      wall-clock run deadline (overrides
                              defaults.run.run_timeout_s; expiry → exit 2)
                              End outputs → stdout as JSON; logs → stderr
napf list                     discovered flows + their Start/End ports
napf check [--no-git-meta-check]
                              validate all flows (schema, edges, env.required,
                              guard analysis of cycles, subflow-reference DAG)
```

Exit codes for `napf run`: 0 passed · 1 failed · 2 error · 130 aborted.
`napf check` (pinned at S1/M5): 0 clean or warnings-only · 1 any E-code ·
2 operational error (no workspace found). `napf init` refuses a directory
that already has a `napflow.yaml` (exit 2). Scaffold content is never
overwritten; existing-path and planned file/directory role collisions fail
during preflight. The sole existing-file mutation is an explicitly authorized LF
append to root `.gitignore`/`.gitattributes`. W109 is warning-class, so it does
not change `napf check`'s exit status; W108 is equally advisory.

`napf run` pins (S2/M5, 2026-07-05):
- **Run gate** = `check_flow` on the target flow (E-codes → exit 2
  before anything executes, no JSONL; warnings print to stderr and
  proceed). The full-workspace closure gate stays `napf check`; the
  run gate deepens at S3 when flow references become runnable.
- **stdout carries ONLY the End-outputs JSON and is NOT masked** — it
  is the functional output (`napf run flows/login | jq .token` is the
  contract). CLI stderr and reports use the declared-secret redacted view;
  raw local history and the local UI retain exact values (D35). Raw run
  directories/files use ordinary OS/workspace permissions: POSIX umask and
  inherited Windows ACLs apply, with no custom ownership or mode migration.
- **Inputs**: `--input-json` (object) is applied first, `-i KEY=VALUE`
  overrides per key; `-i` values arrive as strings and BIND coerces
  them against the port's declared type.
- **Env**: explicit `--env NAME` and `environments.default` both select one
  literal filename and must resolve to a valid discovered profile (exit 2
  otherwise). `default: null` means process-environment only. Invalid
  unselected candidates are reported but do not prevent a run; the entire
  process environment still overrides the selected file.
- **Reports** (`defaults.run.report`) are written next to the JSONL:
  `<run-id>.report.json` / `<run-id>.junit.xml`, built as schema-aware
  declared-secret redacted views over the raw local JSONL (junit: testcase
  per assert, errored testcase per unhandled error). `none` installs no report
  collector; JSON retains only the final summary, while JUnit makes bounded
  streaming passes over the closed durable JSONL. Both gate the history
  feature envelope and resolve only records they render, so an unrelated
  missing blob is not fetched. For `content-blobs/1` histories, a JSON report
  carries `format`/`features` and persists its redacted final values through
  the same run store; unchanged large content keeps the canonical descriptor
  rather than being duplicated inline. An unclassified event/field fails
  closed instead of leaking into a nominally safe report. Report
  closeout precedes complete-history publication and whole-unit retention, so
  a retained JSONL never loses or orphans its configured report companion.
  An ordinary sink-close failure does not replace the run outcome, but forces
  the history unit to `.incomplete` and skips report publication. Control-flow
  exceptions still propagate after that cleanup.
- **Ctrl-C** = clean abort (exit 130) where asyncio signal handlers
  exist; on Windows the KeyboardInterrupt path exits 130 and the JSONL
  keeps a valid prefix (EC20).

Dropped for now: `napf sync` — with no registry, copied folders just appear
and broken references surface in `napf check` / on canvas. Possible later
nicety: generate a committed environment template from the union of all
flows' `env.required`.

`napf check` is the CI pre-gate: fails fast on broken references before
anything executes.

## Server surface — v0.1 API plus v0.2/M5 boundary (2026-07-13)

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
  a loopback IP (dynamic ports allowed). Unsafe HTTP methods and WebSockets
  validate an Origin when present; browser-supplied Origin must be one
  `http(s)` authority exactly matching scheme/host/port. Programmatic loopback
  clients may omit Origin. A foreign, duplicate, or malformed required
  authority is rejected before the handler/WS accept as HTTP 403
  `{error: "request_origin"}` or WS close 4403. There is no auth/public bind
  mode (D37/EC51).
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
  `GET /api/runs?flow=` (history from the JSONL dir, ordered by a locked
  internal monotonic lifecycle value with legacy mtime fallback; states from a robust
  backward scan for the last valid record, including records larger than
  64 KiB; active server runs are `running`, exact external `.active` units are
  `indeterminate`, markerless/known-closed prefixes are `incomplete`, and a
  durable `run_finished` contributes its outcome state) ·
  `GET /api/runs/{run_id}` (status; bounded scalar summary when finished:
  state/duration/assert counts/unhandled-error count/never-fired count —
  detail and raw End outputs remain in canonical `run_finished`, NEVER this
  scalar endpoint) ·
  `GET /api/runs/{run_id}/events?flow=&after_seq=&limit=&frame=` (replay =
  re-read the JSONL, D13; returns a bounded `napflow-replay/1` page with
  `run_format`, `features`, `root_frame`, `history_state`, bounded
  `run_summary`/`view_summary`, sequence cursor, `has_more`, and `events`.
  `run_summary` is a scalar projection of the durable final fact (state,
  duration, assertion and
  error/skipped counts), so the browser can settle after one page without
  returning tail End/error values. `view_summary` scans the frozen snapshot
  into frame-scoped node/edge/port/request aggregates and a fixed per-node Log
  ring without returning the other event pages or resolving descriptors.
  `history_state` is `running` for a run owned live
  by this server, `complete` for a durable `run_finished`, `incomplete` for a
  known-closed/legacy EC20 prefix, or `indeterminate` for another process's
  exact regular `.active` unit. `limit` defaults to 200 and is capped at 500;
  `next_after_seq` is the last returned canonical sequence. An optional
  `frame` selects that exact frame; the root-frame view also includes the
  frame-less `run_started`/`run_finished` run envelopes. Blob descriptors pass
  through unchanged) ·
  `GET /api/runs/{run_id}/frames?flow=&parent_frame=&after_seq=&limit=`
  (the same versioned bounded envelope over direct-child `frame_finished`
  projections; absent `parent_frame` means the root, and recursively requesting
  a selected completed child reconstructs the durable frame tree without
  retained runtime `Frame` objects. Each entry keeps navigation/scalar fields,
  assertion/error counts, and End-port names only; full errors/End values remain
  lazy on that canonical event's sequence. Cancelled children have no false
  summary) ·
  `GET /api/runs/{run_id}/events/{seq}?flow=` (resolve only that canonical
  event's schema-declared content on demand, verifying blob size/hash before
  returning `event`; missing, corrupt, omitted, and malformed content use
  explicit `history_content_*` errors rather than a partial value). All three
  readers validate the first `run_started` envelope, support
  `content-blobs/1`, accept an unmarked v0.1 log best-effort, return 422
  `history_format` for malformed/newer/unsupported history, and hold a
  retention reader lease for the request. Each request captures a sequence and
  lifecycle boundary before scanning, validates every record through that
  boundary (including the unreturned tail), and rejects invalid UTF-8,
  malformed middle data, or non-consecutive sequences as `history_format` ·
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
  the same save barrier and restores a blocked history transition. Canvas
  document undo/redo (FR-1118) is a separate, memory-only 100-step stack for
  the currently open `FlowModel`: structurally shared roots cover
  nodes/edges/config/ports/layout without retaining ETags, diagnostics, or run
  state. Undo and redo enqueue ordinary coordinator revisions; navigation,
  conflict reload, and successful external flow-document reload clear the
  stack, while post-save and code-only detail refreshes preserve it. Run/replay
  mode guards the actions, and focused text/code editors retain native undo.
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
  Live run: capture the sink's last flushed `seq`, stream the durable JSONL
  through that boundary, then consume a 256-record subscriber queue. The
  synchronous cutoff/register step makes every record land in exactly the
  disk prefix or queue. Queue overflow collapses backlog to one resync signal;
  the same handler atomically captures a new cutoff and streams only the
  missing `last_sent_seq < seq <= cutoff` range before resuming live delivery.
  Each send has a five-second ceiling; a transport-blocked client closes
  `4410 resync_required`, and the UI resets/reconnects at most three times
  without falsely marking the run incomplete. Progressing ranges have no hard
  wall deadline; every individual send retains the five-second no-progress
  ceiling. At most eight WebSocket presentation readers attach per run. A
  filesystem reader lease excludes the exact unit from retention across
  server/CLI processes and remains for the five-second reconnect window after
  timeout. Lease release re-applies retention and reconciles the in-memory
  registry, so watched runs do not leave history permanently over its limit.
  Server closes normally after `run_finished`.
  Finished-run WebSockets validate the history envelope and stream the file
  without a full in-memory list; malformed/newer/unsupported format closes
  `4409`. Historical browser inspection uses the bounded REST pages above;
  opening the root or a completed child requests one event page and one
  direct-child-summary page, while explicit page controls continue from the
  cursor. The WebSocket remains the bounded live/catch-up transport.
  Unknown run: close `4404`; malformed run id/boundary: `4400`; rejected
  Host/Origin before accept: `4403`.
- **Run registry**: runs the server started, in memory — running entries keep
  scalar status/last-sequence state plus bounded subscriber queues, never a
  full event prefix (JSONL is the durable record); finished summaries are
  capped at 32 and retain counts/scalars rather than `RunResult` payloads.
  Browser folding keeps a 2,000-record event window and a 256-record pending
  batch while applying every record to graph-bounded aggregate state. Private
  `.active`/`.complete.json`/`.incomplete` plus dynamic `.reader-*` companions
  protect execution and distinguish retainable completion; retention runs
  after server-run finalization and removes exact whole-run companions.
  Server shutdown aborts running flows (clean JSONL prefix, EC20). Reports
  (`defaults.run.report`) are NOT written for
  server runs in current v0.2 — they stay a `napf run`/CI concern (D29 — the
  canvas gets full wire detail live
  over the WebSocket plus the JSONL history browser).
- **Static UI**: the pre-built bundle ships inside the wheel and is
  served at `/` with an SPA fallback (S4/M2, NFR-03). Canvas deep links
  live only below `/flow/`; API and `/assets/` retain their own namespaces.
  A raw checkout with no generated bundle serves an explanatory artifact-
  boundary placeholder directing users to a release wheel/sdist; direct VCS
  and raw-source package installs are unsupported (D40).

## Roadmap / reserved

- `codegen:` key — parsed, unused in current v0.x (design-constrained
  today; see PRODUCT.md).
- **Runtime secret redaction (D22)** — `set ... secret: true` or a
  response field-path redaction directive, so login-acquired tokens can
  opt into safe presentation/export. D35 intentionally preserves this raw
  local truth; declared-secret terminal/report views are implemented, but no
  absolute shareability guarantee is made. Export and runtime-secret policy
  are future work under D39.
- `napf check --write-env-example`.
