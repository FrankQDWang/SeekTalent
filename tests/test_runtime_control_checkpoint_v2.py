from __future__ import annotations

import json
import sqlite3

import pytest

import seektalent_runtime_control.store as store_module
from seektalent.models import RunState
from seektalent.source_contracts.detail_open_claims import DetailOpenClaimLedger
from seektalent_runtime_control.checkpoint_v2 import (
    RUNTIME_CHECKPOINT_SCHEMA_V2,
    checkpoint_projection,
)
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.models import RuntimeCheckpoint
from seektalent_runtime_control.recovery_state import RecoveryStateAssembler

from tests.test_runtime_multi_source_round_dispatch import _run_state


def test_checkpoint_v2_projection_excludes_durable_candidate_truth() -> None:
    state = _run_state()
    projection = checkpoint_projection(state)

    assert projection.schema_version == RUNTIME_CHECKPOINT_SCHEMA_V2
    assert set(projection.control_state).isdisjoint(
        {
            "candidate_store",
            "normalized_store",
            "source_evidence_by_resume_id",
            "source_evidence_by_identity_id",
            "candidate_identity_by_resume_id",
            "candidate_identities",
            "identity_aliases_by_canonical_id",
            "identity_conflicts",
            "canonical_resume_by_identity_id",
            "scorecards_by_resume_id",
            "detail_open_claims_by_provider_key",
            "round_history",
            "runtime_source_lane_results",
            "finalization_revisions",
        }
    )
    assert "candidate_store" in projection.candidate_state
    assert projection.control_state_hash
    assert projection.payload_size_bytes < len(
        json.dumps(state.model_dump(mode="json"), ensure_ascii=False).encode()
    )


