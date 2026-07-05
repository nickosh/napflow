"""Run reports (FR-804): `none | junit | json` per `defaults.run.report`,
written next to the run's JSONL as `<run-id>.report.json` /
`<run-id>.junit.xml` (pinned at S2/M5).

Reports are built from the MASKED wire records (the same objects the
JSONL holds), so declared secrets never leak into CI artifacts. The
junit mapping: one testsuite per run; each `assert_result` is a
testcase (failures carry expected/actual); each unhandled error is an
errored testcase — CI dashboards show exactly what the exit code says.
"""

import json
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from napflow.core.engine import RunResult


class ListSink:
    """In-memory capture of the masked event stream — feeds reports and
    the stderr summary without re-reading the JSONL."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def write(self, record: dict[str, Any]) -> None:
        self.records.append(record)

    def close(self) -> None:
        pass


def write_report(
    kind: str,
    directory: Path,
    run_id: str,
    flow_identity: str,
    result: RunResult,
    records: list[dict[str, Any]],
) -> Path | None:
    if kind == "none":
        return None
    finished = next((r for r in records if r["event"] == "run_finished"), {})
    if kind == "json":
        path = directory / f"{run_id}.report.json"
        payload = {
            "flow": flow_identity,
            "run_id": run_id,
            "exit_code": result.exit_code,
        } | {
            key: value
            for key, value in finished.items()
            if key not in ("event", "run_id", "ts", "seq")
        }
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return path
    path = directory / f"{run_id}.junit.xml"
    path.write_bytes(_junit_xml(flow_identity, finished, records))
    return path


def _junit_xml(
    identity: str, finished: dict[str, Any], records: list[dict[str, Any]]
) -> bytes:
    asserts = [r for r in records if r["event"] == "assert_result"]
    unhandled = finished.get("unhandled_errors", [])
    failures = sum(1 for a in asserts if not a["passed"])
    duration_ms = finished.get("duration_ms", 0)
    suite = ElementTree.Element(
        "testsuite",
        {
            "name": identity,
            "tests": str(len(asserts) + len(unhandled)),
            "failures": str(failures),
            "errors": str(len(unhandled)),
            "time": f"{duration_ms / 1000:.3f}",
        },
    )
    for record in asserts:
        name = record["check"]
        if record.get("op"):
            name = f"{name} [{record['op']}]"
        case = ElementTree.SubElement(
            suite,
            "testcase",
            {"classname": f"{identity}.{record.get('node', '')}", "name": name},
        )
        if not record["passed"]:
            message = (
                f"expected {record.get('expected')!r}, actual {record.get('actual')!r}"
            )
            ElementTree.SubElement(case, "failure", {"message": message})
    for error in unhandled:
        case = ElementTree.SubElement(
            suite,
            "testcase",
            {
                "classname": identity,
                "name": f"{error.get('node') or 'run'}: {error['kind']}",
            },
        )
        ElementTree.SubElement(case, "error", {"message": str(error["message"])})
    ElementTree.indent(suite)
    return ElementTree.tostring(suite, encoding="utf-8", xml_declaration=True)
