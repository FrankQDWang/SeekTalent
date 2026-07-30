from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3

from seektalent.support_bundle import create_execution_support_bundle
from seektalent.browser_bridge_manifest import (
    WTSCLI_BUILD_ID,
    WTSCLI_EXTENSION_ID,
    WTSCLI_FORK_COMMIT,
    WTSCLI_VERSION,
)
from seektalent.domi_bootstrap import (
    INSTALL_RECEIPT_RELATIVE_PATH,
    INSTALL_RECEIPT_SCHEMA,
)
from seektalent_runtime_control.models import RuntimeRunRecord, RuntimeRunSnapshot
from seektalent_runtime_control.store import RuntimeControlStore
from tests.settings_factory import make_settings


def test_support_bundle_is_allowlisted_local_and_private(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import hashlib

    monkeypatch.setenv("SEEKTALENT_INSTALL_HOME", str(tmp_path))
    settings = make_settings(
        workspace_root=str(tmp_path),
        runtime_mode="prod",
        artifacts_dir=str(tmp_path / "artifacts"),
    )
    store = RuntimeControlStore(settings.runtime_control_path)
    store.initialize()
    store.create_run(
        RuntimeRunRecord(
            runtime_run_id="runtime_support_1",
            run_intent_id="intent_support_1",
            start_idempotency_key="start_support_1",
            run_kind="primary",
            agent_conversation_id="agent_support_1",
            workbench_session_id=None,
            approved_requirement_revision_id="approved_support_1",
            status="queued",
            current_stage="queued",
            current_round=None,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["liepin"],
            stop_reason_code=None,
            created_at="2026-07-30T00:00:00.000000Z",
            updated_at="2026-07-30T00:00:00.000000Z",
            completed_at=None,
        )
    )
    store.record_component_health(
        component="runtime_runner",
        alive=True,
        last_heartbeat_at="2026-07-30T00:00:01.000000Z",
        last_success_at=None,
        first_failure_at="2026-07-30T00:00:01.000000Z",
        first_failure_type="KeyError",
        failure_count=1,
        restart_count=2,
        observed_at="2026-07-30T00:00:01.000000Z",
    )
    store.record_execution_failure(
        runtime_run_id="runtime_support_1",
        component="runtime_worker",
        boundary="execute_claimed_run",
        safe_reason_code="runtime_worker_failed",
        error=KeyError("PRIVATE_FIRST_CAUSE_SENTINEL"),
        failure_role="primary",
        occurred_at="2026-07-30T00:00:02.000000Z",
    )
    restarted = RuntimeControlStore(settings.runtime_control_path)
    restarted.initialize()
    assert restarted.list_execution_failures()[0].exception_type == (
        "KeyError"
    )
    receipt = tmp_path / INSTALL_RECEIPT_RELATIVE_PATH
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps(
            {
                "schemaVersion": INSTALL_RECEIPT_SCHEMA,
                "productVersion": "0.8.0rc1",
                "sourceRevision": "a" * 40,
                "productBuildId": "seektalent-0.8.0rc1+" + "a" * 40,
                "wheelSha256": "b" * 64,
                "deliveryManifestSha256": "c" * 64,
                "bridgeBuildId": WTSCLI_BUILD_ID,
                "wtscliVersion": WTSCLI_VERSION,
                "wtscliForkCommit": WTSCLI_FORK_COMMIT,
                "extensionVersion": WTSCLI_VERSION,
                "extensionIdSha256": hashlib.sha256(
                    WTSCLI_EXTENSION_ID.encode()
                ).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    snapshot = RuntimeRunSnapshot(
            runtime_run_id="runtime_support_1",
            status="queued",
            current_stage="queued",
            current_round=None,
            latest_event_seq=0,
            snapshot={
                "workflowInput": {
                    "jdText": "PRIVATE_JD_SENTINEL",
                    "notes": "PRIVATE_BUSINESS_SENTINEL",
                }
            },
            updated_at="2026-07-30T00:00:00.000000Z",
        )
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """
            INSERT INTO runtime_control_snapshots (
              runtime_run_id, status, current_stage, current_round,
              latest_event_seq, snapshot_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.runtime_run_id,
                snapshot.status,
                snapshot.current_stage,
                snapshot.current_round,
                snapshot.latest_event_seq,
                json.dumps(snapshot.snapshot),
                snapshot.updated_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO runtime_control_events (
              event_id, runtime_run_id, event_seq, event_type, stage,
              round_no, source_id, status, summary, payload_json,
              visibility, idempotency_key, created_at
            )
            VALUES (?, ?, 1, 'runtime_run_queued', 'queued', NULL, NULL,
                    'queued', ?, ?, 'internal', NULL, ?)
            """,
            (
                "event_support_1",
                "runtime_support_1",
                "RAW_STDERR_SENTINEL",
                json.dumps({"dom": "PRIVATE_DOM_SENTINEL"}),
                "2026-07-30T00:00:00.000000Z",
            ),
        )

    path = create_execution_support_bundle(
        settings=settings,
        runtime_run_id="runtime_support_1",
        now=lambda: datetime(2026, 7, 30, tzinfo=UTC),
    )
    text = path.read_text()
    payload = json.loads(text)

    assert path.parent.parent == (
        settings.artifacts_path / "support-bundles"
    )
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert payload["privacy"] == {
        "allowlistOnly": True,
        "automaticUpload": False,
        "includesBusinessText": False,
        "includesDom": False,
        "includesRawStderr": False,
    }
    assert payload["runtimeControl"]["runs"][0]["runtime_run_id"] == (
        "runtime_support_1"
    )
    assert payload["runtimeControl"]["componentHealth"] == [
        {
            "alive": 1,
            "component": "runtime_runner",
            "failure_count": 1,
            "first_failure_at": "2026-07-30T00:00:01.000000Z",
            "first_failure_type": "KeyError",
            "last_heartbeat_at": "2026-07-30T00:00:01.000000Z",
            "last_success_at": None,
            "observed_at": "2026-07-30T00:00:01.000000Z",
            "restart_count": 2,
        }
    ]
    assert payload["runtimeControl"]["phaseDurations"][0][
        "durationSeconds"
    ] == 0.0
    assert payload["executionIdentity"]["receiptAvailable"] is True
    assert payload["executionIdentity"]["receipt"]["sourceRevision"] == (
        "a" * 40
    )
    assert payload["runtimeControl"]["executionFailures"][0][
        "safe_reason_code"
    ] == "runtime_worker_failed"
    assert "PRIVATE_FIRST_CAUSE_SENTINEL" not in text
    for forbidden in (
        "PRIVATE_JD_SENTINEL",
        "PRIVATE_BUSINESS_SENTINEL",
        "RAW_STDERR_SENTINEL",
        "PRIVATE_DOM_SENTINEL",
    ):
        assert forbidden not in text


def test_support_bundle_does_not_chmod_callers_existing_directory(
    tmp_path: Path,
) -> None:
    settings = make_settings(workspace_root=str(tmp_path))
    RuntimeControlStore(settings.runtime_control_path).initialize()
    caller_directory = tmp_path / "shared-output"
    caller_directory.mkdir(mode=0o755)

    path = create_execution_support_bundle(
        settings=settings,
        output_dir=caller_directory,
        now=lambda: datetime(2026, 7, 30, tzinfo=UTC),
    )

    assert caller_directory.stat().st_mode & 0o777 == 0o755
    assert path.parent.parent == caller_directory
    assert path.parent.stat().st_mode & 0o777 == 0o700
