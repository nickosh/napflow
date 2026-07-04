"""NFR-01: napflow.core importable standalone — zero cli/server/UI imports.

Runs the import-linter contracts defined in pyproject.toml, so the same
rule holds locally and in CI.
"""

from pathlib import Path

import pytest
from importlinter.cli import lint_imports

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_core_imports_nothing_from_cli_or_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    assert lint_imports() == 0
