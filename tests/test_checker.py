"""FR-301–309: the `napf check` rule set, code by code."""

from pathlib import Path

import pytest

from napflow.core.checker import check_workspace, template_refs, used_by
from napflow.core.gitmeta import default_git_metadata_rules
from napflow.core.runprep import RunPrepError, prepare_run
from napflow.core.workspace import load_workspace

# --------------------------------------------------------------------------
# Workspace builder


def make_ws(tmp_path: Path, files: dict[str, str], *, git_metadata: bool = True):
    if "napflow.yaml" not in files:
        files = {"napflow.yaml": "schema: napflow/v1\n", **files}
    if git_metadata:
        files = {
            **{
                rules.filename: "\n".join(rules.required_rules) + "\n"
                for rules in default_git_metadata_rules()
            },
            **files,
        }
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="")
    return load_workspace(tmp_path)


def codes(diags) -> list[str]:
    return [d.code for d in diags]


OK_FLOW = """\
schema: napflow/v1
flow: {name: ok}
env: {required: [BASE_URL]}
nodes:
  - id: start
    type: start
    config: {ports: [{name: base, type: string, default: "{{ env.BASE_URL }}"}]}
  - id: req
    type: request
    config: {url: "{{ inputs.base }}/get"}
  - id: verify
    type: assert
    config: {checks: [{kind: status, equals: 200}]}
  - id: oops
    type: log
    config: {label: "transport error"}
  - id: end
    type: end
    config: {ports: [{name: result}, {name: problem, required: false}]}
edges:
  - {from: start.out, to: req.trigger}
  - {from: req.response, to: verify.in}
  - {from: verify.passed, to: end.result}
  - {from: verify.failed, to: end.problem}
  - {from: req.error, to: oops.in}
"""

DEV_ENV = "BASE_URL=https://httpbin.org\n"


def test_clean_flow_has_no_diagnostics(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, {"flows/main/flow.yaml": OK_FLOW, "envs/dev.env": DEV_ENV})
    assert check_workspace(ws) == []


# --------------------------------------------------------------------------
# E001 / E002 / E011 (loader mapping)


def test_e001_unparseable_yaml(tmp_path: Path) -> None:
    ws = make_ws(tmp_path, {"flows/bad/flow.yaml": "nodes: [unclosed\n"})
    diags = check_workspace(ws)
    assert codes(diags) == ["E001"]
    assert diags[0].line is not None


def test_e002_unknown_node_type_and_config_key(tmp_path: Path) -> None:
    flow = (
        "schema: napflow/v1\n"
        "flow: {name: t}\n"
        "nodes:\n"
        "  - {id: start, type: start}\n"
        "  - {id: w, type: webhook, config: {}}\n"
        "  - {id: n, type: note, config: {text: hi, color: red}}\n"
        "  - {id: end, type: end}\n"
    )
    ws = make_ws(tmp_path, {"flows/t/flow.yaml": flow})
    assert codes(check_workspace(ws)) == ["E002", "E002"]


def test_e011_bad_charset_and_duplicate(tmp_path: Path) -> None:
    bad_charset = (
        "schema: napflow/v1\nflow: {name: t}\n"
        "nodes:\n  - {id: 9lives, type: note, config: {text: x}}\n"
    )
    ws = make_ws(tmp_path, {"flows/a/flow.yaml": bad_charset})
    assert "E011" in codes(check_workspace(ws))

    duplicate = (
        "schema: napflow/v1\nflow: {name: t}\n"
        "nodes:\n"
        "  - {id: start, type: start}\n"
        "  - {id: start, type: end}\n"
    )
    ws2 = make_ws(tmp_path / "d", {"flows/a/flow.yaml": duplicate})
    assert "E011" in codes(check_workspace(ws2))


# --------------------------------------------------------------------------
# E003–E006, E012


def _flow(nodes: str, edges: str = "") -> str:
    text = f"schema: napflow/v1\nflow: {{name: t}}\nnodes:\n{nodes}"
    if edges:
        text += f"edges:\n{edges}"
    return text


