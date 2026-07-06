"""Run registry: live runs, event fan-out to WebSockets, summaries.

The engine executes as an asyncio task on the SERVER's event loop, so
`_BufferSink.write` is called synchronously on that loop — buffer
appends and subscriber handoff need no locks. Records arrive born
masked (D22); the JSONL on disk stays the durable record — live
buffers are dropped the moment a run finishes, late consumers replay
the file (D13: replay = re-read).
"""

import asyncio
import contextlib
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from napflow.core.engine import FlowRun, RunResult
from napflow.core.runprep import PreparedRun, open_run_stream
from napflow.core.workspace import Workspace

logger = logging.getLogger("napflow.server")

# Finished-run summaries kept in memory for status polls; event detail
# always lives in the JSONL.
FINISHED_KEPT = 32

RunRecord = dict[str, Any]


class _BufferSink:
    """EventStream sink feeding the live buffer + subscriber queues.
    `run` is bound right after the stream is opened (the ActiveRun
    needs the stream's run_id first)."""

    run: "ActiveRun"

    def write(self, record: RunRecord) -> None:
        self.run.buffer.append(record)
        for queue in self.run.subscribers:
            queue.put_nowait(record)

    def close(self) -> None:
        pass


@dataclass
class ActiveRun:
    run_id: str
    identity: str
    log_path: Path
    flow_run: FlowRun
    task: asyncio.Task | None = None
    buffer: list[RunRecord] = field(default_factory=list)
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    result: RunResult | None = None
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
                "unhandled_errors": self.result.unhandled_errors,
                "nodes_never_fired": self.result.nodes_never_fired,
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

    def get(self, run_id: str) -> ActiveRun | None:
        return self._runs.get(run_id)

    def start(
        self, workspace: Workspace, prepared: PreparedRun, inputs: dict[str, Any]
    ) -> ActiveRun:
        """Wire stream + engine and schedule the run on the current
        loop. Caller has already passed the prepare_run gate."""
        sink = _BufferSink()
        opened = open_run_stream(workspace, prepared, extra_sinks=[sink])
        flow_run = FlowRun(
            prepared.loaded.model,
            flow_identity=prepared.identity,
            manifest=workspace.manifest.model,
            env=prepared.env,
            env_name=prepared.env_name,
            inputs=inputs,
            stream=opened.stream,
            flow_dir=workspace.root / Path(prepared.identity),
            workspace_root=workspace.root,
        )
        run = ActiveRun(
            run_id=opened.run_id,
            identity=prepared.identity,
            log_path=opened.log_path,
            flow_run=flow_run,
        )
        sink.run = run
        self._runs[opened.run_id] = run
        run.task = asyncio.get_running_loop().create_task(
            self._drive(run, opened.stream), name=f"napf-run-{opened.run_id}"
        )
        return run

    async def _drive(self, run: ActiveRun, stream: Any) -> None:
        try:
            run.result = await run.flow_run.execute()
        except Exception as e:  # engine bug — the server must survive it
            run.crashed = f"{type(e).__name__}: {e}"
            logger.exception("run %s crashed", run.run_id)
        finally:
            stream.close()
            # JSONL is the durable record; live subscribers already got
            # every record. None = end-of-stream sentinel.
            run.buffer.clear()
            for queue in run.subscribers:
                queue.put_nowait(None)
            self._trim()

    def subscribe(self, run: ActiveRun) -> tuple[list[RunRecord], asyncio.Queue]:
        """Snapshot + live queue, atomically (no awaits): every record
        lands in exactly one of the two. Call only on unfinished runs —
        finished ones replay from the JSONL."""
        queue: asyncio.Queue = asyncio.Queue()
        snapshot = list(run.buffer)
        run.subscribers.add(queue)
        return snapshot, queue

    def unsubscribe(self, run: ActiveRun, queue: asyncio.Queue) -> None:
        run.subscribers.discard(queue)

    def _trim(self) -> None:
        finished = [r for r in self._runs.values() if r.finished]
        for stale in finished[: max(0, len(finished) - FINISHED_KEPT)]:
            del self._runs[stale.run_id]

    async def shutdown(self) -> None:
        """Abort anything still running (server stop = client abort)."""
        pending = [r.task for r in self._runs.values() if not r.finished and r.task]
        for run in self._runs.values():
            if not run.finished:
                run.flow_run.abort()
        for task in pending:
            # best-effort drain; crashes are logged in _drive
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.shield(task), timeout=10)
