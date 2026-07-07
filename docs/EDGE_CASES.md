# napflow — Edge Cases: Resolution Ledger

All 41 cases logged to date are **resolved**: EC01–EC23 from the
2026-06-14 spec review, EC24–EC37 from the 2026-07-02 finalization and
senior-review rounds, EC38–EC41 from S4 implementation (server + UI
canvas). This ledger records what each case was and where
its resolution now lives; the full original problem analyses for
EC01–EC23 are preserved in `archive/EDGE_CASES.md`. New edge cases found
during implementation should be appended here with the same format.

Resolution kinds: **ADR** (a `DECISIONS.md` entry), **fix** (spec text
corrected), **doc** (behavior was acceptable but needed an explicit
statement).

| ID   | Was                                                          | Resolution |
|------|--------------------------------------------------------------|------------|
| EC01 | Unreached declared End port → run reported `passed` (false green) | ADR **D18**: End ports `required` by default; unwritten required port at quiescence ⇒ `failed`, exit 1. Schema *Start/End rules*, engine §2. |
| EC02 | Unconnected `exhausted`/`expired` semantics self-contradictory | ADR **D19**: guard exhaustion ports are ordinary pass-through outputs, not error ports; unconnected = dropped + lint **W106**; "gave up" fails via D18 or a wired assert. Schema *Wire format* + *Guards*; example wires `attempts.exhausted`. |
| EC03 | `merge: all` in a cycle with one re-firing input → stall     | doc: `all` is a one-shot rendezvous (slots clear on emit); use `any` to rejoin retry paths. Schema *Merge*, engine §4. |
| EC04 | Multi-input node with a never-arriving input → silent skip   | doc: "skipped" is a first-class outcome; escalate to failure via required End ports (D18). Engine §4. |
| EC05 | Cross-frame aggregation of asserts/errors unstated           | ADR **D20**: outcomes aggregate run-wide (worst state anywhere in the frame tree); only data is frame-isolated. Engine §2. |
| EC06 | Loop `on_error`/`errors`: "iteration error" undefined        | ADR **D20**: iteration error = body frame ending `failed`/`error`; `on_error` only gates further scheduling; failures always score. Engine §5, schema *Loop*. |
| EC07 | `flow` node had no error port                                | ADR **D21**: implicit `error` port carrying `{state, failed_asserts, unhandled_errors}`; name `error` reserved on End ports & python outputs (**E012**). Engine §5, schema *Flow node*. |
| EC08 | Empty/degenerate seed → pump deadlocks (no QUIESCENT)        | fix: finalize immediately when post-seed `in_flight == 0`. Engine §3; rationale folded into D14. |
| EC09 | Python worker concurrency unspecified; stuck firing blocks   | doc: worker is serial per flow module; `mode: parallel` gains no CPU parallelism through python nodes; stuck firing blocks until `max_seconds` kill + respawn. Engine §5a. |
| EC10 | Runtime-acquired secrets unmasked; masking algo unspecified  | ADR **D22**: substring scan of declared secret values (≥5 chars), active profile + process env; runtime tokens stored in full (stated honestly); runtime redaction → roadmap. Engine §7, manifest rule 5. |
| EC11 | Check-code numbering: E010 missing, W105 misplaced           | fix: E010 marked retired/reserved; W105 moved into the W-block; CLAUDE.md corrected to E001–E009, E011–E012, W101–W107. Engine §8. |
| EC12 | Templating asymmetry: `trigger` envelope vs `nodes.*` value  | doc: `trigger` = full `{value, meta}` envelope; `nodes.<id>.<port>` = unwrapped value; prefer `trigger`. Engine §6, schema *Templating*; example switched to `trigger.value.body.state`. |
| EC13 | request `error` vs `response` for non-2xx unstated           | doc: non-2xx = valid response on `response`; `error` = transport failures only (connection/DNS/TLS/timeout-after-retries). Engine §5, schema *Request*. |
| EC14 | `napf check` import-vs-AST of `nodes.py` undefined           | decided: `check` AST-parses (no import side effects); the worker imports for real at run time; dynamic signatures invisible to check. Engine §8. |
| EC15 | `loop` input port + `over` evaluation, `start.out` payload   | doc: loop fires on `trigger`, `over` evaluated against that delivery; `start.out` carries the frame's full `inputs` dict. Schema catalog notes, engine §4/§5. |
| EC16 | Counter off-by-one; W101 cycle-vs-SCC check scope            | decided: counter is check-then-decrement — `count: N` = exactly N `continue` passes, message N+1 exhausts. W101 gives the strict guarantee (every simple cycle guarded) in linear time: delete guard nodes, test acyclicity — a remaining cycle is exactly a guard-free cycle. Schema *Guards*, engine §8. |
| EC17 | Env validation split: W105 (check) vs ENV (run)              | doc: W105 warns when a key is in *no* profile at check time; ENV errors when missing from the *active* profile at run time — by design. Engine §2/§8. |
| EC18 | `nodes.*` is last-writer-wins under cycles/concurrency       | doc: stated; prefer `{{ trigger }}` for the firing value. Engine §6. |
| EC19 | Set/Get reintroduce ordering hazards wires avoid             | doc: Set-before-Get holds only when a path exists from the Set to the Get's `trigger`; frame variables are not a synchronization primitive. Schema *Scoping*. |
| EC20 | Abort mid-request leaves dangling `request_started` in JSONL | doc: replay tolerates a dangling start. Engine §7. |
| EC21 | Node-id charset constraints unspecified                      | decided: ids match `[A-Za-z_][A-Za-z0-9_]*`; E011 enforces charset + uniqueness. Schema *Node ids*. |
| EC22 | Grandchild processes from python nodes not killed            | doc: known v1 limitation; process-group kill a later candidate. Engine §5a. |
| EC23 | `defaults.request` may only reference `env`/`run`            | doc: `inputs`/`nodes` are frame-scoped and would StrictUndefined-error every inheriting request. Manifest rule 4. |
| EC24 | Nodes without an error port: where do evaluation errors go? (found 2026-07-02) | decided: evaluation/template failures on port-less-error nodes (condition, switch, merge, guards, set/get, delay, log, fixture) are unhandled node errors — recorded in the report, run `failed`. Engine §6. |
| EC25 | `max_seconds` referenced by engine §5a but never documented in the flow schema (found 2026-07-02) | ADR **D24**: universal optional node key; documented in schema *Execution timeouts* with per-node routing (`{error_kind: "timeout"}` on the error port, never a data port). |
| EC26 | Default 300s ceiling applied to container/self-bounded nodes — healthy long loops/subflows killed at 5 min (found 2026-07-02) | ADR **D24**: the default auto-applies to `request`/`python` only; `delay`/`loop`/`flow` exempt from the default (bounded by config/children), explicit `max_seconds` honored; container timeout routing defined (flow → implicit error port; loop → EC24, wrap-in-subflow to branch). |
| EC27 | No wall-clock run deadline — runs bounded only at budget × ceiling; a CI SIGKILL loses the report (found 2026-07-02) | ADR **D24**: `defaults.run.run_timeout_s` (null = off) + `napf run --timeout`; expiry ⇒ run `error` (exit 2), `error_reason: run_timeout`, report/JSONL written. Engine §3. |
| EC28 | Worker protocol self-corrupting: user `print()` writes to stdout — the JSON-lines channel — while the spec claimed stderr carries prints (2026-07-02 senior review) | fix: worker `dup()`s the real stdout fd for protocol lines at startup, rebinds `sys.stdout`/`sys.stderr` to capture streams forwarded as log events. Engine §5a. |
| EC29 | Round-trip + diagnostics architecture unstated: re-dumping Pydantic models would delete comments; diagnostics had no file:line (2026-07-02 review) | decided: the loaded CommentedMap is the single write source (models = read-only views); ruamel source marks threaded through validation; every E/W carries file:line + node id + fix hint. `yaml-profile.md` *Write path*, engine §0/§8. |
| EC30 | Flagship example had no create step and polled `{{ run.id }}` — napflow's own run id, not an API job id (2026-07-02 review) | fix: `create` POST node added; poll URL reads `{{ nodes.create.response.body.id }}` (also demos ghost-wires). Schema example. |
| EC31 | Message budget default 10000 trips legitimate data-driven loops (~1k-row fixture × small body ⇒ false red) (2026-07-02 review) | fix: default raised to 100000 — runaway protection, not resource accounting; a tight unguarded cycle still dies in well under a second. Manifest, engine §3, schema *Guards*. |
| EC32 | 10MB valve is per *body* — one big loop can write gigabytes of JSONL per run, ×`history: 20` (2026-07-02 review) | fix: run-level valve `defaults.run.run_capture_mb` (500); excess bodies truncated with marker, `capture_warning` at 10% left. Engine §7, manifest. |
| EC33 | Windows: asyncio subprocess pipes need the Proactor loop; ASGI/WS stacks sometimes force Selector — python workers would break under `napf ui` only (2026-07-02 review) | doc + test: requirement stated in engine §5a; Windows integration test runs a python-node flow through the server (TR-9). |
| EC34 | First-touch check depended on httpbin.org — offline/proxy/flaky ⇒ broken first impression and flaky CI (2026-07-02 review) | fix: `napf init` adds offline `flows/smoke` (fixture→python→assert) as the first-touch check; httpbin example kept as the HTTP demo. Manifest. |
| EC35 | Trust model undocumented: running a workspace executes its `nodes.py`; Jinja sandbox ≠ security boundary; sync render can stall the loop (2026-07-02 review) | doc: engine §11 *Security & trust model* — flows are code, review like code; localhost-only server; accepted risks stated. |
| EC36 | Bundle of small underspecs: `.env` dialect; same-node firing overlap; `parallel` loop `results` ordering; Start `default:` scope; python optional-vs-required params under AST (2026-07-02 review) | doc: dotenv-style dialect, no interpolation (manifest §2); async firings may overlap, sync serialized (engine §4); `results` index-ordered (engine §5, schema); Start defaults see env/run at BIND (engine §6, schema); literal defaults = optional (schema). |
| EC37 | `{{ }}` always renders strings — structured bodies impossible (`body:` would emit Python-repr'd dicts, not JSON) (2026-07-02 review) | ADR **D25**: a config value that is exactly one `{{ expr }}` evaluates natively; mixed content renders to string; field schema type applies post-evaluation. Engine §6, schema *Templating*. |
| EC38 | Write endpoints take a flow identity as a path tail — a crafted identity (`..`, absolute path, drive-letter colon) could write outside the workspace (S4/M4) | fix: `_safe_identity` guard rejects them with 400 before any filesystem touch (`server/app.py`); `test_write_endpoints_reject_path_escapes`. Manifest *Server surface*. |
| EC39 | Flow-detail GET 400'd on check E-codes — a mid-edit flow (e.g. transient E004) locked the canvas, so the editor couldn't fix its own in-progress state; broke the M3 e2e when changed (S4/M4) | fix: check diagnostics never 400 the detail endpoint (flows stay editable, E-codes render as diagnostics); only unloadable files 400; E-codes still gate *runs*. Manifest *Server surface*. |
| EC40 | Rejecting a nodes.py save on syntax errors would hold code hostage over a missing colon — no way to save work-in-progress (S4/M4) | doc: PUT `/api/code/*` always saves (last-write-wins); the syntax error is AST-detected and reported in the response + inline in the editor, never blocks. Manifest *Server surface*. |
| EC41 | An edge wired to a port the node's surface doesn't declare (merge growth, broken python ref ⇒ `null` surface) would orphan visually on the canvas (S4/M3) | fix: the canvas grows an `any`-typed handle for wired-but-undeclared ports — edges never dangle; the checker still owns correctness. Manifest *Server surface* (`ports` payload pin). |

Related watch items (not defects): see *Known open risks* in
`DECISIONS.md` — notably W103 chattiness and D18 `required: false`
boilerplate.