def test_e003_unknown_node_and_port(tmp_path: Path) -> None:
    flow = _flow(
        "  - {id: start, type: start}\n  - {id: end, type: end}\n",
        "  - {from: start.out, to: ghost.in}\n  - {from: start.nope, to: end.out}\n",
    )
    ws = make_ws(tmp_path, {"flows/t/flow.yaml": flow})
    diags = [d for d in check_workspace(ws) if d.code == "E003"]
    messages = " | ".join(d.message for d in diags)
    assert "unknown node 'ghost'" in messages
    assert "unknown output port 'start.nope'" in messages
    assert all(d.line is not None for d in diags)


def test_e003_merge_growable_inputs(tmp_path: Path) -> None:
    flow = _flow(
        "  - {id: start, type: start}\n"
        "  - {id: m, type: merge, config: {mode: any}}\n"
        "  - {id: end, type: end, config: {ports: [{name: out}]}}\n",
        "  - {from: start.out, to: m.in1}\n"
        "  - {from: m.out, to: m.in99}\n"  # inN is fine (cycle → W101 too)
        "  - {from: m.out, to: end.out}\n",
    )
    ws = make_ws(tmp_path, {"flows/t/flow.yaml": flow})
    result = codes(check_workspace(ws))
    assert "E003" not in result

    bad = _flow(
        "  - {id: start, type: start}\n"
        "  - {id: m, type: merge, config: {mode: any}}\n"
        "  - {id: end, type: end}\n",
        "  - {from: start.out, to: m.in0}\n",  # in0 invalid — 1-based
    )
    ws2 = make_ws(tmp_path / "b", {"flows/t/flow.yaml": bad})
    assert "E003" in codes(check_workspace(ws2))


def test_e004_two_edges_one_input(tmp_path: Path) -> None:
    flow = _flow(
        "  - {id: start, type: start}\n"
        "  - {id: a, type: log, config: {}}\n"
        "  - {id: end, type: end}\n",
        "  - {from: start.out, to: a.in}\n  - {from: start.out, to: a.in}\n",
    )
    ws = make_ws(tmp_path, {"flows/t/flow.yaml": flow})
    diags = check_workspace(ws)
    assert "E004" in codes(diags)
    e004 = next(d for d in diags if d.code == "E004")
    assert "merge node" in e004.hint


def test_e005_required_input_and_required_end_port(tmp_path: Path) -> None:
    flow = _flow(
        "  - {id: start, type: start}\n"
        "  - {id: req, type: request, config: {url: x}}\n"  # trigger unwired
        "  - id: end\n"
        "    type: end\n"
        "    config: {ports: [{name: must}, {name: may, required: false}]}\n"
    )
    ws = make_ws(tmp_path, {"flows/t/flow.yaml": flow})
    e005 = [d for d in check_workspace(ws) if d.code == "E005"]
    targets = {
        d.message.split()[-4] for d in e005
    }  # "... req.trigger is not connected"
    assert {"req.trigger", "end.must"} <= targets
    assert not any("end.may" in d.message for d in e005)


def test_e006_cardinality(tmp_path: Path) -> None:
    flow = _flow(
        "  - {id: a, type: end}\n  - {id: b, type: end}\n"
    )  # no start, two ends
    ws = make_ws(tmp_path, {"flows/t/flow.yaml": flow})
    e006 = [d.message for d in check_workspace(ws) if d.code == "E006"]
    assert any("0 start" in m for m in e006)
    assert any("2 end" in m for m in e006)


def test_e012_reserved_error_port(tmp_path: Path) -> None:
    flow = _flow(
        "  - {id: start, type: start}\n"
        "  - {id: py, type: python, config: {function: f, outputs: [error]}}\n"
        "  - {id: end, type: end, config: {ports: [{name: error}]}}\n"
    )
    ws = make_ws(
        tmp_path,
        {"flows/t/flow.yaml": flow, "flows/t/nodes.py": "def f(x):\n    return {}\n"},
    )
    assert codes([d for d in check_workspace(ws) if d.code == "E012"]) == [
        "E012",
        "E012",
    ]


