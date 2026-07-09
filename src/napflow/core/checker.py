"""`napf check` rules: E001–E012, W101–W107 (engine spec §8, FR-301–309).

Errors block `napf run`; warnings print and proceed. Every diagnostic
carries file path, best-effort line/column (ruamel marks via
`loader.locate`), the offending node id, and a one-line fix hint (EC29:
diagnostic quality is product surface).

Rule ownership: per-field structure is model territory (loader raises
LoadError, mapped here to E001/E002/E011); everything spanning nodes,
edges, files, or flows lives here. E010 is permanently reserved — never
reuse it.
"""

import ast
import datetime
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from napflow.core.loader import LoadedFlow, LoadError, load_flow, locate
from napflow.core.models import (
    CounterNode,
    EndNode,
    FixtureNode,
    FlowFile,
    FlowNode,
    LoopNode,
    MergeNode,
    Node,
    PythonNode,
    StartNode,
    SwitchNode,
    TimeoutNode,
)
from napflow.core.templating import (
    create_environment,
    expression_syntax_error,
    referenced_nodes,
    template_syntax_error,
)
from napflow.core.workspace import EnvFileError, Workspace, parse_env_file

GUARD_TYPES = ("counter", "timeout")
_MERGE_INPUT_RE = re.compile(r"^in[1-9][0-9]*$")

# fields holding bare Jinja2 expressions (everything else is `{{ }}` text)
_EXPR_FIELDS = {"expr", "over"}


@dataclass(frozen=True)
class CheckDiagnostic:
    code: str  # "E001".."E012" / "W101".."W107"
    message: str
    path: Path
    hint: str
    line: int | None = None
    column: int | None = None
    node_id: str | None = None

    @property
    def severity(self) -> str:
        return "error" if self.code.startswith("E") else "warning"

    def render(self) -> str:
        pos = f":{self.line}" if self.line is not None else ""
        node = f" [{self.node_id}]" if self.node_id else ""
        return f"{self.path}{pos}: {self.code}{node}: {self.message} ({self.hint})"


# --------------------------------------------------------------------------
# Port surfaces


@dataclass(frozen=True)
class PortSurface:
    """inputs/outputs: name → soft type; required_inputs must be wired
    (E005). merge grows inputs by wiring in1..inN (D11)."""

    inputs: dict[str, str]
    outputs: dict[str, str]
    required_inputs: frozenset[str]
    growable: bool = False  # merge: in1..inN


def _simple(
    inputs: dict[str, str], outputs: dict[str, str], *, optional: Sequence[str] = ()
) -> PortSurface:
    return PortSurface(
        inputs=inputs,
        outputs=outputs,
        required_inputs=frozenset(set(inputs) - set(optional)),
    )


