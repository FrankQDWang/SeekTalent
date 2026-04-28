from __future__ import annotations

from seektalent.candidate_feedback import (
    build_feedback_decision,
    extract_feedback_candidate_expressions,
    select_feedback_seed_resumes,
)
from seektalent.models import (
    QueryTermCandidate,
    RetrievalState,
    RunState,
    SearchControllerDecision,
    is_primary_anchor_role,
    is_title_anchor_role,
)
from seektalent.progress import ProgressCallback
from seektalent.providers.cts.filter_projection import build_default_filter_plan
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
) -> SearchControllerDecision | None:
    seeds = select_feedback_seed_resumes(
        [
            run_state.scorecards_by_resume_id[resume_id]
            for resume_id in run_state.top_pool_ids
            if resume_id in run_state.scorecards_by_resume_id
        ]
    )
    negatives = [
        item
        for item in run_state.scorecards_by_resume_id.values()
        if item.fit_bucket == "not_fit" or item.risk_score > 60
    ]
    sent_terms = [term for record in run_state.retrieval_state.sent_query_history for term in record.query_terms]
    feedback = build_feedback_decision(
        seed_resumes=seeds,
        negative_resumes=negatives,
        existing_terms=run_state.retrieval_state.query_term_pool,
        sent_query_terms=sent_terms,
        round_no=round_no,
    )
    shared_expression_evidence = extract_feedback_candidate_expressions(
        seed_resumes=seeds,
        negative_resumes=negatives,
        known_company_entities=set(),
        known_product_platforms=set(),
    )
    tracer.write_json(
        _round_artifact(tracer, round_no=round_no, name="candidate_feedback_input"),
        {
            "seed_resume_ids": [item.resume_id for item in seeds],
            "negative_resume_ids": [item.resume_id for item in negatives],
            "sent_query_terms": sent_terms,
        },
    )
    tracer.write_json(
        _round_artifact(tracer, round_no=round_no, name="candidate_feedback_expression_evidence"),
        [item.model_dump(mode="json") for item in shared_expression_evidence],
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
        },
    )
    if feedback.accepted_term is None:
        return None
    run_state.retrieval_state.query_term_pool.append(feedback.accepted_term)
    emit_progress(
        progress_callback,
        "rescue_lane_completed",
        f"Recall repair: extracted feedback term {feedback.accepted_term.term} from {len(seeds)} fit seed resumes.",
        round_no=round_no,
        payload={
            "stage": "rescue",
            "selected_lane": "candidate_feedback",
            "accepted_term": feedback.accepted_term.term,
            "seed_resume_count": len(seeds),
        },
    )
    return SearchControllerDecision(
        thought_summary="Runtime rescue: candidate feedback expansion.",
        action="search_cts",
        decision_rationale=f"Runtime rescue: candidate feedback term {feedback.accepted_term.term}; {reason}",
        proposed_query_terms=feedback.forced_query_terms,
        proposed_filter_plan=build_default_filter_plan(run_state.requirement_sheet),
        response_to_reflection=f"Runtime rescue: {reason}",
    )


def force_anchor_only_decision(*, run_state: RunState, round_no: int, reason: str) -> SearchControllerDecision:
    del round_no
    anchor = active_admitted_anchor(run_state.retrieval_state.query_term_pool)
    return SearchControllerDecision(
        thought_summary="Runtime rescue: final anchor-only broaden.",
        action="search_cts",
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
        action="search_cts",
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
    candidates = [
        item
        for item in retrieval_state.query_term_pool
        if item.queryability == "admitted" and not is_title_anchor_role(item.retrieval_role) and item.family not in tried
    ]
    return min(
        candidates,
        key=lambda item: (0 if item.active else 1, item.priority, item.first_added_round, item.family),
        default=None,
    )


def tried_query_families(retrieval_state: RetrievalState) -> set[str]:
    term_index = {query_term_key(item.term): item for item in retrieval_state.query_term_pool}
    return {
        candidate.family
        for record in retrieval_state.sent_query_history
        for term in record.query_terms
        if (candidate := term_index.get(query_term_key(term))) is not None
    }


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
