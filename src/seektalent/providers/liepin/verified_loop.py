from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import Callable

from seektalent.corpus.runtime import ProviderReturnedCandidate
from seektalent.providers.liepin.mapper import LiepinMappedCandidate, map_liepin_worker_detail
from seektalent.providers.liepin.policy import (
    LiepinCardCandidate,
    LiepinDetailOpenPlan,
    build_detail_open_plan,
)
from seektalent.providers.liepin.store import LiepinDetailAttemptRow, LiepinStore
from seektalent.providers.liepin.worker_contracts import (
    LiepinDetailOpenRequest,
    LiepinDetailOpenRequestItem,
    LiepinDetailOpenResult,
)
from seektalent.models import ScoredCandidate


@dataclass(frozen=True)
class LiepinDetailOpenLoopResult:
    plan: LiepinDetailOpenPlan
    attempts: list[LiepinDetailAttemptRow]
    detail_candidates: list[LiepinMappedCandidate]


async def execute_liepin_detail_open_plan(
    *,
    store: LiepinStore,
    worker_client: object,
    card_candidates: list[LiepinCardCandidate],
    tenant_id: str,
    workspace_id: str,
    actor_id: str,
    provider_account_hash: str,
    budget_date: str,
    provider_day_key: str,
    timezone: str,
    daily_detail_budget: int,
    detail_open_policy_version: str,
    run_id: str,
    query_instance_id: str,
    query_fingerprint: str,
    already_opened_provider_ids: set[str] | None = None,
    already_seen_weak_fingerprints: set[str] | None = None,
    min_card_value_score: float = 0.0,
    record_provider_return: Callable[[ProviderReturnedCandidate], None] | None = None,
    score_metadata_by_candidate_id: dict[str, dict[str, object]] | None = None,
) -> LiepinDetailOpenLoopResult:
    consumed = store.count_detail_budget_consumed(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_id=actor_id,
        provider_account_hash=provider_account_hash,
        provider_day_key=provider_day_key,
    )
    plan = build_detail_open_plan(
        candidates=card_candidates,
        already_opened_provider_ids=already_opened_provider_ids or set(),
        daily_detail_budget=daily_detail_budget,
        consumed_detail_budget=consumed,
        already_seen_weak_fingerprints=already_seen_weak_fingerprints,
        min_card_value_score=min_card_value_score,
    )
    selected_by_id = {
        candidate.candidate_id: candidate
        for candidate, decision in zip(card_candidates, plan.decisions, strict=True)
        if decision.action == "open_detail"
    }
    if not selected_by_id:
        return LiepinDetailOpenLoopResult(plan=plan, attempts=[], detail_candidates=[])

    attempts: list[LiepinDetailAttemptRow] = []
    request_items: list[LiepinDetailOpenRequestItem] = []
    detail_open_reason_by_key: dict[str, str] = {}
    for decision in plan.decisions:
        if decision.action != "open_detail":
            continue
        candidate = selected_by_id[decision.candidate_id]
        candidate_provider_id = candidate.stable_provider_id or candidate.candidate_id
        idempotency_key = f"open:{candidate_provider_id}"
        attempt = store.reserve_detail_attempt(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            provider_account_hash=provider_account_hash,
            candidate_provider_id=candidate_provider_id,
            budget_date=budget_date,
            provider_day_key=provider_day_key,
            timezone=timezone,
            idempotency_key=idempotency_key,
        )
        attempts.append(attempt)
        detail_open_reason_by_key[idempotency_key] = decision.reason
        request_items.append(
            LiepinDetailOpenRequestItem(
                request_id=f"detail:{candidate.candidate_id}",
                attempt_id=attempt.attempt_id,
                idempotency_key=idempotency_key,
                candidate_id=candidate_provider_id,
            )
        )

    worker_command_id = f"liepin-detail-{uuid.uuid4().hex[:16]}"
    worker_request = LiepinDetailOpenRequest(worker_command_id=worker_command_id, requests=request_items)
    for attempt in attempts:
        store.transition_detail_attempt(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            attempt_id=attempt.attempt_id,
            state="started",
            consumption_state="not_consumed",
            worker_command_id=worker_command_id,
        )

    try:
        response = await worker_client.open_details(worker_request)
    except Exception:
        for attempt in attempts:
            store.transition_detail_attempt(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_id=actor_id,
                attempt_id=attempt.attempt_id,
                state="unknown",
                consumption_state="possibly_consumed",
                worker_command_id=worker_command_id,
                raw_evidence_ref="worker:unknown-crash-after-dispatch",
            )
        raise

    detail_candidates: list[LiepinMappedCandidate] = []
    attempts_by_key = {attempt.idempotency_key: attempt for attempt in attempts}
    _validate_worker_response_keys(
        store=store,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_id=actor_id,
        attempts=attempts,
        worker_command_id=worker_command_id,
        response_keys=[result.idempotency_key for result in response.results],
    )
    for result in response.results:
        attempt = attempts_by_key[result.idempotency_key]
        mapped = _apply_worker_detail_result(
            store=store,
            result=result,
            attempt=attempt,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            run_id=run_id,
            query_instance_id=query_instance_id,
            query_fingerprint=query_fingerprint,
            record_provider_return=record_provider_return,
            detail_open_reason=detail_open_reason_by_key.get(result.idempotency_key),
            detail_open_policy_version=detail_open_policy_version,
            score_metadata=(score_metadata_by_candidate_id or {}).get(attempt.candidate_provider_id, {}),
        )
        if mapped is not None:
            detail_candidates.append(mapped)

    return LiepinDetailOpenLoopResult(plan=plan, attempts=attempts, detail_candidates=detail_candidates)