class _SurfaceResolver:
    """Derives each node's port surface; flow/python surfaces need files
    beyond the node itself (target flow.yaml, nodes.py via AST — EC14:
    `check` never imports user code)."""

    def __init__(self, workspace: Workspace | None):
        self.workspace = workspace
        self._flow_files: dict[str, FlowFile | None] = {}
        self._modules: dict[
            Path, dict[str, ast.FunctionDef | ast.AsyncFunctionDef] | None
        ] = {}

    def target_flow(self, identity: str) -> FlowFile | None:
        """The FlowFile a flow/loop node references, or None when it
        cannot be loaded (reported separately as E008 / its own check)."""
        if identity not in self._flow_files:
            model = None
            if self.workspace is not None:
                file = self.workspace.root / Path(identity) / "flow.yaml"
                if file.is_file():
                    try:
                        model = load_flow(file).model
                    except LoadError:
                        model = None  # its own check run reports the details
            self._flow_files[identity] = model
        return self._flow_files[identity]

    def module_functions(
        self, flow_dir: Path
    ) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef] | None:
        """Top-level function defs in the flow's nodes.py, by AST only."""
        path = flow_dir / "nodes.py"
        if path not in self._modules:
            functions = None
            if path.is_file():
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8"))
                    functions = {
                        stmt.name: stmt
                        for stmt in tree.body
                        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef)
                    }
                except SyntaxError:
                    functions = None
            self._modules[path] = functions
        return self._modules[path]

    def surface(self, node: Node, flow_dir: Path) -> PortSurface | None:
        """None = surface unknowable (broken reference — E008 reported
        elsewhere); edge checks against this node are then skipped."""
        match node:
            case StartNode():
                outs = {"out": "object"} | {p.name: p.type for p in node.config.ports}
                return _simple({}, outs)
            case EndNode():
                inputs = {p.name: "any" for p in node.config.ports}
                optional = [p.name for p in node.config.ports if not p.required]
                return _simple(inputs, {}, optional=optional)
            case PythonNode():
                functions = self.module_functions(flow_dir)
                if functions is None or node.config.function not in functions:
                    return None
                fn = functions[node.config.function]
                positional = fn.args.posonlyargs + fn.args.args
                params = [a.arg for a in positional + fn.args.kwonlyargs]
                defaults = len(fn.args.defaults)
                literal_optional = {
                    a.arg
                    for a, d in zip(
                        positional[len(positional) - defaults :],
                        fn.args.defaults,
                        strict=True,
                    )
                    if isinstance(d, ast.Constant)  # non-literal ⇒ required (EC36)
                } | {
                    a.arg
                    for a, d in zip(
                        fn.args.kwonlyargs, fn.args.kw_defaults, strict=True
                    )
                    if isinstance(d, ast.Constant)
                }
                outs = dict.fromkeys(node.config.outputs, "any") | {"error": "object"}
                return _simple(
                    dict.fromkeys(params, "any"), outs, optional=literal_optional
                )
            case FlowNode():
                target = self.target_flow(node.config.flow)
                if target is None:
                    return None
                start = next(
                    (n for n in target.nodes if isinstance(n, StartNode)), None
                )
                end = next((n for n in target.nodes if isinstance(n, EndNode)), None)
                if start is None or end is None:
                    return None  # target fails E006 in its own check
                inputs = {p.name: p.type for p in start.config.ports}
                optional = [
                    p.name
                    for p in start.config.ports
                    if "default" in p.model_fields_set
                ]
                outs = {p.name: "any" for p in end.config.ports} | {"error": "object"}
                return _simple(inputs, outs, optional=optional)
            case SwitchNode():
                outs = {c.name: "any" for c in node.config.cases} | {"default": "any"}
                return _simple({"in": "any"}, outs)
            case MergeNode():
                return PortSurface(
                    inputs={},
                    outputs={"out": "any"},
                    required_inputs=frozenset(),
                    growable=True,
                )
            case CounterNode():
                return _simple(
                    {"in": "any", "reset": "any"},
                    {"continue": "any", "exhausted": "any"},
                    optional=["reset"],
                )
            case TimeoutNode():
                return _simple(
                    {"in": "any", "reset": "any"},
                    {"continue": "any", "expired": "any"},
                    optional=["reset"],
                )
            case LoopNode():
                return _simple(
                    {"trigger": "any"}, {"results": "list", "errors": "list"}
                )
            case FixtureNode():
                return _simple(
                    {"trigger": "any"}, {"value": "any"}, optional=["trigger"]
                )
            case _:
                pass
        return {
            "request": _simple(
                {"trigger": "any"}, {"response": "object", "error": "object"}
            ),
            "assert": _simple({"in": "any"}, {"passed": "any", "failed": "any"}),
            "condition": _simple({"in": "any"}, {"true": "any", "false": "any"}),
            "set": _simple({"in": "any"}, {"out": "any"}),
            "get": _simple({"trigger": "any"}, {"value": "any"}),
            "delay": _simple({"in": "any"}, {"out": "any"}),
            "log": _simple({"in": "any"}, {"out": "any"}),
            "note": _simple({}, {}),
        }[node.type]


# --------------------------------------------------------------------------
# Load-diagnostic mapping (E001 / E002 / E011)


