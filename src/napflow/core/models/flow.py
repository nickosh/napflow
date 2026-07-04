"""Pydantic models for `flow.yaml` — the full v1 node catalog (FR-201).

Authoritative spec: docs/napflow-flow-schema.md (v0.4). Rules that span
nodes (unique ids E011, edge resolution E003, start/end cardinality
E006, reserved names E012, cycles W101/E007) belong to the checker,
not these models; per-field structure lives here.
"""

from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from napflow.core.models.common import (
    EnvVarName,
    FrozenModel,
    NodeId,
    PortName,
    PortRef,
    PortType,
    Scalar,
    TemplatableBool,
    TemplatableInt,
    TemplatableNumber,
)

# --------------------------------------------------------------------------
# Ports (start / end)


class StartPort(FrozenModel):
    """A flow input. `default:` templates see only env.*/run.* (EC36).

    Absent `default` = the input is required at BIND; an explicit
    `default: null` is a null default. Distinguish via
    `"default" in port.model_fields_set`.
    """

    name: PortName
    type: PortType = "any"
    default: Any = None


class EndPort(FrozenModel):
    """A flow output — a REAL input port, wired via `end.<port>` (D18).

    `required: true` (default): unwritten at quiescence ⇒ run failed.
    The name `error` is reserved (E012, enforced by the checker).
    """

    name: PortName
    required: bool = True


# --------------------------------------------------------------------------
# Node configs


class StartConfig(FrozenModel):
    ports: list[StartPort] = []


class EndConfig(FrozenModel):
    ports: list[EndPort] = []


class RetryConfig(FrozenModel):
    """Transport-level retries; replaces the whole block on merge (EC23)."""

    max_attempts: Annotated[int, Field(ge=1)] = 1


class RequestConfig(FrozenModel):
    """HTTP call via niquests. Non-2xx is a valid response (EC13).

    `defaults.request` from the manifest merges in shallowly; None here
    means "inherit the default". `max_seconds` (node-level) is the hard
    wall-clock stop above all transport attempts (D24).
    """

    url: str
    method: str = "GET"
    headers: dict[str, Scalar] | str | None = None
    query: dict[str, Scalar] | str | None = None
    body: Any = None
    timeout_s: TemplatableNumber | None = None
    verify_tls: TemplatableBool | None = None
    retry: RetryConfig | None = None
    http_version: Literal["1.1", "2", "3"] | None = None  # None = negotiate


class PythonConfig(FrozenModel):
    """Run a function from the flow's nodes.py in the worker subprocess.

    Input ports derive from the function signature (AST, EC14); declared
    `outputs` may not use the reserved name `error` (E012, checker).
    """

    function: str
    outputs: list[PortName] = []


class StatusCheck(FrozenModel):
    kind: Literal["status"]
    equals: TemplatableInt


class ExprCheck(FrozenModel):
    """Sandboxed Jinja2 expression over the standard templating context."""

    kind: Literal["expr"]
    expr: str
    op: Literal[
        "present", "equals", "not_equals", "contains", "matches", "gt", "lt"
    ] = "present"
    value: Any = None

    @model_validator(mode="after")
    def _value_matches_op(self) -> "ExprCheck":
        has_value = "value" in self.model_fields_set
        if self.op == "present" and has_value:
            raise ValueError("op 'present' takes no 'value'")
        if self.op != "present" and not has_value:
            raise ValueError(f"op '{self.op}' requires a 'value'")
        return self


class ResponseTimeCheck(FrozenModel):
    kind: Literal["response_time"]
    under_ms: TemplatableNumber


AssertCheck = Annotated[
    StatusCheck | ExprCheck | ResponseTimeCheck, Field(discriminator="kind")
]


class AssertConfig(FrozenModel):
    checks: Annotated[list[AssertCheck], Field(min_length=1)]
    mode: Literal["report_all", "fail_fast"] = "report_all"


class ConditionConfig(FrozenModel):
    expr: str


class SwitchCase(FrozenModel):
    """One switch branch: output port `name` taken when the evaluated
    expression equals `equals`."""

    name: PortName
    equals: Any


class SwitchConfig(FrozenModel):
    """Multi-way branch; output ports = case names + `default`."""

    expr: str
    cases: Annotated[list[SwitchCase], Field(min_length=1)]


class LoopConfig(FrozenModel):
    """Run a body flow once per item of `over` (a Jinja2 expression
    evaluated against the trigger delivery). Body Start must declare
    `item`; may declare `index`. `results` ordered by item index (EC36)."""

    over: str
    body: str  # workspace-relative flow path, e.g. "flows/enroll_user"
    mode: Literal["sequential", "parallel"] = "sequential"
    max_concurrency: Annotated[int, Field(ge=1)] = 4
    on_error: Literal["stop", "continue"] = "stop"
    fresh_session: bool = False


class FlowRefConfig(FrozenModel):
    """Run another flow (reference, never embedding — E007 DAG). Outer
    ports derive from the target's Start/End + implicit `error` (D21)."""

    flow: str  # workspace-relative flow path, e.g. "flows/login"