def build_detail_scorecard_metadata(
    *,
    card_scorecard: ScoredCandidate,
    detail_scorecard: ScoredCandidate,
    card_scorecard_ref: str,
    detail_scorecard_ref: str,
    detail_open_reason: str,
    detail_open_policy_version: str,
) -> ScoredCandidate:
    if card_scorecard.resume_id != detail_scorecard.resume_id:
        raise ValueError("card and detail scorecards must refer to the same resume_id")
    return detail_scorecard.model_copy(
        update={
            "score_evidence_source": "detail_enriched",
            "card_scorecard_ref": card_scorecard_ref,
            "detail_scorecard_ref": detail_scorecard_ref,
            "score_delta": detail_scorecard.overall_score - card_scorecard.overall_score,
            "detail_open_reason": detail_open_reason,
            "detail_open_policy_version": detail_open_policy_version,
        }
    )


def _validate_worker_response_keys(
    *,
    store: LiepinStore,
    tenant_id: str,
    workspace_id: str,
    actor_id: str,
    attempts: list[LiepinDetailAttemptRow],
    worker_command_id: str,
    response_keys: list[str],
) -> None:
    expected_keys = {attempt.idempotency_key for attempt in attempts}
    response_key_set = set(response_keys)
    has_duplicates = len(response_keys) != len(response_key_set)
    if response_key_set == expected_keys and not has_duplicates:
        return
    for attempt in attempts:
        store.transition_detail_attempt(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            attempt_id=attempt.attempt_id,
            state="unknown",
            consumption_state="possibly_consumed",
            worker_command_id=worker_command_id,
            raw_evidence_ref="worker:detail-response-mismatch-after-dispatch",
        )
    raise ValueError("Liepin detail worker response mismatch after dispatch.")


