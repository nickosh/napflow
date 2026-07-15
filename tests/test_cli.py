"""FR-801/802/805 + FR-107: the S1 CLI surface."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

import napflow.cli.main as cli_main
import napflow.cli.scaffold as cli_scaffold
from napflow.cli.main import app
from napflow.cli.scaffold import scaffold_workspace
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


@pytest.mark.parametrize("answer", ["y\n", "\n"])
def test_init_interactive_accept_and_default_append_missing_rules(
    answer: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "brownfield"
    ws.mkdir()
    gitignore = ws / ".gitignore"
    gitignore.write_text("# owner\n.napflow/\n", encoding="utf-8", newline="")
    monkeypatch.setattr(cli_main, "_stdin_is_tty", lambda: True)

    result = runner.invoke(app, ["init", str(ws)], input=answer)

    assert result.exit_code == 0, result.output
    assert ".gitignore is missing this napflow block" in result.output
    assert "# napflow\nenvs/*.env\n!envs/example.env" in result.output
    assert "appended .gitignore" in result.output
    assert "WARNING:" not in result.output
    assert gitignore.read_text(encoding="utf-8").endswith(
        "# napflow\nenvs/*.env\n!envs/example.env\n"
    )


def test_init_interactive_decline_skips_and_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "brownfield"
    ws.mkdir()
    gitignore = ws / ".gitignore"
    original = b"# owner\n"
    gitignore.write_bytes(original)
    monkeypatch.setattr(cli_main, "_stdin_is_tty", lambda: True)

    result = runner.invoke(app, ["init", str(ws)], input="n\n")

    assert result.exit_code == 0, result.output
    assert "skipped  .gitignore" in result.output
    assert "WARNING: .gitignore napflow rules were not added." in result.output
    assert "envs/*.env" in result.output
    assert gitignore.read_bytes() == original


def test_init_prompts_for_each_existing_metadata_file_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "brownfield"
    ws.mkdir()
    gitignore = ws / ".gitignore"
    attributes = ws / ".gitattributes"
    gitignore_original = b"# owner ignore\n"
    attributes_original = b"# owner attributes\n"
    gitignore.write_bytes(gitignore_original)
    attributes.write_bytes(attributes_original)
    monkeypatch.setattr(cli_main, "_stdin_is_tty", lambda: True)

    result = runner.invoke(app, ["init", str(ws)], input="n\ny\n")

    assert result.exit_code == 0, result.output
    assert result.output.count("Append it?") == 2
    assert result.output.index(".gitignore is missing") < result.output.index(
        ".gitattributes is missing"
    )
    assert "skipped  .gitignore" in result.output
    assert "appended .gitattributes" in result.output
    assert gitignore.read_bytes() == gitignore_original
    assert attributes.read_bytes().endswith(
        b"# napflow\n*.yaml text eol=lf\n*.yml text eol=lf\n"
    )


def test_init_eof_on_second_prompt_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "abort-second"
    ws.mkdir()
    gitignore = ws / ".gitignore"
    attributes = ws / ".gitattributes"
    gitignore_original = b"# owner ignore\n"
    attributes_original = b"# owner attributes\n"
    gitignore.write_bytes(gitignore_original)
    attributes.write_bytes(attributes_original)
    monkeypatch.setattr(cli_main, "_stdin_is_tty", lambda: True)

    result = runner.invoke(app, ["init", str(ws)], input="y\n")

    assert result.exit_code != 0
    assert ".gitignore is missing" in result.output
    assert ".gitattributes is missing" in result.output
    assert gitignore.read_bytes() == gitignore_original
    assert attributes.read_bytes() == attributes_original
    assert not (ws / "napflow.yaml").exists()


def test_accepted_prompt_is_bound_to_the_displayed_file_snapshot(
    tmp_path: Path,
) -> None:
    ws = tmp_path / "changed-during-prompt"
    ws.mkdir()
    (ws / ".gitignore").write_text(
        "envs/*.env\n!envs/example.env\n.napflow/\n",
        encoding="utf-8",
        newline="",
    )
    attributes = ws / ".gitattributes"
    attributes.write_bytes(b"# owner before prompt\n")
    changed = b"# owner changed during prompt\n*.yaml text eol=lf\n"

    def accept_then_change(_inspection) -> bool:
        attributes.write_bytes(changed)
        return True

    results = scaffold_workspace(ws, decide_git_metadata=accept_then_change)
    result = next(item for item in results if item.relative_path == ".gitattributes")

    assert result.status == "skipped"
    assert result.metadata is not None
    assert result.metadata.missing_rules == ("*.yml text eol=lf",)
    assert attributes.read_bytes() == changed


def test_init_no_tty_never_mutates_existing_metadata_and_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "brownfield"
    ws.mkdir()
    gitignore = ws / ".gitignore"
    original = b"# owner\n"
    gitignore.write_bytes(original)
    monkeypatch.setattr(cli_main, "_stdin_is_tty", lambda: False)

    result = runner.invoke(app, ["init", str(ws)])

    assert result.exit_code == 0, result.output
    assert "Append it?" not in result.output
    assert "skipped  .gitignore" in result.output
    assert "WARNING: .gitignore napflow rules were not added." in result.output
    assert "!envs/example.env" in result.output
    assert gitignore.read_bytes() == original


def test_init_existing_covered_metadata_needs_no_prompt_or_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "covered"
    ws.mkdir()
    gitignore = ws / ".gitignore"
    attributes = ws / ".gitattributes"
    gitignore.write_text("envs/*.env\n!envs/example.env\n.napflow/\n", encoding="utf-8")
    attributes.write_text("*.yaml text eol=lf\n*.yml text eol=lf\n", encoding="utf-8")
    before = (gitignore.read_bytes(), attributes.read_bytes())
    monkeypatch.setattr(cli_main, "_stdin_is_tty", lambda: True)

    result = runner.invoke(app, ["init", str(ws)])

    assert result.exit_code == 0, result.output
    assert "Append it?" not in result.output
    assert "exists   .gitignore (rules covered)" in result.output
    assert "exists   .gitattributes (rules covered)" in result.output
    assert (gitignore.read_bytes(), attributes.read_bytes()) == before


@pytest.mark.parametrize(
    ("mode", "expected_status", "changed"),
    [("append", "appended", True), ("skip", "skipped", False)],
)
def test_init_git_meta_mode_is_explicit_and_noninteractive(
    mode: str,
    expected_status: str,
    changed: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ws = tmp_path / mode
    ws.mkdir()
    gitignore = ws / ".gitignore"
    original = b"# owner\n"
    gitignore.write_bytes(original)
    monkeypatch.setattr(cli_main, "_stdin_is_tty", lambda: True)

    result = runner.invoke(app, ["init", str(ws), "--git-meta", mode])

    assert result.exit_code == 0, result.output
    assert "Append it?" not in result.output
    assert f"{expected_status:<8} .gitignore" in result.output
    assert (gitignore.read_bytes() != original) is changed
    assert ("WARNING:" in result.output) is (not changed)


def test_init_no_git_meta_check_bypasses_existing_files_but_creates_missing(
    tmp_path: Path,
) -> None:
    ws = tmp_path / "unchecked"
    ws.mkdir()
    gitignore = ws / ".gitignore"
    original = b"# owner\r\n"
    gitignore.write_bytes(original)

    result = runner.invoke(app, ["init", str(ws), "--no-git-meta-check"])

    assert result.exit_code == 0, result.output
    assert "WARNING:" not in result.output
    assert "exists   .gitignore" in result.output
    assert "created  .gitattributes" in result.output
    assert gitignore.read_bytes() == original


def test_init_no_git_meta_check_leaves_both_existing_files_byte_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "unchecked-existing"
    ws.mkdir()
    gitignore = ws / ".gitignore"
    attributes = ws / ".gitattributes"
    gitignore_original = b"# owner\r\n"
    attributes_original = b"owner=\xff\n"
    gitignore.write_bytes(gitignore_original)
    attributes.write_bytes(attributes_original)
    monkeypatch.setattr(cli_main, "_stdin_is_tty", lambda: True)

    result = runner.invoke(app, ["init", str(ws), "--no-git-meta-check"])

    assert result.exit_code == 0, result.output
    assert "Append it?" not in result.output
    assert "WARNING:" not in result.output
    assert gitignore.read_bytes() == gitignore_original
    assert attributes.read_bytes() == attributes_original


def test_init_rejects_append_when_git_meta_check_is_disabled(tmp_path: Path) -> None:
    ws = tmp_path / "conflict"

    result = runner.invoke(
        app,
        ["init", str(ws), "--git-meta", "append", "--no-git-meta-check"],
    )

    assert result.exit_code == 2
    assert "cannot be used with --no-git-meta-check" in result.output
    assert not ws.exists()


def test_init_allows_redundant_skip_when_git_meta_check_is_disabled(
    tmp_path: Path,
) -> None:
    ws = tmp_path / "unchecked-skip"
    ws.mkdir()
    gitignore = ws / ".gitignore"
    original = b"# owner\r\n"
    gitignore.write_bytes(original)

    result = runner.invoke(
        app,
        ["init", str(ws), "--git-meta", "skip", "--no-git-meta-check"],
    )

    assert result.exit_code == 0, result.output
    assert "WARNING:" not in result.output
    assert "exists   .gitignore" in result.output
    assert gitignore.read_bytes() == original


def test_init_never_appends_or_normalizes_crlf_metadata(tmp_path: Path) -> None:
    ws = tmp_path / "crlf"
    ws.mkdir()
    gitignore = ws / ".gitignore"
    original = b"# owner\r\n.napflow/\r\n"
    gitignore.write_bytes(original)

    result = runner.invoke(app, ["init", str(ws), "--git-meta", "append"])

    assert result.exit_code == 0, result.output
    assert "skipped  .gitignore" in result.output
    assert "uses CRLF or CR line endings" in result.output
    assert "envs/*.env" in result.output
    assert gitignore.read_bytes() == original


def test_init_warns_and_preserves_invalid_utf8_metadata(tmp_path: Path) -> None:
    ws = tmp_path / "invalid-utf8"
    ws.mkdir()
    gitignore = ws / ".gitignore"
    original = b"owner=\xff\n"
    gitignore.write_bytes(original)

    result = runner.invoke(app, ["init", str(ws), "--git-meta", "append"])

    assert result.exit_code == 0, result.output
    assert "skipped  .gitignore" in result.output
    assert "is not valid UTF-8 and was left unchanged" in result.output
    assert gitignore.read_bytes() == original


def test_init_metadata_create_failure_precedes_manifest_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "create-failure"

    def fail_create(_path: Path, _content: str) -> bool:
        raise PermissionError("owner denied create")

    monkeypatch.setattr(cli_scaffold, "atomic_create_text", fail_create)

    result = runner.invoke(app, ["init", str(ws)])

    assert result.exit_code == 2
    assert "could not initialize" in result.output
    assert "owner denied create" in result.output
    assert not (ws / "napflow.yaml").exists()


def test_init_prompts_before_writing_any_scaffold_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "abort"
    ws.mkdir()
    (ws / ".gitignore").write_text("# owner\n", encoding="utf-8")
    monkeypatch.setattr(cli_main, "_stdin_is_tty", lambda: True)

    result = runner.invoke(app, ["init", str(ws)], input="")

    assert result.exit_code != 0
    assert not (ws / "napflow.yaml").exists()


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


def test_check_warns_read_only_for_root_git_metadata(
    scaffolded: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attributes = scaffolded / ".gitattributes"
    attributes.unlink()
    monkeypatch.chdir(scaffolded)

    result = runner.invoke(app, ["check"])

    assert result.exit_code == 0
    assert ".gitattributes: W109" in result.output
    assert "1 warning" in result.output
    assert not attributes.exists()


def test_check_git_metadata_warning_can_be_disabled(
    scaffolded: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (scaffolded / ".gitignore").unlink()
    monkeypatch.chdir(scaffolded)

    result = runner.invoke(app, ["check", "--no-git-meta-check"])

    assert result.exit_code == 0
    assert "W109" not in result.output
    assert "0 warnings" in result.output
    assert not (scaffolded / ".gitignore").exists()


# --------------------------------------------------------------------------
# meta


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "napflow" in result.output
