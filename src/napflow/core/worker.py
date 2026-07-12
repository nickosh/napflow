"""Engine-side python worker management (EN §5a, FR-901–906).

One persistent worker subprocess per flow module (nodes.py), spawned
lazily at first use inside the firing's `max_seconds` window, closed at
FINALIZE. Tasks are serial per worker (FR-902); a timeout-cancelled
task marks the worker dead and terminates it before cancellation returns
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
_STDERR_TAIL_LINE_CAP = 8192

# asyncio's default subprocess StreamReader limit is only 64 KiB.  The
# protocol deliberately supports the M0 10 MiB round-trip probe and rejects
# larger single JSON records at a documented, bounded 16 MiB ceiling.  The
# child mirrors this value so ordinary oversize task replies become compact
# WorkerTaskError messages; this parent-side limit also contains a malformed
# or non-cooperating child without leaking LimitOverrunError/ValueError.
_PROTOCOL_LINE_LIMIT = 16 * 1024 * 1024
_PROTOCOL_LIMIT_LABEL = "16 MiB"

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


class _ProtocolError(Exception):
    """Invalid or over-limit child protocol data."""


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
        self._lifecycle_lock = asyncio.Lock()
        self._normal_shutdown = False
        self._terminal_error: WorkerCrash | None = None
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
                limit=_PROTOCOL_LINE_LIMIT,
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
                    {"task_id": task_id, "function": function, "inputs": inputs},
                    ensure_ascii=False,
                ).encode("utf-8")
            except (TypeError, ValueError) as e:
                raise WorkerTaskError(
                    "python_error",
                    f"inputs are not JSON-serializable: {e}",
                    "TypeError",
                    "",
                ) from e
            if len(line) > _PROTOCOL_LINE_LIMIT:
                raise WorkerTaskError(
                    "python_error",
                    "python worker request exceeds the "
                    f"{_PROTOCOL_LIMIT_LABEL} JSON-line limit",
                    "WorkerProtocolError",
                    "",
                )
            future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
            self._pending[task_id] = future
            assert self._proc is not None and self._proc.stdin is not None
            try:
                self._proc.stdin.write(line + b"\n")
                await self._proc.stdin.drain()
                return await future
            except asyncio.CancelledError:
                # The pool synchronously terminates this worker before the
                # cancellation is allowed to escape (D36/EC43).
                self.dead = True
                self._pending.pop(task_id, None)
                raise
            except (ConnectionError, OSError) as e:  # write into a dead pipe
                self._pending.pop(task_id, None)
                await self._reap()
                error = WorkerCrash(self._death_message())
                self._record_failure(error)
                await self.terminate()
                raise error from e

    async def _pump_protocol(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            while True:
                try:
                    raw = await self._proc.stdout.readline()
                except ValueError as e:
                    raise _ProtocolError(
                        f"response exceeded the {_PROTOCOL_LIMIT_LABEL} JSON-line limit"
                    ) from e
                if not raw:
                    break
                try:
                    msg = json.loads(raw)
                except (UnicodeDecodeError, ValueError) as e:
                    raise _ProtocolError("malformed JSON response") from e
                if not isinstance(msg, dict):
                    raise _ProtocolError("response must be a JSON object")
                kinds = [
                    key for key in ("stream", "ready", "fatal", "task_id") if key in msg
                ]
                if len(kinds) != 1:
                    raise _ProtocolError("response has an ambiguous or unknown shape")
                if "stream" in msg:
                    stream = msg["stream"]
                    if stream not in {"stdout", "stderr"}:
                        raise _ProtocolError(f"unknown stream {stream!r}")
                    level = "info" if stream == "stdout" else "warn"
                    self._on_log(level, self._label, str(msg.get("text", "")))
                elif "ready" in msg:
                    if msg["ready"] is not True:
                        raise _ProtocolError("invalid ready handshake")
                    if self._ready is None or self._ready.done():
                        raise _ProtocolError("duplicate ready handshake")
                    self._ready.set_result(None)
                elif "fatal" in msg:
                    error = WorkerCrash(
                        f"{msg.get('fatal', 'worker failed')}\n"
                        f"{msg.get('traceback', '')}"
                    )
                    self._record_failure(error)
                    await self._terminate_process()
                    return
                elif "task_id" in msg:
                    task_id = msg["task_id"]
                    if not isinstance(task_id, str):
                        raise _ProtocolError("task_id must be a string")
                    has_error = "error" in msg
                    has_outputs = "outputs" in msg
                    if has_error == has_outputs:
                        raise _ProtocolError(
                            "task response must have exactly one of outputs or error"
                        )
                    future = self._pending.pop(task_id, None)
                    if future is None or future.done():
                        continue
                    if has_error:
                        future.set_exception(
                            WorkerTaskError(
                                str(msg.get("error_kind", "python_error")),
                                str(msg["error"]),
                                str(msg.get("error_type", "Exception")),
                                str(msg.get("traceback", "")),
                            )
                        )
                    else:
                        future.set_result(msg["outputs"])
        except asyncio.CancelledError:
            raise
        except _ProtocolError as e:
            self._record_failure(WorkerCrash(f"worker protocol error: {e}"))
            await self._terminate_process()
            return
        except Exception as e:
            self._record_failure(
                WorkerCrash(f"worker protocol reader failed: {type(e).__name__}: {e}")
            )
            await self._terminate_process()
            return

        # EOF: the process died (or exited at normal close). Reap FIRST:
        # on the Windows Proactor loop EOF can race exit-status collection.
        self.dead = True
        await self._reap()
        if self._normal_shutdown:
            if self._pending:
                self._record_failure(
                    WorkerCrash("python worker shut down before a task completed")
                )
            return
        self._record_failure(self._terminal_error or WorkerCrash(self._death_message()))
        if self._proc.returncode is None:
            await self._terminate_process()

    def _record_failure(self, error: WorkerCrash) -> None:
        """Publish one stable terminal cause to readiness and task waiters."""
        self.dead = True
        if self._terminal_error is None:
            self._terminal_error = error
        terminal = self._terminal_error
        if self._ready is not None and not self._ready.done():
            self._ready.set_exception(terminal)
        for future in self._pending.values():
            if not future.done():
                future.set_exception(terminal)
        self._pending.clear()

    async def _reap(self) -> None:
        """Collect the exit status so `_death_message` can report it.
        Bounded: a process that closed stdout yet lives on (possible,
        pathological) must not hang the reader — report unknown then."""
        if self._proc is None or self._proc.returncode is not None:
            return
        with suppress(TimeoutError):
            await asyncio.wait_for(self._proc.wait(), _GRACE_S)

    async def _pump_stderr(self) -> None:
        # raw fd-1/fd-2 writers land here (EC28) → log events + crash tail
        assert self._proc is not None and self._proc.stderr is not None
        try:
            while True:
                try:
                    raw = await self._proc.stderr.readline()
                except ValueError as e:
                    raise _ProtocolError(
                        "stderr line exceeded the "
                        f"{_PROTOCOL_LIMIT_LABEL} pipe-reader limit"
                    ) from e
                if not raw:
                    return
                text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                self._stderr_tail.append(text[:_STDERR_TAIL_LINE_CAP])
                self._on_log("warn", self._label, text)
        except asyncio.CancelledError:
            raise
        except _ProtocolError as e:
            self._record_failure(WorkerCrash(f"worker protocol error: {e}"))
            await self._terminate_process()
        except Exception as e:
            self._record_failure(
                WorkerCrash(f"worker stderr reader failed: {type(e).__name__}: {e}")
            )
            await self._terminate_process()

    def _death_message(self) -> str:
        code = self._proc.returncode if self._proc is not None else None
        shown = code if code is not None else "unknown"
        message = f"worker process died (exit code {shown})"
        if self._stderr_tail:
            message += "; stderr tail:\n" + "\n".join(self._stderr_tail)
        return message

    async def shutdown(self) -> None:
        """Normal finalization: send EOF and let an idle child exit cleanly.

        This path is deliberately distinct from :meth:`terminate`, which is
        used immediately for timeout, cancellation, and protocol failure.
        """
        proc = self._proc
        if proc is None:
            return
        self.dead = True
        async with self._lifecycle_lock:
            if proc.returncode is None:
                self._normal_shutdown = True
                if proc.stdin is not None:
                    with suppress(OSError, ConnectionResetError):
                        proc.stdin.close()
                try:
                    await asyncio.wait_for(proc.wait(), _GRACE_S)
                except TimeoutError:
                    self._normal_shutdown = False
                    self._record_failure(
                        WorkerCrash("python worker did not exit after normal EOF")
                    )
                    await self._terminate_process_locked()
        await self._join_pumps()

    async def _terminate_process(self) -> None:
        async with self._lifecycle_lock:
            await self._terminate_process_locked()

    async def _terminate_process_locked(self) -> None:
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        self._normal_shutdown = False
        with suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), _GRACE_S)
        except TimeoutError:
            with suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()

    async def _join_pumps(self) -> None:
        """Retrieve reader outcomes and bound pipe finalization."""
        current = asyncio.current_task()
        tasks = [
            task
            for task in (self._reader, self._stderr_reader)
            if task is not None and task is not current
        ]
        if not tasks:
            return
        done, pending = await asyncio.wait(tasks, timeout=_GRACE_S)
        for task in pending:
            task.cancel()
        await asyncio.gather(*done, *pending, return_exceptions=True)

    async def terminate(self) -> None:
        """Abnormal teardown: terminate now, then grace, then hard kill."""
        self.dead = True
        await self._terminate_process()
        await self._join_pumps()

    async def kill(self) -> None:
        """Compatibility alias for abnormal teardown used by diagnostics."""
        await self.terminate()


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
                # `_Worker.call` marks the process dead only after this task
                # acquires the serial lock and can have crossed the pipe. A
                # queued firing cancelled before send must not kill an
                # unrelated in-flight firing on the same module.
                if worker.dead:
                    await self._terminate_cancel_safe(worker)
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
                # Never overlap a replacement with a process that can still
                # commit work (D36/EC43).
                try:
                    await worker.terminate()
                except asyncio.CancelledError:
                    await self._terminate_cancel_safe(worker)
                    raise
            worker = _Worker(self._interpreter, nodes_path, self._cwd, self._on_log)
            self._workers[nodes_path] = worker
            try:
                await worker.start()
            except asyncio.CancelledError:
                await self._terminate_cancel_safe(worker)
                raise
            return worker

    async def _terminate_cancel_safe(self, worker: _Worker) -> None:
        """Finish teardown even if the caller receives another cancel."""
        task = asyncio.create_task(worker.terminate())
        self._kill_tasks.add(task)
        task.add_done_callback(self._kill_tasks.discard)
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        await task

    async def close(self) -> None:
        """Close every idle worker normally at FINALIZE (EN §5a)."""
        workers = list(self._workers.values())
        self._workers.clear()
        results: list[Any] = []
        if workers:
            results.extend(
                await asyncio.gather(
                    *(worker.shutdown() for worker in workers),
                    return_exceptions=True,
                )
            )
        teardown_tasks = tuple(self._kill_tasks)
        if teardown_tasks:
            results.extend(
                await asyncio.gather(*teardown_tasks, return_exceptions=True)
            )
        failures = [result for result in results if isinstance(result, BaseException)]
        if failures:
            details = "; ".join(
                f"{type(error).__name__}: {error}" for error in failures
            )
            raise WorkerCrash(f"python worker cleanup failed: {details}")


def default_interpreter() -> str:
    return sys.executable
