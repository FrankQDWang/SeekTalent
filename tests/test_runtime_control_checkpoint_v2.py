from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

import seektalent_runtime_control.store as store_module
import seektalent_runtime_control.needs_attention as needs_attention_module
import seektalent.runtime.finalize_runtime as finalize_runtime
import seektalent.runtime.orchestrator as orchestrator_module
import seektalent.runtime.controller_runtime as controller_runtime
from seektalent.models import (
    LocationExecutionPlan,
    ProposedFilterPlan,
    RoundRetrievalPlan,
    RoundState,
    RunState,
    RuntimeFinalizationRevision,
    SearchControllerDecision,
    SearchObservation,
)
from seektalent.source_contracts.detail_open_claims import DetailOpenClaimLedger
from seektalent_runtime_control.checkpoint_v2 import (
    RUNTIME_CHECKPOINT_SCHEMA_V2,
    checkpoint_projection,
)
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
from seektalent_runtime_control.models import RuntimeCheckpoint
from seektalent_runtime_control.checkpoint_recovery import (
    RuntimeCheckpointLoadFailure,
)
from seektalent_runtime_control.recovery_state import RecoveryStateAssembler

from tests.test_runtime_multi_source_round_dispatch import (
    _candidate,
    _run_state,
)
from tests.settings_factory import make_settings


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


def test_detail_claim_high_watermark_can_advance_after_checkpoint(tmp_path) -> None:
    store = _seed_running_store(tmp_path)
    checkpoint = store.write_checkpoint_v2(
        checkpoint_id="checkpoint-before-detail-claim",
        runtime_run_id="runtime_run_1",
        executor_id="executor-1",
        attempt_no=1,
        stage="finalization",
        round_no=None,
        safe_boundary="before_finalization",
        accepted_requirement_revision_id="approved-1",
        source_ids=["liepin"],
        projection=checkpoint_projection(_run_state()),
        detail_claim_revision=0,
        detail_claim_hash=None,
        created_at="2026-07-28T00:00:02.000000Z",
    )
    store.write_detail_claim_snapshot(
        runtime_run_id="runtime_run_1",
        claims={
            "candidate-key": {
                "status": "claimed",
                "browser_open_attempt_count": 1,
                "last_safe_reason_code": None,
            }
        },
        expected_revision=0,
        updated_at="2026-07-28T00:00:03.000000Z",
    )

    recoverable = store.get_latest_recoverable_checkpoint(
        runtime_run_id="runtime_run_1"
    )

    assert recoverable == checkpoint
    assembled = RecoveryStateAssembler(store).assemble(checkpoint)
    assert assembled.detail_open_claims_by_provider_key[
        "candidate-key"
    ].browser_open_attempt_count == 1


