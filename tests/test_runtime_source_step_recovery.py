from __future__ import annotations

import asyncio
from collections import defaultdict
from hashlib import sha256
from itertools import count
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from seektalent.models import QueryTermCandidate, SCORING_SEMANTICS_VERSION
from seektalent.opencli_browser.contracts import (
    OpenCliBrowserConfig,
    OpenCliBrowserResult,
)
from seektalent.providers.liepin.liepin_site_parsing import (
    stable_liepin_detail_candidate_key_hash,
)
from seektalent.providers.liepin.liepin_site_adapter import (
    LiepinOpenCliSiteConfig,
    LiepinSiteAdapter,
)
from seektalent.providers.liepin.opencli_retriever import (
    LiepinOpenCliResumeRetriever,
)
from seektalent.providers.liepin.opencli_worker_client import (
    LiepinOpenCliWorkerClient,
)
from seektalent.providers.liepin.runtime_context import (
    local_opencli_liepin_source_context,
)
from seektalent.runtime.orchestrator import _PRFBackendSelection
from seektalent.source_port.authenticated_liepin_cards_frames import (
    LiepinCardsAcceptedAckV1,
    LiepinCardsResultV1,
    ReceivedLiepinCardsResult,
)
from seektalent.source_port.authenticated_liepin_details_frames import (
    LiepinDetailsAcceptedAckV1,
    LiepinDetailsObservationV1,
    LiepinDetailsResultV1,
    ReceivedLiepinDetailsResult,
)
from seektalent.source_port.history_contract import SourceHistoryNotFound
from seektalent.source_port.liepin_cards_artifacts import (
    write_liepin_cards_artifact,
)
from seektalent.source_port.liepin_cards_contract import (
    LiepinCardsArtifactV1,
    LiepinCardsObservationV1,
)
from seektalent.source_port.liepin_details_artifacts import (
    write_liepin_details_artifact,
)
from seektalent.source_port.liepin_details_contract import (
    LiepinDetailsArtifactV1,
)
from seektalent.source_port.wire_primitives import canonical_json_bytes
from seektalent_runtime_control.requirements import (
    ApprovedRequirementRevision,
)
from tests.settings_factory import make_settings
from tests.test_runtime_state_flow import (
    GenericFallbackScorer,
    SequenceController,
    _install_runtime_stubs,
    _requirement_sheet,
)


