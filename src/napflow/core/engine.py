"""Execution engine: scheduler, frames, node runners (EN §1–§6).

S2/M3 laid down the scheduler (QUIESCENT sentinel + empty-seed guard,
budget, deadline, abort, `max_seconds` cancellation), frames, outcome
aggregation (D18/D20), and runners for start/end/condition/assert/
delay. S3/M1 grew the pump dispatch to firing rules 2–3: latest-value
slots for multi-input nodes and the merge node (`any`/`all`/`collect`).
Remaining node types land through S3; a flow using one is a run `error`
(`unsupported_node_type`), never a crash.

The engine trusts its input: LOAD and CHECK are the CLI's lifecycle
steps (`napf run` refuses E-codes, M5). BIND and ENV happen here — the
engine is also the `from napflow.core import ...` pytest surface and
must fail fast standalone.

The QUIESCENT sentinel (EN §3) is the load-bearing detail: termination
is detected by whichever `in_flight` decrement reaches zero, never by
the pump polling. Single event loop ⇒ no locks, but every emission MUST
increment before enqueueing, and the pump finalizes immediately when
post-seed `in_flight` is zero (EC08) — otherwise QUIESCENT is never
enqueued and the pump blocks forever.
"""

import asyncio
import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from napflow import __version__
from napflow.core.events import (
    AssertResult,
    BudgetWarning,
    CaptureWarning,
    EventStream,
    LogEvent,
    MessageEmitted,
    NodeFired,
    PythonError,
    RequestFailed,
    RequestFinished,
    RequestStarted,
    RunFinished,
    RunStarted,
    isoformat_ms,
)
from napflow.core.httpclient import HttpClient, TransportError, WireResponse
from napflow.core.models import FlowFile
from napflow.core.models.flow import (
    AssertCheck,
    AssertNode,
    ConditionNode,
    DelayNode,
    EndNode,
    ExprCheck,
    MergeNode,
    Node,
    PythonNode,
    RequestNode,
    ResponseTimeCheck,
    StartNode,
    StatusCheck,
)
from napflow.core.models.manifest import Manifest, RequestDefaults
from napflow.core.templating import (
    Renderer,
    TemplateEvaluationError,
    TypeCoercionError,
    coerce_value,
    stringify_native,
)
from napflow.core.worker import (
    WorkerCrash,
    WorkerPool,
    WorkerTaskError,
    default_interpreter,
)

RunState = Literal["passed", "failed", "error", "aborted"]

# FR-406: run states → CLI exit codes
EXIT_CODES: dict[RunState, int] = {
    "passed": 0,
    "failed": 1,
    "error": 2,
    "aborted": 130,
}

# Node types the engine can run; the rest land through S3
# (set/get/switch/log/fixture at M3, guards at M4, loop/flow at M5).
# `note` has no runtime behavior but is legal on any canvas.
SUPPORTED_NODE_TYPES = frozenset(
    {
        "start",
        "end",
        "condition",
        "assert",
        "delay",
        "request",
        "merge",
        "python",
        "note",
    }
)

_MB = 1024 * 1024

# Node-error OUTLETS: where a failed firing routes (D24, EC24). Distinct
# from error-CLASS ports (below): assert.failed carries check failures,
# never node errors.
_NODE_ERROR_OUTLET = {"request": "error", "python": "error", "flow": "error"}

# Error-class output ports (mirrors W103 in checker.py): a message into
# one of these with no edge is an unhandled error ⇒ run failed (EN §2).
_ERROR_CLASS_PORTS = {
    "request": "error",
    "python": "error",
    "flow": "error",
    "assert": "failed",
}

# The manifest node_timeout_s default auto-applies to these only —
# the potentially-unbounded leaf firings (D24).
_DEFAULT_CEILING_TYPES = ("request", "python")

_PREVIEW_LIMIT = 512  # chars of compact JSON in message_emitted previews


class _BudgetExhausted(Exception):
    def __init__(self, edge: str):
        self.edge = edge
        super().__init__(f"message budget exhausted at edge {edge}")


class _NodeError(Exception):
    """A firing failed (template error, bad shape, timeout). Routed to
    the node's error outlet when it has one, else recorded as an
    unhandled node error ⇒ run failed (EC24). `extra` merges into the
    error-port payload (traceback/function/max_seconds fields)."""

    def __init__(self, kind: str, message: str, extra: dict[str, Any] | None = None):
        self.kind = kind
        self.extra = extra
        super().__init__(message)


class _BindError(Exception):
    """BIND/ENV lifecycle failure — run `error`, fail fast (EN §2)."""

    def __init__(self, reason: str, message: str):
        self.reason = reason
        super().__init__(message)


# --------------------------------------------------------------------------
# Core objects (EN §1)