def test_checkpoint_rejects_injected_detail_claim_binding(tmp_path) -> None:
    store = _seed_running_store(tmp_path)

    with pytest.raises(
        RuntimeControlError,
        match="runtime_checkpoint_detail_claim_binding_invalid",
    ):
        store.write_checkpoint_v2(
            checkpoint_id="checkpoint-invalid-detail-binding",
            runtime_run_id="runtime_run_1",
            executor_id="executor-1",
            attempt_no=1,
            stage="round",
            round_no=1,
            safe_boundary="after_round_controller",
            accepted_requirement_revision_id="approved-1",
            source_ids=["liepin"],
            projection=checkpoint_projection(_run_state()),
            detail_claim_revision=999,
            detail_claim_hash="0" * 64,
            created_at="2026-07-28T00:00:02.000000Z",
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


@pytest.mark.parametrize(
    ("owner", "mutation"),
    [
        (
            "source_results",
            (
                "UPDATE runtime_control_candidate_truth_state "
                "SET source_lane_results_json = '[]' "
                "WHERE runtime_run_id = 'runtime_run_1'"
            ),
        ),
        (
            "detail_claim",
            (
                "UPDATE runtime_control_detail_claims "
                "SET browser_open_attempt_count = 7 "
                "WHERE runtime_run_id = 'runtime_run_1'"
            ),
        ),
        (
            "round_state",
            (
                "UPDATE runtime_control_round_states "
                "SET state_json = json_set(state_json, '$.round_no', 2) "
                "WHERE runtime_run_id = 'runtime_run_1'"
            ),
        ),
        (
            "finalization",
            (
                "UPDATE runtime_control_candidate_finalization_revisions "
                "SET reason_code = 'tampered_but_valid' "
                "WHERE runtime_run_id = 'runtime_run_1'"
            ),
        ),
    ],
)
def test_valid_shape_owner_tamper_fails_closed(
    tmp_path,
    owner: str,
    mutation: str,
) -> None:
    store = _seed_running_store(tmp_path)
    state = _state_with_round_and_finalization()
    detail_revision, detail_hash = store.write_detail_claim_snapshot(
        runtime_run_id="runtime_run_1",
        claims={
            "candidate-key": {
                "status": "claimed",
                "browser_open_attempt_count": 1,
                "last_safe_reason_code": None,
            }
        },
        expected_revision=0,
        updated_at="2026-07-28T00:00:01.000000Z",
    )
    checkpoint = store.write_checkpoint_v2(
        checkpoint_id=f"checkpoint-tamper-{owner}",
        runtime_run_id="runtime_run_1",
        executor_id="executor-1",
        attempt_no=1,
        stage="finalization",
        round_no=1,
        safe_boundary="before_finalization",
        accepted_requirement_revision_id="approved-1",
        source_ids=["liepin"],
        projection=checkpoint_projection(state),
        detail_claim_revision=detail_revision,
        detail_claim_hash=detail_hash,
        created_at="2026-07-28T00:00:02.000000Z",
    )
    with store._connect() as conn:
        conn.execute(mutation)

    recoverable = store.get_latest_recoverable_checkpoint(
        runtime_run_id="runtime_run_1"
    )

    assert isinstance(recoverable, RuntimeCheckpointLoadFailure)
    assert recoverable.reason_code == "runtime_checkpoint_safe_boundary_invalid"
    with pytest.raises(
        RuntimeControlError,
        match="runtime_checkpoint_durable_owner_mismatch",
    ):
        RecoveryStateAssembler(store).assemble(checkpoint)


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


def test_paused_run_keeps_recoverable_v2_continuation(tmp_path) -> None:
    store = _seed_running_store(tmp_path)
    state = _state_with_round_and_finalization()
    state.finalization_revisions = []
    checkpoint = store.write_checkpoint_v2(
        checkpoint_id="checkpoint-paused-v2",
        runtime_run_id="runtime_run_1",
        executor_id="executor-1",
        attempt_no=1,
        stage="round",
        round_no=1,
        safe_boundary="after_round_controller",
        accepted_requirement_revision_id="approved-1",
        source_ids=["liepin"],
        projection=checkpoint_projection(state),
        detail_claim_revision=0,
        detail_claim_hash=None,
        created_at="2026-07-28T00:00:02.000000Z",
        continuation_cursor={
            "nextPhase": "rounds",
            "completedRounds": 1,
            "stopReason": "max_rounds_reached",
        },
    )
    store.update_run_status(
        runtime_run_id="runtime_run_1",
        status="paused",
        current_stage="round",
        current_round=1,
        updated_at="2026-07-28T00:00:03.000000Z",
    )

    assert store.get_latest_recoverable_checkpoint(
        runtime_run_id="runtime_run_1"
    ) == checkpoint
    assert (
        RecoveryStateAssembler(store)
        .assemble(checkpoint)
        .round_history[0]
        .round_no
        == 1
    )


def test_workflow_runtime_resume_does_not_replay_committed_round(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state_with_round_and_finalization()
    state.finalization_revisions = []
    candidate = _candidate("resume-1", "liepin")
    state.candidate_store[candidate.resume_id] = candidate
    state.seen_resume_ids = [candidate.resume_id]
    runtime = orchestrator_module.WorkflowRuntime(
        make_settings(
            runs_dir=str(tmp_path / "runs"),
            mock_cts=True,
            provider_name="cts",
            min_rounds=1,
            max_rounds=1,
        )
    )
    observed: dict[str, object] = {}
    original_run_rounds = runtime._run_rounds
    monkeypatch.setattr(runtime, "_require_live_llm_config", lambda: None)

    async def capture_run_rounds(**kwargs: object):
        run_state = kwargs["run_state"]
        assert isinstance(run_state, RunState)
        observed["candidate_ids"] = list(run_state.candidate_store)
        return await original_run_rounds(**kwargs)

    async def fail_controller(*args: object, **kwargs: object) -> object:
        raise AssertionError("controller replayed")

    async def fail_dispatch(*args: object, **kwargs: object) -> object:
        raise AssertionError("source dispatch replayed")

    class FinalizationProbe(RuntimeError):
        pass

    async def stop_at_finalization(**kwargs: object) -> object:
        finalize_context = kwargs["finalize_context"]
        observed["rounds_executed"] = finalize_context.rounds_executed
        raise FinalizationProbe

    monkeypatch.setattr(runtime, "_run_rounds", capture_run_rounds)
    monkeypatch.setattr(
        controller_runtime,
        "run_controller_stage",
        fail_controller,
    )
    monkeypatch.setattr(
        orchestrator_module,
        "dispatch_source_rounds",
        fail_dispatch,
    )
    monkeypatch.setattr(
        finalize_runtime,
        "run_deterministic_finalization_stage",
        stop_at_finalization,
    )

    with pytest.raises(FinalizationProbe):
        asyncio.run(
            runtime.run_async(
                job_title=state.input_truth.job_title,
                jd=state.input_truth.jd,
                notes=state.input_truth.notes,
                source_kinds=(),
                resume_checkpoint=_resume_checkpoint_payload(
                    safe_boundary="after_round_controller",
                    next_phase="rounds",
                ),
                resume_run_state=state.model_dump(mode="json"),
            )
        )

    assert observed == {
        "candidate_ids": ["resume-1"],
        "rounds_executed": 1,
    }


def test_after_source_result_commit_is_not_recoverable_without_mid_round_cursor(
    tmp_path,
) -> None:
    store = _seed_running_store(tmp_path)
    checkpoint = store.write_checkpoint_v2(
        checkpoint_id="checkpoint-source-result",
        runtime_run_id="runtime_run_1",
        executor_id="executor-1",
        attempt_no=1,
        stage="round",
        round_no=1,
        safe_boundary="after_source_result_commit",
        accepted_requirement_revision_id="approved-1",
        source_ids=["liepin"],
        projection=checkpoint_projection(_state_with_round_and_finalization()),
        detail_claim_revision=0,
        detail_claim_hash=None,
        created_at="2026-07-28T00:00:02.000000Z",
    )

    assert store.get_latest_recoverable_checkpoint(
        runtime_run_id="runtime_run_1"
    ) == RuntimeCheckpointLoadFailure(
        checkpoint_id=checkpoint.checkpoint_id,
        reason_code="runtime_checkpoint_safe_boundary_invalid",
    )


def test_workflow_runtime_before_finalization_resumes_without_round_replay(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state_with_round_and_finalization()
    state.finalization_revisions = []
    runtime = orchestrator_module.WorkflowRuntime(
        make_settings(
            runs_dir=str(tmp_path / "runs"),
            mock_cts=True,
            provider_name="cts",
            min_rounds=1,
            max_rounds=1,
        )
    )
    calls = {"rounds": 0, "finalization": 0}
    monkeypatch.setattr(runtime, "_require_live_llm_config", lambda: None)

    async def fail_rounds(**kwargs: object) -> object:
        calls["rounds"] += 1
        raise AssertionError("round replayed")

    class FinalizationProbe(RuntimeError):
        pass

    async def stop_at_finalization(**kwargs: object) -> object:
        calls["finalization"] += 1
        assert kwargs["finalize_context"].rounds_executed == 1
        raise FinalizationProbe

    monkeypatch.setattr(runtime, "_run_rounds", fail_rounds)
    monkeypatch.setattr(
        finalize_runtime,
        "run_deterministic_finalization_stage",
        stop_at_finalization,
    )

    with pytest.raises(FinalizationProbe):
        asyncio.run(
            runtime.run_async(
                job_title=state.input_truth.job_title,
                jd=state.input_truth.jd,
                notes=state.input_truth.notes,
                source_kinds=(),
                resume_checkpoint=_resume_checkpoint_payload(
                    safe_boundary="before_finalization",
                    next_phase="finalization",
                ),
                resume_run_state=state.model_dump(mode="json"),
            )
        )

    assert calls == {"rounds": 0, "finalization": 1}


def test_executor_settles_after_finalization_commit_without_runtime_replay(
    tmp_path,
) -> None:
    store = _seed_running_store(tmp_path)
    state = _state_with_round_and_finalization()
    checkpoint = store.write_checkpoint_v2(
        checkpoint_id="checkpoint-finalization-committed",
        runtime_run_id="runtime_run_1",
        executor_id="executor-1",
        attempt_no=1,
        stage="finalization",
        round_no=None,
        safe_boundary="after_finalization_commit",
        accepted_requirement_revision_id="approved-1",
        source_ids=["liepin"],
        projection=checkpoint_projection(state),
        detail_claim_revision=0,
        detail_claim_hash=None,
        created_at="2026-07-28T00:00:02.000000Z",
        continuation_cursor={
            "nextPhase": "complete",
            "completedRounds": 1,
            "stopReason": "max_rounds_reached",
        },
    )
    assert store.get_latest_recoverable_checkpoint(
        runtime_run_id="runtime_run_1"
    ) == checkpoint

    class RuntimeMustNotRun:
        async def run_async(self, **kwargs: object) -> object:
            raise AssertionError("committed finalization replayed")

    executor = WorkflowRuntimeExecutor(
        store=store,
        runtime_factory=RuntimeMustNotRun,
        now=lambda: "2026-07-28T00:00:03.000000Z",
    )
    with store._connect() as conn:
        conn.execute(
            """
            UPDATE runtime_control_runs
            SET status = 'resume_requested'
            WHERE runtime_run_id = 'runtime_run_1'
            """
        )
        snapshot = conn.execute(
            """
            SELECT snapshot_json
            FROM runtime_control_snapshots
            WHERE runtime_run_id = 'runtime_run_1'
            """
        ).fetchone()
        snapshot_payload = json.loads(snapshot["snapshot_json"])
        snapshot_payload["claimReason"] = "resume_requested"
        conn.execute(
            """
            UPDATE runtime_control_snapshots
            SET snapshot_json = ?
            WHERE runtime_run_id = 'runtime_run_1'
            """,
            (json.dumps(snapshot_payload),),
        )

    settled = asyncio.run(
        executor.execute_claimed_run(
            runtime_run_id="runtime_run_1",
            executor_id="executor-1",
            attempt_no=1,
        )
    )

    assert settled.status == "completed"
    metrics = store.checkpoint_storage_metrics(
        runtime_run_id="runtime_run_1"
    )
    assert metrics["checkpointCount"] == 1
    assert store.get_latest_checkpoint(
        runtime_run_id="runtime_run_1"
    ).is_final_manifest


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


def test_terminal_compaction_archives_immutable_action_checkpoint_evidence(
    tmp_path,
) -> None:
    from tests.test_runtime_control_needs_attention import (
        ACTION_ID,
        ENTERED_AT,
        RESOLVED_AT,
        RUN_ID,
        _action,
        _checkpoint,
        _entry_admission,
        _envelope,
        _store,
    )

    store = _store(tmp_path)
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    entered = store.commit_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=_entry_admission(store, checkpoint),
        checkpoint=checkpoint,
        envelope=_envelope(action=_action()),
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        entered_at=ENTERED_AT,
    )
    store.cancel_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        expected_state_revision=entered.state_revision,
        cancelled_at=RESOLVED_AT,
        cancellation_evidence_ref="c" * 64,
    )

    first = store.compact_terminal_checkpoints(runtime_run_id=RUN_ID)
    second = store.compact_terminal_checkpoints(runtime_run_id=RUN_ID)

    assert first.checkpoint_count == 1
    assert second == first
    [action] = store.list_user_actions(runtime_run_id=RUN_ID)
    assert action.action_id == ACTION_ID
    assert action.checkpoint_id == checkpoint.checkpoint_id
    with store._connect() as conn:
        with pytest.raises(
            sqlite3.IntegrityError,
            match="runtime_action_checkpoint_evidence_immutable",
        ):
            conn.execute(
                """
                UPDATE runtime_control_action_checkpoint_evidence
                SET evidence_json = '{"valid":"shape"}'
                WHERE action_id = ?
                """,
                (ACTION_ID,),
            )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="runtime_action_checkpoint_evidence_delete_forbidden",
        ):
            conn.execute(
                """
                DELETE FROM runtime_control_action_checkpoint_evidence
                WHERE action_id = ?
                """,
                (ACTION_ID,),
            )
        conn.execute(
            """
            DROP TRIGGER
            runtime_action_checkpoint_evidence_update_forbidden
            """
        )
        evidence = conn.execute(
            """
            SELECT evidence_json
            FROM runtime_control_action_checkpoint_evidence
            WHERE action_id = ?
            """,
            (ACTION_ID,),
        ).fetchone()
        evidence_payload = json.loads(evidence["evidence_json"])
        evidence_payload["checkpoint"]["stage"] = "tampered_but_valid"
        conn.execute(
            """
            UPDATE runtime_control_action_checkpoint_evidence
            SET evidence_json = ?
            WHERE action_id = ?
            """,
            (json.dumps(evidence_payload), ACTION_ID),
        )
        action_row = conn.execute(
            """
            SELECT *
            FROM runtime_control_user_actions
            WHERE action_id = ?
            """,
            (ACTION_ID,),
        ).fetchone()
        manifest = store.get_latest_checkpoint(runtime_run_id=RUN_ID)
        assert manifest is not None
        with pytest.raises(
            RuntimeControlError,
            match="runtime_needs_attention_checkpoint_mismatch",
        ):
            needs_attention_module._require_checkpoint_binding(
                conn,
                action_row,
                manifest,
            )


