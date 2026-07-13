"""Pydantic models for `napflow.yaml` — the workspace manifest (FR-101).

Authoritative spec: docs/napflow-workspace-manifest.md (v0.3). Field
defaults here ARE the documented built-in defaults — one source of truth.
"""

from typing import Annotated, Any, Literal

from pydantic import Field

from napflow.core.models.common import (
    FrozenModel,
    Scalar,
    TemplatableBool,
    TemplatableNumber,
)
from napflow.core.models.flow import RetryConfig


class WorkspaceInfo(FrozenModel):
    name: str
    description: str | None = None


class FlowsConfig(FrozenModel):
    root: str = "flows"
    main: str = "flows/main"  # canvas the UI opens by default


class EnvironmentsConfig(FrozenModel):
    """Profiles are auto-discovered from envs/*.env (FR-103) — no registry.
    `secrets` are glob patterns over env var NAMES; matching values are
    redacted from terminal/report views while raw local history remains
    raw (D35)."""

    default: str | None = None
    secrets: list[str] = []


class RequestDefaults(FrozenModel):
    """Merged shallowly into request-node configs (node keys win; `retry:`
    replaces the whole block). Templates here see only env.*/run.* (EC23)."""

    timeout_s: TemplatableNumber = 30
    verify_tls: TemplatableBool = True
    retry: RetryConfig = RetryConfig()
    headers: dict[str, Scalar] = {}


class RunDefaults(FrozenModel):
    history: Annotated[int, Field(ge=1)] = 20
    report: Literal["none", "junit", "json"] = "none"
    # Runaway protection, NOT resource accounting — counts every emitted
    # message run-wide including child frames (EC31).
    message_budget: Annotated[int, Field(ge=1)] = 100_000
    # Default max_seconds per firing — auto-applies to request/python only;
    # delay/loop/flow are exempt from the DEFAULT (D24).
    node_timeout_s: Annotated[float, Field(gt=0)] = 300
    # Wall-clock run deadline; None = off. Expiry ⇒ run `error`, exit 2,
    # report still written (D24).
    run_timeout_s: Annotated[float, Field(gt=0)] | None = None
    body_capture_mb: Annotated[float, Field(gt=0)] = 10  # per-body valve
    run_capture_mb: Annotated[float, Field(gt=0)] = 500  # per-run valve (EC32)


class Defaults(FrozenModel):
    request: RequestDefaults = RequestDefaults()
    run: RunDefaults = RunDefaults()


class PythonSettings(FrozenModel):
    """`interpreter` = python executable for the nodes.py worker
    subprocess; None = napflow's own interpreter (FR-108)."""

    interpreter: str | None = None


class Manifest(FrozenModel):
    """One `napflow.yaml`, located by walking upward from cwd (FR-101)."""

    schema_: Literal["napflow/v1"] = Field(alias="schema")
    workspace: WorkspaceInfo | None = None
    flows: FlowsConfig = FlowsConfig()
    environments: EnvironmentsConfig = EnvironmentsConfig()
    defaults: Defaults = Defaults()
    python: PythonSettings = PythonSettings()
    codegen: Any = None  # RESERVED: parsed, unused in v1 (FR-109)
