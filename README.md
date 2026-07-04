# napflow

[![CI](https://github.com/nickosh/napflow/actions/workflows/ci.yml/badge.svg)](https://github.com/nickosh/napflow/actions/workflows/ci.yml)

Local-first, git-friendly, node-based flow editor and engine for complex
API request/response processing — think *"Postman Flows, but open,
file-based, Python-powered, and composable."* Built for QA and dev teams
who test APIs and want their flows reviewed, diffed, and run in CI like
any other code.

**Status: pre-alpha.** Stage S1 is complete — the file format, validator,
and CLI (`napf init` / `list` / `check`) work end to end on macOS,
Windows, and Linux. The execution engine (`napf run`) is in progress;
the visual canvas comes after. See [docs/PLAN.md](docs/PLAN.md).

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
5. **Full observability** — complete request/response detail (headers,
   bodies, timing, retries) captured in run history.

## What works today

| Command      | Status | Does |
|--------------|--------|------|
| `napf init`  | ✅     | scaffold a workspace (demo + offline smoke flow included) |
| `napf list`  | ✅     | discovered flows with their input/output ports |
| `napf check` | ✅     | validate everything — schema, edges, guards, references, env — with `file:line` diagnostics and CI exit codes |
| `napf run`   | 🚧 S2  | headless engine, JSONL run history, exit codes 0/1/2/130 |
| `napf ui`    | 🚧 S4  | local canvas editor (React Flow), served from the wheel |

## Try it

Not on PyPI yet — install from git (needs [uv](https://docs.astral.sh/uv/)):

```sh
uv tool install git+https://github.com/nickosh/napflow
napf init my-flows && cd my-flows
napf list
napf check
```

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

Conventional commits (`type(scope): subject`) — they feed the
[git-cliff](https://git-cliff.org) changelog.

## License

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