def diagnostics_from_load_error(error: LoadError) -> list[CheckDiagnostic]:
    """Map loader diagnostics onto check codes: unknown keys/types are
    E002, bad node-id charset is E011, everything else is E001."""
    out = []
    for d in error.diagnostics:
        if d.kind in ("extra_forbidden", "union_tag_invalid"):
            code, hint = "E002", "see the node catalog in docs/napflow-flow-schema.md"
        elif d.kind == "string_pattern_mismatch" and d.loc and d.loc[-1] == "id":
            code, hint = "E011", "node ids match [A-Za-z_][A-Za-z0-9_]*"
        else:
            code, hint = "E001", "fix the file so it parses and validates"
        where = ".".join(str(s) for s in d.loc)
        message = f"{where}: {d.message}" if where else d.message
        out.append(
            CheckDiagnostic(
                code=code,
                message=message,
                path=error.path,
                hint=hint,
                line=d.line,
                column=d.column,
            )
        )
    return out


# --------------------------------------------------------------------------
# Single-flow checks


def _node_line(
    loaded: LoadedFlow, index: int, *suffix: int | str
) -> tuple[int | None, int | None]:
    pos = locate(loaded.doc, ("nodes", index, *suffix))
    return (pos[0], pos[1]) if pos else (None, None)