@dataclass(frozen=True)
class Message:
    """The envelope on every edge; `trigger` in templates is exactly
    this, as `{value, meta}` (EC12)."""

    value: Any
    msg_id: str
    produced_by: str  # "<node>.<port>"
    frame: str
    ts: str

    def envelope(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "meta": {
                "msg_id": self.msg_id,
                "produced_by": self.produced_by,
                "frame": self.frame,
                "ts": self.ts,
            },
        }


@dataclass(frozen=True)
class _Delivery:
    node_id: str
    port: str
    message: Message


_QUIESCENT = object()  # the last decrement wakes the pump (EN §3)
_FATAL = object()  # budget exhaustion / abort: stop pumping now


class _Graph:
    """Static per-flow edge index."""

    def __init__(self, flow: FlowFile):
        self.nodes: dict[str, Node] = {n.id: n for n in flow.nodes}
        self.start = next(n for n in flow.nodes if isinstance(n, StartNode))
        self.end = next(n for n in flow.nodes if isinstance(n, EndNode))
        self.out_edges: dict[tuple[str, str], list[tuple[str, str]]] = {}
        # connected input ports per node, in edge-declaration order —
        # the rule-2/3 firing set AND merge-`all`'s emit key order
        self.in_ports: dict[str, list[str]] = {}
        for edge in flow.edges:
            from_node, _, from_port = edge.from_.partition(".")
            to_node, _, to_port = edge.to.partition(".")
            key = (from_node, from_port)
            self.out_edges.setdefault(key, []).append((to_node, to_port))
            ports = self.in_ports.setdefault(to_node, [])
            if to_port not in ports:
                ports.append(to_port)


@dataclass
class Frame:
    """One invocation of one flow (EN §1): the DATA isolation unit —
    variables, inputs, firing counts, `nodes.*` context. Outcomes are
    not isolated (D20). Hierarchical ids (`f-0/f-3`) arrive with
    subflows/loops in S3; the root run is always `f-0`."""

    id: str
    graph: _Graph
    inputs: dict[str, Any]
    variables: dict[str, Any] = field(default_factory=dict)
    node_outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    firing_counts: dict[str, int] = field(default_factory=dict)
    end_values: dict[str, Any] = field(default_factory=dict)
    # latest-value slots per multi-input node (rule 2 retains across
    # firings; merge `all` clears on emit) + merge `collect` buffers
    slots: dict[str, dict[str, Any]] = field(default_factory=dict)
    collected: dict[str, list[Any]] = field(default_factory=dict)


@dataclass
class RunResult:
    state: RunState
    end_outputs: dict[str, Any]
    asserts_passed: int
    asserts_failed: int
    unhandled_errors: list[dict[str, Any]]
    nodes_never_fired: list[str]
    duration_ms: float
    error_reason: str | None = None

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.state]


# --------------------------------------------------------------------------
# The run


