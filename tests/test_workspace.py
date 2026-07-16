"""FR-101 (walk-up) / FR-102 (flow discovery) / FR-103 (env profiles +
dialect, EC36)."""

from pathlib import Path

import pytest

import napflow.core.workspace as workspace_module
from napflow.core.workspace import (
    EnvFileError,
    WorkspaceBoundaryError,
    WorkspaceNotFoundError,
    WorkspaceResolver,
    find_manifest,
    load_workspace,
    parse_env_file,
)

MINIMAL_FLOW = (
    "schema: napflow/v1\n"
    "flow: {name: t}\n"
    "nodes:\n"
    "  - {id: start, type: start}\n"
    "  - {id: end, type: end}\n"
)


def _make_workspace(root: Path, manifest: str = "schema: napflow/v1\n") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "napflow.yaml").write_text(manifest, encoding="utf-8")
    return root


def _add_flow(root: Path, identity: str) -> None:
    directory = root / Path(identity)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "flow.yaml").write_text(MINIMAL_FLOW, encoding="utf-8")


# --------------------------------------------------------------------------
# Walk-up (FR-101)


def test_find_manifest_walks_up(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path / "ws")
    deep = ws / "flows" / "payments" / "refund"
    deep.mkdir(parents=True)
    assert find_manifest(deep) == ws / "napflow.yaml"
    assert find_manifest(ws) == ws / "napflow.yaml"


def test_find_manifest_stops_at_nearest(tmp_path: Path) -> None:
    outer = _make_workspace(tmp_path / "outer")
    inner = _make_workspace(outer / "inner")
    assert find_manifest(inner) == inner / "napflow.yaml"
    assert find_manifest(outer) == outer / "napflow.yaml"


def test_load_workspace_not_found(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceNotFoundError):
        load_workspace(tmp_path)


def test_load_workspace_from_nested_dir(tmp_path: Path) -> None:
    root = _make_workspace(tmp_path / "ws")
    _add_flow(root, "flows/main")
    ws = load_workspace(root / "flows" / "main")
    assert ws.root == root.resolve()
    assert ws.manifest.model.flows.root == "flows"


# --------------------------------------------------------------------------
# Flow discovery (FR-102)


def test_discover_flows_recursive_sorted(tmp_path: Path) -> None:
    root = _make_workspace(tmp_path / "ws")
    _add_flow(root, "flows/main")
    _add_flow(root, "flows/payments/refund")
    _add_flow(root, "flows/main/inner")  # nesting inside a flow dir is free
    (root / "flows" / "payments").mkdir(exist_ok=True)  # grouping dir, no flow.yaml
    (root / "flows" / "README.md").write_text("not a flow", encoding="utf-8")

    ws = load_workspace(root)
    identities = [ref.identity for ref in ws.discover_flows()]
    assert identities == ["flows/main", "flows/main/inner", "flows/payments/refund"]


def test_discover_flows_custom_root(tmp_path: Path) -> None:
    manifest = "schema: napflow/v1\nflows: {root: pipelines, main: pipelines/main}\n"
    root = _make_workspace(tmp_path / "ws", manifest)
    _add_flow(root, "pipelines/main")
    _add_flow(root, "flows/stray")  # outside flows.root — not discovered

    identities = [ref.identity for ref in load_workspace(root).discover_flows()]
    assert identities == ["pipelines/main"]


