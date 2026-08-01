from __future__ import annotations

from seektalent_runtime_control.store import RuntimeControlStore
from tests.settings_factory import make_settings


def test_runtime_runner_health_preserves_first_cause_across_store_reopen(tmp_path) -> None:
    settings = make_settings(workspace_root=str(tmp_path))
    store = RuntimeControlStore(settings.runtime_control_path)
    store.initialize()
    store.record_component_health(
        component="runtime_runner",
        alive=False,
        last_heartbeat_at="2026-07-30T00:00:01.000000Z",
        last_success_at=None,
        first_failure_at="2026-07-30T00:00:01.000000Z",
        first_failure_type="KeyError",
        failure_count=1,
        restart_count=4,
        observed_at="2026-07-30T00:00:01.000000Z",
    )
    store.record_component_health(
        component="runtime_runner",
        alive=True,
        last_heartbeat_at="2026-07-30T00:00:02.000000Z",
        last_success_at=None,
        first_failure_at=None,
        first_failure_type=None,
        failure_count=0,
        restart_count=0,
        observed_at="2026-07-30T00:00:02.000000Z",
    )

    reopened = RuntimeControlStore(settings.runtime_control_path)
    reopened.initialize()
    health = reopened.get_component_health("runtime_runner")
    assert health is not None
    assert health.first_failure_type == "KeyError"
    assert health.failure_count == 1
    assert health.restart_count == 4


def test_runtime_control_store_is_the_supervisor_health_authority(tmp_path) -> None:
    settings = make_settings(workspace_root=str(tmp_path))
    store = RuntimeControlStore(settings.runtime_control_path)
    store.initialize()
    store.record_component_health(
        component="runtime_runner",
        alive=True,
        last_heartbeat_at="2026-07-30T00:00:02.000000Z",
        last_success_at="2026-07-30T00:00:02.000000Z",
        first_failure_at=None,
        first_failure_type=None,
        failure_count=0,
        restart_count=0,
        observed_at="2026-07-30T00:00:02.000000Z",
    )
    health = store.get_component_health("runtime_runner")
    assert health is not None
    assert health.alive is True
    assert health.last_success_at == "2026-07-30T00:00:02.000000Z"
