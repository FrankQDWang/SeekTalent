from seektalent.models import RoundRetrievalPlan, SecondLaneDecision
from seektalent.candidate_feedback.models import FeedbackCandidateExpression
from seektalent.candidate_feedback.policy import PRFGateInput, build_prf_policy_decision
from seektalent.retrieval import build_location_execution_plan
from seektalent.runtime.retrieval_runtime import build_logical_query_state
from seektalent.runtime.second_lane_runtime import build_second_lane_decision


def _retrieval_plan(*, query_terms: list[str]) -> RoundRetrievalPlan:
    return RoundRetrievalPlan(
        plan_version=2,
        round_no=2,
        query_terms=query_terms,
        role_anchor_terms=[query_terms[0]],
        must_have_anchor_terms=query_terms[1:],
        keyword_query=" ".join(query_terms),
        projected_provider_filters={},
        runtime_only_constraints=[],
        location_execution_plan=build_location_execution_plan(
            allowed_locations=["shanghai"],
            preferred_locations=["shanghai"],
            round_no=2,
            target_new=6,
        ),
        target_new=6,
        rationale="test",
    )


def test_build_second_lane_decision_falls_back_to_generic_when_prf_policy_is_unavailable() -> None:
    retrieval_plan = _retrieval_plan(query_terms=["python", "ranking"])

    decision, lane = build_second_lane_decision(
        round_no=2,
        retrieval_plan=retrieval_plan,
        query_term_pool=[],
        sent_query_history=[],
        prf_decision=None,
        run_id="run-a",
        job_intent_fingerprint="job-1",
        source_plan_version="2",
    )

    assert decision == SecondLaneDecision(
        round_no=2,
        attempted_prf=True,
        prf_gate_passed=False,
        selected_lane_type="generic_explore",
        selected_query_instance_id=decision.selected_query_instance_id,
        selected_query_fingerprint=decision.selected_query_fingerprint,
        reject_reasons=["prf_policy_not_available"],
        fallback_lane_type="generic_explore",
        fallback_query_fingerprint=decision.selected_query_fingerprint,
        prf_policy_version="unavailable",
        generic_explore_version="v1",
    )
    assert lane is not None
    assert lane.lane_type == "generic_explore"


def test_second_lane_decision_carries_llm_prf_metadata_without_prf_v1_5_fields() -> None:
    retrieval_plan = _retrieval_plan(query_terms=["python", "ranking"])

    decision, lane = build_second_lane_decision(
        round_no=2,
        retrieval_plan=retrieval_plan,
        query_term_pool=[],
        sent_query_history=[],
        prf_decision=None,
        run_id="run-a",
        job_intent_fingerprint="job-1",
        source_plan_version="2",
        prf_probe_proposal_backend="llm_deepseek_v4_flash",
        llm_prf_failure_kind="llm_prf_timeout",
        llm_prf_input_artifact_ref="round.02.retrieval.llm_prf_input",
        llm_prf_call_artifact_ref="round.02.retrieval.llm_prf_call",
        llm_prf_candidates_artifact_ref="round.02.retrieval.llm_prf_candidates",
        llm_prf_grounding_artifact_ref="round.02.retrieval.llm_prf_grounding",
    )

    assert lane is not None
    assert lane.lane_type == "generic_explore"
    assert decision.selected_lane_type == "generic_explore"
    assert decision.fallback_lane_type == "generic_explore"
    assert decision.prf_probe_proposal_backend == "llm_deepseek_v4_flash"
    assert decision.llm_prf_failure_kind == "llm_prf_timeout"
    assert decision.llm_prf_input_artifact_ref == "round.02.retrieval.llm_prf_input"
    assert decision.llm_prf_call_artifact_ref == "round.02.retrieval.llm_prf_call"
    assert decision.llm_prf_candidates_artifact_ref == "round.02.retrieval.llm_prf_candidates"
    assert decision.llm_prf_grounding_artifact_ref == "round.02.retrieval.llm_prf_grounding"
    dumped = decision.model_dump(mode="json")
    assert "prf_v1_5_mode" not in dumped
    assert "shadow_prf_v1_5_artifact_ref" not in dumped


def test_build_second_lane_decision_selects_prf_probe_when_gate_passes() -> None:
    retrieval_plan = _retrieval_plan(query_terms=["python", "ranking", "trace"])
    prf_decision = build_prf_policy_decision(
        PRFGateInput(
            round_no=2,
            seed_resume_ids=["seed-1", "seed-2"],
            seed_count=2,
            negative_resume_ids=[],
            candidate_expressions=[
                FeedbackCandidateExpression(
                    term_family_id="feedback.langgraph",
                    canonical_expression="LangGraph",
                    surface_forms=["LangGraph"],
                    candidate_term_type="technical_phrase",
                    source_seed_resume_ids=["seed-1", "seed-2"],
                    positive_seed_support_count=2,
                    negative_support_count=0,
                )
            ],
            candidate_expression_count=1,
            tried_term_family_ids=["feedback.python", "feedback.ranking", "feedback.trace"],
            tried_query_fingerprints=["fp-1", "fp-2"],
            min_seed_count=2,
            max_negative_support_rate=0.4,
            policy_version="prf-policy-v1",
        )
    )

    decision, lane = build_second_lane_decision(
        round_no=2,
        retrieval_plan=retrieval_plan,
        query_term_pool=[],
        sent_query_history=[],
        prf_decision=prf_decision,
        run_id="run-a",
        job_intent_fingerprint="job-1",
        source_plan_version="2",
    )

    assert lane is not None
    assert lane.lane_type == "prf_probe"
    assert lane.query_terms == ["python", "LangGraph"]
    assert decision.selected_lane_type == "prf_probe"
    assert decision.prf_gate_passed is True
    assert decision.accepted_prf_expression == "LangGraph"
    assert decision.accepted_prf_term_family_id == "feedback.langgraph"
    assert decision.prf_seed_resume_ids == ["seed-1", "seed-2"]
    assert decision.prf_candidate_expression_count == 1
    assert decision.prf_policy_version == "prf-policy-v1"


def test_build_logical_query_state_fingerprint_changes_with_filters_and_location_plan() -> None:
    base_location_plan = build_location_execution_plan(
        allowed_locations=["shanghai"],
        preferred_locations=["shanghai"],
        round_no=2,
        target_new=6,
    )
    other_location_plan = build_location_execution_plan(
        allowed_locations=["beijing"],
        preferred_locations=["beijing"],
        round_no=2,
        target_new=6,
    )

    base_state = build_logical_query_state(
        run_id="run-a",
        round_no=2,
        lane_type="exploit",
        query_terms=["python", "ranking"],
        job_intent_fingerprint="job-1",
        source_plan_version="2",
        provider_filters={"company_names": ["acme"]},
        location_execution_plan=base_location_plan,
    )
    changed_filters_state = build_logical_query_state(
        run_id="run-a",
        round_no=2,
        lane_type="exploit",
        query_terms=["python", "ranking"],
        job_intent_fingerprint="job-1",
        source_plan_version="2",
        provider_filters={"company_names": ["globex"]},
        location_execution_plan=base_location_plan,
    )
    changed_location_state = build_logical_query_state(
        run_id="run-a",
        round_no=2,
        lane_type="exploit",
        query_terms=["python", "ranking"],
        job_intent_fingerprint="job-1",
        source_plan_version="2",
        provider_filters={"company_names": ["acme"]},
        location_execution_plan=other_location_plan,
    )

    assert changed_filters_state.query_fingerprint != base_state.query_fingerprint
    assert changed_location_state.query_fingerprint != base_state.query_fingerprint
