from __future__ import annotations

import asyncio
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from seektalent.models import RunState
from seektalent.runtime import WorkflowRuntime
from seektalent.runtime.orchestrator import RunStageError
from seektalent.source_contracts import RuntimeSourceBudgetPolicy
from seektalent.source_contracts.detail_open_claims import (
    DetailOpenClaimLedger,
)
from seektalent.source_port.authenticated_liepin_cards_frames import (
    LiepinCardsAcceptedAckV1,
    LiepinCardsResultV1,
    ReceivedLiepinCardsResult,
)
from seektalent.source_port.liepin_cards_artifacts import (
    write_liepin_cards_artifact,
)
from seektalent.source_port.liepin_cards_contract import (
    LiepinCardsArtifactV1,
    LiepinCardsObservationV1,
    canonical_liepin_cards_request_hash,
    stable_liepin_cards_operation_id,
)
from seektalent.source_port.liepin_round_work_plan_artifacts import (
    LiepinRoundLaneWorkItemV1,
    LiepinRoundWorkPlanV1,
)
from seektalent.source_port.wire_primitives import canonical_json_bytes
from seektalent.sources.liepin.runtime_lane import (
    LiepinPreparedRoundResume,
    _cards_operation_request_from_round_plan_lane,
    prepare_liepin_round_work_plan_resume,
    resume_liepin_round_work_plan,
)
from seektalent_runtime_control.checkpoint_v2 import checkpoint_projection
from tests.settings_factory import make_settings
from tests.test_runtime_control_checkpoint_v2 import _seed_running_store
from tests.test_runtime_multi_source_round_dispatch import _run_state


def test_workflow_runtime_rejects_stale_round_plan_before_resume_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_state = _run_state()
    checkpoint_id = "checkpoint-before-controller"
    plan = _round_plan(
        run_state=run_state,
        checkpoint_id=checkpoint_id,
        accepted_requirement_revision_id="approved-stale",
    )
    request = _cards_operation_request_from_round_plan_lane(
        plan=plan,
        lane=plan.lanes[0],
        requirement_sheet=run_state.requirement_sheet,
        budget=RuntimeSourceBudgetPolicy.defaults(),
    )
    prepared = LiepinPreparedRoundResume(
        plan=plan,
        lane_payloads=({},),
        cards_requests=(request,),
    )
    external_calls = 0

    async def resume_effect(
        _prepared: object,
        _claims: object,
    ) -> object:
        nonlocal external_calls
        external_calls += 1
        raise AssertionError("resume effect must not run")

    runtime = WorkflowRuntime(
        make_settings(
            runs_dir=str(tmp_path / "runs"),
            mock_cts=True,
            provider_name="liepin",
            min_rounds=1,
            max_rounds=1,
        )
    )
    monkeypatch.setattr(runtime, "_require_live_llm_config", lambda: None)

    with pytest.raises(
        RunStageError,
        match="runtime_workflow_round_resume_invalid",
    ):
        asyncio.run(
            runtime.run_async(
                job_title=run_state.input_truth.job_title,
                jd=run_state.input_truth.jd,
                notes=run_state.input_truth.notes,
                source_kinds=["liepin"],
                approved_requirement_sheet=run_state.requirement_sheet,
                resume_run_state=run_state.model_dump(mode="json"),
                resume_checkpoint={
                    "checkpoint_id": checkpoint_id,
                    "runtime_run_id": "runtime_run_1",
                    "stage": "round",
                    "round_no": 1,
                    "safe_boundary": "before_round_controller",
                    "accepted_requirement_revision_id": "approved-1",
                    "durable_refs": {
                        "continuationCursor": {
                            "nextPhase": "rounds",
                            "completedRounds": 0,
                            "stopReason": "max_rounds_reached",
                        }
                    },
                },
                runtime_run_id="runtime_run_1",
                resume_workflow_transitions=[{"lane": "durable"}],
                runtime_source_workflow_prepare_callback=lambda _lanes: prepared,
                runtime_source_workflow_resume_callback=resume_effect,
            )
        )

    assert external_calls == 0


