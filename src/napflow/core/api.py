"""Public Python embedding surface (D38 / FR-1112).

``Workspace`` and ``Flow`` are immutable, reusable source handles.  Runtime
state begins only inside ``run_flow_async``: every call repeats preparation and
constructs a new ``FlowRun``, event stream, history unit, frames, sessions, and
workers.  The synchronous API is a small ``asyncio.run`` adapter over that one
async implementation.
"""

from __future__ import annotations

import asyncio
import keyword
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from napflow.core.engine import RunResult, execute_flow
from napflow.core.events import EventStream, SecretMasker, new_run_id
from napflow.core.runprep import (
    OpenedRun,
    finalize_run_history,
    open_run_stream,
    prepare_run,
)
from napflow.core.workspace import Workspace


@dataclass(frozen=True, slots=True)
class Flow:
    """An immutable exact flow handle bound to one reusable workspace."""

    workspace: Workspace
    identity: str

    def __post_init__(self) -> None:
        normalized = self.workspace.resolver.normalize_identity(self.identity)
        if not self.workspace.resolver.flow_file(normalized).is_file():
            raise KeyError(f"no flow at {normalized!r}")
        object.__setattr__(self, "identity", normalized)

    @property
    def directory(self) -> Path:
        """Canonical flow directory, resolved when accessed."""
        return self.workspace.resolver.flow_dir(self.identity)

    @property
    def file(self) -> Path:
        """Canonical ``flow.yaml`` path, resolved when accessed."""
        return self.workspace.resolver.flow_file(self.identity)

    def run(
        self,
        *,
        inputs: Mapping[str, Any] | None = None,
        env: str | None = None,
        env_overrides: Mapping[str, str] | None = None,
        timeout: float | None = None,
        history: bool = True,
    ) -> RunResult:
        """Execute a fresh isolated run synchronously."""
        return run_flow(
            self.workspace,
            self.identity,
            inputs=inputs,
            env=env,
            env_overrides=env_overrides,
            timeout=timeout,
            history=history,
        )

    async def run_async(
        self,
        *,
        inputs: Mapping[str, Any] | None = None,
        env: str | None = None,
        env_overrides: Mapping[str, str] | None = None,
        timeout: float | None = None,
        history: bool = True,
    ) -> RunResult:
        """Execute a fresh isolated run on the caller's event loop."""
        return await run_flow_async(
            self.workspace,
            self.identity,
            inputs=inputs,
            env=env,
            env_overrides=env_overrides,
            timeout=timeout,
            history=history,
        )

    def __getitem__(self, identity: str) -> Flow | FlowNamespace:
        """Exact child lookup when an attribute would collide with a member."""
        return _catalog_lookup(self.workspace, self.identity, identity)

    def __getattr__(self, segment: str) -> Flow | FlowNamespace:
        return _catalog_attribute(self, self.workspace, self.identity, segment)

    def __dir__(self) -> list[str]:
        return _catalog_dir(self, self.workspace, self.identity)


@dataclass(frozen=True, slots=True)
class FlowNamespace:
    """A non-runnable catalog directory that contains discovered flows."""

    workspace: Workspace
    identity: str

    def __getitem__(self, identity: str) -> Flow | FlowNamespace:
        return _catalog_lookup(self.workspace, self.identity, identity)

    def __getattr__(self, segment: str) -> Flow | FlowNamespace:
        return _catalog_attribute(self, self.workspace, self.identity, segment)

    def __dir__(self) -> list[str]:
        return _catalog_dir(self, self.workspace, self.identity)


@dataclass(frozen=True, slots=True)
class FlowCatalog:
    """Fresh runtime catalog rooted below ``workspace.flows_root``.

    ``catalog.foo.bar`` is available only for exact attribute-safe segments.
    ``catalog["foo/bar"]`` is always relative to the configured flows root;
    ``workspace.flow("flows/foo/bar")`` is the workspace-relative exact form.
    Together they cover every legal name and member collision without an
    ambiguous prefix heuristic.
    """

    workspace: Workspace

    @property
    def _prefix(self) -> str:
        return self.workspace.resolver.flows_root_identity

    def __getitem__(self, identity: str) -> Flow | FlowNamespace:
        return _catalog_lookup(self.workspace, self._prefix, identity)

    def __getattr__(self, segment: str) -> Flow | FlowNamespace:
        return _catalog_attribute(self, self.workspace, self._prefix, segment)

    def __dir__(self) -> list[str]:
        return _catalog_dir(self, self.workspace, self._prefix)


def _catalog_identities(workspace: Workspace) -> tuple[str, ...]:
    return tuple(ref.identity for ref in workspace.discover_flows())


