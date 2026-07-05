"""Engine-side python worker management (EN §5a, FR-901–906).

One persistent worker subprocess per flow module (nodes.py), spawned
lazily at first use inside the firing's `max_seconds` window, killed at
FINALIZE. Tasks are serial per worker (FR-902); a timeout-cancelled
task marks the worker dead and kills it in the background
(terminate → 2s grace → kill, FR-903); the next firing respawns
lazily. Worker death is a node error, never an engine failure
(FR-904). The child script (`worker_main.py`) is stdlib-only and
invoked by path — the configured interpreter (FR-108) need not have
napflow installed.
"""

import asyncio
import json
import os
import subprocess
import sys
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

_WORKER_MAIN = Path(__file__).with_name("worker_main.py")
_GRACE_S = 2.0  # terminate → grace → kill (FR-903)
_STDERR_TAIL = 15  # lines kept for crash messages

# (level, (frame_id, node_id), text) → LogEvent emission in the engine
LogFn = Callable[[str, tuple[str, str], str], None]

# FR-906: no console window flashes on Windows; 0 elsewhere
_CREATIONFLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


class WorkerTaskError(Exception):
    """The function raised (or I/O wasn't JSON-serializable): routed to
    the node's error port; `python_assert` also counts as a failed
    assert run-wide (FR-506)."""

    def __init__(self, kind: str, message: str, error_type: str, traceback_text: str):
        self.kind = kind
        self.error_type = error_type
        self.traceback_text = traceback_text
        super().__init__(message)


class WorkerCrash(Exception):
    """Worker spawn failure, import failure, or process death — a node
    error with kind `worker_crash` (FR-904). `before_send` marks deaths
    detected before OUR task went over the pipe: safe to retry once on
    a fresh worker (the lazy-respawn path for queued firings)."""

    def __init__(self, message: str, *, before_send: bool = False):
        self.before_send = before_send
        super().__init__(message)


class _Worker:
    def __init__(
        self, interpreter: str, nodes_path: Path, cwd: Path | None, on_log: LogFn
    ):
        self._interpreter = interpreter
        self._nodes_path = nodes_path
        self._cwd = cwd
        self._on_log = on_log
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()  # FR-902: serial task processing
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._seq = 0
        self._label: tuple[str, str] = ("f-0", "python")
        self._stderr_tail: deque[str] = deque(maxlen=_STDERR_TAIL)
        self._reader: asyncio.Task[None] | None = None
        self._stderr_reader: asyncio.Task[None] | None = None
        self._ready: asyncio.Future[None] | None = None
        self.dead = False

    async def start(self) -> None:
        try:
            self._proc = await asyncio.create_subprocess_exec(
                self._interpreter,
                "-u",
                str(_WORKER_MAIN),
                str(self._nodes_path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd,
                creationflags=_CREATIONFLAGS,
            )
        except OSError as e:
            self.dead = True
            raise WorkerCrash(
                f"cannot spawn python worker ({self._interpreter}): {e}"
            ) from e
        self._ready = asyncio.get_running_loop().create_future()
        self._reader = asyncio.create_task(self._pump_protocol())
        self._stderr_reader = asyncio.create_task(self._pump_stderr())
        await self._ready  # WorkerCrash on import failure / early death

    async def call(
        self, function: str, inputs: dict[str, Any], label: tuple[str, str]
    ) -> Any:
        async with self._lock:
            if self.dead:  # died while we were queued — retriable
                raise WorkerCrash(
                    "worker died before the task was sent", before_send=True
                )
            self._label = label
            self._seq += 1
            task_id = f"t-{self._seq}"
            try:
                line = json.dumps(
                    {"task_id": task_id, "function": function, "inputs": inputs}
                )
            except (TypeError, ValueError) as e:
                raise WorkerTaskError(
                    "python_error",
                    f"inputs are not JSON-serializable: {e}",
                    "TypeError",
                    "",
                ) from e
            future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
            self._pending[task_id] = future
            assert self._proc is not None and self._proc.stdin is not None
            try:
                self._proc.stdin.write(line.encode("utf-8") + b"\n")
                await self._proc.stdin.drain()
                return await future
            except asyncio.CancelledError:
                # engine `max_seconds` tripped (FR-903): kill in the
                # background, respawn lazily on the next firing
                self.dead = True
                self._pending.pop(task_id, None)
                raise
            except (ConnectionError, OSError) as e:  # write into a dead pipe
                raise WorkerCrash(self._death_message()) from e

    async def _pump_protocol(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            raw = await self._proc.stdout.readline()
            if not raw:
                break
            try:
                msg = json.loads(raw)
            except ValueError:  # unreachable via nodes.py (EC28)
                continue
            if "stream" in msg:
                level = "info" if msg["stream"] == "stdout" else "warn"
                self._on_log(level, self._label, str(msg.get("text", "")))
            elif "ready" in msg:
                if self._ready is not None and not self._ready.done():
                    self._ready.set_result(None)
            elif "fatal" in msg:
                self.dead = True
                error = WorkerCrash(
                    f"{msg.get('fatal', 'worker failed')}\n{msg.get('traceback', '')}"
                )
                if self._ready is not None and not self._ready.done():
                    self._ready.set_exception(error)
            elif "task_id" in msg:
                future = self._pending.pop(msg["task_id"], None)
                if future is None or future.done():
                    continue
                if "error" in msg:
                    future.set_exception(
                        WorkerTaskError(
                            msg.get("error_kind", "python_error"),
                            msg["error"],
                            msg.get("error_type", "Exception"),
                            msg.get("traceback", ""),
                        )
                    )
                else:
                    future.set_result(msg.get("outputs"))
        # EOF: the process died (or exited at close) — crash any waiters
        self.dead = True
        error = WorkerCrash(self._death_message())
        if self._ready is not None and not self._ready.done():
            self._ready.set_exception(error)
        for future in self._pending.values():
            if not future.done():
                future.set_exception(WorkerCrash(self._death_message()))
        self._pending.clear()

    async def _pump_stderr(self) -> None:
        # raw fd-1/fd-2 writers land here (EC28) → log events + crash tail
        assert self._proc is not None and self._proc.stderr is not None
        while True:
            raw = await self._proc.stderr.readline()
            if not raw:
                return
            text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            self._stderr_tail.append(text)
            self._on_log("warn", self._label, text)

    def _death_message(self) -> str:
        code = self._proc.returncode if self._proc is not None else None
        shown = code if code is not None else "unknown"
        message = f"worker process died (exit code {shown})"
        if self._stderr_tail:
            message += "; stderr tail:\n" + "\n".join(self._stderr_tail)
        return message

    async def shutdown(self) -> None:
        """EOF for a graceful exit, then terminate → grace → kill."""
        proc = self._proc
        if proc is None:
            return
        self.dead = True
        if proc.returncode is None:
            if proc.stdin is not None:
                with suppress(OSError, ConnectionResetError):
                    proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), _GRACE_S)
            except TimeoutError:
                await self.kill()
        for task in (self._reader, self._stderr_reader):
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    async def kill(self) -> None:
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        with suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), _GRACE_S)
        except TimeoutError:
            with suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()