# --------------------------------------------------------------------------
# E007 / E008 (references + closure)


def test_e007_reference_cycle_with_path(tmp_path: Path) -> None:
    a = _flow(
        "  - {id: start, type: start}\n"
        "  - {id: use_b, type: flow, config: {flow: flows/b}}\n"
        "  - {id: end, type: end}\n"
    )
    b = _flow(
        "  - {id: start, type: start}\n"
        "  - {id: use_a, type: flow, config: {flow: flows/a}}\n"
        "  - {id: end, type: end}\n"
    )
    ws = make_ws(tmp_path, {"flows/a/flow.yaml": a, "flows/b/flow.yaml": b})
    e007 = [d for d in check_workspace(ws) if d.code == "E007"]
    assert len(e007) == 1
    assert "→" in e007[0].message  # cycle path

    self_ref = _flow(
        "  - {id: start, type: start}\n"
        "  - {id: me, type: flow, config: {flow: flows/s}}\n"
        "  - {id: end, type: end}\n"
    )
    ws2 = make_ws(tmp_path / "s", {"flows/s/flow.yaml": self_ref})
    assert "E007" in codes(check_workspace(ws2))


def test_e008_broken_references(tmp_path: Path) -> None:
    flow = _flow(
        "  - {id: start, type: start}\n"
        "  - {id: sub, type: flow, config: {flow: flows/ghost}}\n"
        '  - {id: dyn, type: flow, config: {flow: "flows/{{ x }}"}}\n'
        "  - {id: fix, type: fixture, config: {file: fixtures/nope.json}}\n"
        "  - {id: py, type: python, config: {function: missing}}\n"
        "  - {id: end, type: end}\n"
    )
    ws = make_ws(
        tmp_path,
        {"flows/t/flow.yaml": flow, "flows/t/nodes.py": "def present(x):\n    pass\n"},
    )
    e008 = [d.message for d in check_workspace(ws) if d.code == "E008"]
    assert len(e008) == 4
    assert any("flows/ghost" in m for m in e008)
    assert any("static path" in m for m in e008)
    assert any("fixtures/nope.json" in m for m in e008)
    assert any("'missing' not found" in m for m in e008)


def test_e008_rejects_lexical_workspace_boundary_paths(tmp_path: Path) -> None:
    flow = _flow(
        "  - {id: start, type: start}\n"
        "  - {id: sub, type: flow, config: {flow: ../outside}}\n"
        "  - {id: each, type: loop, config: {over: '[]', body: C:/outside}}\n"
        "  - {id: fix, type: fixture, config: {file: ../outside.json}}\n"
        "  - {id: end, type: end}\n"
    )
    ws = make_ws(tmp_path, {"flows/t/flow.yaml": flow})
    boundary = [
        d
        for d in check_workspace(ws)
        if d.code == "E008" and "workspace boundary" in d.message
    ]
    assert {d.node_id for d in boundary} == {"sub", "each", "fix"}


def test_e008_rejects_reference_and_fixture_symlink_escapes(tmp_path: Path) -> None:
    flow = _flow(
        "  - {id: start, type: start}\n"
        "  - {id: sub, type: flow, config: {flow: flows/escape}}\n"
        "  - {id: fix, type: fixture, config: {file: fixtures/escape.json}}\n"
        "  - {id: end, type: end}\n"
    )
    ws = make_ws(tmp_path, {"flows/t/flow.yaml": flow})
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    outside_flow = _flow("  - {id: start, type: start}\n  - {id: end, type: end}\n")
    (outside / "flow.yaml").write_text(outside_flow, encoding="utf-8")
    (outside / "data.json").write_text("[]", encoding="utf-8")
    (tmp_path / "fixtures").mkdir()
    try:
        (tmp_path / "flows" / "escape").symlink_to(outside, target_is_directory=True)
        (tmp_path / "fixtures" / "escape.json").symlink_to(outside / "data.json")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/privilege level")

    boundary = [
        d
        for d in check_workspace(ws)
        if d.code == "E008" and "workspace boundary" in d.message
    ]
    assert {d.node_id for d in boundary} == {"sub", "fix"}


