"""Run reports (FR-804): `none | junit | json` per `defaults.run.report`,
written next to the run's JSONL as `<run-id>.report.json` /
`<run-id>.junit.xml` (pinned at S2/M5).

Reports are explicit redacted views over the raw private JSONL: declared
secrets are removed from schema-classified content values while canonical
local truth and protocol structure remain exact. The junit mapping: one
testsuite per run; each `assert_result` is a
testcase (failures carry expected/actual); each unhandled error is an
errored testcase — CI dashboards show exactly what the exit code says.
"""

import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TextIO
from xml.sax.saxutils import quoteattr

from napflow.core.engine import RunResult
from napflow.core.events import SecretMasker


def _iter_records(
    log_path: Path, masker: SecretMasker
) -> Iterator[dict[str, Any]]:
    """Read and redact one canonical record at a time from a closed log."""
    with log_path.open(encoding="utf-8") as log:
        for line in log:
            if line.strip():
                yield masker.redact_record(json.loads(line))


def _report_facts(
    log_path: Path, masker: SecretMasker
) -> tuple[dict[str, Any], int, int]:
    """Return the final summary and bounded JUnit counters.

    The report path is reached only after ``FlowRun`` has closed the JSONL,
    so a completed run must have exactly one final summary.  Keep that one
    record plus integer counters; assertion records are streamed again only
    if a JUnit report is requested.
    """
    finished: dict[str, Any] = {}
    assertions = 0
    failures = 0
    for record in _iter_records(log_path, masker):
        event = record.get("event")
        if event == "assert_result":
            assertions += 1
            failures += not record["passed"]
        elif event == "run_finished":
            finished = record
    if not finished:
        raise ValueError(f"completed run log has no run_finished record: {log_path}")
    return finished, assertions, failures


def write_report(
    kind: str,
    log_path: Path,
    flow_identity: str,
    result: RunResult,
    *,
    masker: SecretMasker,
) -> Path | None:
    if kind == "none":
        return None
    finished, assertions, failures = _report_facts(log_path, masker)
    directory = log_path.parent
    run_id = log_path.stem
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
        with _atomic_report_writer(path) as report:
            json.dump(payload, report, indent=2, ensure_ascii=False)
            report.write("\n")
        return path
    path = directory / f"{run_id}.junit.xml"
    _write_junit(
        path,
        flow_identity,
        finished,
        assertions=assertions,
        failures=failures,
        records=_iter_records(log_path, masker),
    )
    return path


def _xml_attributes(values: dict[str, Any]) -> str:
    return "".join(
        f" {name}={quoteattr(_xml_text(value))}" for name, value in values.items()
    )


@contextmanager
def _atomic_report_writer(path: Path) -> Iterator[TextIO]:
    """Publish a complete report without following a planted target symlink."""
    fd, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    fd_open = True
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as report:
            fd_open = False
            yield report
            report.flush()
            os.fsync(report.fileno())
        temporary.replace(path)
    finally:
        if fd_open:
            os.close(fd)
        temporary.unlink(missing_ok=True)


def _xml_text(value: Any) -> str:
    """Replace characters XML 1.0 cannot represent with U+FFFD."""
    return "".join(
        character
        if character in "\t\n\r"
        or "\x20" <= character <= "\ud7ff"
        or "\ue000" <= character <= "\ufffd"
        or "\U00010000" <= character <= "\U0010ffff"
        else "\ufffd"
        for character in str(value)
    )


def _write_junit(
    path: Path,
    identity: str,
    finished: dict[str, Any],
    *,
    assertions: int,
    failures: int,
    records: Iterator[dict[str, Any]],
) -> None:
    """Stream JUnit cases without retaining the run or assertion set."""
    unhandled = finished.get("unhandled_errors", [])
    duration_ms = finished.get("duration_ms", 0)
    suite = _xml_attributes(
        {
            "name": identity,
            "tests": assertions + len(unhandled),
            "failures": failures,
            "errors": len(unhandled),
            "time": f"{duration_ms / 1000:.3f}",
        }
    )
    with _atomic_report_writer(path) as report:
        report.write("<?xml version='1.0' encoding='utf-8'?>\n")
        report.write(f"<testsuite{suite}>")
        for record in records:
            if record.get("event") != "assert_result":
                continue
            name = record["check"]
            if record.get("op"):
                name = f"{name} [{record['op']}]"
            case = _xml_attributes(
                {
                    "classname": f"{identity}.{record.get('node', '')}",
                    "name": name,
                }
            )
            if record["passed"]:
                report.write(f"\n  <testcase{case} />")
                continue
            message = (
                f"expected {record.get('expected')!r}, "
                f"actual {record.get('actual')!r}"
            )
            failure = _xml_attributes({"message": message})
            report.write(
                f"\n  <testcase{case}>\n    <failure{failure} />\n  </testcase>"
            )
        for error in unhandled:
            case = _xml_attributes(
                {
                    "classname": identity,
                    "name": f"{error.get('node') or 'run'}: {error['kind']}",
                }
            )
            detail = _xml_attributes({"message": str(error["message"])})
            report.write(f"\n  <testcase{case}>\n    <error{detail} />\n  </testcase>")
        report.write("\n</testsuite>")
