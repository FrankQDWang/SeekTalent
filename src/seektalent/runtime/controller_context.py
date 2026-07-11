from __future__ import annotations

from math import ceil

from seektalent.models import (
    ControllerQueryExecutionReceipt,
    ControllerContext,
    QueryExecutionReceipt,
    QueryTermCandidate,
    RunState,
    ScoredCandidate,
    StopGuidance,
    TopPoolStrength,
    unique_strings,
    is_title_anchor_role,
)
from seektalent.requirements import build_requirement_digest
from seektalent.candidate_quality import risk_at_or_above, risk_at_or_below

from seektalent.runtime.context_views import (
    _reflection_summary,
    _search_observation_view,
    _top_pool_entry,
    top_candidates,
)
from seektalent.runtime.query_identity import consumed_non_anchor_term_family_ids, logical_outcomes_from_receipts, used_term_group_keys

BUDGET_STOP_RATIO = 0.8
STRONG_FIT_STOP_MIN = 3
HIGH_RISK_FIT_THRESHOLD = 70


def build_controller_context(
    *,
    run_state: RunState,
    round_no: int,
    min_rounds: int,
    max_rounds: int,
    target_new: int,
) -> ControllerContext:
    last_round = run_state.round_history[-1] if run_state.round_history else None
    previous_reflection = last_round.reflection_advice if last_round is not None else None
    latest_search_observation = last_round.search_observation if last_round is not None else None
    top_pool = top_candidates(run_state)
    retrieval_rounds_completed = len(run_state.round_history)
    rounds_remaining_after_current = max(0, max_rounds - round_no)
    budget_used_ratio = round_no / max_rounds
    budget_stop_round = ceil(max_rounds * BUDGET_STOP_RATIO)
    near_budget_limit = round_no >= budget_stop_round
    query_execution_ledger = run_state.retrieval_state.query_execution_ledger
    return ControllerContext(
        full_jd=run_state.input_truth.jd,
        full_notes=run_state.input_truth.notes,
        requirement_sheet=run_state.requirement_sheet,
        round_no=round_no,
        min_rounds=min_rounds,
        max_rounds=max_rounds,
        retrieval_rounds_completed=retrieval_rounds_completed,
        rounds_remaining_after_current=rounds_remaining_after_current,
        budget_used_ratio=budget_used_ratio,
        near_budget_limit=near_budget_limit,
        is_final_allowed_round=round_no >= max_rounds,
        target_new=target_new,
        stop_guidance=_build_stop_guidance(
            run_state=run_state,
            top_pool=top_pool,
            round_no=round_no,
            retrieval_rounds_completed=retrieval_rounds_completed,
            min_rounds=min_rounds,
            max_rounds=max_rounds,
        ),
        requirement_digest=build_requirement_digest(run_state.requirement_sheet),
        query_term_pool=run_state.retrieval_state.query_term_pool,
        current_top_pool=[_top_pool_entry(item) for item in top_pool],
        latest_search_observation=_search_observation_view(latest_search_observation),
        previous_reflection=_reflection_summary(previous_reflection),
        latest_reflection_keyword_advice=previous_reflection.keyword_advice if previous_reflection else None,
        latest_reflection_filter_advice=previous_reflection.filter_advice if previous_reflection else None,
        sent_query_history=run_state.retrieval_state.sent_query_history,
        tried_query_terms=_tried_query_terms(
            query_term_pool=run_state.retrieval_state.query_term_pool,
            query_execution_ledger=query_execution_ledger,
        ),
        recent_query_execution_receipts=_recent_query_execution_receipts(query_execution_ledger),
        used_term_group_keys=sorted(used_term_group_keys(query_execution_ledger)),
        consumed_non_anchor_term_family_ids=sorted(consumed_non_anchor_term_family_ids(query_execution_ledger)),
        previous_query_outcomes=last_round.query_outcomes[-2:] if last_round is not None else [],
        shortage_history=[
            round_state.search_observation.shortage_count
            for round_state in run_state.round_history
            if round_state.search_observation is not None
        ],
        latest_canonical_intake_summary=run_state.latest_canonical_intake_summary,
        budget_reminder=_budget_reminder(
            round_no=round_no,
            retrieval_rounds_completed=retrieval_rounds_completed,
            max_rounds=max_rounds,
            budget_stop_round=budget_stop_round,
            rounds_remaining_after_current=rounds_remaining_after_current,
            near_budget_limit=near_budget_limit,
        ),
    )


