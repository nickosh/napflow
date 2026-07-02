# `napflow.yaml` — Workspace Manifest, Draft v0.2

Changes from v0.1: env profiles auto-discovered (all .env gitignored);
`flows.main` entry flow; templating allowed in `defaults.request`;
`python:` key reserved.

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
  request:                  # templating allowed ({{ env.* }}, {{ run.* }})
    timeout_s: 30
    verify_tls: true
    retry:
      max_attempts: 1
    headers:
      User-Agent: "napflow/0.1 ({{ env.TEAM_TAG }})"
  run:
    history: 20             # runs kept per flow in .napflow/runs/
    report: junit           # none | junit | json
    message_budget: 10000   # per-run runaway protection
    node_timeout_s: 300     # default max_seconds per node firing
    body_capture_mb: 10     # per-body JSONL disk valve (full detail under cap)

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
3. **Env layering** — lookup order, last wins:
   profile file → process environment. Process env winning makes CI
   overrides trivial: `API_TOKEN=$CI_SECRET napf run flows/login`.
4. **Request defaults merge shallowly** — node-level `retry:` replaces the
   whole block; no deep-merge surprises.
5. **Secrets masking** is workspace-level; matching values become `•••`
   everywhere (UI, logs, stored runs).
6. **`napf` walks upward** from cwd to find `napflow.yaml` (like git);
   all manifest paths are workspace-relative.

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
  created  envs/dev.env                # BASE_URL=https://httpbin.org
  created  .gitignore                  # envs/*.env, .napflow/
  created  .napflow/
```

First-touch check: `napf run flows/example` must pass out of the box.

## CLI surface (v1)

```
napf init [dir]               scaffold workspace
napf ui [--port]              serve editor + engine, open browser
napf run <flow> [--env NAME]  headless run, exit code from asserts
     [-i key=value ...]       bind values to Start ports (validated, typed)
     [--input-json JSON]      structured inputs
                              End outputs → stdout as JSON; logs → stderr
napf list                     discovered flows + their Start/End ports
napf check                    validate all flows (schema, edges, env.required,
                              guard analysis of cycles, subflow-reference DAG)
```

Dropped for now: `napf sync` — with no registry, copied folders just appear
and broken references surface in `napf check` / on canvas. Possible later
nicety: `napf check --write-env-example` to regenerate a committed
`envs/example.env` from the union of all flows' `env.required`.

`napf check` is the CI pre-gate: fails fast on broken references before
anything executes.
