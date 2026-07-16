"""FR-801/802/805 + FR-107: the S1 CLI surface."""

from pathlib import Path

import pytest
from click import unstyle
from typer.testing import CliRunner

import napflow.cli.main as cli_main
import napflow.cli.scaffold as cli_scaffold
from napflow.cli.main import app
from napflow.cli.scaffold import scaffold_workspace
from napflow.core.checker import check_workspace
from napflow.core.gitmeta import (
    default_git_metadata_rules,
    example_git_metadata_rules,
)
from napflow.core.workspace import load_workspace

runner = CliRunner()

MINIMAL_SCAFFOLD_FILES = [
    "napflow.yaml",
    "flows/main/flow.yaml",
    "flows/main/nodes.py",
    ".gitignore",
    ".gitattributes",
]

EXAMPLE_SCAFFOLD_FILES = [
    *MINIMAL_SCAFFOLD_FILES,
    "flows/example/flow.yaml",
    "flows/example/nodes.py",
    "flows/smoke/flow.yaml",
    "flows/smoke/nodes.py",
    "data/smoke.json",
    ".env",
    ".env.example",
]

GITIGNORE_RULES = default_git_metadata_rules()[0].required_rules
EXAMPLE_GITIGNORE_RULES = example_git_metadata_rules()[0].required_rules
EXAMPLE_PROFILE_RULES = tuple(
    rule for rule in EXAMPLE_GITIGNORE_RULES if rule not in GITIGNORE_RULES
)
EXAMPLE_PROFILE_BLOCK = "# napflow\n" + "\n".join(EXAMPLE_PROFILE_RULES) + "\n"