def test_completed_cards_lane_cold_resume_restores_artifact_without_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from seektalent.liepin_cards_source_operation import (
        LiepinCardsSourceOperationExecutor,
    )
    from seektalent_runtime_control.store import RuntimeControlStore

    run_state = _run_state()
    store = _seed_running_store(tmp_path)
    checkpoint_id = "checkpoint-before-controller"
    store.write_checkpoint_v2(
        checkpoint_id=checkpoint_id,
        runtime_run_id="runtime_run_1",
        executor_id="executor-1",
        attempt_no=1,
        stage="round",
        round_no=1,
        safe_boundary="before_round_controller",
        accepted_requirement_revision_id="approved-1",
        source_ids=["liepin"],
        projection=checkpoint_projection(run_state),
        detail_claim_revision=0,
        detail_claim_hash=None,
        continuation_cursor={
            "nextPhase": "rounds",
            "completedRounds": 0,
            "stopReason": "max_rounds_reached",
        },
        created_at="2026-07-28T00:00:01.000000Z",
    )
    settings = make_settings(
        workspace_root=str(tmp_path),
        runtime_control_path=str(store.path),
        liepin_worker_mode="opencli",
        liepin_browser_action_backend="opencli",
    )
    monkeypatch.setattr(
        "seektalent.liepin_cards_source_operation._now",
        lambda: "2026-07-28T00:00:02.000000Z",
    )
    first_executor = LiepinCardsSourceOperationExecutor(
        settings=settings,
        store=store,
        runtime_run_id="runtime_run_1",
        executor_id="executor-1",
        attempt_no=1,
        accepted_requirement_revision_id="approved-1",
        runtime_attempt_authority_ref=("executor-lease://runtime_run_1/1"),
    )
    plan = _round_plan(
        run_state=run_state,
        checkpoint_id=checkpoint_id,
        accepted_requirement_revision_id="approved-1",
    )
    lane = plan.lanes[0]
    request = _cards_operation_request_from_round_plan_lane(
        plan=plan,
        lane=lane,
        requirement_sheet=run_state.requirement_sheet,
        budget=RuntimeSourceBudgetPolicy.defaults(),
    )
    first_executor.bind_round_work_plan(plan)
    first_executor.bind_lane(
        lane.source_lane_run_id,
        lane.query_instance_id,
        source_plan_id=plan.source_plan_id,
        round_no=plan.round_no,
        query_terms=lane.query_terms,
        keyword_query=lane.keyword_query,
        query_fingerprint=lane.query_fingerprint,
        query_role=lane.query_role,
        requested_count=lane.logical_requested_count,
        max_pages=1,
        max_cards=1,
    )
    monkeypatch.setattr(
        LiepinCardsSourceOperationExecutor,
        "_ready_source_process",
        lambda self: SimpleNamespace(),
    )
    dispatch_count = 0

    def exchange_cards(self, submit):  # type: ignore[no-untyped-def]
        nonlocal dispatch_count
        dispatch_count += 1
        identity = submit.identity
        artifact_ref, artifact_hash = write_liepin_cards_artifact(
            self._artifact_root,
            LiepinCardsArtifactV1(
                contract_version=("seektalent.source.liepin-cards.artifact/v1"),
                operation_id=identity.operation_id,
                canonical_request_hash=identity.request_hash,
                status="succeeded",
                cards=(
                    {
                        "display_title": "Senior Python Engineer",
                        "city": "Shanghai",
                        "masked_name": True,
                    },
                ),
                cards_seen=1,
            ),
        )
        ordinal = submit.delivery.authorization.dispatch_authorization_ordinal
        ack = LiepinCardsAcceptedAckV1(
            contract_version="seektalent.source.liepin-cards.ack/v1",
            identity=identity,
            sidecar_generation=1,
            accepted_journal_revision=1,
            ack_kind=("new_logical_operation" if ordinal == 1 else "new_dispatch_authorization"),
            dispatch_intent_ref=(f"source-dispatch://{identity.operation_id}/{ordinal}"),
        )
        observation = LiepinCardsObservationV1(
            contract_version=("seektalent.source.liepin-cards.observation/v1"),
            operation_id=identity.operation_id,
            canonical_request_hash=identity.request_hash,
            disposition="completed",
            artifact_ref=artifact_ref,
            artifact_hash=artifact_hash,
            cards_seen=1,
            card_count=1,
            producer_generation=1,
        )
        return ack, ReceivedLiepinCardsResult(
            message_id="cards-result",
            reply_to="cards-submit",
            correlation_id=identity.correlation_id,
            payload=LiepinCardsResultV1(
                contract_version=("seektalent.source.liepin-cards.result/v1"),
                identity=identity,
                observation=observation,
            ),
        )

    monkeypatch.setattr(
        LiepinCardsSourceOperationExecutor,
        "_exchange",
        exchange_cards,
    )
    envelope, structured = first_executor._execute(request)  # noqa: SLF001
    assert envelope["status"] == "succeeded"
    assert structured["ok"] is True
    first_executor.complete_lane(
        source_lane_run_id=lane.source_lane_run_id,
        query_instance_id=lane.query_instance_id,
    )
    operation_id = stable_liepin_cards_operation_id(request)
    assert (
        store.get_source_operation(
            "runtime_run_1",
            operation_id,
        ).operation_phase
        == "main_committed"
    )
    assert dispatch_count == 1

    del first_executor, request, structured, envelope
    reopened = RuntimeControlStore(store.path)
    reopened.initialize()
    cold_executor = LiepinCardsSourceOperationExecutor(
        settings=settings,
        store=reopened,
        runtime_run_id="runtime_run_1",
        executor_id="executor-2",
        attempt_no=2,
        accepted_requirement_revision_id="approved-1",
        runtime_attempt_authority_ref=("executor-lease://runtime_run_1/2"),
    )
    resume_lanes = reopened.get_workflow_round_resume_lanes(
        runtime_run_id="runtime_run_1",
        base_checkpoint_id=checkpoint_id,
    )
    prepared = prepare_liepin_round_work_plan_resume(
        resume_lanes=[lane.resume_payload() for lane in resume_lanes],
        cards_operation_executor=cold_executor,
    )
    dispatch_count = 0
    recovered = asyncio.run(
        resume_liepin_round_work_plan(
            settings=settings,
            prepared_resume=prepared,
            detail_open_claim_ledger=DetailOpenClaimLedger({}),
            cards_operation_executor=cold_executor,
        )
    )

    assert dispatch_count == 0
    assert recovered.plan == plan
    assert recovered.lane_result.status == "completed"
    assert recovered.lane_result.raw_candidate_count == 1
    assert len(recovered.lane_result.candidate_store_updates) == 1
    assert (
        reopened.get_source_operation(
            "runtime_run_1",
            operation_id,
        ).operation_phase
        == "main_committed"
    )


