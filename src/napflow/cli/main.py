"""The `napf` CLI (Typer) — thin adapter over napflow.core.

S1 surface: init / list / check; S2 adds `napf run`; `napf ui` lands
with the server (S4). `run` exit codes come from the run state
(0/1/2/130, FR-406); everything else: 0 ok, 1 check errors,
2 operational error (no workspace, bad arguments).
"""

import asyncio
import json
import signal
import socket
import webbrowser
from contextlib import suppress
from pathlib import Path
from typing import Annotated

import typer

import napflow
from napflow.cli.report import ListSink, write_report
from napflow.cli.scaffold import scaffold_workspace
from napflow.core.checker import check_workspace
from napflow.core.engine import FlowRun
from napflow.core.loader import LoadError, load_flow
from napflow.core.models import EndNode, StartNode
from napflow.core.runprep import RunPrepError, open_run_stream, prepare_run
from napflow.core.workspace import (
    Workspace,
    WorkspaceBoundaryError,
    WorkspaceNotFoundError,
    load_workspace,
)

app = typer.Typer(
    name="napf",
    help="napflow — local-first, git-friendly, node-based API flows.",
    no_args_is_help=True,
    add_completion=False,
)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"napflow {napflow.__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_print_version,
            is_eager=True,
            help="Show version and exit.",
        ),
    ] = False,
) -> None: ...


def _workspace() -> Workspace:
    try:
        return load_workspace()
    except (WorkspaceNotFoundError, WorkspaceBoundaryError) as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e


@app.command()
def init(
    directory: Annotated[
        Path, typer.Argument(help="Workspace directory (created if missing).")
    ] = Path(),
) -> None:
    """Scaffold a workspace: manifest, flows/{main,example,smoke}, envs."""
    if (directory / "napflow.yaml").exists():
        typer.echo(f"error: {directory / 'napflow.yaml'} already exists", err=True)
        raise typer.Exit(2)
    for rel, status in scaffold_workspace(directory):
        typer.echo(f"  {status:<8} {rel}")
    typer.echo("\nfirst touch: cd into the workspace, then `napf check`")


@app.command("list")
def list_flows() -> None:
    """Discovered flows with their Start/End ports."""
    ws = _workspace()
    refs = ws.discover_flows()
    if not refs:
        typer.echo(f"no flows under {ws.flows_root}")
        return
    for ref in refs:
        try:
            model = load_flow(ref.file).model
        except LoadError:
            typer.echo(f"{ref.identity}  !! invalid — run `napf check`")
            continue
        start = next((n for n in model.nodes if isinstance(n, StartNode)), None)
        end = next((n for n in model.nodes if isinstance(n, EndNode)), None)
        inputs = ", ".join(
            f"{p.name}({p.type})" + ("?" if "default" in p.model_fields_set else "")
            for p in (start.config.ports if start else [])
        )
        outputs = ", ".join(
            p.name + ("" if p.required else "?")
            for p in (end.config.ports if end else [])
        )
        typer.echo(f"{ref.identity}  in: {inputs or '—'}  out: {outputs or '—'}")


def _fail(message: str) -> None:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(2)


class _LogEcho:
    """Live `log` events → stderr (FR-512: log nodes and worker
    stdout/stderr are visible as they happen, already masked)."""

    def write(self, record: dict) -> None:
        if record.get("event") != "log":
            return
        label = record.get("label") or record.get("node") or "log"
        value = record.get("value")
        shown = value if isinstance(value, str) else json.dumps(value, default=str)
        typer.echo(f"[{record.get('level', 'info')}] {label}: {shown}", err=True)

    def close(self) -> None:
        pass


def _parse_inputs(pairs: list[str] | None, input_json: str | None) -> dict:
    """`-i` values arrive as strings (BIND coerces them against the
    port's type); `--input-json` carries structured values; `-i`
    overrides per key."""
    bound: dict = {}
    if input_json is not None:
        try:
            parsed = json.loads(input_json)
        except ValueError as e:
            _fail(f"--input-json: {e}")
        if not isinstance(parsed, dict):
            _fail("--input-json must be a JSON object")
        bound.update(parsed)
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            _fail(f"-i expects KEY=VALUE, got {pair!r}")
        bound[key] = value
    return bound


