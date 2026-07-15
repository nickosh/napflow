"""`napf init` workspace scaffold (FR-107, WM §napf init output).

YAML files are emitted through the one canonical serializer
(`core.loader.emit_document`) — the scaffold IS canonical output. The
minimal scaffold is ready for real work with one empty main flow. The
offline smoke flow and HTTP demo are creation-time opt-in examples.
"""

import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml.comments import CommentedMap

from napflow.core.files import atomic_create_text
from napflow.core.gitmeta import (
    GITIGNORE,
    GitMetadataAppendError,
    GitMetadataInspection,
    GitMetadataRules,
    GitMetadataState,
    append_git_metadata,
    default_git_metadata_rules,
    example_git_metadata_rules,
    inspect_git_metadata,
)
from napflow.core.loader import emit_document
from napflow.core.workspace import WorkspaceResolver

MAIN_FLOW = {
    "schema": "napflow/v1",
    "flow": {"name": "main", "description": "Default canvas — every canvas is a flow."},
    "nodes": [
        {"id": "start", "type": "start"},
        {"id": "end", "type": "end"},
    ],
    "edges": [],
    "layout": {"start": [40, 120], "end": [260, 120]},
}

EXAMPLE_MAIN_FLOW = {
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
        "offline first-touch check is the smoke flow).",
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
        {"id": "users", "type": "fixture", "config": {"file": "smoke.json"}},
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
"""Python node functions for the offline smoke example."""


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
# local profile - gitignored; teammates copy .env.example to get started
BASE_URL=https://httpbin.org
"""

EXAMPLE_ENV = """\
# committed onboarding template - copy to .env and fill in real values
BASE_URL=https://httpbin.org
"""

GITIGNORE_PREAMBLE = "# napflow: local runtime data stays local\n"


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


def _manifest(
    name: str,
    *,
    example: bool,
    flows_root: str,
    data_root: str,
    environments_root: str,
) -> dict:
    environment_values: dict[str, object] = {"root": environments_root}
    if example:
        environment_values["default"] = ".env"
    environment_values["secrets"] = []
    environments = CommentedMap(environment_values)
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
        "flows": {"root": flows_root, "main": f"{flows_root}/main"},
        "data": {"root": data_root},
        "environments": environments,
    }


def _required_directories(
    resolver: WorkspaceResolver, *, example: bool
) -> tuple[Path, ...]:
    paths = [
        resolver.root,
        resolver.flows_root,
        resolver.flows_root / "main",
        resolver.data_root,
        resolver.root / ".napflow",
    ]
    if resolver.environments_root != resolver.root:
        paths.append(resolver.environments_root)
    if example:
        paths.extend([resolver.flows_root / "example", resolver.flows_root / "smoke"])
    return tuple(paths)


def _relative_label(root: Path, path: Path) -> str:
    return "." if path == root else path.relative_to(root).as_posix()


def _preflight_scaffold_plan(
    root: Path,
    required_directories: tuple[Path, ...],
    scaffold_files: tuple[Path, ...],
    metadata_files: tuple[Path, ...],
) -> None:
    """Validate every planned file/directory role before the first write.

    Brownfield scaffold entries remain untouched. Root Git metadata keeps F6's
    separate consent/inspection policy; both still participate in the in-memory
    role plan so a configured root cannot occupy a planned file location such
    as ``flow.yaml``, ``.gitignore``, or ``.gitattributes``.
    """

    manifest_path = root / "napflow.yaml"
    try:
        manifest_path.lstat()
    except FileNotFoundError:
        pass
    else:
        raise FileExistsError(f"{manifest_path} already exists")

    planned_files = sorted(
        {*scaffold_files, *metadata_files}, key=lambda path: (len(path.parts), path)
    )
    planned_directories = sorted(
        set(required_directories), key=lambda path: (len(path.parts), path)
    )

    for directory in planned_directories:
        if directory == root:
            continue
        for file in planned_files:
            if directory == file or directory.is_relative_to(file):
                raise OSError(
                    f"planned directory {_relative_label(root, directory)!r} "
                    f"conflicts with planned file {_relative_label(root, file)!r}"
                )

    for index, file in enumerate(planned_files):
        for other in planned_files[index + 1 :]:
            if file.is_relative_to(other) or other.is_relative_to(file):
                raise OSError(
                    f"planned file {_relative_label(root, file)!r} conflicts "
                    f"with planned file {_relative_label(root, other)!r}"
                )

    # Existing brownfield source files are preserved byte-for-byte, but a
    # non-regular path cannot fulfill a scaffold file role. Keep root Git
    # metadata out of this check: D43 deliberately inspects and warns about
    # symlinks/directories/other unsafe metadata paths without replacing them.
    for path in scaffold_files:
        if path == manifest_path:
            continue
        try:
            path_stat = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(path_stat.st_mode):
            if stat.S_ISLNK(path_stat.st_mode):
                kind = "symlink"
            elif stat.S_ISDIR(path_stat.st_mode):
                kind = "directory"
            else:
                kind = "non-regular path"
            raise OSError(
                f"planned scaffold file {_relative_label(root, path)!r} is a {kind}"
            )

    candidates = {root}
    for path in required_directories:
        relative = path.relative_to(root)
        for index in range(1, len(relative.parts) + 1):
            candidates.add(root.joinpath(*relative.parts[:index]))

    for path in sorted(candidates, key=lambda candidate: len(candidate.parts)):
        label = _relative_label(root, path)
        if path.is_symlink():
            raise OSError(f"required directory {label!r} is a symlink")
        if path.is_junction():
            raise OSError(f"required directory {label!r} is a junction")
        if path.exists() and not path.is_dir():
            raise NotADirectoryError(f"required directory {label!r} is not a directory")


def scaffold_workspace(
    directory: Path,
    *,
    example: bool = False,
    flows_root: str = "flows",
    data_root: str = "data",
    environments_root: str = ".",
    check_git_metadata: bool = True,
    decide_git_metadata: GitMetadataDecision | None = None,
) -> list[ScaffoldResult]:
    """Create the minimal scaffold, optionally including runnable examples.

    Existing root Git metadata is inspected before any scaffold write so an
    interactive caller can collect every append decision safely. Missing files
    retain the greenfield behavior and are created even when inspection is
    disabled. Existing metadata is appended only through an explicit decision.
    """
    resolver = WorkspaceResolver(
        directory,
        flows_root_identity=flows_root,
        data_root_identity=data_root,
        environments_root_identity=environments_root,
    )
    directory = resolver.root
    metadata_rules = (
        example_git_metadata_rules(resolver.environments_root_identity)
        if example
        else default_git_metadata_rules()
    )
    flows_identity = resolver.flows_root_identity
    data_identity = resolver.data_root_identity
    environments_identity = resolver.environments_root_identity
    environment_prefix = (
        "" if environments_identity == "." else f"{environments_identity}/"
    )

    yaml_files: dict[str, dict] = {
        "napflow.yaml": _manifest(
            directory.name,
            example=example,
            flows_root=flows_identity,
            data_root=data_identity,
            environments_root=environments_identity,
        ),
        f"{flows_identity}/main/flow.yaml": (
            EXAMPLE_MAIN_FLOW if example else MAIN_FLOW
        ),
    }
    text_files: dict[str, str] = {
        f"{flows_identity}/main/nodes.py": EMPTY_NODES_PY,
    }
    if example:
        yaml_files.update(
            {
                f"{flows_identity}/example/flow.yaml": EXAMPLE_FLOW,
                f"{flows_identity}/smoke/flow.yaml": SMOKE_FLOW,
            }
        )
        text_files.update(
            {
                f"{flows_identity}/example/nodes.py": EMPTY_NODES_PY,
                f"{flows_identity}/smoke/nodes.py": SMOKE_NODES_PY,
                f"{data_identity}/smoke.json": SMOKE_FIXTURE,
                f"{environment_prefix}.env": DEV_ENV,
                f"{environment_prefix}.env.example": EXAMPLE_ENV,
            }
        )

    required_directories = _required_directories(resolver, example=example)
    scaffold_files = tuple(
        directory / relative for relative in (*yaml_files, *text_files)
    )
    metadata_files = tuple(directory / rules.filename for rules in metadata_rules)
    _preflight_scaffold_plan(
        directory,
        required_directories,
        scaffold_files,
        metadata_files,
    )

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

    directory_existed = {
        path: path.is_dir()
        for path in {
            resolver.data_root,
            resolver.environments_root,
            resolver.root / ".napflow",
        }
    }
    for path in required_directories:
        path.mkdir(parents=True, exist_ok=True)

    results: list[ScaffoldResult] = []
    for rel, doc in yaml_files.items():
        path = directory / rel
        created = atomic_create_text(
            path,
            emit_document(doc),  # the ONE canonical serializer (FR-204)
        )
        results.append(ScaffoldResult(rel, "created" if created else "exists"))
    for rel, content in text_files.items():
        path = directory / rel
        created = atomic_create_text(path, content)
        results.append(ScaffoldResult(rel, "created" if created else "exists"))

    results.append(
        ScaffoldResult(
            f"{data_identity}/",
            "exists" if directory_existed[resolver.data_root] else "created",
        )
    )
    if resolver.environments_root != resolver.root:
        results.append(
            ScaffoldResult(
                f"{environments_identity}/",
                "exists"
                if directory_existed[resolver.environments_root]
                else "created",
            )
        )

    results.extend(metadata_results)

    results.append(
        ScaffoldResult(
            ".napflow/",
            "exists" if directory_existed[resolver.root / ".napflow"] else "created",
        )
    )
    return results
