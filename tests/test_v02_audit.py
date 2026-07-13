"""v0.2 audit probes (PLAN M0) — one test per confirmed critical/high
finding from the first-working-version review (TR-11–22, EC42–EC51).

Each unresolved backend-reproducible finding is an `xfail(strict=True)`
test that asserts the CORRECT, post-fix behavior. When its owning milestone
lands, the marker is removed and the test becomes an ordinary regression;
M1's workspace-boundary probe is the first converted case.

Findings owned by later milestones without a clean test at this layer remain
explicit `skip`s near the bottom. M1's frontend persistence cases moved to
real Playwright coverage plus pure coordinator tests, so they no longer sit
in this placeholder ledger.
"""

import asyncio
import hashlib
import json
import os
import textwrap

import pytest

from napflow.core.engine import FlowRun, execute_flow
from napflow.core.events import (
    HISTORY_FEATURE_CONTENT_BLOBS,
    EventStream,
    JsonlSink,
    SecretMasker,
    apply_retention,
    persist_record_content,
    resolve_record_content,
)
from napflow.core.history_content import RunContentStore
from napflow.core.models.manifest import Manifest
from napflow.core.runprep import RunPrepError, prepare_run
from napflow.core.workspace import load_workspace
from test_engine import CaptureSink, end, flow, run, start


def py(node_id, function, outputs=(), **extra):
    config = {"function": function, "outputs": list(outputs)}
    return {"id": node_id, "type": "python", "config": config} | extra


def write_nodes(tmp_path, source):
    (tmp_path / "nodes.py").write_text(textwrap.dedent(source), encoding="utf-8")
    return tmp_path


# --------------------------------------------------------------------------
# TR-11 — public core API (FR-1112, EC42; owner: M6)


def test_public_run_flow_import():
    """`from napflow.core import run_flow` is the pytest entry point."""
    from napflow.core import run_flow

    assert callable(run_flow)


# --------------------------------------------------------------------------
# TR-12 — workspace boundary is symlink-aware (D37, EC38; owner: M1)


def test_entry_flow_resolution_is_symlink_contained(tmp_path):
    """Run preparation is a stable entry-flow behavior surface. A clean
    identity that resolves through a symlink outside the workspace must be
    rejected there; replacing the old `_safe_identity` helper must not leave
    this probe xfailed merely because that private helper disappeared."""
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "napflow.yaml").write_text("schema: napflow/v1\n", encoding="utf-8")
    (outside / "flow.yaml").write_text(
        "schema: napflow/v1\n"
        "flow: {name: outside}\n"
        "nodes:\n"
        "  - {id: start, type: start}\n"
        "  - {id: end, type: end}\n",
        encoding="utf-8",
    )
    (root / "flows").mkdir()
    try:
        (root / "flows" / "evil").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/privilege level")

    workspace = load_workspace(root)
    try:
        prepare_run(workspace, "flows/evil")
    except RunPrepError as error:
        if error.reason != "workspace_boundary":
            # RuntimeError is outside the expected current AssertionError;
            # an unrelated load/check/env failure must never false-XPASS.
            raise RuntimeError(
                f"entry flow failed for non-boundary reason {error.reason!r}"
            ) from error
        return  # correct target behavior: the entry identity was rejected
    raise AssertionError("entry flow resolved through a symlink outside workspace")


# --------------------------------------------------------------------------
# TR-14 — worker protocol handles large results (D36; owner: M2)


def test_worker_large_result_completes(tmp_path):
    """A valid ~70KB Python result must complete (or fail as a documented
    node error) without hitting asyncio's default 64KB reader cliff."""
    write_nodes(tmp_path, "def big(seed):\n    return {'data': 'x' * 70000}\n")
    f = flow(
        start(),
        py("p", "big", ["data"], max_seconds=2),
        end({"name": "out"}),
        edges=[("start.out", "p.seed"), ("p.data", "end.out")],
    )
    result, _ = run(f, flow_dir=tmp_path)
    assert result.state == "passed"
    assert result.end_outputs["out"] == "x" * 70000


