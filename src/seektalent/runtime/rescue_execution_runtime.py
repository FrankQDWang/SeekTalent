from __future__ import annotations

from seektalent.candidate_feedback.models import (
    CandidateFeedbackDecision,
    FeedbackCandidateExpression,
    FeedbackCandidateTerm,
)
from seektalent.candidate_feedback.policy import PRFPolicyDecision
from seektalent.core.filter_plan import build_default_filter_plan
from seektalent.models import (
    QueryTermCandidate,
    RetrievalState,
    RunState,
    SearchControllerDecision,
    is_primary_anchor_role,
    is_title_anchor_role,
)
from seektalent.progress import ProgressCallback
from seektalent.retrieval.query_identity import build_term_group_key
from seektalent.runtime.query_identity import consumed_non_anchor_term_family_ids, used_term_group_keys
from seektalent.tracing import RunTracer


def _round_artifact(tracer: RunTracer, *, round_no: int, name: str) -> str:
    logical_name = f"round.{round_no:02d}.retrieval.{name}"
    tracer.session.register_path(
        logical_name,
        f"rounds/{round_no:02d}/retrieval/{name}.json",
        content_type="application/json",
        schema_version="v1",
    )
    return logical_name


def force_candidate_feedback_decision(
    *,
    run_state: RunState,
    round_no: int,
    reason: str,
    tracer: RunTracer,
    progress_callback: ProgressCallback | None,
    emit_progress,
    prf_decision: PRFPolicyDecision,
    proposal_backend: str,
) -> SearchControllerDecision | None:
    sent_terms = [
        term
        for receipt in run_state.retrieval_state.query_execution_ledger
        if receipt.dispatch_started
        for term in receipt.query_terms
    ]
    feedback = _feedback_decision_from_prf(
        run_state=run_state,
        round_no=round_no,
        prf_decision=prf_decision,
    )
    tracer.write_json(
        _round_artifact(tracer, round_no=round_no, name="candidate_feedback_input"),
        {
            "proposal_backend": proposal_backend,
            "seed_resume_ids": prf_decision.gate_input.seed_resume_ids,
            "negative_resume_ids": prf_decision.gate_input.negative_resume_ids,
            "sent_query_terms": sent_terms,
        },
    )
    tracer.write_json(
        _round_artifact(tracer, round_no=round_no, name="candidate_feedback_expression_evidence"),
        [item.model_dump(mode="json") for item in prf_decision.candidate_expressions],
    )
    tracer.write_json(
        _round_artifact(tracer, round_no=round_no, name="candidate_feedback_terms"),
        feedback.model_dump(mode="json"),
    )
    run_state.retrieval_state.candidate_feedback_attempted = True
    tracer.write_json(
        _round_artifact(tracer, round_no=round_no, name="candidate_feedback_decision"),
        {
            "accepted_term": (
                feedback.accepted_term.model_dump(mode="json") if feedback.accepted_term is not None else None
            ),
            "forced_query_terms": feedback.forced_query_terms,
            "skipped_reason": feedback.skipped_reason,
            "proposal_backend": proposal_backend,
            "prf_gate_passed": prf_decision.gate_passed,
            "prf_reject_reasons": prf_decision.reject_reasons,
        },
    )
    if feedback.accepted_term is None or feedback.accepted_term.family in consumed_non_anchor_term_family_ids(
        run_state.retrieval_state.query_execution_ledger
    ):
        return None
    run_state.retrieval_state.query_term_pool.append(feedback.accepted_term)
    emit_progress(
        progress_callback,
        "rescue_lane_completed",
        (
            f"Recall repair: accepted grounded feedback term {feedback.accepted_term.term} "
            f"from {len(prf_decision.gate_input.seed_resume_ids)} fit seed resumes."
        ),
        round_no=round_no,
        payload={
            "stage": "rescue",
            "selected_lane": "candidate_feedback",
            "accepted_term": feedback.accepted_term.term,
            "seed_resume_count": len(prf_decision.gate_input.seed_resume_ids),
        },
    )
    return SearchControllerDecision(
        thought_summary="Runtime rescue: candidate feedback expansion.",
        action="source_search",
        decision_rationale=f"Runtime rescue: candidate feedback term {feedback.accepted_term.term}; {reason}",
        proposed_query_terms=feedback.forced_query_terms,
        proposed_filter_plan=build_default_filter_plan(run_state.requirement_sheet),
        response_to_reflection=f"Runtime rescue: {reason}",
    )


def _feedback_decision_from_prf(
    *,
    run_state: RunState,
    round_no: int,
    prf_decision: PRFPolicyDecision,
) -> CandidateFeedbackDecision:
    seed_resume_ids = list(prf_decision.gate_input.seed_resume_ids)
    candidate_terms = [_feedback_candidate_term(item) for item in prf_decision.candidate_expressions]
    accepted_expression = prf_decision.accepted_expression if prf_decision.gate_passed else None
    if accepted_expression is None:
        return CandidateFeedbackDecision(
            seed_resume_ids=seed_resume_ids,
            candidate_terms=candidate_terms,
            rejected_terms=[item for item in candidate_terms if item.rejection_reason is not None],
            skipped_reason="no_safe_feedback_term",
        )

    anchor = active_admitted_anchor(run_state.retrieval_state.query_term_pool)
    supporting_resume_ids = list(accepted_expression.source_seed_resume_ids)
    accepted_candidate = _feedback_candidate_term(accepted_expression)
    accepted_term = QueryTermCandidate(
        term=accepted_expression.canonical_expression,
        source="candidate_feedback",
        category="expansion",
        priority=1,
        evidence=(
            f"Grounded in {len(supporting_resume_ids)} seed resumes: "
            f"{', '.join(supporting_resume_ids)}."
        ),
        first_added_round=round_no,
        active=True,
        retrieval_role="core_skill",
        queryability="admitted",
        family=accepted_expression.term_family_id,
    )
    return CandidateFeedbackDecision(
        seed_resume_ids=seed_resume_ids,
        candidate_terms=candidate_terms,
        rejected_terms=[item for item in candidate_terms if item.rejection_reason is not None],
        accepted_candidates=[accepted_candidate],
        accepted_term=accepted_term,
        forced_query_terms=[anchor.term, accepted_term.term],
    )


