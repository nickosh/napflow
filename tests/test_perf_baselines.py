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

Two items in the M0 list are browser-side (history first-render at
10MB/100MB) and are measured in the manual dev window, not here; the
server-side replay-read baseline below is their headless half.
"""

import gc
import json
import time
import tracemalloc

import pytest

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


def test_worker_roundtrip_by_size(tmp_path):
    """Round trip through a python worker at working payload sizes. NOTE:
    ≥70KB currently crashes the reader (TR-14, test_v02_audit) — that
    baseline unblocks in M2, so it is intentionally omitted here."""

    def echo_flow(size):
        (tmp_path / "nodes.py").write_text(
            f"def echo(seed):\n    return {{'data': 'x' * {size}}}\n"
        )
        return flow(
            start(),
            {
                "id": "p",
                "type": "python",
                "config": {"function": "echo", "outputs": ["data"]},
                "max_seconds": 30,
            },
            end({"name": "out"}),
            edges=[("start.out", "p.seed"), ("p.data", "end.out")],
        )

    for size in (1024, 10 * 1024, 60 * 1024):
        f = echo_flow(size)
        run(f, flow_dir=tmp_path)  # warm spawn
        gc.collect()
        t = time.perf_counter()
        result, _ = run(f, flow_dir=tmp_path)
        ms = (time.perf_counter() - t) * 1000
        _report("worker round trip", f"{size:,}B in {ms:.1f}ms (incl. spawn)")
        assert result.state == "passed"


@pytest.mark.parametrize("n", [100, 10_000, 100_000])
def test_parallel_loop_scaling(tmp_path, n):
    """Parallel loop over N trivial iterations. M3 replaces the current
    one-task-per-item `gather` with a bounded producer; the peak-heap
    number here is that milestone's before-baseline."""
    (tmp_path / "napflow.yaml").write_text("schema: napflow/v1\n")
    write_flow(
        tmp_path,
        "flows/body",
        [start({"name": "item", "type": "any"}), end({"name": "result"})],
        [("start.item", "end.result")],
    )
    f = flow(
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
    gc.collect()
    tracemalloc.start()
    t = time.perf_counter()
    result, _ = run(f, workspace_root=tmp_path, mani=BIG_BUDGET)
    elapsed = time.perf_counter() - t
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    _report(
        "parallel loop",
        f"n={n:,} in {elapsed:.3f}s = {n / elapsed:,.0f} "
        f"items/s, peak {peak / 1e6:.1f} MB",
    )
    assert result.state == "passed"


def test_history_replay_read_memory(tmp_path):
    """Server-side replay reads the whole JSONL into RAM (`_read_records`).
    M5 replaces this with bounded/paged reads; this is the before-baseline
    (the 10MB/100MB browser first-render halves are measured manually)."""
    log = tmp_path / "r.jsonl"
    line = json.dumps(
        {"event": "message_emitted", "seq": 1, "value_preview": "y" * 400}
    )
    count = (10 * 1024 * 1024) // (len(line) + 1)
    with log.open("w", encoding="utf-8") as fh:
        for _ in range(count):
            fh.write(line + "\n")
    size_mb = log.stat().st_size / 1e6
    gc.collect()
    tracemalloc.start()
    t = time.perf_counter()
    records = _read_records(log)
    elapsed = time.perf_counter() - t
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    _report(
        "replay read",
        f"{size_mb:.1f}MB log, {len(records):,} records in "
        f"{elapsed:.3f}s, peak {peak / 1e6:.1f} MB",
    )
    assert len(records) == count
