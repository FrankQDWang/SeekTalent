from __future__ import annotations

import asyncio
from collections.abc import Mapping

import pytest

from seektalent.models import InputTruth, RequirementSheet, ResumeCandidate, RetrievalState, RunState, ScoringPolicy
from seektalent.runtime.logical_query_dispatch import LogicalQueryDispatch
from seektalent.runtime.orchestrator import WorkflowRuntime
from seektalent.runtime.source_lanes import apply_source_lane_result, runtime_source_lane_result_from_source_result
from seektalent.runtime.source_round_dispatch import (
    RuntimeSourceInvariantError,
    SourceRoundAdapterResult,
    SourceRoundDispatchRequest,
    dispatch_source_rounds,
)
from seektalent.source_contracts import (
    RegisteredSource,
    SourceBudget,
    SourceCapabilities,
    SourceLaneRequest,
    SourceLaneResult,
    SourcePlan,
)
from seektalent.sources.public_events import require_public_source_reason_code
from seektalent.source_contracts import SourceRegistry
from tests.settings_factory import make_settings


def _requirement_sheet() -> RequirementSheet:
    return RequirementSheet(
        job_title="Data Engineer",
        title_anchor_terms=("Data Engineer",),
        title_anchor_rationale="Data Engineer is the role anchor.",
        role_summary="Build data systems.",
        scoring_rationale="Prioritize data platform experience.",
    )


def _run_state() -> RunState:
    requirement_sheet = _requirement_sheet()
    return RunState(
        input_truth=InputTruth(
            job_title=requirement_sheet.job_title,
            jd="Build data systems.",
            notes="",
            job_title_sha256="job",
            jd_sha256="jd",
            notes_sha256="notes",
        ),
        requirement_sheet=requirement_sheet,
        scoring_policy=ScoringPolicy(
            job_title=requirement_sheet.job_title,
            role_summary=requirement_sheet.role_summary,
            must_have_capabilities=requirement_sheet.must_have_capabilities,
            preferred_capabilities=requirement_sheet.preferred_capabilities,
            exclusion_signals=requirement_sheet.exclusion_signals,
            hard_constraints=requirement_sheet.hard_constraints,
            preferences=requirement_sheet.preferences,
            scoring_rationale=requirement_sheet.scoring_rationale,
        ),
        retrieval_state=RetrievalState(),
    )


def _fixture_source() -> RegisteredSource:
    budget = SourceBudget(card_target=1, detail_target=0, scan_limit=1)

    def plan_source(*, runtime_run_id: str, source_index: int, budget_overrides: Mapping[str, int] | None) -> SourcePlan:
        del budget_overrides
        return SourcePlan(
            source_id="fixture_source",
            source_plan_id=f"{runtime_run_id}:source:{source_index}:fixture_source",
            runtime_run_id=runtime_run_id,
            label="Fixture Source",
            budget=budget,
            query_intents=("fixture python",),
        )

    async def run_card_lane(request: SourceLaneRequest) -> SourceLaneResult:
        assert request.source_id == "fixture_source"
        candidate = ResumeCandidate(
            resume_id="fixture-resume-1",
            source_resume_id="fixture-provider-1",
            snapshot_sha256="fixture-snapshot",
            dedup_key="fixture-person-1",
            search_text="Python data platform engineer",
            raw={"source": "fixture_source"},
        )
        return SourceLaneResult.from_candidates(
            request=request,
            status="completed",
            candidates=(candidate,),
            collected_at="2026-06-04T00:00:00Z",
            raw_candidate_count=1,
            safe_reason_code="source_card_candidate",
        )

    return RegisteredSource(
        source_id="fixture_source",
        label="Fixture Source",
        capabilities=SourceCapabilities(
            supports_card_search=True,
            supports_detail_fetch=False,
            supports_native_filters=False,
            supports_incremental_detail=False,
            requires_human_login=False,
            max_safe_concurrency=1,
            stable_external_id=True,
            stable_dedup_key=True,
        ),
        default_budget=budget,
        plan=plan_source,
        run_card_lane=run_card_lane,
    )


def test_fixture_source_runs_through_registry_and_runtime_merge_without_runtime_source_branch() -> None:
    registry = SourceRegistry([_fixture_source()], default_source_ids=("fixture_source",))
    source = registry.enabled_sources(["fixture_source"])[0]
    plan = source.plan(runtime_run_id="run-1", source_index=0, budget_overrides=None)
    request = SourceLaneRequest(
        source_id=plan.source_id,
        lane_mode="card",
        runtime_run_id=plan.runtime_run_id,
        source_plan_id=plan.source_plan_id,
        source_lane_run_id=f"{plan.source_plan_id}:lane:1",
        job_title="Data Engineer",
        jd="Build data systems.",
        notes=None,
        requirement_sheet=_requirement_sheet(),
        source_query_terms=plan.query_intents,
        budget=plan.budget,
    )

    source_result = asyncio.run(source.run_card_lane(request))
    runtime_result = runtime_source_lane_result_from_source_result(source_result)
    run_state = _run_state()

    apply_source_lane_result(
        run_state=run_state,
        result=runtime_result,
        source_order={"fixture_source": 0},
    )

    assert run_state.candidate_store["fixture-resume-1"].raw["source"] == "fixture_source"
    assert run_state.source_evidence_by_resume_id["fixture-resume-1"][0].source == "fixture_source"


