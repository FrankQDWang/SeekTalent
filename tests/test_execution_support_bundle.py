from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3

from seektalent.support_bundle import create_execution_support_bundle
from seektalent_runtime_control.models import RuntimeRunRecord, RuntimeRunSnapshot
from seektalent_runtime_control.store import RuntimeControlStore
from tests.settings_factory import make_settings


def test_support_bundle_is_allowlisted_local_and_private(tmp_path: Path) -> None:
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

    assert path.parent == settings.artifacts_path / "support-bundles"
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
    for forbidden in (
        "PRIVATE_JD_SENTINEL",
        "PRIVATE_BUSINESS_SENTINEL",
        "RAW_STDERR_SENTINEL",
        "PRIVATE_DOM_SENTINEL",
    ):
        assert forbidden not in text
