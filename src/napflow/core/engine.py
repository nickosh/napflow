"""Execution engine: scheduler, frames, node runners (EN §1–§6).

S2/M3 laid down the scheduler (QUIESCENT sentinel + empty-seed guard,
budget, deadline, abort, `max_seconds` cancellation), root frame, and
outcome aggregation (D18/D20); S3 M1–M4 added firing rules 2–4 and the
leaf node set; S3/M5 completed the catalog with hierarchical frames:
`flow`/`loop` spawn child frames that share ONE pump, ONE budget, and
ONE quiescence detector — a frame completes when its own `in_flight`
drains to zero (the same last-decrement trick, per frame), waking the
container node that awaits it. Data crosses frames only via Start/End
binding; outcomes aggregate run-wide (D20).

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
import csv
import io
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
    GuardTripped,
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
from napflow.core.httpclient import (
    HttpClient,
    RequestEncodingError,
    TransportError,
    WireResponse,
)
from napflow.core.loader import LoadError, load_flow
from napflow.core.models import FlowFile
from napflow.core.models.flow import (
    AssertCheck,
    AssertNode,
    ConditionNode,
    CounterNode,
    DelayNode,
    EndNode,
    ExprCheck,
    FixtureNode,
    FlowNode,
    GetNode,
    LogNode,
    LoopNode,
    MergeNode,
    Node,
    PythonNode,
    RequestNode,
    ResponseTimeCheck,
    SetNode,
    StartNode,
    StatusCheck,
    SwitchNode,
    TimeoutNode,
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
from napflow.core.workspace import WorkspaceBoundaryError, WorkspaceResolver

RunState = Literal["passed", "failed", "error", "aborted"]

# FR-406: run states → CLI exit codes
EXIT_CODES: dict[RunState, int] = {
    "passed": 0,
    "failed": 1,
    "error": 2,
    "aborted": 130,
}

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

# Inline merge/guard deliveries can keep the ready queue perpetually
# non-empty, so Queue.get() alone is not a cooperative scheduling point.
# Yield after a bounded batch: large enough to keep the fast path cheap,
# small enough for millisecond-scale deadlines/abort requests to be seen.
_READY_BATCH_SIZE = 128


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
    frame: "Frame"


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
    variables, inputs, firing counts, `nodes.*` context, guard state.
    Outcomes are not isolated (D20) but ARE counted per frame so
    container nodes can compute their child subtree's state (D21).
    Ids are hierarchical paths (`f-0/f-3`); the root is always `f-0`.
    A frame completes when its own `in_flight` drains to zero — the
    per-frame copy of the run-level QUIESCENT trick."""

    id: str
    graph: _Graph
    inputs: dict[str, Any]
    flow_dir: Path | None = None  # locates this flow's nodes.py
    variables: dict[str, Any] = field(default_factory=dict)
    node_outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    firing_counts: dict[str, int] = field(default_factory=dict)
    end_values: dict[str, Any] = field(default_factory=dict)
    # latest-value slots per multi-input node (rule 2 retains across
    # firings; merge `all` clears on emit) + merge `collect` buffers
    slots: dict[str, dict[str, Any]] = field(default_factory=dict)
    collected: dict[str, list[Any]] = field(default_factory=dict)
    # rule-4 guard state, frame-local by construction (TR-5): counter
    # remaining / timeout start; absent key = pristine (post-reset)
    guards: dict[str, Any] = field(default_factory=dict)
    # frame tree + completion machinery (S3/M5)
    in_flight: int = 0
    done: asyncio.Event = field(default_factory=asyncio.Event)
    tasks: set[asyncio.Task[None]] = field(default_factory=set)
    children: list["Frame"] = field(default_factory=list)
    cancelled: bool = False
    loop_binding: tuple[Any, int] | None = None  # (item, index) in bodies
    http: HttpClient | None = None  # fresh_session override, inherited
    # frame-local outcome counts (subtree sums feed D21 payloads)
    failed_asserts: int = 0
    unhandled_count: int = 0


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
        workspace_resolver: WorkspaceResolver | None = None,
    ):
        self._flow = flow
        self._flow_identity = flow_identity
        self._flow_dir = flow_dir
        self._workspace_resolver = workspace_resolver
        if self._workspace_resolver is None and workspace_root is not None:
            self._workspace_resolver = WorkspaceResolver(workspace_root)
        self._workspace_root = (
            self._workspace_resolver.root
            if self._workspace_resolver is not None
            else workspace_root
        )
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
        self._deadline_at: float | None = None
        self._runtime_closed = False
        self._cleanup_task: asyncio.Task[None] | None = None

        self._asserts_passed = 0
        self._asserts_failed = 0
        self._unhandled: list[dict[str, Any]] = []

        self._http: HttpClient | None = None
        self._extra_http: list[HttpClient] = []  # fresh_session leftovers
        self._workers: WorkerPool | None = None
        self._fixture_cache: dict[str, Any] = {}  # per RUN, keyed by path
        self._flow_cache: dict[str, tuple[_Graph, Path, Any]] = {}
        self._frame_seq = 0
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
        frame: Frame | None = None
        try:
            self._stream.emit(
                RunStarted(
                    flow=self._flow_identity,
                    env_name=self._env_name,
                    inputs=dict(self._given_inputs),
                    engine_version=__version__,
                )
            )
            try:
                self._check_env_required(self._flow.env)
                graph = _Graph(self._flow)
                frame = Frame(
                    id="f-0",
                    graph=graph,
                    inputs=self._bind(graph.start, self._given_inputs),
                    flow_dir=self._flow_dir,
                )
            except _BindError as e:
                self._fatal = (e.reason, str(e))
            except TypeCoercionError as e:
                self._fatal = ("bind_error", str(e))

            if frame is not None:
                try:
                    if self._run_timeout_s is not None:
                        self._deadline_at = (
                            asyncio.get_running_loop().time() + self._run_timeout_s
                        )
                        async with asyncio.timeout_at(self._deadline_at):
                            await self._pump(frame)
                    else:
                        await self._pump(frame)
                except TimeoutError:
                    # D24: expiry cancels in-flight work like an abort but
                    # finalizes `error` — the report is still written.
                    self._set_run_timeout()

            # Normal completion, abort, and run deadline all converge here:
            # no owned task/session/worker is live when run_finished is born.
            await self._wait_for_cleanup()
            return self._finalize(frame, started)
        finally:
            # External coroutine cancellation and server-task teardown skip
            # the normal path above.  Shield the one cleanup task so cancellation
            # cannot strand the resources it is responsible for closing; the
            # original CancelledError still escapes after cleanup completes.
            try:
                await self._wait_for_cleanup()
            finally:
                self._stream.close()

    async def _wait_for_cleanup(self) -> None:
        """Await the single cleanup task without letting caller cancellation
        cancel that task too. Repeated cancellation still waits for teardown,
        then propagates to the caller (D36/TR-15).
        """
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._close_runtime())
        try:
            await asyncio.shield(self._cleanup_task)
        except asyncio.CancelledError:
            while not self._cleanup_task.done():
                try:
                    await asyncio.shield(self._cleanup_task)
                except asyncio.CancelledError:
                    continue
            # Retrieve a cleanup failure before propagating cancellation so
            # no task exception is leaked as an unobserved warning.
            self._cleanup_task.exception()
            raise

    async def _close_runtime(self) -> None:
        """Close every resource owned by this run, once (D36/NFR-13).

        Firing tasks go first so they cannot create another HTTP session or
        worker while those pools are being torn down.  WorkerPool.close()
        also drains any terminate/kill task started by a cancelled firing.
        """
        if self._runtime_closed:
            return
        tasks = [task for task in self._tasks if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        closers = []
        if self._http is not None:
            closers.append(self._http.close())
        for extra in list(self._extra_http):
            closers.append(extra.close())
        if self._workers is not None:
            closers.append(self._workers.close())
        results = (
            await asyncio.gather(*closers, return_exceptions=True) if closers else []
        )
        errors = [result for result in results if isinstance(result, BaseException)]
        if errors:
            raise errors[0]
        self._extra_http.clear()
        self._runtime_closed = True

    # -- lifecycle: BIND / ENV (EN §2) --------------------------------------

    def _check_env_required(self, env_cfg: Any) -> None:
        required = env_cfg.required if env_cfg else []
        missing = [k for k in required if k not in self._env]
        if missing:
            raise _BindError(
                "env_missing",
                "env.required key(s) missing from the active profile: "
                + ", ".join(missing),
            )

    def _bind(self, start: StartNode, given: Mapping[str, Any]) -> dict[str, Any]:
        """Validate & type-coerce inputs against Start ports; evaluate
        `default:` templates with env/run scope only (FR-501, EC36).
        Used for the root frame's CLI inputs AND for child-frame
        binding by flow/loop nodes — same rules everywhere."""
        declared = {p.name: p for p in start.config.ports}
        unknown = set(given) - set(declared)
        if unknown:
            raise _BindError(
                "bind_error", f"unknown input(s): {', '.join(sorted(unknown))}"
            )
        context = {"env": self._env, "run": self._run_context()}
        bound: dict[str, Any] = {}
        for name, port in declared.items():
            if name in given:
                value = given[name]
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

    def _inc(self, frame: Frame) -> None:
        frame.in_flight += 1
        self._in_flight += 1

    def _dec(self, frame: Frame) -> None:
        # frame completion FIRST: the container node awaiting `done`
        # is itself still in flight in the PARENT frame, so the global
        # counter cannot hit zero before it resumes
        frame.in_flight -= 1
        if frame.in_flight == 0:
            frame.done.set()
        self._in_flight -= 1
        if self._in_flight == 0:
            self._queue.put_nowait(_QUIESCENT)

    def _spawn_task(self, frame: Frame, coro: Any) -> None:
        self._inc(frame)
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        frame.tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(frame.tasks.discard)

    async def _pump(self, root: Frame) -> None:
        try:
            self._seed(root)
        except _BudgetExhausted as e:
            self._set_fatal("budget_exhausted", str(e), e.edge)
            return
        if self._in_flight == 0:
            return  # nothing seeded — finalize immediately (EC08)
        batch_remaining = _READY_BATCH_SIZE
        while True:
            item = await self._queue.get()
            # A task may produce the final QUIESCENT wakeup just after the
            # deadline.  Check that boundary before accepting a successful
            # termination; otherwise the timeout callback can lose the race
            # with leaving `asyncio.timeout_at()` and the run reports passed.
            if item is _QUIESCENT and self._deadline_expired():
                self._set_run_timeout()
                break
            if item is _QUIESCENT or item is _FATAL:
                break
            delivery: _Delivery = item
            try:
                if not delivery.frame.cancelled:
                    self._dispatch(delivery.frame, delivery)
            except _BudgetExhausted as e:
                # inline merge/guard firings emit inside the pump (EN §4)
                self._set_fatal("budget_exhausted", str(e), e.edge)
            self._dec(delivery.frame)  # delivery consumed
            batch_remaining -= 1
            if batch_remaining == 0:
                # Queue.get() does not yield when an inline cycle keeps the
                # queue ready.  This is the explicit fairness/control point.
                await asyncio.sleep(0)
                if self._aborted:
                    break
                if self._deadline_expired():
                    self._set_run_timeout()
                    break
                batch_remaining = _READY_BATCH_SIZE

    def _deadline_expired(self) -> bool:
        return self._deadline_at is not None and (
            asyncio.get_running_loop().time() >= self._deadline_at
        )

    def _set_run_timeout(self) -> None:
        if self._fatal is None:
            self._set_fatal("run_timeout", "run deadline expired")

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
        if isinstance(node, CounterNode | TimeoutNode):
            self._deliver_guard(frame, node, delivery)  # rule 4
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
        elif isinstance(node, PythonNode | FlowNode):
            # single-input python/flow: the runner still needs the
            # value keyed by port name (param / target Start port)
            slot_values = {delivery.port: delivery.message.value}
        # rule 1 — or a complete rule-2 slot set: fire per delivery
        self._spawn_task(frame, self._fire(frame, node, delivery.message, slot_values))

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

    def _deliver_guard(
        self, frame: Frame, node: CounterNode | TimeoutNode, delivery: _Delivery
    ) -> None:
        """Rule 4 (D19/EC16): guards consult/update frame-local state.
        `reset` deliveries restore the pristine state and emit NOTHING
        (absorbed — no firing, no events). `exhausted`/`expired` are
        ordinary pass-through outputs, never error ports (D19). Instant
        nodes, fired inline like merge."""
        if delivery.port == "reset":
            frame.guards.pop(node.id, None)  # silent restore
            return
        if isinstance(node, CounterNode):
            # EC16 check-then-decrement: exactly `count` passes, then
            # every message exhausts
            remaining = frame.guards.get(node.id, node.config.count)
            if remaining > 0:
                frame.guards[node.id] = remaining - 1
                port = "continue"
            else:
                port = "exhausted"
        else:
            # lazy deadline: the first message starts the clock; expiry
            # is evaluated on arrival, never by a background timer
            now = time.monotonic()
            started = frame.guards.setdefault(node.id, now)
            port = "continue" if now - started < node.config.seconds else "expired"
        firing_no = frame.firing_counts.get(node.id, 0) + 1
        frame.firing_counts[node.id] = firing_no
        self._stream.emit(NodeFired(frame=frame.id, node=node.id, firing_no=firing_no))
        if port in ("exhausted", "expired"):
            self._stream.emit(
                GuardTripped(frame=frame.id, node=node.id, kind=node.type, port=port)
            )
        self._emit_output(frame, node.id, port, delivery.message.value)

    def _seed(self, frame: Frame) -> None:
        """Sources (rule 6): Start is seeded once per frame — `out`
        carries the full inputs dict, each declared port emits its
        bound value (FS catalog); a fixture with an unconnected
        `trigger` auto-fires once with a synthetic trigger (D17)."""
        start = frame.graph.start
        frame.firing_counts[start.id] = 1
        self._stream.emit(NodeFired(frame=frame.id, node=start.id, firing_no=1))
        outputs = [("out", dict(frame.inputs))]
        outputs += [(p.name, frame.inputs[p.name]) for p in start.config.ports]
        for port, value in outputs:
            self._emit_output(frame, start.id, port, value)
        for node in frame.graph.nodes.values():
            if isinstance(node, FixtureNode) and "trigger" not in (
                frame.graph.in_ports.get(node.id, [])
            ):
                seed = Message(
                    value=None,
                    msg_id="m-seed",
                    produced_by="__seed__",
                    frame=frame.id,
                    ts=isoformat_ms(datetime.now(UTC)),
                )
                self._spawn_task(frame, self._fire(frame, node, seed))

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
            extra: dict[str, Any] = {"max_seconds": ceiling}  # FS D24 shape
            if isinstance(node, FlowNode):
                extra["state"] = "aborted"  # child frame cancelled (D24)
            self._node_error(
                frame,
                node,
                kind="timeout",
                message=f"firing exceeded max_seconds={ceiling}",
                extra=extra,
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
            self._dec(frame)

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
            self._inc(frame)  # ALWAYS increment before enqueueing
            self._queue.put_nowait(_Delivery(to_node, to_port, message, frame))
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
            case FlowNode():
                return await self._run_flow_node(frame, node, slot_values or {})
            case LoopNode():
                return await self._run_loop(frame, node, context)
            case SwitchNode():
                value = self._renderer.evaluate(node.config.expr, context)
                port = "default"
                for case in node.config.cases:  # first matching case wins
                    if value == case.equals:
                        port = case.name
                        break
                return [(port, trigger.value)]
            case SetNode():
                # config `value` templates recursively (D25 native rule)
                written = self._renderer.render_config(node.config.value, context)
                frame.variables[node.config.name] = written
                return [("out", written)]
            case GetNode():
                name = node.config.name
                if name not in frame.variables:
                    # never a silent null (EC19/EC24): a Get racing its
                    # Set means a missing wire, not an empty value
                    raise _NodeError(
                        "variable_unset",
                        f"variable {name!r} was never set in this frame"
                        " (EC19: wire a path from the Set to this trigger)",
                    )
                return [("value", frame.variables[name])]
            case LogNode():
                self._stream.emit(  # masked at emission (D13/D22)
                    LogEvent(
                        frame=frame.id,
                        node=node.id,
                        label=node.config.label,
                        level=node.config.level,
                        value=trigger.value,
                    )
                )
                return [("out", trigger.value)]
            case FixtureNode():
                return [("value", self._load_fixture(node))]
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
        if frame.flow_dir is None:
            raise _NodeError(
                "worker_crash",
                "python nodes need the flow directory (pass flow_dir= to"
                " FlowRun/execute_flow) to locate nodes.py",
            )
        nodes_path = frame.flow_dir / "nodes.py"
        if self._workspace_resolver is not None:
            try:
                relative_dir = frame.flow_dir.resolve(strict=False).relative_to(
                    self._workspace_resolver.root
                )
                if relative_dir.parts:
                    nodes_path = self._workspace_resolver.source_file(
                        relative_dir.as_posix(), "nodes.py"
                    )
                else:
                    nodes_path = self._workspace_resolver.resolve_workspace_path(
                        "nodes.py", label="python source"
                    )
            except (OSError, RuntimeError, ValueError, WorkspaceBoundaryError) as e:
                raise _NodeError(
                    "worker_crash", f"python source violates workspace boundary: {e}"
                ) from e
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
                frame.failed_asserts += 1
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

    # -- child frames: flow + loop (EN §5, FR-404/515/516, D20/D21) -----------

    def _child_graph(self, ref: str) -> tuple[_Graph, Path, Any]:
        """Load a referenced flow once per run. The engine trusts the
        checker for E007 (reference DAG) — runtime recursion is caught
        by the message budget, never by a stack overflow."""
        cached = self._flow_cache.get(ref)
        if cached is not None:
            return cached
        if self._workspace_resolver is None:
            raise _NodeError(
                "flow_load_error",
                "flow references need workspace_root — pass it to FlowRun/execute_flow",
            )
        try:
            flow_dir = self._workspace_resolver.flow_dir(ref)
            loaded = load_flow(self._workspace_resolver.flow_file(ref))
        except WorkspaceBoundaryError as e:
            raise _NodeError(
                "flow_load_error", f"flow {ref!r} violates workspace boundary: {e}"
            ) from e
        except (OSError, LoadError) as e:
            raise _NodeError("flow_load_error", f"cannot load flow {ref!r}: {e}") from e
        entry = (_Graph(loaded.model), flow_dir, loaded.model.env)
        self._flow_cache[ref] = entry
        return entry

    def _spawn_frame(
        self,
        parent: Frame,
        graph: _Graph,
        flow_dir: Path,
        inputs: dict[str, Any],
        *,
        loop_binding: tuple[Any, int] | None = None,
        http: HttpClient | None = None,
    ) -> Frame:
        self._frame_seq += 1
        child = Frame(
            id=f"{parent.id}/f-{self._frame_seq}",
            graph=graph,
            inputs=inputs,
            flow_dir=flow_dir,
            loop_binding=loop_binding,
            http=http or parent.http,
        )
        parent.children.append(child)
        return child

    def _cancel_frame(self, frame: Frame) -> None:
        """Cancel a child subtree (D24): queued deliveries are skipped
        by the pump, firing tasks are cancelled; outcomes the subtree
        already recorded still aggregate (D20)."""
        frame.cancelled = True
        for task in frame.tasks:
            task.cancel()
        for child in frame.children:
            self._cancel_frame(child)

    async def _run_child(self, child: Frame) -> None:
        """Seed a child frame and await its quiescence (the per-frame
        `done` event set by the last decrement). Cancellation (a
        container ceiling) cancels the whole subtree."""
        try:
            self._seed(child)
            if child.in_flight > 0:
                await child.done.wait()
        except asyncio.CancelledError:
            self._cancel_frame(child)
            raise

    def _close_child(self, child: Frame) -> tuple[str, int, list[dict], dict]:
        """Child-frame FINALIZE: the D18 required-End check runs per
        frame (TR-3 cross-frame), then the subtree outcome (D21):
        → (state, failed_asserts, unhandled_entries, end_outputs)."""
        outputs = self._close_end_ports(child)
        failed_asserts, unhandled_count = self._subtree_counts(child)
        prefix = child.id + "/"
        entries = [
            u
            for u in self._unhandled
            if u["frame"] and (u["frame"] == child.id or u["frame"].startswith(prefix))
        ]
        if child.cancelled:
            state = "aborted"
        elif failed_asserts or unhandled_count:
            state = "failed"
        else:
            state = "passed"
        return state, failed_asserts, entries, outputs

    def _subtree_counts(self, frame: Frame) -> tuple[int, int]:
        failed, unhandled = frame.failed_asserts, frame.unhandled_count
        for child in frame.children:
            child_failed, child_unhandled = self._subtree_counts(child)
            failed += child_failed
            unhandled += child_unhandled
        return failed, unhandled

    def _close_end_ports(self, frame: Frame) -> dict[str, Any]:
        """D18, applied to ANY frame at its quiescence: a required End
        port never written fails the run; optional ports note null."""
        outputs: dict[str, Any] = {}
        for port in frame.graph.end.config.ports:
            if port.name in frame.end_values:
                outputs[port.name] = frame.end_values[port.name]
            elif port.required:
                self._record_unhandled(
                    frame,
                    frame.graph.end.id,
                    port=port.name,
                    kind="required_end_unwritten",
                    message=f"required End port '{port.name}' was never written",
                )
            else:
                outputs[port.name] = None  # noted by null (FR-502)
        return outputs

    async def _run_flow_node(
        self, frame: Frame, node: FlowNode, slot_values: dict[str, Any]
    ) -> list[tuple[str, Any]]:
        """FR-516/D21: bind inputs → child Start, await child
        quiescence, emit child End values on derived ports; the
        implicit `error` port fires when the child subtree ended
        failed/error, carrying `{state, failed_asserts,
        unhandled_errors}`. Outcomes aggregate regardless (D20) — a
        wired error port is branching, not absolution."""
        graph, flow_dir, env_cfg = self._child_graph(node.config.flow)
        try:
            self._check_env_required(env_cfg)
            inputs = self._bind(graph.start, slot_values)
        except _BindError as e:
            raise _NodeError(e.reason, f"flow {node.config.flow!r}: {e}") from e
        child = self._spawn_frame(frame, graph, flow_dir, inputs)
        await self._run_child(child)
        state, failed_asserts, entries, port_values = self._close_child(child)
        outputs = list(port_values.items())
        if state != "passed":
            outputs.append(
                (
                    "error",
                    {
                        "state": state,
                        "failed_asserts": failed_asserts,
                        "unhandled_errors": entries,
                    },
                )
            )
        return outputs

    async def _run_loop(
        self, frame: Frame, node: LoopNode, context: dict[str, Any]
    ) -> list[tuple[str, Any]]:
        """FR-515: child frame per item; `results` = end-value dicts of
        PASSED iterations in item-index order (EC36); `errors` = one
        entry per failed iteration, emitted only when non-empty.
        `on_error: stop` gates scheduling only; failed iterations count
        toward the run regardless (EC06/D20). Node-level failures
        (bad `over`, load failure, tripped ceiling) have no outlet:
        unhandled node error, the loop emits nothing (EC24)."""
        cfg = node.config
        items = self._renderer.evaluate(cfg.over, context)
        if not isinstance(items, list):
            raise _NodeError(
                "loop_error",
                f"`over` must evaluate to a list, got {type(items).__name__}",
            )
        graph, flow_dir, env_cfg = self._child_graph(cfg.body)
        try:
            self._check_env_required(env_cfg)
        except _BindError as e:
            raise _NodeError(e.reason, f"loop body {cfg.body!r}: {e}") from e
        declared = {p.name for p in graph.start.config.ports}
        results: dict[int, dict[str, Any]] = {}
        errors: list[dict[str, Any]] = []
        stop = False

        async def run_iteration(index: int, item: Any) -> None:
            nonlocal stop
            given: dict[str, Any] = {"item": item}
            if "index" in declared:
                given["index"] = index
            try:
                inputs = self._bind(graph.start, given)
            except _BindError as e:
                # a frame that cannot start = iteration `error` (EN §5)
                self._record_unhandled(
                    frame,
                    node.id,
                    port=None,
                    kind="bind_error",
                    message=f"iteration {index}: {e}",
                )
                errors.append({"index": index, "state": "error", "message": str(e)})
                stop = True
                return
            http = None
            if cfg.fresh_session:
                http = HttpClient()
                self._extra_http.append(http)
            child = self._spawn_frame(
                frame,
                graph,
                flow_dir,
                inputs,
                loop_binding=(item, index),
                http=http,
            )
            await self._run_child(child)
            state, failed_asserts, entries, port_values = self._close_child(child)
            if http is not None:  # close eagerly; cancellation paths
                await http.close()
                self._extra_http.remove(http)  # close failed/cancelled → run cleanup
            if state == "passed":
                results[index] = port_values
            else:
                errors.append(
                    {
                        "index": index,
                        "state": state,
                        "failed_asserts": failed_asserts,
                        "unhandled_errors": entries,
                    }
                )
                stop = True

        if cfg.mode == "sequential":
            for index, item in enumerate(items):
                if stop and cfg.on_error == "stop":
                    break  # gates scheduling only — never in-flight work
                await run_iteration(index, item)
        else:
            semaphore = asyncio.Semaphore(cfg.max_concurrency)

            async def gated(index: int, item: Any) -> None:
                async with semaphore:
                    if stop and cfg.on_error == "stop":
                        return
                    await run_iteration(index, item)

            await asyncio.gather(*(gated(i, it) for i, it in enumerate(items)))

        outputs: list[tuple[str, Any]] = [
            ("results", [results[i] for i in sorted(results)])
        ]
        if errors:
            errors.sort(key=lambda e: e["index"])
            outputs.append(("errors", errors))
        return outputs

    def _load_fixture(self, node: FixtureNode) -> Any:
        """FR-514: workspace-relative json/csv, read once and cached
        per RUN (keyed by resolved path — a mid-run file change or
        deletion never splits the data). CSV → list of dicts, header
        row required, values stay strings (no inference)."""
        resolver = self._workspace_resolver
        if resolver is None and self._flow_dir is not None:
            resolver = WorkspaceResolver(self._flow_dir)
        if resolver is None:
            raise _NodeError(
                "fixture_error",
                "fixture nodes need workspace_root (or flow_dir) to"
                " resolve files — pass it to FlowRun/execute_flow",
            )
        try:
            path = resolver.fixture_file(node.config.file)
        except WorkspaceBoundaryError as e:
            raise _NodeError(
                "fixture_error",
                f"fixture {node.config.file!r} violates workspace boundary: {e}",
            ) from e
        key = str(path)
        if key in self._fixture_cache:
            return self._fixture_cache[key]
        fmt = node.config.format
        if fmt is None:
            fmt = {".json": "json", ".csv": "csv"}.get(path.suffix.lower())
            if fmt is None:
                raise _NodeError(
                    "fixture_error",
                    f"cannot infer format from {path.suffix!r}"
                    " — set config.format to json or csv",
                )
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            raise _NodeError(
                "fixture_error", f"cannot read fixture {node.config.file!r}: {e}"
            ) from e
        if fmt == "json":
            try:
                value: Any = json.loads(text)
            except ValueError as e:
                raise _NodeError(
                    "fixture_error", f"invalid JSON in {node.config.file!r}: {e}"
                ) from e
        else:
            reader = csv.DictReader(io.StringIO(text))
            if reader.fieldnames is None:
                raise _NodeError(
                    "fixture_error",
                    f"{node.config.file!r} is empty (CSV needs a header row)",
                )
            rows = []
            for line_no, row in enumerate(reader, start=2):
                if None in row:  # DictReader parks extra fields under None
                    raise _NodeError(
                        "fixture_error",
                        f"{node.config.file!r} line {line_no} has more"
                        " fields than the header",
                    )
                rows.append(dict(row))
            value = rows
        self._fixture_cache[key] = value
        return value

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
        http = frame.http  # fresh_session loops carry their own session
        if http is None:
            if self._http is None:
                self._http = HttpClient()
            http = self._http
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
                wire = await http.request(
                    method=cfg["method"],
                    url=cfg["url"],
                    headers=cfg["headers"],
                    query=cfg["query"],
                    body=cfg["body"],
                    timeout_s=cfg["timeout_s"],
                    verify_tls=cfg["verify_tls"],
                    http_version=node.config.http_version,
                )
            except RequestEncodingError as e:
                self._stream.emit(
                    RequestFailed(
                        frame=frame.id,
                        node=node.id,
                        error_kind="request_encoding",
                        message=str(e),
                        attempt=attempt,
                        will_retry=False,
                    )
                )
                raise _NodeError(
                    "request_encoding", f"request body encoding failed: {e}"
                ) from e
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
                frame.failed_asserts += 1
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
        frame.unhandled_count += 1
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
            # D18: same required-End check every frame gets (TR-3);
            # child frames already closed at their own quiescence
            end_outputs = self._close_end_ports(frame)

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
        context = {
            "env": self._env,
            "inputs": frame.inputs,
            "run": self._run_context(),
            "nodes": frame.node_outputs,
            "trigger": trigger.envelope(),
        }
        if frame.loop_binding is not None:  # loop bodies (EN §6, FR-602)
            context["item"], context["index"] = frame.loop_binding
        return context


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
    workspace_resolver: WorkspaceResolver | None = None,
) -> RunResult:
    """Run one flow to quiescence (BIND → EXECUTE → FINALIZE). The
    caller owns LOAD/CHECK; once execution starts, the run owns and closes
    the event stream and its sinks (D36). `flow_dir` locates nodes.py;
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
        workspace_resolver=workspace_resolver,
    )
    return await run.execute()
