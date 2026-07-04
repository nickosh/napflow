from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

DATA_DIR = Path(__file__).resolve().parent / "data"


@pytest.fixture(scope="session")
def load_yaml():
    yaml = YAML(typ="safe")

    def _load(name: str) -> Any:
        with (DATA_DIR / name).open(encoding="utf-8") as f:
            return yaml.load(f)

    return _load