def _feedback_candidate_term(expression: FeedbackCandidateExpression) -> FeedbackCandidateTerm:
    return FeedbackCandidateTerm(
        term=expression.canonical_expression,
        supporting_resume_ids=list(expression.source_seed_resume_ids),
        linked_requirements=list(expression.linked_requirements),
        field_hits=dict(expression.field_hits),
        fit_support_rate=expression.fit_support_rate,
        not_fit_support_rate=expression.not_fit_support_rate,
        score=expression.score,
        risk_flags=list(expression.reject_reasons),
        rejection_reason=expression.reject_reasons[0] if expression.reject_reasons else None,
    )


def force_anchor_only_decision(*, run_state: RunState, round_no: int, reason: str) -> SearchControllerDecision:
    del round_no
    anchor = active_admitted_anchor(run_state.retrieval_state.query_term_pool)
    return SearchControllerDecision(
        thought_summary="Runtime rescue: final anchor-only broaden.",
        action="source_search",
        decision_rationale=f"Runtime broaden: anchor-only search; {reason}",
        proposed_query_terms=[anchor.term],
        proposed_filter_plan=build_default_filter_plan(run_state.requirement_sheet),
        response_to_reflection=f"Runtime rescue: {reason}",
    )


def force_broaden_decision(*, run_state: RunState, round_no: int, reason: str) -> SearchControllerDecision:
    del round_no
    anchor = active_admitted_anchor(run_state.retrieval_state.query_term_pool)
    reserve = untried_admitted_non_anchor_reserve(run_state.retrieval_state)
    if reserve is None:
        query_terms = [anchor.term]
        broaden_detail = "anchor-only search"
    else:
        run_state.retrieval_state.query_term_pool = activate_query_term(
            run_state.retrieval_state.query_term_pool,
            reserve.term,
        )
        query_terms = [anchor.term, reserve.term]
        broaden_detail = f"reserve admitted family {reserve.family}"
    rationale = f"Runtime broaden: {broaden_detail}; {reason}"
    return SearchControllerDecision(
        thought_summary="Runtime override: broaden before low-quality stop.",
        action="source_search",
        decision_rationale=rationale,
        proposed_query_terms=query_terms,
        proposed_filter_plan=build_default_filter_plan(run_state.requirement_sheet),
        response_to_reflection=f"Runtime override: {reason}",
    )


def active_admitted_anchor(query_term_pool: list[QueryTermCandidate]) -> QueryTermCandidate:
    anchors = sorted(
        [
            item
            for item in query_term_pool
            if item.active and item.queryability == "admitted" and is_primary_anchor_role(item.retrieval_role)
        ],
        key=lambda item: (item.priority, item.first_added_round, item.term.casefold()),
    )
    if not anchors:
        raise ValueError("compiled query term pool must include one active admitted anchor.")
    return anchors[0]


def untried_admitted_non_anchor_reserve(retrieval_state: RetrievalState) -> QueryTermCandidate | None:
    tried = tried_query_families(retrieval_state)
    anchor = active_admitted_anchor(retrieval_state.query_term_pool)
    used_keys = used_term_group_keys(retrieval_state.query_execution_ledger)
    candidates = [
        item
        for item in retrieval_state.query_term_pool
        if item.queryability == "admitted" and not is_title_anchor_role(item.retrieval_role) and item.family not in tried
    ]
    for candidate in sorted(
        candidates,
        key=lambda item: (0 if item.active else 1, item.priority, item.first_added_round, item.family),
    ):
        term_group_key = build_term_group_key(
            query_terms=[anchor.term, candidate.term],
            query_term_pool=retrieval_state.query_term_pool,
        )
        if term_group_key not in used_keys:
            return candidate
    return None


def tried_query_families(retrieval_state: RetrievalState) -> set[str]:
    return consumed_non_anchor_term_family_ids(retrieval_state.query_execution_ledger)


def activate_query_term(
    query_term_pool: list[QueryTermCandidate],
    term: str,
) -> list[QueryTermCandidate]:
    key = query_term_key(term)
    return [
        item.model_copy(update={"active": True}) if query_term_key(item.term) == key else item
        for item in query_term_pool
    ]


def query_term_key(term: str) -> str:
    return " ".join(term.strip().split()).casefold()


def has_novel_anchor_only_group(retrieval_state: RetrievalState) -> bool:
    anchor = active_admitted_anchor(retrieval_state.query_term_pool)
    term_group_key = build_term_group_key(
        query_terms=[anchor.term],
        query_term_pool=retrieval_state.query_term_pool,
    )
    return term_group_key not in used_term_group_keys(retrieval_state.query_execution_ledger)