# --------------------------------------------------------------------------
# TR-15 — external cancellation leaves no worker behind (NFR-13; owner: M2)


def test_external_cancellation_kills_workers(tmp_path):
    """Cancelling the run coroutine mid-flight must tear down the worker.
    Capture the actual worker before cancelling: pool cleanup clears its
    mapping, which must not let a still-live process false-pass inspection."""
    entered = tmp_path / "worker-entered"
    write_nodes(
        tmp_path,
        f"""
        import time
        from pathlib import Path

        def slow(seed):
            Path({str(entered)!r}).write_text("entered", encoding="utf-8")
            time.sleep(30)
        """,
    )
    f = flow(
        start(),
        py("p", "slow", [], max_seconds=60),
        end({"name": "out"}),
        edges=[("start.out", "p.seed")],
    )

    async def scenario():
        stream = EventStream("cancel", SecretMasker([], {}), [])
        run_obj = FlowRun(
            f,
            flow_identity="flows/t",
            manifest=Manifest.model_validate({"schema": "napflow/v1"}),
            env={},
            env_name="dev",
            inputs={},
            stream=stream,
            flow_dir=tmp_path,
            workspace_root=tmp_path,
        )
        task = asyncio.ensure_future(run_obj.execute())
        workers = []
        live_at_return = []
        try:
            # Synchronize on the child entering user code instead of sleeping for
            # a guessed duration (which could false-XPASS on a slow runner).
            async with asyncio.timeout(5):
                while not entered.exists():
                    await asyncio.sleep(0.01)
            pool = run_obj._workers
            workers = list(pool._workers.values()) if pool else []
            assert len(workers) == 1
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            live_at_return = [
                worker
                for worker in workers
                if worker._proc is not None and worker._proc.returncode is None
            ]
        finally:
            # Cleanup is outermost: marker timeout, cancellation mismatch, or
            # inspection failure must still reap every test-owned process.
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            pool = run_obj._workers
            if pool is not None:
                for worker in pool._workers.values():
                    if worker not in workers:
                        workers.append(worker)
            cleanup = await asyncio.gather(
                *(worker.kill() for worker in workers), return_exceptions=True
            )
            if pool is not None:
                cleanup.extend(
                    await asyncio.gather(pool.close(), return_exceptions=True)
                )
            cleanup_errors = [
                item for item in cleanup if isinstance(item, BaseException)
            ]
            still_live = [
                worker
                for worker in workers
                if worker._proc is not None and worker._proc.returncode is None
            ]
            if cleanup_errors or still_live:
                # RuntimeError is outside this probe's expected AssertionError,
                # so broken cleanup is a real failure—not a concealed orphan.
                raise RuntimeError(
                    f"audit-probe cleanup failed: errors={cleanup_errors!r}, "
                    f"live_workers={len(still_live)}"
                )
        return live_at_return

    live = asyncio.run(scenario())
    assert live == [], f"leaked {len(live)} worker subprocess(es)"


# --------------------------------------------------------------------------
# TR-16 — large non-body payloads are bounded on disk (EC32; owner: M4)


