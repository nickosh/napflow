# napflow — v0.2 before-change performance baselines

These are the **v0.1 baselines** recorded for the v0.2 lifecycle/history work
(D34–D36) and retained for the future scale review under D39. They were
recorded BEFORE the correctness refactors so later work can show a deliberate
trade-off, not a regression (PLAN M0, NFR-08/future NFR-18). They are
baselines, **not correctness gates**:
they inform batch sizing (M2), bounded loop/history design (M3), the
inline-blob threshold (M4), and paged replay (M5).

Reproduce the Python/engine/server rows with the opt-in suite (excluded from
ordinary CI):

```
uv run python -m pytest -m perf -s
```

Reproduce the built-UI first-render/retained-heap rows with the separate
opt-in Playwright harness (also excluded from ordinary CI):

```
cd ui
npm run build
npm run perf:history
```

## Machine / build

- Recorded 2026-07-12, package `0.1.0` (`feat/v0.2` HEAD, pre-refactor).
- macOS (Darwin 25.5, arm64), CPython 3.12.13. Python rows are the final
  clean run; browser timing is the median of three clean runs (heap was stable).
- Numbers are order-of-magnitude anchors on one developer machine, not a
  cross-machine contract — re-run locally before comparing.

## Baselines

| Measurement | v0.1 baseline | v0.2 owner / target |
|---|---|---|
| Guarded inline throughput (merge `any` + counter, no delay) | 50,000 laps in 1.120s ≈ **44.6k laps/s** | M2 cooperative batching must keep this fast while gaining deadline/abort responsiveness (NFR-12) |
| Worker round trip, 1KB result | **21.5 ms**, including fresh spawn/import/call/teardown | M2 should retain a comparable working-size baseline |
| Worker round trip, 100KB / 10MB result | **No successful v0.1 round trip exists:** both cross the ≈70KB cliff where asyncio's default 64KB `StreamReader.readline()` raises `ValueError`. The bounded teardown-inclusive probe observed/reaped the failures after **22.2 ms / 4.037 s** respectively; the 10MB path exposes the current grace/kill cleanup cost. These are failure-lifecycle measurements, not round-trip timings. | M2 raises/handles the reader limit and separates immediate timeout termination from normal EOF; re-run the same sizes for successful post-fix timings (TR-14) |
| Parallel loop timing, 100 items | 0.009s ≈ 10.5k items/s | Uninstrumented timing; compare like-for-like |
| Parallel loop timing, 10,000 items | 0.288s ≈ 34.8k items/s | Uninstrumented timing; compare like-for-like |
| Parallel loop timing, 100,000 items | 3.410s ≈ 29.3k items/s | Uninstrumented timing; compare like-for-like |
| Parallel loop peak heap, 100,000 items | **485.1 MB** at `max_concurrency: 16`, measured in a separate `tracemalloc` run | M3 bounded producer/fixed task set — heap must become proportional to `max_concurrency`, not item count (NFR-14) |
| M3 parallel-loop active state, 100,000 items | v0.1 allocated one task + retained Frame per item | **Met:** 2.68s opt-in correctness run; exactly 16 helpers, ≤16 live Frames, 100,000 durable frame summaries (`test_parallel_loop_active_tasks_and_frames_100k`) |
| History replay read (`_read_records`), 10MB | 10.0MiB / 22,623 records: **0.021s** uninstrumented, **19.5 MB peak heap** | M5 replaced the public full-list path with frozen bounded pages + graph-sized projections; this remains a future scale comparison |
| History replay read (`_read_records`), 100MB | 100.0MiB / 225,739 records: **0.204s** uninstrumented, **194.4 MB peak heap** | Future 100k/large-replay gate under D39; not a v0.2 release target |
| Browser history first render + retained JS heap, 10MB | **127 ms median; +11.2 MB retained JS heap** | Historical before-state: M5 now opens one event/frame page and retains bounded projections; formal remeasurement remains future work |
| Browser history first render + retained JS heap, 100MB | **810 ms median; +99.8 MB retained JS heap** | Future large-replay performance candidate under D39 |

Timing and heap are deliberately separate passes for the loop and history
measurements. `tracemalloc` changes allocation-heavy timings enough that a
single instrumented elapsed value is not a reproducible throughput baseline.

## M0 measurement status

Both promised surfaces have opt-in harnesses. The Python suite records
server replay time/heap at 10MB and 100MB; `npm run perf:history` opens
otherwise-equivalent histories through the real server and production bundle,
records click-to-settled-render time, forces GC through CDP, and records the
retained JS heap delta. Both commands were run on the recorded machine/build
before any M5 replay/UI change. PLAN M0's promised sizes therefore all have an
honest before-state: successful timings where the path works, and an explicit
reader-limit failure at the requested 100KB/10MB worker sizes.

## Notes

- Worker probes include fresh spawn, import, call/failure, and teardown—not
  steady per-call latency. At 100KB/10MB the only honest pre-fix measurement
  is the reader failure lifecycle; the 10MB line's ~4s cleanup is itself M2
  evidence, while successful large-result timing begins after M2.
- The 485.1 MB / 100k-loop figure is the single most important before-number
  for M3: it is the concrete cost of `gather(one task per item)` plus one
  frame per iteration that the bounded rewrite targets. M3's correctness gate
  now measures scheduler-owned helpers/live Frames separately from semantic
  input/result/event content, which necessarily scales with the requested
  100k-item run.