def test_e008_rejects_python_source_alias_with_stable_prep_reason(
    tmp_path: Path,
) -> None:
    flow = _flow(
        "  - {id: start, type: start}\n"
        "  - {id: py, type: python, config: {function: present}}\n"
        "  - {id: end, type: end}\n"
    )
    ws = make_ws(
        tmp_path,
        {
            "flows/t/flow.yaml": flow,
            "flows/t/nodes.py": "def present():\n    return None\n",
        },
    )
    nodes = tmp_path / "flows" / "t" / "nodes.py"
    nodes.unlink()
    try:
        nodes.symlink_to(tmp_path / "flows" / "t" / "flow.yaml")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/privilege level")

    boundary = [
        diagnostic
        for diagnostic in check_workspace(ws)
        if diagnostic.code == "E008" and diagnostic.node_id == "py"
    ]
    assert len(boundary) == 1
    assert boundary[0].reason == "workspace_boundary"
    with pytest.raises(RunPrepError) as excinfo:
        prepare_run(ws, "flows/t")
    assert excinfo.value.reason == "workspace_boundary"


def test_e008_loop_body_needs_item_port(tmp_path: Path) -> None:
    body_no_item = _flow("  - {id: start, type: start}\n  - {id: end, type: end}\n")
    parent = _flow(
        "  - {id: start, type: start}\n"
        "  - id: each\n"
        "    type: loop\n"
        "    config: {over: trigger.value, body: flows/body}\n"
        "  - {id: end, type: end, config: {ports: [{name: out, required: false}]}}\n",
        "  - {from: start.out, to: each.trigger}\n"
        "  - {from: each.results, to: end.out}\n",
    )
    ws = make_ws(
        tmp_path,
        {"flows/p/flow.yaml": parent, "flows/body/flow.yaml": body_no_item},
    )
    assert any(d.code == "E008" and "`item`" in d.message for d in check_workspace(ws))


def test_closure_checks_referenced_flows_outside_root(tmp_path: Path) -> None:
    parent = _flow(
        "  - {id: start, type: start}\n"
        "  - {id: sub, type: flow, config: {flow: lib/helper}}\n"
        "  - {id: end, type: end}\n"
    )
    helper = _flow("  - {id: start, type: start}\n")  # no end → E006
    ws = make_ws(
        tmp_path,
        {"flows/p/flow.yaml": parent, "lib/helper/flow.yaml": helper},
    )
    e006 = [d for d in check_workspace(ws) if d.code == "E006"]
    # as_posix(): str(path) uses backslashes on Windows
    assert any("lib/helper" in d.path.as_posix() for d in e006)  # closure reached it


# --------------------------------------------------------------------------
# E009


def test_e009_template_and_expression_syntax(tmp_path: Path) -> None:
    flow = _flow(
        "  - {id: start, type: start}\n"
        '  - {id: req, type: request, config: {url: "{{ env.X"}}\n'
        '  - {id: c, type: condition, config: {expr: "a =="}}\n'
        "  - {id: end, type: end}\n"
    )
    ws = make_ws(tmp_path, {"flows/t/flow.yaml": flow})
    e009 = [d for d in check_workspace(ws) if d.code == "E009"]
    assert len(e009) == 2
    assert all(d.line is not None for d in e009)


# --------------------------------------------------------------------------
# python AST ports (FR-307)