def test_pending_target_is_skipped_when_completed_target_satisfied_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from seektalent.sources.liepin import runtime_lane

    run_state = _run_state()
    first_plan = _round_plan(
        run_state=run_state,
        checkpoint_id="checkpoint-before-controller",
        accepted_requirement_revision_id="approved-1",
    )
    first_lane = first_plan.lanes[0]
    second_lane = first_lane.model_copy(
        update={
            "lane_ordinal": 2,
            "target_ordinal": 2,
            "source_lane_run_id": ("runtime_run_1:source:1:liepin:round:1:lane:1:target:2"),
        }
    )
    plan = first_plan.model_copy(update={"lanes": (first_lane, second_lane)})
    budget = RuntimeSourceBudgetPolicy.defaults()
    requests = tuple(
        _cards_operation_request_from_round_plan_lane(
            plan=plan,
            lane=lane,
            requirement_sheet=run_state.requirement_sheet,
            budget=budget,
        )
        for lane in plan.lanes
    )
    artifact = LiepinCardsArtifactV1(
        contract_version="seektalent.source.liepin-cards.artifact/v1",
        operation_id=stable_liepin_cards_operation_id(requests[0]),
        canonical_request_hash=canonical_liepin_cards_request_hash(requests[0]),
        status="succeeded",
        cards=(
            {
                "display_title": "Synthetic Python Engineer",
                "city": "Shanghai",
                "masked_name": True,
            },
        ),
        cards_seen=1,
    )
    external_calls = 0

    async def unexpected_lane_run(**_kwargs: object):
        nonlocal external_calls
        external_calls += 1
        raise AssertionError("satisfied pending target must not dispatch")

    monkeypatch.setattr(
        runtime_lane,
        "run_liepin_source_lane",
        unexpected_lane_run,
    )

    class DurableExecutor:
        def __init__(self) -> None:
            self.skipped: list[tuple[str, str]] = []

        def activate_recovered_round_work_plan(
            self,
            recovered_plan: LiepinRoundWorkPlanV1,
        ) -> None:
            assert recovered_plan == plan

        def resume_completed_cards_workflow_transition(
            self,
            _transition: object,
            *,
            expected_request: object,
        ) -> LiepinCardsArtifactV1:
            assert expected_request == requests[0]
            return artifact

        def complete_lane(self, **_kwargs: object) -> None:
            return None

        def skip_lane(
            self,
            *,
            round_no: int,
            source_lane_run_id: str,
            query_instance_id: str,
        ) -> None:
            assert round_no == plan.round_no
            self.skipped.append((source_lane_run_id, query_instance_id))

    durable_executor = DurableExecutor()
    prepared = LiepinPreparedRoundResume(
        plan=plan,
        lane_payloads=(
            {
                "roundNo": 1,
                "barrierStatus": "completed",
                "transitions": [
                    {
                        "stepKind": "lane_completed",
                        "continuation": {"laneResultKind": "cards_only"},
                    }
                ],
            },
            {
                "roundNo": 1,
                "barrierStatus": "pending",
                "transitions": [],
            },
        ),
        cards_requests=requests,
    )

    recovered = asyncio.run(
        resume_liepin_round_work_plan(
            settings=make_settings(
                workspace_root=str(tmp_path),
                liepin_worker_mode="fake_fixture",
                liepin_allow_fake_fixture_worker=True,
            ),
            prepared_resume=prepared,
            detail_open_claim_ledger=DetailOpenClaimLedger({}),
            cards_operation_executor=durable_executor,
        )
    )

    assert external_calls == 0
    assert durable_executor.skipped == [
        (
            second_lane.source_lane_run_id,
            second_lane.query_instance_id,
        )
    ]
    assert len(recovered.lane_result.candidate_store_updates) == 1