def test_discovery_preserves_configured_identity_through_internal_symlink(
    tmp_path: Path,
) -> None:
    root = _make_workspace(tmp_path / "ws")
    _add_flow(root, "catalog/main")
    try:
        (root / "flows").symlink_to(root / "catalog", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/privilege level")

    workspace = load_workspace(root)
    assert [ref.identity for ref in workspace.discover_flows()] == ["flows/main"]
    assert workspace.resolver.flow_file("flows/main") == root / "catalog/main/flow.yaml"
    assert workspace.resolver.clone_destination("flows/copy") == root / "catalog/copy"
    with pytest.raises(WorkspaceBoundaryError):
        workspace.resolver.clone_destination("catalog/copy")


def test_discover_flows_missing_root_dir(tmp_path: Path) -> None:
    root = _make_workspace(tmp_path / "ws")
    assert load_workspace(root).discover_flows() == []


def test_load_flow_by_identity(tmp_path: Path) -> None:
    root = _make_workspace(tmp_path / "ws")
    _add_flow(root, "flows/main")
    loaded = load_workspace(root).load_flow("flows/main")
    assert loaded.model.flow.name == "t"


# --------------------------------------------------------------------------
# Central workspace boundary (FR-1107 / D37)


def test_resolver_identity_grammar_is_platform_independent(tmp_path: Path) -> None:
    root = _make_workspace(tmp_path / "ws")
    resolver = WorkspaceResolver(root)

    assert resolver.normalize_identity("flows/nested name/#100%?done") == (
        "flows/nested name/#100%?done"
    )
    for bad in (
        "",
        "/absolute",
        "flows//empty",
        "flows/./dot",
        "flows/../parent",
        "C:/outside",
        "flows/C:/outside",
        r"C:\outside",
        r"\\server\share",
        "flows/control\x00name",
        "flows/control\x7fname",
        "flows/\ud800",
    ):
        with pytest.raises(WorkspaceBoundaryError) as excinfo:
            resolver.normalize_identity(bad)
        assert excinfo.value.reason == "workspace_boundary"


def test_resolver_rejects_parent_and_final_symlink_escapes(tmp_path: Path) -> None:
    root = _make_workspace(tmp_path / "ws")
    (root / "flows").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "flow.yaml").write_text(MINIMAL_FLOW, encoding="utf-8")
    resolver = WorkspaceResolver(root)

    try:
        (root / "flows" / "parent_escape").symlink_to(outside, target_is_directory=True)
        final = root / "flows" / "final_escape"
        final.mkdir()
        (final / "flow.yaml").symlink_to(outside / "flow.yaml")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/privilege level")

    with pytest.raises(WorkspaceBoundaryError):
        resolver.flow_file("flows/parent_escape")
    with pytest.raises(WorkspaceBoundaryError):
        resolver.flow_file("flows/final_escape")


def test_resolver_allows_symlink_targets_that_remain_inside(tmp_path: Path) -> None:
    root = _make_workspace(tmp_path / "ws")
    _add_flow(root, "flows/real")
    try:
        (root / "flows" / "alias").symlink_to(
            root / "flows" / "real", target_is_directory=True
        )
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/privilege level")

    resolver = WorkspaceResolver(root)
    assert resolver.flow_file("flows/alias") == (root / "flows/real/flow.yaml")


def test_resolver_rejects_flow_file_symlink_to_another_flow(tmp_path: Path) -> None:
    root = _make_workspace(tmp_path / "ws")
    _add_flow(root, "flows/one")
    _add_flow(root, "flows/two")
    (root / "flows" / "one" / "flow.yaml").unlink()
    try:
        (root / "flows" / "one" / "flow.yaml").symlink_to(
            root / "flows" / "two" / "flow.yaml"
        )
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/privilege level")

    with pytest.raises(WorkspaceBoundaryError):
        WorkspaceResolver(root).flow_file("flows/one")


@pytest.mark.parametrize(
    ("source_name", "target_name"),
    [("nodes.py", "flow.yaml"), ("flow.yaml", "nodes.py")],
)
def test_resolver_rejects_flow_source_symlink_to_sibling(
    tmp_path: Path, source_name: str, target_name: str
) -> None:
    root = _make_workspace(tmp_path / "ws")
    _add_flow(root, "flows/one")
    flow_dir = root / "flows" / "one"
    (flow_dir / "nodes.py").write_text("def one():\n    return 1\n", encoding="utf-8")
    (flow_dir / source_name).unlink()
    try:
        (flow_dir / source_name).symlink_to(flow_dir / target_name)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/privilege level")

    resolver = WorkspaceResolver(root)
    with pytest.raises(WorkspaceBoundaryError):
        resolver.source_file("flows/one", source_name)


