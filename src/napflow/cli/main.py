"""The `napf` CLI (Typer) — thin adapter over napflow.core.

S1 surface: init / list / check; S2 adds `napf run`; `napf ui` lands
with the server (S4). `run` exit codes come from the run state
(0/1/2/130, FR-406); everything else: 0 ok, 1 check errors,
2 operational error (no workspace, bad arguments).
"""

import asyncio
import json
import signal
from contextlib import suppress
from pathlib import Path
from typing import Annotated

import typer

import napflow
from napflow.cli.report import ListSink, write_report
from napflow.cli.scaffold import scaffold_workspace
from napflow.core.checker import (
    check_flow,
    check_workspace,
    diagnostics_from_load_error,
)
from napflow.core.engine import FlowRun
from napflow.core.events import (
    EventStream,
    JsonlSink,
    SecretMasker,
    apply_retention,
    new_run_id,
    run_log_path,
)
from napflow.core.loader import LoadError, load_flow
from napflow.core.models import EndNode, StartNode
from napflow.core.workspace import (
    EnvFileError,
    Workspace,
    WorkspaceNotFoundError,
    layer_env,
    load_workspace,
    parse_env_file,
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
    except WorkspaceNotFoundError as e:
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
    identity = Path(flow).as_posix().strip("/")
    file = ws.root / Path(identity) / "flow.yaml"
    if not file.is_file():
        known = ", ".join(r.identity for r in ws.discover_flows()) or "none"
        _fail(f"no flow at {identity!r} (discovered: {known})")

    # LOAD + CHECK (EN §2): E-codes block with exit 2; warnings proceed.
    # Gate = check_flow on the target; the full-workspace closure gate
    # stays `napf check` (deepens at S3 when flow references run).
    try:
        loaded = load_flow(file)
    except LoadError as e:
        for diag in diagnostics_from_load_error(e):
            typer.echo(diag.render(), err=True)
        raise typer.Exit(2) from e
    diagnostics = check_flow(loaded, ws)
    for diag in diagnostics:
        typer.echo(diag.render(), err=True)
    if any(d.severity == "error" for d in diagnostics):
        raise typer.Exit(2)

    # ENV: an explicit --env must exist; the manifest default is
    # best-effort (profiles are gitignored — fresh clones have none)
    profiles = ws.env_profiles()
    env_name = env if env is not None else ws.manifest.model.environments.default
    profile_values: dict[str, str] = {}
    if env is not None and env not in profiles:
        available = ", ".join(profiles) or "none"
        _fail(f"--env {env!r}: no envs/{env}.env (available: {available})")
    if env_name in profiles:
        try:
            profile_values = parse_env_file(profiles[env_name])
        except EnvFileError as e:
            _fail(str(e))
    elif env_name is not None:
        typer.echo(
            f"note: env profile {env_name!r} not found — process env only", err=True
        )
        env_name = None

    layered = layer_env(profile_values)
    bound = _parse_inputs(inputs, input_json)
    manifest = ws.manifest.model

    run_id = new_run_id()
    log_path = run_log_path(ws.root, identity, run_id)
    collect = ListSink()
    stream = EventStream(
        run_id,
        SecretMasker(manifest.environments.secrets, layered),
        [JsonlSink(log_path), collect],
    )
    apply_retention(log_path.parent, manifest.defaults.run.history)

    flow_run = FlowRun(
        loaded.model,
        flow_identity=identity,
        manifest=manifest,
        env=layered,
        env_name=env_name,
        inputs=bound,
        stream=stream,
        run_timeout_s=timeout,
        flow_dir=ws.root / Path(identity),
        workspace_root=ws.root,
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
