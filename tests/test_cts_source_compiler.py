from __future__ import annotations

from seektalent.models import AgeRequirement, HardConstraintSlots, ProposedFilterPlan, RequirementSheet
from seektalent.runtime.logical_query_dispatch import LogicalQueryDispatch
from seektalent.runtime.source_filters import build_runtime_filter_intents
from seektalent.runtime.source_query_intent import build_runtime_source_query_intents
from seektalent.runtime.source_lanes import RuntimeSourceBudgetPolicy
from seektalent.providers.cts.source_compiler import compile_cts_source_query_intents


def test_cts_source_compiler_projects_runtime_filter_intent_to_native_filters() -> None:
    requirement_sheet = RequirementSheet(
        job_title="Data Engineer",
        title_anchor_terms=["data engineer"],
        title_anchor_rationale="Title anchor.",
        role_summary="Build data platforms.",
        hard_constraints=HardConstraintSlots(
            age_requirement=AgeRequirement(min_age=30, max_age=35, raw_text="30-35"),
        ),
        initial_query_term_pool=[],
        scoring_rationale="Prefer data platform fit.",
    )
    filter_intents = build_runtime_filter_intents(
        requirement_sheet=requirement_sheet,
        proposed_filter_plan=ProposedFilterPlan(
            optional_filters={
                "age_requirement": ["min=30", "max=35"],
                "position": "Data Engineer",
            }
        ),
    )
    source_intents = build_runtime_source_query_intents(
        source_kinds=("cts",),
        logical_dispatches=(
            LogicalQueryDispatch(
                round_no=1,
                query_role="exploit",
                lane_type="exploit",
                query_instance_id="query-1",
                query_fingerprint="fingerprint-1",
                query_terms=("data engineer", "spark"),
                keyword_query="data engineer spark",
                requested_count=7,
                source_plan_version="1",
            ),
        ),
        filter_intents=filter_intents,
        location_intent=None,
        age_intent=None,
        source_budget_policy=RuntimeSourceBudgetPolicy(cts_page_size=10),
    )

    compiled = compile_cts_source_query_intents(source_intents["cts"])

    assert len(compiled) == 1
    assert compiled[0].intent.query_role == "exploit"
    assert compiled[0].intent.lane_type == "exploit"
    assert compiled[0].provider_filters == {
        "age": 3,
        "position": "Data Engineer",
    }
    assert compiled[0].runtime_only_constraints == ()