def test_python_ports_from_ast(tmp_path: Path) -> None:
    nodes_py = "import os\ndef f(a, b=1, c=os.environ):\n    return {'out': a}\n"
    flow = _flow(
        "  - {id: start, type: start}\n"
        "  - {id: py, type: python, config: {function: f, outputs: [out]}}\n"
        "  - {id: end, type: end, config: {ports: [{name: r, required: false}]}}\n",
        "  - {from: start.out, to: py.a}\n"
        "  - {from: py.out, to: end.r}\n"
        "  - {from: py.error, to: end.r}\n",
    )
    ws = make_ws(tmp_path, {"flows/t/flow.yaml": flow, "flows/t/nodes.py": nodes_py})
    diags = check_workspace(ws)
    # `c` has a non-literal default → treated as required (EC36) → E005;
    # `b` has a literal default → optional, no E005; E004 on end.r is real
    e005 = [d.message for d in diags if d.code == "E005"]
    assert any("py.c" in m for m in e005)
    assert not any("py.b" in m for m in e005)


@pytest.mark.parametrize(
    ("nodes_py", "message"),
    [
        (
            "async def f(value):\n    return {'out': value}\n",
            "is async",
        ),
        (
            "def f(value, /):\n    return {'out': value}\n",
            "positional-only parameter(s): value",
        ),
    ],
)
def test_e008_rejects_worker_incompatible_callable_shapes(
    tmp_path: Path, nodes_py: str, message: str
) -> None:
    flow = _flow(
        "  - {id: start, type: start}\n"
        "  - {id: py, type: python, config: {function: f, outputs: [out]}}\n"
        "  - {id: end, type: end, config: {ports: [{name: r, required: false}]}}\n",
        "  - {from: start.out, to: py.value}\n  - {from: py.out, to: end.r}\n",
    )
    ws = make_ws(tmp_path, {"flows/t/flow.yaml": flow, "flows/t/nodes.py": nodes_py})

    violations = [
        diagnostic
        for diagnostic in check_workspace(ws)
        if diagnostic.code == "E008" and diagnostic.node_id == "py"
    ]

    assert len(violations) == 1
    assert message in violations[0].message
    assert violations[0].line is not None
    assert violations[0].column is not None


# --------------------------------------------------------------------------
# W101–W107


def test_w101_unguarded_vs_guarded_cycle(tmp_path: Path) -> None:
    def cycle_flow(guarded: bool) -> str:
        guard_node = (
            "  - {id: g, type: counter, config: {count: 3}}\n" if guarded else ""
        )
        retry_edge = (
            "  - {from: c.false, to: g.in}\n  - {from: g.continue, to: kick.in2}\n"
            if guarded
            else "  - {from: c.false, to: kick.in2}\n"
        )
        exhausted = "  - {from: g.exhausted, to: end.gave, }\n" if guarded else ""
        ports = (
            "{ports: [{name: done}, {name: gave, required: false}]}"
            if guarded
            else "{ports: [{name: done}]}"
        )
        return _flow(
            "  - {id: start, type: start}\n"
            "  - {id: kick, type: merge, config: {mode: any}}\n"
            '  - {id: c, type: condition, config: {expr: "true"}}\n'
            f"{guard_node}"
            f"  - id: end\n    type: end\n    config: {ports}\n",
            "  - {from: start.out, to: kick.in1}\n"
            "  - {from: kick.out, to: c.in}\n"
            "  - {from: c.true, to: end.done}\n" + retry_edge + exhausted,
        )

    ws = make_ws(tmp_path / "u", {"flows/t/flow.yaml": cycle_flow(False)})
    w101 = [d for d in check_workspace(ws) if d.code == "W101"]
    assert len(w101) == 1
    assert "→" in w101[0].message

    ws2 = make_ws(tmp_path / "g", {"flows/t/flow.yaml": cycle_flow(True)})
    assert "W101" not in codes(check_workspace(ws2))


