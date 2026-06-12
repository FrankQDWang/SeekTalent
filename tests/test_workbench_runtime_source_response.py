from __future__ import annotations


def test_runtime_source_response_projection_is_owned_by_dedicated_module() -> None:
    from seektalent_ui import workbench_routes, workbench_runtime_source_response

    assert hasattr(workbench_runtime_source_response, "runtime_source_state_response")
    assert not hasattr(workbench_routes, "_runtime_source_state_response")
    assert not hasattr(workbench_routes, "_runtime_source_lane_state_response")


def test_runtime_source_state_response_contract_for_frontend_source_cards() -> None:
    from seektalent_ui.workbench_runtime_source_response import runtime_source_state_response
    from seektalent_ui.workbench_store import (
        RuntimeSourceCountProjection,
        WorkbenchRequirementReview,
        WorkbenchRuntimeSourceLaneLatestState,
        WorkbenchSession,
        WorkbenchSourceRun,
    )

    class FakeSourceStateStore:
        def list_runtime_source_lane_latest_state(self, *, user, session_id):
            del user, session_id
            return [
                WorkbenchRuntimeSourceLaneLatestState(
                    source_run_id="source-run-liepin",
                    source_kind="liepin",
                    runtime_run_id="runtime-1",
                    source_lane_run_id="lane-liepin-1",
                    attempt=1,
                    event_seq=7,
                    event_type="source_workflow_step_completed",
                    status="partial",
                    payload={
                        "safe_counts": {
                            "cards_seen": 30,
                            "cards_filtered": 8,
                            "candidates": 4,
                            "detail_recommendations": 2,
                        },
                        "safe_reason_code": "source_filter_unsupported",
                        "step_name": "card_search",
                        "source_coverage_summary": {"status": "degraded"},
                        "finalization_revision": {"revision": 3, "reason_code": "source_partial"},
                        "merge_summary": {
                            "identity_merge_count": 2,
                            "ambiguous_duplicate_count": 1,
                            "canonical_resume_selected_count": 4,
                        },
                    },
                )
            ]

        def latest_runtime_source_count_projection(self, *, user, session_id):
            del user, session_id
            return {
                "cts": RuntimeSourceCountProjection(
                    source_kind="cts",
                    status="completed",
                    warning_code=None,
                    cards_scanned_count=10,
                    unique_candidates_count=3,
                    event_seq=5,
                )
            }

    requirement_review = WorkbenchRequirementReview(
        session_id="session-1",
        status="approved",
        requirement_sheet=None,
        created_at="2026-06-12T00:00:00+08:00",
        updated_at="2026-06-12T00:00:00+08:00",
        approved_at="2026-06-12T00:00:00+08:00",
    )
    session = WorkbenchSession(
        session_id="session-1",
        workspace_id="default",
        owner_user_id="user-1",
        job_title="Data Engineer",
        jd_text="Build data platforms",
        notes="",
        status="draft",
        source_runs=[
            WorkbenchSourceRun(
                source_run_id="source-run-cts",
                source_kind="cts",
                status="running",
                auth_state="not_required",
                warning_code=None,
                warning_message=None,
            ),
            WorkbenchSourceRun(
                source_run_id="source-run-liepin",
                source_kind="liepin",
                status="running",
                auth_state="login_required",
                warning_code=None,
                warning_message=None,
            ),
        ],
        requirement_review=requirement_review,
    )

    response = runtime_source_state_response(
        store=FakeSourceStateStore(),
        user=object(),
        session=session,
    )

    assert response.selectedSourceKinds == ["cts", "liepin"]
    assert response.coverageStatus == "degraded"
    assert response.finalizationRevision == 3
    assert response.finalizationReasonCode == "source_partial"
    assert response.identityMergeCount == 2
    assert response.ambiguousDuplicateCount == 1
    assert response.canonicalResumeSelectedCount == 4

    cts, liepin = response.sources
    assert cts.sourceKind == "cts"
    assert cts.status == "completed"
    assert cts.reasonCode is None
    assert cts.cardsSeenCount == 10
    assert cts.candidatesCount == 3

    assert liepin.sourceKind == "liepin"
    assert liepin.status == "partial"
    assert liepin.reasonCode == "source_filter_unsupported"
    assert liepin.eventType == "source_workflow_step_completed"
    assert liepin.eventSeq == 7
    assert liepin.cardsSeenCount == 30
    assert liepin.cardsFilteredCount == 8
    assert liepin.candidatesCount == 4
    assert liepin.detailRecommendationsCount == 2
    assert liepin.detailState == "detail_recommended"
    assert liepin.latestWorkflowStep is not None
    assert liepin.latestWorkflowStep.stepName == "card_search"
    assert liepin.latestWorkflowStep.status == "partial"
    assert liepin.latestWorkflowStep.safeCounts == {
        "cards_seen": 30,
        "cards_filtered": 8,
        "candidates": 4,
        "detail_recommendations": 2,
    }
    assert liepin.latestWorkflowStep.safeReasonCode == "source_filter_unsupported"