class _FlowCheck:
    def __init__(self, loaded: LoadedFlow, resolver: _SurfaceResolver):
        self.loaded = loaded
        self.flow = loaded.model
        self.resolver = resolver
        self.diags: list[CheckDiagnostic] = []
        self.index_of = {node.id: i for i, node in enumerate(self.flow.nodes)}
        self.surfaces: dict[str, PortSurface | None] = {}

    def add(
        self,
        code: str,
        message: str,
        hint: str,
        *,
        node: str | None = None,
        loc_suffix: tuple[int | str, ...] = (),
    ) -> None:
        line = column = None
        if node is not None and node in self.index_of:
            line, column = _node_line(self.loaded, self.index_of[node], *loc_suffix)
        self.diags.append(
            CheckDiagnostic(
                code=code,
                message=message,
                path=self.loaded.path,
                hint=hint,
                line=line,
                column=column,
                node_id=node,
            )
        )

    # -- E006 / E011 -------------------------------------------------------

    def check_cardinality_and_ids(self) -> None:
        seen: dict[str, str] = {}
        for node in self.flow.nodes:
            if node.id in seen:
                self.add(
                    "E011",
                    f"duplicate node id {node.id!r}",
                    "node ids are unique per flow",
                    node=node.id,
                )
            seen[node.id] = node.type
        for kind in ("start", "end"):
            count = sum(1 for n in self.flow.nodes if n.type == kind)
            if count != 1:
                self.add(
                    "E006",
                    f"flow has {count} {kind} nodes, expected exactly 1",
                    f"declare exactly one `{kind}` node",
                )

    # -- E012 ---------------------------------------------------------------

    def check_reserved_names(self) -> None:
        for node in self.flow.nodes:
            if isinstance(node, EndNode):
                for p, port in enumerate(node.config.ports):
                    if port.name == "error":
                        self.add(
                            "E012",
                            "End port name `error` is reserved",
                            "it is the flow node's implicit error port (D21)",
                            node=node.id,
                            loc_suffix=("config", "ports", p),
                        )
            if isinstance(node, PythonNode) and "error" in node.config.outputs:
                self.add(
                    "E012",
                    "python output name `error` is reserved",
                    "the implicit error port carries exceptions (D21)",
                    node=node.id,
                    loc_suffix=("config", "outputs"),
                )

    # -- E003 / E004 / E005 --------------------------------------------------

    def _surface(self, node_id: str) -> PortSurface | None:
        if node_id not in self.surfaces:
            node = self.flow.nodes[self.index_of[node_id]]
            self.surfaces[node_id] = self.resolver.surface(
                node, self.loaded.path.parent
            )
        return self.surfaces[node_id]

    def check_edges(self) -> None:
        connected_to: dict[str, list[int]] = {}
        for e, edge in enumerate(self.flow.edges):
            for endpoint, is_output in ((edge.from_, True), (edge.to, False)):
                node_id, _, port = endpoint.partition(".")
                if node_id not in self.index_of:
                    self._edge_diag(
                        "E003",
                        f"edge references unknown node {node_id!r}",
                        "check node ids",
                        e,
                    )
                    continue
                surface = self._surface(node_id)
                if surface is None:
                    continue  # broken reference — E008 reported separately
                ports = surface.outputs if is_output else surface.inputs
                growable_ok = (
                    not is_output
                    and surface.growable
                    and _MERGE_INPUT_RE.match(port) is not None
                )
                if port not in ports and not growable_ok:
                    direction = "output" if is_output else "input"
                    known = ", ".join(sorted(ports)) or "none"
                    extra = (
                        " (or in1..inN)" if surface.growable and not is_output else ""
                    )
                    self._edge_diag(
                        "E003",
                        f"edge references unknown {direction} port {endpoint!r}",
                        f"known {direction}s: {known}{extra}",
                        e,
                    )
            connected_to.setdefault(edge.to, []).append(e)

        for target, indices in connected_to.items():
            if len(indices) > 1:
                self._edge_diag(
                    "E004",
                    f"{len(indices)} edges into input port {target!r}",
                    "input ports accept exactly one edge; join paths with a merge node",
                    indices[-1],
                )

    def _edge_diag(self, code: str, message: str, hint: str, index: int) -> None:
        pos = locate(self.loaded.doc, ("edges", index))
        self.diags.append(
            CheckDiagnostic(
                code=code,
                message=message,
                path=self.loaded.path,
                hint=hint,
                line=pos[0] if pos else None,
                column=pos[1] if pos else None,
            )
        )

    def check_required_inputs(self) -> None:
        wired = {edge.to for edge in self.flow.edges}
        for node in self.flow.nodes:
            surface = self._surface(node.id)
            if surface is None:
                continue
            for port in sorted(surface.required_inputs):
                if f"{node.id}.{port}" not in wired:
                    is_end = isinstance(node, EndNode)
                    what = "required End port" if is_end else "required input"
                    hint = (
                        "a required End port with no edge can never be written — "
                        "wire it or mark it required: false (D18)"
                        if is_end
                        else "connect an edge or the node can never fire"
                    )
                    self.add(
                        "E005",
                        f"{what} {node.id}.{port} is not connected",
                        hint,
                        node=node.id,
                    )

    # -- E008 (+ static reference shape) -------------------------------------

    def check_file_references(self) -> None:
        ws = self.resolver.workspace
        for node in self.flow.nodes:
            refs: list[tuple[str, tuple[int | str, ...]]] = []
            if isinstance(node, FlowNode):
                refs.append((node.config.flow, ("config", "flow")))
            elif isinstance(node, LoopNode):
                refs.append((node.config.body, ("config", "body")))
            for ref, loc in refs:
                if "{{" in ref:
                    self.add(
                        "E008",
                        f"flow reference {ref!r} must be a static path",
                        "flow references form a static DAG (E007) — no templates",
                        node=node.id,
                        loc_suffix=loc,
                    )
                    continue
                if ws is None:
                    continue
                target_file = ws.root / Path(ref) / "flow.yaml"
                if not target_file.is_file():
                    self.add(
                        "E008",
                        f"referenced flow {ref!r} not found ({target_file})",
                        "check the workspace-relative path",
                        node=node.id,
                        loc_suffix=loc,
                    )
                elif isinstance(node, LoopNode):
                    target = self.resolver.target_flow(ref)
                    if target is not None:
                        start = next(
                            (n for n in target.nodes if isinstance(n, StartNode)), None
                        )
                        port_names = (
                            {p.name for p in start.config.ports} if start else set()
                        )
                        if "item" not in port_names:
                            self.add(
                                "E008",
                                f"loop body {ref!r} declares no `item` Start port",
                                "the body's Start must declare `item` (EC36)",
                                node=node.id,
                                loc_suffix=("config", "body"),
                            )
            if isinstance(node, FixtureNode) and ws is not None:
                file = node.config.file
                if "{{" not in file and not (ws.root / Path(file)).is_file():
                    self.add(
                        "E008",
                        f"fixture file {file!r} not found under {ws.root}",
                        "fixture paths are workspace-relative",
                        node=node.id,
                        loc_suffix=("config", "file"),
                    )
            if isinstance(node, PythonNode):
                functions = self.resolver.module_functions(self.loaded.path.parent)
                if functions is None:
                    self.add(
                        "E008",
                        "nodes.py missing or not parseable next to flow.yaml",
                        "python nodes need a nodes.py with literal `def`s (EC14)",
                        node=node.id,
                        loc_suffix=("config", "function"),
                    )
                elif node.config.function not in functions:
                    self.add(
                        "E008",
                        f"function {node.config.function!r} not found in nodes.py",
                        "write a literal top-level `def` (EC14)",
                        node=node.id,
                        loc_suffix=("config", "function"),
                    )

    # -- E009 / W107 ----------------------------------------------------------

    def check_templates(self) -> None:
        env = create_environment()
        for i, node in enumerate(self.flow.nodes):
            doc_node = self.loaded.doc["nodes"][i]
            config = doc_node.get("config")
            if config is None:
                continue
            for loc, value in _walk(config, ("config",)):
                field = loc[-1]
                where = ".".join(map(str, loc))
                if isinstance(value, str):
                    error = None
                    if field in _EXPR_FIELDS:
                        error = expression_syntax_error(env, value)
                    elif "{{" in value or "{%" in value:
                        error = template_syntax_error(env, value)
                    if error:
                        self.add(
                            "E009",
                            f"jinja2 syntax error in {where}: {error}",
                            "see docs/napflow-flow-schema.md §Templating",
                            node=node.id,
                            loc_suffix=loc,
                        )
                    elif type(value) is str and _W107_DANGER.match(value):
                        self.add(
                            "W107",
                            f"unquoted {value!r} in {where} — "
                            "YAML 1.1 loaders coerce it",
                            "quote the value",
                            node=node.id,
                            loc_suffix=loc,
                        )
                elif isinstance(value, datetime.date | datetime.datetime):
                    self.add(
                        "W107",
                        f"unquoted date-like scalar in {where} "
                        "was parsed as a date object",
                        "quote the value to keep it a string",
                        node=node.id,
                        loc_suffix=loc,
                    )
        # assert expr checks are expression-typed but the field is `expr`
        # inside checks[] — covered by _EXPR_FIELDS via the walk.

    # -- W101 -----------------------------------------------------------------

    def check_guarded_cycles(self) -> None:
        guardless = {n.id: [] for n in self.flow.nodes if n.type not in GUARD_TYPES}
        for edge in self.flow.edges:
            a, _, _ = edge.from_.partition(".")
            b, _, _ = edge.to.partition(".")
            if a in guardless and b in guardless:
                guardless[a].append(b)
        cycle = _find_cycle(guardless)
        if cycle:
            path = " → ".join(cycle)
            self.add(
                "W101",
                f"edge cycle without a counter/timeout guard: {path}",
                "put a guard node inside every cycle (possible infinite loop)",
                node=cycle[0],
            )

    # -- W103 / W106 ----------------------------------------------------------

    def check_unconnected_outputs(self) -> None:
        wired_from = {edge.from_ for edge in self.flow.edges}
        for node in self.flow.nodes:
            error_ports = []
            if node.type in ("request", "python", "flow"):
                error_ports.append(("error", "W103"))
            if node.type == "assert":
                error_ports.append(("failed", "W103"))
            if node.type == "counter":
                error_ports.append(("exhausted", "W106"))
            if node.type == "timeout":
                error_ports.append(("expired", "W106"))
            for port, code in error_ports:
                if f"{node.id}.{port}" not in wired_from:
                    if code == "W103":
                        message = f"error output {node.id}.{port} is unconnected"
                        hint = "an unhandled message here marks the run failed"
                    else:
                        message = f"guard exit {node.id}.{port} is unconnected"
                        hint = "this loop exit produces no output (D19)"
                    self.add(code, message, hint, node=node.id)

    # -- W104 -----------------------------------------------------------------

    def check_reachability(self) -> None:
        adjacency: dict[str, set[str]] = {n.id: set() for n in self.flow.nodes}
        for edge in self.flow.edges:
            a, _, _ = edge.from_.partition(".")
            b, _, _ = edge.to.partition(".")
            if a in adjacency and b in adjacency:
                adjacency[a].add(b)
        roots = [
            n.id
            for n in self.flow.nodes
            if isinstance(n, StartNode)
            or (
                isinstance(n, FixtureNode)
                and not any(e.to == f"{n.id}.trigger" for e in self.flow.edges)
            )
        ]
        seen = set(roots)
        stack = list(roots)
        while stack:
            for succ in adjacency[stack.pop()]:
                if succ not in seen:
                    seen.add(succ)
                    stack.append(succ)
        for node in self.flow.nodes:
            if node.type == "note" or node.id in seen:
                continue
            self.add(
                "W104",
                f"node {node.id!r} is unreachable from start",
                "wire it into the graph or remove it",
                node=node.id,
            )

    # -- W102 -----------------------------------------------------------------

    def check_port_types(self) -> None:
        for e, edge in enumerate(self.flow.edges):
            a, _, ap = edge.from_.partition(".")
            b, _, bp = edge.to.partition(".")
            sa = self._surface(a) if a in self.index_of else None
            sb = self._surface(b) if b in self.index_of else None
            if sa is None or sb is None:
                continue
            ta = sa.outputs.get(ap, "any")
            tb = sb.inputs.get(bp, "any")
            if "any" not in (ta, tb) and ta != tb:
                self._edge_diag(
                    "W102",
                    f"port type mismatch: {edge.from_} is {ta}, {edge.to} expects {tb}",
                    "soft types never block, but this edge looks wrong",
                    e,
                )

    def run(self) -> list[CheckDiagnostic]:
        self.check_cardinality_and_ids()
        self.check_reserved_names()
        self.check_edges()
        self.check_required_inputs()
        self.check_file_references()
        self.check_templates()
        self.check_guarded_cycles()
        self.check_unconnected_outputs()
        self.check_reachability()
        self.check_port_types()
        return self.diags