def test_w102_port_type_mismatch_via_flow_node(tmp_path: Path) -> None:
    target = _flow(
        "  - id: start\n"
        "    type: start\n"
        "    config: {ports: [{name: n, type: number}]}\n"
        "  - {id: end, type: end, config: {ports: [{name: out, required: false}]}}\n"
    )
    parent = _flow(
        "  - {id: start, type: start}\n"
        "  - {id: sub, type: flow, config: {flow: flows/target}}\n"
        "  - {id: end, type: end, config: {ports: [{name: r, required: false}]}}\n",
        "  - {from: start.out, to: sub.n}\n"  # object → number
        "  - {from: sub.out, to: end.r}\n",
    )
    ws = make_ws(
        tmp_path,
        {"flows/p/flow.yaml": parent, "flows/target/flow.yaml": target},
    )
    assert any(
        d.code == "W102" and "object" in d.message and "number" in d.message
        for d in check_workspace(ws)
    )


def test_w103_w106_unconnected_outputs(tmp_path: Path) -> None:
    flow = _flow(
        "  - {id: start, type: start}\n"
        "  - {id: req, type: request, config: {url: x}}\n"
        "  - {id: g, type: counter, config: {count: 2}}\n"
        "  - {id: end, type: end, config: {ports: [{name: r, required: false}]}}\n",
        "  - {from: start.out, to: req.trigger}\n"
        "  - {from: req.response, to: g.in}\n"
        "  - {from: g.continue, to: end.r}\n",
    )
    ws = make_ws(tmp_path, {"flows/t/flow.yaml": flow})
    result = codes(check_workspace(ws))
    assert "W103" in result  # req.error unconnected
    assert "W106" in result  # g.exhausted unconnected


def test_w104_unreachable_node(tmp_path: Path) -> None:
    flow = _flow(
        "  - {id: start, type: start}\n"
        "  - {id: stranded, type: log, config: {}}\n"
        "  - {id: readme, type: note, config: {text: docs}}\n"
        "  - {id: source, type: fixture, config: {file: fixtures/a.json}}\n"
        "  - {id: end, type: end, config: {ports: [{name: r, required: false}]}}\n",
        "  - {from: source.value, to: end.r}\n",
    )
    ws = make_ws(tmp_path, {"flows/t/flow.yaml": flow, "fixtures/a.json": "[]"})
    w104 = [d for d in check_workspace(ws) if d.code == "W104"]
    # stranded (log, wired nowhere) — but NOT the note, NOT the
    # trigger-less fixture (it self-seeds), NOT end (fed by fixture)
    assert [d.node_id for d in w104] == ["stranded"]
    # E005: stranded.in is required and unwired — also expected
    assert any(
        d.code == "E005" and d.node_id == "stranded" for d in check_workspace(ws)
    )


def test_w105_env_key_in_no_profile(tmp_path: Path) -> None:
    flow = OK_FLOW  # requires BASE_URL
    ws = make_ws(
        tmp_path,
        {"flows/main/flow.yaml": flow, "envs/dev.env": "OTHER=1\n"},
    )
    w105 = [d for d in check_workspace(ws) if d.code == "W105"]
    assert len(w105) == 1
    assert "BASE_URL" in w105[0].message


def test_w105_unparseable_profile(tmp_path: Path) -> None:
    ws = make_ws(
        tmp_path,
        {"flows/main/flow.yaml": OK_FLOW, "envs/dev.env": "not a kv line\n"},
    )
    w105 = [d for d in check_workspace(ws) if d.code == "W105"]
    assert any("could not be parsed" in d.message for d in w105)


def test_w107_implicit_coercion_lint(tmp_path: Path) -> None:
    flow = _flow(
        "  - {id: start, type: start}\n"
        "  - {id: l, type: log, config: {label: yes}}\n"  # plain — 1.1 bool
        '  - {id: l2, type: log, config: {label: "yes"}}\n'  # quoted — fine
        "  - {id: s, type: set, config: {name: d, value: 2026-07-04}}\n"  # date obj
        "  - {id: end, type: end, config: {ports: [{name: r, required: false}]}}\n",
        "  - {from: start.out, to: l.in}\n"
        "  - {from: l.out, to: l2.in}\n"
        "  - {from: l2.out, to: s.in}\n"
        "  - {from: s.out, to: end.r}\n",
    )
    ws = make_ws(tmp_path, {"flows/t/flow.yaml": flow})
    w107 = [d for d in check_workspace(ws) if d.code == "W107"]
    assert len(w107) == 2
    assert {d.node_id for d in w107} == {"l", "s"}