class SetConfig(FrozenModel):
    name: str
    value: Any


class GetConfig(FrozenModel):
    name: str


class MergeConfig(FrozenModel):
    """Join paths. Inputs `in1..inN` come from edges, not config.
    `collect` gathers `count` messages into a list (count-based in v1)."""

    mode: Literal["any", "all", "collect"]
    count: Annotated[int, Field(ge=1)] | None = None

    @model_validator(mode="after")
    def _count_matches_mode(self) -> "MergeConfig":
        if self.mode == "collect" and self.count is None:
            raise ValueError("mode 'collect' requires 'count'")
        if self.mode != "collect" and self.count is not None:
            raise ValueError(
                f"'count' is only valid with mode 'collect', not '{self.mode}'"
            )
        return self


class CounterConfig(FrozenModel):
    """Cycle guard: exactly `count` passes, check-then-decrement (EC16)."""

    count: Annotated[int, Field(ge=0)]


class TimeoutConfig(FrozenModel):
    """Cycle guard: deadline gate, evaluated lazily on message arrival."""

    seconds: Annotated[float, Field(gt=0)]


class DelayConfig(FrozenModel):
    seconds: TemplatableNumber


class LogConfig(FrozenModel):
    label: str | None = None
    level: Literal["debug", "info", "warn", "error"] = "info"


class FixtureConfig(FrozenModel):
    """Load JSON/CSV from fixtures/; format inferred from the file
    extension when omitted. CSV → list of dicts, header row required."""

    file: str
    format: Literal["json", "csv"] | None = None


class NoteConfig(FrozenModel):
    text: str


# --------------------------------------------------------------------------
# Nodes


class NodeBase(FrozenModel):
    """`max_seconds` is a hard wall-clock ceiling on ONE firing (D24) —
    node-level, valid on every node type. The manifest default
    (`defaults.run.node_timeout_s`) auto-applies to request/python only.
    """

    id: NodeId
    max_seconds: Annotated[float, Field(gt=0)] | None = None


class StartNode(NodeBase):
    type: Literal["start"]
    config: StartConfig = StartConfig()


class EndNode(NodeBase):
    type: Literal["end"]
    config: EndConfig = EndConfig()


class RequestNode(NodeBase):
    type: Literal["request"]
    config: RequestConfig


class PythonNode(NodeBase):
    type: Literal["python"]
    config: PythonConfig


class AssertNode(NodeBase):
    type: Literal["assert"]
    config: AssertConfig


class ConditionNode(NodeBase):
    type: Literal["condition"]
    config: ConditionConfig


class SwitchNode(NodeBase):
    type: Literal["switch"]
    config: SwitchConfig


class LoopNode(NodeBase):
    type: Literal["loop"]
    config: LoopConfig


class FlowNode(NodeBase):
    type: Literal["flow"]
    config: FlowRefConfig


class SetNode(NodeBase):
    type: Literal["set"]
    config: SetConfig


class GetNode(NodeBase):
    type: Literal["get"]
    config: GetConfig


class MergeNode(NodeBase):
    type: Literal["merge"]
    config: MergeConfig


class CounterNode(NodeBase):
    type: Literal["counter"]
    config: CounterConfig


class TimeoutNode(NodeBase):
    type: Literal["timeout"]
    config: TimeoutConfig


class DelayNode(NodeBase):
    type: Literal["delay"]
    config: DelayConfig


class LogNode(NodeBase):
    type: Literal["log"]
    config: LogConfig


class FixtureNode(NodeBase):
    type: Literal["fixture"]
    config: FixtureConfig


class NoteNode(NodeBase):
    type: Literal["note"]
    config: NoteConfig


Node = Annotated[
    StartNode
    | EndNode
    | RequestNode
    | PythonNode
    | AssertNode
    | ConditionNode
    | SwitchNode
    | LoopNode
    | FlowNode
    | SetNode
    | GetNode
    | MergeNode
    | CounterNode
    | TimeoutNode
    | DelayNode
    | LogNode
    | FixtureNode
    | NoteNode,
    Field(discriminator="type"),
]


# --------------------------------------------------------------------------
# Flow file


class Edge(FrozenModel):
    """`from`/`to` are `<node>.<port>` refs; one edge per input port (E004)."""

    from_: PortRef = Field(alias="from")
    to: PortRef


class FlowMeta(FrozenModel):
    name: str
    description: str | None = None


class FlowEnv(FrozenModel):
    required: list[EnvVarName] = []


class FlowFile(FrozenModel):
    """One `flow.yaml`. `layout:` is quarantined at the file bottom and
    never affects engine behavior (FR-203)."""

    schema_: Literal["napflow/v1"] = Field(alias="schema")
    flow: FlowMeta
    env: FlowEnv | None = None
    nodes: list[Node]
    edges: list[Edge] = []
    layout: dict[NodeId, tuple[float, float]] | None = None