def test_large_log_value_uses_typed_reference_without_changing_runtime(tmp_path):
    """D34 moves persisted large values to typed content references without
    changing the value delivered through the flow. This probe validates the
    collision-safe reference shape and feature-gated round trip."""
    big = "z" * 200_000
    f = flow(
        start({"name": "p", "type": "any"}),
        {"id": "lg", "type": "log", "config": {"label": "dump"}},
        end({"name": "out"}),
        edges=[("start.p", "lg.in"), ("lg.out", "end.out")],
    )
    log_path = tmp_path / "run.jsonl"
    store = RunContentStore(log_path)
    stream = EventStream(
        "test-run",
        SecretMasker([], {}),
        [JsonlSink(log_path)],
        content_store=store,
    )
    try:
        result = asyncio.run(
            execute_flow(
                f,
                flow_identity="flows/t",
                manifest=Manifest.model_validate({"schema": "napflow/v1"}),
                env={},
                env_name="dev",
                inputs={"p": big},
                stream=stream,
                flow_dir=tmp_path,
                workspace_root=tmp_path,
            )
        )
    finally:
        stream.close()
    records = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert result.end_outputs["out"] == big  # persisted form never changes runtime data
    logrec = next(r for r in records if r["event"] == "log")
    ref = logrec["value"]
    raw = big.encode("utf-8")
    assert isinstance(ref, dict), "large Log value is still persisted inline"
    assert set(ref) == {"$napflow"}
    descriptor = ref["$napflow"]
    assert descriptor.get("kind") == "blob"
    assert descriptor.get("hash") == f"sha256:{hashlib.sha256(raw).hexdigest()}"
    assert descriptor.get("bytes") == len(raw)
    assert descriptor.get("codec") == "utf-8"
    assert isinstance(descriptor.get("media_type"), str) and descriptor["media_type"]
    stored = [
        path
        for path in tmp_path.rglob("*")
        if path.is_file() and path != log_path and path.read_bytes() == raw
    ]
    assert len(stored) == 1, "typed reference has no unique byte-identical blob"
    assert hashlib.sha256(stored[0].read_bytes()).hexdigest() == descriptor[
        "hash"
    ].removeprefix("sha256:")
    assert HISTORY_FEATURE_CONTENT_BLOBS in records[0]["features"]
    assert resolve_record_content(logrec, records[0]["features"], store)["value"] == big


# --------------------------------------------------------------------------
# TR-17 — redaction never rewrites protocol vocabulary (D35, EC45; owner: M4)


@pytest.mark.parametrize(
    ("secret_value", "record"),
    [
        (
            "passed",
            {
                "event": "run_finished",
                "state": "passed",
                "asserts": {"passed": 1, "failed": 0},
            },
        ),
        (
            "error",
            {
                "event": "run_finished",
                "state": "error",
                "error_reason": "run_timeout",
                "unhandled_errors": [],
            },
        ),
    ],
)
def test_secret_value_does_not_corrupt_state_vocabulary(secret_value, record):
    """A declared secret whose VALUE happens to equal a protocol token
    ('passed', 'error') must not rewrite event state, schema keys, or enum
    values in a redacted presentation view."""
    masker = SecretMasker(["TOKEN"], {"TOKEN": secret_value})
    masked = masker.redact_record(record)
    assert masked == record  # protocol enums, names, and keys must all survive


# --------------------------------------------------------------------------
# TR-18 — a >64KB final event is still recognized (EC47/M3)


def test_large_final_event_is_recognized(tmp_path):
    """A final line larger than one reverse-read block remains visible."""
    from napflow.core.events import last_jsonl_record

    log = tmp_path / "r.jsonl"
    with log.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"event": "run_started", "seq": 1}) + "\n")
        f.write(
            json.dumps(
                {"event": "run_finished", "state": "passed", "big": "z" * 80_000}
            )
            + "\n"
        )
    record = last_jsonl_record(log)
    assert record is not None and record["event"] == "run_finished"


# --------------------------------------------------------------------------
# EC47 — retention is truly chronological within a second (owner: M3)


def test_retention_keeps_newest_within_same_second(tmp_path):
    """Two runs in the same wall-clock second get run ids differing only in
    a random hex suffix, so filename sort ≠ creation order. Retention must
    keep the newer run; today it can delete it."""
    d = tmp_path / "runs"
    d.mkdir()
    older = d / "20260712-100000-ffffff.jsonl"  # created first, hex sorts last
    newer = d / "20260712-100000-000000.jsonl"  # created second, hex sorts first
    older.write_text(json.dumps({"event": "run_finished", "run_id": older.stem}) + "\n")
    newer.write_text(json.dumps({"event": "run_finished", "run_id": newer.stem}) + "\n")
    # make creation order unambiguous to the filesystem
    os.utime(older, (1_000_000, 1_000_000))
    os.utime(newer, (2_000_000, 2_000_000))

    apply_retention(d, history=1)
    survivors = {p.name for p in d.glob("*.jsonl")}
    assert survivors == {newer.name}, f"retention kept the wrong run: {survivors}"