def _walk(
    node: Any, loc: tuple[int | str, ...]
) -> Iterator[tuple[tuple[int | str, ...], Any]]:
    """Yield (loc, scalar) for every leaf under a config mapping."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, (*loc, key))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            yield from _walk(item, (*loc, i))
    else:
        yield loc, node


_W107_DANGER = re.compile(
    r"^([yY]|yes|Yes|YES|[nN]|no|No|NO|true|True|false|False"
    r"|on|On|ON|off|Off|OFF|~|null|Null|NULL"
    r"|[-+]?[0-9]+(:[0-9]+)+)$"  # sexagesimal — a string in YAML 1.2
)


def _find_cycle(adjacency: dict[str, list[str]]) -> list[str] | None:
    """First cycle in a directed graph, as [a, b, ..., a]; else None."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(adjacency, WHITE)

    def dfs(start: str) -> list[str] | None:
        stack: list[tuple[str, Iterator[str]]] = [(start, iter(adjacency[start]))]
        color[start] = GRAY
        while stack:
            node, successors = stack[-1]
            advanced = False
            for succ in successors:
                if color[succ] == GRAY:
                    path = [entry[0] for entry in stack]
                    return path[path.index(succ) :] + [succ]
                if color[succ] == WHITE:
                    color[succ] = GRAY
                    stack.append((succ, iter(adjacency[succ])))
                    advanced = True
                    break
            if not advanced:
                color[node] = BLACK
                stack.pop()
        return None

    for node in adjacency:
        if color[node] == WHITE:
            cycle = dfs(node)
            if cycle:
                return cycle
    return None


