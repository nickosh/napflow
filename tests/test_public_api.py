"""Public Workspace/Flow embedding API (D38 / FR-1112 / EC42)."""

import asyncio
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import get_type_hints

import pytest

from napflow.core import Flow, load_workspace, run_flow, run_flow_async


def _workspace(root: Path, *, default_env: str | None = None) -> Path:
    root.mkdir(parents=True)
    default = (
        f'environments: {{default: "{default_env}"}}\n'
        if default_env is not None
        else ""
    )
    (root / "napflow.yaml").write_text(
        f'schema: "napflow/v1"\n{default}', encoding="utf-8"
    )
    return root


def _echo_flow(root: Path, identity: str, *, default: str | None = None) -> None:
    directory = root.joinpath(*identity.split("/"))
    directory.mkdir(parents=True, exist_ok=True)
    default_yaml = f', default: "{default}"' if default is not None else ""
    (directory / "flow.yaml").write_text(
        'schema: "napflow/v1"\n'
        f'flow: {{name: "{identity}"}}\n'
        "nodes:\n"
        "  - id: start\n"
        "    type: start\n"
        "    config:\n"
        f"      ports: [{{name: value, type: string{default_yaml}}}]\n"
        "  - id: end\n"
        "    type: end\n"
        "    config:\n"
        "      ports: [{name: result}]\n"
        "edges:\n"
        '  - {from: "start.value", to: "end.result"}\n',
        encoding="utf-8",
    )


def _delay_flow(root: Path, identity: str) -> None:
    directory = root.joinpath(*identity.split("/"))
    directory.mkdir(parents=True)
    (directory / "flow.yaml").write_text(
        'schema: "napflow/v1"\n'
        'flow: {name: "slow"}\n'
        "nodes:\n"
        "  - {id: start, type: start}\n"
        "  - {id: wait, type: delay, config: {seconds: 60}}\n"
        "  - {id: end, type: end, config: {ports: [{name: result}]}}\n"
        "edges:\n"
        '  - {from: "start.out", to: "wait.in"}\n'
        '  - {from: "wait.out", to: "end.result"}\n',
        encoding="utf-8",
    )


def test_core_exports_functional_and_workspace_bound_surfaces(tmp_path: Path) -> None:
    root = _workspace(tmp_path / "ws")
    _echo_flow(root, "flows/echo")
    _echo_flow(root, "flows/other")
    workspace = load_workspace(root)
    flow = workspace.flow("flows/echo")

    assert isinstance(flow, Flow)
    assert flow.identity == "flows/echo"
    assert flow.directory == root / "flows/echo"
    assert flow.file == root / "flows/echo/flow.yaml"
    assert run_flow(
        workspace, "flows/echo", inputs={"value": "functional"}, history=False
    ).end_outputs == {"result": "functional"}
    assert flow.run(inputs={"value": "bound"}, history=False).end_outputs == {
        "result": "bound"
    }
    assert workspace.flow("flows/other").run(
        inputs={"value": "other"}, history=False
    ).end_outputs == {"result": "other"}
    with pytest.raises(FrozenInstanceError):
        flow.identity = "flows/other"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        workspace.root = tmp_path  # type: ignore[misc]


def test_discovery_and_catalog_are_fresh_and_exact(tmp_path: Path) -> None:
    root = _workspace(tmp_path / "ws")
    for identity in (
        "flows/exact",
        "flows/nested/leaf",
        "flows/non-identifier",
        "flows/flows/non-identifier",
        "flows/a-b",
        "flows/a_b",
        "flows/workspace",
        "flows/class",
        "flows/parent",
        "flows/parent/child",
        "flows/parent/run",
    ):
        _echo_flow(root, identity)
    workspace = load_workspace(root)
    catalog = workspace.flows

    assert catalog.exact == workspace.flow("flows/exact")
    assert catalog.nested.leaf == workspace.flow("flows/nested/leaf")
    assert catalog["non-identifier"] == workspace.flow("flows/non-identifier")
    assert catalog["nested/leaf"] == workspace.flow("flows/nested/leaf")
    # Catalog bracket keys are consistently root-relative. A legal first
    # segment equal to flows.root must never alias the shallower identity.
    assert catalog["flows/non-identifier"] == workspace.flow(
        "flows/flows/non-identifier"
    )
    assert catalog["a-b"].identity == "flows/a-b"
    assert catalog.a_b.identity == "flows/a_b"
    assert catalog["workspace"].identity == "flows/workspace"
    assert catalog["class"].identity == "flows/class"
    assert catalog.parent.identity == "flows/parent"
    assert catalog.parent.child.identity == "flows/parent/child"
    assert catalog.parent["run"].identity == "flows/parent/run"
    assert "exact" in dir(catalog)
    assert "non-identifier" not in dir(catalog)
    assert catalog.workspace is workspace  # member collision stays exact-only
    with pytest.raises(AttributeError):
        getattr(catalog, "non-identifier")

    first = workspace.discover()
    _echo_flow(root, "flows/added_later")
    assert "flows/added_later" not in {flow.identity for flow in first}
    assert catalog.added_later.identity == "flows/added_later"
    assert "flows/added_later" in {flow.identity for flow in workspace.discover()}