# --------------------------------------------------------------------------
# Routed to a later milestone (M0 DoD: explicit owner in lieu of a test)


def test_entry_flow_boundary_matrix(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "napflow.yaml").write_text("schema: napflow/v1\n", encoding="utf-8")
    workspace = load_workspace(root)

    for identity in (
        "",
        "/absolute",
        "../parent",
        "flows/./dot",
        "flows//empty",
        "C:/outside",
        r"C:\outside",
    ):
        with pytest.raises(RunPrepError) as excinfo:
            prepare_run(workspace, identity)
        assert excinfo.value.reason == "workspace_boundary"


@pytest.mark.parametrize(
    "node",
    [
        "  - {id: bad, type: flow, config: {flow: ../outside}}\n",
        "  - {id: bad, type: loop, config: {over: '[]', body: C:/outside}}\n",
        "  - {id: bad, type: fixture, config: {file: ../outside.json}}\n",
    ],
)
def test_reference_boundary_uses_stable_preparation_reason(tmp_path, node):
    root = tmp_path / "workspace"
    flow_dir = root / "flows" / "entry"
    flow_dir.mkdir(parents=True)
    (root / "napflow.yaml").write_text("schema: napflow/v1\n", encoding="utf-8")
    (flow_dir / "flow.yaml").write_text(
        "schema: napflow/v1\n"
        "flow: {name: boundary}\n"
        "nodes:\n"
        "  - {id: start, type: start}\n"
        f"{node}"
        "  - {id: end, type: end}\n",
        encoding="utf-8",
    )

    with pytest.raises(RunPrepError) as excinfo:
        prepare_run(load_workspace(root), "flows/entry")
    assert excinfo.value.reason == "workspace_boundary"
    assert any(d.reason == "workspace_boundary" for d in excinfo.value.diagnostics)


# TR-12 ref/loop/fixture matrices now execute in test_checker.py and
# test_nodes.py; the entry symlink regression remains above.


# TR-12 history identity/run-id/final-symlink coverage executes in
# test_workspace.py and test_server.py.


def _guarded_inline_cycle(count=20_000):
    return flow(
        start(),
        {"id": "m", "type": "merge", "config": {"mode": "any"}},
        {"id": "g", "type": "counter", "config": {"count": count}},
        end({"name": "done"}),
        edges=[
            ("start.out", "m.seed"),
            ("m.out", "g.in"),
            ("g.continue", "m.loop"),
            ("g.exhausted", "end.done"),
        ],
    )


def test_inline_cycle_respects_deadline():
    """A millisecond deadline interrupts a perpetually ready inline path.

    Twenty thousand laps take long enough that the pre-M2 pump completed
    `passed`; cooperative batching must stop after at most one ready batch
    plus event-loop scheduling delay.  The broad 500ms wall tolerance keeps
    this correctness probe stable on loaded cross-platform CI.
    """
    result, _ = run(_guarded_inline_cycle(), timeout=0.005)
    assert result.state == "error"
    assert result.error_reason == "run_timeout"
    assert result.duration_ms < 500


def test_inline_cycle_observes_abort():
    """A sibling event-loop task progresses and aborts the hot cycle."""
    sink = CaptureSink()

    async def scenario():
        stream = EventStream("inline-abort", SecretMasker([], {}), [sink])
        flow_run = FlowRun(
            _guarded_inline_cycle(),
            flow_identity="flows/t",
            manifest=Manifest.model_validate({"schema": "napflow/v1"}),
            env={},
            env_name=None,
            inputs={},
            stream=stream,
        )
        run_task = asyncio.create_task(flow_run.execute())

        async def abort_from_sibling():
            while not any(
                record["event"] == "node_fired" and record.get("node") == "g"
                for record in sink.records
            ):
                await asyncio.sleep(0)
            progressed_before_finish = not any(
                record["event"] == "run_finished" for record in sink.records
            )
            flow_run.abort()
            return progressed_before_finish

        sibling = asyncio.create_task(abort_from_sibling())
        result = await asyncio.wait_for(run_task, timeout=5)
        return result, await sibling

    result, progressed_before_finish = asyncio.run(scenario())
    assert progressed_before_finish
    assert result.state == "aborted"