def test_w109_missing_root_git_metadata_is_path_specific(tmp_path: Path) -> None:
    ws = make_ws(
        tmp_path,
        {"flows/main/flow.yaml": OK_FLOW, "envs/dev.env": DEV_ENV},
        git_metadata=False,
    )

    diagnostics = [d for d in check_workspace(ws) if d.code == "W109"]

    assert [d.path.name for d in diagnostics] == [".gitattributes", ".gitignore"]
    assert "*.yaml text eol=lf" in diagnostics[0].message
    assert "envs/*.env" in diagnostics[1].message
    assert all(d.severity == "warning" and d.node_id is None for d in diagnostics)
    assert all(d.hint and str(d.path) in d.render() for d in diagnostics)


def test_w109_lists_only_canonical_additions_and_check_is_read_only(
    tmp_path: Path,
) -> None:
    gitignore = "# owner\n.napflow/\n"
    ws = make_ws(
        tmp_path,
        {
            "flows/main/flow.yaml": OK_FLOW,
            "envs/dev.env": DEV_ENV,
            ".gitignore": gitignore,
        },
    )
    path = tmp_path / ".gitignore"
    before = path.read_bytes()

    diagnostic = next(d for d in check_workspace(ws) if d.path == path)

    assert diagnostic.code == "W109"
    assert "envs/*.env" in diagnostic.message
    assert "!envs/example.env" in diagnostic.message
    assert ".napflow/" not in diagnostic.message
    assert path.read_bytes() == before


def test_w109_non_lf_warns_even_when_rules_are_covered(tmp_path: Path) -> None:
    ws = make_ws(
        tmp_path,
        {"flows/main/flow.yaml": OK_FLOW, "envs/dev.env": DEV_ENV},
    )
    path = tmp_path / ".gitattributes"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))

    diagnostic = next(d for d in check_workspace(ws) if d.path == path)

    assert diagnostic.code == "W109"
    assert "CRLF or CR" in diagnostic.message
    assert "expected lines" not in diagnostic.message


def test_w109_invalid_utf8_warns_without_crashing(tmp_path: Path) -> None:
    ws = make_ws(
        tmp_path,
        {"flows/main/flow.yaml": OK_FLOW, "envs/dev.env": DEV_ENV},
    )
    path = tmp_path / ".gitignore"
    path.write_bytes(b"owner=\xff\n")

    diagnostic = next(d for d in check_workspace(ws) if d.path == path)

    assert diagnostic.code == "W109"
    assert "not valid UTF-8" in diagnostic.message


def test_w109_can_be_disabled_for_intentional_workspace_policy(
    tmp_path: Path,
) -> None:
    ws = make_ws(
        tmp_path,
        {"flows/main/flow.yaml": OK_FLOW, "envs/dev.env": DEV_ENV},
        git_metadata=False,
    )

    assert not any(
        d.code == "W109" for d in check_workspace(ws, check_git_metadata=False)
    )


# --------------------------------------------------------------------------
# Diagnostic quality (FR-309)


def test_every_diagnostic_has_hint_and_renders(tmp_path: Path) -> None:
    ws = make_ws(
        tmp_path,
        {
            "flows/t/flow.yaml": _flow(
                "  - {id: start, type: start}\n"
                "  - {id: req, type: request, config: {url: x}}\n"
            )
        },
    )
    diags = check_workspace(ws)
    assert diags  # E006 (no end), E005, W103, ...
    for d in diags:
        assert d.hint
        rendered = d.render()
        assert d.code in rendered and str(d.path) in rendered


