from __future__ import annotations

import runpy
import subprocess
import sys
from pathlib import Path

import pytest
from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[1]
TOOL = runpy.run_path(str(ROOT / "tools" / "check_release_version.py"))
ReleaseVersionError = TOOL["ReleaseVersionError"]
check_release_version = TOOL["check_release_version"]
validate_release_version = TOOL["validate_release_version"]


def _write_pyproject(path: Path, version: str) -> Path:
    pyproject = path / "pyproject.toml"
    pyproject.write_text(
        f'[project]\nname = "napflow"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    return pyproject


def _load_workflow(name: str) -> dict:
    yaml = YAML(typ="safe")
    yaml.version = (1, 2)
    return yaml.load((ROOT / ".github" / "workflows" / name).read_text())


def _commands(job: dict) -> str:
    return "\n".join(str(step["run"]) for step in job["steps"] if "run" in step)


def test_release_version_requires_exact_v_tag(tmp_path: Path) -> None:
    pyproject = _write_pyproject(tmp_path, "0.2.0")

    assert check_release_version(pyproject, tag="v0.2.0") == "0.2.0"
    for tag in ("0.2.0", "v0.2.1", "v0.2.0-rc1"):
        with pytest.raises(ReleaseVersionError, match="requires tag 'v0.2.0'"):
            check_release_version(pyproject, tag=tag)


@pytest.mark.parametrize("version", ["0.2.0.dev1", "0.2.0dev1", "1.0.DEV2"])
def test_release_version_refuses_development_checkpoint(version: str) -> None:
    with pytest.raises(ReleaseVersionError, match="cannot be published"):
        validate_release_version(version, tag=f"v{version}")


@pytest.mark.parametrize(
    ("version", "tag", "returncode", "message"),
    [
        ("0.2.0", "v0.2.0", 0, "release version check passed"),
        ("0.2.0", "v0.2.1", 1, "requires tag 'v0.2.0'"),
        ("0.2.0.dev1", "v0.2.0.dev1", 1, "cannot be published"),
    ],
)
def test_release_version_cli_enforces_workflow_contract(
    tmp_path: Path,
    version: str,
    tag: str,
    returncode: int,
    message: str,
) -> None:
    pyproject = _write_pyproject(tmp_path, version)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "check_release_version.py"),
            "--pyproject",
            str(pyproject),
            "--tag",
            tag,
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == returncode
    assert message in result.stdout + result.stderr


def test_ci_workflow_is_the_reusable_product_gate() -> None:
    workflow = _load_workflow("ci.yml")
    assert "workflow_call" in workflow["on"]

    test_commands = _commands(workflow["jobs"]["test"])
    for command in (
        "uv run ruff format --check",
        "uv run ruff check",
        "uv run lint-imports",
        "uv run pytest",
    ):
        assert command in test_commands

    ui = workflow["jobs"]["ui"]
    ui_commands = _commands(ui)
    required = (
        "npm ci",
        "generate_frontend_notices.py --check",
        "npm test",
        "npm run build",
        "uv build --clear",
        "smoke_release_artifact.py dist",
        "npm run e2e",
    )
    for command in required:
        assert command in ui_commands
    assert [ui_commands.index(command) for command in required] == sorted(
        ui_commands.index(command) for command in required
    )
    smoke_step = next(
        step
        for step in ui["steps"]
        if step.get("name") == "Installed release-artifact smoke"
    )
    assert smoke_step["if"] == "runner.os == 'Linux'"

    version_job = workflow["jobs"]["release-version"]
    assert version_job["if"] == "github.ref_type == 'tag'"
    version_commands = _commands(version_job)
    assert "tools/check_release_version.py" in version_commands
    assert '--tag "${{ github.ref_name }}"' in version_commands


def test_release_reuses_gate_and_cannot_publish_a_manual_dispatch() -> None:
    workflow = _load_workflow("release.yml")
    jobs = workflow["jobs"]
    assert jobs["gate"]["uses"] == "./.github/workflows/ci.yml"
    assert jobs["build"]["needs"] == "gate"

    build_commands = _commands(jobs["build"])
    for command in (
        "npm ci",
        "generate_frontend_notices.py --check",
        "npm run build",
        "uv build --clear",
        "smoke_release_artifact.py dist",
    ):
        assert command in build_commands

    assert 'version_notes="docs/release-notes-${GITHUB_REF_NAME}.md"' in build_commands
    assert 'cat docs/release-notes-preamble-v0.md "$version_notes"' in build_commands

    publish_condition = "github.event_name == 'push' && github.ref_type == 'tag'"
    assert jobs["pypi"]["if"] == publish_condition
    assert jobs["github-release"]["if"] == publish_condition


def test_v02_release_notes_publish_breaks_and_best_effort_reader_contract() -> None:
    notes = (ROOT / "docs" / "release-notes-v0.2.0.md").read_text(encoding="utf-8")

    for required in (
        "body_capture_mb",
        "run_capture_mb",
        "rejects YAML anchors and aliases",
        "napflow-run/1",
        "content-blobs/1",
        "napflow-replay/1",
        "Markerless v0.1 logs are read best-effort",
        "raw full values",
        "direct VCS",
    ):
        assert required in notes