def test_timed_out_worker_no_late_side_effect_or_replacement_overlap(tmp_path):
    entered = tmp_path / "old-entered"
    replacement = tmp_path / "replacement-started"
    late = tmp_path / "late-side-effect"
    overlap = tmp_path / "replacement-overlapped-old"
    write_nodes(
        tmp_path,
        f"""
        import time
        from pathlib import Path

        def warm(seed):
            return {{"out": seed}}

        def slow(seed):
            Path({str(entered)!r}).write_text("entered", encoding="utf-8")
            time.sleep(0.6)
            if Path({str(replacement)!r}).exists():
                Path({str(overlap)!r}).write_text("overlap", encoding="utf-8")
            Path({str(late)!r}).write_text("late", encoding="utf-8")
            time.sleep(30)
            return {{"out": seed}}

        def recover(error):
            Path({str(replacement)!r}).write_text("replacement", encoding="utf-8")
            return {{"kind": error["error_kind"]}}
        """,
    )
    f = flow(
        start(),
        py("warm", "warm", ["out"]),
        py("slow", "slow", ["out"], max_seconds=0.2),
        py("recover", "recover", ["kind"]),
        end({"name": "kind"}),
        edges=[
            ("start.out", "warm.seed"),
            ("warm.out", "slow.seed"),
            ("slow.error", "recover.error"),
            ("recover.kind", "end.kind"),
        ],
    )

    result, _ = run(f, flow_dir=tmp_path)

    assert result.state == "passed"
    assert result.end_outputs == {"kind": "timeout"}
    assert entered.exists(), "the timed firing must enter user code"
    assert replacement.exists(), "the timeout path must lazily respawn"
    assert not late.exists(), "the terminated worker committed a late side effect"
    assert not overlap.exists(), "replacement started while the old worker was live"


def test_large_log_blob_round_trip(tmp_path):
    value = "full value" * 20_000
    store = RunContentStore(tmp_path / "run.jsonl", inline_threshold_bytes=1024)
    record = persist_record_content({"event": "log", "value": value}, store)

    assert record["value"]["$napflow"]["kind"] == "blob"
    assert resolve_record_content(record, [HISTORY_FEATURE_CONTENT_BLOBS], store) == {
        "event": "log",
        "value": value,
    }


def test_blob_marker_shaped_user_payload_round_trips_as_literal(tmp_path):
    value = {
        "$napflow": {
            "kind": "blob",
            "hash": f"sha256:{'0' * 64}",
            "bytes": 0,
            "media_type": "text/plain",
            "codec": "utf-8",
        }
    }
    store = RunContentStore(tmp_path / "run.jsonl", inline_threshold_bytes=10_000)
    record = persist_record_content({"event": "log", "value": value}, store)

    assert record["value"] == {"$napflow": {"kind": "literal", "value": value}}
    assert (
        resolve_record_content(record, [HISTORY_FEATURE_CONTENT_BLOBS], store)["value"]
        == value
    )


# TR-11/FR-1113's clean release-sdist -> wheel -> installed `napf ui`
# subprocess check lives in tools/smoke_release_artifact.py. It is explicit
# release-gate work rather than an ordinary pytest because it creates an
# isolated environment and installs dependencies.


# TR-20 immediate navigation, editor close, unload prompt, and overlapping
# saves execute in ui/e2e/editing.spec.ts; pure queue/ETag cases execute in
# ui/src/persistence.test.ts.
