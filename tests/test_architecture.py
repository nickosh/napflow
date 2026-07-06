"""NFR-01: napflow.core importable standalone — zero cli/server/UI imports.

Runs the import-linter contracts defined in pyproject.toml, so the same
rule holds locally and in CI. import-linter is a dev-only dependency:
the NFR-10 compat job (and any user running our suite in their own
pytest env) executes without it, so this module must skip cleanly, not
break collection.
"""

from pathlib import Path

import pytest

importlinter_cli = pytest.importorskip(
    "importlinter.cli",
    reason="import-linter is a dev dependency — not present in the"
    " NFR-10 compat env or user pytest envs",
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_core_imports_nothing_from_cli_or_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    assert importlinter_cli.lint_imports() == 0
