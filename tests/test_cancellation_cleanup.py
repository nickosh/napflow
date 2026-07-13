"""M2 cancellation cleanup regressions (TR-15 / NFR-13 / D36).

External cancellation is intentionally different from ``FlowRun.abort()``:
the caller's ``CancelledError`` propagates, but only after the run has closed
every resource it owns.  These tests synchronize on real firing boundaries
instead of relying on sleeps, and treat the JSONL file as an append-only prefix
that must remain readable even though no terminal result is produced.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

import napflow.core.engine as engine_module
from napflow.core.engine import FlowRun
from napflow.core.events import EventStream, JsonlSink, SecretMasker
from napflow.core.httpclient import WireRequest
from napflow.core.models.manifest import Manifest
from napflow.core.runprep import prepare_run
from napflow.core.workspace import load_workspace
from napflow.server.runs import RunManager
from test_engine import end, flow, start
from test_frames import loop, sub, write_flow

NO_SECRETS = SecretMasker([], {})


class _MarkerSink:
    """Capture events and wake after ``count`` matching records."""

    def __init__(self, predicate, *, count: int = 1):
        self._predicate = predicate
        self._target_count = count
        self._matches = 0
        self.reached = asyncio.Event()
        self.records: list[dict[str, Any]] = []
        self.closed = False

    def write(self, record: dict[str, Any]) -> None:
        self.records.append(record)
        if self._predicate(record):
            self._matches += 1
            if self._matches >= self._target_count:
                self.reached.set()

    def close(self) -> None:
        self.closed = True


class _TrackingJsonlSink(JsonlSink):
    def __init__(self, path: Path):
        super().__init__(path)
        self.closed = False

    def close(self) -> None:
        self.closed = True
        super().close()


class _CancelOnRunFinishedSink(_MarkerSink):
    """Request external cancellation from inside final event emission."""

    def __init__(self) -> None:
        super().__init__(lambda record: record["event"] == "run_finished")
        self.cancel_requested = False

    def write(self, record: dict[str, Any]) -> None:
        super().write(record)
        if record["event"] == "run_finished":
            task = asyncio.current_task()
            assert task is not None
            self.cancel_requested = task.cancel()


class _BlockingHttpClient:
    """Deterministic fake transport with observable teardown ordering."""

    instances: list["_BlockingHttpClient"] = []

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.exited = asyncio.Event()
        self._never = asyncio.Event()
        self.active_requests = 0
        self.close_calls = 0
        self.close_saw_active_requests: int | None = None
        self.closed = False
        type(self).instances.append(self)

    async def request(self, **kwargs: Any) -> Any:
        self.active_requests += 1
        self.entered.set()
        on_prepared = kwargs.get("on_prepared")
        if on_prepared is not None:
            on_prepared(
                WireRequest(
                    method=kwargs["method"],
                    url=kwargs["url"],
                    headers=dict(kwargs.get("headers") or {}),
                    body=None,
                    size_bytes=0,
                )
            )
        try:
            await self._never.wait()
        finally:
            self.active_requests -= 1
            self.exited.set()

    async def close(self) -> None:
        self.close_calls += 1
        self.close_saw_active_requests = self.active_requests
        self.closed = True


class _BlockingCloseResource:
    """Resource whose close lets a test cancel during normal cleanup."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.close_calls = 0
        self.closed = False

    async def close(self) -> None:
        self.close_calls += 1
        self.entered.set()
        await self.release.wait()
        self.closed = True


def _manifest() -> Manifest:
    return Manifest.model_validate({"schema": "napflow/v1"})


def _assert_parseable_jsonl_prefix(path: Path) -> list[dict[str, Any]]:
    raw = path.read_text(encoding="utf-8")
    assert raw
    assert raw.endswith("\n")
    records = [json.loads(line) for line in raw.splitlines()]
    assert records[0]["event"] == "run_started"
    assert [record["seq"] for record in records] == list(range(1, len(records) + 1))
    return records


