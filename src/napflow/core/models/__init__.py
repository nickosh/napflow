"""Validated read-only models for flow files and the workspace manifest.

JSON Schema (Draft 2020-12) is the interchange type authority (FR-206):
`flow_json_schema()` / `manifest_json_schema()` export what the Python
side validates natively via Pydantic and the canvas validates via ajv.
"""

from typing import Any

from napflow.core.models.common import (
    IDENT_PATTERN,
    PORT_REF_PATTERN,
    FrozenModel,
    PortType,
)
from napflow.core.models.flow import (
    AssertCheck,
    AssertConfig,
    AssertNode,
    ConditionConfig,
    ConditionNode,
    CounterConfig,
    CounterNode,
    DelayConfig,
    DelayNode,
    Edge,
    EndConfig,
    EndNode,
    EndPort,
    ExprCheck,
    FixtureConfig,
    FixtureNode,
    FlowEnv,
    FlowFile,
    FlowMeta,
    FlowNode,
    FlowRefConfig,
    GetConfig,
    GetNode,
    LogConfig,
    LogNode,
    LoopConfig,
    LoopNode,
    MergeConfig,
    MergeNode,
    Node,
    NodeBase,
    NoteConfig,
    NoteNode,
    PythonConfig,
    PythonNode,
    RequestConfig,
    RequestNode,
    ResponseTimeCheck,
    RetryConfig,
    SetConfig,
    SetNode,
    StartConfig,
    StartNode,
    StartPort,
    StatusCheck,
    SwitchCase,
    SwitchConfig,
    SwitchNode,
    TimeoutConfig,
    TimeoutNode,
)
from napflow.core.models.manifest import (
    Defaults,
    EnvironmentsConfig,
    FlowsConfig,
    Manifest,
    PythonSettings,
    RequestDefaults,
    RunDefaults,
    WorkspaceInfo,
)

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def flow_json_schema() -> dict[str, Any]:
    """JSON Schema (Draft 2020-12) for flow.yaml files."""
    schema = FlowFile.model_json_schema()
    schema["$schema"] = JSON_SCHEMA_DIALECT
    return schema


def manifest_json_schema() -> dict[str, Any]:
    """JSON Schema (Draft 2020-12) for napflow.yaml manifests."""
    schema = Manifest.model_json_schema()
    schema["$schema"] = JSON_SCHEMA_DIALECT
    return schema


__all__ = [
    "IDENT_PATTERN",
    "PORT_REF_PATTERN",
    "JSON_SCHEMA_DIALECT",
    "FrozenModel",
    "PortType",
    # flow file
    "AssertCheck",
    "AssertConfig",
    "AssertNode",
    "ConditionConfig",
    "ConditionNode",
    "CounterConfig",
    "CounterNode",
    "DelayConfig",
    "DelayNode",
    "Edge",
    "EndConfig",
    "EndNode",
    "EndPort",
    "ExprCheck",
    "FixtureConfig",
    "FixtureNode",
    "FlowEnv",
    "FlowFile",
    "FlowMeta",
    "FlowNode",
    "FlowRefConfig",
    "GetConfig",
    "GetNode",
    "LogConfig",
    "LogNode",
    "LoopConfig",
    "LoopNode",
    "MergeConfig",
    "MergeNode",
    "Node",
    "NodeBase",
    "NoteConfig",
    "NoteNode",
    "PythonConfig",
    "PythonNode",
    "RequestConfig",
    "RequestNode",
    "ResponseTimeCheck",
    "RetryConfig",
    "SetConfig",
    "SetNode",
    "StartConfig",
    "StartNode",
    "StartPort",
    "StatusCheck",
    "SwitchCase",
    "SwitchConfig",
    "SwitchNode",
    "TimeoutConfig",
    "TimeoutNode",
    # manifest
    "Defaults",
    "EnvironmentsConfig",
    "FlowsConfig",
    "Manifest",
    "PythonSettings",
    "RequestDefaults",
    "RunDefaults",
    "WorkspaceInfo",
    # export functions
    "flow_json_schema",
    "manifest_json_schema",
]
