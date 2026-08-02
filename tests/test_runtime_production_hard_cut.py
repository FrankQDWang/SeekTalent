from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from fastapi.testclient import TestClient

from seektalent.config import AppSettings
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_ui.server import create_app
from seektalent_ui.runtime_execution import DEV_PROD_PARITY_DIFFERENCE_ALLOWLIST
from seektalent_workbench_v2.runtime_service import WorkbenchV2RuntimeService
from tests.settings_factory import make_settings


ROOT = Path(__file__).resolve().parents[1]


class _NoopRuntime:
    def __init__(self, _settings: AppSettings, *, source_registry: object | None = None) -> None:
        self.source_registry = source_registry

    async def run_async(self, **_kwargs: object) -> object:
        return object()


def _settings(tmp_path: Path, *, runtime_mode: str = "prod") -> AppSettings:
    return make_settings(
        workspace_root=str(tmp_path),
        runtime_mode=runtime_mode,
        liepin_worker_mode="disabled",
        liepin_browser_action_backend="disabled",
        liepin_api_token="production-test-api-token",
        liepin_account_binding_secret="production-test-binding-secret",
        liepin_stream_token_secret="production-test-stream-secret",
    )


def test_prod_composition_has_one_store_command_and_executor_identity(tmp_path: Path) -> None:
    app = create_app(settings=_settings(tmp_path), runtime_factory=_NoopRuntime)
    assert app.state.runtime_control_store is app.state.runtime_command_service.store
    assert app.state.runtime_control_store is app.state.workbench_v2_runtime_executor.store
    assert app.state.runtime_command_service is app.state.workbench_v2_runtime_executor.command_service
    assert app.state.workbench_v2_runtime_runner.executor is app.state.workbench_v2_runtime_executor
    assert not hasattr(app.state, "agent_conversation_service")
    assert not hasattr(app.state, "agent_memory_service")


@pytest.mark.parametrize("runtime_mode", ["dev", "prod"])
def test_dev_and_prod_share_one_runtime_composition_root(
    tmp_path: Path,
    runtime_mode: str,
) -> None:
    app = create_app(
        settings=_settings(tmp_path / runtime_mode, runtime_mode=runtime_mode),
        runtime_factory=_NoopRuntime,
        serve_frontend=runtime_mode == "prod",
    )

    assert app.state.runtime_control_store is app.state.runtime_command_service.store
    assert app.state.runtime_control_store is app.state.workbench_v2_runtime_executor.store
    assert app.state.runtime_command_service is (
        app.state.workbench_v2_runtime_executor.command_service
    )
    assert app.state.workbench_v2_runtime_runner.executor is (
        app.state.workbench_v2_runtime_executor
    )
    assert app.state.wtscli_lifecycle_supervisor is (
        app.state.workbench_v2_runtime_executor.wtscli_lifecycle_supervisor
    )


def test_dev_prod_difference_allowlist_excludes_runtime_and_source_execution() -> None:
    assert DEV_PROD_PARITY_DIFFERENCE_ALLOWLIST == {
        "frontend_delivery",
        "data_directory",
        "artifact_policy",
        "flywheel_policy",
        "installation_validation",
        "dev_diagnostics",
    }
    assert "runtime_composition" not in DEV_PROD_PARITY_DIFFERENCE_ALLOWLIST
    assert "source_operation_execution" not in DEV_PROD_PARITY_DIFFERENCE_ALLOWLIST


def test_source_execution_dispatch_is_registry_owned() -> None:
    runtime_composition = (ROOT / "src/seektalent/runtime/composition.py").read_text(
        encoding="utf-8"
    )
    runtime_orchestrator = (ROOT / "src/seektalent/runtime/orchestrator.py").read_text(
        encoding="utf-8"
    )
    runtime_executor = (
        ROOT / "src/seektalent_runtime_control/executor.py"
    ).read_text(encoding="utf-8")
    source_registry = (
        ROOT / "src/seektalent/source_adapters/registry.py"
    ).read_text(encoding="utf-8")
    source_adapters_facade = (
        ROOT / "src/seektalent/source_adapters/__init__.py"
    ).read_text(encoding="utf-8")
    provider_plugins = (
        ROOT / "src/seektalent/providers/plugins.py"
    ).read_text(encoding="utf-8")
    runtime_execution = (
        ROOT / "src/seektalent_ui/runtime_execution.py"
    ).read_text(encoding="utf-8")

    assert "source_lane_request_runner" not in runtime_composition
    assert "source_operation_executor" not in runtime_composition
    assert "source_lane_request_runner" not in runtime_orchestrator
    assert "source_operation_executor" not in runtime_orchestrator
    assert "RuntimeLike" not in runtime_executor
    assert "build_source_lane_request_runner" not in source_registry
    assert "build_source_lane_request_runner" not in source_adapters_facade
    assert "default_source_round_adapter_provider" not in source_adapters_facade
    assert "liepin_source_operation_executor" not in provider_plugins
    assert runtime_execution.count("WorkflowRuntimeExecutor(") == 1


def test_prod_openapi_exposes_only_v2_agent_surface(tmp_path: Path) -> None:
    app = create_app(settings=_settings(tmp_path), runtime_factory=_NoopRuntime)
    paths = set(app.openapi()["paths"])
    assert any(path.startswith("/api/agent/workbench/v2/") for path in paths)
    assert not any(path == "/api/agent/memory" or "/api/agent/memory/" in path for path in paths)
    assert not any(path.startswith("/api/agent/conversations") for path in paths)
    assert not any(path.startswith("/api/agent/workbench/conversations") for path in paths)


def test_legacy_agent_write_is_not_a_live_route(tmp_path: Path) -> None:
    app = create_app(settings=_settings(tmp_path), runtime_factory=_NoopRuntime)
    with TestClient(app) as client:
        response = client.post("/api/agent/conversations", json={"title": "legacy"})
    assert response.status_code == 404


def test_expired_unresolved_lane_blocks_before_run_creation() -> None:
    class Store:
        def get_browser_lane(self):
            return SimpleNamespace(
                status="active",
                last_failure_code="liepin_browser_lane_reconciliation_required",
                lease_expires_at="2020-01-01T00:00:00Z",
            )

    service = WorkbenchV2RuntimeService(store=Store(), now=lambda: "2020-01-01T00:00:01Z")
    with pytest.raises(RuntimeControlError, match="liepin_browser_lane_reconciliation_required"):
        service._ensure_browser_lane_admissible()