def test_severity_split() -> None:
    from napflow.core.checker import CheckDiagnostic

    e = CheckDiagnostic(code="E003", message="m", path=Path("f"), hint="h")
    w = CheckDiagnostic(code="W101", message="m", path=Path("f"), hint="h")
    assert e.severity == "error" and w.severity == "warning"


# --------------------------------------------------------------------------
# Subflow UX data (S4/M6, FR-1007): ghost-wire refs + "used in N places"


def test_template_refs_templates_and_expr_fields(tmp_path: Path) -> None:
    # the retry-example shape: check_job's url reaches back to create's
    # output; is_ready's bare expr references check_job
    flow = _flow(
        "  - {id: start, type: start}\n"
        "  - {id: create, type: request, config: {url: 'http://x/job'}}\n"
        "  - id: check_job\n"
        "    type: request\n"
        "    config: {url: 'http://x/job/{{ nodes.create.response.body.id }}'}\n"
        "  - id: is_ready\n"
        "    type: condition\n"
        "    config: {expr: \"nodes.check_job.response.body.state == 'done'\"}\n"
        "  - {id: end, type: end, config: {ports: [{name: r, required: false}]}}\n",
        "  - {from: start.out, to: create.trigger}\n"
        "  - {from: create.response, to: check_job.trigger}\n"
        "  - {from: check_job.response, to: is_ready.in}\n"
        "  - {from: is_ready.true, to: end.r}\n",
    )
    ws = make_ws(tmp_path, {"flows/t/flow.yaml": flow})
    refs = template_refs(ws.load_flow("flows/t").model)
    assert refs == {"check_job": ["create"], "is_ready": ["check_job"]}


def test_template_refs_unknown_ids_dropped(tmp_path: Path) -> None:
    # `nodes.ghost` names no node in this flow — nothing to draw
    flow = _flow(
        "  - {id: start, type: start}\n"
        "  - {id: l, type: log, config: {label: '{{ nodes.ghost.out }}'}}\n"
        "  - {id: end, type: end, config: {ports: [{name: r, required: false}]}}\n",
        "  - {from: start.out, to: l.in}\n  - {from: l.out, to: end.r}\n",
    )
    ws = make_ws(tmp_path, {"flows/t/flow.yaml": flow})
    assert template_refs(ws.load_flow("flows/t").model) == {}


PARENT_FLOW = """\
schema: napflow/v1
flow: {name: parent}
nodes:
  - {id: start, type: start}
  - {id: sub, type: flow, config: {flow: flows/child}}
  - {id: again, type: flow, config: {flow: flows/child}}
  - id: each
    type: loop
    config: {body: flows/child, over: "[1, 2]"}
  - {id: end, type: end, config: {ports: [{name: r, required: false}]}}
edges:
  - {from: start.out, to: sub.val}
  - {from: sub.done, to: again.val}
  - {from: again.done, to: each.trigger}
  - {from: each.results, to: end.r}
"""

CHILD_FLOW = """\
schema: napflow/v1
flow: {name: child}
nodes:
  - id: start
    type: start
    config: {ports: [{name: val, default: 1}, {name: item, default: 0}]}
  - {id: end, type: end, config: {ports: [{name: done, required: false}]}}
edges:
  - {from: start.val, to: end.done}
"""


def test_used_by_counts_referencing_nodes(tmp_path: Path) -> None:
    ws = make_ws(
        tmp_path,
        {
            "flows/parent/flow.yaml": PARENT_FLOW,
            "flows/child/flow.yaml": CHILD_FLOW,
        },
    )
    # flow nodes AND loop bodies count; node ids name the exact places
    assert used_by(ws, "flows/child") == {"flows/parent": ["sub", "again", "each"]}
    assert used_by(ws, "flows/parent") == {}


def test_used_by_skips_unloadable_flows(tmp_path: Path) -> None:
    ws = make_ws(
        tmp_path,
        {
            "flows/child/flow.yaml": CHILD_FLOW,
            "flows/broken/flow.yaml": "nodes: [unclosed\n",
        },
    )
    assert used_by(ws, "flows/child") == {}
