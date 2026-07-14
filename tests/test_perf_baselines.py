"""v0.2 before-change performance baselines (PLAN M0, NFR-08/NFR-18).

Opt-in and EXCLUDED from ordinary CI (the `perf` marker + the
`-m "not perf"` default in pyproject). Run explicitly:

    uv run python -m pytest -m perf -s

These are baselines, not correctness gates: they record the v0.1 numbers
the v0.2 lifecycle refactor is measured against (M2 batching, M3 bounded
loops/history, M4 inline-blob thresholds, M5 paged replay). Assertions
here are deliberately loose — they only guard against a gross regression
in the harness itself. The recorded numbers live in
`docs/perf-baselines.md`; re-run to refresh them on a given machine.

The browser first-render measurements at 10MB/100MB live in the separate
opt-in Playwright harness (`cd ui && npm run perf:history`). This Python
suite measures the server-side replay reader at both sizes; see
`docs/perf-baselines.md` for both reproduction commands.
"""

import asyncio
import gc
import json
import time
import tracemalloc
from contextlib import suppress

import pytest

from napflow.core.engine import FlowRun
from napflow.core.events import HISTORY_FORMAT, EventStream, SecretMasker
from napflow.core.worker import WorkerPool, default_interpreter
from napflow.server.app import _read_records
from test_engine import end, flow, manifest, run, start
from test_frames import loop, write_flow

pytestmark = pytest.mark.perf

BIG_BUDGET = manifest(message_budget=50_000_000)


def _report(name: str, detail: str) -> None:
    print(f"\n[perf] {name}: {detail}")


def test_guarded_inline_throughput():
    """Tight guarded cycle (merge `any` + counter, no delay) — the inline
    dispatch path M2's cooperative batching must keep fast."""

    def cycle(n):
        return flow(
            start(),
            {"id": "m", "type": "merge", "config": {"mode": "any"}},
            {"id": "g", "type": "counter", "config": {"count": n}},
            end({"name": "done"}),
            edges=[
                ("start.out", "m.in1"),
                ("m.out", "g.in"),
                ("g.continue", "m.in2"),
                ("g.exhausted", "end.done"),
            ],
        )

    n = 50_000
    gc.collect()
    t = time.perf_counter()
    result, _ = run(cycle(n), mani=BIG_BUDGET)
    elapsed = time.perf_counter() - t
    _report(
        "guarded inline throughput",
        f"{n:,} laps in {elapsed:.3f}s = {n / elapsed:,.0f} laps/s",
    )
    assert result.state == "passed"