# --------------------------------------------------------------------------
# Workspace-level checks (closure, E007, W105)


def check_flow(
    loaded: LoadedFlow, workspace: Workspace | None = None
) -> list[CheckDiagnostic]:
    """Single-file rule set. Without a workspace, file-reference and env
    checks are skipped — `check_workspace` is the full CI gate."""
    return _FlowCheck(loaded, _SurfaceResolver(workspace)).run()


def node_surfaces(
    model: FlowFile, flow_dir: Path, workspace: Workspace | None = None
) -> dict[str, PortSurface | None]:
    """Port surface per node id — the canvas needs these to draw
    handles and D11 type coloring, and it must NOT derive them itself:
    python ports come from AST (EC14, never imports user code), flow
    ports from the referenced target's Start/End. None = unknowable
    (broken reference; E008 is reported by the checks, not here)."""
    resolver = _SurfaceResolver(workspace)
    return {node.id: resolver.surface(node, flow_dir) for node in model.nodes}


def template_refs(model: FlowFile) -> dict[str, list[str]]:
    """Cross-node template references per node id (FR-1007): `nodes.<id>`
    in `{{ }}`/`{% %}` config strings and in bare expression fields,
    AST-derived like E009's parse. Only ids that exist in this flow
    count, and nodes without references are omitted — the canvas draws
    these as ghost-wires (flow-schema §Templating)."""
    env = create_environment()
    ids = {node.id for node in model.nodes}
    out: dict[str, list[str]] = {}
    for node in model.nodes:
        config = node.model_dump(mode="json").get("config")
        if not isinstance(config, dict):
            continue
        refs: set[str] = set()
        for loc, value in _walk(config, ("config",)):
            if not isinstance(value, str):
                continue
            if loc[-1] in _EXPR_FIELDS:
                refs |= referenced_nodes(env, value, expression=True)
            elif "{{" in value or "{%" in value:
                refs |= referenced_nodes(env, value)
        found = sorted(refs & ids)
        if found:
            out[node.id] = found
    return out


