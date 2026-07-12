"""v0.2 audit probes (PLAN M0) — one test per confirmed critical/high
finding from the first-working-version review (TR-11–22, EC42–EC51).

Each backend-reproducible finding is an `xfail(strict=True)` test that
asserts the CORRECT, post-fix behavior. It therefore FAILS today (the
defect is present) and will XPASS — loudly, because strict — the moment
its owning milestone lands the fix. That XPASS is the signal to remove
the marker and tick the requirement (no checkbox closes from inspection).

Findings with no clean headless reproduction (frontend save races, the
clean-tree wheel build, deadline-under-inline-cycle which depends on the
M2 cooperative-batch redesign, and the racy late-side-effect window) are
routed to their owning milestone with an explicit `skip` at the bottom —
the M0 DoD permits a named later owner in lieu of a failing test, and
these skips keep that ledger visible in the suite itself.
"""

import asyncio
import json
import os
import tempfile
import textwrap
from pathlib import Path

import pytest

from napflow.core.engine import FlowRun
from napflow.core.events import EventStream, SecretMasker, apply_retention
from napflow.core.models.manifest import Manifest
from test_engine import end, flow, manifest, run, start


def py(node_id, function, outputs=(), **extra):
    config = {"function": function, "outputs": list(outputs)}
    return {"id": node_id, "type": "python", "config": config} | extra


def write_nodes(tmp_path, source):
    (tmp_path / "nodes.py").write_text(textwrap.dedent(source), encoding="utf-8")
    return tmp_path


# --------------------------------------------------------------------------
# TR-11 — public core API (FR-1112, EC42; owner: M6)


@pytest.mark.xfail(strict=True, reason="FR-1112/M6: no public run_flow wrapper yet")
def test_public_run_flow_import():
    """`from napflow.core import run_flow` is the documented pytest entry
    point (D32 note); today core exposes no such wrapper."""
    from napflow.core import run_flow  # noqa: F401


# --------------------------------------------------------------------------
# TR-12 — workspace boundary is symlink-aware (D37, EC38; owner: M1)


@pytest.mark.xfail(strict=True, reason="EC38/M1: lexical guard ignores symlinks")
def test_identity_resolution_is_symlink_contained():
    """A lexically-clean identity that resolves (through a symlink) outside
    the workspace must be rejected or contained. `_safe_identity` only
    screens `..`/`:`, so `flows/evil/secret` escapes on resolve."""
    from napflow.server.app import _safe_identity

    root = Path(tempfile.mkdtemp())
    outside = Path(tempfile.mkdtemp())
    (outside / "secret.txt").write_text("SECRET")
    (root / "flows").mkdir()
    try:
        (root / "flows" / "evil").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/privilege level")

    identity = _safe_identity("flows/evil/secret.txt")
    # the boundary must NOT hand back an identity that escapes root
    if identity is not None:
        resolved = (root / Path(identity)).resolve()
        assert resolved.is_relative_to(root.resolve()), f"escaped to {resolved}"


# --------------------------------------------------------------------------
# TR-14 — worker protocol handles large results (D36; owner: M2)


@pytest.mark.xfail(strict=True, reason="M2: asyncio 64KB reader limit crashes reads")
def test_worker_large_result_completes(tmp_path):
    """A valid ~70KB Python result must complete (or fail as a documented
    node error) — today the parent's default 64KB StreamReader limit makes
    `readline()` raise `ValueError` and the read task dies."""
    write_nodes(tmp_path, "def big(seed):\n    return {'data': 'x' * 70000}\n")
    f = flow(
        start(),
        py("p", "big", ["data"], max_seconds=10),
        end({"name": "out"}),
        edges=[("start.out", "p.seed"), ("p.data", "end.out")],
    )
    result, _ = run(f, flow_dir=tmp_path)
    assert result.state == "passed"
    assert result.end_outputs["out"] == "x" * 70000


# --------------------------------------------------------------------------
# TR-15 — external cancellation leaves no worker behind (NFR-13; owner: M2)


@pytest.mark.xfail(strict=True, reason="NFR-13/M2: cleanup not in finally")
def test_external_cancellation_kills_workers(tmp_path):
    """Cancelling the run coroutine mid-flight must tear down the worker.
    Today `_workers.close()` runs after the pump but not in a `finally`,
    so an external `CancelledError` leaks the subprocess."""
    write_nodes(tmp_path, "import time\ndef slow(seed):\n    time.sleep(30)\n")
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
        await asyncio.sleep(1.5)  # let the worker spawn and enter sleep()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.5)
        pool = run_obj._workers
        live = [
            w
            for w in (pool._workers.values() if pool else [])
            if w._proc is not None and w._proc.returncode is None
        ]
        return live

    live = asyncio.run(scenario())
    assert live == [], f"leaked {len(live)} worker subprocess(es)"


# --------------------------------------------------------------------------
# TR-16 — large non-body payloads are bounded on disk (EC32; owner: M4)