def test_action_bound_v1_latest_migrates_and_keeps_action_verifiable(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.test_runtime_control_needs_attention import (
        ACTION_ID,
        ENTERED_AT,
        RUN_ID,
        _action,
        _checkpoint,
        _entry_admission,
        _envelope,
        _store,
    )

    template_path = tmp_path / "template"
    template_path.mkdir()
    template_store = _store(template_path)
    template_checkpoint = _checkpoint()
    template_store.write_checkpoint_for_recovery(template_checkpoint)
    template_store.commit_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=_entry_admission(
            template_store,
            template_checkpoint,
        ),
        checkpoint=template_checkpoint,
        envelope=_envelope(action=_action()),
        expected_state_revision=template_store.get_run(
            RUN_ID
        ).state_revision,
        entered_at=ENTERED_AT,
    )
    with template_store._connect() as conn:
        action_template = conn.execute(
            """
            SELECT *
            FROM runtime_control_user_actions
            WHERE action_id = ?
            """,
            (ACTION_ID,),
        ).fetchone()

    raw_path = tmp_path / "raw-v15"
    raw_path.mkdir()
    store = _store(raw_path)
    legacy_checkpoint = _checkpoint().model_copy(deep=True)
    with store._connect() as conn:
        conn.execute(
            """
            INSERT INTO runtime_control_checkpoints (
                checkpoint_id, runtime_run_id, stage, round_no,
                safe_boundary, run_state_json, source_plan_json,
                pending_commands_json, artifact_manifest_ref,
                schema_version, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                legacy_checkpoint.checkpoint_id,
                legacy_checkpoint.runtime_run_id,
                legacy_checkpoint.stage,
                legacy_checkpoint.round_no,
                legacy_checkpoint.safe_boundary,
                json.dumps(legacy_checkpoint.run_state),
                json.dumps(legacy_checkpoint.source_plan),
                json.dumps(legacy_checkpoint.pending_commands),
                legacy_checkpoint.artifact_manifest_ref,
                "runtime-control-checkpoint/v1",
                legacy_checkpoint.created_at,
            ),
        )
        action_values = dict(action_template)
        action_values["checkpoint_hash"] = (
            needs_attention_module._checkpoint_hash(legacy_checkpoint)
        )
        action_values["candidate_truth_hash"] = (
            needs_attention_module._candidate_truth_hash(
                legacy_checkpoint
            )
        )
        columns = list(action_values)
        conn.execute(
            f"""
            INSERT INTO runtime_control_user_actions (
                {", ".join(columns)}
            )
            VALUES ({", ".join("?" for _ in columns)})
            """,
            tuple(action_values[column] for column in columns),
        )
        conn.execute(
            """
            UPDATE runtime_control_runs
            SET latest_checkpoint_id = ?
            WHERE runtime_run_id = ?
            """,
            (legacy_checkpoint.checkpoint_id, RUN_ID),
        )
        conn.execute("PRAGMA user_version = 15")

    migrated = store_module.RuntimeControlStore(store.path)
    original_migration = store_module._migrate_v15_to_v16

    def interrupt_after_migration(conn: sqlite3.Connection) -> None:
        original_migration(conn)
        raise sqlite3.OperationalError(
            "fault_action_v1_migration_interrupted"
        )

    monkeypatch.setattr(
        store_module,
        "_migrate_v15_to_v16",
        interrupt_after_migration,
    )
    with pytest.raises(
        sqlite3.OperationalError,
        match="fault_action_v1_migration_interrupted",
    ):
        migrated.initialize()
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 15
        assert conn.execute(
            """
            SELECT schema_version
            FROM runtime_control_checkpoints
            WHERE checkpoint_id = ?
            """,
            (legacy_checkpoint.checkpoint_id,),
        ).fetchone()[0] == "runtime-control-checkpoint/v1"
        assert conn.execute(
            """
            SELECT COUNT(*)
            FROM runtime_control_action_checkpoint_evidence
            """
        ).fetchone()[0] == 0

    monkeypatch.setattr(
        store_module,
        "_migrate_v15_to_v16",
        original_migration,
    )
    migrated.initialize()
    migrated.initialize()

    latest = migrated.get_latest_checkpoint(runtime_run_id=RUN_ID)
    assert latest is not None
    assert latest.schema_version == RUNTIME_CHECKPOINT_SCHEMA_V2
    with migrated._connect() as conn:
        action_row = conn.execute(
            """
            SELECT *
            FROM runtime_control_user_actions
            WHERE action_id = ?
            """,
            (ACTION_ID,),
        ).fetchone()
        assert action_row["action_id"] == ACTION_ID
        needs_attention_module._require_checkpoint_binding(
            conn,
            action_row,
            latest,
        )


def test_startup_finishes_existing_manifest_action_compaction(
    tmp_path,
) -> None:
    from seektalent_runtime_control.checkpoint_participant import (
        write_checkpoint_participant,
    )
    from tests.test_runtime_control_needs_attention import (
        ACTION_ID,
        ENTERED_AT,
        RESOLVED_AT,
        RUN_ID,
        _action,
        _checkpoint,
        _entry_admission,
        _envelope,
        _store,
    )

    store = _store(tmp_path)
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    entered = store.commit_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=_entry_admission(store, checkpoint),
        checkpoint=checkpoint,
        envelope=_envelope(action=_action()),
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        entered_at=ENTERED_AT,
    )
    store.cancel_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        expected_state_revision=entered.state_revision,
        cancelled_at=RESOLVED_AT,
        cancellation_evidence_ref="c" * 64,
    )
    manifest_id = f"rtmanifest_{RUN_ID}"
    with store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        write_checkpoint_participant(
            conn,
            checkpoint.model_copy(
                update={
                    "checkpoint_id": manifest_id,
                    "is_final_manifest": True,
                }
            ),
        )
        conn.execute(
            """
            UPDATE runtime_control_runs
            SET latest_checkpoint_id = ?
            WHERE runtime_run_id = ?
            """,
            (manifest_id, RUN_ID),
        )

    restarted = store_module.RuntimeControlStore(store.path)
    restarted.initialize()

    metrics = restarted.checkpoint_storage_metrics(runtime_run_id=RUN_ID)
    assert metrics["checkpointCount"] == 1
    [action] = restarted.list_user_actions(runtime_run_id=RUN_ID)
    assert action.checkpoint_id == checkpoint.checkpoint_id
    with restarted._connect() as conn:
        action_row = conn.execute(
            """
            SELECT *
            FROM runtime_control_user_actions
            WHERE action_id = ?
            """,
            (ACTION_ID,),
        ).fetchone()
        manifest = restarted.get_latest_checkpoint(
            runtime_run_id=RUN_ID
        )
        assert manifest is not None
        needs_attention_module._require_checkpoint_binding(
            conn,
            action_row,
            manifest,
        )


def test_finalization_revision_is_immutable_until_manifest_rehome(
    tmp_path,
) -> None:
    store = _seed_running_store(tmp_path)
    projection = checkpoint_projection(_state_with_round_and_finalization())
    store.write_checkpoint_v2(
        checkpoint_id="checkpoint-finalization-origin",
        runtime_run_id="runtime_run_1",
        executor_id="executor-1",
        attempt_no=1,
        stage="finalization",
        round_no=1,
        safe_boundary="before_finalization",
        accepted_requirement_revision_id="approved-1",
        source_ids=["liepin"],
        projection=projection,
        detail_claim_revision=0,
        detail_claim_hash=None,
        created_at="2026-07-28T00:00:02.000000Z",
    )
    store.write_checkpoint_v2(
        checkpoint_id="checkpoint-finalization-later",
        runtime_run_id="runtime_run_1",
        executor_id="executor-1",
        attempt_no=1,
        stage="finalization",
        round_no=1,
        safe_boundary="before_finalization",
        accepted_requirement_revision_id="approved-1",
        source_ids=["liepin"],
        projection=projection,
        detail_claim_revision=0,
        detail_claim_hash=None,
        created_at="2026-07-28T00:00:03.000000Z",
    )
    with store._connect() as conn:
        before = conn.execute(
            """
            SELECT source_checkpoint_id, created_at
            FROM runtime_control_candidate_finalization_revisions
            WHERE runtime_run_id = ? AND revision = 1
            """,
            ("runtime_run_1",),
        ).fetchone()
    assert tuple(before) == (
        "checkpoint-finalization-origin",
        "2026-07-28T00:00:01.000000Z",
    )

    _complete_run(store)
    manifest = store.compact_terminal_checkpoints(
        runtime_run_id="runtime_run_1"
    )
    with store._connect() as conn:
        after = conn.execute(
            """
            SELECT source_checkpoint_id, created_at
            FROM runtime_control_candidate_finalization_revisions
            WHERE runtime_run_id = ? AND revision = 1
            """,
            ("runtime_run_1",),
        ).fetchone()
        referenced = conn.execute(
            """
            SELECT 1
            FROM runtime_control_checkpoints
            WHERE checkpoint_id = ?
            """,
            (after["source_checkpoint_id"],),
        ).fetchone()
    assert after["source_checkpoint_id"] == manifest.manifest_checkpoint_id
    assert after["created_at"] == "2026-07-28T00:00:01.000000Z"
    assert referenced is not None


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
        "after_round_controller"
        for _round in range(5)
    ] + ["before_finalization", "after_finalization_commit"]
    for index, boundary in enumerate(boundaries, start=1):
        round_no = index if index <= 5 else None
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


def _state_with_round_and_finalization() -> RunState:
    state = _run_state()
    state.runtime_source_lane_results = [
        {
            "source_kind": "liepin",
            "round_no": 1,
            "status": "completed",
        }
    ]
    state.round_history = [
        RoundState(
            round_no=1,
            controller_decision=SearchControllerDecision(
                thought_summary="Search once.",
                action="search_cts",
                decision_rationale="Need candidates.",
                proposed_query_terms=["Backend"],
                proposed_filter_plan=ProposedFilterPlan(),
            ),
            retrieval_plan=RoundRetrievalPlan(
                plan_version=1,
                round_no=1,
                query_terms=["Backend"],
                keyword_query="Backend",
                location_execution_plan=LocationExecutionPlan(
                    mode="none",
                    target_new=10,
                ),
                target_new=10,
                rationale="Need candidates.",
            ),
            search_observation=SearchObservation(
                round_no=1,
                requested_count=10,
                raw_candidate_count=1,
                unique_new_count=1,
                shortage_count=9,
                fetch_attempt_count=1,
            ),
        )
    ]
    state.finalization_revisions = [
        RuntimeFinalizationRevision(
            revision=1,
            runtime_run_id="runtime_run_1",
            reason_code="source_lanes_completed",
            selected_source_kinds=("liepin",),
            candidate_identity_ids=(),
            created_at="2026-07-28T00:00:01.000000Z",
        )
    ]
    return state


def _resume_checkpoint_payload(
    *,
    safe_boundary: str,
    next_phase: str,
) -> dict[str, object]:
    return {
        "schema_version": RUNTIME_CHECKPOINT_SCHEMA_V2,
        "safe_boundary": safe_boundary,
        "round_no": 1,
        "durable_refs": {
            "roundLedgerHighWatermark": 1,
            "continuationCursor": {
                "nextPhase": next_phase,
                "completedRounds": 1,
                "stopReason": "max_rounds_reached",
            },
        },
    }


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