def _apply_worker_detail_result(
    *,
    store: LiepinStore,
    result: LiepinDetailOpenResult,
    attempt: LiepinDetailAttemptRow,
    tenant_id: str,
    workspace_id: str,
    actor_id: str,
    run_id: str,
    query_instance_id: str,
    query_fingerprint: str,
    record_provider_return: Callable[[ProviderReturnedCandidate], None] | None,
    detail_open_reason: str | None,
    detail_open_policy_version: str,
    score_metadata: dict[str, object],
) -> LiepinMappedCandidate | None:
    if result.diagnostics.page_loaded:
        store.transition_detail_attempt(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            attempt_id=attempt.attempt_id,
            state="provider_page_loaded",
            consumption_state="not_consumed",
            worker_command_id=result.worker_command_id,
            raw_evidence_ref=result.raw_evidence_ref,
        )
    if result.diagnostics.payload_seen:
        store.transition_detail_attempt(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            attempt_id=attempt.attempt_id,
            state="detail_payload_seen",
            consumption_state="not_consumed",
            worker_command_id=result.worker_command_id,
            raw_evidence_ref=result.raw_evidence_ref,
        )

    if result.status == "completed":
        if result.candidate is None:
            store.transition_detail_attempt(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_id=actor_id,
                attempt_id=attempt.attempt_id,
                state="unknown",
                consumption_state="possibly_consumed",
                worker_command_id=result.worker_command_id,
                raw_evidence_ref=result.raw_evidence_ref or "worker:invalid-completed-detail-result-after-dispatch",
            )
            raise ValueError("completed Liepin detail result requires a candidate payload")
        mapped = _with_detail_score_metadata(
            map_liepin_worker_detail(result.candidate, raw_payload_artifact_ref=result.raw_evidence_ref),
            detail_open_reason=detail_open_reason,
            detail_open_policy_version=detail_open_policy_version,
            score_metadata=score_metadata,
        )
        _record_provider_return(
            mapped=mapped,
            run_id=run_id,
            query_instance_id=query_instance_id,
            query_fingerprint=query_fingerprint,
            provider_request_id=result.worker_response_id,
            record_provider_return=record_provider_return,
        )
        store.apply_detail_worker_response(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            attempt_id=attempt.attempt_id,
            worker_response_id=result.worker_response_id,
            state="completed",
            consumption_state="consumed",
            worker_command_id=result.worker_command_id,
            raw_evidence_ref=result.raw_evidence_ref,
        )
        return mapped

    state, consumption_state = _terminal_state_for_worker_status(result.status)
    store.apply_detail_worker_response(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_id=actor_id,
        attempt_id=attempt.attempt_id,
        worker_response_id=result.worker_response_id,
        state=state,
        consumption_state=consumption_state,
        worker_command_id=result.worker_command_id,
        raw_evidence_ref=result.raw_evidence_ref,
    )
    return None


def _with_detail_score_metadata(
    mapped: LiepinMappedCandidate,
    *,
    detail_open_reason: str | None,
    detail_open_policy_version: str,
    score_metadata: dict[str, object],
) -> LiepinMappedCandidate:
    raw = dict(mapped.candidate.raw)
    if detail_open_reason:
        raw["detail_open_reason"] = detail_open_reason
    raw["detail_open_policy_version"] = detail_open_policy_version
    for key in ("card_scorecard_ref", "detail_scorecard_ref"):
        value = score_metadata.get(key)
        if isinstance(value, str) and value:
            raw[key] = value
    score_delta = score_metadata.get("score_delta")
    if isinstance(score_delta, int) and not isinstance(score_delta, bool):
        raw["score_delta"] = score_delta
    return replace(mapped, candidate=mapped.candidate.model_copy(update={"raw": raw}))


def _terminal_state_for_worker_status(status: str) -> tuple[str, str]:
    if status == "blocked_by_risk_control":
        return "blocked_by_risk_control", "not_consumed"
    if status == "failed_before_consumption":
        return "failed_before_consumption", "not_consumed"
    if status == "failed_after_possible_consumption":
        return "failed_after_possible_consumption", "possibly_consumed"
    if status == "unknown":
        return "unknown", "possibly_consumed"
    raise ValueError(f"unsupported Liepin detail worker status: {status}")


def _record_provider_return(
    *,
    mapped: LiepinMappedCandidate,
    run_id: str,
    query_instance_id: str,
    query_fingerprint: str,
    provider_request_id: str,
    record_provider_return: Callable[[ProviderReturnedCandidate], None] | None,
) -> None:
    if record_provider_return is None:
        return
    record_provider_return(
        ProviderReturnedCandidate(
            candidate=mapped.candidate,
            provider_snapshot=mapped.provider_snapshot,
            stage_id="detail_open",
            round_no=0,
            query_instance_id=query_instance_id,
            query_fingerprint=query_fingerprint,
            provider_name="liepin",
            provider_request_id=provider_request_id,
            provider_rank=1,
            provider_page_no=1,
            provider_fetch_no=1,
            attempt_no=1,
        )
    )