def test_resolver_validates_run_ids_and_history_symlinks(tmp_path: Path) -> None:
    root = _make_workspace(tmp_path / "ws")
    resolver = WorkspaceResolver(root)
    run_id = "20260712-120000-abcdef"
    assert resolver.run_log("flows/a", run_id) == (
        root / ".napflow/runs/flows/a" / f"{run_id}.jsonl"
    )
    for bad in ("nope", "20260712-120000-ABCDEF", "../escape", f"{run_id}/x"):
        with pytest.raises(WorkspaceBoundaryError):
            resolver.validate_run_id(bad)

    outside = tmp_path / "outside-runs"
    outside.mkdir()
    runs_parent = root / ".napflow"
    runs_parent.mkdir()
    try:
        (runs_parent / "runs").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/privilege level")
    with pytest.raises(WorkspaceBoundaryError):
        resolver.run_log("flows/a", run_id)


def test_resolver_rejects_final_history_symlink_outside_history_root(
    tmp_path: Path,
) -> None:
    root = _make_workspace(tmp_path / "ws")
    resolver = WorkspaceResolver(root)
    run_id = "20260712-120000-abcdef"
    runs = root / ".napflow" / "runs" / "flows" / "a"
    runs.mkdir(parents=True)
    target = root / "not-history.jsonl"
    target.write_text("must not be replayed", encoding="utf-8")
    try:
        (runs / f"{run_id}.jsonl").symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/privilege level")

    with pytest.raises(WorkspaceBoundaryError):
        resolver.run_log("flows/a", run_id)


def test_resolver_rejects_final_history_alias_to_another_flow(tmp_path: Path) -> None:
    root = _make_workspace(tmp_path / "ws")
    resolver = WorkspaceResolver(root)
    run_id = "20260712-120000-abcdef"
    source_runs = root / ".napflow" / "runs" / "flows" / "a"
    target_runs = root / ".napflow" / "runs" / "flows" / "b"
    source_runs.mkdir(parents=True)
    target_runs.mkdir(parents=True)
    target = target_runs / f"{run_id}.jsonl"
    target.write_text("must not be replayed as another flow", encoding="utf-8")
    try:
        (source_runs / f"{run_id}.jsonl").symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/privilege level")

    with pytest.raises(WorkspaceBoundaryError):
        resolver.run_log("flows/a", run_id)


def test_resolver_clone_destination_must_resolve_under_flows_root(
    tmp_path: Path,
) -> None:
    root = _make_workspace(tmp_path / "ws")
    (root / "flows").mkdir()
    resolver = WorkspaceResolver(root)
    assert resolver.clone_destination("flows/copy") == root / "flows/copy"
    with pytest.raises(WorkspaceBoundaryError):
        resolver.clone_destination("elsewhere/copy")

    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (root / "flows" / "linked").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/privilege level")
    with pytest.raises(WorkspaceBoundaryError):
        resolver.clone_destination("flows/linked/copy")


@pytest.mark.parametrize("flows_root", [".", "./"])
def test_flows_root_must_be_a_proper_subdirectory(
    tmp_path: Path, flows_root: str
) -> None:
    root = _make_workspace(tmp_path / "ws")

    with pytest.raises(WorkspaceBoundaryError, match="proper subdirectory"):
        WorkspaceResolver(root, flows_root_identity=flows_root)


