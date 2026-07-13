"""Run registry: bounded live fan-out plus scalar run summaries.

The engine executes as an asyncio task on the server event loop, so sink
writes and subscriber registration need no in-process locks. The flushed
JSONL is the durable prefix; the registry retains no all-event copy. Each
subscriber gets a bounded queue and must resync from that durable log if it
falls behind (D13/D36).
"""

import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from napflow.core.engine import FlowRun, RunResult
from napflow.core.events import apply_retention, end_history_reader
from napflow.core.runprep import (
    OpenedRun,
    PreparedRun,
    finalize_run_history,
    open_run_stream,
)
from napflow.core.workspace import Workspace

logger = logging.getLogger("napflow.server")

# Finished-run summaries kept in memory for status polls; event detail
# always lives in the JSONL.
FINISHED_KEPT = 32
SUBSCRIBER_QUEUE_LIMIT = 256
SUBSCRIBERS_PER_RUN_LIMIT = 8
RESYNC_LEASE_S = 5.0

RunRecord = dict[str, Any]
SUBSCRIBER_END = object()
SUBSCRIBER_RESYNC = object()


class SubscriberLimitError(RuntimeError):
    pass


@dataclass(eq=False)
class RunSubscriber:
    queue: asyncio.Queue[RunRecord | object] = field(
        default_factory=lambda: asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_LIMIT)
    )
    overflowed: bool = False

    def offer(self, item: RunRecord | object) -> None:
        """Queue one item or collapse backlog to a single resync signal."""
        if self.overflowed:
            return
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            self.overflowed = True
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait(SUBSCRIBER_RESYNC)


@dataclass(frozen=True)
class FinishedSummary:
    state: str
    duration_ms: float
    asserts_passed: int
    asserts_failed: int
    unhandled_error_count: int
    nodes_never_fired_count: int
    error_reason: str | None

    @classmethod
    def from_result(cls, result: RunResult) -> "FinishedSummary":
        return cls(
            state=result.state,
            duration_ms=result.duration_ms,
            asserts_passed=result.asserts_passed,
            asserts_failed=result.asserts_failed,
            unhandled_error_count=len(result.unhandled_errors),
            nodes_never_fired_count=len(result.nodes_never_fired),
            error_reason=result.error_reason,
        )


class _LiveSink:
    """EventStream sink feeding bounded subscriber queues.
    `run` is bound right after the stream is opened (the ActiveRun
    needs the stream's run_id first)."""

    run: "ActiveRun"

    def write(self, record: RunRecord) -> None:
        self.run.last_seq = record["seq"]
        for subscriber in self.run.subscribers:
            subscriber.offer(record)

    def close(self) -> None:
        pass


@dataclass
class ActiveRun:
    run_id: str
    identity: str
    log_path: Path
    flow_run: FlowRun | None
    history_limit: int = 20
    task: asyncio.Task | None = None
    last_seq: int = 0
    subscribers: set[RunSubscriber] = field(default_factory=set)
    replay_readers: int = 0
    resync_until: float = 0.0
    result: FinishedSummary | None = None
    crashed: str | None = None  # engine bug surfaced as state=error

    @property
    def finished(self) -> bool:
        return self.result is not None or self.crashed is not None

    @property
    def state(self) -> str:
        if self.result is not None:
            return self.result.state
        if self.crashed is not None:
            return "error"
        return "running"

    def status(self) -> dict[str, Any]:
        """The GET /api/runs/{run_id} payload."""
        payload: dict[str, Any] = {
            "run_id": self.run_id,
            "flow": self.identity,
            "state": self.state,
        }
        if self.result is not None:
            payload |= {
                "duration_ms": self.result.duration_ms,
                "asserts": {
                    "passed": self.result.asserts_passed,
                    "failed": self.result.asserts_failed,
                },
                "unhandled_error_count": self.result.unhandled_error_count,
                "nodes_never_fired_count": self.result.nodes_never_fired_count,
            }
            if self.result.error_reason is not None:
                payload["error_reason"] = self.result.error_reason
        elif self.crashed is not None:
            payload["error_reason"] = self.crashed
        return payload


