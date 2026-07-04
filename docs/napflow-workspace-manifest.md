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

codegen:                    # RESERVED: parsed, unused in prototype
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
3. **Env layering** — lookup order, last wins:
   profile file → process environment. Process env winning makes CI
   overrides trivial: `API_TOKEN=$CI_SECRET napf run flows/login`.
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
napf ui [--port]              serve editor + engine, open browser
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

Dropped for now: `napf sync` — with no registry, copied folders just appear
and broken references surface in `napf check` / on canvas. Possible later
nicety: `napf check --write-env-example` to regenerate a committed
`envs/example.env` from the union of all flows' `env.required`.

`napf check` is the CI pre-gate: fails fast on broken references before
anything executes.

## Roadmap / reserved

- `codegen:` key — parsed, unused in v1 (design-constrained today; see
  PRODUCT.md).
- **Runtime secret redaction (D22)** — `set ... secret: true` or a
  response field-path redaction directive, so login-acquired tokens can
  opt into masking. Deferred from v1; until then the shareability
  guarantee is scoped to declared secrets.
- `napf check --write-env-example`.
