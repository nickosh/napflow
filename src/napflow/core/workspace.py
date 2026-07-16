"""Workspace discovery: manifest walk-up, flow discovery, env profiles.

Authoritative spec: docs/napflow-workspace-manifest.md (FR-101/102/103).

- `napf` locates `napflow.yaml` by walking upward from cwd (like git).
- Any directory under `flows.root` containing `flow.yaml` is a flow;
  identity = workspace-relative POSIX path (stable across OS); nesting
  is free, including flows inside flow directories.
- Environment profiles are discovered non-recursively below the configured
  root and keep their literal filenames as identities. `layer_env` layers
  profile → process environment (FR-104).
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import napflow
from napflow.core.loader import LoadedFlow, LoadedManifest, load_flow, load_manifest

MANIFEST_NAME = "napflow.yaml"
FLOW_FILE = "flow.yaml"

_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_WINDOWS_DRIVE_SEGMENT_RE = re.compile(r"^[A-Za-z]:")
_RUN_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")

SourceFilename = Literal["flow.yaml", "nodes.py"]
EnvProfileIssueReason = Literal[
    "workspace_boundary",
    "not_regular",
    "unreadable",
    "invalid_encoding",
    "invalid_format",
]


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
    environments_root_identity: str = "."
    data_root_identity: str = "data"
    flows_root: Path = field(init=False)
    flows_root_resolved: Path = field(init=False)
    environments_root: Path = field(init=False)
    environments_root_resolved: Path = field(init=False)
    data_root: Path = field(init=False)
    data_root_resolved: Path = field(init=False)
    runs_root: Path = field(init=False)

    def __post_init__(self) -> None:
        try:
            root = self.root.resolve(strict=False)
        except (OSError, RuntimeError) as e:
            raise WorkspaceBoundaryError(
                f"cannot resolve workspace root {self.root!s}: {e}"
            ) from e
        object.__setattr__(self, "root", root)
        if self.flows_root_identity in {".", "./"}:
            raise WorkspaceBoundaryError(
                "flows.root must resolve to a proper subdirectory of the workspace"
            )
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
        if self.flows_root_resolved == self.root:
            raise WorkspaceBoundaryError(
                "flows.root must resolve to a proper subdirectory of the workspace"
            )

        environments_identity = self.normalize_directory_root(
            self.environments_root_identity,
            label="environments.root",
            allow_workspace_root=True,
        )
        object.__setattr__(self, "environments_root_identity", environments_identity)
        environments_root = self._lexical_root(environments_identity)
        object.__setattr__(self, "environments_root", environments_root)
        object.__setattr__(
            self,
            "environments_root_resolved",
            self._resolve_directory_root(
                environments_identity, label="environments.root"
            ),
        )

        data_identity = self.normalize_directory_root(
            self.data_root_identity,
            label="data.root",
            allow_workspace_root=False,
        )
        object.__setattr__(self, "data_root_identity", data_identity)
        data_root = self._lexical_root(data_identity)
        object.__setattr__(self, "data_root", data_root)
        object.__setattr__(
            self,
            "data_root_resolved",
            self._resolve_directory_root(data_identity, label="data.root"),
        )
        if self.data_root_resolved == self.root:
            raise WorkspaceBoundaryError(
                "data.root must resolve to a proper subdirectory of the workspace"
            )
        object.__setattr__(
            self,
            "runs_root",
            self._resolve_normalized(".napflow/runs", label="run-history root"),
        )

    def normalize_directory_root(
        self,
        value: str,
        *,
        label: str,
        allow_workspace_root: bool,
    ) -> str:
        """Normalize a configurable data root.

        ``.`` and ``./`` are deliberate whole-value spellings for the
        workspace root. A dot segment embedded in any other path remains
        forbidden by :meth:`normalize_identity`.
        """
        if value in {".", "./"}:
            if allow_workspace_root:
                return "."
            raise WorkspaceBoundaryError(
                f"{label} must resolve to a proper subdirectory of the workspace"
            )
        return self.normalize_identity(value, label=label)

    def _lexical_root(self, identity: str) -> Path:
        if identity == ".":
            return self.root
        return self.root.joinpath(*identity.split("/"))

    def _resolve_directory_root(self, identity: str, *, label: str) -> Path:
        if identity == ".":
            return self.root
        return self._resolve_normalized(identity, label=label)

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
        return self._resolve_data_file(
            self.data_root_identity,
            self.data_root_resolved,
            value,
            label="fixture path",
        )

    def environment_file(self, name: str) -> Path:
        normalized = self.normalize_identity(name, label="env profile filename")
        if "/" in normalized:
            raise WorkspaceBoundaryError(
                "env profile filename must be one literal filename"
            )
        return self._resolve_data_file(
            self.environments_root_identity,
            self.environments_root_resolved,
            normalized,
            label="env profile",
        )

    def _resolve_data_file(
        self,
        root_identity: str,
        root_resolved: Path,
        value: str,
        *,
        label: str,
    ) -> Path:
        normalized = self.normalize_identity(value, label=label)
        combined = (
            normalized if root_identity == "." else f"{root_identity}/{normalized}"
        )
        resolved = self._resolve_normalized(combined, label=label)
        if not resolved.is_relative_to(root_resolved):
            raise WorkspaceBoundaryError(
                f"{label} {value!r} resolves outside configured root {root_identity!r}"
            )
        return resolved

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


@dataclass(frozen=True)
class EnvProfile:
    """One selectable, successfully parsed environment profile."""

    name: str
    path: Path
    values: dict[str, str]


@dataclass(frozen=True)
class EnvProfileIssue:
    """A filename-shaped candidate omitted from selectable profiles."""

    name: str
    path: Path
    reason: EnvProfileIssueReason
    message: str


@dataclass(frozen=True)
class EnvProfileDiscovery:
    """Fresh valid profiles and name-addressable skipped candidates."""

    profiles: dict[str, EnvProfile]
    issues: dict[str, EnvProfileIssue]


def _is_env_profile_filename(name: str) -> bool:
    """The deterministic, platform-independent F7 filename union."""
    return name == ".env" or name.startswith(".env.") or name.endswith(".env")


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
        manifest = self.manifest.model
        resolver = WorkspaceResolver(
            self.root,
            flows_root_identity=manifest.flows.root,
            environments_root_identity=manifest.environments.root,
            data_root_identity=manifest.data.root,
        )
        main = resolver.normalize_identity(manifest.flows.main, label="flows.main")
        if main != resolver.flows_root_identity and not main.startswith(
            f"{resolver.flows_root_identity}/"
        ):
            raise WorkspaceBoundaryError(
                f"flows.main {main!r} must be under flows.root "
                f"{resolver.flows_root_identity!r}"
            )
        if not resolver.flow_dir(main).is_relative_to(resolver.flows_root_resolved):
            raise WorkspaceBoundaryError(
                f"flows.main {main!r} resolves outside flows.root "
                f"{resolver.flows_root_identity!r}"
            )
        object.__setattr__(self, "root", resolver.root)
        object.__setattr__(self, "resolver", resolver)

    @property
    def flows_root(self) -> Path:
        return self.resolver.flows_root

    @property
    def environments_root(self) -> Path:
        return self.resolver.environments_root

    @property
    def data_root(self) -> Path:
        return self.resolver.data_root

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

    def discover(self) -> tuple["napflow.core.api.Flow", ...]:
        """Fresh runnable-flow discovery for the public embedding API.

        The workspace is reusable source/configuration, not a cached runtime
        session.  Each call observes the filesystem again and returns immutable
        handles that bind only this workspace and an exact identity (D38).
        """
        from napflow.core.api import Flow

        return tuple(Flow(self, ref.identity) for ref in self.discover_flows())

    def flow(self, identity: str) -> "napflow.core.api.Flow":
        """Bind an exact workspace-relative flow identity.

        Loading and checking deliberately remain per-run, so a reusable handle
        observes edits made after it was created.  Lookup only establishes that
        the exact canonical ``flow.yaml`` exists now; names are never normalized
        beyond the workspace's platform-independent identity grammar.
        """
        from napflow.core.api import Flow

        normalized = self.resolver.normalize_identity(identity)
        file = self.resolver.flow_file(normalized)
        if not file.is_file():
            raise KeyError(f"no flow at {normalized!r}")
        return Flow(self, normalized)

    @property
    def flows(self) -> "napflow.core.api.FlowCatalog":
        """Dynamic nested catalog below the configured ``flows.root``.

        Catalog access performs fresh discovery.  Attribute access is only an
        ergonomic view for exact identifier-safe segments; bracket and
        :meth:`flow` lookup retain every legal identity without normalization.
        """
        from napflow.core.api import FlowCatalog

        return FlowCatalog(self)

    def load_flow(self, identity: str) -> LoadedFlow:
        """Load a flow by its workspace-relative identity."""
        return load_flow(self.resolver.flow_file(identity))

    def discover_env_profiles(self) -> EnvProfileDiscovery:
        """Discover valid profiles and skipped candidates below the env root.

        Discovery is non-recursive and never invents a profile id: the exact
        filename is the id. Content validation is eager so every entry exposed
        to a caller is immediately usable; issues retain enough detail for
        listing/check surfaces and for hard errors on explicit selection.
        """
        root = self.environments_root
        if not root.is_dir():
            return EnvProfileDiscovery({}, {})
        try:
            candidates = sorted(root.iterdir(), key=lambda path: path.name)
        except OSError:
            # A root has no candidate filename to key an issue by. Callers see
            # an empty discovery; configured-root diagnostics belong at the
            # workspace/check surface rather than masquerading as a profile.
            return EnvProfileDiscovery({}, {})

        profiles: dict[str, EnvProfile] = {}
        issues: dict[str, EnvProfileIssue] = {}
        for candidate in candidates:
            name = candidate.name
            if not _is_env_profile_filename(name):
                continue
            try:
                path = self.resolver.environment_file(name)
            except WorkspaceBoundaryError as error:
                issues[name] = EnvProfileIssue(
                    name,
                    candidate,
                    "workspace_boundary",
                    str(error),
                )
                continue
            try:
                is_file = path.is_file()
            except OSError as error:
                issues[name] = EnvProfileIssue(
                    name,
                    path,
                    "unreadable",
                    f"could not inspect env profile: {error}",
                )
                continue
            if not is_file:
                issues[name] = EnvProfileIssue(
                    name,
                    path,
                    "not_regular",
                    "env profile candidate is not a regular file",
                )
                continue
            try:
                values = parse_env_file(path)
            except EnvFileError as error:
                issues[name] = EnvProfileIssue(
                    name,
                    path,
                    "invalid_format",
                    str(error),
                )
            except UnicodeDecodeError as error:
                issues[name] = EnvProfileIssue(
                    name,
                    path,
                    "invalid_encoding",
                    f"env profile is not valid UTF-8: {error}",
                )
            except OSError as error:
                issues[name] = EnvProfileIssue(
                    name,
                    path,
                    "unreadable",
                    f"could not read env profile: {error}",
                )
            else:
                profiles[name] = EnvProfile(name, path, values)
        return EnvProfileDiscovery(profiles, issues)

    def env_profiles(self) -> dict[str, Path]:
        """Compatibility view: literal profile filename → valid file path."""
        return {
            name: profile.path
            for name, profile in self.discover_env_profiles().profiles.items()
        }


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
