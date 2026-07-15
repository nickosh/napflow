"""`napf init` workspace scaffold (FR-107, WM §napf init output).

YAML files are written through the one canonical serializer
(`core.loader.save_document`) — the scaffold IS canonical output. The
scaffolded workspace must pass `napf check` with zero diagnostics and
`napf run flows/smoke` must pass offline once the S3 node set lands
(EC34: first-touch check).
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml.comments import CommentedMap

from napflow.core.files import atomic_create_text, atomic_write_text
from napflow.core.gitmeta import (
    GITIGNORE,
    GitMetadataAppendError,
    GitMetadataInspection,
    GitMetadataRules,
    GitMetadataState,
    append_git_metadata,
    default_git_metadata_rules,
    inspect_git_metadata,
)
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

GITIGNORE_PREAMBLE = (
    "# napflow: real env profiles stay local; run history is per-machine\n"
)


@dataclass(frozen=True)
class ScaffoldResult:
    """One scaffold entry plus optional existing-metadata inspection."""

    relative_path: str
    status: str
    metadata: GitMetadataInspection | None = None


GitMetadataDecision = Callable[[GitMetadataInspection], bool]


def _new_metadata_content(rules: GitMetadataRules) -> str:
    preamble = GITIGNORE_PREAMBLE if rules.filename == GITIGNORE else ""
    return preamble + "\n".join(rules.required_rules) + "\n"


def _manifest(name: str) -> dict:
    environments = CommentedMap({"default": "dev", "secrets": []})
    environments.yaml_set_comment_before_after_key(
        "secrets",
        before=(
            "Opt in to terminal/report masking by adding env-name patterns below,\n"
            'for example: ["API_TOKEN", "*_PASSWORD"].\n'
            "Raw local history and the local UI remain unmasked."
        ),
    )
    return {
        "schema": "napflow/v1",
        "workspace": {"name": name, "description": "API flows workspace."},
        "flows": {"root": "flows", "main": "flows/main"},
        "environments": environments,
    }


def scaffold_workspace(
    directory: Path,
    *,
    check_git_metadata: bool = True,
    decide_git_metadata: GitMetadataDecision | None = None,
) -> list[ScaffoldResult]:
    """Create the FR-107 scaffold without overwriting user content.

    Existing root Git metadata is inspected before any scaffold write so an
    interactive caller can collect every append decision safely. Missing files
    retain the greenfield behavior and are created even when inspection is
    disabled. Existing metadata is appended only through an explicit decision.
    """
    metadata_rules = default_git_metadata_rules()
    metadata_preflight: dict[str, GitMetadataInspection] = {}
    metadata_decisions: dict[str, bool] = {}
    if check_git_metadata:
        for rules in metadata_rules:
            inspection = inspect_git_metadata(directory, rules)
            metadata_preflight[rules.filename] = inspection
            if (
                inspection.state is GitMetadataState.NEEDS_APPEND
                and decide_git_metadata is not None
            ):
                # All callbacks run before napflow.yaml or any other scaffold
                # file is created. EOF/Ctrl-C therefore cannot strand an init
                # that the existing-manifest refusal would make unretryable.
                metadata_decisions[rules.filename] = decide_git_metadata(inspection)

    # Apply metadata decisions before creating napflow.yaml. A metadata I/O
    # failure therefore cannot make init refuse its own retry, and exclusive
    # creation never replaces a path that appears after preflight.
    directory.mkdir(parents=True, exist_ok=True)
    metadata_results: list[ScaffoldResult] = []
    for rules in metadata_rules:
        rel = rules.filename
        path = directory / rel
        content = _new_metadata_content(rules)

        if not check_git_metadata:
            created = atomic_create_text(path, content)
            metadata_results.append(
                ScaffoldResult(rel, "created" if created else "exists")
            )
            continue

        inspection = inspect_git_metadata(directory, rules)
        if inspection.state is GitMetadataState.MISSING:
            if atomic_create_text(path, content):
                metadata_results.append(ScaffoldResult(rel, "created"))
                continue
            inspection = inspect_git_metadata(directory, rules)

        if inspection.state is GitMetadataState.COVERED:
            metadata_results.append(ScaffoldResult(rel, "exists", inspection))
            continue

        prompted = metadata_preflight.get(rel)
        if (
            inspection.state is GitMetadataState.NEEDS_APPEND
            and prompted is not None
            and metadata_decisions.get(rel, False)
        ):
            try:
                inspection = append_git_metadata(prompted)
            except GitMetadataAppendError:
                inspection = inspect_git_metadata(directory, rules)
            else:
                metadata_results.append(ScaffoldResult(rel, "appended", inspection))
                continue

            # The owner may have changed the file after the prompt. Respect
            # that new state instead of applying the stale decision.
            if inspection.state is GitMetadataState.MISSING:
                if atomic_create_text(path, content):
                    metadata_results.append(ScaffoldResult(rel, "created"))
                    continue
                inspection = inspect_git_metadata(directory, rules)
            if inspection.state is GitMetadataState.COVERED:
                metadata_results.append(ScaffoldResult(rel, "exists", inspection))
                continue

        status = (
            "exists"
            if not inspection.missing_rules
            and inspection.state is GitMetadataState.NON_LF
            else "skipped"
        )
        metadata_results.append(ScaffoldResult(rel, status, inspection))

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
    }

    results: list[ScaffoldResult] = []
    for rel, doc in yaml_files.items():
        path = directory / rel
        if path.exists():
            results.append(ScaffoldResult(rel, "exists"))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        save_document(doc, path)  # the ONE canonical serializer (FR-204)
        results.append(ScaffoldResult(rel, "created"))
    for rel, content in text_files.items():
        path = directory / rel
        if path.exists():
            results.append(ScaffoldResult(rel, "exists"))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, content)
        results.append(ScaffoldResult(rel, "created"))

    results.extend(metadata_results)

    (directory / ".napflow").mkdir(parents=True, exist_ok=True)
    results.append(ScaffoldResult(".napflow/", "created"))
    return results