def test_fixture_source_executes_through_workflow_runtime_without_runtime_source_branch(tmp_path) -> None:
    registry = SourceRegistry([_fixture_source()], default_source_ids=("fixture_source",))
    runtime = WorkflowRuntime(
        make_settings(workspace_root=str(tmp_path), runs_dir=str(tmp_path / "runs")),
        source_registry=registry,
    )
    run_state = _run_state()

    dispatch_result = asyncio.run(
        runtime.run_source_round_for_testing(
            run_state=run_state,
            source_kinds=("fixture_source",),
            logical_queries=(
                LogicalQueryDispatch(
                    round_no=1,
                    query_role="exploit",
                    lane_type="exploit",
                    query_instance_id="fixture-query",
                    query_fingerprint="fixture-fingerprint",
                    query_terms=("fixture python",),
                    keyword_query="fixture python",
                    requested_count=1,
                    source_plan_version="fixture-plan",
                ),
            ),
            round_no=1,
        )
    )

    assert dispatch_result.source_results[0].source == "fixture_source"
    assert run_state.candidate_store["fixture-resume-1"].raw["source"] == "fixture_source"
    assert run_state.source_evidence_by_resume_id["fixture-resume-1"][0].source == "fixture_source"


def test_source_registry_rejects_unknown_and_duplicate_sources() -> None:
    registry = SourceRegistry([_fixture_source()], default_source_ids=("fixture_source",))

    with pytest.raises(ValueError, match="empty_source_selection"):
        registry.enabled_sources(())
    with pytest.raises(ValueError, match="unknown_source:missing"):
        registry.enabled_sources(["missing"])
    with pytest.raises(ValueError, match="duplicate_source:fixture_source"):
        registry.enabled_sources(["fixture_source", "fixture_source"])


def test_public_source_reason_codes_reject_provider_specific_codes() -> None:
    assert require_public_source_reason_code("source_login_required") == "source_login_required"
    assert require_public_source_reason_code(None) is None
    with pytest.raises(ValueError, match="unknown_public_source_reason_code:liepin_opencli_timeout"):
        require_public_source_reason_code("liepin_opencli_timeout")


def test_source_round_dispatch_accepts_registered_fixture_source_without_cts_liepin_adapters() -> None:
    async def fixture_adapter(request: SourceRoundDispatchRequest) -> SourceRoundAdapterResult:
        assert request.selected_sources == ("fixture_source",)
        return SourceRoundAdapterResult(
            source="fixture_source",
            status="completed",
            candidates=(
                ResumeCandidate(
                    resume_id="fixture-resume-1",
                    source_resume_id="fixture-provider-1",
                    snapshot_sha256="fixture-snapshot",
                    dedup_key="fixture-person-1",
                    search_text="Python data platform engineer",
                    raw={"source": "fixture_source"},
                ),
            ),
            raw_candidate_count=1,
        )

    async def scenario():
        return await dispatch_source_rounds(
            request=SourceRoundDispatchRequest(
                runtime_run_id="run-1",
                round_no=1,
                logical_queries=(),
                selected_sources=("fixture_source",),
                seen_resume_ids=frozenset(),
                seen_dedup_keys=frozenset(),
                requirement_sheet=_requirement_sheet(),
            ),
            source_adapters={"fixture_source": fixture_adapter},
        )

    result = asyncio.run(scenario())

    assert result.source_results[0].source == "fixture_source"
    assert result.candidates[0].resume_id == "fixture-resume-1"


def test_source_round_dispatch_rejects_missing_registered_source_adapter() -> None:
    async def scenario():
        return await dispatch_source_rounds(
            request=SourceRoundDispatchRequest(
                runtime_run_id="run-1",
                round_no=1,
                logical_queries=(),
                selected_sources=("fixture_source",),
                seen_resume_ids=frozenset(),
                seen_dedup_keys=frozenset(),
                requirement_sheet=_requirement_sheet(),
            ),
            source_adapters={},
        )

    with pytest.raises(RuntimeSourceInvariantError, match="unsupported_source_kind:fixture_source"):
        asyncio.run(scenario())