def test_detail_claim_change_does_not_dump_run_state(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _run_state()
    snapshots: list[dict[str, object]] = []

    def fail_dump(*args: object, **kwargs: object) -> object:
        raise AssertionError("detail claim must not serialize RunState")

    monkeypatch.setattr(RunState, "model_dump", fail_dump)
    ledger = DetailOpenClaimLedger(
        state.detail_open_claims_by_provider_key,
        checkpoint=lambda: snapshots.append(
            {
                key: value.model_dump(mode="json")
                for key, value in ledger.snapshot().items()
            }
        ),
    )

    assert ledger.try_claim("candidate-key") is True
    assert snapshots[-1]["candidate-key"] == {
        "status": "claimed",
        "browser_open_attempt_count": 0,
        "last_safe_reason_code": None,
    }


def test_detail_claim_revision_is_independent_cas(tmp_path) -> None:
    store = _seed_running_store(tmp_path)
    claims = {
        "candidate-key": {
            "status": "claimed",
            "browser_open_attempt_count": 0,
            "last_safe_reason_code": None,
        }
    }

    revision, payload_hash = store.write_detail_claim_snapshot(
        runtime_run_id="runtime_run_1",
        claims=claims,
        expected_revision=0,
        updated_at="2026-07-28T00:00:02.000000Z",
    )

    assert revision == 1
    assert payload_hash
    assert store.get_run("runtime_run_1").latest_checkpoint_id is None
    with pytest.raises(
        RuntimeControlError,
        match="runtime_detail_claim_revision_conflict",
    ):
        store.write_detail_claim_snapshot(
            runtime_run_id="runtime_run_1",
            claims={},
            expected_revision=0,
            updated_at="2026-07-28T00:00:03.000000Z",
        )


def test_v2_store_rejects_unregistered_safe_boundary(tmp_path) -> None:
    store = _seed_running_store(tmp_path)
    projection = checkpoint_projection(_run_state())

    with pytest.raises(RuntimeControlError, match="runtime_checkpoint_safe_boundary_unregistered"):
        store.write_checkpoint_v2(
            checkpoint_id="checkpoint-v2-invalid",
            runtime_run_id="runtime_run_1",
            executor_id="executor-1",
            attempt_no=1,
            stage="round",
            round_no=1,
            safe_boundary="after_arbitrary_callback",
            accepted_requirement_revision_id="approved-1",
            source_ids=["liepin"],
            projection=projection,
            detail_claim_revision=0,
            detail_claim_hash=None,
            created_at="2026-07-28T00:00:02.000000Z",
        )


def test_v2_checkpoint_candidate_revision_and_latest_pointer_are_atomic(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _seed_running_store(tmp_path)
    projection = checkpoint_projection(_run_state())

    def fail_latest_pointer(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("fault_latest_pointer")

    monkeypatch.setattr(store, "_update_checkpoint_pointer", fail_latest_pointer)
    with pytest.raises(sqlite3.OperationalError, match="fault_latest_pointer"):
        store.write_checkpoint_v2(
            checkpoint_id="checkpoint-v2-atomic",
            runtime_run_id="runtime_run_1",
            executor_id="executor-1",
            attempt_no=1,
            stage="round",
            round_no=1,
            safe_boundary="after_round_controller",
            accepted_requirement_revision_id="approved-1",
            source_ids=["liepin"],
            projection=projection,
            detail_claim_revision=0,
            detail_claim_hash=None,
            created_at="2026-07-28T00:00:02.000000Z",
        )

    with store._connect() as conn:
        assert conn.execute(
            "SELECT 1 FROM runtime_control_checkpoints WHERE checkpoint_id = ?",
            ("checkpoint-v2-atomic",),
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM runtime_control_candidate_truth_state WHERE runtime_run_id = ?",
            ("runtime_run_1",),
        ).fetchone() is None
        assert conn.execute(
            "SELECT latest_checkpoint_id FROM runtime_control_runs WHERE runtime_run_id = ?",
            ("runtime_run_1",),
        ).fetchone()[0] is None


def test_recovery_state_assembler_is_deterministic(tmp_path) -> None:
    store = _seed_running_store(tmp_path)
    state = _run_state()
    projection = checkpoint_projection(state)
    checkpoint = store.write_checkpoint_v2(
        checkpoint_id="checkpoint-v2-recovery",
        runtime_run_id="runtime_run_1",
        executor_id="executor-1",
        attempt_no=1,
        stage="round",
        round_no=1,
        safe_boundary="after_round_controller",
        accepted_requirement_revision_id="approved-1",
        source_ids=["liepin"],
        projection=projection,
        detail_claim_revision=0,
        detail_claim_hash=None,
        created_at="2026-07-28T00:00:02.000000Z",
    )

    assembler = RecoveryStateAssembler(store)
    first = assembler.assemble(checkpoint)
    second = assembler.assemble(checkpoint)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.candidate_store == state.candidate_store
    assert first.normalized_store == state.normalized_store
    assert first.scorecards_by_resume_id == state.scorecards_by_resume_id
    assert first.candidate_identities == state.candidate_identities
    assert first.detail_open_claims_by_provider_key == (
        state.detail_open_claims_by_provider_key
    )
    assert first.round_history == state.round_history
    assert first.runtime_source_lane_results == (
        state.runtime_source_lane_results
    )
    assert first.finalization_revisions == state.finalization_revisions


def test_recovery_never_falls_back_to_an_older_checkpoint(tmp_path) -> None:
    store = _seed_running_store(tmp_path)
    projection = checkpoint_projection(_run_state())
    older = store.write_checkpoint_v2(
        checkpoint_id="checkpoint-v2-older",
        runtime_run_id="runtime_run_1",
        executor_id="executor-1",
        attempt_no=1,
        stage="round",
        round_no=1,
        safe_boundary="after_source_result_commit",
        accepted_requirement_revision_id="approved-1",
        source_ids=["liepin"],
        projection=projection,
        detail_claim_revision=0,
        detail_claim_hash=None,
        created_at="2026-07-28T00:00:02.000000Z",
    )
    latest = store.write_checkpoint_v2(
        checkpoint_id="checkpoint-v2-latest",
        runtime_run_id="runtime_run_1",
        executor_id="executor-1",
        attempt_no=1,
        stage="round",
        round_no=1,
        safe_boundary="after_round_controller",
        accepted_requirement_revision_id="approved-1",
        source_ids=["liepin"],
        projection=projection,
        detail_claim_revision=0,
        detail_claim_hash=None,
        created_at="2026-07-28T00:00:03.000000Z",
    )

    assembler = RecoveryStateAssembler(store)
    with pytest.raises(
        RuntimeControlError,
        match="runtime_checkpoint_not_latest",
    ):
        assembler.assemble(older)
    assert assembler.assemble(latest).candidate_store == (
        _run_state().candidate_store
    )


def test_source_result_commit_advances_durable_revision_without_candidate_change(
    tmp_path,
) -> None:
    store = _seed_running_store(tmp_path)
    state = _run_state()
    first_payload = state.model_dump(mode="json")
    first_payload["runtime_source_lane_results"] = []
    source_results = [
        {
            "source_kind": "liepin",
            "round_no": 1,
            "status": "completed",
        }
    ]
    second_payload = state.model_dump(mode="json")
    second_payload["runtime_source_lane_results"] = source_results
    first = store.write_checkpoint_v2(
        checkpoint_id="checkpoint-source-before",
        runtime_run_id="runtime_run_1",
        executor_id="executor-1",
        attempt_no=1,
        stage="round",
        round_no=1,
        safe_boundary="after_source_result_commit",
        accepted_requirement_revision_id="approved-1",
        source_ids=["liepin"],
        projection=checkpoint_projection(first_payload),
        detail_claim_revision=0,
        detail_claim_hash=None,
        created_at="2026-07-28T00:00:02.000000Z",
    )
    second = store.write_checkpoint_v2(
        checkpoint_id="checkpoint-source-after",
        runtime_run_id="runtime_run_1",
        executor_id="executor-1",
        attempt_no=1,
        stage="round",
        round_no=1,
        safe_boundary="after_source_result_commit",
        accepted_requirement_revision_id="approved-1",
        source_ids=["liepin"],
        projection=checkpoint_projection(second_payload),
        detail_claim_revision=0,
        detail_claim_hash=None,
        created_at="2026-07-28T00:00:03.000000Z",
    )

    assert second.candidate_truth_hash == first.candidate_truth_hash
    assert second.candidate_truth_revision == (
        first.candidate_truth_revision + 1
    )
    assert RecoveryStateAssembler(store).assemble(
        second
    ).runtime_source_lane_results == source_results


def test_new_checkpoint_write_is_v2_even_for_legacy_input(tmp_path) -> None:
    store = _seed_running_store(tmp_path)
    legacy = RuntimeCheckpoint(
        checkpoint_id="checkpoint-v1-import",
        runtime_run_id="runtime_run_1",
        stage="round",
        round_no=1,
        safe_boundary="after_round_controller",
        run_state={"round": 1},
        source_plan={"sourceIds": ["liepin"]},
        pending_commands=[],
        artifact_manifest_ref=None,
        schema_version="runtime-control-checkpoint/v1",
        created_at="2026-07-28T00:00:02.000000Z",
    )

    stored = store.write_checkpoint(legacy, executor_id="executor-1", attempt_no=1)

    assert stored.schema_version == RUNTIME_CHECKPOINT_SCHEMA_V2
    with store._connect() as conn:
        assert conn.execute(
            "SELECT schema_version FROM runtime_control_checkpoints WHERE checkpoint_id = ?",
            ("checkpoint-v1-import",),
        ).fetchone()[0] == RUNTIME_CHECKPOINT_SCHEMA_V2


def test_terminal_compaction_is_idempotent_and_retains_one_manifest(tmp_path) -> None:
    store = _seed_running_store(tmp_path)
    projection = checkpoint_projection(_run_state())
    for index, boundary in enumerate(
        ("after_source_result_commit", "after_round_controller"),
        start=1,
    ):
        store.write_checkpoint_v2(
            checkpoint_id=f"checkpoint-v2-{index}",
            runtime_run_id="runtime_run_1",
            executor_id="executor-1",
            attempt_no=1,
            stage="round",
            round_no=1,
            safe_boundary=boundary,
            accepted_requirement_revision_id="approved-1",
            source_ids=["liepin"],
            projection=projection,
            detail_claim_revision=0,
            detail_claim_hash=None,
            created_at=f"2026-07-28T00:00:0{index + 1}.000000Z",
        )
    from seektalent_runtime_control.models import RuntimeControlEventInput

    store.append_executor_event(
        RuntimeControlEventInput(
            event_id="terminal-event",
            runtime_run_id="runtime_run_1",
            event_type="runtime_run_completed",
            stage="finalization",
            round_no=None,
            source_id=None,
            status="completed",
            summary="completed",
            payload={},
            idempotency_key=None,
            workbench_event_global_seq=None,
            created_at="2026-07-28T00:00:10.000000Z",
        ),
        executor_id="executor-1",
        attempt_no=1,
        run_status="completed",
        completed_at="2026-07-28T00:00:10.000000Z",
    )

    first = store.compact_terminal_checkpoints(runtime_run_id="runtime_run_1")
    second = store.compact_terminal_checkpoints(runtime_run_id="runtime_run_1")

    assert first.checkpoint_count == 1
    assert second == first
    assert first.checkpoint_bytes <= 512 * 1024
    with store._connect() as conn:
        rows = conn.execute(
            "SELECT is_final_manifest FROM runtime_control_checkpoints WHERE runtime_run_id = ?",
            ("runtime_run_1",),
        ).fetchall()
    assert [row[0] for row in rows] == [1]


@pytest.mark.parametrize(
    "safe_boundary",
    [
        "before_source_dispatch",
        "after_source_result_commit",
        "runtime_candidate_checkpoint",
        "after_round_controller",
        "before_finalization",
        "after_finalization_commit",
        "entering_pause",
        "entering_needs_attention",
    ],
)
def test_each_v2_safe_boundary_rolls_back_before_latest_pointer(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    safe_boundary: str,
) -> None:
    store = _seed_running_store(tmp_path)
    projection = checkpoint_projection(_run_state())

    def fail_pointer(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("fault_latest_pointer")

    monkeypatch.setattr(store, "_update_checkpoint_pointer", fail_pointer)
    with pytest.raises(sqlite3.OperationalError, match="fault_latest_pointer"):
        store.write_checkpoint_v2(
            checkpoint_id=f"checkpoint-fault-{safe_boundary}",
            runtime_run_id="runtime_run_1",
            executor_id="executor-1",
            attempt_no=1,
            stage="round",
            round_no=1,
            safe_boundary=safe_boundary,
            accepted_requirement_revision_id="approved-1",
            source_ids=["liepin"],
            projection=projection,
            detail_claim_revision=0,
            detail_claim_hash=None,
            created_at="2026-07-28T00:00:02.000000Z",
        )

    with store._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM runtime_control_checkpoints"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM runtime_control_candidate_truth_state"
        ).fetchone()[0] == 0


def test_candidate_commit_fault_rolls_back_checkpoint_and_truth(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _seed_running_store(tmp_path)

    def fail_candidate_commit(*args: object, **kwargs: object) -> object:
        raise sqlite3.OperationalError("fault_candidate_commit")

    monkeypatch.setattr(
        store_module,
        "_sync_candidate_truth_v2",
        fail_candidate_commit,
    )
    with pytest.raises(sqlite3.OperationalError, match="fault_candidate_commit"):
        store.write_checkpoint_v2(
            checkpoint_id="checkpoint-candidate-fault",
            runtime_run_id="runtime_run_1",
            executor_id="executor-1",
            attempt_no=1,
            stage="round",
            round_no=1,
            safe_boundary="after_source_result_commit",
            accepted_requirement_revision_id="approved-1",
            source_ids=["liepin"],
            projection=checkpoint_projection(_run_state()),
            detail_claim_revision=0,
            detail_claim_hash=None,
            created_at="2026-07-28T00:00:02.000000Z",
        )

    assert store.get_run("runtime_run_1").latest_checkpoint_id is None


def test_terminal_compaction_fault_is_retryable(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _seed_running_store(tmp_path)
    checkpoint = store.write_checkpoint_v2(
        checkpoint_id="checkpoint-before-terminal",
        runtime_run_id="runtime_run_1",
        executor_id="executor-1",
        attempt_no=1,
        stage="round",
        round_no=1,
        safe_boundary="after_round_controller",
        accepted_requirement_revision_id="approved-1",
        source_ids=["liepin"],
        projection=checkpoint_projection(_run_state()),
        detail_claim_revision=0,
        detail_claim_hash=None,
        created_at="2026-07-28T00:00:02.000000Z",
    )
    _complete_run(store)
    original = store_module.write_checkpoint_participant

    def fail_manifest(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("fault_terminal_compaction")

    monkeypatch.setattr(store_module, "write_checkpoint_participant", fail_manifest)
    with pytest.raises(sqlite3.OperationalError, match="fault_terminal_compaction"):
        store.compact_terminal_checkpoints(runtime_run_id="runtime_run_1")
    assert (
        store.get_latest_checkpoint(runtime_run_id="runtime_run_1").checkpoint_id
        == checkpoint.checkpoint_id
    )

    monkeypatch.setattr(store_module, "write_checkpoint_participant", original)
    restarted = store_module.RuntimeControlStore(store.path)
    restarted.initialize()
    metrics = restarted.checkpoint_storage_metrics(
        runtime_run_id="runtime_run_1"
    )
    assert metrics["checkpointCount"] == 1
    assert restarted.get_latest_checkpoint(
        runtime_run_id="runtime_run_1"
    ).is_final_manifest


def test_representative_twenty_candidate_run_stays_within_checkpoint_budgets(
    tmp_path,
) -> None:
    store = _seed_running_store(tmp_path)
    payload = _run_state().model_dump(mode="json")
    payload["candidate_store"] = {
        f"resume-{index}": {
            "resume_id": f"resume-{index}",
            "dedup_key": f"dedup-{index}",
            "raw": {"safeText": "x" * 10_000},
        }
        for index in range(20)
    }
    payload["normalized_store"] = {
        f"resume-{index}": {"candidate_name": f"Candidate {index}"}
        for index in range(20)
    }
    projection = checkpoint_projection(payload)
    boundaries = [
        boundary
        for _round in range(5)
        for boundary in (
            "after_source_result_commit",
            "after_round_controller",
        )
    ] + ["before_finalization", "after_finalization_commit"]
    for index, boundary in enumerate(boundaries, start=1):
        round_no = min((index + 1) // 2, 5) if index <= 10 else None
        store.write_checkpoint_v2(
            checkpoint_id=f"checkpoint-budget-{index}",
            runtime_run_id="runtime_run_1",
            executor_id="executor-1",
            attempt_no=1,
            stage="round" if round_no is not None else "finalization",
            round_no=round_no,
            safe_boundary=boundary,
            accepted_requirement_revision_id="approved-1",
            source_ids=["liepin"],
            projection=projection,
            detail_claim_revision=0,
            detail_claim_hash=None,
            created_at=f"2026-07-28T00:00:{index:02d}.000000Z",
        )

    active = store.checkpoint_storage_metrics(
        runtime_run_id="runtime_run_1"
    )
    assert active["checkpointCount"] == len(boundaries)
    assert active["checkpointBytes"] <= 2 * 1024 * 1024
    assert sum(
        checkpoint["checkpointBytes"]
        for checkpoint in active["checkpoints"]
    ) == active["checkpointBytes"]
    assert all(
        checkpoint["controlPayloadBytes"] < checkpoint["checkpointBytes"]
        for checkpoint in active["checkpoints"]
    )

    _complete_run(store)
    terminal = store.compact_terminal_checkpoints(
        runtime_run_id="runtime_run_1"
    )
    assert terminal.checkpoint_count == 1
    assert terminal.checkpoint_bytes <= 512 * 1024


def test_v15_v1_fixture_migrates_once_and_rejects_poison(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _seed_running_store(tmp_path)
    db_path = store.path
    with store._connect() as conn:
        conn.execute(
            """
            INSERT INTO runtime_control_checkpoints (
                checkpoint_id, runtime_run_id, stage, round_no, safe_boundary,
                run_state_json, source_plan_json, pending_commands_json,
                artifact_manifest_ref, schema_version, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-latest",
                "runtime_run_1",
                "round",
                1,
                "after_round_controller",
                json.dumps({"round": 1}),
                json.dumps({"sourceIds": ["liepin"]}),
                "[]",
                None,
                "runtime-control-checkpoint/v1",
                "2026-07-28T00:00:02.000000Z",
            ),
        )
        conn.execute(
            """
            UPDATE runtime_control_runs
            SET latest_checkpoint_id = ?
            WHERE runtime_run_id = ?
            """,
            ("legacy-latest", "runtime_run_1"),
        )
        conn.execute("PRAGMA user_version = 15")

    migrated = store_module.RuntimeControlStore(db_path)
    migrated.initialize()
    checkpoint = migrated.get_latest_checkpoint(
        runtime_run_id="runtime_run_1"
    )
    assert checkpoint is not None
    assert checkpoint.schema_version == RUNTIME_CHECKPOINT_SCHEMA_V2

    with migrated._connect() as conn:
        conn.execute("PRAGMA user_version = 15")
    migrated.initialize()
    assert migrated.get_latest_checkpoint(
        runtime_run_id="runtime_run_1"
    ) == checkpoint

    poisoned_path = tmp_path / "poisoned.sqlite3"
    poisoned_path.write_bytes(db_path.read_bytes())
    with sqlite3.connect(poisoned_path) as conn:
        conn.execute("PRAGMA user_version = 15")
        conn.execute(
            """
            UPDATE runtime_control_checkpoints
            SET schema_version = 'runtime-control-checkpoint/v1',
                run_state_json = '{poison'
            WHERE checkpoint_id = 'legacy-latest'
            """
        )
    poisoned = store_module.RuntimeControlStore(poisoned_path)
    with pytest.raises(RuntimeControlError) as exc_info:
        poisoned.initialize()
    assert (
        exc_info.value.reason_code
        == "runtime_checkpoint_v1_migration_invalid"
    )
    with sqlite3.connect(poisoned_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 15


def test_v15_migration_interruption_rolls_back_and_retries(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _seed_running_store(tmp_path)
    with store._connect() as conn:
        conn.execute("PRAGMA user_version = 15")
    original = store_module._migrate_v15_to_v16

    def interrupted(conn: sqlite3.Connection) -> None:
        original(conn)
        raise sqlite3.OperationalError("fault_migration_interrupted")

    monkeypatch.setattr(store_module, "_migrate_v15_to_v16", interrupted)
    with pytest.raises(
        sqlite3.OperationalError,
        match="fault_migration_interrupted",
    ):
        store.initialize()
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 15

    monkeypatch.setattr(store_module, "_migrate_v15_to_v16", original)
    store.initialize()
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 16


def _seed_running_store(tmp_path):
    from seektalent_runtime_control.models import (
        RuntimeRunRecord,
        RuntimeRunSnapshot,
    )
    from seektalent_runtime_control.requirements import ApprovedRequirementRevision
    from seektalent_runtime_control.store import RuntimeControlStore

    state = _run_state()
    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    store.save_approved_requirement(
        ApprovedRequirementRevision(
            approved_requirement_revision_id="approved-1",
            draft_revision_id=None,
            base_approved_requirement_revision_id=None,
            source_amendment_id=None,
            agent_conversation_id="agent-1",
            requirement_sheet=state.requirement_sheet,
            selected_item_ids=[],
            deselected_item_ids=[],
            created_at="2026-07-28T00:00:00.000000Z",
        ),
        idempotency_key="approved-1",
    )
    store.create_run(
        RuntimeRunRecord(
            runtime_run_id="runtime_run_1",
            run_intent_id="intent-1",
            start_idempotency_key="start-1",
            run_kind="primary",
            agent_conversation_id="agent-1",
            workbench_session_id=None,
            approved_requirement_revision_id="approved-1",
            status="running",
            current_stage="round",
            current_round=1,
            latest_checkpoint_id=None,
            latest_event_seq=0,
            source_ids=["liepin"],
            stop_reason_code=None,
            created_at="2026-07-28T00:00:00.000000Z",
            updated_at="2026-07-28T00:00:00.000000Z",
            completed_at=None,
        )
    )
    with store._connect() as conn:
        snapshot = RuntimeRunSnapshot(
            runtime_run_id="runtime_run_1",
            status="running",
            current_stage="round",
            current_round=1,
            latest_event_seq=0,
            snapshot={
                "workflowInput": {
                    "jobTitle": state.input_truth.job_title,
                    "jdText": state.input_truth.jd,
                    "notes": state.input_truth.notes,
                }
            },
            updated_at="2026-07-28T00:00:00.000000Z",
        )
        conn.execute(
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
    store.acquire_executor_lease(
        runtime_run_id="runtime_run_1",
        executor_id="executor-1",
        acquired_at="2026-07-28T00:00:00.000000Z",
        lease_expires_at="2026-07-28T00:10:00.000000Z",
    )
    return store


def _complete_run(store) -> None:
    from seektalent_runtime_control.models import RuntimeControlEventInput

    store.append_executor_event(
        RuntimeControlEventInput(
            event_id="terminal-event",
            runtime_run_id="runtime_run_1",
            event_type="runtime_run_completed",
            stage="finalization",
            round_no=None,
            source_id=None,
            status="completed",
            summary="completed",
            payload={},
            idempotency_key=None,
            workbench_event_global_seq=None,
            created_at="2026-07-28T00:01:00.000000Z",
        ),
        executor_id="executor-1",
        attempt_no=1,
        run_status="completed",
        completed_at="2026-07-28T00:01:00.000000Z",
    )
