"""Workspace discovery: manifest walk-up, flow discovery, env profiles.

Authoritative spec: docs/napflow-workspace-manifest.md (FR-101/102/103).

- `napf` locates `napflow.yaml` by walking upward from cwd (like git).
- Any directory under `flows.root` containing `flow.yaml` is a flow;
  identity = workspace-relative POSIX path (stable across OS); nesting
  is free, including flows inside flow directories.
- Every `envs/*.env` file is a profile named by its filename stem — no
  registry. `layer_env` layers profile → process environment (FR-104).
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from napflow.core.loader import LoadedFlow, LoadedManifest, load_flow, load_manifest

MANIFEST_NAME = "napflow.yaml"
ENVS_DIR = "envs"
FLOW_FILE = "flow.yaml"

_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_WINDOWS_DRIVE_SEGMENT_RE = re.compile(r"^[A-Za-z]:")
_RUN_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")

SourceFilename = Literal["flow.yaml", "nodes.py"]


class WorkspaceNotFoundError(Exception):
    def __init__(self, start: Path):
        self.start = start
        super().__init__(f"no {MANIFEST_NAME} found in {start} or any parent directory")


class WorkspaceBoundaryError(ValueError):
    """A user- or workspace-supplied path crossed the selected workspace.

    ``reason`` is the stable preparation/API vocabulary required by D37.
    Missing files are deliberately not boundary errors: callers retain their
    existing not-found/E008 behavior after a safe path has been resolved.
    """

    reason = "workspace_boundary"

    def __init__(self, message: str):
        self.reason = type(self).reason
        super().__init__(message)


@dataclass(frozen=True)
class WorkspaceResolver:
    """The sole identity-to-path authority for one selected workspace.

    Identities are POSIX-style on every platform. Resolution is lexical first,
    then symlink-aware: the final candidate (including a missing destination's
    existing parents) is resolved and must remain beneath the canonical root.
    """

    root: Path
    flows_root_identity: str = "flows"
    flows_root: Path = field(init=False)
    flows_root_resolved: Path = field(init=False)
    runs_root: Path = field(init=False)

    def __post_init__(self) -> None:
        try:
            root = self.root.resolve(strict=False)
        except (OSError, RuntimeError) as e:
            raise WorkspaceBoundaryError(
                f"cannot resolve workspace root {self.root!s}: {e}"
            ) from e
        object.__setattr__(self, "root", root)
        flows_identity = self.normalize_identity(
            self.flows_root_identity, label="flows.root"
        )
        object.__setattr__(self, "flows_root_identity", flows_identity)
        object.__setattr__(
            self, "flows_root", self.root.joinpath(*flows_identity.split("/"))
        )
        object.__setattr__(
            self,
            "flows_root_resolved",
            self._resolve_normalized(flows_identity, label="flows.root"),
        )
        object.__setattr__(
            self,
            "runs_root",
            self._resolve_normalized(".napflow/runs", label="run-history root"),
        )

    def normalize_identity(self, value: str, *, label: str = "flow identity") -> str:
        """Validate and return one canonical relative POSIX identity.

        URL-reserved filename characters remain legal (spaces, ``#``, ``%``,
        ``?``); the browser transport encodes them per segment. Backslashes and
        Windows drive syntax are rejected even on POSIX so the boundary does
        not depend on the host running the check.
        """
        if not isinstance(value, str) or not value:
            raise WorkspaceBoundaryError(f"{label} must be a non-empty string")
        if value.startswith("/"):
            raise WorkspaceBoundaryError(f"{label} must be workspace-relative")
        if "\\" in value:
            raise WorkspaceBoundaryError(
                f"{label} must use POSIX '/' separators, not backslashes"
            )
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise WorkspaceBoundaryError(f"{label} contains a control character")
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise WorkspaceBoundaryError(
                f"{label} contains an invalid Unicode surrogate"
            )

        parts = value.split("/")
        if any(part == "" for part in parts):
            raise WorkspaceBoundaryError(f"{label} contains an empty path segment")
        if any(part in {".", ".."} for part in parts):
            raise WorkspaceBoundaryError(
                f"{label} contains a forbidden '.' or '..' path segment"
            )
        if any(_WINDOWS_DRIVE_SEGMENT_RE.match(part) for part in parts):
            raise WorkspaceBoundaryError(f"{label} contains Windows drive syntax")
        return "/".join(parts)

    def _resolve_normalized(self, value: str, *, label: str) -> Path:
        candidate = self.root.joinpath(*value.split("/"))
        try:
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError) as e:
            raise WorkspaceBoundaryError(
                f"cannot resolve {label} {value!r}: {e}"
            ) from e
        if not resolved.is_relative_to(self.root):
            raise WorkspaceBoundaryError(
                f"{label} {value!r} resolves outside workspace {self.root}"
            )
        return resolved

    def resolve_workspace_path(
        self, value: str, *, label: str = "workspace path"
    ) -> Path:
        normalized = self.normalize_identity(value, label=label)
        return self._resolve_normalized(normalized, label=label)

    def flow_dir(self, identity: str) -> Path:
        return self.resolve_workspace_path(identity, label="flow identity")

    def flow_file(self, identity: str) -> Path:
        return self.source_file(identity, FLOW_FILE)

    def source_file(self, identity: str, filename: SourceFilename) -> Path:
        if filename not in {FLOW_FILE, "nodes.py"}:
            raise WorkspaceBoundaryError(
                f"unsupported flow source filename {filename!r}"
            )
        normalized = self.normalize_identity(identity, label="flow identity")
        flow_dir = self._resolve_normalized(normalized, label="flow identity")
        source = self._resolve_normalized(
            f"{normalized}/{filename}", label="flow source"
        )
        expected = flow_dir / filename
        if source != expected:
            raise WorkspaceBoundaryError(
                f"flow source {normalized + '/' + filename!r} resolves outside "
                f"its canonical source path {expected}"
            )
        return source

    def fixture_file(self, value: str) -> Path:
        return self.resolve_workspace_path(value, label="fixture path")

    def runs_dir(self, identity: str) -> Path:
        normalized = self.normalize_identity(identity, label="flow identity")
        resolved = self._resolve_normalized(
            f".napflow/runs/{normalized}", label="run-history directory"
        )
        if not resolved.is_relative_to(self.runs_root):
            raise WorkspaceBoundaryError(
                f"run-history directory for {identity!r} resolves outside "
                f"{self.runs_root}"
            )
        return resolved

    def validate_run_id(self, run_id: str) -> str:
        if not isinstance(run_id, str) or _RUN_ID_RE.fullmatch(run_id) is None:
            raise WorkspaceBoundaryError(
                "run id must match YYYYmmdd-HHMMSS-xxxxxx (lowercase hex)"
            )
        return run_id

    def run_log(self, identity: str, run_id: str) -> Path:
        validated = self.validate_run_id(run_id)
        runs = self.runs_dir(identity)
        try:
            resolved = (runs / f"{validated}.jsonl").resolve(strict=False)
        except (OSError, RuntimeError) as e:
            raise WorkspaceBoundaryError(
                f"cannot resolve run log {validated!r}: {e}"
            ) from e
        expected = runs / f"{validated}.jsonl"
        if resolved != expected:
            raise WorkspaceBoundaryError(
                f"run log {validated!r} resolves outside its canonical path {expected}"
            )
        return resolved

    def clone_source(self, identity: str) -> Path:
        return self.flow_dir(identity)

    def clone_destination(self, identity: str) -> Path:
        normalized = self.normalize_identity(identity, label="clone destination")
        if normalized != self.flows_root_identity and not normalized.startswith(
            f"{self.flows_root_identity}/"
        ):
            raise WorkspaceBoundaryError(
                f"clone destination {identity!r} must be lexically under "
                f"{self.flows_root_identity!r}"
            )
        destination = self.flow_dir(normalized)
        if not destination.is_relative_to(self.flows_root_resolved):
            raise WorkspaceBoundaryError(
                f"clone destination {identity!r} must be under "
                f"{self.flows_root_identity!r}"
            )
        return destination


class EnvFileError(Exception):
    """A .env line outside the pinned dialect (EC36) — fail fast, like
    every other CI-gate input."""

    def __init__(self, path: Path, line_no: int, problem: str):
        self.path = path
        self.line_no = line_no
        super().__init__(f"{path}:{line_no}: {problem}")


def find_manifest(start: Path) -> Path | None:
    """Walk upward from `start` (file or directory) to the filesystem
    root, returning the first napflow.yaml found."""
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for directory in (current, *current.parents):
        candidate = directory / MANIFEST_NAME
        if candidate.is_file():
            return candidate
    return None


@dataclass(frozen=True)
class FlowRef:
    """A discovered flow. `identity` is the workspace-relative POSIX path
    of the flow directory (e.g. "flows/payments/refund")."""

    identity: str
    directory: Path
    file: Path


@dataclass(frozen=True)
class Workspace:
    root: Path  # the directory containing napflow.yaml
    manifest: LoadedManifest = field(repr=False)
    resolver: WorkspaceResolver = field(init=False, repr=False)

    def __post_init__(self) -> None:
        resolver = WorkspaceResolver(self.root, self.manifest.model.flows.root)
        object.__setattr__(self, "root", resolver.root)
        object.__setattr__(self, "resolver", resolver)

    @property
    def flows_root(self) -> Path:
        return self.resolver.flows_root

    def discover_flows(self) -> list[FlowRef]:
        """Every directory under flows.root containing a flow.yaml,
        sorted by identity for deterministic output (FR-102)."""
        refs = []
        if self.flows_root.is_dir():
            for file in self.flows_root.rglob(FLOW_FILE):
                directory = file.parent
                identity = directory.relative_to(self.root).as_posix()
                try:
                    safe_file = self.resolver.flow_file(identity)
                    safe_directory = self.resolver.flow_dir(identity)
                except WorkspaceBoundaryError:
                    # Discovery must never read an escaping symlink. An explicit
                    # entry/reference to it reports the stable boundary error.
                    continue
                if not safe_file.is_file():
                    continue
                refs.append(
                    FlowRef(
                        identity=identity,
                        directory=safe_directory,
                        file=safe_file,
                    )
                )
        return sorted(refs, key=lambda ref: ref.identity)

    def load_flow(self, identity: str) -> LoadedFlow:
        """Load a flow by its workspace-relative identity."""
        return load_flow(self.resolver.flow_file(identity))

    def env_profiles(self) -> dict[str, Path]:
        """Profile name (filename stem) → file path, sorted by name
        (FR-103). Drop a file in envs/, it appears — no registry."""
        envs = self.root / ENVS_DIR
        if not envs.is_dir():
            return {}
        return {p.stem: p for p in sorted(envs.glob("*.env"))}


def load_workspace(start: Path | None = None) -> Workspace:
    """Locate and load the workspace governing `start` (default: cwd)."""
    start = start if start is not None else Path.cwd()
    manifest_path = find_manifest(start)
    if manifest_path is None:
        raise WorkspaceNotFoundError(start)
    return Workspace(root=manifest_path.parent, manifest=load_manifest(manifest_path))


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse one profile in the pinned dialect (EC36): `KEY=VALUE` per
    line; full-line `#` comments and blank lines ignored; one matching
    pair of single/double quotes stripped from the value; values are
    literal — no `export`, no interpolation. Duplicate keys: last wins.
    Anything else is an EnvFileError with file:line."""
    values: dict[str, str] = {}
    with path.open(encoding="utf-8") as f:
        for line_no, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, eq, value = line.partition("=")
            if not eq:
                raise EnvFileError(path, line_no, f"expected KEY=VALUE, got {line!r}")
            key = key.strip()
            if not _ENV_KEY_RE.match(key):
                problem = f"invalid key {key!r} (no `export` prefix, [A-Za-z0-9_] only)"
                raise EnvFileError(path, line_no, problem)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            values[key] = value
    return values


def layer_env(
    profile: Mapping[str, str], process_env: Mapping[str, str] | None = None
) -> dict[str, str]:
    """WM §3 layering — lookup order, last wins: profile file → process
    environment. The WHOLE process environment participates: a key the
    profile never mentions is still visible as `{{ env.KEY }}`, which is
    what makes CI overrides trivial (`API_TOKEN=$CI_SECRET napf run …`).
    (FR-104)"""
    if process_env is None:
        process_env = os.environ
    return {**profile, **process_env}
