"""FR-801/802/805 + FR-107: the S1 CLI surface."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from napflow.cli.main import app
from napflow.core.checker import check_workspace
from napflow.core.workspace import load_workspace

runner = CliRunner()

SCAFFOLD_FILES = [
    "napflow.yaml",
    "flows/main/flow.yaml",
    "flows/main/nodes.py",
    "flows/example/flow.yaml",
    "flows/example/nodes.py",
    "flows/smoke/flow.yaml",
    "flows/smoke/nodes.py",
    "fixtures/smoke.json",
    "envs/dev.env",
    "envs/example.env",
    ".gitignore",
    ".gitattributes",
]


@pytest.fixture
def scaffolded(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    result = runner.invoke(app, ["init", str(ws)])
    assert result.exit_code == 0, result.output
    return ws


# --------------------------------------------------------------------------
# napf init (FR-805 / FR-107)


def test_init_creates_the_fr107_scaffold(scaffolded: Path) -> None:
    for rel in SCAFFOLD_FILES:
        assert (scaffolded / rel).is_file(), rel
    assert (scaffolded / ".napflow").is_dir()


def test_scaffolded_workspace_checks_clean(scaffolded: Path) -> None:
    # the money test: fresh workspace → zero errors, zero warnings
    assert check_workspace(load_workspace(scaffolded)) == []


def test_scaffold_secret_patterns_are_opt_in(scaffolded: Path) -> None:
    workspace = load_workspace(scaffolded)
    text = (scaffolded / "napflow.yaml").read_text(encoding="utf-8")

    assert workspace.manifest.model.environments.secrets == []
    assert "secrets: []" in text
    assert '# for example: ["API_TOKEN", "*_PASSWORD"].' in text
    assert "# Raw local history and the local UI remain unmasked." in text


def test_scaffold_is_canonical_output(scaffolded: Path) -> None:
    text = (scaffolded / "flows" / "smoke" / "flow.yaml").read_bytes().decode()
    assert 'schema: "napflow/v1"' in text  # strings force-quoted
    assert '- {from: "users.value", to: "summarize.users"}' in text  # edge island
    assert "\r" not in text and text.endswith("\n")


def test_scaffold_gitignore_keeps_example_env(scaffolded: Path) -> None:
    gitignore = (scaffolded / ".gitignore").read_text(encoding="utf-8")
    assert "envs/*.env" in gitignore
    assert "!envs/example.env" in gitignore
    attrs = (scaffolded / ".gitattributes").read_text(encoding="utf-8")
    assert "*.yaml text eol=lf" in attrs


def test_init_refuses_existing_workspace(scaffolded: Path) -> None:
    result = runner.invoke(app, ["init", str(scaffolded)])
    assert result.exit_code == 2
    assert "already exists" in result.output


# --------------------------------------------------------------------------
# napf list (FR-802)


def test_list_shows_flows_and_ports(
    scaffolded: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(scaffolded)
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    lines = result.output.strip().splitlines()
    assert len(lines) == 3  # example, main, smoke — sorted
    assert lines[0].startswith("flows/example")
    assert "base_url(string)?" in lines[0]  # start port with default
    assert "failed_check?" in lines[0]  # optional end port
    assert lines[1].startswith("flows/main")
    assert "done" in lines[1]


def test_list_marks_invalid_flow(
    scaffolded: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = scaffolded / "flows" / "broken" / "flow.yaml"
    bad.parent.mkdir(parents=True)
    bad.write_text("nodes: [unclosed\n", encoding="utf-8")
    monkeypatch.chdir(scaffolded)
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0  # list is informational
    assert "flows/broken  !! invalid" in result.output


def test_list_without_workspace_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 2


# --------------------------------------------------------------------------
# napf check (FR-801)


def test_check_clean_exits_0(scaffolded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(scaffolded)
    result = runner.invoke(app, ["check"])
    assert result.exit_code == 0
    assert "3 flows: 0 errors, 0 warnings" in result.output


def test_check_errors_exit_1(scaffolded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = scaffolded / "flows" / "broken" / "flow.yaml"
    bad.parent.mkdir(parents=True)
    bad.write_text(
        "schema: napflow/v1\nflow: {name: b}\n"
        "nodes:\n  - {id: start, type: start}\n",  # no end → E006
        encoding="utf-8",
    )
    monkeypatch.chdir(scaffolded)
    result = runner.invoke(app, ["check"])
    assert result.exit_code == 1
    assert "E006" in result.output


def test_check_warnings_only_exit_0(
    scaffolded: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    warn = scaffolded / "flows" / "warny" / "flow.yaml"
    warn.parent.mkdir(parents=True)
    warn.write_text(
        "schema: napflow/v1\nflow: {name: w}\n"
        "nodes:\n"
        "  - {id: start, type: start}\n"
        "  - {id: req, type: request, config: {url: x}}\n"
        "  - {id: end, type: end, config: {ports: [{name: r, required: false}]}}\n"
        "edges:\n"
        "  - {from: start.out, to: req.trigger}\n"
        "  - {from: req.response, to: end.r}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(scaffolded)
    result = runner.invoke(app, ["check"])
    assert result.exit_code == 0  # W103 (req.error unconnected) is not fatal
    assert "W103" in result.output
    assert "1 warning" in result.output


# --------------------------------------------------------------------------
# meta


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "napflow" in result.output