def test_real_runtime_two_lane_detail_recovery_resumes_complete_round(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from seektalent.browser_lane_reconciliation import (
        BrowserLaneReconciliationCoordinator,
        _history_query,
    )
    from seektalent.liepin_cards_source_operation import (
        LiepinCardsSourceOperationExecutor,
    )
    from seektalent.source_adapters import (
        build_default_source_registry as build_registry,
    )
    from seektalent.source_adapters import build_source_enabled_runtime
    from seektalent_runtime_control.executor import WorkflowRuntimeExecutor
    from seektalent_runtime_control.store import RuntimeControlStore

    run_id = "runtime_run_source_resume"
    store_path = tmp_path / "runtime_control.sqlite3"
    settings = make_settings(
        workspace_root=str(tmp_path),
        runtime_control_path=str(store_path),
        runs_dir=str(tmp_path / "runs"),
        liepin_worker_mode="opencli",
        liepin_browser_action_backend="opencli",
        liepin_browser_lane_admission_timeout_seconds=0.01,
        liepin_explore_detail_target=2,
        provider_name="liepin",
        min_rounds=1,
        max_rounds=2,
        enable_eval=False,
        enable_flywheel=False,
        candidate_feedback_enabled=False,
    )
    store = RuntimeControlStore(store_path)
    store.initialize()
    requirement_sheet = _requirement_sheet()
    requirement_sheet = requirement_sheet.model_copy(
        update={
            "initial_query_term_pool": [
                *requirement_sheet.initial_query_term_pool,
                QueryTermCandidate(
                    term="evaluation",
                    source="jd",
                    category="tooling",
                    priority=4,
                    evidence="JD body",
                    first_added_round=0,
                ),
                QueryTermCandidate(
                    term="observability",
                    source="jd",
                    category="tooling",
                    priority=5,
                    evidence="JD body",
                    first_added_round=0,
                ),
            ]
        }
    )
    approved = ApprovedRequirementRevision(
        approved_requirement_revision_id="approved-1",
        draft_revision_id=None,
        base_approved_requirement_revision_id=None,
        source_amendment_id=None,
        agent_conversation_id="agent-source-resume",
        requirement_sheet=requirement_sheet,
        selected_item_ids=[],
        deselected_item_ids=[],
        created_at="2026-08-04T00:00:00.000000Z",
    )
    store.save_approved_requirement(
        approved,
        idempotency_key="approved-source-resume",
    )
    runtime_clock = {"now": "2026-08-04T00:00:00.500000Z"}
    monkeypatch.setattr(
        "seektalent.liepin_cards_source_operation._now",
        lambda: runtime_clock["now"],
    )
    monkeypatch.setattr(
        LiepinCardsSourceOperationExecutor,
        "_ready_source_process",
        lambda self: SimpleNamespace(),
    )
    monkeypatch.setattr(
        LiepinCardsSourceOperationExecutor,
        "prepare_readiness",
        lambda self: None,
    )

    class ReadySessionGate:
        async def verify(self) -> None:
            return None

    monkeypatch.setattr(
        "seektalent.liepin_verify_session_gate.create_production_liepin_verify_session_gate",
        lambda _settings: ReadySessionGate(),
    )
    monkeypatch.setattr(
        LiepinCardsSourceOperationExecutor,
        "_query_terminal_history",
        lambda self, *_args: (_ for _ in ()).throw(
            RuntimeError("history unavailable")
        ),
    )

    card_dispatches: dict[str, int] = defaultdict(int)
    detail_dispatches: dict[tuple[str, str, int], int] = defaultdict(int)
    artifact_payload_bytes = 0
    injected_unknown = False

    def exchange_cards(self, submit):  # type: ignore[no-untyped-def]
        nonlocal artifact_payload_bytes
        request = submit.request
        lane_id = request.source_lane_run_id
        card_dispatches[lane_id] += 1
        lane_context = self._lane_recovery_contexts[lane_id]
        card_count = lane_context.requested_count
        lane_token = sha256(lane_id.encode()).hexdigest()[:12]
        cards = tuple(
            {
                "ref": f"card-{lane_token}-{rank}",
                "provider_rank": rank,
                "display_title": "Synthetic Python Engineer",
                "city": "Shanghai",
                "masked_name": True,
            }
            for rank in range(1, card_count + 1)
        )
        identity = submit.identity
        artifact = LiepinCardsArtifactV1(
            contract_version=(
                "seektalent.source.liepin-cards.artifact/v1"
            ),
            operation_id=identity.operation_id,
            canonical_request_hash=identity.request_hash,
            status="succeeded",
            cards=cards,
            cards_seen=card_count,
        )
        artifact_payload_bytes += len(
            canonical_json_bytes(artifact.model_dump(mode="json"))
        )
        artifact_ref, artifact_hash = write_liepin_cards_artifact(
            self._artifact_root,
            artifact,
        )
        ordinal = (
            submit.delivery.authorization.dispatch_authorization_ordinal
        )
        ack = LiepinCardsAcceptedAckV1(
            contract_version="seektalent.source.liepin-cards.ack/v1",
            identity=identity,
            sidecar_generation=1,
            accepted_journal_revision=1,
            ack_kind=(
                "new_logical_operation"
                if ordinal == 1
                else "new_dispatch_authorization"
            ),
            dispatch_intent_ref=(
                f"source-dispatch://{identity.operation_id}/{ordinal}"
            ),
        )
        observation = LiepinCardsObservationV1(
            contract_version=(
                "seektalent.source.liepin-cards.observation/v1"
            ),
            operation_id=identity.operation_id,
            canonical_request_hash=identity.request_hash,
            disposition="completed",
            artifact_ref=artifact_ref,
            artifact_hash=artifact_hash,
            cards_seen=card_count,
            card_count=card_count,
            producer_generation=1,
        )
        return ack, ReceivedLiepinCardsResult(
            message_id=f"cards-result-{identity.operation_id}",
            reply_to="cards-submit",
            correlation_id=identity.correlation_id,
            payload=LiepinCardsResultV1(
                contract_version=(
                    "seektalent.source.liepin-cards.result/v1"
                ),
                identity=identity,
                observation=observation,
            ),
        )

    def exchange_details(self, submit):  # type: ignore[no-untyped-def]
        nonlocal artifact_payload_bytes, injected_unknown
        request = submit.request
        lane_id = request.source_lane_run_id
        key = (lane_id, request.open_mode, request.rank)
        detail_dispatches[key] += 1
        if (
            not injected_unknown
            and ":round:2:lane:2" in lane_id
            and request.open_mode == "cached_locator"
            and request.rank == 1
        ):
            injected_unknown = True
            raise OSError("sidecar exited after uncertain detail dispatch")

        identity = submit.identity
        detail_url = _detail_url(request.card_ref)
        provider_hash = (
            stable_liepin_detail_candidate_key_hash(detail_url)
            if request.open_mode == "resolve_locator"
            else request.provider_candidate_key_hash
        )
        assert provider_hash is not None
        resume = (
            None
            if request.open_mode == "resolve_locator"
            else {
                "provider_rank": request.rank,
                "detail_payload": {
                    "candidate_name": "Synthetic Candidate",
                    "currentTitle": "Python Engineer",
                    "skills": ["Python", "retrieval"],
                    "sourceUrl": detail_url,
                },
                "normalized_text": "Python retrieval engineer",
                "page_url_hash": sha256(detail_url.encode()).hexdigest(),
                "claim_aware": True,
                "provider_candidate_key_hash": provider_hash,
            }
        )
        action_attempted = int(request.open_mode == "cached_locator")
        artifact = LiepinDetailsArtifactV1(
            contract_version=(
                "seektalent.source.liepin-details.artifact/v1"
            ),
            operation_id=identity.operation_id,
            canonical_request_hash=identity.request_hash,
            status="succeeded",
            open_mode=request.open_mode,
            provider_candidate_key_hash=provider_hash,
            rank=request.rank,
            card_ref=request.card_ref,
            detail_url=detail_url,
            resume=resume,
            action_attempted=action_attempted,
            effect_posture=(
                "attempted" if action_attempted else "not_attempted"
            ),
        )
        artifact_payload_bytes += len(
            canonical_json_bytes(artifact.model_dump(mode="json"))
        )
        artifact_ref, artifact_hash = write_liepin_details_artifact(
            self._details_artifact_root,
            artifact,
        )
        ordinal = (
            submit.delivery.authorization.dispatch_authorization_ordinal
        )
        ack = LiepinDetailsAcceptedAckV1(
            contract_version="seektalent.source.liepin-details.ack/v1",
            identity=identity,
            sidecar_generation=2,
            accepted_journal_revision=2,
            ack_kind=(
                "new_logical_operation"
                if ordinal == 1
                else "new_dispatch_authorization"
            ),
            dispatch_intent_ref=(
                f"source-dispatch://{identity.operation_id}/{ordinal}"
            ),
        )
        observation = LiepinDetailsObservationV1(
            contract_version=(
                "seektalent.source.liepin-details.observation/v1"
            ),
            operation_id=identity.operation_id,
            canonical_request_hash=identity.request_hash,
            disposition="completed",
            artifact_ref=artifact_ref,
            artifact_hash=artifact_hash,
            open_mode=request.open_mode,
            provider_candidate_key_hash=provider_hash,
            rank=request.rank,
            action_attempted=action_attempted,
            effect_posture=(
                "attempted" if action_attempted else "not_attempted"
            ),
            producer_generation=2,
        )
        return ack, ReceivedLiepinDetailsResult(
            message_id=f"details-result-{identity.operation_id}",
            reply_to="details-submit",
            correlation_id=identity.correlation_id,
            payload=LiepinDetailsResultV1(
                contract_version=(
                    "seektalent.source.liepin-details.result/v1"
                ),
                identity=identity,
                observation=observation,
            ),
        )

    monkeypatch.setattr(
        LiepinCardsSourceOperationExecutor,
        "_exchange",
        exchange_cards,
    )
    monkeypatch.setattr(
        LiepinCardsSourceOperationExecutor,
        "_exchange_details",
        exchange_details,
    )

    source_executors: list[LiepinCardsSourceOperationExecutor] = []

    def capture_registry(
        registry_settings,
        *,
        liepin_operation_executor=None,
        liepin_worker_client=None,
    ):
        if liepin_operation_executor is not None:
            source_executors.append(liepin_operation_executor)
            site = LiepinSiteAdapter(
                browser_config=OpenCliBrowserConfig(
                    session="seektalent-liepin",
                    timeout_seconds=10,
                ),
                site_config=LiepinOpenCliSiteConfig(
                    allowed_hosts=("h.liepin.com",),
                    allowed_start_urls=(
                        "https://h.liepin.com/search/getConditionItem#session",
                    ),
                    lease_dir=tmp_path / "site-leases",
                    artifact_root=tmp_path / "site-artifacts",
                ),
                automation=SimpleNamespace(
                    daemon_enabled=False,
                    status=lambda: OpenCliBrowserResult(
                        ok=True,
                        action="status",
                    ),
                ),
                cards_operation_executor=liepin_operation_executor,
            )
            liepin_worker_client = LiepinOpenCliWorkerClient(
                retriever=LiepinOpenCliResumeRetriever(runner=site),
                connection_id="local-opencli",
                provider_account_hash="local-opencli",
            )
        return build_registry(
            registry_settings,
            liepin_operation_executor=liepin_operation_executor,
            liepin_worker_client=liepin_worker_client,
        )

    monkeypatch.setattr(
        "seektalent.source_adapters.build_default_source_registry",
        capture_registry,
    )
    controllers: list[SequenceController] = []
    scorers: list[_RecordingScorer] = []

    def runtime_factory(*, source_registry=None):
        runtime = build_source_enabled_runtime(
            settings,
            source_registry=source_registry,
        )
        controller = SequenceController()
        scorer = _RecordingScorer()
        _install_runtime_stubs(
            runtime,
            controller=controller,
            resume_scorer=scorer,
        )
        cast(Any, runtime)._require_live_llm_config = lambda: None
        cast(Any, runtime).resume_quality_commenter = (
            _ResumeQualityCommenter()
        )

        async def no_prf(**_kwargs: object) -> _PRFBackendSelection:
            return _PRFBackendSelection(prf_decision=None)

        cast(Any, runtime)._select_prf_backend_decision = no_prf
        controllers.append(controller)
        scorers.append(scorer)
        return runtime

    checkpoint_counter = count(1)
    first_executor = WorkflowRuntimeExecutor(
        store=store,
        settings=settings,
        runtime_factory=runtime_factory,
        runtime_run_id_factory=lambda: run_id,
        executor_id_factory=lambda: "executor-source-1",
        checkpoint_id_factory=lambda: (
            f"checkpoint-source-{next(checkpoint_counter)}"
        ),
        source_context_provider=local_opencli_liepin_source_context,
        now=lambda: runtime_clock["now"],
    )
    waiting = asyncio.run(
        first_executor.start_workflow(
            conversation_id="agent-source-resume",
            workbench_session_id=None,
            approved_requirement=approved,
            job_title=requirement_sheet.job_title,
            jd_text="Build reliable Python retrieval systems.",
            notes="Synthetic recovery test.",
            source_ids=["liepin"],
        )
    )

    assert waiting.runtime_run_id == run_id
    assert waiting.status == "resume_requested"
    assert waiting.current_action_id is None
    assert store.list_user_actions(runtime_run_id=run_id) == []
    assert injected_unknown is True
    round_two_lanes = store.get_workflow_round_barrier_lanes(
        runtime_run_id=run_id,
        round_no=2,
    )
    assert len(round_two_lanes) == 2
    completed_lane = next(
        source_lane_run_id
        for source_lane_run_id, _query, status in round_two_lanes
        if status == "completed"
    )
    interrupted_lane = next(
        source_lane_run_id
        for source_lane_run_id, _query, status in round_two_lanes
        if status == "active"
    )
    assert ":round:2:lane:1" in completed_lane
    assert ":round:2:lane:2" in interrupted_lane
    assert card_dispatches[completed_lane] == 1
    assert card_dispatches[interrupted_lane] == 1
    active = store.get_active_workflow_transition(
        runtime_run_id=run_id,
        source_lane_run_id=interrupted_lane,
        query_instance_id=next(
            query
            for lane, query, _status in round_two_lanes
            if lane == interrupted_lane
        ),
    )
    assert active is not None
    assert active.step_kind == "detail_dispatch"
    assert active.continuation["detailCursor"] == 0
    assert active.payload_size_bytes < 64 * 1024
    unresolved_operation_id = active.continuation["operationId"]
    assert isinstance(unresolved_operation_id, str)
    unresolved = store.get_source_operation(
        run_id,
        unresolved_operation_id,
    )
    assert unresolved.retry_posture == "reconcile_first"

    interrupted_token = sha256(interrupted_lane.encode()).hexdigest()[:12]
    interrupted_provider_hashes = tuple(
        stable_liepin_detail_candidate_key_hash(
            _detail_url(f"card-{interrupted_token}-{rank}")
        )
        for rank in (1, 2)
    )
    interrupted_claims = store.get_detail_claim_snapshot(
        runtime_run_id=run_id
    )
    assert tuple(
        interrupted_claims.get(provider_hash)
        for provider_hash in interrupted_provider_hashes
    ) == (
        {
            "status": "terminal_failed",
            "browser_open_attempt_count": 1,
            "last_safe_reason_code": "liepin_details_effect_unknown",
        },
        None,
    )

    first_resource = source_executors[0].step_resource_evidence()
    assert first_resource["barrierBindAttemptCount"] == 2
    assert first_resource["barrierCommittedWriteCount"] == 2
    assert first_resource["barrierCommittedLaneCount"] == 3
    assert first_resource["barrierCommittedLogicalPayloadBytes"] > 0
    assert 0 < first_resource["transitionPayloadBytes"] < 64 * 1024
    assert 0 < first_resource["roundPlanArtifactBytes"] < 1024 * 1024
    assert first_resource["transitionTransactionDurationMs"] < 5_000
    assert first_resource["roundPlanArtifactWriteDurationMs"] < 5_000
    before_resume_detail_counts = dict(detail_dispatches)
    first_controller_calls = controllers[0].calls
    assert first_controller_calls == 2

    with store._connect() as connection:
        connection.execute(
            """
            UPDATE runtime_control_browser_lanes
            SET lease_expires_at = '2026-08-04T00:00:01.000000Z'
            WHERE runtime_run_id = ? AND operation_id = ?
            """,
            (run_id, unresolved_operation_id),
        )
    accepted = store.get_accepted_source_operation_context(
        run_id,
        unresolved_operation_id,
    )
    history_query = _history_query(
        accepted,
        searched_last_generation=1,
    )
    no_effect = SourceHistoryNotFound(
        **history_query.model_dump(exclude={"contract_version"}),
        contract_version="seektalent.source-port.query.result/v1",
        outcome="not_found",
        oldest_retained_generation=1,
        newest_known_generation=1,
        history_complete=True,
        history_truncated=False,
    )
    monkeypatch.setattr(
        "seektalent.browser_lane_reconciliation._now",
        lambda: "2026-08-04T00:00:02.000000Z",
    )
    coordinator = BrowserLaneReconciliationCoordinator(store=store)
    monkeypatch.setattr(
        coordinator,
        "_read_history",
        lambda _accepted: (history_query, no_effect),
    )
    assert coordinator.run_once() == "released"
    assert store.get_source_operation(
        run_id,
        unresolved_operation_id,
    ).retry_posture == "safe_retry"

    del first_executor
    source_executors.clear()
    controllers.clear()
    scorers.clear()
    reopened = RuntimeControlStore(store_path)
    reopened.initialize()
    runtime_clock["now"] = "2026-08-04T00:00:10.500000Z"
    claim = reopened.claim_next_runnable_run(
        executor_id="executor-source-2",
        claimed_at="2026-08-04T00:00:10.000000Z",
        lease_expires_at="2026-08-04T00:10:10.000000Z",
        runtime_run_id=run_id,
    )
    assert claim is not None
    final_executor = WorkflowRuntimeExecutor(
        store=reopened,
        settings=settings,
        runtime_factory=runtime_factory,
        checkpoint_id_factory=lambda: (
            f"checkpoint-source-{next(checkpoint_counter)}"
        ),
        source_context_provider=local_opencli_liepin_source_context,
        now=lambda: runtime_clock["now"],
    )
    completed = asyncio.run(
        final_executor.execute_claimed_run(
            runtime_run_id=run_id,
            executor_id=claim.lease.executor_id,
            attempt_no=claim.lease.attempt_no,
            approved_requirement=approved,
        )
    )

    assert completed.status == "completed"
    assert completed.runtime_run_id == run_id
    assert len(controllers) == 1
    assert controllers[0].calls == 0
    assert card_dispatches[completed_lane] == 1
    assert card_dispatches[interrupted_lane] == 1
    for key, prior_count in before_resume_detail_counts.items():
        lane_id, open_mode, rank = key
        if lane_id == completed_lane or open_mode == "resolve_locator":
            assert detail_dispatches[(lane_id, open_mode, rank)] == prior_count
    assert detail_dispatches[(interrupted_lane, "cached_locator", 1)] == 2
    assert detail_dispatches[(interrupted_lane, "cached_locator", 2)] == 1
    assert scorers
    assert [len(call) for call in scorers[0].calls] == [5]
    assert reopened.get_active_workflow_transition_chains(
        runtime_run_id=run_id
    ) == ()

    second_resource = source_executors[0].step_resource_evidence()
    control_bytes = sum(
        int(resource[key])
        for resource in (first_resource, second_resource)
        for key in (
            "barrierCommittedLogicalPayloadBytes",
            "transitionPayloadBytes",
            "requestArtifactBytes",
            "workPlanArtifactBytes",
            "roundPlanArtifactBytes",
        )
    )
    assert control_bytes < 512 * 1024
    assert control_bytes / max(artifact_payload_bytes, 1) < 25
    checkpoint_metrics = reopened.checkpoint_storage_metrics(
        runtime_run_id=run_id
    )
    assert checkpoint_metrics["checkpointBytes"] < 1024 * 1024
    assert checkpoint_metrics["databaseBytes"] < 16 * 1024 * 1024
    assert checkpoint_metrics["walBytes"] < 16 * 1024 * 1024
    assert all(
        item["serializationLatencyMs"] < 1_000
        for item in checkpoint_metrics["checkpoints"]
    )


class _RecordingScorer(GenericFallbackScorer):
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def score_candidates_parallel(self, *, contexts, tracer):
        self.calls.append(
            tuple(
                context.normalized_resume.resume_id
                for context in contexts
            )
        )
        scored, failures = await super().score_candidates_parallel(
            contexts=contexts,
            tracer=tracer,
        )
        return (
            [
                item.model_copy(
                    update={
                        "scoring_semantics_version": (
                            SCORING_SEMANTICS_VERSION
                        )
                    }
                )
                for item in scored
            ],
            failures,
        )


class _ResumeQualityCommenter:
    async def comment(self, **_kwargs: object) -> str:
        return "Synthetic quality summary."


def _detail_url(card_ref: str) -> str:
    subject = sha256(card_ref.encode()).hexdigest()[:24]
    return (
        "https://h.liepin.com/resume/showresumedetail/"
        f"?res_id_encode={subject}"
    )
