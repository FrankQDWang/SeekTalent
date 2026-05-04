from __future__ import annotations

from seektalent.candidate_feedback.policy import PRFPolicyDecision
from seektalent.models import QueryTermCandidate, RoundRetrievalPlan, SecondLaneDecision, SentQueryRecord
from seektalent.retrieval import derive_explore_query_terms
from seektalent.runtime.retrieval_runtime import LogicalQueryState, build_logical_query_state


def _fallback_explore_terms(
    *,
    retrieval_plan: RoundRetrievalPlan,
    query_term_pool: list[QueryTermCandidate],
    sent_query_history: list[SentQueryRecord],
) -> list[str] | None:
    try:
        return derive_explore_query_terms(
            retrieval_plan.query_terms,
            title_anchor_terms=[],
            query_term_pool=query_term_pool,
            sent_query_history=sent_query_history,
        )
    except ValueError:
        if query_term_pool:
            raise
        return list(retrieval_plan.query_terms)


def build_second_lane_decision(
    *,
    round_no: int,
    retrieval_plan: RoundRetrievalPlan,
    query_term_pool: list[QueryTermCandidate],
    sent_query_history: list[SentQueryRecord],
    prf_decision: PRFPolicyDecision | None,
    run_id: str,
    job_intent_fingerprint: str,
    source_plan_version: str,
    prf_v1_5_mode: str = "disabled",
    shadow_prf_v1_5_artifact_ref: str | None = None,
    prf_probe_proposal_backend: str | None = None,
    llm_prf_failure_kind: str | None = None,
    llm_prf_input_artifact_ref: str | None = None,
    llm_prf_call_artifact_ref: str | None = None,
    llm_prf_candidates_artifact_ref: str | None = None,
    llm_prf_grounding_artifact_ref: str | None = None,
) -> tuple[SecondLaneDecision, LogicalQueryState | None]:
    llm_prf_metadata = {
        "prf_probe_proposal_backend": prf_probe_proposal_backend,
        "llm_prf_failure_kind": llm_prf_failure_kind,
        "llm_prf_input_artifact_ref": llm_prf_input_artifact_ref,
        "llm_prf_call_artifact_ref": llm_prf_call_artifact_ref,
        "llm_prf_candidates_artifact_ref": llm_prf_candidates_artifact_ref,
        "llm_prf_grounding_artifact_ref": llm_prf_grounding_artifact_ref,
    }
    if round_no == 1 or len(retrieval_plan.query_terms) <= 1:
        return (
            SecondLaneDecision(
                round_no=round_no,
                attempted_prf=False,
                prf_gate_passed=False,
                reject_reasons=["round_one_or_anchor_only"],
                no_fetch_reason="single_lane_round",
                prf_policy_version="unavailable",
                prf_v1_5_mode=prf_v1_5_mode,
                shadow_prf_v1_5_artifact_ref=shadow_prf_v1_5_artifact_ref,
                **llm_prf_metadata,
            ),
            None,
        )

    if prf_decision is not None and prf_decision.gate_passed and prf_decision.accepted_expression is not None:
        accepted_expression = prf_decision.accepted_expression
        prf_terms = [_select_prf_anchor(retrieval_plan), accepted_expression.canonical_expression]
        query_state = build_logical_query_state(
            run_id=run_id,
            round_no=round_no,
            lane_type="prf_probe",
            query_terms=prf_terms,
            job_intent_fingerprint=job_intent_fingerprint,
            source_plan_version=source_plan_version,
            provider_filters=retrieval_plan.projected_provider_filters,
            location_execution_plan=retrieval_plan.location_execution_plan,
        )
        return (
            SecondLaneDecision(
                round_no=round_no,
                attempted_prf=True,
                prf_gate_passed=True,
                selected_lane_type="prf_probe",
                selected_query_instance_id=query_state.query_instance_id,
                selected_query_fingerprint=query_state.query_fingerprint,
                accepted_prf_expression=accepted_expression.canonical_expression,
                accepted_prf_term_family_id=accepted_expression.term_family_id,
                prf_seed_resume_ids=list(prf_decision.gate_input.seed_resume_ids),
                prf_candidate_expression_count=prf_decision.gate_input.candidate_expression_count,
                prf_policy_version=prf_decision.gate_input.policy_version,
                prf_v1_5_mode=prf_v1_5_mode,
                shadow_prf_v1_5_artifact_ref=shadow_prf_v1_5_artifact_ref,
                **llm_prf_metadata,
            ),
            query_state,
        )

    explore_terms = _fallback_explore_terms(
        retrieval_plan=retrieval_plan,
        query_term_pool=query_term_pool,
        sent_query_history=sent_query_history,
    )
    reject_reasons = prf_decision.reject_reasons if prf_decision is not None else ["prf_policy_not_available"]
    prf_policy_version = prf_decision.gate_input.policy_version if prf_decision is not None else "unavailable"
    prf_seed_resume_ids = list(prf_decision.gate_input.seed_resume_ids) if prf_decision is not None else []
    prf_candidate_expression_count = prf_decision.gate_input.candidate_expression_count if prf_decision is not None else 0
    if not explore_terms:
        return (
            SecondLaneDecision(
                round_no=round_no,
                attempted_prf=True,
                prf_gate_passed=False,
                prf_seed_resume_ids=prf_seed_resume_ids,
                prf_candidate_expression_count=prf_candidate_expression_count,
                reject_reasons=reject_reasons,
                no_fetch_reason="no_generic_explore_query",
                prf_policy_version=prf_policy_version,
                generic_explore_version="v1",
                prf_v1_5_mode=prf_v1_5_mode,
                shadow_prf_v1_5_artifact_ref=shadow_prf_v1_5_artifact_ref,
                **llm_prf_metadata,
            ),
            None,
        )

    query_state = build_logical_query_state(
        run_id=run_id,
        round_no=round_no,
        lane_type="generic_explore",
        query_terms=explore_terms,
        job_intent_fingerprint=job_intent_fingerprint,
        source_plan_version=source_plan_version,
        provider_filters=retrieval_plan.projected_provider_filters,
        location_execution_plan=retrieval_plan.location_execution_plan,
    )
    return (
        SecondLaneDecision(
            round_no=round_no,
            attempted_prf=True,
            prf_gate_passed=False,
            selected_lane_type="generic_explore",
            selected_query_instance_id=query_state.query_instance_id,
            selected_query_fingerprint=query_state.query_fingerprint,
            prf_seed_resume_ids=prf_seed_resume_ids,
            prf_candidate_expression_count=prf_candidate_expression_count,
            reject_reasons=reject_reasons,
            fallback_lane_type="generic_explore",
            fallback_query_fingerprint=query_state.query_fingerprint,
            prf_policy_version=prf_policy_version,
            generic_explore_version="v1",
            prf_v1_5_mode=prf_v1_5_mode,
            shadow_prf_v1_5_artifact_ref=shadow_prf_v1_5_artifact_ref,
            **llm_prf_metadata,
        ),
        query_state,
    )


def _select_prf_anchor(retrieval_plan: RoundRetrievalPlan) -> str:
    for candidates in (
        retrieval_plan.role_anchor_terms,
        retrieval_plan.must_have_anchor_terms,
        retrieval_plan.query_terms,
    ):
        for term in candidates:
            if term.strip():
                return term
    raise ValueError("PRF second lane requires at least one anchor term.")