def _build_stop_guidance(
    *,
    run_state: RunState,
    top_pool: list[ScoredCandidate],
    round_no: int,
    retrieval_rounds_completed: int,
    min_rounds: int,
    max_rounds: int,
) -> StopGuidance:
    top_pool_strength = _top_pool_strength(top_pool)
    fit_candidates = [item for item in top_pool if item.fit_bucket == "fit"]
    strong_fit_count = len(_strong_fit_candidates(top_pool))
    high_risk_fit_count = sum(
        1 for item in fit_candidates if risk_at_or_above(item.risk_score, HIGH_RISK_FIT_THRESHOLD)
    )
    tried_families = _tried_families(
        run_state.retrieval_state.query_term_pool,
        run_state.retrieval_state.query_execution_ledger,
    )
    untried_families = _untried_admitted_families(
        run_state.retrieval_state.query_term_pool,
        tried_families,
    )
    broadening_attempted = _broadening_attempted(run_state)
    productive_round_count = sum(
        1
        for round_state in run_state.round_history
        if round_state.search_observation is not None and round_state.search_observation.unique_new_count > 0
    )
    zero_gain_round_count = sum(
        1
        for round_state in run_state.round_history
        if round_state.search_observation is not None and round_state.search_observation.unique_new_count == 0
    )

    continue_reasons: list[str] = []
    quality_gate_status = "pass"
    budget_stop_round = ceil(max_rounds * BUDGET_STOP_RATIO)
    if retrieval_rounds_completed < min_rounds:
        continue_reasons.append(
            f"{retrieval_rounds_completed} retrieval rounds completed; min_rounds is {min_rounds}."
        )
        reason = continue_reasons[0]
    elif round_no >= budget_stop_round:
        quality_gate_status = "budget_stop_allowed"
        reason = f"round {round_no} reached the {budget_stop_round}/{max_rounds} near-budget stop threshold."
    else:
        if top_pool_strength in {"empty", "weak"}:
            if untried_families:
                continue_reasons.append("top pool is weak and admitted families remain untried.")
                quality_gate_status = "continue_low_quality"
            elif broadening_attempted:
                quality_gate_status = "low_quality_exhausted"
                reason = f"top pool is {top_pool_strength}, but no admitted families remain untried."
            else:
                continue_reasons.append(
                    f"top pool is {top_pool_strength} and no active admitted families remain untried; "
                    "one broaden round is required before stopping."
                )
                quality_gate_status = "broaden_required"
        elif top_pool_strength == "usable" and strong_fit_count < STRONG_FIT_STOP_MIN:
            if untried_families:
                continue_reasons.append(
                    f"top pool is usable but has only {strong_fit_count} strong-fit candidates; admitted families remain untried."
                )
                quality_gate_status = "continue_low_quality"
            elif broadening_attempted:
                quality_gate_status = "low_quality_exhausted"
                reason = (
                    f"top pool is usable with only {strong_fit_count} strong-fit candidates, "
                    "but no admitted families remain untried."
                )
            else:
                continue_reasons.append(
                    f"top pool is usable but has only {strong_fit_count} strong-fit candidates, "
                    "and no active admitted families remain untried; one broaden round is required before stopping."
                )
                quality_gate_status = "broaden_required"
        elif top_pool_strength != "strong" and productive_round_count < 2 and untried_families:
            continue_reasons.append(
                "top pool is not strong, fewer than two rounds were productive, and admitted families remain untried."
            )
            quality_gate_status = "continue_low_quality"
        if continue_reasons:
            reason = continue_reasons[0]
        elif quality_gate_status == "pass":
            reason = "stop allowed by budget, coverage, and quality guidance."

    return StopGuidance(
        can_stop=not continue_reasons,
        reason=reason,
        continue_reasons=continue_reasons,
        tried_families=tried_families,
        untried_admitted_families=untried_families,
        productive_round_count=productive_round_count,
        zero_gain_round_count=zero_gain_round_count,
        top_pool_strength=top_pool_strength,
        fit_count=len(fit_candidates),
        strong_fit_count=strong_fit_count,
        high_risk_fit_count=high_risk_fit_count,
        quality_gate_status=quality_gate_status,
        broadening_attempted=broadening_attempted,
    )


def _top_pool_strength(top_pool: list[ScoredCandidate]) -> TopPoolStrength:
    if not top_pool:
        return "empty"
    fit_candidates = [item for item in top_pool if item.fit_bucket == "fit"]
    if len(top_pool) < 5 or not fit_candidates:
        return "weak"
    if len(top_pool) >= 10 and len(_strong_fit_candidates(top_pool)) >= 5:
        return "strong"
    return "usable"


