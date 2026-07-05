"""napflow python-node worker — the child side of EN §5a.

STDLIB ONLY: this script runs under the interpreter configured in
napflow.yaml (`python.interpreter`, FR-108), which need not have
napflow installed. It is invoked by absolute path with one argument,
the flow's nodes.py. It must never import napflow.

Protocol (JSON lines; requests on stdin, replies on the dup'd fd):
  → {"task_id", "function", "inputs"}
  ← {"ready": true}                          once, after nodes.py imports
  ← {"stream": "stdout"|"stderr", "text"}    user output → log events
  ← {"task_id", "outputs"}                   success (the return value)
  ← {"task_id", "error", "error_kind", "error_type", "traceback"}
  ← {"fatal", "traceback"}                   import failure, then exit 1

Protocol integrity (EC28): fd 1 is dup()'d for protocol lines, then
pointed at fd 2 — so raw-fd writers (C extensions, user subprocesses)
land in the stderr pipe, and user `print()` goes through the rebound
`sys.stdout` capture. Nothing in nodes.py can corrupt the protocol.
"""

import importlib.util
import io
import json
import os
import sys
import traceback
from pathlib import Path

_LINE_CAP = 8192  # per stream line forwarded as a log event


class _StreamCapture(io.TextIOBase):
    """Rebound sys.stdout/sys.stderr: buffers until newline, emits each
    line as a protocol `stream` message. flush() emits any partial."""

    def __init__(self, name, emit):
        self._name = name
        self._emit = emit
        self._buf = ""

    def writable(self):
        return True

    def write(self, text):
        self._buf += str(text)
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._emit(self._name, line)
        return len(text)

    def flush(self):
        if self._buf:
            self._emit(self._name, self._buf)
            self._buf = ""


def main() -> int:
    proto = os.fdopen(os.dup(1), "w", encoding="utf-8", newline="\n")
    os.dup2(2, 1)  # raw fd-1 writers → stderr pipe, never the protocol

    def send(obj) -> None:
        proto.write(json.dumps(obj, ensure_ascii=False) + "\n")
        proto.flush()

    def emit_stream(name, text) -> None:
        send({"stream": name, "text": text[:_LINE_CAP]})

    sys.stdout = _StreamCapture("stdout", emit_stream)
    sys.stderr = _StreamCapture("stderr", emit_stream)

    nodes_path = sys.argv[1]
    try:
        spec = importlib.util.spec_from_file_location("nodes", nodes_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {nodes_path}")
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(Path(nodes_path).resolve().parent))
        spec.loader.exec_module(module)
    except BaseException:
        send({"fatal": "nodes.py import failed", "traceback": traceback.format_exc()})
        return 1
    send({"ready": True})

    def flush_streams() -> None:
        sys.stdout.flush()  # user output ordered before the task result
        sys.stderr.flush()

    for raw in sys.stdin.buffer:
        if not raw.strip():
            continue
        request = json.loads(raw.decode("utf-8"))
        task_id = request["task_id"]
        function = getattr(module, request["function"], None)
        if not callable(function):
            send(
                {
                    "task_id": task_id,
                    "error": f"nodes.py has no function {request['function']!r}",
                    "error_kind": "python_error",
                    "error_type": "LookupError",
                    "traceback": "",
                }
            )
            continue
        try:
            value = function(**request["inputs"])
        except AssertionError as exc:
            flush_streams()
            send(
                {
                    "task_id": task_id,
                    "error": str(exc) or "assertion failed",
                    "error_kind": "python_assert",
                    "error_type": "AssertionError",
                    "traceback": traceback.format_exc(),
                }
            )
            continue
        except BaseException as exc:  # the worker survives ANY user error
            flush_streams()
            send(
                {
                    "task_id": task_id,
                    "error": str(exc) or type(exc).__name__,
                    "error_kind": "python_error",
                    "error_type": type(exc).__name__,
                    "traceback": traceback.format_exc(),
                }
            )
            continue
        flush_streams()
        try:  # send() dumps before writing — a failed dumps sends nothing
            send({"task_id": task_id, "outputs": value})
        except (TypeError, ValueError):
            send(
                {
                    "task_id": task_id,
                    "error": "return value is not JSON-serializable"
                    f" (got {type(value).__name__})",
                    "error_kind": "python_error",
                    "error_type": "TypeError",
                    "traceback": "",
                }
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