class WorkerPool:
    """One worker per flow module, keyed by nodes.py path, spawned
    lazily (FR-901). Cap enforcement is deferred until multi-flow runs
    make >1 module possible (S3/M5) — a run holds one module today."""

    def __init__(self, interpreter: str, cwd: Path | None, on_log: LogFn):
        self._interpreter = interpreter
        self._cwd = cwd
        self._on_log = on_log
        self._workers: dict[Path, _Worker] = {}
        self._spawn_lock = asyncio.Lock()
        self._kill_tasks: set[asyncio.Task[None]] = set()

    async def call(
        self,
        nodes_path: Path,
        function: str,
        inputs: dict[str, Any],
        label: tuple[str, str],
    ) -> Any:
        for attempt in (1, 2):
            worker = await self._get_or_spawn(nodes_path)
            try:
                return await worker.call(function, inputs, label)
            except asyncio.CancelledError:
                self._reap_in_background(worker)
                raise
            except WorkerCrash as e:
                # died while queued (pre-send): retry once on a fresh
                # worker — the lazy-respawn path for queued firings
                if not (e.before_send and attempt == 1):
                    raise
        raise AssertionError("unreachable: retry loop returns or raises")

    async def _get_or_spawn(self, nodes_path: Path) -> _Worker:
        async with self._spawn_lock:
            worker = self._workers.get(nodes_path)
            if worker is not None and not worker.dead:
                return worker
            if worker is not None:
                self._reap_in_background(worker)
            worker = _Worker(self._interpreter, nodes_path, self._cwd, self._on_log)
            self._workers[nodes_path] = worker
            try:
                await worker.start()
            except asyncio.CancelledError:
                self._reap_in_background(worker)
                raise
            return worker

    def _reap_in_background(self, worker: _Worker) -> None:
        worker.dead = True
        task = asyncio.create_task(worker.shutdown())
        self._kill_tasks.add(task)
        task.add_done_callback(self._kill_tasks.discard)

    async def close(self) -> None:
        """Kill every worker at FINALIZE (EN §5a)."""
        workers = list(self._workers.values())
        self._workers.clear()
        for worker in workers:
            await worker.shutdown()
        if self._kill_tasks:
            await asyncio.gather(*self._kill_tasks, return_exceptions=True)


def default_interpreter() -> str:
    return sys.executable
