# napflow

[![CI](https://github.com/nickosh/napflow/actions/workflows/ci.yml/badge.svg)](https://github.com/nickosh/napflow/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/napflow)](https://pypi.org/project/napflow/)

Local-first, git-friendly, node-based flow editor and engine for complex
API request/response processing — think *"Postman Flows, but open,
file-based, Python-powered, and composable."* Built for QA and dev teams
who test APIs and want their flows reviewed, diffed, and run in CI like
any other code.

**Status: v0.1.0 release; v0.2 development through M4 — developer preview.**
The first working milestone:
file format, validator, CLI, headless engine (full node catalog), and
the visual canvas (edit, run, inspect, history) work end to end on
macOS, Windows, and Linux. All `v0.x` formats—including the current
`schema: napflow/v1` marker—are experimental and may change before
v1.0. napflow assumes trusted workspaces (flows run real Python on
your machine) and is localhost-only. The streamlined v0.2
full-fidelity prototype plan is in [docs/PLAN.md](docs/PLAN.md)
(D33–D39).

## Why

1. **Git-friendly** — a flow is one YAML file + one `nodes.py` in one
   folder; diffs are small and reviewable, canvas layout never pollutes
   logic diffs.
2. **Composable** — any flow is usable as a node inside another flow
   (by reference, never copy). Every canvas is a flow.
3. **Python-native** — response parsing/logic are real Python functions,
   testable with pytest; the engine is importable
   (`from napflow.core import ...`).
4. **CI-first** — headless `napf run` with assert-driven exit codes is a
   first-class citizen, not an afterthought.
5. **Full observability** — request/response detail (headers, bodies,
   timing, retries) captured in run history; current v0.2 development stores
   repeated large values once in hash-verified blobs with no destructive
   capture limits and records the effective prepared request.

## What works today

| Command      | Status | Does |
|--------------|--------|------|
| `napf init`  | ✅     | scaffold a workspace (demo + offline smoke flow included) |
| `napf list`  | ✅     | discovered flows with their input/output ports |
| `napf check` | ✅     | validate everything — schema, edges, guards, references, env — with `file:line` diagnostics and CI exit codes |
| `napf run`   | ✅     | headless engine — full node catalog incl. python worker, full-value JSONL/blob history, prepared requests, exit codes 0/1/2/130 |
| `napf ui`    | ✅     | visual canvas on localhost — edit flows (autosaved through the canonical serializer, clean diffs), edit `nodes.py` in-browser, run with live animated events + full request/response detail, replay any run from history, drill into subflows, clone shared flows |

> **Raw history warning:** `.napflow/runs/` may contain complete request and
> response headers/bodies, cookies, credentials, bearer tokens, log values,
> and flow outputs. `napf init` gitignores `.napflow/`, but that is not
> sanitization or access control. Do not commit, upload, attach, publish, or
> otherwise share this directory unless you have inspected its contents.
> Terminal/report masking does not modify the canonical JSONL or blob files.

## Try it

Needs Python 3.12+; simplest with [uv](https://docs.astral.sh/uv/):

```sh
uv tool install napflow
napf init my-flows && cd my-flows
napf check
napf run flows/smoke   # headless, offline demo flow
napf ui                # opens the canvas in your browser
```

> Install from PyPI (above) or a GitHub release wheel. Direct
> `git+https` installs serve a placeholder UI — the bundle is build
> output, not in git; deterministic Git installs are planned (FR-1113).

## What a flow looks like

```yaml
schema: napflow/v1

flow:
  name: example

nodes:
  - id: start
    type: start
    config:
      ports:
        - {name: base_url, type: string, default: "{{ env.BASE_URL }}"}
  - id: get_echo
    type: request
    config: {url: "{{ inputs.base_url }}/get"}
  - id: verify
    type: assert
    config:
      checks:
        - {kind: status, equals: 200}
  - id: end
    type: end
    config: {ports: [{name: response}]}

edges:
  - {from: start.out, to: get_echo.trigger}
  - {from: get_echo.response, to: verify.in}
  - {from: verify.passed, to: end.response}
```

Message-driven, not a DAG: nodes fire when messages arrive, retry cycles
are legal (guarded by `counter`/`timeout` nodes), errors travel on
`error` ports as data, and an unproduced required output fails the run —
no false greens.

## Documentation

| Doc | What it pins |
|-----|--------------|
| [napflow-flow-schema.md](docs/napflow-flow-schema.md) | `flow.yaml` format + the full node catalog |
| [napflow-workspace-manifest.md](docs/napflow-workspace-manifest.md) | `napflow.yaml`, CLI surface, env model |
| [napflow-engine-spec.md](docs/napflow-engine-spec.md) | scheduler, frames, firing rules, events, check rules |
| [yaml-profile.md](docs/yaml-profile.md) | the canonical YAML read/emit profile |
| [DECISIONS.md](docs/DECISIONS.md) / [EDGE_CASES.md](docs/EDGE_CASES.md) | why, and what almost went wrong |
| [PLAN.md](docs/PLAN.md) / [JOURNAL.md](docs/JOURNAL.md) | roadmap + working journal |
| [RELEASING.md](docs/RELEASING.md) | versioning scheme + release flow |

## Development

```sh
uv sync
uv run pytest          # test suite
uv run ruff check      # lint
uv run lint-imports    # architecture contract: core imports nothing from cli/server
```

UI (dev-time only — users get the pre-built bundle inside the wheel):

```sh
cd ui
npm ci
npm run build          # bundle → src/napflow/server/static (what `napf ui` serves)
npm run dev            # Vite dev server, proxies /api + /ws to a running `napf ui`
npm run e2e            # Playwright against the built bundle (needs npm run build)
```

Conventional commits (`type(scope): subject`) — they feed the
[git-cliff](https://git-cliff.org) changelog.

## License

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
