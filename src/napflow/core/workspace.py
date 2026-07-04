"""Workspace discovery: manifest walk-up, flow discovery, env profiles.

Authoritative spec: docs/napflow-workspace-manifest.md (FR-101/102/103).

- `napf` locates `napflow.yaml` by walking upward from cwd (like git).
- Any directory under `flows.root` containing `flow.yaml` is a flow;
  identity = workspace-relative POSIX path (stable across OS); nesting
  is free, including flows inside flow directories.
- Every `envs/*.env` file is a profile named by its filename stem — no
  registry. Layering (profile → process env) is engine territory (S2);
  this module only discovers and parses.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from napflow.core.loader import LoadedFlow, LoadedManifest, load_flow, load_manifest

MANIFEST_NAME = "napflow.yaml"
ENVS_DIR = "envs"
FLOW_FILE = "flow.yaml"

_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class WorkspaceNotFoundError(Exception):
    def __init__(self, start: Path):
        self.start = start
        super().__init__(f"no {MANIFEST_NAME} found in {start} or any parent directory")


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

    @property
    def flows_root(self) -> Path:
        return self.root / self.manifest.model.flows.root

    def discover_flows(self) -> list[FlowRef]:
        """Every directory under flows.root containing a flow.yaml,
        sorted by identity for deterministic output (FR-102)."""
        refs = []
        if self.flows_root.is_dir():
            for file in self.flows_root.rglob(FLOW_FILE):
                if not file.is_file():
                    continue
                directory = file.parent
                identity = directory.relative_to(self.root).as_posix()
                refs.append(FlowRef(identity=identity, directory=directory, file=file))
        return sorted(refs, key=lambda ref: ref.identity)

    def load_flow(self, identity: str) -> LoadedFlow:
        """Load a flow by its workspace-relative identity."""
        return load_flow(self.root / Path(identity) / FLOW_FILE)

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
