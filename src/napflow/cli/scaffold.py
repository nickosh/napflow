"""`napf init` workspace scaffold (FR-107, WM §napf init output).

YAML files are written through the one canonical serializer
(`core.loader.save_document`) — the scaffold IS canonical output. The
scaffolded workspace must pass `napf check` with zero diagnostics and
`napf run flows/smoke` must pass offline once the S3 node set lands
(EC34: first-touch check).
"""

from pathlib import Path

from napflow.core.loader import save_document

MAIN_FLOW = {
    "schema": "napflow/v1",
    "flow": {"name": "main", "description": "Default canvas — every canvas is a flow."},
    "nodes": [
        {"id": "start", "type": "start"},
        {"id": "end", "type": "end", "config": {"ports": [{"name": "done"}]}},
    ],
    "edges": [{"from": "start.out", "to": "end.done"}],
    "layout": {"start": [40, 120], "end": [260, 120]},
}

EXAMPLE_FLOW = {
    "schema": "napflow/v1",
    "flow": {
        "name": "example",
        "description": "HTTP demo against httpbin — network required (the "
        "offline first-touch check is flows/smoke).",
    },
    "env": {"required": ["BASE_URL"]},
    "nodes": [
        {
            "id": "start",
            "type": "start",
            "config": {
                "ports": [
                    {
                        "name": "base_url",
                        "type": "string",
                        "default": "{{ env.BASE_URL }}",
                    }
                ]
            },
        },
        {
            "id": "get_echo",
            "type": "request",
            "config": {
                "url": "{{ inputs.base_url }}/get",
                "query": {"demo": "napflow"},
            },
        },
        {
            "id": "verify",
            "type": "assert",
            "config": {
                "checks": [
                    {"kind": "status", "equals": 200},
                    {
                        "kind": "expr",
                        "expr": "trigger.value.body.args.demo",
                        "op": "equals",
                        "value": "napflow",
                    },
                    {"kind": "response_time", "under_ms": 5000},
                ]
            },
        },
        {
            "id": "transport_log",
            "type": "log",
            "config": {"label": "transport error", "level": "error"},
        },
        {
            "id": "end",
            "type": "end",
            "config": {
                "ports": [
                    {"name": "response"},
                    {"name": "failed_check", "required": False},
                ]
            },
        },
    ],
    "edges": [
        {"from": "start.out", "to": "get_echo.trigger"},
        {"from": "get_echo.response", "to": "verify.in"},
        {"from": "verify.passed", "to": "end.response"},
        {"from": "verify.failed", "to": "end.failed_check"},
        {"from": "get_echo.error", "to": "transport_log.in"},
    ],
    "layout": {
        "start": [40, 160],
        "get_echo": [240, 160],
        "verify": [460, 160],
        "transport_log": [240, 320],
        "end": [680, 160],
    },
}

SMOKE_FLOW = {
    "schema": "napflow/v1",
    "flow": {
        "name": "smoke",
        "description": "First-touch check: fixture→python→assert, fully "
        "offline (EC34).",
    },
    "nodes": [
        # E006: every flow declares exactly one start/end; this flow is
        # fixture-driven, so start carries no ports and no edges
        {"id": "start", "type": "start"},
        {"id": "users", "type": "fixture", "config": {"file": "fixtures/smoke.json"}},
        {
            "id": "summarize",
            "type": "python",
            "config": {"function": "summarize", "outputs": ["summary"]},
        },
        {
            "id": "verify",
            "type": "assert",
            "config": {
                "checks": [
                    {
                        "kind": "expr",
                        "expr": "trigger.value.total",
                        "op": "gt",
                        "value": 0,
                    },
                    {
                        "kind": "expr",
                        "expr": "trigger.value.names",
                        "op": "contains",
                        "value": "Ada",
                    },
                ]
            },
        },
        {
            "id": "end",
            "type": "end",
            "config": {
                "ports": [
                    {"name": "summary"},
                    {"name": "failed_check", "required": False},
                    {"name": "python_error", "required": False},
                ]
            },
        },
    ],
    "edges": [
        {"from": "users.value", "to": "summarize.users"},
        {"from": "summarize.summary", "to": "verify.in"},
        {"from": "verify.passed", "to": "end.summary"},
        {"from": "verify.failed", "to": "end.failed_check"},
        {"from": "summarize.error", "to": "end.python_error"},
    ],
    "layout": {
        "start": [40, 40],
        "users": [40, 160],
        "summarize": [260, 160],
        "verify": [480, 160],
        "end": [700, 160],
    },
}

EMPTY_NODES_PY = '''\
"""Python node functions for this flow.

Functions see ONLY their declared inputs (wire or template values in
explicitly) and return a dict keyed by their declared outputs. Inputs
and outputs must be JSON-serializable. `napf check` reads signatures
via AST — write literal `def`s, not dynamically built ones.
"""
'''

SMOKE_NODES_PY = '''\
"""Python node functions for flows/smoke (offline first-touch check)."""


def summarize(users):
    return {
        "summary": {
            "total": len(users),
            "names": [user["name"] for user in users],
        }
    }
'''

SMOKE_FIXTURE = """\
[
  {"name": "Ada", "role": "qa"},
  {"name": "Linus", "role": "dev"}
]
"""

DEV_ENV = """\
# local profile - gitignored; teammates copy example.env to get started
BASE_URL=https://httpbin.org
"""

EXAMPLE_ENV = """\
# committed onboarding template - copy to dev.env and fill in real values
BASE_URL=https://httpbin.org
"""

GITIGNORE = """\
# napflow: real env profiles stay local; run history is per-machine
envs/*.env
!envs/example.env
.napflow/
"""

GITATTRIBUTES = """\
*.yaml text eol=lf
*.yml text eol=lf
"""


def _manifest(name: str) -> dict:
    return {
        "schema": "napflow/v1",
        "workspace": {"name": name, "description": "API flows workspace."},
        "flows": {"root": "flows", "main": "flows/main"},
        "environments": {"default": "dev", "secrets": ["API_TOKEN", "*_PASSWORD"]},
    }


def scaffold_workspace(directory: Path) -> list[tuple[str, str]]:
    """Create the FR-107 scaffold; returns (relative path, status) per
    entry, status in {"created", "exists"}. Never overwrites."""
    yaml_files: dict[str, dict] = {
        "napflow.yaml": _manifest(directory.resolve().name),
        "flows/main/flow.yaml": MAIN_FLOW,
        "flows/example/flow.yaml": EXAMPLE_FLOW,
        "flows/smoke/flow.yaml": SMOKE_FLOW,
    }
    text_files: dict[str, str] = {
        "flows/main/nodes.py": EMPTY_NODES_PY,
        "flows/example/nodes.py": EMPTY_NODES_PY,
        "flows/smoke/nodes.py": SMOKE_NODES_PY,
        "fixtures/smoke.json": SMOKE_FIXTURE,
        "envs/dev.env": DEV_ENV,
        "envs/example.env": EXAMPLE_ENV,
        ".gitignore": GITIGNORE,
        ".gitattributes": GITATTRIBUTES,
    }

    results = []
    for rel, doc in yaml_files.items():
        path = directory / rel
        if path.exists():
            results.append((rel, "exists"))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        save_document(doc, path)  # the ONE canonical serializer (FR-204)
        results.append((rel, "created"))
    for rel, content in text_files.items():
        path = directory / rel
        if path.exists():
            results.append((rel, "exists"))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        results.append((rel, "created"))

    (directory / ".napflow").mkdir(parents=True, exist_ok=True)
    results.append((".napflow/", "created"))
    return results