def used_by(workspace: Workspace, identity: str) -> dict[str, list[str]]:
    """Flows whose flow/loop nodes reference `identity`, with the
    referencing node ids — D09's "used in N places" (a place = a
    referencing node). Discovered flows only; unloadable flows are
    skipped (they carry their own E-codes)."""
    users: dict[str, list[str]] = {}
    for ref in workspace.discover_flows():
        if ref.identity == identity:
            continue  # a self-reference is E007's problem, not a "use"
        try:
            model = load_flow(ref.file).model
        except LoadError:
            continue
        using = [
            node.id
            for node in model.nodes
            if (isinstance(node, FlowNode) and node.config.flow == identity)
            or (isinstance(node, LoopNode) and node.config.body == identity)
        ]
        if using:
            users[ref.identity] = using
    return users


def python_functions(flow_dir: Path) -> list[str] | None:
    """Top-level function names in the flow's nodes.py, AST-only (EC14 —
    never imports user code). None = no nodes.py or a syntax error; the
    canvas function dropdown distinguishes that from an empty module."""
    functions = _SurfaceResolver(None).module_functions(flow_dir)
    return None if functions is None else list(functions)


def check_workspace(workspace: Workspace) -> list[CheckDiagnostic]:
    """`napf check`: every discovered flow, the closure of referenced
    flows (FR-308), reference-DAG validation (E007), env coverage (W105)."""
    resolver = _SurfaceResolver(workspace)
    diags: list[CheckDiagnostic] = []
    loaded_flows: dict[str, LoadedFlow] = {}
    references: dict[str, list[tuple[str, str, int]]] = {}  # id -> (target, node, idx)

    queue = [ref.identity for ref in workspace.discover_flows()]
    seen = set(queue)
    while queue:
        identity = queue.pop(0)
        file = workspace.root / Path(identity) / "flow.yaml"
        if not file.is_file():
            continue  # referencing flow already got E008
        try:
            loaded = load_flow(file)
        except LoadError as e:
            diags.extend(diagnostics_from_load_error(e))
            continue
        loaded_flows[identity] = loaded
        diags.extend(_FlowCheck(loaded, resolver).run())
        references[identity] = []
        for i, node in enumerate(loaded.model.nodes):
            target = None
            if isinstance(node, FlowNode):
                target = node.config.flow
            elif isinstance(node, LoopNode):
                target = node.config.body
            if target and "{{" not in target:
                references[identity].append((target, node.id, i))
                if target not in seen:
                    seen.add(target)
                    queue.append(target)  # closure: check referenced flows too

    diags.extend(_reference_cycles(references, loaded_flows))
    diags.extend(_env_coverage(workspace, loaded_flows))
    return sorted(diags, key=lambda d: (str(d.path), d.line or 0, d.code))


