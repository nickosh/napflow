"""M5 bounded replay, lazy content, and durable frame drilldown."""

import json
from pathlib import Path

import napflow.server.replay as replay_module
from napflow.core.events import HISTORY_FEATURE_CONTENT_BLOBS, HISTORY_FORMAT
from napflow.core.history_content import RunContentStore
from test_server import make_scaffold_ws, with_client

FLOW = "flows/smoke"


def _record(run_id: str, event: str, seq: int, **fields) -> dict:
    return {
        "event": event,
        "run_id": run_id,
        "ts": "2026-07-13T00:00:00.000Z",
        "seq": seq,
        **fields,
    }


def _header(
    run_id: str,
    *,
    features: list[str] | None = None,
    root_frame: str | None = None,
) -> dict:
    record = _record(
        run_id,
        "run_started",
        1,
        format=HISTORY_FORMAT,
        features=[] if features is None else features,
        flow=FLOW,
        env_name=None,
        inputs={},
        engine_version="test",
    )
    if root_frame is not None:
        record["frame"] = root_frame
    return record


def _finished(run_id: str, seq: int, **fields) -> dict:
    payload = {
        "state": "passed",
        "duration_ms": 1.0,
        "asserts": {"passed": 0, "failed": 0},
        "unhandled_errors": [],
        "end_outputs": {},
        "nodes_never_fired": [],
    } | fields
    return _record(
        run_id,
        "run_finished",
        seq,
        **payload,
    )


