"""Root Git-metadata rules used by workspace scaffolding and checks.

Only ``.gitignore`` and ``.gitattributes`` in the napflow workspace root
participate.  This module deliberately does not invoke Git or inspect inherited,
global, or repository-local configuration: the files are user-owned source and
napflow only offers to append its small canonical block during ``napf init``.
"""

from __future__ import annotations

import stat
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from napflow.core.files import atomic_write_text

GITIGNORE = ".gitignore"
GITATTRIBUTES = ".gitattributes"
NAPFLOW_BLOCK_HEADER = "# napflow"


class GitMetadataState(StrEnum):
    """Result of inspecting one workspace-root metadata file."""

    MISSING = "missing"
    COVERED = "covered"
    NEEDS_APPEND = "needs_append"
    NON_LF = "non_lf"
    INVALID_UTF8 = "invalid_utf8"
    INVALID_PATH = "invalid_path"
    UNREADABLE = "unreadable"


@dataclass(frozen=True)
class GitMetadataRules:
    """Canonical lines for one root metadata file.

    ``ordered_after`` captures the one line-presence rule that also depends on
    ordering: a gitignore exception must occur after the wildcard it negates.
    """

    filename: str
    required_rules: tuple[str, ...]
    ordered_after: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.filename not in {GITIGNORE, GITATTRIBUTES}:
            raise ValueError(
                "Git metadata is limited to root .gitignore/.gitattributes"
            )
        if len(set(self.required_rules)) != len(self.required_rules):
            raise ValueError("Git metadata rules must be unique")
        for rule in self.required_rules:
            if not rule or "\n" in rule or "\r" in rule:
                raise ValueError("Git metadata rules must be non-empty single lines")
        required = set(self.required_rules)
        if any(
            before not in required or after not in required
            for before, after in self.ordered_after
        ):
            raise ValueError("Ordered Git metadata rules must also be required rules")


@dataclass(frozen=True)
class GitMetadataInspection:
    """Inspection and append plan for one metadata file."""

    path: Path
    rules: GitMetadataRules
    state: GitMetadataState
    missing_rules: tuple[str, ...] = ()
    detail: str | None = None
    _text: str | None = field(default=None, repr=False, compare=False)

    @property
    def appendable(self) -> bool:
        return self.state is GitMetadataState.NEEDS_APPEND

    @property
    def append_block(self) -> str:
        """The exact marked block an init prompt can display and append."""

        if not self.missing_rules:
            return ""
        return f"{NAPFLOW_BLOCK_HEADER}\n" + "\n".join(self.missing_rules) + "\n"


class GitMetadataAppendError(RuntimeError):
    """The inspected metadata path is not safe to append."""


def gitignore_rules() -> GitMetadataRules:
    """Return F6's canonical rules for the current fixed ``envs/`` layout."""

    wildcard = "envs/*.env"
    template_exception = "!envs/example.env"
    return GitMetadataRules(
        GITIGNORE,
        (wildcard, template_exception, ".napflow/"),
        ((wildcard, template_exception),),
    )


def gitattributes_rules() -> GitMetadataRules:
    """Return napflow's canonical YAML line-ending attributes."""

    return GitMetadataRules(
        GITATTRIBUTES,
        ("*.yaml text eol=lf", "*.yml text eol=lf"),
    )


def default_git_metadata_rules() -> tuple[GitMetadataRules, GitMetadataRules]:
    """Return the two root-file rule sets in scaffold output order."""

    return (gitignore_rules(), gitattributes_rules())