async def _cancel_at_marker(
    flow_file,
    tmp_path: Path,
    marker: _MarkerSink,
    *,
    flow_dir: Path | None = None,
    workspace_root: Path | None = None,
) -> tuple[FlowRun, list[asyncio.Task[Any]], list[dict[str, Any]]]:
    """Cancel a running flow and assert the cancellation boundary itself.

    The returned task snapshot includes firing tasks and any loop helper tasks
    that existed at the marker.  It deliberately does not inspect scheduler
    counters: cancellation may strand accounted deliveries in the queue while
    still having released every live resource correctly.
    """

    log_path = tmp_path / "cancelled.jsonl"
    jsonl = _TrackingJsonlSink(log_path)
    stream = EventStream("cancelled-run", NO_SECRETS, [jsonl, marker])
    run = FlowRun(
        flow_file,
        flow_identity="flows/cancelled",
        manifest=_manifest(),
        env={},
        env_name="dev",
        inputs={},
        stream=stream,
        flow_dir=flow_dir,
        workspace_root=workspace_root,
    )
    execute_task = asyncio.create_task(run.execute(), name="cancelled-flow-run")
    current = asyncio.current_task()
    tasks_at_marker: list[asyncio.Task[Any]] = []
    try:
        async with asyncio.timeout(5):
            await marker.reached.wait()

        # all_tasks() also catches parallel-loop helpers created implicitly by
        # gather(), not just the firing tasks registered on FlowRun._tasks.
        tasks_at_marker = [
            task for task in asyncio.all_tasks() if task not in {current, execute_task}
        ]
        assert tasks_at_marker, "marker was reached without a live owned task"

        execute_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            async with asyncio.timeout(5):
                await execute_task

        # If CancelledError escaped before cleanup, at least one of these task
        # references or sinks would still be live at this exact boundary.
        assert all(task.done() for task in tasks_at_marker)
        assert not [task for task in run._tasks if not task.done()]
        assert marker.closed
        assert jsonl.closed
        records = _assert_parseable_jsonl_prefix(log_path)
        return run, tasks_at_marker, records
    finally:
        # A failing implementation must not leave test-owned work behind.
        if not execute_task.done():
            execute_task.cancel()
            await asyncio.gather(execute_task, return_exceptions=True)
        leftovers = {
            task for task in (*tasks_at_marker, *run._tasks) if not task.done()
        }
        for task in leftovers:
            task.cancel()
        if leftovers:
            await asyncio.gather(*leftovers, return_exceptions=True)
        # This fallback runs only after the cleanup assertions above. It frees
        # the test's file handle without calling the engine's private runtime
        # cleanup, which could otherwise conceal a client/session leak.
        stream.close()


def test_external_cancellation_closes_delay_task_stream_and_jsonl(tmp_path):
    delayed = flow(
        start(),
        {"id": "sleep", "type": "delay", "config": {"seconds": 60}},
        end({"name": "out"}),
        edges=[("start.out", "sleep.in"), ("sleep.out", "end.out")],
    )
    marker = _MarkerSink(
        lambda record: record["event"] == "node_fired" and record.get("node") == "sleep"
    )

    _run, tasks, records = asyncio.run(_cancel_at_marker(delayed, tmp_path, marker))

    assert all(task.cancelled() or task.exception() is None for task in tasks)
    assert records[-1]["event"] == "node_fired"