@pytest.mark.xfail(
    strict=True, reason="EC32/M4: capture valve only covers request bodies"
)
def test_log_value_respects_capture_budget():
    """The run capture budget is meant to bound persisted bytes, but the
    valve only wraps request bodies — a large value through Log (or End) is
    written in full. Under D34 it becomes a store-once blob reference."""
    big = "z" * 200_000
    f = flow(
        start({"name": "p", "type": "any"}),
        {"id": "lg", "type": "log", "config": {"label": "dump"}},
        end({"name": "out"}),
        edges=[("start.p", "lg.in"), ("lg.out", "end.out")],
    )
    _, records = run(
        f, inputs={"p": big}, mani=manifest(body_capture_mb=0.001, run_capture_mb=0.001)
    )
    logrec = next(r for r in records if r["event"] == "log")
    persisted = json.dumps(logrec["value"])
    assert len(persisted) < len(big), "log value persisted in full — capture bypassed"


# --------------------------------------------------------------------------
# TR-17 — redaction never rewrites protocol vocabulary (D35, EC45; owner: M4)


@pytest.mark.xfail(
    strict=True, reason="EC45/M4: substring masking hits schema/state text"
)
def test_secret_value_does_not_corrupt_state_vocabulary():
    """A declared secret whose VALUE happens to equal a protocol token
    ('passed', 'error') must not rewrite event state, schema keys, or enum
    values. Today the substring masker replaces them wherever they appear."""
    masker = SecretMasker(["TOKEN"], {"TOKEN": "passed"})
    record = {
        "event": "run_finished",
        "state": "passed",
        "asserts": {"passed": 1, "failed": 0},
    }
    masked = masker.mask(record)
    assert masked["state"] == "passed"  # run state must survive
    assert "passed" in masked["asserts"]  # schema key must survive


# --------------------------------------------------------------------------
# TR-18 — a >64KB final event is still recognized (EC47/M5; owner: M3/M5)


@pytest.mark.xfail(
    strict=True, reason="M5: fixed 64KB tail window drops a large final line"
)
def test_large_final_event_is_recognized():
    """A `run_finished` line larger than the 64KB tail window makes the
    server report a completed run as 'incomplete'. Needs a durable summary
    or a robust backward record reader, not a fixed-size tail read."""
    from napflow.server.app import _tail_record

    d = Path(tempfile.mkdtemp())
    log = d / "r.jsonl"
    with log.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"event": "run_started", "seq": 1}) + "\n")
        f.write(
            json.dumps(
                {"event": "run_finished", "state": "passed", "big": "z" * 80_000}
            )
            + "\n"
        )
    record = _tail_record(log)
    assert record is not None and record["event"] == "run_finished"


# --------------------------------------------------------------------------
# EC47 — retention is truly chronological within a second (owner: M3)


@pytest.mark.xfail(strict=True, reason="EC47/M3: same-second runs sort by random hex")
def test_retention_keeps_newest_within_same_second():
    """Two runs in the same wall-clock second get run ids differing only in
    a random hex suffix, so filename sort ≠ creation order. Retention must
    keep the newer run; today it can delete it."""
    d = Path(tempfile.mkdtemp())
    older = d / "20260712-100000-ffffff.jsonl"  # created first, hex sorts last
    newer = d / "20260712-100000-000000.jsonl"  # created second, hex sorts first
    older.write_text("{}")
    newer.write_text("{}")
    # make creation order unambiguous to the filesystem
    os.utime(older, (1_000_000, 1_000_000))
    os.utime(newer, (2_000_000, 2_000_000))

    apply_retention(d, history=1)
    survivors = {p.name for p in d.glob("*.jsonl")}
    assert survivors == {newer.name}, f"retention kept the wrong run: {survivors}"


# --------------------------------------------------------------------------
# Routed to a later milestone (M0 DoD: explicit owner in lieu of a test)


@pytest.mark.skip(
    reason="TR-13/NFR-12 owner M2: deadline under inline cycle needs the "
    "cooperative-batch scheduler redesign; contract pinned there"
)
def test_inline_cycle_respects_deadline(): ...


@pytest.mark.skip(
    reason="TR-14 owner M2: timed-out worker late-side-effect window is "
    "racy to reproduce; covered by the M2 terminate→grace→kill rewrite"
)
def test_timed_out_worker_no_late_side_effect(): ...


@pytest.mark.skip(
    reason="TR-11/FR-1113 owner M6: clean-tree wheel/UI-bundle build is a "
    "subprocess build check; see napf git-install gotcha"
)
def test_clean_tree_wheel_contains_ui(): ...


@pytest.mark.skip(
    reason="TR-20/FR-1110 owner M1: navigate/close-during-autosave is a "
    "frontend (Playwright) save-coordinator test"
)
def test_navigation_during_autosave_flushes(): ...


@pytest.mark.skip(
    reason="TR-20/FR-1110 owner M1: overlapping saves is a frontend "
    "(Playwright) serialized-save-coordinator test"
)
def test_overlapping_saves_serialize(): ...
