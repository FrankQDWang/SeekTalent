from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from seektalent.config import AppSettings
from seektalent_conversation_agent.factory import build_agent_service
from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
from seektalent_ui.server import create_app
from seektalent_ui.workbench_paths import workbench_db_path
from tests.settings_factory import make_settings


class _NoopRuntime:
    def __init__(
        self,
        _settings: AppSettings,
        *,
        source_operation_executor: object | None = None,
    ) -> None:
        self.source_operation_executor = source_operation_executor

    async def run_async(self, **_kwargs: object) -> object:
        return object()

    def extract_requirements(self, **_kwargs: object) -> object:
        raise AssertionError("request must not reach the legacy runtime")


def test_prod_composition_uses_one_runtime_execution_authority(
    tmp_path: Path,
) -> None:
    settings = make_settings(
        workspace_root=str(tmp_path),
        runtime_mode="prod",
        liepin_worker_mode="disabled",
        liepin_browser_action_backend="disabled",
        liepin_api_token="production-test-api-token",
        liepin_account_binding_secret="production-test-binding-secret",
        liepin_stream_token_secret="production-test-stream-secret",
    )
    app = create_app(settings=settings, runtime_factory=_NoopRuntime)
    adapter = (
        app.state.agent_conversation_service.service_action_adapter
    )
    runtime_service = app.state.workbench_v2_service.runtime_service

    assert isinstance(adapter.workflow_executor, WorkflowRuntimeExecutor)
    assert app.state.workbench_v2_runtime_executor is adapter.workflow_executor
    assert app.state.workbench_v2_runtime_runner.executor is adapter.workflow_executor
    assert runtime_service._runtime_executor is adapter.workflow_executor
    assert app.state.runtime_control_store is adapter.runtime_store
    assert adapter.workflow_executor.store is adapter.runtime_store
    assert app.state.runtime_command_service is adapter.command_service
    assert runtime_service.command_service is adapter.command_service
    assert adapter.command_service.store is adapter.runtime_store
    assert app.state.workbench_job_runner is None


def test_prod_legacy_write_returns_410_without_database_mutation(
    tmp_path: Path,
) -> None:
    settings = make_settings(
        workspace_root=str(tmp_path),
        runtime_mode="prod",
        liepin_worker_mode="disabled",
        liepin_browser_action_backend="disabled",
        liepin_api_token="production-test-api-token",
        liepin_account_binding_secret="production-test-binding-secret",
        liepin_stream_token_secret="production-test-stream-secret",
    )
    app = create_app(settings=settings, runtime_factory=_NoopRuntime)
    before = _table_counts(workbench_db_path(settings))

    response = TestClient(
        app,
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
    ).post(
        "/api/workbench/sessions",
        json={
            "jobTitle": "Backend Engineer",
            "jdText": "Python",
            "notes": "",
        },
    )

    assert response.status_code == 410
    assert response.json()["reasonCode"] == (
        "legacy_workbench_execution_removed"
    )
    assert _table_counts(workbench_db_path(settings)) == before


def test_source_operation_is_injected_during_runtime_construction(
    tmp_path: Path,
) -> None:
    settings = make_settings(
        workspace_root=str(tmp_path),
        liepin_worker_mode="disabled",
        liepin_browser_action_backend="disabled",
    )
    service = build_agent_service(
        settings=settings,
        runtime_factory=_NoopRuntime,
    )
    executor = service.service_action_adapter.workflow_executor
    assert executor is not None
    operation_executor = object()

    runtime = executor._build_runtime(  # noqa: SLF001
        source_operation_executor=operation_executor,
    )

    assert isinstance(runtime, _NoopRuntime)
    assert runtime.source_operation_executor is operation_executor
    source = inspect.getsource(WorkflowRuntimeExecutor.execute_claimed_run)
    assert "isinstance(runtime, WorkflowRuntime)" not in source
    assert "runtime.source_operation_executor =" not in source


def _table_counts(path: Path) -> dict[str, int]:
    with sqlite3.connect(path) as connection:
        tables = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()
        return {
            str(name): int(
                connection.execute(
                    f'SELECT COUNT(*) FROM "{name}"'
                ).fetchone()[0]
            )
            for (name,) in tables
        }