def test_external_cancellation_waits_for_blocking_http_client_close(
    tmp_path, monkeypatch
):
    _BlockingHttpClient.instances = []
    monkeypatch.setattr(engine_module, "HttpClient", _BlockingHttpClient)
    requested = flow(
        start(),
        {
            "id": "req",
            "type": "request",
            "config": {"url": "https://example.invalid/never"},
        },
        end({"name": "out"}),
        edges=[("start.out", "req.trigger"), ("req.response", "end.out")],
    )
    marker = _MarkerSink(
        lambda record: (
            record["event"] == "request_started" and record.get("node") == "req"
        )
    )

    asyncio.run(_cancel_at_marker(requested, tmp_path, marker))

    assert len(_BlockingHttpClient.instances) == 1
    client = _BlockingHttpClient.instances[0]
    assert client.entered.is_set()
    assert client.exited.is_set()
    assert client.close_calls == 1
    assert client.close_saw_active_requests == 0
    assert client.closed


def test_external_cancellation_closes_flow_child_delay(tmp_path):
    write_flow(
        tmp_path,
        "flows/slow_child",
        [
            start({"name": "value", "type": "any"}),
            {"id": "child_sleep", "type": "delay", "config": {"seconds": 60}},
            end({"name": "out"}),
        ],
        [("start.value", "child_sleep.in"), ("child_sleep.out", "end.out")],
    )
    parent = flow(
        start(),
        sub("child", "flows/slow_child"),
        end({"name": "out"}),
        edges=[("start.out", "child.value"), ("child.out", "end.out")],
    )
    marker = _MarkerSink(
        lambda record: (
            record["event"] == "node_fired"
            and record.get("node") == "child_sleep"
            and record.get("frame") != "f-0"
        )
    )

    _run, tasks, _records = asyncio.run(
        _cancel_at_marker(parent, tmp_path, marker, workspace_root=tmp_path)
    )

    # Both the container firing and the child firing were live at the marker.
    assert len(tasks) >= 2
    assert all(task.done() for task in tasks)


def test_external_cancellation_closes_parallel_loop_children_and_fresh_sessions(
    tmp_path, monkeypatch
):
    _BlockingHttpClient.instances = []
    monkeypatch.setattr(engine_module, "HttpClient", _BlockingHttpClient)
    write_flow(
        tmp_path,
        "flows/slow_iteration",
        [
            start({"name": "item", "type": "number"}),
            {
                "id": "iteration_sleep",
                "type": "delay",
                "config": {"seconds": 60},
            },
            end({"name": "out"}),
        ],
        [("start.item", "iteration_sleep.in"), ("iteration_sleep.out", "end.out")],
    )
    parent = flow(
        start(),
        loop(
            "iterations",
            "[1, 2, 3]",
            "flows/slow_iteration",
            mode="parallel",
            max_concurrency=3,
            fresh_session=True,
        ),
        end({"name": "results"}),
        edges=[
            ("start.out", "iterations.trigger"),
            ("iterations.results", "end.results"),
        ],
    )
    marker = _MarkerSink(
        lambda record: (
            record["event"] == "node_fired" and record.get("node") == "iteration_sleep"
        ),
        count=3,
    )

    _run, tasks, _records = asyncio.run(
        _cancel_at_marker(parent, tmp_path, marker, workspace_root=tmp_path)
    )

    assert len(_BlockingHttpClient.instances) == 3
    assert all(client.closed for client in _BlockingHttpClient.instances)
    assert all(client.close_calls == 1 for client in _BlockingHttpClient.instances)
    assert all(
        client.close_saw_active_requests == 0
        for client in _BlockingHttpClient.instances
    )
    assert all(task.done() for task in tasks)


