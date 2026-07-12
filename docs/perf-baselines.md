# napflow — v0.2 before-change performance baselines

These are the **v0.1 baselines** the v0.2 lifecycle refactor (D34–D36) is
measured against. They are recorded BEFORE the correctness refactors so
later milestones can show a deliberate trade-off, not a regression
(PLAN M0, NFR-08/NFR-18). They are baselines, **not correctness gates**:
they inform batch sizing (M2), bounded loop/history design (M3), the
inline-blob threshold (M4), and paged replay (M5).

Reproduce with the opt-in suite (excluded from ordinary CI):

```
uv run python -m pytest -m perf -s
```

## Machine / build

- Recorded 2026-07-12, package `0.1.0` (`feat/v0.2` HEAD, pre-refactor).
- macOS (Darwin 25.5, arm64), CPython 3.12.13, single-run measurements.
- Numbers are order-of-magnitude anchors on one developer machine, not a
  cross-machine contract — re-run locally before comparing.

## Baselines

| Measurement | v0.1 baseline | v0.2 owner / target |
|---|---|---|
| Guarded inline throughput (merge `any` + counter, no delay) | 50,000 laps in 1.19s ≈ **42k laps/s** (≈46k/s at 10k laps) | M2 cooperative batching must keep this fast while gaining deadline/abort responsiveness (NFR-12) |
| Worker round trip, 1KB / 10KB / 60KB result | ≈ **20 ms each**, incl. spawn+teardown (spawn dominates; payload size is negligible below the cliff) | M2 protocol limit — see cliff below |
| Worker round trip, ≥70KB result | **crashes** — asyncio's default 64KB `StreamReader` limit makes `readline()` raise `ValueError` (TR-14, `test_v02_audit`) | M2 raises/handles the reader limit; the ≥70KB / 10MB baseline unblocks then |
| Parallel loop, 100 items | 0.005s ≈ 19k items/s | — |
| Parallel loop, 10,000 items | 0.250s ≈ 40k items/s | — |
| Parallel loop, 100,000 items | 3.25s ≈ 31k items/s, **peak heap ≈ 489 MB** | M3 bounded producer/fixed task set — heap must become proportional to `max_concurrency`, not item count (NFR-14) |
| History replay read (`_read_records`) | 10.5MB log / 22,795 records: 0.088s, **peak ≈ 19 MB** (whole file into RAM) | M5 bounded/paged reads + lazy blobs (FR-1106); memory must not track total log size |

## Deferred to the manual dev window (browser-side)

- History **first render** and **scrub** at 10MB and 100MB run logs: a
  frontend measurement (xyflow canvas + `runview` reducer), captured in
  the manual testing window, not headless. The `_read_records` row above
  is its server-side half.
- Worker round trips at 100KB / 10MB: blocked on the M2 reader-limit fix;
  measure once the ≥70KB cliff above is gone.

## Notes

- Worker round trips include a fresh spawn every run (workers are killed
  at FINALIZE), so the ~20 ms is spawn+import+call+teardown, not steady
  per-call latency. When comparing M2, hold the spawn model constant or
  measure warm calls explicitly.
- The 489 MB / 100k-loop figure is the single most important before-number
  for M3: it is the concrete cost of `gather(one task per item)` plus one
  frame per iteration that the bounded rewrite targets.