def _catalog_lookup(
    workspace: Workspace, prefix: str, relative: str
) -> Flow | FlowNamespace:
    if not isinstance(relative, str) or not relative:
        raise KeyError(relative)
    if relative.startswith("/"):
        raise KeyError(relative)
    candidate = f"{prefix}/{relative}"
    try:
        candidate = workspace.resolver.normalize_identity(candidate)
    except ValueError as error:
        raise KeyError(relative) from error
    identities = _catalog_identities(workspace)
    if candidate in identities:
        return Flow(workspace, candidate)
    if any(identity.startswith(f"{candidate}/") for identity in identities):
        return FlowNamespace(workspace, candidate)
    raise KeyError(relative)


def _catalog_attribute(
    owner: object, workspace: Workspace, prefix: str, segment: str
) -> Flow | FlowNamespace:
    if not _attribute_safe(owner, segment):
        raise AttributeError(segment)
    try:
        return _catalog_lookup(workspace, prefix, segment)
    except KeyError as error:
        raise AttributeError(segment) from error


def _attribute_safe(owner: object, segment: str) -> bool:
    return (
        segment.isidentifier()
        and not keyword.iskeyword(segment)
        and segment not in _static_members(owner)
    )


def _static_members(owner: object) -> set[str]:
    """Class-defined names without invoking dynamic ``__getattr__``."""
    return {name for cls in type(owner).__mro__ for name in vars(cls)}


def _catalog_dir(owner: object, workspace: Workspace, prefix: str) -> list[str]:
    members = _static_members(owner)
    prefix_parts = prefix.split("/")
    for identity in _catalog_identities(workspace):
        parts = identity.split("/")
        if parts[: len(prefix_parts)] != prefix_parts or len(parts) <= len(
            prefix_parts
        ):
            continue
        segment = parts[len(prefix_parts)]
        if _attribute_safe(owner, segment):
            members.add(segment)
    return sorted(members)


def run_flow(
    workspace: Workspace,
    identity: str,
    *,
    inputs: Mapping[str, Any] | None = None,
    env: str | None = None,
    env_overrides: Mapping[str, str] | None = None,
    timeout: float | None = None,
    history: bool = True,
) -> RunResult:
    """Run an exact flow identity synchronously in a fresh isolated runtime.

    Use :func:`run_flow_async` (or ``Flow.run_async``) from an active event
    loop; nesting ``asyncio.run`` is deliberately rejected.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError(
            "run_flow() cannot be called from an active event loop; "
            "await run_flow_async() instead"
        )
    return asyncio.run(
        run_flow_async(
            workspace,
            identity,
            inputs=inputs,
            env=env,
            env_overrides=env_overrides,
            timeout=timeout,
            history=history,
        )
    )


async def run_flow_async(
    workspace: Workspace,
    identity: str,
    *,
    inputs: Mapping[str, Any] | None = None,
    env: str | None = None,
    env_overrides: Mapping[str, str] | None = None,
    timeout: float | None = None,
    history: bool = True,
) -> RunResult:
    """Run an exact flow identity asynchronously in a fresh isolated runtime."""
    if not isinstance(workspace, Workspace):
        raise TypeError("workspace must be a napflow.core Workspace")
    prepared = prepare_run(workspace, identity, env)
    if env_overrides is not None:
        overrides = dict(env_overrides)
        if any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in overrides.items()
        ):
            raise TypeError("env_overrides must map strings to strings")
        prepared = replace(prepared, env={**prepared.env, **overrides})

    opened: OpenedRun | None = None
    if history:
        opened = open_run_stream(workspace, prepared)
        stream = opened.stream
    else:
        masker = SecretMasker(
            workspace.manifest.model.environments.secrets, prepared.env
        )
        stream = EventStream(new_run_id(), masker, [])

    completed = False
    stream_closed = False
    try:
        result = await execute_flow(
            prepared.loaded.model,
            flow_identity=prepared.identity,
            manifest=workspace.manifest.model,
            env=prepared.env,
            env_name=prepared.env_name,
            inputs=dict(inputs or {}),
            stream=stream,
            run_timeout_s=timeout,
            flow_dir=workspace.resolver.flow_dir(prepared.identity),
            workspace_root=workspace.root,
            workspace_resolver=workspace.resolver,
        )
        completed = True
        return result
    finally:
        try:
            stream.close()
            stream_closed = True
        except Exception:
            # EventStream remembers sink failures while FlowRun preserves the
            # completed result/cancellation contract.  An unpublishable unit is
            # abandoned below, matching the CLI/server lifecycle policy.
            pass
        if opened is not None:
            try:
                finalize_run_history(opened, completed=completed and stream_closed)
            except Exception:
                if completed and stream_closed:
                    with suppress(Exception):
                        finalize_run_history(opened, completed=False)


__all__ = [
    "Flow",
    "FlowCatalog",
    "FlowNamespace",
    "run_flow",
    "run_flow_async",
]