def test_external_cancellation_during_final_event_still_closes_stream(tmp_path):
    """A cancellation requested synchronously by final emission lands at
    the lifecycle's final await; it must still close every sink before the
    caller observes ``CancelledError``. A complete JSONL is also a valid
    prefix, so its terminal event remains readable.
    """
    completed = flow(
        start(),
        end({"name": "out"}),
        edges=[("start.out", "end.out")],
    )

    async def scenario():
        log_path = tmp_path / "cancelled-at-final.jsonl"
        jsonl = _TrackingJsonlSink(log_path)
        cancel_sink = _CancelOnRunFinishedSink()
        stream = EventStream("cancelled-final", NO_SECRETS, [jsonl, cancel_sink])
        run = FlowRun(
            completed,
            flow_identity="flows/final",
            manifest=_manifest(),
            env={},
            env_name=None,
            inputs={},
            stream=stream,
        )

        task = asyncio.create_task(run.execute())
        try:
            with pytest.raises(asyncio.CancelledError):
                await task
            assert cancel_sink.cancel_requested
            assert cancel_sink.closed
            assert jsonl.closed
            records = _assert_parseable_jsonl_prefix(log_path)
            assert records[-1]["event"] == "run_finished"
            assert records[-1]["state"] == "passed"
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            stream.close()

    asyncio.run(scenario())


def test_external_cancellation_during_normal_cleanup_does_not_cancel_cleanup_task(
    tmp_path,
):
    completed = flow(
        start(),
        end({"name": "out"}),
        edges=[("start.out", "end.out")],
    )

    async def scenario():
        log_path = tmp_path / "cancelled-during-cleanup.jsonl"
        jsonl = _TrackingJsonlSink(log_path)
        marker = _MarkerSink(lambda _record: False)
        stream = EventStream("cancelled-cleanup", NO_SECRETS, [jsonl, marker])
        run = FlowRun(
            completed,
            flow_identity="flows/cleanup",
            manifest=_manifest(),
            env={},
            env_name=None,
            inputs={},
            stream=stream,
        )
        resource = _BlockingCloseResource()
        run._http = resource
        task = asyncio.create_task(run.execute())
        try:
            async with asyncio.timeout(5):
                await resource.entered.wait()
            task.cancel()
            resource.release.set()
            with pytest.raises(asyncio.CancelledError):
                await task

            assert resource.closed
            assert resource.close_calls == 1
            assert marker.closed
            assert jsonl.closed
            records = _assert_parseable_jsonl_prefix(log_path)
            assert records[-1]["event"] != "run_finished"
        finally:
            resource.release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            stream.close()

    asyncio.run(scenario())


def test_server_shutdown_uses_abort_cleanup_and_drains_run(tmp_path):
    """The server lifecycle delegates to the same FlowRun cleanup path."""
    root = tmp_path / "workspace"
    flow_dir = root / "flows" / "slow"
    flow_dir.mkdir(parents=True)
    (root / "napflow.yaml").write_text('schema: "napflow/v1"\n', encoding="utf-8")
    (flow_dir / "flow.yaml").write_text(
        'schema: "napflow/v1"\n'
        'flow: {name: "slow"}\n'
        "nodes:\n"
        '  - {id: "start", type: "start"}\n'
        '  - {id: "sleep", type: "delay", config: {seconds: 60}}\n'
        '  - {id: "end", type: "end", config: {ports: [{name: "out"}]}}\n'
        "edges:\n"
        '  - {from: "start.out", to: "sleep.in"}\n'
        '  - {from: "sleep.out", to: "end.out"}\n',
        encoding="utf-8",
    )
    workspace = load_workspace(root)
    prepared = prepare_run(workspace, "flows/slow")

    async def scenario():
        manager = RunManager()
        active = manager.start(workspace, prepared, {})
        flow_run = active.flow_run
        assert flow_run is not None
        async with asyncio.timeout(5):
            while True:
                durable_prefix = active.log_path.read_text(encoding="utf-8")
                if (
                    '"event":"node_fired"' in durable_prefix
                    and '"node":"sleep"' in durable_prefix
                ):
                    break
                await asyncio.sleep(0)

        await manager.shutdown()

        assert active.task is not None and active.task.done()
        assert active.result is not None and active.result.state == "aborted"
        assert active.flow_run is None
        assert flow_run._stream._closed
        records = _assert_parseable_jsonl_prefix(active.log_path)
        assert records[-1]["event"] == "run_finished"
        assert records[-1]["state"] == "aborted"

    asyncio.run(scenario())
