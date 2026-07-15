# napflow

[![CI](https://github.com/nickosh/napflow/actions/workflows/ci.yml/badge.svg)](https://github.com/nickosh/napflow/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/napflow)](https://pypi.org/project/napflow/)

Local-first, git-friendly, node-based flow editor and engine for complex
API request/response processing — think *"Postman Flows, but open,
file-based, Python-powered, and composable."* Built for QA and dev teams
who test APIs and want their flows reviewed, diffed, and run in CI like
any other code.

**Status: v0.2.0 — usable full-fidelity developer preview.**
The file format, validator, CLI, headless engine (full node catalog), and
the visual canvas (edit, run, inspect, history) work end to end on
macOS, Windows, and Linux. All `v0.x` formats—including the current
`schema: napflow/v1` marker—are experimental and may change before
v1.0. napflow assumes trusted workspaces (flows run real Python on
your machine) and is localhost-only. The v0.2 implementation, reusable
release gate, compatibility audit, and prepared-artifact dry-run are complete.
See the
[v0.2 compatibility notes](docs/release-notes-v0.2.0.md) and
[plan](docs/PLAN.md) (D33–D40).

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
   timing, retries) captured in run history; v0.2 stores
   repeated large values once in hash-verified blobs with no destructive
   capture limits and records the effective prepared request.

## What works today

| Command      | Status | Does |
|--------------|--------|------|
| `napf init`  | ✅     | scaffold a workspace; consent-based root metadata handling for brownfield dirs |
| `napf list`  | ✅     | discovered flows with their input/output ports |
| `napf check` | ✅     | validate everything — schema, edges, guards, references, env — with path-specific diagnostics and CI exit codes |
| `napf run`   | ✅     | headless engine — full node catalog incl. python worker, full-value JSONL/blob history, prepared requests, exit codes 0/1/2/130 |
| `napf ui`    | ✅     | visual canvas on localhost — edit flows (autosaved through the canonical serializer, clean diffs), edit `nodes.py` in-browser, run with live animated events + full request/response detail, replay bounded history pages with lazy blob detail, drill into completed frame canvases, clone shared flows |

> **Raw history warning:** `.napflow/runs/` may contain complete request and
> response headers/bodies, cookies, credentials, bearer tokens, log values,
> and flow outputs. Greenfield `napf init` creates the root ignore rule;
> interactive brownfield init asks before appending to an existing LF
> `.gitignore` (or accepts explicit `--git-meta append`) and leaves
> skipped/CRLF files unchanged. `napf check` warns with advisory W109,
> but Git metadata is not sanitization or access control. Do not commit,
> upload, attach, publish, or otherwise share this directory unless inspected.
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

> Install from PyPI or a GitHub release artifact (wheel or sdist). Direct
> VCS (`git+https`) installs and PEP 517 builds from a raw source checkout are
> unsupported: the generated UI bundle is deliberately not committed. Release
> artifacts contain the pre-built bundle; users never need Node.

## Use it from pytest

The functional and workspace-bound forms create independent runs through the
same core path—without importing the CLI or server:

```python
from pathlib import Path

from napflow.core import load_workspace, run_flow

workspace = load_workspace(Path("."))
bound_result = workspace.flows.smoke.run(history=False)
functional_result = run_flow(workspace, "flows/smoke", history=False)

assert bound_result.state == functional_result.state == "passed"
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

UI (dev-time only — users get the pre-built bundle inside the wheel):

```sh
cd ui
npm ci
npm run build          # bundle → src/napflow/server/static (what `napf ui` serves)
npm run dev            # Vite dev server, proxies /api + /ws to a running `napf ui`
npm run e2e            # Playwright against the built bundle (needs npm run build)
```

Contributor builds from a checkout must build the UI before packaging. This
is a development workflow, not a supported installation path; published
release sdists already contain the generated bundle and build wheels without
Node.

Conventional commits (`type(scope): subject`) — they feed the
[git-cliff](https://git-cliff.org) changelog.

## License

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
