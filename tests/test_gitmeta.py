import os
import stat
from pathlib import Path

import pytest

from napflow.core.gitmeta import (
    GITATTRIBUTES,
    GITIGNORE,
    GitMetadataAppendError,
    GitMetadataState,
    append_git_metadata,
    default_git_metadata_rules,
    gitattributes_rules,
    gitignore_rules,
    inspect_git_metadata,
)


def test_default_rules_are_the_current_exact_scaffold_rules() -> None:
    ignore, attributes = default_git_metadata_rules()

    assert ignore.filename == GITIGNORE
    assert ignore.required_rules == (
        "envs/*.env",
        "!envs/example.env",
        ".napflow/",
    )
    assert attributes.filename == GITATTRIBUTES
    assert attributes.required_rules == (
        "*.yaml text eol=lf",
        "*.yml text eol=lf",
    )


def test_missing_file_is_reported_but_not_created(tmp_path: Path) -> None:
    inspection = inspect_git_metadata(tmp_path, gitattributes_rules())

    assert inspection.state is GitMetadataState.MISSING
    assert not inspection.appendable
    with pytest.raises(GitMetadataAppendError, match="missing"):
        append_git_metadata(inspection)
    assert not inspection.path.exists()


@pytest.mark.parametrize("rules", default_git_metadata_rules())
def test_parent_and_repository_local_metadata_never_count(
    rules, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (tmp_path / rules.filename).write_text(
        "\n".join(rules.required_rules) + "\n", encoding="utf-8"
    )
    info = tmp_path / ".git" / "info"
    info.mkdir(parents=True)
    (info / ("exclude" if rules.filename == GITIGNORE else "attributes")).write_text(
        "\n".join(rules.required_rules) + "\n", encoding="utf-8"
    )

    inspection = inspect_git_metadata(workspace, rules)

    assert inspection.state is GitMetadataState.MISSING


def test_exact_rules_are_covered_without_requiring_a_marker(tmp_path: Path) -> None:
    path = tmp_path / GITIGNORE
    original = "user-rule\nenvs/*.env\n!envs/example.env\n.napflow/\n"
    path.write_text(original, encoding="utf-8", newline="")

    inspection = inspect_git_metadata(tmp_path, gitignore_rules())

    assert inspection.state is GitMetadataState.COVERED
    assert inspection.missing_rules == ()
    assert inspection.append_block == ""
    assert append_git_metadata(inspection) == inspection
    assert path.read_text(encoding="utf-8") == original


def test_partial_rules_produce_only_missing_lines_in_marked_block(
    tmp_path: Path,
) -> None:
    path = tmp_path / GITATTRIBUTES
    path.write_text("# owner\n*.yaml text eol=lf\n", encoding="utf-8", newline="")

    inspection = inspect_git_metadata(tmp_path, gitattributes_rules())

    assert inspection.state is GitMetadataState.NEEDS_APPEND
    assert inspection.missing_rules == ("*.yml text eol=lf",)
    assert inspection.append_block == "# napflow\n*.yml text eol=lf\n"


@pytest.mark.parametrize(
    ("existing", "missing"),
    [
        (
            "!envs/example.env\n.napflow/\n",
            ("envs/*.env", "!envs/example.env"),
        ),
        (
            "!envs/example.env\nenvs/*.env\n.napflow/\n",
            ("!envs/example.env",),
        ),
        (
            "envs/*.env\n.napflow/\n!envs/example.env\n",
            (),
        ),
    ],
)
def test_example_exception_is_readded_after_the_last_wildcard_when_needed(
    tmp_path: Path, existing: str, missing: tuple[str, ...]
) -> None:
    (tmp_path / GITIGNORE).write_text(existing, encoding="utf-8", newline="")

    inspection = inspect_git_metadata(tmp_path, gitignore_rules())

    assert inspection.missing_rules == missing
    expected_state = (
        GitMetadataState.NEEDS_APPEND if missing else GitMetadataState.COVERED
    )
    assert inspection.state is expected_state


def test_append_is_atomic_preserves_content_and_mode_and_is_idempotent(
    tmp_path: Path,
) -> None:
    path = tmp_path / GITATTRIBUTES
    original = b"# owner content without final newline"
    path.write_bytes(original)
    path.chmod(0o640)
    original_mode = stat.S_IMODE(path.stat().st_mode)

    inspection = inspect_git_metadata(tmp_path, gitattributes_rules())
    appended = append_git_metadata(inspection)

    assert appended.state is GitMetadataState.COVERED
    assert path.read_bytes() == (
        original + b"\n\n# napflow\n*.yaml text eol=lf\n*.yml text eol=lf\n"
    )
    assert stat.S_IMODE(path.stat().st_mode) == original_mode

    before = path.read_bytes()
    assert append_git_metadata(appended).state is GitMetadataState.COVERED
    assert path.read_bytes() == before


@pytest.mark.parametrize("line_ending", [b"\r\n", b"\r"])
def test_non_lf_file_is_never_appended(line_ending: bytes, tmp_path: Path) -> None:
    path = tmp_path / GITATTRIBUTES
    original = b"# owner" + line_ending
    path.write_bytes(original)

    inspection = inspect_git_metadata(tmp_path, gitattributes_rules())

    assert inspection.state is GitMetadataState.NON_LF
    assert inspection.missing_rules == gitattributes_rules().required_rules
    assert not inspection.appendable
    with pytest.raises(GitMetadataAppendError, match="non_lf"):
        append_git_metadata(inspection)
    assert path.read_bytes() == original


def test_crlf_rules_are_recognized_but_file_still_requires_owner_conversion(
    tmp_path: Path,
) -> None:
    path = tmp_path / GITATTRIBUTES
    path.write_bytes(b"*.yaml text eol=lf\r\n*.yml text eol=lf\r\n")

    inspection = inspect_git_metadata(tmp_path, gitattributes_rules())

    assert inspection.state is GitMetadataState.NON_LF
    assert inspection.missing_rules == ()
    assert not inspection.appendable


def test_unreadable_regular_file_is_never_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / GITIGNORE
    original = b"owner\n"
    path.write_bytes(original)
    real_read_bytes = Path.read_bytes

    def fail_read(candidate: Path) -> bytes:
        if candidate == path:
            raise PermissionError("owner denied read")
        return real_read_bytes(candidate)

    monkeypatch.setattr(Path, "read_bytes", fail_read)
    inspection = inspect_git_metadata(tmp_path, gitignore_rules())

    assert inspection.state is GitMetadataState.UNREADABLE
    assert "owner denied read" in (inspection.detail or "")
    with pytest.raises(GitMetadataAppendError, match="unreadable"):
        append_git_metadata(inspection)
    assert real_read_bytes(path) == original


def test_invalid_utf8_is_never_overwritten(tmp_path: Path) -> None:
    path = tmp_path / GITIGNORE
    original = b"owner=\xff\n"
    path.write_bytes(original)

    inspection = inspect_git_metadata(tmp_path, gitignore_rules())

    assert inspection.state is GitMetadataState.INVALID_UTF8
    assert inspection.detail
    with pytest.raises(GitMetadataAppendError, match="invalid_utf8"):
        append_git_metadata(inspection)
    assert path.read_bytes() == original


@pytest.mark.parametrize("kind", ["symlink", "directory", "fifo"])
def test_symlink_and_non_regular_paths_are_never_followed_or_replaced(
    kind: str, tmp_path: Path
) -> None:
    path = tmp_path / GITIGNORE
    target = tmp_path / "outside"
    target.write_text("outside stays exact\n", encoding="utf-8", newline="")
    if kind == "symlink":
        try:
            path.symlink_to(target)
        except OSError as error:
            pytest.skip(f"symlink unavailable: {error}")
    elif kind == "directory":
        path.mkdir()
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO unavailable")
        os.mkfifo(path)

    inspection = inspect_git_metadata(tmp_path, gitignore_rules())

    assert inspection.state is GitMetadataState.INVALID_PATH
    with pytest.raises(GitMetadataAppendError, match="invalid_path"):
        append_git_metadata(inspection)
    if kind == "symlink":
        assert path.is_symlink()
    elif kind == "directory":
        assert path.is_dir()
    else:
        assert stat.S_ISFIFO(path.lstat().st_mode)
    assert target.read_bytes() == b"outside stays exact\n"


def test_append_rechecks_a_path_changed_to_a_symlink(tmp_path: Path) -> None:
    path = tmp_path / GITIGNORE
    path.write_text("owner\n", encoding="utf-8", newline="")
    inspection = inspect_git_metadata(tmp_path, gitignore_rules())
    assert inspection.appendable

    target = tmp_path / "outside"
    target.write_text("outside stays exact\n", encoding="utf-8", newline="")
    path.unlink()
    try:
        path.symlink_to(target)
    except OSError as error:
        pytest.skip(f"symlink unavailable: {error}")

    with pytest.raises(GitMetadataAppendError, match="changed after inspection"):
        append_git_metadata(inspection)
    assert path.is_symlink()
    assert target.read_bytes() == b"outside stays exact\n"


def test_append_refuses_lf_content_changed_after_inspection(tmp_path: Path) -> None:
    path = tmp_path / GITATTRIBUTES
    path.write_text("# owner\n", encoding="utf-8", newline="")
    inspection = inspect_git_metadata(tmp_path, gitattributes_rules())
    assert inspection.appendable

    changed = b"# owner changed while the prompt was open\n"
    path.write_bytes(changed)

    with pytest.raises(GitMetadataAppendError, match="changed after inspection"):
        append_git_metadata(inspection)
    assert path.read_bytes() == changed