def _missing_rules(text: str, rules: GitMetadataRules) -> tuple[str, ...]:
    # Git's metadata formats are line-oriented on LF. Other Unicode line
    # separators are ordinary pattern characters and must not create false
    # exact-line coverage.
    lines = text.split("\n")
    positions: dict[str, list[int]] = {}
    for index, line in enumerate(lines):
        positions.setdefault(line, []).append(index)

    missing = {rule for rule in rules.required_rules if rule not in positions}
    for before, after in rules.ordered_after:
        before_positions = positions.get(before)
        after_positions = positions.get(after)
        # If the wildcard itself will be appended, an exception already above
        # it must be re-added below it. The same repair applies when the last
        # existing wildcard currently follows the last exception.
        if not before_positions or not after_positions:
            if not before_positions:
                missing.add(after)
            continue
        if after_positions[-1] < before_positions[-1]:
            missing.add(after)

    return tuple(rule for rule in rules.required_rules if rule in missing)


def inspect_git_metadata(
    workspace_root: Path, rules: GitMetadataRules
) -> GitMetadataInspection:
    """Inspect one canonical file directly under ``workspace_root``.

    Symlinks and every non-regular path are reported without being followed.
    Existing files with any CR byte or invalid UTF-8 are never appendable.
    """

    path = Path(workspace_root) / rules.filename
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        return GitMetadataInspection(path, rules, GitMetadataState.MISSING)
    except OSError as error:
        return GitMetadataInspection(
            path,
            rules,
            GitMetadataState.UNREADABLE,
            detail=str(error),
        )

    if not stat.S_ISREG(path_stat.st_mode):
        kind = "symlink" if stat.S_ISLNK(path_stat.st_mode) else "non-regular path"
        return GitMetadataInspection(
            path,
            rules,
            GitMetadataState.INVALID_PATH,
            detail=kind,
        )

    try:
        raw = path.read_bytes()
    except OSError as error:
        return GitMetadataInspection(
            path,
            rules,
            GitMetadataState.UNREADABLE,
            detail=str(error),
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        return GitMetadataInspection(
            path,
            rules,
            GitMetadataState.INVALID_UTF8,
            detail=str(error),
        )

    # Coverage and line-ending policy are separate: canonical rules written
    # with CRLF are still recognizable for a precise warning, but the file is
    # never appendable until the owner converts it to LF.
    coverage_text = text.replace("\r\n", "\n").replace("\r", "\n")
    missing = _missing_rules(coverage_text, rules)
    if b"\r" in raw:
        state = GitMetadataState.NON_LF
    elif missing:
        state = GitMetadataState.NEEDS_APPEND
    else:
        state = GitMetadataState.COVERED
    return GitMetadataInspection(path, rules, state, missing, _text=text)


def _with_append_block(text: str, block: str) -> str:
    if not text:
        return block
    if text.endswith("\n\n"):
        return text + block
    if text.endswith("\n"):
        return text + "\n" + block
    return text + "\n\n" + block


def append_git_metadata(inspection: GitMetadataInspection) -> GitMetadataInspection:
    """Atomically append the current missing-rule block and re-inspect.

    Covered input is an idempotent no-op.  The file is re-inspected first so a
    path changed to a symlink/non-regular file after prompting is refused.
    Missing files remain the scaffold's responsibility.
    """

    current = inspect_git_metadata(inspection.path.parent, inspection.rules)
    if inspection.state is GitMetadataState.COVERED:
        if current.state is GitMetadataState.COVERED:
            return current
        raise GitMetadataAppendError(
            f"cannot append {current.path.name}: changed after inspection"
        )
    if inspection.state is not GitMetadataState.NEEDS_APPEND:
        raise GitMetadataAppendError(
            f"cannot append {current.path.name}: {current.state.value}"
        )
    if (
        current.state is not GitMetadataState.NEEDS_APPEND
        or current._text != inspection._text
        or current.missing_rules != inspection.missing_rules
    ):
        raise GitMetadataAppendError(
            f"cannot append {current.path.name}: changed after inspection"
        )
    if current.state is not GitMetadataState.NEEDS_APPEND or current._text is None:
        raise GitMetadataAppendError(
            f"cannot append {current.path.name}: {current.state.value}"
        )

    atomic_write_text(
        current.path,
        _with_append_block(current._text, current.append_block),
    )
    return inspect_git_metadata(current.path.parent, current.rules)
