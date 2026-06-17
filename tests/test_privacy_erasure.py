from __future__ import annotations

import json
from pathlib import Path
import sqlite3


def test_candidate_subject_erasure_redacts_runtime_control_and_workbench_product_tables(tmp_path: Path) -> None:
    from seektalent.privacy_erasure import erase_candidate_subject
    from seektalent_runtime_control.models import RuntimeCheckpoint, RuntimeControlEventInput, RuntimeFinalSummary, RuntimeRunSnapshot
    from seektalent_ui.runtime_control_projection import RuntimeControlProjectionService
    from seektalent_runtime_control.store import RuntimeControlStore
    from seektalent_ui.runtime_workbench_bridge import RuntimeWorkbenchBridge
    from seektalent_ui.workbench_store import WorkbenchStore
    from tests.test_runtime_control_candidate_truth import _run_state_payload

    runtime_store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    runtime_store.initialize()
    _create_run(runtime_store)
    lease = runtime_store.acquire_executor_lease(
        runtime_run_id="runtime_run_erasure",
        executor_id="executor_erasure",
        acquired_at="2026-06-17T00:00:00.000000Z",
        lease_expires_at="2026-06-17T00:01:00.000000Z",
    )
    runtime_store.write_checkpoint(
        RuntimeCheckpoint(
            checkpoint_id="rtcheckpoint_erasure",
            runtime_run_id="runtime_run_erasure",
            stage="finalization",
            round_no=None,
            safe_boundary="runtime_candidate_checkpoint",
            run_state={
                **_run_state_payload(),
                "finalization_revisions": [
                    {
                        "revision": 1,
                        "runtime_run_id": "runtime_run_erasure",
                        "reason_code": "runtime_finalized",
                        "candidate_identity_ids": ["identity_1"],
                        "coverage_summary": {"status": "complete"},
                    }
                ],
            },
            source_plan={"sourceIds": ["cts"]},
            pending_commands=[],
            artifact_manifest_ref=None,
            schema_version="runtime-control-checkpoint/v1",
            created_at="2026-06-17T00:00:10.000000Z",
        ),
        executor_id="executor_erasure",
        attempt_no=lease.attempt_no,
    )
    runtime_store.append_event(
        RuntimeControlEventInput(
            event_id="rtevt_erasure_snapshot",
            runtime_run_id="runtime_run_erasure",
            event_type="runtime_run_completed",
            stage="finalization",
            round_no=None,
            source_id=None,
            status="completed",
            summary="Alice Chen completed",
            payload={"resumeId": "resume_1", "providerHash": "provider_hash_1"},
            workbench_event_global_seq=None,
            created_at="2026-06-17T00:00:10.500000Z",
        ),
        snapshot=RuntimeRunSnapshot(
            runtime_run_id="runtime_run_erasure",
            status="completed",
            current_stage="finalization",
            current_round=None,
            latest_event_seq=1,
            snapshot={
                "finalCandidates": [
                    {
                        "candidateId": "resume_1",
                        "displayName": "Alice Chen",
                        "rationale": "provider_hash_1 has strong platform fit",
                    }
                ]
            },
            updated_at="2026-06-17T00:00:10.500000Z",
        ),
    )
    runtime_store.save_final_summary(
        RuntimeFinalSummary(
            summary_id="rtfinalsummary_erasure",
            runtime_run_id="runtime_run_erasure",
            status="prepared",
            summary="Alice Chen should be contacted for resume_1.",
            facts=[{"label": "Candidate", "value": "Alice Chen: provider_hash_1"}],
            source_event_ids=["rtevt_erasure_snapshot"],
            source_snapshot_event_seq=1,
            latest_snapshot_event_seq=1,
            user_instruction="Focus on resume_1 and Alice Chen.",
            reason_code=None,
            created_at="2026-06-17T00:00:10.750000Z",
        ),
        idempotency_key="summary-erasure",
    )
    workbench_store = WorkbenchStore(tmp_path / "workbench.sqlite3")
    user = workbench_store.ensure_local_actor()
    session = workbench_store.create_workbench_session(
        user=user,
        job_title="Data Engineer",
        jd_text="Own data products.",
        notes="",
        source_kinds=["cts"],
        runtime_run_id="runtime_run_erasure",
    )
    runtime_store.link_workbench_session(
        runtime_run_id="runtime_run_erasure",
        workbench_session_id=session.session_id,
        updated_at="2026-06-17T00:00:11.000000Z",
    )
    RuntimeControlProjectionService(
        runtime_store=runtime_store,
        bridge=RuntimeWorkbenchBridge(runtime_store=runtime_store, workbench_store=workbench_store),
        user=user,
        now=lambda: "2026-06-17T00:00:12.000000Z",
    ).project_unprojected_candidate_truth(runtime_run_id="runtime_run_erasure")
    with sqlite3.connect(workbench_store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        evidence_row = conn.execute("SELECT evidence_id, review_item_id FROM candidate_evidence").fetchone()
        assert evidence_row is not None
        conn.execute(
            """
            INSERT INTO candidate_actions (
                action_id, tenant_id, workspace_id, user_id, session_id,
                review_item_id, action_kind, note, created_at
            )
            VALUES (
                'action_erasure', 'local', ?, ?, ?, ?, 'note',
                'Alice Chen note for resume_1 provider_hash_1', ?
            )
            """,
            (
                user.workspace_id,
                user.user_id,
                session.session_id,
                evidence_row["review_item_id"],
                "2026-06-17T00:00:12.250000Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO detail_open_requests (
                request_id, tenant_id, workspace_id, user_id, session_id, source_run_id,
                connection_id, candidate_evidence_id, review_item_id, provider_candidate_key_hash,
                detail_candidates_json, detail_open_mode, status, idempotency_key,
                blocked_reason, decision_note, ledger_id, decided_at, created_at, updated_at
            )
            VALUES (
                'dor_erasure', 'local', ?, ?, ?, 'source_run_erasure', 'conn_erasure',
                ?, ?, 'provider_hash_1',
                '[{"candidate_id":"resume_1","display_name":"Alice Chen","provider_candidate_key_hash":"provider_hash_1"}]',
                'human_confirm', 'pending', 'detail-erasure', 'blocked resume_1',
                'decision Alice Chen provider_hash_1', 'dol_erasure', NULL, ?, ?
            )
            """,
            (
                user.workspace_id,
                user.user_id,
                session.session_id,
                evidence_row["evidence_id"],
                evidence_row["review_item_id"],
                "2026-06-17T00:00:12.500000Z",
                "2026-06-17T00:00:12.500000Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO detail_open_ledger (
                ledger_id, tenant_id, workspace_id, actor_id, connection_id, source_run_id,
                request_id, candidate_evidence_id, provider_candidate_key_hash, status,
                budget_day, idempotency_key, lease_expires_at, opened_at, created_at, updated_at
            )
            VALUES (
                'dol_erasure', 'local', ?, ?, 'conn_erasure', 'source_run_erasure',
                'dor_erasure', ?, 'provider_hash_1', 'leased',
                '2026-06-17', 'ledger-erasure', '2026-06-17T00:05:00.000000Z',
                NULL, ?, ?
            )
            """,
            (
                user.workspace_id,
                user.user_id,
                evidence_row["evidence_id"],
                "2026-06-17T00:00:12.500000Z",
                "2026-06-17T00:00:12.500000Z",
            ),
        )

    result = erase_candidate_subject(
        resume_id="resume_1",
        erased_at="2026-06-17T00:00:13.000000Z",
        runtime_control_path=runtime_store.path,
        workbench_path=workbench_store.db_path,
    )

    assert result.total_count == 4
    identity = runtime_store.list_candidate_identities(runtime_run_id="runtime_run_erasure")[0]
    evidence = runtime_store.list_candidate_evidence(runtime_run_id="runtime_run_erasure")[0]
    assert identity.display_name == "Candidate erased"
    assert identity.canonical_resume_id == ""
    assert identity.merged_resume_ids == []
    assert identity.summary == ""
    assert identity.score is None
    assert evidence.provider_candidate_key_hash == ""
    assert evidence.resume_id == ""
    assert evidence.payload == {}

    with sqlite3.connect(workbench_store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        review = conn.execute("SELECT * FROM candidate_review_items").fetchone()
        workbench_evidence = conn.execute("SELECT * FROM candidate_evidence").fetchone()
    assert review["display_name"] == "Candidate erased"
    assert review["summary"] == ""
    assert review["aggregate_score"] is None
    assert workbench_evidence["provider_candidate_key_hash"] == ""
    assert workbench_evidence["resume_id"] == ""
    assert workbench_evidence["strengths_json"] == "[]"

    with sqlite3.connect(runtime_store.path) as conn:
        runtime_retained_text = json.dumps(
            [
                row[0]
                for row in conn.execute(
                    """
                    SELECT run_state_json FROM runtime_control_checkpoints WHERE runtime_run_id = ?
                    UNION ALL
                    SELECT snapshot_json FROM runtime_control_snapshots WHERE runtime_run_id = ?
                    UNION ALL
                    SELECT summary_json FROM runtime_control_final_summaries WHERE runtime_run_id = ?
                    UNION ALL
                    SELECT COALESCE(user_instruction, '') FROM runtime_control_final_summaries WHERE runtime_run_id = ?
                    """,
                    ("runtime_run_erasure", "runtime_run_erasure", "runtime_run_erasure", "runtime_run_erasure"),
                ).fetchall()
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
    with sqlite3.connect(workbench_store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        workbench_retained_text = json.dumps(
            [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT canonical_resume_id, merged_resume_ids_json, source_evidence_ids_json
                    FROM runtime_candidate_identity_snapshots
                    WHERE runtime_run_id = ?
                    UNION ALL
                    SELECT note, '', '' FROM candidate_actions
                    UNION ALL
                    SELECT provider_candidate_key_hash, COALESCE(detail_candidates_json, ''), COALESCE(decision_note, '')
                    FROM detail_open_requests
                    UNION ALL
                    SELECT provider_candidate_key_hash, '', ''
                    FROM detail_open_ledger
                    """,
                    ("runtime_run_erasure",),
                ).fetchall()
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
    retained_subject_text = runtime_retained_text + workbench_retained_text
    assert "resume_1" not in retained_subject_text
    assert "Alice Chen" not in retained_subject_text
    assert "provider_hash_1" not in retained_subject_text


def test_candidate_subject_erasure_tolerates_existing_unrelated_sqlite_files(tmp_path: Path) -> None:
    from seektalent.privacy_erasure import erase_candidate_subject

    runtime_path = tmp_path / "runtime.sqlite3"
    workbench_path = tmp_path / "workbench.sqlite3"
    for path in (runtime_path, workbench_path):
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE unrelated (id TEXT PRIMARY KEY)")

    result = erase_candidate_subject(
        resume_id="resume_missing",
        erased_at="2026-06-17T00:00:13.000000Z",
        runtime_control_path=runtime_path,
        workbench_path=workbench_path,
    )

    assert result.total_count == 0


def test_candidate_subject_erasure_matches_merged_resume_ids_exactly_when_value_contains_like_wildcards(
    tmp_path: Path,
) -> None:
    from seektalent.privacy_erasure import erase_candidate_subject

    runtime_path = tmp_path / "runtime.sqlite3"
    with sqlite3.connect(runtime_path) as conn:
        conn.executescript(
            """
            CREATE TABLE runtime_control_candidate_identities (
              runtime_run_id TEXT NOT NULL,
              identity_id TEXT NOT NULL,
              canonical_resume_id TEXT NOT NULL,
              merged_resume_ids_json TEXT NOT NULL,
              source_evidence_ids_json TEXT NOT NULL,
              display_name TEXT NOT NULL,
              title TEXT NOT NULL,
              company TEXT NOT NULL,
              location TEXT NOT NULL,
              summary TEXT NOT NULL,
              score INTEGER,
              fit_bucket TEXT,
              source_round INTEGER,
              payload_hash TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              PRIMARY KEY(runtime_run_id, identity_id)
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO runtime_control_candidate_identities (
              runtime_run_id, identity_id, canonical_resume_id, merged_resume_ids_json,
              source_evidence_ids_json, display_name, title, company, location, summary,
              score, fit_bucket, source_round, payload_hash, updated_at
            )
            VALUES (?, ?, ?, ?, '[]', ?, '', '', '', '', NULL, NULL, NULL, ?, ?)
            """,
            [
                (
                    "runtime_run_erasure",
                    "identity_exact",
                    "resume_other",
                    '["resume_%"]',
                    "Exact",
                    "hash_exact",
                    "2026-06-17T00:00:00.000000Z",
                ),
                (
                    "runtime_run_erasure",
                    "identity_wildcard_false_positive",
                    "resume_other",
                    '["resume_100"]',
                    "Keep",
                    "hash_keep",
                    "2026-06-17T00:00:00.000000Z",
                ),
            ],
        )

    result = erase_candidate_subject(
        resume_id="resume_%",
        erased_at="2026-06-17T00:00:13.000000Z",
        runtime_control_path=runtime_path,
    )

    assert result.runtime_candidate_identity_count == 1
    with sqlite3.connect(runtime_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = {
            row["identity_id"]: row
            for row in conn.execute(
                """
                SELECT identity_id, canonical_resume_id, merged_resume_ids_json, display_name, payload_hash
                FROM runtime_control_candidate_identities
                ORDER BY identity_id
                """
            )
        }
    assert rows["identity_exact"]["display_name"] == "Candidate erased"
    assert rows["identity_exact"]["merged_resume_ids_json"] == "[]"
    assert rows["identity_wildcard_false_positive"]["display_name"] == "Keep"
    assert rows["identity_wildcard_false_positive"]["merged_resume_ids_json"] == '["resume_100"]'


def _create_run(store) -> None:
    from seektalent_runtime_control.models import RuntimeRunRecord

    store.create_run(
        RuntimeRunRecord(
            runtime_run_id="runtime_run_erasure",
            run_intent_id="intent_erasure",
            start_idempotency_key="start_erasure",
            run_kind="primary",
            agent_conversation_id="agent_conv_erasure",
            workbench_session_id=None,
            approved_requirement_revision_id="reqapproved_erasure",
            status="running",
            current_stage="runtime",
            current_round=1,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["cts"],
            stop_reason_code=None,
            created_at="2026-06-17T00:00:00.000000Z",
            updated_at="2026-06-17T00:00:00.000000Z",
            completed_at=None,
        )
    )