@app.command()
def run(
    flow: Annotated[str, typer.Argument(help="Flow identity, e.g. flows/login.")],
    env: Annotated[
        str | None,
        typer.Option("--env", help="Env profile name (default: environments.default)."),
    ] = None,
    inputs: Annotated[
        list[str] | None,
        typer.Option(
            "--input", "-i", metavar="KEY=VALUE", help="Bind a Start-port input."
        ),
    ] = None,
    input_json: Annotated[
        str | None,
        typer.Option("--input-json", help="JSON object of inputs; -i overrides."),
    ] = None,
    timeout: Annotated[
        float | None,
        typer.Option("--timeout", help="Run deadline in seconds (expiry = exit 2)."),
    ] = None,
) -> None:
    """Run a flow headless: End outputs → stdout as one JSON object,
    logs → stderr, exit code 0/1/2/130 from the run state."""
    ws = _workspace()
    # LOAD + CHECK + ENV gate shared with the server (core/runprep.py):
    # E-codes block with exit 2 across the reference closure, warnings
    # proceed; explicit --env must exist, the default is best-effort.
    try:
        prepared = prepare_run(ws, flow, env)
    except RunPrepError as e:
        if e.diagnostics:
            for diag in e.diagnostics:
                typer.echo(diag.render(), err=True)
        else:
            typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e
    for diag in prepared.diagnostics:
        typer.echo(diag.render(), err=True)
    for note in prepared.notes:
        typer.echo(note, err=True)

    bound = _parse_inputs(inputs, input_json)
    manifest = ws.manifest.model
    identity = prepared.identity

    collect = ListSink()
    try:
        opened = open_run_stream(ws, prepared, extra_sinks=[collect, _LogEcho()])
    except WorkspaceBoundaryError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2) from e
    run_id, log_path, stream = opened.run_id, opened.log_path, opened.stream

    flow_run = FlowRun(
        prepared.loaded.model,
        flow_identity=identity,
        manifest=manifest,
        env=prepared.env,
        env_name=prepared.env_name,
        inputs=bound,
        stream=stream,
        run_timeout_s=timeout,
        flow_dir=ws.resolver.flow_dir(identity),
        workspace_root=ws.root,
        workspace_resolver=ws.resolver,
    )

    async def _execute():
        loop = asyncio.get_running_loop()
        # Ctrl-C = clean abort (exit 130) where loop signal handlers
        # exist; on Windows Proactor the KeyboardInterrupt path below
        # applies and the JSONL keeps a valid prefix (EC20)
        with suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(signal.SIGINT, flow_run.abort)
        try:
            return await flow_run.execute()
        finally:
            with suppress(NotImplementedError, RuntimeError, ValueError):
                loop.remove_signal_handler(signal.SIGINT)

    try:
        result = asyncio.run(_execute())
    except KeyboardInterrupt:
        typer.echo("aborted", err=True)
        raise typer.Exit(130) from None
    finally:
        stream.close()

    tally = f"{result.asserts_passed} passed, {result.asserts_failed} failed"
    typer.echo(
        f"{result.state} — asserts: {tally} — {result.duration_ms:.0f}ms", err=True
    )
    for error in result.unhandled_errors:
        where = error.get("node") or "run"
        typer.echo(f"  ! {where}: {error['kind']}: {error['message']}", err=True)
    if result.nodes_never_fired:
        skipped = ", ".join(result.nodes_never_fired)
        typer.echo(f"  skipped (never fired): {skipped}", err=True)
    typer.echo(f"run log: {log_path}", err=True)
    report_path = write_report(
        manifest.defaults.run.report,
        log_path.parent,
        run_id,
        identity,
        result,
        collect.records,
    )
    if report_path is not None:
        typer.echo(f"report: {report_path}", err=True)

    # stdout carries ONLY the End outputs — `napf run flows/login | jq
    # .token` is the contract, so this is deliberately NOT masked
    typer.echo(json.dumps(result.end_outputs, indent=2, ensure_ascii=False))
    raise typer.Exit(result.exit_code)


# "NAPF" on a phone keypad. When taken and --port wasn't explicit, the
# next free port of the following 20 is used (multiple workspaces open
# at once — the Jupyter convention).
DEFAULT_UI_PORT = 6273
_PORT_SCAN_SPAN = 20


def _port_free(port: int) -> bool:
    # probe WITHOUT SO_REUSEADDR — a TIME_WAIT port must read as taken
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True


def _pick_ui_port(requested: int | None) -> int:
    """Explicit --port: use it or fail (exit 2). Default: scan."""
    if requested is not None:
        if not _port_free(requested):
            _fail(f"--port {requested}: already in use")
        return requested
    for candidate in range(DEFAULT_UI_PORT, DEFAULT_UI_PORT + _PORT_SCAN_SPAN):
        if _port_free(candidate):
            return candidate
    _fail(
        f"no free port in {DEFAULT_UI_PORT}–"
        f"{DEFAULT_UI_PORT + _PORT_SCAN_SPAN - 1}; pass --port"
    )
    raise AssertionError("unreachable")


@app.command()
def ui(
    port: Annotated[
        int | None,
        typer.Option(
            "--port",
            help=f"Port to serve on (default {DEFAULT_UI_PORT}, auto-scans"
            " when taken; an explicit busy port is an error).",
        ),
    ] = None,
    no_browser: Annotated[
        bool,
        typer.Option("--no-browser", help="Don't open the default browser."),
    ] = False,
) -> None:
    """Serve the canvas UI + API + WebSocket on one localhost port and
    open the default browser (D03). Ctrl-C stops the server."""
    ws = _workspace()
    # deferred imports: blacksheep/uvicorn stay out of `napf run`'s path
    import uvicorn

    from napflow.server import build_app

    chosen = _pick_ui_port(port)
    server = uvicorn.Server(
        uvicorn.Config(
            build_app(ws),
            host="127.0.0.1",  # localhost only — never a network service
            port=chosen,
            log_level="warning",
            ws="websockets-sansio",
        )
    )
    url = f"http://127.0.0.1:{chosen}/"
    typer.echo(f"napflow ui: {url}  (workspace: {ws.root}, Ctrl-C stops)", err=True)

    async def _serve() -> None:
        serving = asyncio.get_running_loop().create_task(server.serve())
        if not no_browser:
            while not server.started and not serving.done():
                await asyncio.sleep(0.02)
            if server.started:
                webbrowser.open(url)
        await serving

    # uvicorn's own SIGINT handler usually shuts down first; suppress
    # covers the Windows KeyboardInterrupt path
    with suppress(KeyboardInterrupt):
        asyncio.run(_serve())


@app.command()
def check() -> None:
    """Validate all flows (schema, edges, guards, references, env)."""
    ws = _workspace()
    diagnostics = check_workspace(ws)
    for diag in diagnostics:
        typer.echo(diag.render())
    errors = sum(1 for d in diagnostics if d.severity == "error")
    warnings = len(diagnostics) - errors
    flows = len(ws.discover_flows())
    typer.echo(
        f"checked {flows} flow{'s' if flows != 1 else ''}: "
        f"{errors} error{'s' if errors != 1 else ''}, "
        f"{warnings} warning{'s' if warnings != 1 else ''}"
    )
    if errors:
        raise typer.Exit(1)