def _log_path(workspace, run_id: str) -> Path:
    path = workspace.resolver.run_log(FLOW, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _write_records(path: Path, records: list[dict], partial: bytes = b"") -> None:
    payload = b"".join(
        json.dumps(record, separators=(",", ":")).encode("utf-8") + b"\n"
        for record in records
    )
    path.write_bytes(payload + partial)


def _url(run_id: str, suffix: str = "events") -> str:
    return f"/api/runs/{run_id}/{suffix}?flow={FLOW}"


def test_event_pages_are_bounded_cursor_based_and_validate_queries(tmp_path):
    workspace = make_scaffold_ws(tmp_path)
    run_id = "20260713-010000-000001"
    path = _log_path(workspace, run_id)
    records = [_header(run_id)]
    records.extend(
        _record(run_id, "node_fired", seq, frame="f-0", node="n", firing_no=seq)
        for seq in range(2, 505)
    )
    records.append(_finished(run_id, 505))
    _write_records(path, records)

    async def scenario(client):
        first = await client.get(_url(run_id))
        assert first.status == 200
        page = await first.json()
        assert {
            key: page[key]
            for key in (
                "api_format",
                "run_id",
                "run_format",
                "features",
                "root_frame",
                "history_state",
                "frame",
                "after_seq",
                "next_after_seq",
                "has_more",
            )
        } == {
            "api_format": "napflow-replay/1",
            "run_id": run_id,
            "run_format": HISTORY_FORMAT,
            "features": [],
            "root_frame": "f-0",
            "history_state": "complete",
            "frame": None,
            "after_seq": 0,
            "next_after_seq": 200,
            "has_more": True,
        }
        assert len(page["events"]) == 200
        assert page["run_summary"] == {
            "state": "passed",
            "duration_ms": 1.0,
            "asserts": {"passed": 0, "failed": 0},
            "unhandled_error_count": 0,
            "nodes_never_fired_count": 0,
        }
        assert page["view_summary"]["scope_frame"] == "f-0"
        assert page["view_summary"]["record_count"] == 505
        assert page["view_summary"]["nodes"]["n"] | {
            "ports": {},
            "request": None,
            "log": None,
        } == {
            "firings": 503,
            "active": False,
            "outcome": "none",
            "guard": None,
            "lastSeq": 504,
            "ports": {},
            "request": None,
            "log": None,
        }

        second = await client.get(_url(run_id) + "&after_seq=200&limit=500")
        page = await second.json()
        assert [event["seq"] for event in page["events"]] == list(range(201, 506))
        assert page["next_after_seq"] == 505
        assert page["has_more"] is False

        empty = await client.get(_url(run_id) + "&after_seq=999")
        page = await empty.json()
        assert page["events"] == []
        assert page["next_after_seq"] == 999
        assert page["has_more"] is False

        invalid = (
            "after_seq=-1",
            "after_seq=1.0",
            "limit=0",
            "limit=501",
            "limit=nope",
            "limit=1&limit=2",
            "frame=",
        )
        for query in invalid:
            response = await client.get(_url(run_id) + f"&{query}")
            assert response.status == 400, query
            assert (await response.json())["error"] == "bad_request"

        assert not list(path.parent.glob(f"{run_id}.reader-*"))

    with_client(workspace, scenario)


def test_event_page_projects_complete_node_edge_and_port_summary(tmp_path):
    workspace = make_scaffold_ws(tmp_path)
    run_id = "20260713-010000-00000f"
    path = _log_path(workspace, run_id)
    records = [
        _header(run_id),
        _record(run_id, "node_fired", 2, frame="f-0", node="a", firing_no=1),
        _record(
            run_id,
            "request_started",
            3,
            frame="f-0",
            node="a",
            method="GET",
            url="https://example.test",
            attempt=1,
        ),
        _record(
            run_id,
            "request_finished",
            4,
            frame="f-0",
            node="a",
            status=200,
            size_bytes=7,
            timing={"total_ms": 2.5},
            attempt=1,
        ),
        _record(
            run_id,
            "message_emitted",
            5,
            frame="f-0",
            node="a",
            from_port="a.out",
            to_node="b",
            to_port="in",
            value={"whole": "value"},
        ),
        _record(run_id, "node_fired", 6, frame="f-0", node="b", firing_no=1),
        _record(
            run_id,
            "log",
            7,
            frame="f-0",
            node="b",
            label="shown",
            level="info",
            value="full log",
        ),
        _record(
            run_id,
            "assert_result",
            8,
            frame="f-0",
            node="b",
            passed=False,
            check="no",
        ),
        _record(
            run_id,
            "guard_tripped",
            9,
            frame="f-0",
            node="b",
            port="exhausted",
        ),
        _finished(
            run_id,
            10,
            asserts={"passed": 0, "failed": 1},
            nodes_never_fired=["c"],
        ),
    ]
    _write_records(path, records)

    async def scenario(client):
        response = await client.get(_url(run_id) + "&frame=f-0&limit=1")
        assert response.status == 200
        payload = await response.json()
        assert [record["seq"] for record in payload["events"]] == [1]
        summary = payload["view_summary"]
        assert summary["record_count"] == 10
        assert summary["asserts"] == {"passed": 0, "failed": 1}
        assert summary["edges"]["a.out→b.in"] == {"count": 1, "lastSeq": 5}
        assert summary["nodes"]["a"]["outcome"] == "ok"
        assert summary["nodes"]["a"]["ports"]["out:out"]["lastValue"] == {
            "whole": "value"
        }
        assert summary["nodes"]["a"]["ports"]["out:out"]["lastSeq"] == 5
        assert summary["nodes"]["a"]["request"]["status"] == 200
        assert summary["nodes"]["b"]["ports"]["in:in"]["count"] == 1
        assert summary["nodes"]["b"]["ports"]["in:in"]["lastSeq"] == 5
        assert summary["nodes"]["b"]["log"] == {
            "ring": ["full log"],
            "count": 1,
        }
        assert summary["nodes"]["b"]["guard"] == "exhausted"
        assert summary["nodes"]["b"]["outcome"] == "failed"
        assert summary["nodes"]["b"]["active"] is False
        assert summary["nodes"]["c"]["outcome"] == "skipped"

    with_client(workspace, scenario)


def test_root_and_child_frame_pages_use_matching_cursors(tmp_path):
    workspace = make_scaffold_ws(tmp_path)
    run_id = "20260713-010000-000002"
    path = _log_path(workspace, run_id)
    root = "root-frame"
    child = f"{root}/child"
    grandchild = f"{child}/grandchild"
    other = f"{root}/other"
    records = [
        _header(run_id, root_frame=root),
        _record(run_id, "node_fired", 2, frame=root, node="root", firing_no=1),
        _record(run_id, "node_fired", 3, frame=child, node="child", firing_no=1),
        _record(run_id, "node_fired", 4, frame=grandchild, node="leaf", firing_no=1),
        _record(
            run_id,
            "frame_finished",
            5,
            frame=grandchild,
            parent_frame=child,
            parent_node="nested",
        ),
        _record(
            run_id,
            "frame_finished",
            6,
            frame=child,
            parent_frame=root,
            parent_node="container",
            flow="flows/child",
            kind="flow",
            loop_index=None,
            duration_ms=2.0,
            state="passed",
            asserts={"passed": 1, "failed": 0},
            unhandled_errors=[{"message": "must stay behind detail"}],
            end_outputs={"done": "must stay behind detail"},
        ),
        _record(
            run_id,
            "frame_finished",
            7,
            frame=other,
            parent_frame=root,
            parent_node="loop",
        ),
        _finished(run_id, 8),
    ]
    _write_records(path, records)

    async def scenario(client):
        root_page = await client.get(_url(run_id) + f"&frame={root}&limit=2")
        page = await root_page.json()
        assert page["root_frame"] == root
        assert [event["seq"] for event in page["events"]] == [1, 2]
        assert page["has_more"] is True
        assert page["view_summary"]["record_count"] == 3
        assert page["view_summary"]["nodes"]["root"]["firings"] == 1
        root_tail = await client.get(
            _url(run_id) + f"&frame={root}&after_seq={page['next_after_seq']}"
        )
        assert [event["seq"] for event in (await root_tail.json())["events"]] == [8]

        child_page = await client.get(_url(run_id) + f"&frame={child}")
        child_payload = await child_page.json()
        assert [event["seq"] for event in child_payload["events"]] == [3, 6]
        assert child_payload["view_summary"]["record_count"] == 2
        assert child_payload["view_summary"]["nodes"]["child"]["firings"] == 1

        direct = await client.get(_url(run_id, "frames") + "&limit=1")
        frame_page = await direct.json()
        assert frame_page["parent_frame"] == root
        assert [frame["seq"] for frame in frame_page["frames"]] == [6]
        assert frame_page["frames"][0]["unhandled_error_count"] == 1
        assert frame_page["frames"][0]["end_output_names"] == ["done"]
        assert "unhandled_errors" not in frame_page["frames"][0]
        assert "end_outputs" not in frame_page["frames"][0]
        assert frame_page["next_after_seq"] == 6
        assert frame_page["has_more"] is True
        direct_tail = await client.get(_url(run_id, "frames") + "&after_seq=6&limit=1")
        assert [frame["seq"] for frame in (await direct_tail.json())["frames"]] == [7]

        nested = await client.get(_url(run_id, "frames") + f"&parent_frame={child}")
        assert [frame["seq"] for frame in (await nested.json())["frames"]] == [5]

    with_client(workspace, scenario)


def test_event_detail_resolves_blob_only_on_demand(tmp_path):
    workspace = make_scaffold_ws(tmp_path)
    run_id = "20260713-010000-000003"
    path = _log_path(workspace, run_id)
    store = RunContentStore(path, inline_threshold_bytes=8)
    value = "full-fidelity-" * 10_000
    descriptor = store.persist(value)
    records = [
        _header(run_id, features=[HISTORY_FEATURE_CONTENT_BLOBS]),
        _record(
            run_id,
            "message_emitted",
            2,
            frame="f-0",
            node="source",
            from_port="source.out",
            to_node="target",
            to_port="in",
            value=descriptor,
        ),
        _finished(run_id, 3),
    ]
    _write_records(path, records)

    async def scenario(client):
        page_response = await client.get(_url(run_id))
        page = await page_response.json()
        assert page["events"][1]["value"] == descriptor
        source = page["view_summary"]["nodes"]["source"]["ports"]["out:out"]
        target = page["view_summary"]["nodes"]["target"]["ports"]["in:in"]
        assert source == {
            "count": 1,
            "lastValue": descriptor,
            "lastTs": "2026-07-13T00:00:00.000Z",
            "lastSeq": 2,
        }
        assert target == source

        detail = await client.get(_url(run_id, "events/2"))
        assert detail.status == 200
        payload = await detail.json()
        assert payload["api_format"] == "napflow-replay/1"
        assert payload["history_state"] == "complete"
        assert payload["event"]["value"] == value

        missing_event = await client.get(_url(run_id, "events/99"))
        assert missing_event.status == 404
        assert (await missing_event.json())["error"] == "event_not_found"

        digest = descriptor["$napflow"]["hash"].removeprefix("sha256:")
        (store.blob_dir / digest).unlink()
        still_lazy = await client.get(_url(run_id))
        assert still_lazy.status == 200
        assert (await still_lazy.json())["events"][1]["value"] == descriptor
        missing = await client.get(_url(run_id, "events/2"))
        assert missing.status == 404
        assert (await missing.json())["error"] == "history_content_missing"
        assert not list(path.parent.glob(f"{run_id}.reader-*"))

    with_client(workspace, scenario)


def test_detail_reports_corrupt_omitted_and_malformed_content(tmp_path):
    workspace = make_scaffold_ws(tmp_path)
    cases = (
        ("000004", "corrupt"),
        ("000005", "omitted"),
        ("000006", "malformed"),
    )
    paths: dict[str, Path] = {}
    for suffix, case in cases:
        run_id = f"20260713-010000-{suffix}"
        path = _log_path(workspace, run_id)
        store = RunContentStore(path, inline_threshold_bytes=1)
        if case == "corrupt":
            descriptor = store.persist("expected-content")
            digest = descriptor["$napflow"]["hash"].removeprefix("sha256:")
            blob = store.blob_dir / digest
            blob.write_bytes(b"x" * blob.stat().st_size)
        elif case == "omitted":
            descriptor = store.omit("not-stored", "hard_limit")
        else:
            descriptor = {"$napflow": {"kind": "blob", "hash": "bad"}}
        _write_records(
            path,
            [
                _header(run_id, features=[HISTORY_FEATURE_CONTENT_BLOBS]),
                _record(
                    run_id,
                    "log",
                    2,
                    frame="f-0",
                    node="show",
                    level="info",
                    value=descriptor,
                ),
                _finished(run_id, 3),
            ],
        )
        paths[case] = path

    async def scenario(client):
        expected = {
            "corrupt": (422, "history_content_corrupt"),
            "omitted": (422, "history_content_omitted"),
            "malformed": (422, "history_content_malformed"),
        }
        for (suffix, case), (status, code) in zip(
            cases, expected.values(), strict=True
        ):
            run_id = f"20260713-010000-{suffix}"
            response = await client.get(_url(run_id, "events/2"))
            assert response.status == status
            payload = await response.json()
            assert payload["error"] == code
            assert payload["message"]
            assert not list(paths[case].parent.glob(f"{run_id}.reader-*"))

    with_client(workspace, scenario)


def test_featureless_detail_preserves_marker_shaped_user_data(tmp_path):
    workspace = make_scaffold_ws(tmp_path)
    run_id = "20260713-010000-00000b"
    path = _log_path(workspace, run_id)
    literal = {"$napflow": {"kind": "blob", "user_note": "ordinary data"}}
    _write_records(
        path,
        [
            _header(run_id),
            _record(
                run_id,
                "log",
                2,
                frame="f-0",
                node="show",
                level="info",
                value=literal,
            ),
            _finished(run_id, 3),
        ],
    )

    async def scenario(client):
        response = await client.get(_url(run_id, "events/2"))
        assert response.status == 200
        assert (await response.json())["event"]["value"] == literal

    with_client(workspace, scenario)


def test_incomplete_prefix_and_large_final_record_are_classified(tmp_path):
    workspace = make_scaffold_ws(tmp_path)
    incomplete_id = "20260713-010000-000007"
    incomplete = _log_path(workspace, incomplete_id)
    _write_records(
        incomplete,
        [
            _header(incomplete_id),
            _record(
                incomplete_id,
                "node_fired",
                2,
                frame="f-0",
                node="n",
                firing_no=1,
            ),
        ],
        partial=b'{"event":"request_started"',
    )
    complete_id = "20260713-010000-000008"
    complete = _log_path(workspace, complete_id)
    large_final = _finished(complete_id, 2)
    large_final["nodes_never_fired"] = ["z" * 80_000]
    _write_records(complete, [_header(complete_id), large_final], partial=b"{partial")

    async def scenario(client):
        prefix = await client.get(_url(incomplete_id))
        payload = await prefix.json()
        assert payload["history_state"] == "incomplete"
        assert payload["run_summary"] is None
        assert [event["seq"] for event in payload["events"]] == [1, 2]

        final = await client.get(_url(complete_id))
        payload = await final.json()
        assert payload["history_state"] == "complete"
        assert payload["run_summary"]["nodes_never_fired_count"] == 1
        assert payload["events"][-1]["event"] == "run_finished"
        assert len(payload["events"][-1]["nodes_never_fired"][0]) == 80_000

    with_client(workspace, scenario)


def test_replay_rejects_non_consecutive_or_non_integer_sequences(tmp_path):
    workspace = make_scaffold_ws(tmp_path)

    async def scenario(client):
        for suffix, bad_seq in (("000009", 3), ("00000a", True)):
            run_id = f"20260713-010000-{suffix}"
            path = _log_path(workspace, run_id)
            _write_records(
                path,
                [
                    _header(run_id),
                    _record(
                        run_id,
                        "node_fired",
                        bad_seq,
                        frame="f-0",
                        node="n",
                        firing_no=1,
                    ),
                ],
            )
            response = await client.get(_url(run_id))
            assert response.status == 422
            assert (await response.json())["error"] == "history_format"

    with_client(workspace, scenario)


def test_replay_rejects_malformed_json_before_a_later_record(tmp_path):
    workspace = make_scaffold_ws(tmp_path)
    run_id = "20260713-010000-00000c"
    path = _log_path(workspace, run_id)
    header = json.dumps(_header(run_id), separators=(",", ":")).encode()
    final = json.dumps(_finished(run_id, 2), separators=(",", ":")).encode()
    path.write_bytes(header + b'\n{"event":"broken"\n' + final + b"\n")

    async def scenario(client):
        response = await client.get(_url(run_id))
        assert response.status == 422
        payload = await response.json()
        assert payload["error"] == "history_format"
        assert "later record" in payload["message"]

    with_client(workspace, scenario)


def test_page_and_detail_validate_corrupt_records_beyond_their_selection(tmp_path):
    workspace = make_scaffold_ws(tmp_path)
    run_id = "20260713-010000-00000e"
    path = _log_path(workspace, run_id)
    records = [
        _header(run_id),
        _record(run_id, "node_fired", 2, frame="f-0", node="n", firing_no=1),
        _record(run_id, "node_fired", 4, frame="f-0", node="n", firing_no=2),
        _finished(run_id, 5),
    ]
    _write_records(path, records)

    async def scenario(client):
        first_page = await client.get(_url(run_id) + "&limit=1")
        assert first_page.status == 422
        assert (await first_page.json())["error"] == "history_format"

        early_detail = await client.get(_url(run_id, "events/1"))
        assert early_detail.status == 422
        assert (await early_detail.json())["error"] == "history_format"

    with_client(workspace, scenario)


def test_replay_snapshot_does_not_mix_a_new_final_tail_into_an_older_page(
    tmp_path, monkeypatch
):
    workspace = make_scaffold_ws(tmp_path)
    run_id = "20260713-010000-000010"
    path = _log_path(workspace, run_id)
    prefix = [
        _header(run_id),
        _record(run_id, "node_fired", 2, frame="f-0", node="n", firing_no=1),
    ]
    _write_records(path, prefix)
    active = path.with_name(f"{run_id}.active")
    active.write_text("{}\n", encoding="utf-8")
    capture = replay_module.capture_replay_snapshot

    def append_after_capture(run, log_path):
        snapshot = capture(run, log_path)
        _write_records(path, [*prefix, _finished(run_id, 3)])
        active.unlink()
        return snapshot

    monkeypatch.setattr(
        replay_module,
        "capture_replay_snapshot",
        append_after_capture,
    )

    async def scenario(client):
        response = await client.get(_url(run_id))
        assert response.status == 200
        payload = await response.json()
        assert payload["history_state"] == "indeterminate"
        assert payload["run_summary"] is None
        assert [record["seq"] for record in payload["events"]] == [1, 2]
        assert payload["view_summary"]["record_count"] == 2
        assert payload["has_more"] is False

    with_client(workspace, scenario)


def test_page_and_detail_reject_invalid_utf8_as_history_format(tmp_path):
    workspace = make_scaffold_ws(tmp_path)
    run_id = "20260713-010000-000011"
    path = _log_path(workspace, run_id)
    header = json.dumps(_header(run_id), separators=(",", ":")).encode()
    final = json.dumps(_finished(run_id, 2), separators=(",", ":")).encode()
    path.write_bytes(header + b"\n\xff\n" + final + b"\n")

    async def scenario(client):
        for url in (_url(run_id), _url(run_id, "events/1")):
            response = await client.get(url)
            assert response.status == 422
            payload = await response.json()
            assert payload["error"] == "history_format"
            assert "UTF-8" in payload["message"]

    with_client(workspace, scenario)


def test_external_active_empty_prefix_is_indeterminate(tmp_path):
    workspace = make_scaffold_ws(tmp_path)
    run_id = "20260713-010000-00000d"
    path = _log_path(workspace, run_id)
    path.touch()
    active = path.with_name(f"{run_id}.active")
    active.write_text("{}\n", encoding="utf-8")

    async def scenario(client):
        listing = await client.get("/api/runs", query={"flow": FLOW})
        assert (await listing.json())["runs"] == [
            {"run_id": run_id, "state": "indeterminate"}
        ]
        response = await client.get(_url(run_id))
        assert response.status == 200
        payload = await response.json()
        assert payload["history_state"] == "indeterminate"
        assert payload["run_summary"] is None
        assert payload["events"] == []
        assert payload["run_format"] is None
        assert payload["features"] == []

    with_client(workspace, scenario)