def test_flows_root_cannot_symlink_back_to_workspace_root(tmp_path: Path) -> None:
    root = _make_workspace(tmp_path / "ws")
    try:
        (root / "flows").symlink_to(root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/privilege level")

    with pytest.raises(WorkspaceBoundaryError, match="proper subdirectory"):
        WorkspaceResolver(root)


def test_data_root_cannot_symlink_back_to_workspace_root(tmp_path: Path) -> None:
    root = _make_workspace(tmp_path / "ws")
    try:
        (root / "data").symlink_to(root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/privilege level")

    with pytest.raises(WorkspaceBoundaryError, match="proper subdirectory"):
        WorkspaceResolver(root)


@pytest.mark.parametrize(
    "bad",
    [
        "/absolute",
        "../parent",
        "nested/../parent",
        "nested//empty",
        "./nested",
        r"nested\windows",
        "C:/outside",
    ],
)
@pytest.mark.parametrize("field", ["environments_root_identity", "data_root_identity"])
def test_configurable_data_roots_reject_unsafe_identities(
    tmp_path: Path, bad: str, field: str
) -> None:
    root = _make_workspace(tmp_path / "ws")

    with pytest.raises(WorkspaceBoundaryError):
        WorkspaceResolver(root, **{field: bad})


@pytest.mark.parametrize("workspace_root_spelling", [".", "./"])
def test_environment_root_accepts_workspace_root_spellings(
    tmp_path: Path, workspace_root_spelling: str
) -> None:
    root = _make_workspace(tmp_path / "ws")
    resolver = WorkspaceResolver(
        root,
        environments_root_identity=workspace_root_spelling,
    )

    assert resolver.environments_root_identity == "."
    assert resolver.environments_root == root.resolve()


@pytest.mark.parametrize("data_root", [".", "./"])
def test_data_root_must_be_a_proper_subdirectory(
    tmp_path: Path, data_root: str
) -> None:
    root = _make_workspace(tmp_path / "ws")

    with pytest.raises(WorkspaceBoundaryError, match="proper subdirectory"):
        WorkspaceResolver(root, data_root_identity=data_root)


def test_workspace_threads_nested_configured_roots_to_resolver(tmp_path: Path) -> None:
    root = _make_workspace(
        tmp_path / "ws",
        "schema: napflow/v1\n"
        "flows: {root: qa/flows, main: qa/flows/main}\n"
        "environments: {root: config/env}\n"
        "data: {root: tests/data}\n",
    )
    workspace = load_workspace(root)

    assert workspace.flows_root == root / "qa/flows"
    assert workspace.environments_root == root / "config/env"
    assert workspace.data_root == root / "tests/data"


def test_configured_flows_root_derives_main_and_rejects_main_outside_it(
    tmp_path: Path,
) -> None:
    root = _make_workspace(
        tmp_path / "derived-main",
        "schema: napflow/v1\nflows: {root: qa/flows}\n",
    )

    workspace = load_workspace(root)

    assert workspace.manifest.model.flows.main == "qa/flows/main"

    (root / "napflow.yaml").write_text(
        "schema: napflow/v1\nflows: {root: qa/flows, main: other/main}\n",
        encoding="utf-8",
    )
    with pytest.raises(WorkspaceBoundaryError, match="flows.main.*flows.root"):
        load_workspace(root)


def test_configured_main_cannot_symlink_outside_flows_root(tmp_path: Path) -> None:
    root = _make_workspace(
        tmp_path / "linked-main",
        "schema: napflow/v1\nflows: {root: qa/flows}\n",
    )
    flows = root / "qa/flows"
    flows.mkdir(parents=True)
    outside = root / "other-main"
    outside.mkdir()
    try:
        (flows / "main").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/privilege level")

    with pytest.raises(WorkspaceBoundaryError, match="flows.main.*flows.root"):
        load_workspace(root)


@pytest.mark.parametrize(
    ("field", "link_name"),
    [
        ("environments_root_identity", "linked-env"),
        ("data_root_identity", "linked-data"),
    ],
)
def test_configured_data_roots_reject_symlink_escapes(
    tmp_path: Path, field: str, link_name: str
) -> None:
    root = _make_workspace(tmp_path / "ws")
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (root / link_name).symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/privilege level")

    with pytest.raises(WorkspaceBoundaryError):
        WorkspaceResolver(root, **{field: link_name})


def test_fixture_paths_are_relative_to_configured_data_root(tmp_path: Path) -> None:
    root = _make_workspace(tmp_path / "ws")
    resolver = WorkspaceResolver(root, data_root_identity="tests/data")

    assert resolver.fixture_file("users.json") == root / "tests/data/users.json"
    assert resolver.fixture_file("nested/users.csv") == (
        root / "tests/data/nested/users.csv"
    )
    with pytest.raises(WorkspaceBoundaryError):
        resolver.fixture_file("../users.json")


def test_default_data_root_resolves_fixture_paths_below_data_directory(
    tmp_path: Path,
) -> None:
    root = _make_workspace(tmp_path / "ws")

    assert WorkspaceResolver(root).fixture_file("users.json") == (
        root / "data/users.json"
    )


def test_fixture_path_cannot_symlink_outside_configured_data_root(
    tmp_path: Path,
) -> None:
    root = _make_workspace(tmp_path / "ws")
    data = root / "data"
    data.mkdir()
    outside_fixture = root / "elsewhere.json"
    outside_fixture.write_text("[]", encoding="utf-8")
    try:
        (data / "escape.json").symlink_to(outside_fixture)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/privilege level")

    resolver = WorkspaceResolver(root)
    with pytest.raises(WorkspaceBoundaryError):
        resolver.fixture_file("escape.json")


# --------------------------------------------------------------------------
# Env profiles (FR-103)


def test_env_profiles_use_literal_filenames_for_all_supported_patterns(
    tmp_path: Path,
) -> None:
    root = _make_workspace(
        tmp_path / "ws",
        "schema: napflow/v1\nenvironments: {root: envs}\n",
    )
    envs = root / "envs"
    envs.mkdir()
    (envs / ".env").write_text("A=root\n", encoding="utf-8")
    (envs / ".env.staging").write_text("A=staging\n", encoding="utf-8")
    (envs / "dev.env").write_text("A=1\n", encoding="utf-8")
    (envs / "UPPER.ENV").write_text("A=ignored\n", encoding="utf-8")
    (envs / "notes.txt").write_text("ignored", encoding="utf-8")
    nested = envs / "nested"
    nested.mkdir()
    (nested / "hidden.env").write_text("A=hidden\n", encoding="utf-8")

    workspace = load_workspace(root)
    discovery = workspace.discover_env_profiles()

    assert list(discovery.profiles) == [".env", ".env.staging", "dev.env"]
    assert discovery.profiles[".env"].values == {"A": "root"}
    assert discovery.profiles["dev.env"].path == envs / "dev.env"
    assert discovery.issues == {}
    assert workspace.env_profiles() == {
        ".env": envs / ".env",
        ".env.staging": envs / ".env.staging",
        "dev.env": envs / "dev.env",
    }


@pytest.mark.parametrize("root_spelling", [".", "./"])
def test_env_profiles_can_be_discovered_from_workspace_root(
    tmp_path: Path, root_spelling: str
) -> None:
    root = _make_workspace(
        tmp_path / "ws",
        f'schema: napflow/v1\nenvironments: {{root: "{root_spelling}"}}\n',
    )
    (root / ".env").write_text("ROOT=yes\n", encoding="utf-8")

    workspace = load_workspace(root)
    assert workspace.env_profiles() == {".env": root / ".env"}


def test_env_discovery_retains_invalid_nonregular_and_unreadable_issues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_workspace(
        tmp_path / "ws",
        "schema: napflow/v1\nenvironments: {root: envs}\n",
    )
    envs = root / "envs"
    envs.mkdir()
    (envs / "good.env").write_text("GOOD=yes\n", encoding="utf-8")
    (envs / "bad.env").write_text("not a key-value line\n", encoding="utf-8")
    (envs / ".env.binary").write_bytes(b"VALUE=\xff\n")
    (envs / "directory.env").mkdir()
    (envs / "unreadable.env").write_text("VALUE=x\n", encoding="utf-8")
    real_parse = workspace_module.parse_env_file

    def parse_or_refuse(path: Path) -> dict[str, str]:
        if path.name == "unreadable.env":
            raise PermissionError("owner denied read")
        return real_parse(path)

    monkeypatch.setattr(workspace_module, "parse_env_file", parse_or_refuse)

    discovery = load_workspace(root).discover_env_profiles()

    assert list(discovery.profiles) == ["good.env"]
    assert discovery.profiles["good.env"].values == {"GOOD": "yes"}
    assert {name: issue.reason for name, issue in discovery.issues.items()} == {
        ".env.binary": "invalid_encoding",
        "bad.env": "invalid_format",
        "directory.env": "not_regular",
        "unreadable.env": "unreadable",
    }
    assert "owner denied read" in discovery.issues["unreadable.env"].message


def test_env_discovery_retains_escaping_candidate_as_boundary_issue(
    tmp_path: Path,
) -> None:
    root = _make_workspace(
        tmp_path / "ws",
        "schema: napflow/v1\nenvironments: {root: envs}\n",
    )
    envs = root / "envs"
    envs.mkdir()
    outside = tmp_path / "outside.env"
    outside.write_text("SECRET=outside\n", encoding="utf-8")
    try:
        (envs / "escape.env").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/privilege level")

    discovery = load_workspace(root).discover_env_profiles()

    assert discovery.profiles == {}
    assert discovery.issues["escape.env"].reason == "workspace_boundary"
    assert discovery.issues["escape.env"].path == envs / "escape.env"


def test_env_discovery_allows_contained_regular_file_alias(tmp_path: Path) -> None:
    root = _make_workspace(
        tmp_path / "ws",
        "schema: napflow/v1\nenvironments: {root: envs}\n",
    )
    envs = root / "envs"
    envs.mkdir()
    target = envs / "shared-profile"
    target.write_text("SHARED=yes\n", encoding="utf-8")
    try:
        (envs / "alias.env").symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/privilege level")

    discovery = load_workspace(root).discover_env_profiles()

    assert discovery.issues == {}
    assert discovery.profiles["alias.env"].path == target
    assert discovery.profiles["alias.env"].values == {"SHARED": "yes"}


def test_env_profiles_no_envs_dir(tmp_path: Path) -> None:
    root = _make_workspace(tmp_path / "ws")
    assert load_workspace(root).env_profiles() == {}


# --------------------------------------------------------------------------
# .env dialect (EC36)


def test_dialect_happy_path(tmp_path: Path) -> None:
    env = tmp_path / "dev.env"
    env.write_text(
        "# full-line comment\n"
        "\n"
        "BASE_URL=https://httpbin.org\n"
        "API_TOKEN='quoted value'\n"
        'GREETING="hello world"\n'
        "EQUALS_INSIDE=a=b=c\n"
        "NO_INTERPOLATION=$HOME/${X}\n"
        "EMPTY=\n"
        "SPACED = padded \n"
        "DUPLICATE=first\n"
        "DUPLICATE=last\n",
        encoding="utf-8",
    )
    assert parse_env_file(env) == {
        "BASE_URL": "https://httpbin.org",
        "API_TOKEN": "quoted value",
        "GREETING": "hello world",
        "EQUALS_INSIDE": "a=b=c",
        "NO_INTERPOLATION": "$HOME/${X}",
        "EMPTY": "",
        "SPACED": "padded",
        "DUPLICATE": "last",  # last wins
    }


def test_dialect_quotes_stripped_once_only_when_matching(tmp_path: Path) -> None:
    env = tmp_path / "q.env"
    env.write_text(
        "NESTED=\"'inner'\"\n"  # outer pair stripped once
        "MISMATCHED=\"oops'\n"  # no matching pair — literal
        "LONE='\n",  # single char — literal
        encoding="utf-8",
    )
    assert parse_env_file(env) == {
        "NESTED": "'inner'",
        "MISMATCHED": "\"oops'",
        "LONE": "'",
    }


def test_dialect_rejects_line_without_equals(tmp_path: Path) -> None:
    env = tmp_path / "bad.env"
    env.write_text("A=1\njust some words\n", encoding="utf-8")
    with pytest.raises(EnvFileError, match=r"bad\.env:2: expected KEY=VALUE"):
        parse_env_file(env)


def test_dialect_rejects_export_prefix(tmp_path: Path) -> None:
    env = tmp_path / "bad.env"
    env.write_text("export A=1\n", encoding="utf-8")
    with pytest.raises(EnvFileError, match="no `export` prefix"):
        parse_env_file(env)


def test_dialect_rejects_invalid_key(tmp_path: Path) -> None:
    env = tmp_path / "bad.env"
    env.write_text("MY-KEY=1\n", encoding="utf-8")
    with pytest.raises(EnvFileError, match="invalid key"):
        parse_env_file(env)


def test_dialect_tolerates_crlf(tmp_path: Path) -> None:
    env = tmp_path / "win.env"
    env.write_bytes(b"A=1\r\nB=two\r\n")
    assert parse_env_file(env) == {"A": "1", "B": "two"}
