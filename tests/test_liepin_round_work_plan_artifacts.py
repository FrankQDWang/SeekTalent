from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from seektalent.liepin_cards_source_operation import (
    LiepinCardsSourceOperationExecutor,
)
from seektalent.source_port.liepin_round_work_plan_artifacts import (
    MAX_ROUND_WORK_PLAN_BYTES,
    LiepinRoundLaneWorkItemV1,
    LiepinRoundWorkPlanV1,
    read_liepin_round_work_plan_artifact,
    write_liepin_round_work_plan_artifact,
)
from seektalent.source_port.wire_primitives import canonical_json_bytes
from seektalent_runtime_control.checkpoint_v2 import checkpoint_projection
from tests.settings_factory import make_settings
from tests.test_runtime_control_checkpoint_v2 import _seed_running_store
from tests.test_runtime_multi_source_round_dispatch import _run_state


class SimulatedProcessDeath(BaseException):
    pass


def test_round_work_plan_size_limit_precedes_artifact_and_barrier_writes(
    tmp_path: Path,
) -> None:
    store = _seed_running_store(tmp_path)
    store.write_checkpoint_v2(
        checkpoint_id="checkpoint-before-controller",
        runtime_run_id="runtime_run_1",
        executor_id="executor-1",
        attempt_no=1,
        stage="round",
        round_no=1,
        safe_boundary="before_round_controller",
        accepted_requirement_revision_id="approved-1",
        source_ids=["liepin"],
        projection=checkpoint_projection(_run_state()),
        detail_claim_revision=0,
        detail_claim_hash=None,
        continuation_cursor={
            "nextPhase": "rounds",
            "completedRounds": 0,
            "stopReason": "max_rounds_reached",
        },
        created_at="2026-07-28T00:00:01.000000Z",
    )
    executor = LiepinCardsSourceOperationExecutor(
        settings=make_settings(
            workspace_root=str(tmp_path),
            runtime_control_path=str(store.path),
            liepin_worker_mode="opencli",
            liepin_browser_action_backend="opencli",
        ),
        store=store,
        runtime_run_id="runtime_run_1",
        executor_id="executor-1",
        attempt_no=1,
        accepted_requirement_revision_id="approved-1",
        runtime_attempt_authority_ref="executor-lease://runtime_run_1/1",
    )
    plan = _round_plan(
        source_context={"forbiddenRunState": "x" * MAX_ROUND_WORK_PLAN_BYTES}
    )

    with pytest.raises(
        ValueError,
        match="liepin_round_work_plan_artifact_too_large",
    ):
        executor.bind_round_work_plan(plan)

    artifact_root = tmp_path / "source-port" / "liepin-round-work-plans"
    assert not artifact_root.exists()
    assert store.get_workflow_round_barrier_lanes(
        runtime_run_id="runtime_run_1",
        round_no=1,
    ) == ()


def test_round_work_plan_repair_never_removes_existing_final_before_replace(
    tmp_path: Path,
) -> None:
    plan = _round_plan()
    payload = canonical_json_bytes(plan.model_dump(mode="json"))
    digest = sha256(payload).hexdigest()
    root = tmp_path / "round-plans"
    root.mkdir()
    final_path = root / f"{digest}.json"
    final_path.write_bytes(b"truncated-existing-final")

    def fail_after_temporary_fsync(point: str) -> None:
        if point == "after_temporary_fsynced":
            raise SimulatedProcessDeath(point)

    with pytest.raises(SimulatedProcessDeath):
        write_liepin_round_work_plan_artifact(
            root,
            plan,
            fault_injector=fail_after_temporary_fsync,
        )

    assert final_path.read_bytes() == b"truncated-existing-final"
    assert list(root.glob("*.tmp")) == []

    write = write_liepin_round_work_plan_artifact(root, plan)

    assert write.published is True
    assert write.payload_size_bytes == len(payload)
    assert final_path.read_bytes() == payload
    assert read_liepin_round_work_plan_artifact(
        root,
        write.artifact_ref,
        expected_hash=write.artifact_hash,
    ) == plan


def test_round_work_plan_reader_rejects_oversize_artifact(
    tmp_path: Path,
) -> None:
    raw = b"x" * (MAX_ROUND_WORK_PLAN_BYTES + 1)
    digest = sha256(raw).hexdigest()
    root = tmp_path / "round-plans"
    root.mkdir()
    (root / f"{digest}.json").write_bytes(raw)

    with pytest.raises(
        ValueError,
        match="liepin_round_work_plan_artifact_too_large",
    ):
        read_liepin_round_work_plan_artifact(
            root,
            f"liepin-round-work-plan://sha256/{digest}",
            expected_hash=digest,
        )


def _round_plan(
    *,
    source_context: dict[str, object] | None = None,
) -> LiepinRoundWorkPlanV1:
    return LiepinRoundWorkPlanV1(
        contract_version="seektalent.source.liepin-round-work-plan/v1",
        runtime_run_id="runtime_run_1",
        base_checkpoint_id="checkpoint-before-controller",
        accepted_requirement_revision_id="approved-1",
        requirement_sheet_hash=sha256(
            canonical_json_bytes({})
        ).hexdigest(),
        source_plan_id="runtime_run_1:source:1:liepin",
        round_no=1,
        job_title="AI Agent Engineer",
        jd="Build reliable agent systems.",
        notes="",
        requirement_sheet={},
        source_context=source_context or {},
        source_budget_policy={},
        resume_context={
            "controllerDecision": {},
            "retrievalPlan": {},
            "constraintProjectionResult": {},
            "secondLaneDecision": {},
            "prfSelection": {},
            "jobIntentFingerprint": "test",
            "sourceRawTargets": {"liepin": 10},
        },
        detail_claim_aware=True,
        lanes=(
            LiepinRoundLaneWorkItemV1(
                lane_ordinal=1,
                logical_query_ordinal=1,
                target_ordinal=1,
                source_lane_run_id="runtime_run_1:source:liepin:lane:a",
                query_instance_id="query-a",
                query_fingerprint="a" * 64,
                query_role="exploit",
                lane_type="logical_query",
                term_group_key="primary",
                primary_anchor_family_id="role.agent-engineer",
                non_anchor_term_family_ids=(),
                source_plan_version="source-plan/v1",
                logical_query_terms=("agent",),
                query_terms=("agent",),
                keyword_query="agent",
                logical_target_total=10,
                logical_requested_count=10,
                provider_scan_limit=20,
            ),
        ),
    )
