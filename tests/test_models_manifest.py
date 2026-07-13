"""FR-101 (model half): napflow.yaml parses into Pydantic models; the
model defaults ARE the documented built-in defaults."""

import pytest
from pydantic import ValidationError

from napflow.core.models import Manifest


def test_spec_example_parses(load_yaml) -> None:
    manifest = Manifest.model_validate(load_yaml("napflow.yaml"))

    assert manifest.workspace is not None
    assert manifest.workspace.name == "qa-api-flows"
    assert manifest.flows.root == "flows"
    assert manifest.environments.default == "dev"
    assert manifest.environments.secrets == []
    assert manifest.defaults.request.headers["User-Agent"].startswith("napflow/0.1")
    assert manifest.defaults.run.report == "junit"
    assert manifest.defaults.run.run_timeout_s is None
    assert manifest.python.interpreter is None
    # codegen: parsed and preserved, but not modeled (FR-109)
    assert manifest.codegen == {"output": "generated/", "client_style": "niquests"}


def test_minimal_manifest_gets_documented_defaults() -> None:
    manifest = Manifest.model_validate({"schema": "napflow/v1"})

    assert manifest.flows.root == "flows"
    assert manifest.flows.main == "flows/main"
    assert manifest.environments.secrets == []
    assert manifest.defaults.request.timeout_s == 30
    assert manifest.defaults.request.verify_tls is True
    assert manifest.defaults.request.retry.max_attempts == 1
    assert manifest.defaults.run.history == 20
    assert manifest.defaults.run.report == "none"
    assert manifest.defaults.run.message_budget == 100_000
    assert manifest.defaults.run.node_timeout_s == 300
    assert manifest.defaults.run.run_timeout_s is None
    assert manifest.python.interpreter is None
    assert manifest.codegen is None


@pytest.mark.parametrize(
    "bad",
    [
        {},  # schema is required
        {"schema": "napflow/v2"},
        {"schema": "napflow/v1", "flowz": {}},  # unknown top-level key
        {"schema": "napflow/v1", "defaults": {"run": {"report": "xml"}}},
        {"schema": "napflow/v1", "defaults": {"run": {"message_budget": 0}}},
        {"schema": "napflow/v1", "defaults": {"run": {"body_capture_mb": 10}}},
        {"schema": "napflow/v1", "defaults": {"run": {"run_capture_mb": 500}}},
    ],
)
def test_rejected(bad: dict) -> None:
    with pytest.raises(ValidationError):
        Manifest.model_validate(bad)