async def _worker_roundtrip_probe(tmp_path, size):
    """Measure spawn/import/call-or-failure/teardown as one lifecycle.

    For the known pre-M2 >64KB reader bug, race the call against the worker's
    protocol-reader task. That observes the actual `ValueError` promptly
    instead of waiting for a 30-second node timeout, then reaps the child.
    """
    nodes_path = tmp_path / "nodes.py"
    nodes_path.write_text(
        f"def echo(seed):\n    return {{'data': 'x' * {size}}}\n",
        encoding="utf-8",
    )
    pool = WorkerPool(default_interpreter(), tmp_path, lambda *_: None)
    worker = None
    call_task = None
    reader_failure = None
    outcome = None
    started = time.perf_counter()
    try:
        # Deliberately includes spawn/import in the measured path, matching the
        # v0.1 CLI/engine lifecycle (workers close at run finalization).
        worker = await pool._get_or_spawn(nodes_path)
        call_task = asyncio.create_task(
            worker.call("echo", {"seed": None}, ("f-0", "python"))
        )
        assert worker._reader is not None
        done, _ = await asyncio.wait(
            {call_task, worker._reader},
            timeout=5,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if call_task in done:
            outputs = call_task.result()
            assert len(outputs["data"]) == size
            outcome = "passed"
        elif worker._reader in done:
            reader_failure = worker._reader.exception()
            assert isinstance(reader_failure, ValueError), reader_failure
            outcome = "reader_limit"
        else:
            raise AssertionError(
                f"worker probe made no progress within 5s at {size:,}B"
            )
    finally:
        if call_task is not None and not call_task.done():
            call_task.cancel()
            with suppress(asyncio.CancelledError):
                await call_task
        try:
            await pool.close()
        except ValueError as error:
            # `shutdown()` awaits the already-failed protocol reader after it
            # has reaped the worker. This is the same known failure observed
            # above; an unrelated close failure must remain visible.
            if reader_failure is None or error is not reader_failure:
                raise
        finally:
            if worker is not None:
                proc = worker._proc
                if proc is not None and proc.stdin is not None:
                    proc.stdin.close()
                    with suppress(
                        BrokenPipeError,
                        ConnectionResetError,
                        RuntimeError,
                    ):
                        await proc.stdin.wait_closed()
                await worker.kill()
                tasks = [
                    task
                    for task in (worker._reader, worker._stderr_reader)
                    if task is not None
                ]
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                # asyncio's Process has no public `close`; after the failed
                # readline path, explicitly close its fully-reaped transport
                # before `asyncio.run()` closes the loop. This keeps the
                # baseline probe from producing an unraisable destructor
                # warning while preserving the production bug being measured.
                transport = getattr(proc, "_transport", None)
                if transport is not None:
                    transport.close()
                await asyncio.sleep(0)
    assert outcome is not None
    return outcome, time.perf_counter() - started


@pytest.mark.parametrize(
    ("size", "expected_before_m2"),
    [
        pytest.param(1024, "passed", id="1KB"),
        pytest.param(100 * 1024, "reader_limit", id="100KB"),
        pytest.param(10 * 1024 * 1024, "reader_limit", id="10MB"),
    ],
)
def test_worker_roundtrip_by_size(tmp_path, size, expected_before_m2):
    """PLAN M0's exact 1KB/100KB/10MB sizes.

    A reader-limit failure is a valid *before-state*, not a successful timing.
    Once M2 removes the cliff the larger probes may pass and print their new
    timings; the static v0.1 failure baseline remains in the documentation.
    """
    gc.collect()
    outcome, elapsed = asyncio.run(_worker_roundtrip_probe(tmp_path, size))
    if outcome == "passed":
        detail = f"{size:,}B in {elapsed * 1000:.1f}ms (incl. spawn+import+teardown)"
    else:
        detail = (
            f"{size:,}B hit the 64KB protocol-reader limit after "
            f"{elapsed * 1000:.1f}ms "
            "(bounded failure+teardown probe; no round trip)"
        )
    _report("worker round trip", detail)
    if expected_before_m2 == "passed":
        assert outcome == "passed"
    else:
        # Keep the harness useful after M2: a fixed worker produces a real
        # timing; pre-M2 records the expected reader cliff without hanging.
        assert outcome in {expected_before_m2, "passed"}


def _parallel_loop_flow(tmp_path, n):
    (tmp_path / "napflow.yaml").write_text("schema: napflow/v1\n", encoding="utf-8")
    write_flow(
        tmp_path,
        "flows/body",
        [start({"name": "item", "type": "any"}), end({"name": "result"})],
        [("start.item", "end.result")],
    )
    return flow(
        start(),
        loop(
            "lp",
            f"range({n}) | list",
            "flows/body",
            mode="parallel",
            max_concurrency=16,
        ),
        end({"name": "res"}),
        edges=[("start.out", "lp.trigger"), ("lp.results", "end.res")],
    )


@pytest.mark.parametrize("n", [100, 10_000, 100_000])
def test_parallel_loop_timing(tmp_path, n):
    """Uninstrumented 100/10k/100k loop timings.

    `tracemalloc` materially slows allocation-heavy code, so peak heap is a
    separate 100k run below and must not contaminate these throughput numbers.
    """
    f = _parallel_loop_flow(tmp_path, n)
    gc.collect()
    t = time.perf_counter()
    result, _ = run(f, workspace_root=tmp_path, mani=BIG_BUDGET)
    elapsed = time.perf_counter() - t
    _report(
        "parallel loop timing",
        f"n={n:,} in {elapsed:.3f}s = {n / elapsed:,.0f} items/s",
    )
    assert result.state == "passed"


def test_parallel_loop_peak_memory_100k(tmp_path):
    """The M3 before-state: peak heap for 100k items at concurrency 16."""
    n = 100_000
    f = _parallel_loop_flow(tmp_path, n)
    gc.collect()
    tracemalloc.start()
    try:
        result, _ = run(f, workspace_root=tmp_path, mani=BIG_BUDGET)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    _report("parallel loop peak heap", f"n={n:,}, peak {peak / 1e6:.1f} MB")
    assert result.state == "passed"


def test_parallel_loop_active_tasks_and_frames_100k(tmp_path, monkeypatch):
    """M3/TR-19 scale gate: semantic outputs may grow; active runtime may not."""
    n = 100_000
    concurrency = 16
    _parallel_loop_flow(tmp_path, n)  # writes the referenced body flow
    # Results are deliberately unwired: the loop must still process all items,
    # but the root result does not retain the 100k output dictionaries.
    f = flow(
        start(),
        loop(
            "lp",
            f"range({n}) | list",
            "flows/body",
            mode="parallel",
            max_concurrency=concurrency,
        ),
        end(),
        edges=[("start.out", "lp.trigger")],
    )

    active = 0
    peak = 0
    worker_count = 0
    original_spawn = FlowRun._spawn_frame
    original_finish = FlowRun._finish_child
    original_create_task = asyncio.TaskGroup.create_task

    def tracked_spawn(self, *args, **kwargs):
        nonlocal active, peak
        child = original_spawn(self, *args, **kwargs)
        active += 1
        peak = max(peak, active)
        return child

    def tracked_finish(self, *args, **kwargs):
        nonlocal active
        try:
            return original_finish(self, *args, **kwargs)
        finally:
            active -= 1

    def tracked_create_task(group, coro, *, name=None, context=None):
        nonlocal worker_count
        if name and name.startswith("napf-loop-"):
            worker_count += 1
        return original_create_task(group, coro, name=name, context=context)

    class SummarySink:
        def __init__(self):
            self.count = 0
            self.index_sum = 0

        def write(self, record):
            if record["event"] == "frame_finished":
                self.count += 1
                self.index_sum += record["loop_index"]

        def close(self):
            pass

    monkeypatch.setattr(FlowRun, "_spawn_frame", tracked_spawn)
    monkeypatch.setattr(FlowRun, "_finish_child", tracked_finish)
    monkeypatch.setattr(asyncio.TaskGroup, "create_task", tracked_create_task)
    sink = SummarySink()
    engine = FlowRun(
        f,
        flow_identity="flows/perf",
        manifest=BIG_BUDGET,
        env={},
        env_name=None,
        inputs={},
        stream=EventStream("perf-loop", SecretMasker([], {}), [sink]),
        workspace_root=tmp_path,
    )

    result = asyncio.run(engine.execute())

    assert result.state == "passed"
    assert worker_count == concurrency
    assert peak <= concurrency
    assert active == 0
    assert sink.count == n
    assert sink.index_sum == n * (n - 1) // 2


def _write_history_log(log, target_mb):
    header = json.dumps(
        {
            "event": "run_started",
            "run_id": "perf",
            "seq": 1,
            "format": HISTORY_FORMAT,
            "features": [],
            "flow": "flows/perf",
            "env_name": None,
            "inputs": {},
            "engine_version": "0.1.0",
        }
    )
    target_bytes = target_mb * 1024 * 1024
    header_line = header + "\n"
    written = len(header_line.encode("utf-8"))
    count = 1
    with log.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(header_line)
        for seq in range(2, 10**9):
            line = (
                json.dumps(
                    {"event": "message_emitted", "seq": seq, "value_preview": "y" * 400}
                )
                + "\n"
            )
            line_bytes = len(line.encode("utf-8"))
            if written + line_bytes > target_bytes:
                break
            fh.write(line)
            written += line_bytes
            count += 1
    return count


@pytest.mark.parametrize("target_mb", [10, 100], ids=["10MB", "100MB"])
def test_history_replay_read_memory(tmp_path, target_mb):
    """Server-side replay reads the whole JSONL into RAM (`_read_records`).
    Time an uninstrumented read, release it, then measure peak heap on a
    separate read so `tracemalloc` does not distort the timing. Browser first
    render at these sizes is measured by the opt-in Playwright harness."""
    log = tmp_path / "r.jsonl"
    count = _write_history_log(log, target_mb)
    size_mb = log.stat().st_size / (1024 * 1024)
    gc.collect()
    t = time.perf_counter()
    records = _read_records(log)
    elapsed = time.perf_counter() - t
    assert len(records) == count
    del records
    gc.collect()

    tracemalloc.start()
    try:
        records = _read_records(log)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    _report(
        "replay read",
        f"{size_mb:.1f}MiB log, {len(records):,} records in "
        f"{elapsed:.3f}s uninstrumented, peak {peak / 1e6:.1f} MB",
    )
    assert len(records) == count
    del records
    gc.collect()