def _round_plan(
    *,
    run_state: RunState,
    checkpoint_id: str,
    accepted_requirement_revision_id: str,
) -> LiepinRoundWorkPlanV1:
    requirement_payload = run_state.requirement_sheet.model_dump(mode="json")
    lane = LiepinRoundLaneWorkItemV1(
        lane_ordinal=1,
        logical_query_ordinal=1,
        target_ordinal=1,
        source_lane_run_id=("runtime_run_1:source:1:liepin:round:1:lane:1"),
        query_instance_id="query-1",
        query_fingerprint="a" * 64,
        query_role="exploit",
        lane_type="exploit",
        term_group_key="group-1",
        primary_anchor_family_id="role.python-engineer",
        non_anchor_term_family_ids=(),
        source_plan_version="1",
        logical_query_terms=("python",),
        query_terms=("python",),
        keyword_query="python",
        logical_target_total=1,
        logical_requested_count=1,
        provider_scan_limit=1,
    )
    return LiepinRoundWorkPlanV1(
        contract_version="seektalent.source.liepin-round-work-plan/v1",
        runtime_run_id="runtime_run_1",
        base_checkpoint_id=checkpoint_id,
        accepted_requirement_revision_id=(accepted_requirement_revision_id),
        requirement_sheet_hash=sha256(canonical_json_bytes(requirement_payload)).hexdigest(),
        source_plan_id="runtime_run_1:source:1:liepin",
        round_no=1,
        job_title=run_state.input_truth.job_title,
        jd=run_state.input_truth.jd,
        notes=run_state.input_truth.notes or "",
        requirement_sheet=requirement_payload,
        source_context={},
        source_budget_policy=(RuntimeSourceBudgetPolicy.defaults().to_public_payload()),
        resume_context={
            "controllerDecision": {},
            "retrievalPlan": {},
            "constraintProjectionResult": {},
            "secondLaneDecision": {},
            "prfSelection": {},
            "jobIntentFingerprint": "test",
            "sourceRawTargets": {"liepin": 1},
        },
        detail_claim_aware=False,
        lanes=(lane,),
    )
