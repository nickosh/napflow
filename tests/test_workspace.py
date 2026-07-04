"""FR-101 (walk-up) / FR-102 (flow discovery) / FR-103 (env profiles +
dialect, EC36)."""

from pathlib import Path

import pytest

from napflow.core.workspace import (
    EnvFileError,
    WorkspaceNotFoundError,
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


def test_discover_flows_missing_root_dir(tmp_path: Path) -> None:
    root = _make_workspace(tmp_path / "ws")
    assert load_workspace(root).discover_flows() == []


def test_load_flow_by_identity(tmp_path: Path) -> None:
    root = _make_workspace(tmp_path / "ws")
    _add_flow(root, "flows/main")
    loaded = load_workspace(root).load_flow("flows/main")
    assert loaded.model.flow.name == "t"


# --------------------------------------------------------------------------
# Env profiles (FR-103)


def test_env_profiles_discovered_by_stem(tmp_path: Path) -> None:
    root = _make_workspace(tmp_path / "ws")
    envs = root / "envs"
    envs.mkdir()
    (envs / "dev.env").write_text("A=1\n", encoding="utf-8")
    (envs / "staging.env").write_text("A=2\n", encoding="utf-8")
    (envs / "example.env").write_text("A=\n", encoding="utf-8")
    (envs / "notes.txt").write_text("ignored", encoding="utf-8")

    profiles = load_workspace(root).env_profiles()
    assert list(profiles) == ["dev", "example", "staging"]  # sorted
    assert profiles["dev"] == envs / "dev.env"


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