class FlowRun:
    """One execution of one entry flow (EN §2 lifecycle from BIND on).

    Single-use: construct, `await execute()` once. `abort()` may be
    called from another task; the run finalizes as `aborted` with the
    report and JSONL written (EC20).
    """

    def __init__(
        self,
        flow: FlowFile,
        *,
        flow_identity: str,
        manifest: Manifest,
        env: Mapping[str, str],
        env_name: str | None,
        inputs: Mapping[str, Any] | None,
        stream: EventStream,
        run_timeout_s: float | None = None,
        flow_dir: Path | None = None,
        workspace_root: Path | None = None,
    ):
        self._flow = flow
        self._flow_identity = flow_identity
        self._flow_dir = flow_dir
        self._workspace_root = workspace_root
        self._python_settings = manifest.python
        self._defaults = manifest.defaults.run
        self._env = dict(env)
        self._env_name = env_name
        self._given_inputs = dict(inputs or {})
        self._stream = stream
        self._run_timeout_s = (
            run_timeout_s if run_timeout_s is not None else self._defaults.run_timeout_s
        )
        self._renderer = Renderer()
        self._started_ts = isoformat_ms(datetime.now(UTC))

        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._in_flight = 0
        self._tasks: set[asyncio.Task[None]] = set()
        self._msg_seq = 0
        self._budget_remaining = self._defaults.message_budget
        self._budget_warned = False
        self._aborted = False
        self._fatal: tuple[str, str] | None = None  # (error_reason, message)

        self._asserts_passed = 0
        self._asserts_failed = 0
        self._unhandled: list[dict[str, Any]] = []

        self._http: HttpClient | None = None
        self._workers: WorkerPool | None = None
        self._request_defaults: RequestDefaults = manifest.defaults.request
        self._body_cap = int(self._defaults.body_capture_mb * _MB)
        self._capture_remaining = int(self._defaults.run_capture_mb * _MB)
        self._capture_warned = False

    # -- public ------------------------------------------------------------

    def abort(self) -> None:
        """User cancellation: state `aborted`, exit 130 (FR-408)."""
        self._aborted = True
        self._queue.put_nowait(_FATAL)

    async def execute(self) -> RunResult:
        started = time.monotonic()
        self._stream.emit(
            RunStarted(
                flow=self._flow_identity,
                env_name=self._env_name,
                inputs=dict(self._given_inputs),
                engine_version=__version__,
            )
        )
        frame: Frame | None = None
        try:
            self._check_supported()
            self._check_env_required()
            graph = _Graph(self._flow)
            frame = Frame(id="f-0", graph=graph, inputs=self._bind(graph.start))
        except _BindError as e:
            self._fatal = (e.reason, str(e))
        except TypeCoercionError as e:
            self._fatal = ("bind_error", str(e))

        if frame is not None:
            try:
                if self._run_timeout_s is not None:
                    async with asyncio.timeout(self._run_timeout_s):
                        await self._pump(frame)
                else:
                    await self._pump(frame)
            except TimeoutError:
                # D24: expiry cancels in-flight work like an abort but
                # finalizes `error` — the report is still written
                self._fatal = self._fatal or ("run_timeout", "run deadline expired")
            for task in self._tasks:
                task.cancel()
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)

        if self._http is not None:  # FR-408: session closed on every exit
            await self._http.close()
        if self._workers is not None:  # workers killed at FINALIZE (EN §5a)
            await self._workers.close()
        return self._finalize(frame, started)

    # -- lifecycle: BIND / ENV (EN §2) --------------------------------------

    def _check_supported(self) -> None:
        for node in self._flow.nodes:
            if node.type not in SUPPORTED_NODE_TYPES:
                raise _BindError(
                    "unsupported_node_type",
                    f"node '{node.id}': type '{node.type}' is not runnable yet"
                    " (lands in a later stage; see docs/PLAN.md)",
                )

    def _check_env_required(self) -> None:
        required = self._flow.env.required if self._flow.env else []
        missing = [k for k in required if k not in self._env]
        if missing:
            raise _BindError(
                "env_missing",
                "env.required key(s) missing from the active profile: "
                + ", ".join(missing),
            )

    def _bind(self, start: StartNode) -> dict[str, Any]:
        """Validate & type-coerce inputs against Start ports; evaluate
        `default:` templates with env/run scope only (FR-501, EC36)."""
        declared = {p.name: p for p in start.config.ports}
        unknown = set(self._given_inputs) - set(declared)
        if unknown:
            raise _BindError(
                "bind_error", f"unknown input(s): {', '.join(sorted(unknown))}"
            )
        context = {"env": self._env, "run": self._run_context()}
        bound: dict[str, Any] = {}
        for name, port in declared.items():
            if name in self._given_inputs:
                value = self._given_inputs[name]
            elif "default" in port.model_fields_set:
                value = port.default
                if isinstance(value, str):
                    try:
                        value = self._renderer.render(value, context)
                    except TemplateEvaluationError as e:
                        raise _BindError(
                            "bind_error", f"input '{name}' default: {e}"
                        ) from e
            else:
                raise _BindError("bind_error", f"missing required input '{name}'")
            try:
                bound[name] = coerce_value(value, port.type)
            except TypeCoercionError as e:
                raise _BindError("bind_error", f"input '{name}': {e}") from e
        return bound

    # -- scheduler (EN §3) ---------------------------------------------------

    def _dec(self) -> None:
        self._in_flight -= 1
        if self._in_flight == 0:
            self._queue.put_nowait(_QUIESCENT)

    async def _pump(self, frame: Frame) -> None:
        try:
            self._seed(frame)
        except _BudgetExhausted as e:
            self._set_fatal("budget_exhausted", str(e), e.edge)
            return
        if self._in_flight == 0:
            return  # nothing seeded — finalize immediately (EC08)
        while True:
            item = await self._queue.get()
            if item is _QUIESCENT or item is _FATAL:
                break
            try:
                self._dispatch(frame, item)
            except _BudgetExhausted as e:
                # inline merge firings emit inside the pump (EN §4)
                self._set_fatal("budget_exhausted", str(e), e.edge)
            self._dec()  # delivery consumed

    def _dispatch(self, frame: Frame, delivery: _Delivery) -> None:
        """Firing rules per delivery (EN §4). Absorbed deliveries — a
        slot fill short of rendezvous, a `collect` append short of
        `count` — update state and emit nothing."""
        node = frame.graph.nodes.get(delivery.node_id)
        if node is None:
            return
        if isinstance(node, EndNode):
            # rule 5: End accumulates latest value, never fires
            frame.end_values[delivery.port] = delivery.message.value
            return
        if isinstance(node, MergeNode):
            self._deliver_merge(frame, node, delivery)  # rule 3
            return
        connected = frame.graph.in_ports.get(node.id, [])
        slot_values: dict[str, Any] | None = None
        if len(connected) > 1:
            # rule 2: latest-value slot per connected input; fire once
            # all are filled; later deliveries overwrite their slot and
            # re-fire immediately (slots retained, unlike merge `all`)
            slots = frame.slots.setdefault(node.id, {})
            slots[delivery.port] = delivery.message.value
            if len(slots) < len(connected):
                return
            slot_values = dict(slots)  # snapshot at decision time
        elif isinstance(node, PythonNode):
            # single-input python: the function still needs its kwarg
            # by port (=param) name (FR-506)
            slot_values = {delivery.port: delivery.message.value}
        # rule 1 — or a complete rule-2 slot set: fire per delivery
        self._in_flight += 1
        task = asyncio.create_task(
            self._fire(frame, node, delivery.message, slot_values)
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _deliver_merge(
        self, frame: Frame, node: MergeNode, delivery: _Delivery
    ) -> None:
        """Rule 3: `any` forwards every delivery; `all` is a strict
        rendezvous (slots CLEARED on emit — no stale re-fire, EC03/04);
        `collect` batches `count` values, leftovers never emit. Instant
        node, fired inline: no task, no ceiling (an explicit
        `max_seconds` is accepted and never trips — FS)."""
        cfg = node.config
        if cfg.mode == "any":
            out: Any = delivery.message.value
        elif cfg.mode == "all":
            slots = frame.slots.setdefault(node.id, {})
            slots[delivery.port] = delivery.message.value
            connected = frame.graph.in_ports[node.id]
            if len(slots) < len(connected):
                return
            out = {port: slots[port] for port in connected}
            slots.clear()
        else:  # collect
            bucket = frame.collected.setdefault(node.id, [])
            bucket.append(delivery.message.value)
            if len(bucket) < (cfg.count or 1):
                return
            out = list(bucket)
            bucket.clear()
        firing_no = frame.firing_counts.get(node.id, 0) + 1
        frame.firing_counts[node.id] = firing_no
        self._stream.emit(NodeFired(frame=frame.id, node=node.id, firing_no=firing_no))
        self._emit_output(frame, node.id, "out", out)

    def _seed(self, frame: Frame) -> None:
        """Start is seeded once per frame: `out` carries the full inputs
        dict, and each declared port emits its bound value (FS catalog).
        Unconnected fixture auto-seeding arrives with fixtures (S3)."""
        start = frame.graph.start
        frame.firing_counts[start.id] = 1
        self._stream.emit(NodeFired(frame=frame.id, node=start.id, firing_no=1))
        outputs = [("out", dict(frame.inputs))]
        outputs += [(p.name, frame.inputs[p.name]) for p in start.config.ports]
        for port, value in outputs:
            self._emit_output(frame, start.id, port, value)

    async def _fire(
        self,
        frame: Frame,
        node: Node,
        trigger: Message,
        slot_values: dict[str, Any] | None = None,
    ) -> None:
        ceiling = self._max_seconds(node)
        try:
            firing_no = frame.firing_counts.get(node.id, 0) + 1
            frame.firing_counts[node.id] = firing_no
            self._stream.emit(
                NodeFired(frame=frame.id, node=node.id, firing_no=firing_no)
            )
            coro = self._run_node(frame, node, trigger, slot_values)
            if ceiling is not None:
                async with asyncio.timeout(ceiling):
                    outputs = await coro
            else:
                outputs = await coro
            for port, value in outputs:
                self._emit_output(frame, node.id, port, value)
        except TimeoutError:
            self._node_error(
                frame,
                node,
                kind="timeout",
                message=f"firing exceeded max_seconds={ceiling}",
                extra={"max_seconds": ceiling},  # payload shape per FS D24
            )
        except (TemplateEvaluationError, TypeCoercionError) as e:
            self._node_error(frame, node, kind="template_error", message=str(e))
        except _NodeError as e:
            self._node_error(frame, node, kind=e.kind, message=str(e), extra=e.extra)
        except _BudgetExhausted as e:
            self._set_fatal("budget_exhausted", str(e), e.edge)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # engine bug — run `error`, never a crash
            self._set_fatal("internal_error", f"{node.id}: {type(e).__name__}: {e}")
        finally:
            self._dec()

    def _emit_output(self, frame: Frame, node_id: str, port: str, value: Any) -> None:
        # nodes.* holds the LATEST output — last-writer-wins (EC12/EC18)
        frame.node_outputs.setdefault(node_id, {})[port] = value
        node_type = frame.graph.nodes[node_id].type
        edges = frame.graph.out_edges.get((node_id, port), [])
        if not edges:
            if _ERROR_CLASS_PORTS.get(node_type) == port:
                # unhandled error-port message ⇒ run failed (EN §2, EC24);
                # surface the payload's cause — the report must say WHY
                detail = ""
                if isinstance(value, Mapping) and "message" in value:
                    kind = value.get("error_kind", "error")
                    detail = f" — {kind}: {value['message']}"
                self._record_unhandled(
                    frame,
                    node_id,
                    port=port,
                    kind="unhandled_error_port",
                    message=(
                        f"message into unconnected error port {node_id}.{port}" + detail
                    ),
                )
            return
        for to_node, to_port in edges:
            edge_ref = f"{node_id}.{port} → {to_node}.{to_port}"
            self._tick_budget(edge_ref)
            self._msg_seq += 1
            message = Message(
                value=value,
                msg_id=f"m-{self._msg_seq:06d}",
                produced_by=f"{node_id}.{port}",
                frame=frame.id,
                ts=isoformat_ms(datetime.now(UTC)),
            )
            self._in_flight += 1  # ALWAYS increment before enqueueing
            self._queue.put_nowait(_Delivery(to_node, to_port, message))
            self._stream.emit(
                MessageEmitted(
                    frame=frame.id,
                    node=node_id,
                    from_port=f"{node_id}.{port}",
                    to_node=to_node,
                    to_port=to_port,
                    msg_id=message.msg_id,
                    value_preview=_preview(value),
                )
            )

    def _tick_budget(self, edge: str) -> None:
        self._budget_remaining -= 1
        total = self._defaults.message_budget
        if not self._budget_warned and self._budget_remaining <= total // 10:
            self._budget_warned = True
            self._stream.emit(BudgetWarning(remaining=self._budget_remaining))
        if self._budget_remaining < 0:
            raise _BudgetExhausted(edge)

    def _max_seconds(self, node: Node) -> float | None:
        """D24 scope: explicit `max_seconds` honored on any node; the
        manifest default applies to request/python only."""
        if node.max_seconds is not None:
            return node.max_seconds
        if node.type in _DEFAULT_CEILING_TYPES:
            return self._defaults.node_timeout_s
        return None

    # -- node runners (EN §5) ------------------------------------------------

    async def _run_node(
        self,
        frame: Frame,
        node: Node,
        trigger: Message,
        slot_values: dict[str, Any] | None,
    ) -> list[tuple[str, Any]]:
        """`slot_values` is the rule-2 snapshot (port → value) for
        multi-input nodes — None on rule-1 firings. Its consumer is the
        python runner (declared inputs only, S3/M2)."""
        context = self._template_context(frame, trigger)
        match node:
            case ConditionNode():
                result = self._renderer.evaluate(node.config.expr, context)
                return [("true" if result else "false", trigger.value)]
            case AssertNode():
                return self._run_assert(frame, node, trigger, context)
            case RequestNode():
                return await self._run_request(frame, node, context)
            case PythonNode():
                return await self._run_python(frame, node, slot_values or {})
            case DelayNode():
                seconds = node.config.seconds
                if isinstance(seconds, str):
                    seconds = self._renderer.render(seconds, context)
                await asyncio.sleep(coerce_value(seconds, "number"))
                return [("out", trigger.value)]
            case _:  # unreachable: _check_supported gates node types
                raise _NodeError("internal", f"no runner for type '{node.type}'")

    async def _run_python(
        self, frame: Frame, node: PythonNode, inputs: dict[str, Any]
    ) -> list[tuple[str, Any]]:
        """FR-506: declared inputs only (the slot snapshot, keyed by
        port = param name), JSON-serializable I/O, run in the module's
        persistent worker (EN §5a). AssertionError counts as a failed
        assert run-wide AND routes to the error port; other exceptions
        route with traceback. Return value: a dict keyed by declared
        `outputs` (each key required); with no declared outputs the
        return value is discarded."""
        function = node.config.function
        if self._flow_dir is None:
            raise _NodeError(
                "worker_crash",
                "python nodes need the flow directory (pass flow_dir= to"
                " FlowRun/execute_flow) to locate nodes.py",
            )
        nodes_path = self._flow_dir / "nodes.py"
        if not nodes_path.is_file():
            raise _NodeError("worker_crash", f"no nodes.py at {nodes_path}")
        if self._workers is None:
            self._workers = WorkerPool(
                self._resolve_interpreter(),
                cwd=self._workspace_root or self._flow_dir,
                on_log=self._worker_log,
            )
        try:
            value = await self._workers.call(
                nodes_path, function, inputs, label=(frame.id, node.id)
            )
        except WorkerTaskError as e:
            self._stream.emit(
                PythonError(
                    frame=frame.id,
                    node=node.id,
                    function=function,
                    error_type=e.error_type,
                    message=str(e),
                    traceback=e.traceback_text,
                )
            )
            if e.kind == "python_assert":  # FR-506: report as python-assert
                self._asserts_failed += 1
                self._stream.emit(
                    AssertResult(
                        frame=frame.id,
                        node=node.id,
                        check=f"{function}: {e}",
                        op="python-assert",
                        expected=None,
                        actual=None,
                        passed=False,
                    )
                )
            raise _NodeError(
                e.kind,
                str(e),
                extra={"traceback": e.traceback_text, "function": function},
            ) from e
        except WorkerCrash as e:
            raise _NodeError("worker_crash", str(e)) from e
        outputs: list[tuple[str, Any]] = []
        for port in node.config.outputs:
            if not isinstance(value, Mapping) or port not in value:
                raise _NodeError(
                    "python_error",
                    f"{function}() must return a dict with a key for each"
                    f" declared output (missing {port!r})",
                )
            outputs.append((port, value[port]))
        return outputs

    def _resolve_interpreter(self) -> str:
        """FR-108: `python.interpreter` from napflow.yaml; None = the
        interpreter running napflow. A relative multi-part path resolves
        against the workspace root; bare names go through PATH."""
        configured = self._python_settings.interpreter
        if configured is None:
            return default_interpreter()
        path = Path(configured)
        if not path.is_absolute() and len(path.parts) > 1 and self._workspace_root:
            return str(self._workspace_root / path)
        return configured

    def _worker_log(self, level: str, label: tuple[str, str], text: str) -> None:
        frame_id, node_id = label
        self._stream.emit(
            LogEvent(
                frame=frame_id,
                node=node_id,
                label=f"python:{node_id}",
                level=level,  # type: ignore[arg-type]  # worker sends info|warn
                value=text,
            )
        )

    async def _run_request(
        self, frame: Frame, node: RequestNode, context: dict[str, Any]
    ) -> list[tuple[str, Any]]:
        """FR-503: engine-level retry over the shared session; non-2xx
        emits on `response`, transport failures on `error` (EC13).
        `max_seconds` (default 300, D24) is enforced above this by
        `_fire` — it cancels all attempts at once."""
        if self._http is None:
            self._http = HttpClient()
        cfg = self._effective_request_config(node, context)
        attempts: int = cfg["retry_attempts"]
        for attempt in range(1, attempts + 1):
            self._stream.emit(
                RequestStarted(
                    frame=frame.id,
                    node=node.id,
                    method=cfg["method"],
                    url=cfg["url"],
                    headers=cfg["headers"],
                    body_preview=_preview(cfg["body"]),
                    attempt=attempt,
                )
            )
            try:
                wire = await self._http.request(
                    method=cfg["method"],
                    url=cfg["url"],
                    headers=cfg["headers"],
                    query=cfg["query"],
                    body=cfg["body"],
                    timeout_s=cfg["timeout_s"],
                    verify_tls=cfg["verify_tls"],
                    http_version=node.config.http_version,
                )
            except TransportError as e:
                will_retry = attempt < attempts
                self._stream.emit(
                    RequestFailed(
                        frame=frame.id,
                        node=node.id,
                        error_kind=e.kind,
                        message=str(e),
                        attempt=attempt,
                        will_retry=will_retry,
                    )
                )
                if not will_retry:
                    raise _NodeError(
                        e.kind, f"transport failure after {attempt} attempt(s): {e}"
                    ) from e
                continue
            self._stream.emit(
                RequestFinished(
                    frame=frame.id,
                    node=node.id,
                    status=wire.status,
                    http_version=wire.http_version,
                    headers=wire.headers,
                    body=self._capture_body(wire),
                    size_bytes=wire.size_bytes,
                    timing=wire.timing,
                    attempt=attempt,
                    retries_total=attempt - 1,
                )
            )
            value = {
                "status": wire.status,
                "headers": wire.headers,
                "body": wire.body,
                "elapsed_ms": wire.elapsed_ms,
                "url": wire.url,
                "http_version": wire.http_version,
                "attempt": attempt,
            }
            return [("response", value)]
        raise AssertionError("unreachable: retry loop returns or raises")

    def _effective_request_config(
        self, node: RequestNode, context: dict[str, Any]
    ) -> dict[str, Any]:
        """`defaults.request` merges SHALLOWLY (FR-105/EC23): node keys
        win wholesale, `retry:` replaces the whole block. Default-origin
        templates render with env/run scope only — `inputs`/`nodes`
        there are StrictUndefined by design."""
        cfg = node.config
        defaults = self._request_defaults
        restricted = {"env": context["env"], "run": context["run"]}

        def render(value: Any, from_defaults: bool) -> Any:
            return self._renderer.render_config(
                value, restricted if from_defaults else context
            )

        def merged(name: str) -> tuple[Any, bool]:
            node_value = getattr(cfg, name)
            if node_value is None:
                return getattr(defaults, name), True
            return node_value, False

        headers_raw, headers_default = merged("headers")
        headers = render(headers_raw, headers_default)
        if not isinstance(headers, Mapping):
            raise TypeCoercionError(headers, "object (headers)")
        query = render(cfg.query, False)
        if query is not None and not isinstance(query, Mapping):
            raise TypeCoercionError(query, "object (query)")
        timeout_raw, timeout_default = merged("timeout_s")
        verify_raw, verify_default = merged("verify_tls")
        retry = cfg.retry if cfg.retry is not None else defaults.retry
        return {
            "method": render(cfg.method, False).upper(),
            "url": coerce_value(render(cfg.url, False), "string"),
            "headers": {str(k): coerce_value(v, "string") for k, v in headers.items()},
            "query": (
                {str(k): coerce_value(v, "string") for k, v in query.items()}
                if query is not None
                else None
            ),
            "body": render(cfg.body, False),
            "timeout_s": coerce_value(render(timeout_raw, timeout_default), "number"),
            "verify_tls": coerce_value(render(verify_raw, verify_default), "boolean"),
            "retry_attempts": retry.max_attempts,
        }

    def _capture_body(self, wire: WireResponse) -> Any:
        """Capture valves (FR-703/706): the EVENT copy of a body is
        capped per body and per run; the port value always carries the
        full body. Truncation is a marked wrapper, never a silent cut."""
        size = wire.size_bytes
        self._capture_remaining -= size
        run_cap = int(self._defaults.run_capture_mb * _MB)
        if not self._capture_warned and self._capture_remaining <= run_cap // 10:
            self._capture_warned = True
            remaining_mb = max(self._capture_remaining, 0) / _MB
            self._stream.emit(CaptureWarning(remaining_mb=round(remaining_mb, 3)))
        if size <= self._body_cap and self._capture_remaining >= 0:
            return wire.body
        body = wire.body
        if isinstance(body, dict) and body.get("__binary__") is True:
            return body | {
                "base64": body["base64"][: self._body_cap],
                "truncated": True,
            }
        text = body if isinstance(body, str) else stringify_native(body)
        return {
            "__truncated__": True,
            "size_bytes": size,
            "prefix": text[: self._body_cap],
        }

    def _run_assert(
        self,
        frame: Frame,
        node: AssertNode,
        trigger: Message,
        context: dict[str, Any],
    ) -> list[tuple[str, Any]]:
        all_passed = True
        for check in node.config.checks:
            label, op, expected, actual, passed = self._eval_check(
                check, trigger, context
            )
            self._stream.emit(
                AssertResult(
                    frame=frame.id,
                    node=node.id,
                    check=label,
                    op=op,
                    expected=expected,
                    actual=actual,
                    passed=passed,
                )
            )
            if passed:
                self._asserts_passed += 1
            else:
                self._asserts_failed += 1
                all_passed = False
                if node.config.mode == "fail_fast":
                    break
        return [("passed" if all_passed else "failed", trigger.value)]

    def _eval_check(
        self, check: AssertCheck, trigger: Message, context: dict[str, Any]
    ) -> tuple[str, str | None, Any, Any, bool]:
        """→ (check label, op, expected, actual, passed). Evaluation
        errors are node errors — except `op: present`, where an
        undefined path IS the answer (check fails, run continues)."""
        match check:
            case StatusCheck():
                actual = self._response_field(trigger, "status")
                expected = self._render_scalar(check.equals, context, "number")
                return ("status", None, expected, actual, actual == expected)
            case ResponseTimeCheck():
                actual = self._response_field(trigger, "elapsed_ms")
                expected = self._render_scalar(check.under_ms, context, "number")
                return ("response_time", None, expected, actual, actual < expected)
            case ExprCheck():
                pass
        if check.op == "present":
            try:
                actual = self._renderer.evaluate(check.expr, context)
            except TemplateEvaluationError:
                return (check.expr, "present", None, None, False)
            return (check.expr, "present", None, actual, actual is not None)
        actual = self._renderer.evaluate(check.expr, context)
        expected = self._renderer.render_config(check.value, context)
        try:
            passed = {
                "equals": lambda: actual == expected,
                "not_equals": lambda: actual != expected,
                "contains": lambda: expected in actual,
                "matches": lambda: re.search(str(expected), str(actual)) is not None,
                "gt": lambda: actual > expected,
                "lt": lambda: actual < expected,
            }[check.op]()
        except (TypeError, re.error) as e:
            raise _NodeError(
                "assert_error", f"check '{check.expr}' op '{check.op}': {e}"
            ) from e
        return (check.expr, check.op, expected, actual, passed)

    def _response_field(self, trigger: Message, key: str) -> Any:
        if not isinstance(trigger.value, Mapping) or key not in trigger.value:
            raise _NodeError(
                "assert_error",
                f"this check needs a response-shaped message with '{key}'"
                f" (got {type(trigger.value).__name__})",
            )
        return trigger.value[key]

    def _render_scalar(
        self, value: Any, context: dict[str, Any], type_: Literal["number"]
    ) -> Any:
        if isinstance(value, str):
            value = self._renderer.render(value, context)
        return coerce_value(value, type_)

    # -- errors & outcomes (EN §2, D18/D20, EC24) ------------------------------

    def _node_error(
        self,
        frame: Frame,
        node: Node,
        *,
        kind: str,
        message: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        outlet = _NODE_ERROR_OUTLET.get(node.type)
        if outlet is not None:
            payload = {"error_kind": kind, "message": message} | (extra or {})
            self._emit_output(frame, node.id, outlet, payload)
        else:
            self._record_unhandled(
                frame, node.id, port=None, kind=kind, message=message
            )

    def _record_unhandled(
        self, frame: Frame, node_id: str, *, port: str | None, kind: str, message: str
    ) -> None:
        self._unhandled.append(
            {
                "frame": frame.id,
                "node": node_id,
                "port": port,
                "kind": kind,
                "message": message,
            }
        )

    def _set_fatal(self, reason: str, message: str, edge: str | None = None) -> None:
        if self._fatal is None:
            self._fatal = (reason, message)
            self._unhandled.append(
                {
                    "frame": None,
                    "node": None,
                    "port": edge,  # budget: the hot edge (EN §3)
                    "kind": reason,
                    "message": message,
                }
            )
        self._queue.put_nowait(_FATAL)

    def _finalize(self, frame: Frame | None, started: float) -> RunResult:
        end_outputs: dict[str, Any] = {}
        if frame is not None:
            for port in frame.graph.end.config.ports:
                if port.name in frame.end_values:
                    end_outputs[port.name] = frame.end_values[port.name]
                elif port.required:
                    # D18: a declared output never produced is never a
                    # false green — run failed
                    self._record_unhandled(
                        frame,
                        frame.graph.end.id,
                        port=port.name,
                        kind="required_end_unwritten",
                        message=f"required End port '{port.name}' was never written",
                    )
                else:
                    end_outputs[port.name] = None  # noted by null (FR-502)

        if self._aborted:
            state: RunState = "aborted"
        elif self._fatal is not None:
            state = "error"
        elif self._asserts_failed or self._unhandled:
            state = "failed"
        else:
            state = "passed"

        never_fired = []
        if frame is not None:
            never_fired = [
                n.id
                for n in self._flow.nodes
                # End never fires by design (rule 5); note has no runtime
                if n.type not in ("end", "note") and not frame.firing_counts.get(n.id)
            ]

        duration_ms = (time.monotonic() - started) * 1000
        self._stream.emit(
            RunFinished(
                state=state,
                duration_ms=round(duration_ms, 3),
                asserts={
                    "passed": self._asserts_passed,
                    "failed": self._asserts_failed,
                },
                unhandled_errors=self._unhandled,
                end_outputs=end_outputs,
                nodes_never_fired=never_fired,
                error_reason=self._fatal[0] if self._fatal else None,
            )
        )
        return RunResult(
            state=state,
            end_outputs=end_outputs,
            asserts_passed=self._asserts_passed,
            asserts_failed=self._asserts_failed,
            unhandled_errors=self._unhandled,
            nodes_never_fired=never_fired,
            duration_ms=duration_ms,
            error_reason=self._fatal[0] if self._fatal else None,
        )

    # -- templating context (EN §6, FR-602) -----------------------------------

    def _run_context(self) -> dict[str, Any]:
        return {
            "id": self._stream.run_id,
            "timestamp": self._started_ts,
            "env_name": self._env_name,
        }

    def _template_context(self, frame: Frame, trigger: Message) -> dict[str, Any]:
        return {
            "env": self._env,
            "inputs": frame.inputs,
            "run": self._run_context(),
            "nodes": frame.node_outputs,
            "trigger": trigger.envelope(),
        }


def _preview(value: Any) -> Any:
    """`value_preview` for stream events: the value itself when small,
    a truncated compact-JSON string when large (EN §7 — full bodies
    live in request_* events, previews stay light)."""
    try:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        text = repr(value)
    if len(text) <= _PREVIEW_LIMIT:
        return value
    return text[:_PREVIEW_LIMIT] + "…(truncated)"


async def execute_flow(
    flow: FlowFile,
    *,
    flow_identity: str,
    manifest: Manifest,
    env: Mapping[str, str],
    env_name: str | None,
    inputs: Mapping[str, Any] | None,
    stream: EventStream,
    run_timeout_s: float | None = None,
    flow_dir: Path | None = None,
    workspace_root: Path | None = None,
) -> RunResult:
    """Run one flow to quiescence (BIND → EXECUTE → FINALIZE). The
    caller owns LOAD/CHECK, the event stream, and its sinks (M5 wires
    `napf run`). `flow_dir` locates nodes.py for python nodes;
    `workspace_root` sets the worker cwd and resolves a relative
    `python.interpreter`."""
    run = FlowRun(
        flow,
        flow_identity=flow_identity,
        manifest=manifest,
        env=env,
        env_name=env_name,
        inputs=inputs,
        stream=stream,
        run_timeout_s=run_timeout_s,
        flow_dir=flow_dir,
        workspace_root=workspace_root,
    )
    return await run.execute()
