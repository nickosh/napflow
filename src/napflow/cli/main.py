"""The `napf` CLI (Typer) — thin adapter over napflow.core.

S1 surface: init / list / check. `napf run` lands with the engine (S2),
`napf ui` with the server (S4). Exit codes: 0 ok, 1 check errors,
2 operational error (no workspace, bad arguments).
"""

from pathlib import Path
from typing import Annotated

import typer

import napflow
from napflow.cli.scaffold import scaffold_workspace
from napflow.core.checker import check_workspace
from napflow.core.loader import LoadError, load_flow
from napflow.core.models import EndNode, StartNode
from napflow.core.workspace import Workspace, WorkspaceNotFoundError, load_workspace

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
