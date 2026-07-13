"""Run preparation shared by `napf run` and the server (S4/M1).

CLI and server are both thin adapters over core; the LOAD/CHECK gate,
env-profile resolution, and event-stream wiring are run SEMANTICS
(pinned in WM), so they live here once — the adapters only present the
outcome (exit codes there, HTTP statuses here).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from napflow.core.checker import (
    CheckDiagnostic,
    check_run_closure,
    diagnostics_from_load_error,
)
from napflow.core.events import (
    EventStream,
    JsonlSink,
    RunHistoryUnit,
    SecretMasker,
    abandon_run_history,
    begin_run_history,
    complete_run_history,
    new_run_id,
)
from napflow.core.loader import LoadedFlow, LoadError, load_flow
from napflow.core.workspace import (
    EnvFileError,
    Workspace,
    WorkspaceBoundaryError,
    layer_env,
    parse_env_file,
)

PrepFailure = Literal[
    "flow_not_found",
    "workspace_boundary",
    "load",
    "check",
    "env_not_found",
    "env_invalid",
]


class RunPrepError(Exception):
    """The run cannot start. `reason` is machine-readable — the CLI
    maps every reason to exit 2, the server to 404 (flow_not_found) or
    400. For `load`/`check`, `diagnostics` carries the E/W details."""

    def __init__(
        self,
        reason: PrepFailure,
        message: str,
        diagnostics: Iterable[CheckDiagnostic] = (),
    ):
        super().__init__(message)
        self.reason: PrepFailure = reason
        self.diagnostics = list(diagnostics)


@dataclass(frozen=True)
class PreparedRun:
    """Everything a FlowRun needs, minus the caller's sinks. Reaching
    this object means the gate passed: `diagnostics` are warnings only."""

    identity: str
    loaded: LoadedFlow
    diagnostics: list[CheckDiagnostic]
    env: dict[str, str]  # layered: active profile → process env (FR-104)
    env_name: str | None
    notes: list[str]  # operator-facing, e.g. missing default profile


def prepare_run(workspace: Workspace, flow: str, env: str | None = None) -> PreparedRun:
    """LOAD + CHECK + ENV (EN §2): E-codes block, warnings proceed. The
    gate covers the entry flow plus its reference closure (E007) — a
    broken subflow blocks like a broken entry. An explicit `env` must
    exist; the manifest default is best-effort (profiles are gitignored
    — fresh clones have none)."""
    try:
        identity = workspace.resolver.normalize_identity(flow)
        file = workspace.resolver.flow_file(identity)
    except WorkspaceBoundaryError as e:
        raise RunPrepError("workspace_boundary", str(e)) from e
    if not file.is_file():
        known = ", ".join(r.identity for r in workspace.discover_flows()) or "none"
        raise RunPrepError(
            "flow_not_found", f"no flow at {identity!r} (discovered: {known})"
        )

    try:
        loaded = load_flow(file)
    except LoadError as e:
        raise RunPrepError("load", str(e), diagnostics_from_load_error(e)) from e
    diagnostics = check_run_closure(loaded, identity, workspace)
    boundary = next((d for d in diagnostics if d.reason == "workspace_boundary"), None)
    if boundary is not None:
        raise RunPrepError(
            "workspace_boundary",
            f"{identity}: {boundary.message}",
            diagnostics,
        )
    if any(d.severity == "error" for d in diagnostics):
        raise RunPrepError("check", f"{identity}: check errors", diagnostics)

    profiles = workspace.env_profiles()
    env_name = env if env is not None else workspace.manifest.model.environments.default
    profile_values: dict[str, str] = {}
    notes: list[str] = []
    if env is not None and env not in profiles:
        available = ", ".join(profiles) or "none"
        raise RunPrepError(
            "env_not_found",
            f"--env {env!r}: no envs/{env}.env (available: {available})",
        )
    if env_name in profiles:
        try:
            profile_values = parse_env_file(profiles[env_name])
        except EnvFileError as e:
            raise RunPrepError("env_invalid", str(e)) from e
    elif env_name is not None:
        notes.append(f"note: env profile {env_name!r} not found — process env only")
        env_name = None

    return PreparedRun(
        identity=identity,
        loaded=loaded,
        diagnostics=diagnostics,
        env=layer_env(profile_values),
        env_name=env_name,
        notes=notes,
    )


@dataclass(frozen=True)
class OpenedRun:
    run_id: str
    log_path: Path
    stream: EventStream
    history_unit: RunHistoryUnit
    history_limit: int


def finalize_run_history(opened: OpenedRun, *, completed: bool) -> list[Path]:
    """Publish/retain a complete unit or preserve a valid incomplete prefix."""
    if completed:
        return complete_run_history(opened.history_unit, opened.history_limit)
    abandon_run_history(opened.history_unit)
    return []


def open_run_stream(
    workspace: Workspace, prepared: PreparedRun, *, extra_sinks: Iterable[Any] = ()
) -> OpenedRun:
    """Open JSONL + active lifecycle marker + masker wiring.

    Retention runs only through ``finalize_run_history`` after execution and
    adapter-owned reports have finished. Extra sinks receive the same
    born-masked records (D13).
    """
    manifest = workspace.manifest.model
    run_id = new_run_id()
    log_path = workspace.resolver.run_log(prepared.identity, run_id)
    jsonl = JsonlSink(log_path)
    try:
        history_unit = begin_run_history(log_path, run_id)
    except BaseException:
        jsonl.close()
        log_path.unlink(missing_ok=True)
        raise
    stream = EventStream(
        run_id,
        SecretMasker(manifest.environments.secrets, prepared.env),
        [jsonl, *extra_sinks],
    )
    return OpenedRun(
        run_id=run_id,
        log_path=log_path,
        stream=stream,
        history_unit=history_unit,
        history_limit=manifest.defaults.run.history,
    )