@pytest.fixture
def scaffolded(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    result = runner.invoke(app, ["init", str(ws)])
    assert result.exit_code == 0, result.output
    return ws


@pytest.fixture
def example_scaffolded(tmp_path: Path) -> Path:
    ws = tmp_path / "example-ws"
    result = runner.invoke(app, ["init", str(ws), "--example"])
    assert result.exit_code == 0, result.output
    return ws


# --------------------------------------------------------------------------
# napf init (FR-805 / FR-107)


def test_init_creates_only_the_minimal_scaffold(scaffolded: Path) -> None:
    for rel in MINIMAL_SCAFFOLD_FILES:
        assert (scaffolded / rel).is_file(), rel
    assert (scaffolded / ".napflow").is_dir()
    assert not (scaffolded / "envs").exists()
    assert (scaffolded / "data").is_dir()
    assert not any((scaffolded / "data").iterdir())
    assert not (scaffolded / "flows" / "example").exists()
    assert not (scaffolded / "flows" / "smoke").exists()
    assert {path.name for path in scaffolded.iterdir()} == {
        ".gitattributes",
        ".gitignore",
        ".napflow",
        "data",
        "flows",
        "napflow.yaml",
    }
    assert {path.name for path in (scaffolded / "flows").iterdir()} == {"main"}
    assert {path.name for path in (scaffolded / "flows" / "main").iterdir()} == {
        "flow.yaml",
        "nodes.py",
    }

    workspace = load_workspace(scaffolded)
    manifest = workspace.manifest.model
    assert manifest.environments.root == "."
    assert manifest.environments.default is None
    assert manifest.data.root == "data"
    assert [ref.identity for ref in workspace.discover_flows()] == ["flows/main"]


def test_init_example_restores_the_full_example_scaffold(
    example_scaffolded: Path,
) -> None:
    for rel in EXAMPLE_SCAFFOLD_FILES:
        assert (example_scaffolded / rel).is_file(), rel
    assert (example_scaffolded / ".napflow").is_dir()

    workspace = load_workspace(example_scaffolded)
    assert workspace.manifest.model.environments.default == ".env"
    assert workspace.manifest.model.data.root == "data"
    assert [ref.identity for ref in workspace.discover_flows()] == [
        "flows/example",
        "flows/main",
        "flows/smoke",
    ]
    smoke = workspace.load_flow("flows/smoke").model
    fixture = next(node for node in smoke.nodes if node.id == "users")
    assert fixture.config.file == "smoke.json"


def test_scaffolded_workspace_checks_clean(scaffolded: Path) -> None:
    # the money test: fresh workspace → zero errors, zero warnings
    assert check_workspace(load_workspace(scaffolded)) == []


def test_example_scaffolded_workspace_checks_clean(example_scaffolded: Path) -> None:
    assert check_workspace(load_workspace(example_scaffolded)) == []


def test_scaffold_secret_patterns_are_opt_in(scaffolded: Path) -> None:
    workspace = load_workspace(scaffolded)
    text = (scaffolded / "napflow.yaml").read_text(encoding="utf-8")

    assert workspace.manifest.model.environments.secrets == []
    assert "secrets: []" in text
    assert '# for example: ["API_TOKEN", "*_PASSWORD"].' in text
    assert "# Raw local history and the local UI remain unmasked." in text


def test_scaffold_is_canonical_output(scaffolded: Path) -> None:
    text = (scaffolded / "flows" / "main" / "flow.yaml").read_bytes().decode()
    assert 'schema: "napflow/v1"' in text  # strings force-quoted
    assert "edges: []" in text
    assert "\r" not in text and text.endswith("\n")

    main = load_workspace(scaffolded).load_flow("flows/main").model
    assert [node.id for node in main.nodes] == ["start", "end"]
    assert main.edges == []


def test_minimal_scaffold_gitignore_only_owns_runtime_data(scaffolded: Path) -> None:
    gitignore = (scaffolded / ".gitignore").read_text(encoding="utf-8")
    assert GITIGNORE_RULES == (".napflow/",)
    assert ".napflow/" in gitignore
    assert "/.env" not in gitignore
    attrs = (scaffolded / ".gitattributes").read_text(encoding="utf-8")
    assert "*.yaml text eol=lf" in attrs


def test_example_scaffold_ignores_only_generated_root_env(
    example_scaffolded: Path,
) -> None:
    gitignore = (example_scaffolded / ".gitignore").read_text(encoding="utf-8")

    assert EXAMPLE_GITIGNORE_RULES == ("/.env", ".napflow/")
    assert "/.env" in gitignore
    assert "*.env" not in gitignore
    assert ".env.*" not in gitignore
    assert (example_scaffolded / ".env.example").is_file()


def test_init_refuses_existing_workspace(scaffolded: Path) -> None:
    result = runner.invoke(app, ["init", str(scaffolded)])
    assert result.exit_code == 2
    assert "already exists" in result.output


def test_init_refuses_broken_manifest_symlink_without_other_writes(
    tmp_path: Path,
) -> None:
    ws = tmp_path / "broken-manifest-link"
    ws.mkdir()
    manifest = ws / "napflow.yaml"
    try:
        manifest.symlink_to(ws / "missing-manifest.yaml")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/privilege level")

    result = runner.invoke(app, ["init", str(ws)])

    assert result.exit_code == 2
    assert "already exists" in result.output
    assert manifest.is_symlink()
    assert {path.name for path in ws.iterdir()} == {"napflow.yaml"}


def test_init_reports_preexisting_napflow_directory_as_exists(tmp_path: Path) -> None:
    ws = tmp_path / "preexisting-history"
    (ws / ".napflow").mkdir(parents=True)

    result = runner.invoke(app, ["init", str(ws)])

    assert result.exit_code == 0, result.output
    assert "exists   .napflow/" in result.output
    assert "created  .napflow/" not in result.output


@pytest.mark.parametrize(
    "help_env",
    [
        {"NO_COLOR": "1", "FORCE_COLOR": None, "PY_COLORS": None},
        {
            "NO_COLOR": None,
            "FORCE_COLOR": "1",
            "PY_COLORS": "1",
            "CI": "true",
            "GITHUB_ACTIONS": "true",
            "TERM": "xterm-256color",
        },
    ],
    ids=["plain", "ansi"],
)
def test_init_help_lists_example_and_root_options(
    help_env: dict[str, str | None],
) -> None:
    help_result = runner.invoke(app, ["init", "--help"], env=help_env)
    assert help_result.exit_code == 0
    help_output = unstyle(help_result.output)
    assert "--example" in help_output
    assert "--flows-root" in help_output
    assert "--data-root" in help_output
    assert "--environments-ro" in help_output  # Rich may ellipsize the tail
    assert "optionally with runnable examples" in " ".join(help_output.split())


def test_init_first_touch_distinguishes_example_mode(tmp_path: Path) -> None:
    minimal = runner.invoke(app, ["init", str(tmp_path / "minimal")])
    example = runner.invoke(app, ["init", str(tmp_path / "with-examples"), "--example"])
    assert minimal.exit_code == example.exit_code == 0
    assert "then `napf ui`" in minimal.output
    assert "then `napf run flows/smoke`" in example.output


def test_init_configures_and_creates_custom_workspace_roots(tmp_path: Path) -> None:
    ws = tmp_path / "custom-roots"

    result = runner.invoke(
        app,
        [
            "init",
            str(ws),
            "--flows-root",
            "automation/flows",
            "--data-root",
            "input-data",
            "--environments-root",
            "config/env",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (ws / "automation/flows/main/flow.yaml").is_file()
    assert (ws / "input-data").is_dir()
    assert not any((ws / "input-data").iterdir())
    assert (ws / "config/env").is_dir()
    assert not any((ws / "config/env").iterdir())
    workspace = load_workspace(ws)
    assert workspace.manifest.model.flows.root == "automation/flows"
    assert workspace.manifest.model.flows.main == "automation/flows/main"
    assert workspace.manifest.model.data.root == "input-data"
    assert workspace.manifest.model.environments.root == "config/env"
    assert [ref.identity for ref in workspace.discover_flows()] == [
        "automation/flows/main"
    ]


def test_init_example_places_files_below_custom_roots(tmp_path: Path) -> None:
    ws = tmp_path / "custom-example"

    result = runner.invoke(
        app,
        [
            "init",
            str(ws),
            "--example",
            "--flows-root",
            "napflow-flows",
            "--data-root",
            "input-data",
            "--environments-root",
            "profiles",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (ws / "napflow-flows/smoke/flow.yaml").is_file()
    assert (ws / "input-data/smoke.json").is_file()
    assert (ws / "profiles/.env").is_file()
    assert (ws / "profiles/.env.example").is_file()
    assert "`napf run napflow-flows/smoke`" in result.output
    gitignore = (ws / ".gitignore").read_text(encoding="utf-8")
    assert "/profiles/.env" in gitignore.splitlines()
    assert "*.env" not in gitignore
    assert ".env.*" not in gitignore
    assert check_workspace(load_workspace(ws)) == []


def test_init_reuses_existing_root_directories_without_replacing_owner_files(
    tmp_path: Path,
) -> None:
    ws = tmp_path / "brownfield-roots"
    owner_files = {
        "flows/README.md": "owner flow docs\n",
        "flows/main/nodes.py": "# owner main functions\n",
        "data/users.json": "[]\n",
        "profiles/owner.env": "OWNER=yes\n",
    }
    for rel, content in owner_files.items():
        path = ws / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    result = runner.invoke(
        app,
        ["init", str(ws), "--environments-root", "profiles"],
    )

    assert result.exit_code == 0, result.output
    assert (ws / "flows/main/flow.yaml").is_file()
    assert "exists   flows/main/nodes.py" in result.output
    for rel, content in owner_files.items():
        assert (ws / rel).read_text(encoding="utf-8") == content


@pytest.mark.parametrize(
    ("option", "collision"),
    [
        ("--flows-root", "napflow.yaml"),
        ("--data-root", ".gitignore/cache"),
        ("--environments-root", "flows/main/flow.yaml"),
    ],
)
def test_init_rejects_planned_file_directory_role_collisions_without_writes(
    tmp_path: Path, option: str, collision: str
) -> None:
    ws = tmp_path / option.removeprefix("--")

    result = runner.invoke(app, ["init", str(ws), option, collision])

    assert result.exit_code == 2
    assert "conflicts with planned file" in result.output
    assert not ws.exists()


def test_init_example_rejects_root_at_example_only_asset_without_writes(
    tmp_path: Path,
) -> None:
    ws = tmp_path / "example-role-collision"

    result = runner.invoke(
        app,
        [
            "init",
            str(ws),
            "--example",
            "--environments-root",
            "data/smoke.json",
        ],
    )

    assert result.exit_code == 2
    assert "planned directory 'data/smoke.json'" in result.output
    assert "planned file 'data/smoke.json'" in result.output
    assert not ws.exists()


def test_scaffold_rejects_planned_role_collision_before_any_write(
    tmp_path: Path,
) -> None:
    ws = tmp_path / "direct-role-collision"
    ws.mkdir()
    gitignore = ws / ".gitignore"
    original = b"# owner\n"
    gitignore.write_bytes(original)

    def unexpected_prompt(_inspection) -> bool:
        pytest.fail("planned-role preflight must run before metadata callbacks")

    with pytest.raises(OSError, match="conflicts with planned file"):
        scaffold_workspace(
            ws,
            data_root="flows/main/flow.yaml",
            decide_git_metadata=unexpected_prompt,
        )

    assert gitignore.read_bytes() == original
    assert {path.name for path in ws.iterdir()} == {".gitignore"}


def test_scaffold_rejects_directory_at_source_file_before_callback_or_write(
    tmp_path: Path,
) -> None:
    ws = tmp_path / "source-directory-collision"
    source = ws / "flows/main/flow.yaml"
    source.mkdir(parents=True)
    gitignore = ws / ".gitignore"
    original = b"# owner\n"
    gitignore.write_bytes(original)

    def unexpected_prompt(_inspection) -> bool:
        pytest.fail("source-file preflight must run before metadata callbacks")

    with pytest.raises(OSError, match="planned scaffold file.*is a directory"):
        scaffold_workspace(ws, decide_git_metadata=unexpected_prompt)

    assert source.is_dir()
    assert gitignore.read_bytes() == original
    assert {path.name for path in ws.iterdir()} == {".gitignore", "flows"}
    assert {path.name for path in source.parent.iterdir()} == {"flow.yaml"}


def test_scaffold_rejects_dangling_source_symlink_before_callback_or_write(
    tmp_path: Path,
) -> None:
    ws = tmp_path / "source-symlink-collision"
    source = ws / "flows/main/flow.yaml"
    source.parent.mkdir(parents=True)
    try:
        source.symlink_to(ws / "missing-flow.yaml")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/privilege level")
    gitignore = ws / ".gitignore"
    original = b"# owner\n"
    gitignore.write_bytes(original)

    def unexpected_prompt(_inspection) -> bool:
        pytest.fail("source-file preflight must run before metadata callbacks")

    with pytest.raises(OSError, match="planned scaffold file.*is a symlink"):
        scaffold_workspace(ws, decide_git_metadata=unexpected_prompt)

    assert source.is_symlink() and not source.exists()
    assert gitignore.read_bytes() == original
    assert {path.name for path in ws.iterdir()} == {".gitignore", "flows"}
    assert {path.name for path in source.parent.iterdir()} == {"flow.yaml"}


@pytest.mark.parametrize(
    ("option", "collision"),
    [
        ("--flows-root", "owner-flows"),
        ("--data-root", "owner-data"),
        ("--environments-root", "owner-envs"),
    ],
)
def test_init_root_file_collision_fails_before_manifest_write(
    tmp_path: Path, option: str, collision: str
) -> None:
    ws = tmp_path / collision
    ws.mkdir()
    (ws / collision).write_text("owner file\n", encoding="utf-8")

    result = runner.invoke(app, ["init", str(ws), option, collision])

    assert result.exit_code == 2
    assert "directory" in result.output
    assert collision in result.output
    assert not (ws / "napflow.yaml").exists()


def test_init_root_symlink_collision_fails_before_manifest_write(
    tmp_path: Path,
) -> None:
    ws = tmp_path / "symlink-collision"
    ws.mkdir()
    target = ws / "owner-data"
    target.mkdir()
    try:
        (ws / "data-link").symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform/privilege level")

    result = runner.invoke(app, ["init", str(ws), "--data-root", "data-link"])

    assert result.exit_code == 2
    assert "symlink" in result.output
    assert not (ws / "napflow.yaml").exists()


def test_init_root_junction_collision_fails_before_manifest_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "junction-collision"
    data = ws / "data-link"
    data.mkdir(parents=True)
    real_is_junction = Path.is_junction
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda path: path == data or real_is_junction(path),
    )

    result = runner.invoke(app, ["init", str(ws), "--data-root", "data-link"])

    assert result.exit_code == 2
    assert "junction" in result.output
    assert not (ws / "napflow.yaml").exists()


@pytest.mark.parametrize(
    ("option", "value"),
    [("--flows-root", "."), ("--data-root", "./")],
)
def test_init_rejects_workspace_root_for_flow_and_data_roots(
    tmp_path: Path, option: str, value: str
) -> None:
    ws = tmp_path / f"invalid-{option.removeprefix('--')}"

    result = runner.invoke(app, ["init", str(ws), option, value])

    assert result.exit_code == 2
    assert "proper subdirectory" in result.output
    assert not (ws / "napflow.yaml").exists()


@pytest.mark.parametrize("answer", ["y\n", "\n"])
def test_init_interactive_accept_and_default_append_missing_rules(
    answer: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "brownfield"
    ws.mkdir()
    gitignore = ws / ".gitignore"
    gitignore.write_text("# owner\n.napflow/\n", encoding="utf-8", newline="")
    monkeypatch.setattr(cli_main, "_stdin_is_tty", lambda: True)

    result = runner.invoke(app, ["init", str(ws), "--example"], input=answer)

    assert result.exit_code == 0, result.output
    assert ".gitignore is missing this napflow block" in result.output
    assert EXAMPLE_PROFILE_BLOCK.rstrip("\n") in result.output
    assert "appended .gitignore" in result.output
    assert "WARNING:" not in result.output
    assert gitignore.read_text(encoding="utf-8").endswith(EXAMPLE_PROFILE_BLOCK)


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
    assert ".napflow/" in result.output
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
        "\n".join(GITIGNORE_RULES) + "\n",
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
    assert ".napflow/" in result.output
    assert gitignore.read_bytes() == original


def test_init_existing_covered_metadata_needs_no_prompt_or_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "covered"
    ws.mkdir()
    gitignore = ws / ".gitignore"
    attributes = ws / ".gitattributes"
    gitignore.write_text(
        "\n".join(GITIGNORE_RULES) + "\n",
        encoding="utf-8",
        newline="",
    )
    attributes.write_text(
        "*.yaml text eol=lf\n*.yml text eol=lf\n",
        encoding="utf-8",
        newline="",
    )
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

    result = runner.invoke(app, ["init", str(ws), "--example", "--git-meta", "append"])

    assert result.exit_code == 0, result.output
    assert "skipped  .gitignore" in result.output
    assert "uses CRLF or CR line endings" in result.output
    assert "/.env" in result.output
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
    (ws / ".gitignore").write_text("# owner\n", encoding="utf-8", newline="")
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
    assert len(lines) == 1
    assert lines[0].startswith("flows/main")
    assert "out: —" in lines[0]


def test_list_shows_all_opt_in_example_flows(
    example_scaffolded: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(example_scaffolded)
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
    assert "1 flow: 0 errors, 0 warnings" in result.output


def test_check_example_scaffold_exits_0(
    example_scaffolded: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(example_scaffolded)
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