def _strong_fit_candidates(top_pool: list[ScoredCandidate]) -> list[ScoredCandidate]:
    return [
        item
        for item in top_pool
        if item.fit_bucket == "fit"
        and item.overall_score >= 80
        and item.must_have_match_score >= 70
        and risk_at_or_below(item.risk_score, 30)
    ]


def _budget_reminder(
    *,
    round_no: int,
    retrieval_rounds_completed: int,
    max_rounds: int,
    budget_stop_round: int,
    rounds_remaining_after_current: int,
    near_budget_limit: bool,
) -> str:
    return (
        f"Budget reminder: current controller round {round_no}; "
        f"completed retrieval rounds {retrieval_rounds_completed}; "
        f"max_rounds {max_rounds}; 80% stop threshold starts at round {budget_stop_round}; "
        f"rounds remaining after current decision {rounds_remaining_after_current}; "
        f"near_budget_limit={near_budget_limit}."
    )


def _tried_families(
    query_term_pool: list[QueryTermCandidate],
    query_execution_ledger,
) -> list[str]:
    term_index = {_term_key(item.term): item for item in query_term_pool}
    return unique_strings(
        candidate.family
        for receipt in query_execution_ledger
        if receipt.dispatch_started
        for term in receipt.query_terms
        if (candidate := term_index.get(_term_key(term))) is not None
    )


def _tried_query_terms(
    *,
    query_term_pool: list[QueryTermCandidate],
    query_execution_ledger: list[QueryExecutionReceipt],
) -> list[str]:
    started_terms = {
        _term_key(term)
        for receipt in query_execution_ledger
        if receipt.dispatch_started
        for term in receipt.query_terms
        if _term_key(term)
    }
    tried_terms: list[str] = []
    seen_terms: set[str] = set()
    for candidate in query_term_pool:
        term_key = _term_key(candidate.term)
        if term_key in started_terms and term_key not in seen_terms:
            tried_terms.append(candidate.term)
            seen_terms.add(term_key)
    return tried_terms


def _recent_query_execution_receipts(
    query_execution_ledger: list[QueryExecutionReceipt],
) -> list[ControllerQueryExecutionReceipt]:
    receipts_by_query_instance_id: dict[str, list[QueryExecutionReceipt]] = {}
    recent_query_instance_ids: list[str] = []
    for receipt in query_execution_ledger:
        if not receipt.dispatch_started:
            continue
        query_instance_id = receipt.query_instance_id
        receipts_by_query_instance_id.setdefault(query_instance_id, []).append(receipt)
        if query_instance_id in recent_query_instance_ids:
            recent_query_instance_ids.remove(query_instance_id)
        recent_query_instance_ids.append(query_instance_id)

    recent_receipts: list[ControllerQueryExecutionReceipt] = []
    for query_instance_id in recent_query_instance_ids[-6:]:
        receipts = receipts_by_query_instance_id[query_instance_id]
        outcome = logical_outcomes_from_receipts(receipts)[0]
        latest_receipt = receipts[-1]
        recent_receipts.append(
            ControllerQueryExecutionReceipt(
                round_no=latest_receipt.round_no,
                query_instance_id=query_instance_id,
                query_terms=list(outcome.query_terms),
                keyword_query=outcome.keyword_query,
                status=outcome.status,
            )
        )
    return recent_receipts


def _untried_admitted_families(
    query_term_pool: list[QueryTermCandidate],
    tried_families: list[str],
) -> list[str]:
    tried = set(tried_families)
    family_candidates: dict[str, QueryTermCandidate] = {}
    for item in query_term_pool:
        if not item.active or item.queryability != "admitted" or is_title_anchor_role(item.retrieval_role):
            continue
        if item.family in tried:
            continue
        family_candidates.setdefault(item.family, item)
    return [
        item.family
        for item in sorted(
            family_candidates.values(),
            key=lambda item: (item.priority, item.first_added_round, item.family),
        )
    ]


def _broadening_attempted(run_state: RunState) -> bool:
    if run_state.retrieval_state.anchor_only_broaden_attempted:
        return True
    return any(
        str(item.get("selected_lane") or "") in {"reserve_broaden", "anchor_only"}
        for item in run_state.retrieval_state.rescue_lane_history
    )


def _term_key(term: str) -> str:
    return " ".join(term.strip().split()).casefold()