def check_run_closure(
    loaded: LoadedFlow, identity: str, workspace: Workspace
) -> list[CheckDiagnostic]:
    """`napf run` gate (WM, deepened at S3/M5): the entry flow PLUS
    every flow reachable through flow/loop references, with E007 cycle
    detection over that sub-closure — a broken subflow must block the
    run before anything executes, exactly like a broken entry flow."""
    resolver = _SurfaceResolver(workspace)
    diags: list[CheckDiagnostic] = []
    loaded_flows: dict[str, LoadedFlow] = {}
    references: dict[str, list[tuple[str, str, int]]] = {}
    queue = [identity]
    seen = {identity}
    while queue:
        current = queue.pop(0)
        if current == identity:
            current_loaded = loaded
        else:
            file = workspace.root / Path(current) / "flow.yaml"
            if not file.is_file():
                continue  # the referencing flow already got E008
            try:
                current_loaded = load_flow(file)
            except LoadError as e:
                diags.extend(diagnostics_from_load_error(e))
                continue
        loaded_flows[current] = current_loaded
        diags.extend(_FlowCheck(current_loaded, resolver).run())
        references[current] = []
        for i, node in enumerate(current_loaded.model.nodes):
            target = None
            if isinstance(node, FlowNode):
                target = node.config.flow
            elif isinstance(node, LoopNode):
                target = node.config.body
            if target and "{{" not in target:
                references[current].append((target, node.id, i))
                if target not in seen:
                    seen.add(target)
                    queue.append(target)
    diags.extend(_reference_cycles(references, loaded_flows))
    return sorted(diags, key=lambda d: (str(d.path), d.line or 0, d.code))


def _reference_cycles(
    references: dict[str, list[tuple[str, str, int]]],
    loaded_flows: dict[str, LoadedFlow],
) -> list[CheckDiagnostic]:
    adjacency = {
        identity: [t for t, _, _ in refs if t in references]
        for identity, refs in references.items()
    }
    cycle = _find_cycle(adjacency)
    if not cycle:
        return []
    culprit, target = cycle[0], cycle[1]
    loaded = loaded_flows[culprit]
    node_id, index = next((n, i) for t, n, i in references[culprit] if t == target)
    pos = locate(loaded.doc, ("nodes", index, "config"))
    return [
        CheckDiagnostic(
            code="E007",
            message=f"flow-reference cycle: {' → '.join(cycle)}",
            path=loaded.path,
            hint="flow references are a strict DAG — no recursive flows",
            line=pos[0] if pos else None,
            column=pos[1] if pos else None,
            node_id=node_id,
        )
    ]


def _env_coverage(
    workspace: Workspace, loaded_flows: dict[str, LoadedFlow]
) -> list[CheckDiagnostic]:
    diags = []
    available: set[str] = set()
    for name, path in workspace.env_profiles().items():
        try:
            available |= parse_env_file(path).keys()
        except EnvFileError as e:
            diags.append(
                CheckDiagnostic(
                    code="W105",
                    message=f"env profile {name!r} could not be parsed: {e}",
                    path=path,
                    hint="fix the profile; its keys cannot be checked (EC36 dialect)",
                )
            )
    for loaded in loaded_flows.values():
        env = loaded.model.env
        if env is None:
            continue
        for key in env.required:
            if key not in available:
                pos = locate(loaded.doc, ("env", "required"))
                diags.append(
                    CheckDiagnostic(
                        code="W105",
                        message=f"env.required key {key!r} in no discovered profile",
                        path=loaded.path,
                        hint="add it to an envs/*.env profile (process env can still "
                        "provide it at run time — EC17)",
                        line=pos[0] if pos else None,
                        column=pos[1] if pos else None,
                    )
                )
    return diags