class RunManager:
    """All runs started through this server process, newest last.
    Running entries are never evicted; finished ones are capped."""

    def __init__(self) -> None:
        self._runs: OrderedDict[str, ActiveRun] = OrderedDict()
        self._deferred_readers: dict[
            Path, tuple[asyncio.TimerHandle, Path, int]
        ] = {}

    def get(self, run_id: str) -> ActiveRun | None:
        return self._runs.get(run_id)

    def start(
        self, workspace: Workspace, prepared: PreparedRun, inputs: dict[str, Any]
    ) -> ActiveRun:
        """Wire stream + engine and schedule the run on the current
        loop. Caller has already passed the prepare_run gate."""
        sink = _LiveSink()
        opened = open_run_stream(workspace, prepared, extra_sinks=[sink])
        try:
            flow_run = FlowRun(
                prepared.loaded.model,
                flow_identity=prepared.identity,
                manifest=workspace.manifest.model,
                env=prepared.env,
                env_name=prepared.env_name,
                inputs=inputs,
                stream=opened.stream,
                flow_dir=workspace.resolver.flow_dir(prepared.identity),
                workspace_root=workspace.root,
                workspace_resolver=workspace.resolver,
            )
        except BaseException:
            opened.stream.close()
            try:
                finalize_run_history(opened, completed=False)
            except Exception:
                logger.exception(
                    "run %s history abandonment failed", opened.run_id
                )
            raise
        run = ActiveRun(
            run_id=opened.run_id,
            identity=prepared.identity,
            log_path=opened.log_path,
            flow_run=flow_run,
            history_limit=opened.history_limit,
        )
        sink.run = run
        self._runs[opened.run_id] = run
        run.task = asyncio.get_running_loop().create_task(
            self._drive(run, opened), name=f"napf-run-{opened.run_id}"
        )
        return run

    async def _drive(self, run: ActiveRun, opened: OpenedRun) -> None:
        completed = False
        try:
            assert run.flow_run is not None
            result = await run.flow_run.execute()
            run.result = FinishedSummary.from_result(result)
            completed = True
        except asyncio.CancelledError:
            run.crashed = "CancelledError: server run task cancelled"
            raise
        except Exception as e:  # engine bug — the server must survive it
            run.crashed = f"{type(e).__name__}: {e}"[:1024]
            logger.exception("run %s crashed", run.run_id)
        finally:
            opened.stream.close()
            # The engine owns inputs, caches, outputs and diagnostic detail;
            # the durable log owns history once execution stops.
            run.flow_run = None
            for subscriber in run.subscribers:
                subscriber.offer(SUBSCRIBER_END)
            try:
                deleted = finalize_run_history(opened, completed=completed)
            except Exception:
                logger.exception("run %s history finalization failed", run.run_id)
                try:
                    finalize_run_history(opened, completed=False)
                except Exception:
                    logger.exception(
                        "run %s history abandonment failed", run.run_id
                    )
            else:
                for log_path in deleted:
                    self._runs.pop(log_path.stem, None)
            self._trim()

    def subscribe(self, run: ActiveRun) -> tuple[int, RunSubscriber]:
        """Register at an exact durable-prefix boundary, without an await.

        The JSONL sink flushes before this fan-out sink, so records through
        ``last_seq`` are already readable from disk. Later records land in the
        bounded queue while the adapter replays that prefix.
        """
        if run.finished:
            raise RuntimeError("finished runs must replay directly from JSONL")
        if len(run.subscribers) + run.replay_readers >= SUBSCRIBERS_PER_RUN_LIMIT:
            raise SubscriberLimitError("too many live run subscribers")
        subscriber = RunSubscriber()
        through_seq = run.last_seq
        run.subscribers.add(subscriber)
        return through_seq, subscriber

    def resubscribe(
        self, run: ActiveRun, previous: RunSubscriber
    ) -> tuple[int, RunSubscriber]:
        """Atomically replace an overflowed queue at a new disk cutoff."""
        subscriber = RunSubscriber()
        through_seq = run.last_seq
        run.subscribers.discard(previous)
        run.subscribers.add(subscriber)
        if run.finished:
            subscriber.offer(SUBSCRIBER_END)
        return through_seq, subscriber

    def unsubscribe(self, run: ActiveRun, subscriber: RunSubscriber) -> None:
        run.subscribers.discard(subscriber)

    def pin_replay(self, run: ActiveRun) -> None:
        if len(run.subscribers) + run.replay_readers >= SUBSCRIBERS_PER_RUN_LIMIT:
            raise SubscriberLimitError("too many run replay readers")
        run.replay_readers += 1

    def unpin_replay(self, run: ActiveRun) -> None:
        run.replay_readers = max(0, run.replay_readers - 1)

    def reserve_resync(self, run: ActiveRun) -> None:
        run.resync_until = max(
            run.resync_until,
            asyncio.get_running_loop().time() + RESYNC_LEASE_S,
        )

    def release_history_reader(
        self, log_path: Path, lease: Path, history_limit: int
    ) -> None:
        """Release a lease, then enforce retention and reconcile the registry."""
        try:
            end_history_reader(lease)
        except OSError:
            logger.exception("history reader lease release failed: %s", lease)
            return
        try:
            deleted = apply_retention(log_path.parent, history_limit)
        except OSError:
            logger.exception("post-reader retention failed: %s", log_path.parent)
            return
        for deleted_log in deleted:
            self._runs.pop(deleted_log.stem, None)
        self._trim()

    def defer_history_reader_release(
        self, log_path: Path, lease: Path, history_limit: int
    ) -> None:
        """Keep a reconnect lease briefly, but make shutdown own its cleanup."""
        handle = asyncio.get_running_loop().call_later(
            RESYNC_LEASE_S, self._release_deferred_reader, lease
        )
        self._deferred_readers[lease] = (handle, log_path, history_limit)

    def _release_deferred_reader(self, lease: Path) -> None:
        deferred = self._deferred_readers.pop(lease, None)
        if deferred is None:
            return
        _handle, log_path, history_limit = deferred
        self.release_history_reader(log_path, lease, history_limit)

    def _trim(self) -> None:
        now = asyncio.get_running_loop().time()
        finished = [
            run
            for run in self._runs.values()
            if run.finished
            and not run.subscribers
            and run.replay_readers == 0
            and run.resync_until <= now
        ]
        for stale in finished[: max(0, len(finished) - FINISHED_KEPT)]:
            del self._runs[stale.run_id]

    async def shutdown(self) -> None:
        """Abort anything still running (server stop = client abort)."""
        pending = [
            run.task
            for run in self._runs.values()
            if run.task is not None and not run.task.done()
        ]
        for run in self._runs.values():
            if not run.finished and run.flow_run is not None:
                run.flow_run.abort()
        try:
            if pending:
                results = await asyncio.wait_for(
                    asyncio.gather(
                        *(asyncio.shield(task) for task in pending),
                        return_exceptions=True,
                    ),
                    timeout=10,
                )
                errors = [
                    result
                    for result in results
                    if isinstance(result, BaseException)
                    and not isinstance(result, asyncio.CancelledError)
                ]
                if errors:
                    raise errors[0]
        finally:
            for lease, (handle, log_path, history_limit) in list(
                self._deferred_readers.items()
            ):
                handle.cancel()
                self._deferred_readers.pop(lease, None)
                self.release_history_reader(log_path, lease, history_limit)