def test_workspace_public_annotations_are_runtime_resolvable() -> None:
    from napflow.core import Workspace

    assert get_type_hints(Workspace.flow)["return"] is Flow
    assert get_type_hints(Workspace.discover)["return"] == tuple[Flow, ...]
    assert get_type_hints(Workspace.flows.fget)["return"].__name__ == "FlowCatalog"


def test_custom_flows_root_catalog_strips_only_configured_root(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "napflow.yaml").write_text(
        'schema: "napflow/v1"\nflows: {root: "pipelines", main: "pipelines/main"}\n',
        encoding="utf-8",
    )
    _echo_flow(root, "pipelines/group/leaf")
    workspace = load_workspace(root)

    assert workspace.flows.group.leaf.identity == "pipelines/group/leaf"
    assert workspace.flows["group/leaf"] == workspace.flow("pipelines/group/leaf")


def test_reusable_handles_isolate_inputs_env_and_workspaces(tmp_path: Path) -> None:
    root_a = _workspace(tmp_path / "a", default_env="one")
    root_b = _workspace(tmp_path / "b", default_env="two")
    for root, profile, value in ((root_a, "one", "A"), (root_b, "two", "B")):
        (root / "envs").mkdir()
        (root / "envs" / f"{profile}.env").write_text(
            f"VALUE={value}\n", encoding="utf-8"
        )
        _echo_flow(root, "flows/echo", default="{{ env.VALUE }}")

    flow_a = load_workspace(root_a).flow("flows/echo")
    flow_b = load_workspace(root_b).flow("flows/echo")
    assert flow_a.run(history=False).end_outputs == {"result": "A"}
    assert flow_b.run(history=False).end_outputs == {"result": "B"}
    assert flow_a.run(inputs={"value": "input-a"}, history=False).end_outputs == {
        "result": "input-a"
    }
    assert flow_a.run(inputs={"value": "input-b"}, history=False).end_outputs == {
        "result": "input-b"
    }
    assert flow_a.run(
        env_overrides={"VALUE": "override"}, history=False
    ).end_outputs == {"result": "override"}


def test_async_runs_share_public_path_without_runtime_state(tmp_path: Path) -> None:
    root = _workspace(tmp_path / "ws")
    _echo_flow(root, "flows/echo")
    workspace = load_workspace(root)
    flow = workspace.flow("flows/echo")

    async def scenario() -> None:
        one, two, three = await asyncio.gather(
            flow.run_async(inputs={"value": "one"}, history=False),
            flow.run_async(inputs={"value": "two"}, history=False),
            run_flow_async(
                workspace,
                "flows/echo",
                inputs={"value": "three"},
                history=False,
            ),
        )
        assert [one.end_outputs, two.end_outputs, three.end_outputs] == [
            {"result": "one"},
            {"result": "two"},
            {"result": "three"},
        ]
        with pytest.raises(RuntimeError, match="await run_flow_async"):
            run_flow(workspace, "flows/echo", history=False)

    asyncio.run(scenario())


def test_each_public_run_gets_distinct_complete_history(tmp_path: Path) -> None:
    root = _workspace(tmp_path / "ws")
    _echo_flow(root, "flows/echo")
    flow = load_workspace(root).flow("flows/echo")

    flow.run(inputs={"value": "one"})
    flow.run(inputs={"value": "two"})

    logs = sorted((root / ".napflow/runs/flows/echo").glob("*.jsonl"))
    assert len(logs) == 2
    records = [
        [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        for log in logs
    ]
    assert {entry[-1]["state"] for entry in records} == {"passed"}
    assert {entry[0]["run_id"] for entry in records} == {log.stem for log in logs}
    assert not list((root / ".napflow/runs").rglob("*.active"))


def test_async_cancellation_abandons_history_after_runtime_cleanup(
    tmp_path: Path,
) -> None:
    root = _workspace(tmp_path / "ws")
    _delay_flow(root, "flows/slow")
    flow = load_workspace(root).flow("flows/slow")

    async def scenario() -> None:
        task = asyncio.create_task(flow.run_async())
        runs_dir = root / ".napflow/runs/flows/slow"
        async with asyncio.timeout(5):
            while True:
                logs = list(runs_dir.glob("*.jsonl"))
                if logs and '"node":"wait"' in logs[0].read_text(encoding="utf-8"):
                    break
                await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert not list((root / ".napflow/runs").rglob("*.active"))
    logs = list((root / ".napflow/runs/flows/slow").glob("*.jsonl"))
    assert len(logs) == 1
    for line in logs[0].read_text(encoding="utf-8").splitlines():
        json.loads(line)
