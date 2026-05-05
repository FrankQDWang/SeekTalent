from __future__ import annotations

import json
import re
import httpx
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

from seektalent.artifacts import ArtifactResolver
from seektalent.config import TextLLMConfigMigrationError
from seektalent.evaluation import EvaluationResult
from seektalent.models import (
    ControllerContext,
    ControllerDecision,
    FinalResult,
    FinalizeContext,
    LocationExecutionPhase,
    QueryOutcomeClassification,
    QueryOutcomeThresholds,
    QueryRole,
    QueryResumeHit,
    QueryTermCandidate,
    ReplaySnapshot,
    ReflectionContext,
    RoundState,
    RunState,
    ScoredCandidate,
    SearchAttempt,
    SearchObservation,
    SearchControllerDecision,
    SecondLaneDecision,
    TerminalControllerRound,
    scored_candidate_sort_key,
    unique_strings,
)
from seektalent.retrieval.query_plan import normalize_term
from seektalent.requirements import build_requirement_digest
from seektalent.tracing import RunTracer, json_char_count, json_sha256


@dataclass(frozen=True)
class ProviderFailureInfo:
    failure_kind: str
    provider_failure_kind: str | None = None
    provider_status_code: int | None = None
    provider_error_type: str | None = None
    provider_error_code: str | None = None
    provider_request_id: str | None = None


def classify_text_llm_failure(exc: Exception) -> ProviderFailureInfo:
    if isinstance(exc, TextLLMConfigMigrationError):
        return ProviderFailureInfo(failure_kind="settings_migration_error")
    if isinstance(exc, TimeoutError | httpx.TimeoutException):
        return ProviderFailureInfo(
            failure_kind="timeout",
            provider_failure_kind="provider_timeout",
        )
    if isinstance(exc, httpx.ConnectError):
        return ProviderFailureInfo(failure_kind="transport_error")
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        status_code = response.status_code
        provider_failure_kind = "provider_unknown_error"
        if status_code == 400:
            provider_failure_kind = "provider_invalid_request"
        elif status_code == 401:
            provider_failure_kind = "provider_auth_error"
        elif status_code == 403:
            provider_failure_kind = "provider_access_denied"
        elif status_code == 404:
            provider_failure_kind = "provider_model_not_found"
        elif status_code == 429:
            provider_failure_kind = "provider_rate_limited"
        return ProviderFailureInfo(
            failure_kind="provider_error",
            provider_failure_kind=provider_failure_kind,
            provider_status_code=status_code,
            provider_error_type=(response.text or "")[:200] or None,
            provider_request_id=response.headers.get("x-request-id"),
        )
    return ProviderFailureInfo(failure_kind="response_validation_error")

_ACTIVE_SCHEMA_PRESSURE_LOGICAL_PATTERNS = [
    "round.*.controller.controller_call",
    "round.*.scoring.tui_summary_call",
    "round.*.controller.repair_controller_call",
    "round.*.reflection.repair_reflection_call",
    "round.*.reflection.reflection_call",
]

_LEGACY_COMPANY_DISCOVERY_SCHEMA_PRESSURE_PATTERNS = [
    "round.*.retrieval.company_discovery_plan_call",
    "round.*.retrieval.company_discovery_extract_call",
    "round.*.retrieval.company_discovery_reduce_call",
]

_LLM_PRF_SNAPSHOT_METADATA_FIELDS = frozenset(
    {
        "llm_prf_extractor_version",
        "llm_prf_grounding_validator_version",
        "llm_prf_familying_version",
        "llm_prf_model_id",
        "llm_prf_protocol_family",
        "llm_prf_endpoint_kind",
        "llm_prf_endpoint_region",
        "llm_prf_structured_output_mode",
        "llm_prf_prompt_hash",
        "llm_prf_output_retry_count",
    }
)


@dataclass
class _TermSurfaceStats:
    used_rounds: set[int] = field(default_factory=set)
    sent_query_count: int = 0
    raw_candidate_count: int = 0
    unique_new_count: int = 0
    duplicate_count: int = 0


def _preview_text(text: str, *, limit: int) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    if len(collapsed) <= limit:
        return collapsed
    return f"{collapsed[:limit].rstrip()}..."


def classify_query_outcome(
    *,
    provider_returned_count: int,
    new_unique_resume_count: int,
    new_fit_or_near_fit_count: int,
    fit_rate: float,
    must_have_match_avg: float,
    exploit_baseline_must_have_match_avg: float,
    off_intent_reason_count: int,
    thresholds: QueryOutcomeThresholds,
) -> QueryOutcomeClassification:
    labels: set[str] = set()
    reasons: list[str] = []

    if provider_returned_count == 0:
        labels.add("zero_recall")
        reasons.append("provider_returned_count == 0")
    if provider_returned_count > 0 and new_unique_resume_count == 0:
        labels.add("duplicate_only")
        reasons.append("new_unique_resume_count == 0")
    if new_fit_or_near_fit_count >= 1:
        labels.add("marginal_gain")
        reasons.append("new_fit_or_near_fit_count >= 1")
    if (
        new_unique_resume_count >= 1
        and fit_rate <= thresholds.noise_threshold
        and must_have_match_avg <= thresholds.must_have_noise_threshold
    ):
        labels.add("broad_noise")
        reasons.append("fit_rate and must_have_match_avg both indicate noise")
    if (
        new_unique_resume_count >= 1
        and must_have_match_avg < exploit_baseline_must_have_match_avg - thresholds.drift_must_have_drop
        and off_intent_reason_count >= thresholds.drift_off_intent_min_count
    ):
        labels.add("drift_suspected")
        reasons.append("must_have_match_avg dropped materially against exploit baseline")
    if (
        new_unique_resume_count <= thresholds.low_recall_threshold
        and fit_rate >= thresholds.high_precision_threshold
    ):
        labels.add("low_recall_high_precision")
        reasons.append("small sample but high precision")

    priority = [
        "zero_recall",
        "duplicate_only",
        "drift_suspected",
        "broad_noise",
        "marginal_gain",
        "low_recall_high_precision",
    ]
    primary_label = next((label for label in priority if label in labels), "low_recall_high_precision")
    return QueryOutcomeClassification(primary_label=primary_label, labels=sorted(labels), reasons=reasons)


def build_replay_snapshot(
    *,
    run_id: str,
    round_no: int,
    second_lane_decision: SecondLaneDecision,
    search_attempts: list[SearchAttempt],
    query_resume_hits: list[QueryResumeHit],
    search_observation: SearchObservation,
    scoring_model_version: str,
    query_plan_version: str,
    llm_prf_snapshot_metadata: Mapping[str, object] | None = None,
) -> ReplaySnapshot:
    llm_prf_snapshot_update = _validate_llm_prf_snapshot_metadata(llm_prf_snapshot_metadata)
    ordered_resume_ids = [hit.resume_id for hit in query_resume_hits]
    snapshot = ReplaySnapshot(
        run_id=run_id,
        round_no=round_no,
        retrieval_snapshot_id=f"{run_id}:round:{round_no}",
        second_lane_query_fingerprint=(
            second_lane_decision.selected_query_fingerprint or second_lane_decision.fallback_query_fingerprint
        ),
        provider_request={
            "search_attempts": [attempt.request_payload for attempt in search_attempts],
        },
        provider_response_resume_ids=unique_strings(ordered_resume_ids),
        provider_response_raw_rank=ordered_resume_ids,
        dedupe_version="v1",
        scoring_model_version=scoring_model_version,
        query_plan_version=query_plan_version,
        prf_gate_version=second_lane_decision.prf_policy_version,
        generic_explore_version=second_lane_decision.generic_explore_version,
        prf_probe_proposal_backend=second_lane_decision.prf_probe_proposal_backend,
        llm_prf_failure_kind=second_lane_decision.llm_prf_failure_kind,
        llm_prf_input_artifact_ref=second_lane_decision.llm_prf_input_artifact_ref,
        llm_prf_call_artifact_ref=second_lane_decision.llm_prf_call_artifact_ref,
        llm_prf_candidates_artifact_ref=second_lane_decision.llm_prf_candidates_artifact_ref,
        llm_prf_grounding_artifact_ref=second_lane_decision.llm_prf_grounding_artifact_ref,
        **llm_prf_snapshot_update,
    )
    return snapshot


def _validate_llm_prf_snapshot_metadata(metadata: Mapping[str, object] | None) -> dict[str, object]:
    if metadata is None:
        return {}
    unsupported_fields = set(metadata) - _LLM_PRF_SNAPSHOT_METADATA_FIELDS
    if unsupported_fields:
        field_list = ", ".join(sorted(unsupported_fields))
        raise ValueError(f"Unsupported LLM PRF replay snapshot metadata: {field_list}")
    return dict(metadata)


def slim_controller_context(
    *,
    context: ControllerContext,
    input_text_refs_builder: Callable[..., dict[str, object]],
) -> dict[str, object]:
    digest = context.requirement_digest or build_requirement_digest(context.requirement_sheet)
    return {
        "schema_version": "v0.2.3a",
        "context_type": "controller",
        "round_no": context.round_no,
        "input": input_text_refs_builder(
            role_title=context.requirement_sheet.role_title,
            jd=context.full_jd,
            notes=context.full_notes,
        ),
        "refs": {
            "requirement_sheet": "input.requirement_sheet",
            "sent_query_history": "runtime.sent_query_history",
        },
        "budget": {
            "min_rounds": context.min_rounds,
            "max_rounds": context.max_rounds,
            "retrieval_rounds_completed": context.retrieval_rounds_completed,
            "rounds_remaining_after_current": context.rounds_remaining_after_current,
            "budget_used_ratio": context.budget_used_ratio,
            "near_budget_limit": context.near_budget_limit,
            "is_final_allowed_round": context.is_final_allowed_round,
            "target_new": context.target_new,
            "budget_reminder": context.budget_reminder,
        },
        "stop_guidance": context.stop_guidance.model_dump(mode="json"),
        "requirement_digest": digest.model_dump(mode="json"),
        "query_term_pool": [item.model_dump(mode="json") for item in context.query_term_pool],
        "current_top_pool": [item.model_dump(mode="json") for item in context.current_top_pool],
        "latest_search_observation": (
            context.latest_search_observation.model_dump(mode="json")
            if context.latest_search_observation is not None
            else None
        ),
        "previous_reflection": (
            context.previous_reflection.model_dump(mode="json") if context.previous_reflection is not None else None
        ),
        "latest_reflection_keyword_advice": (
            context.latest_reflection_keyword_advice.model_dump(mode="json")
            if context.latest_reflection_keyword_advice is not None
            else None
        ),
        "latest_reflection_filter_advice": (
            context.latest_reflection_filter_advice.model_dump(mode="json")
            if context.latest_reflection_filter_advice is not None
            else None
        ),
        "shortage_history": context.shortage_history,
    }


def slim_reflection_context(
    *,
    context: ReflectionContext,
    input_text_refs_builder: Callable[..., dict[str, object]],
    slim_search_attempt: Callable[[SearchAttempt], dict[str, object]],
    slim_scored_candidate: Callable[..., dict[str, object]],
) -> dict[str, object]:
    return {
        "schema_version": "v0.2.3a",
        "context_type": "reflection",
        "round_no": context.round_no,
        "input": input_text_refs_builder(
            role_title=context.requirement_sheet.role_title,
            jd=context.full_jd,
            notes=context.full_notes,
        ),
        "refs": {
            "requirement_sheet": "input.requirement_sheet",
            "sent_query_history": "runtime.sent_query_history",
        },
        "requirement_digest": build_requirement_digest(context.requirement_sheet).model_dump(mode="json"),
        "query_term_pool": [item.model_dump(mode="json") for item in context.query_term_pool],
        "current_retrieval_plan": context.current_retrieval_plan.model_dump(mode="json"),
        "search_observation": context.search_observation.model_dump(mode="json"),
        "search_attempts": [slim_search_attempt(item) for item in context.search_attempts],
        "top_candidates": [
            slim_scored_candidate(candidate, rank=index)
            for index, candidate in enumerate(context.top_candidates[:8], start=1)
        ],
        "dropped_candidates": [
            slim_scored_candidate(candidate, rank=index)
            for index, candidate in enumerate(context.dropped_candidates[:5], start=1)
        ],
        "scoring_failures": [item.model_dump(mode="json") for item in context.scoring_failures],
        "sent_query_count": len(context.sent_query_history),
    }


def slim_finalize_context(
    *,
    context: FinalizeContext,
    slim_scored_candidate: Callable[..., dict[str, object]],
) -> dict[str, object]:
    return {
        "schema_version": "v0.2.3a",
        "context_type": "finalize",
        "run_id": context.run_id,
        "run_dir": context.run_dir,
        "rounds_executed": context.rounds_executed,
        "stop_reason": context.stop_reason,
        "refs": {
            "requirement_sheet": "input.requirement_sheet",
            "sent_query_history": "runtime.sent_query_history",
            "scorecards": "round.*.scoring.scorecards",
            "top_pool_snapshots": "round.*.scoring.top_pool_snapshot",
        },
        "requirement_digest": (
            context.requirement_digest.model_dump(mode="json")
            if context.requirement_digest is not None
            else None
        ),
        "top_candidates": [
            slim_scored_candidate(candidate, rank=index)
            for index, candidate in enumerate(context.top_candidates, start=1)
        ],
        "sent_query_count": len(context.sent_query_history),
    }


def slim_search_attempt(attempt: SearchAttempt) -> dict[str, object]:
    payload = attempt.model_dump(mode="json")
    request_payload = payload.pop("request_payload", {})
    payload["request_payload_sha256"] = json_sha256(request_payload)
    payload["request_payload_chars"] = json_char_count(request_payload)
    return payload


def slim_scored_candidate(candidate: ScoredCandidate, *, rank: int | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "resume_id": candidate.resume_id,
        "fit_bucket": candidate.fit_bucket,
        "overall_score": candidate.overall_score,
        "must_have_match_score": candidate.must_have_match_score,
        "preferred_match_score": candidate.preferred_match_score,
        "risk_score": candidate.risk_score,
        "source_round": candidate.source_round,
        "sort_key": list(scored_candidate_sort_key(candidate)),
        "matched_must_haves": candidate.matched_must_haves[:3],
        "missing_must_haves": candidate.missing_must_haves[:1],
        "matched_preferences": candidate.matched_preferences[:1],
        "negative_signals": candidate.negative_signals[:1],
        "risk_flags": candidate.risk_flags[:1],
        "reasoning_summary": _preview_text(candidate.reasoning_summary, limit=80),
    }
    if rank is not None:
        payload["rank"] = rank
    return payload


def slim_top_pool_snapshot(candidates: list[ScoredCandidate]) -> list[dict[str, object]]:
    return [
        {
            "resume_id": candidate.resume_id,
            "rank": index,
            "fit_bucket": candidate.fit_bucket,
            "overall_score": candidate.overall_score,
            "must_have_match_score": candidate.must_have_match_score,
            "risk_score": candidate.risk_score,
            "source_round": candidate.source_round,
            "sort_key": list(scored_candidate_sort_key(candidate)),
        }
        for index, candidate in enumerate(candidates, start=1)
    ]


def build_judge_packet(
    *,
    tracer: RunTracer,
    run_state: RunState,
    final_result: FinalResult,
    rounds_executed: int,
    stop_reason: str,
    terminal_controller_round: TerminalControllerRound | None,
    requirements_model: str,
    controller_model: str,
    scoring_model: str,
    reflection_model: str,
    finalize_model: str,
    prompt_hashes: dict[str, str],
) -> dict[str, object]:
    return {
        "schema_version": "v0.2",
        "run": {
            "run_id": tracer.run_id,
            "rounds_executed": rounds_executed,
            "stop_reason": stop_reason,
            "stop_decision_round": terminal_controller_round.round_no if terminal_controller_round else None,
            "models": {
                "requirements": requirements_model,
                "controller": controller_model,
                "scoring": scoring_model,
                "reflection": reflection_model,
                "finalize": finalize_model,
            },
            "prompt_hashes": prompt_hashes,
        },
        "requirements": {
            "input_truth": run_state.input_truth.model_dump(mode="json"),
            "requirement_sheet": run_state.requirement_sheet.model_dump(mode="json"),
            "scoring_policy": run_state.scoring_policy.model_dump(mode="json"),
        },
        "rounds": [
            {
                "round_no": round_state.round_no,
                "controller_decision": round_state.controller_decision.model_dump(mode="json"),
                "retrieval_plan": round_state.retrieval_plan.model_dump(mode="json"),
                "constraint_projection_result": (
                    round_state.constraint_projection_result.model_dump(mode="json")
                    if round_state.constraint_projection_result is not None
                    else None
                ),
                "sent_query_records": [
                    item.model_dump(mode="json")
                    for item in run_state.retrieval_state.sent_query_history
                    if item.round_no == round_state.round_no
                ],
                "search_observation": (
                    round_state.search_observation.model_dump(mode="json")
                    if round_state.search_observation is not None
                    else None
                ),
                "top_candidates": [item.model_dump(mode="json") for item in round_state.top_candidates],
                "dropped_candidates": [item.model_dump(mode="json") for item in round_state.dropped_candidates],
                "reflection_advice": (
                    round_state.reflection_advice.model_dump(mode="json")
                    if round_state.reflection_advice is not None
                    else None
                ),
            }
            for round_state in run_state.round_history
        ],
        "terminal_controller_round": (
            terminal_controller_round.model_dump(mode="json") if terminal_controller_round is not None else None
        ),
        "final": {"final_result": final_result.model_dump(mode="json")},
    }


def build_search_diagnostics(
    *,
    tracer: RunTracer,
    run_state: RunState,
    final_result: FinalResult,
    terminal_controller_round: TerminalControllerRound | None,
    collect_llm_schema_pressure: Callable[[Path], list[dict[str, object]]],
    build_round_search_diagnostics: Callable[[RunState, RoundState], dict[str, object]],
    reflection_advice_application_for_decision: Callable[[RunState, int, ControllerDecision], dict[str, object]],
) -> dict[str, object]:
    observations = [
        round_state.search_observation
        for round_state in run_state.round_history
        if round_state.search_observation is not None
    ]
    terminal_controller = None
    if terminal_controller_round is not None:
        terminal_controller = {
            "round_no": terminal_controller_round.round_no,
            "stop_reason": terminal_controller_round.controller_decision.stop_reason,
            "response_to_reflection": terminal_controller_round.controller_decision.response_to_reflection,
            "reflection_advice_application": reflection_advice_application_for_decision(
                run_state=run_state,
                round_no=terminal_controller_round.round_no,
                controller_decision=terminal_controller_round.controller_decision,
            ),
            "stop_guidance": terminal_controller_round.stop_guidance.model_dump(mode="json"),
        }
    return {
        "run_id": tracer.run_id,
        "input": {
            "job_title": run_state.input_truth.job_title,
            "jd_sha256": run_state.input_truth.jd_sha256,
            "notes_sha256": run_state.input_truth.notes_sha256,
        },
        "summary": {
            "rounds_executed": final_result.rounds_executed,
            "total_sent_queries": len(run_state.retrieval_state.sent_query_history),
            "total_raw_candidates": sum(item.raw_candidate_count for item in observations),
            "total_unique_new_candidates": sum(item.unique_new_count for item in observations),
            "final_candidate_count": len(final_result.candidates),
            "stop_reason": final_result.stop_reason,
            "terminal_controller": terminal_controller,
        },
        "llm_schema_pressure": collect_llm_schema_pressure(tracer.run_dir),
        "rounds": [
            build_round_search_diagnostics(run_state=run_state, round_state=round_state)
            for round_state in run_state.round_history
        ],
    }


def build_term_surface_audit(
    *,
    tracer: RunTracer,
    run_state: RunState,
    final_result: FinalResult,
    evaluation_result: EvaluationResult | None,
) -> dict[str, object]:
    stats_by_term = _query_containing_term_stats(run_state)
    positive_final_ids = _positive_final_candidate_ids(evaluation_result)
    terms = []
    used_term_count = 0
    for item in run_state.retrieval_state.query_term_pool:
        stats = stats_by_term.get(item.term.casefold(), _TermSurfaceStats())
        used_rounds = sorted(stats.used_rounds)
        if used_rounds:
            used_term_count += 1
        final_ids = {
            candidate.resume_id for candidate in final_result.candidates if candidate.source_round in used_rounds
        }
        terms.append(
            {
                "term": item.term,
                "source": item.source,
                "category": item.category,
                "retrieval_role": item.retrieval_role,
                "queryability": item.queryability,
                "family": item.family,
                "active": item.active,
                "used_rounds": used_rounds,
                "sent_query_count": stats.sent_query_count,
                "queries_containing_term_raw_candidate_count": stats.raw_candidate_count,
                "queries_containing_term_unique_new_count": stats.unique_new_count,
                "queries_containing_term_duplicate_count": stats.duplicate_count,
                "final_candidate_count_from_used_rounds": len(final_ids),
                "judge_positive_count_from_used_rounds": (
                    None if evaluation_result is None else len(final_ids & positive_final_ids)
                ),
                "human_label": None,
            }
        )
    surfaces, candidate_rules = _build_surface_audit_rows(
        query_term_pool=run_state.retrieval_state.query_term_pool,
        stats_by_term=stats_by_term,
        positive_final_ids=positive_final_ids,
        final_result=final_result,
        evaluation_result=evaluation_result,
    )
    return {
        "run_id": tracer.run_id,
        "input": {
            "job_title": run_state.input_truth.job_title,
            "jd_sha256": run_state.input_truth.jd_sha256,
            "notes_sha256": run_state.input_truth.notes_sha256,
        },
        "summary": {
            "term_count": len(run_state.retrieval_state.query_term_pool),
            "used_term_count": used_term_count,
            "candidate_surface_rule_count": len(candidate_rules),
            "eval_enabled": evaluation_result is not None,
        },
        "terms": terms,
        "surfaces": surfaces,
        "candidate_surface_rules": candidate_rules,
    }


def collect_llm_schema_pressure(run_dir: Path) -> list[dict[str, object]]:
    resolver = ArtifactResolver.for_root(run_dir)
    pressure: list[dict[str, object]] = []
    pressure.append(_llm_schema_pressure_item(json.loads(resolver.resolve("runtime.requirements_call").read_text(encoding="utf-8"))))
    repair_requirements_call = resolver.resolve_optional("runtime.repair_requirements_call")
    if repair_requirements_call is not None and repair_requirements_call.exists():
        pressure.append(_llm_schema_pressure_item(json.loads(repair_requirements_call.read_text(encoding="utf-8"))))

    for logical_name in _ACTIVE_SCHEMA_PRESSURE_LOGICAL_PATTERNS:
        for path in resolver.resolve_many(logical_name):
            if not path.exists():
                continue
            pressure.append(_llm_schema_pressure_item(json.loads(path.read_text(encoding="utf-8"))))
    for path in _historical_company_discovery_call_paths(resolver):
        if not path.exists():
            continue
        pressure.append(_llm_schema_pressure_item(json.loads(path.read_text(encoding="utf-8"))))
    for path in resolver.resolve_many("round.*.scoring.scoring_calls"):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                pressure.append(_llm_schema_pressure_item(json.loads(line)))

    pressure.append(_llm_schema_pressure_item(json.loads(resolver.resolve("runtime.finalizer_call").read_text(encoding="utf-8"))))
    return pressure


def _historical_company_discovery_call_paths(resolver: ArtifactResolver) -> list[Path]:
    legacy_names = [
        logical_name
        for logical_name in sorted(resolver.manifest.logical_artifacts)
        if any(fnmatch(logical_name, pattern) for pattern in _LEGACY_COMPANY_DISCOVERY_SCHEMA_PRESSURE_PATTERNS)
    ]
    return [resolver.resolve(logical_name) for logical_name in legacy_names]


def _query_containing_term_stats(run_state: RunState) -> dict[str, _TermSurfaceStats]:
    attempt_totals: dict[tuple[object, ...], Counter[str]] = {}
    for round_state in run_state.round_history:
        for attempt in round_state.search_attempts:
            key = _sent_query_key(
                round_no=round_state.round_no,
                query_role=attempt.query_role,
                city=attempt.city,
                phase=attempt.phase,
                batch_no=attempt.batch_no,
            )
            totals = attempt_totals.setdefault(key, Counter())
            totals["raw_candidate_count"] += attempt.raw_candidate_count
            totals["unique_new_count"] += attempt.batch_unique_new_count
            totals["duplicate_count"] += attempt.batch_duplicate_count

    stats_by_term: dict[str, _TermSurfaceStats] = {}
    for record in run_state.retrieval_state.sent_query_history:
        totals = attempt_totals.get(
            _sent_query_key(
                round_no=record.round_no,
                query_role=record.query_role,
                city=record.city,
                phase=record.phase,
                batch_no=record.batch_no,
            ),
            Counter(),
        )
        for term in record.query_terms:
            stats = stats_by_term.setdefault(term.casefold(), _TermSurfaceStats())
            stats.used_rounds.add(record.round_no)
            stats.sent_query_count += 1
            stats.raw_candidate_count += totals["raw_candidate_count"]
            stats.unique_new_count += totals["unique_new_count"]
            stats.duplicate_count += totals["duplicate_count"]
    return stats_by_term


def _sent_query_key(
    *,
    round_no: int,
    query_role: QueryRole,
    city: str | None,
    phase: LocationExecutionPhase | None,
    batch_no: int | None,
) -> tuple[object, ...]:
    return (round_no, query_role, city, phase, batch_no)


def _positive_final_candidate_ids(evaluation_result: EvaluationResult | None) -> set[str]:
    if evaluation_result is None:
        return set()
    return {candidate.resume_id for candidate in evaluation_result.final.candidates if candidate.judge_score >= 2}


def _build_surface_audit_rows(
    *,
    query_term_pool: list[QueryTermCandidate],
    stats_by_term: dict[str, _TermSurfaceStats],
    positive_final_ids: set[str],
    final_result: FinalResult,
    evaluation_result: EvaluationResult | None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    surfaces: list[dict[str, object]] = []
    candidate_rules: list[dict[str, object]] = []
    for item in query_term_pool:
        rule = _candidate_surface_rule(item.term)
        if rule is None:
            continue
        stats = stats_by_term.get(item.term.casefold(), _TermSurfaceStats())
        used_rounds = set(stats.used_rounds)
        final_ids = {candidate.resume_id for candidate in final_result.candidates if candidate.source_round in used_rounds}
        surfaces.append(
            {
                "original_term": item.term,
                "retrieval_term": item.term,
                "canonical_surface": rule["to_retrieval_term"],
                "surface_family": rule["surface_family"],
                "surface_transform": "candidate_alias_not_applied",
                "surface_transform_reason": rule["reason"],
                "used_in_query": bool(used_rounds),
                "cts_raw_hits": stats.raw_candidate_count,
                "unique_new_count": stats.unique_new_count,
                "judge_positive_count": None if evaluation_result is None else len(final_ids & positive_final_ids),
            }
        )
        candidate_rules.append(
            {
                "from_original_term": item.term,
                "to_retrieval_term": rule["to_retrieval_term"],
                "domain": "agent_llm",
                "applies_to": "retrieval_only",
                "status": "candidate",
                "evidence_status": "needs_surface_probe",
            }
        )
    return surfaces, candidate_rules


def _candidate_surface_rule(term: str) -> dict[str, str] | None:
    clean = " ".join(term.strip().split())
    if clean.casefold() == "ai agent":
        return {
            "to_retrieval_term": "Agent",
            "surface_family": "role.agent",
            "reason": "Candidate resume surface may use broader Agent more often than AI Agent.",
        }
    compact = clean.replace(" ", "")
    suffixes = ("架构", "系统", "应用", "工程")
    if compact.casefold().startswith("multiagent") and compact.casefold() != "multiagent":
        if any(compact.endswith(suffix) for suffix in suffixes):
            return {
                "to_retrieval_term": "MultiAgent",
                "surface_family": "domain.multi_agent",
                "reason": "Candidate resume surface may omit suffix context around MultiAgent.",
            }
    return None


def _reflection_advice_application_for_decision(
    *,
    run_state: RunState,
    round_no: int,
    controller_decision: ControllerDecision,
) -> dict[str, object]:
    previous_reflection = None
    if round_no > 1:
        previous_index = round_no - 2
        if previous_index >= 0:
            previous_reflection = run_state.round_history[previous_index].reflection_advice
    if previous_reflection is None:
        return {
            "suggested_activate_terms": [],
            "suggested_keep_terms": [],
            "suggested_deprioritize_terms": [],
            "suggested_drop_terms": [],
            "suggested_filter_fields": [],
            "accepted_activate_terms": [],
            "ignored_activate_terms": [],
            "accepted_keep_terms": [],
            "ignored_keep_terms": [],
            "accepted_deprioritize_terms": [],
            "ignored_deprioritize_terms": [],
            "accepted_drop_terms": [],
            "ignored_drop_terms": [],
            "accepted_terms": [],
            "ignored_terms": [],
            "accepted_keep_filter_fields": [],
            "ignored_keep_filter_fields": [],
            "accepted_add_filter_fields": [],
            "ignored_add_filter_fields": [],
            "accepted_drop_filter_fields": [],
            "ignored_drop_filter_fields": [],
            "accepted_filter_fields": [],
            "ignored_filter_fields": [],
            "controller_response": controller_decision.response_to_reflection,
        }
    selected_terms = (
        set(term.casefold() for term in controller_decision.proposed_query_terms)
        if isinstance(controller_decision, SearchControllerDecision)
        else set()
    )
    keyword_advice = previous_reflection.keyword_advice
    accepted_activate_terms = [term for term in keyword_advice.suggested_activate_terms if term.casefold() in selected_terms]
    ignored_activate_terms = [term for term in keyword_advice.suggested_activate_terms if term.casefold() not in selected_terms]
    accepted_keep_terms = [term for term in keyword_advice.suggested_keep_terms if term.casefold() in selected_terms]
    ignored_keep_terms = [term for term in keyword_advice.suggested_keep_terms if term.casefold() not in selected_terms]
    accepted_deprioritize_terms = [
        term for term in keyword_advice.suggested_deprioritize_terms if term.casefold() not in selected_terms
    ]
    ignored_deprioritize_terms = [
        term for term in keyword_advice.suggested_deprioritize_terms if term.casefold() in selected_terms
    ]
    accepted_drop_terms = [term for term in keyword_advice.suggested_drop_terms if term.casefold() not in selected_terms]
    ignored_drop_terms = [term for term in keyword_advice.suggested_drop_terms if term.casefold() in selected_terms]

    active_filter_fields = (
        set(controller_decision.proposed_filter_plan.pinned_filters)
        | set(controller_decision.proposed_filter_plan.optional_filters)
        | set(controller_decision.proposed_filter_plan.added_filter_fields)
        if isinstance(controller_decision, SearchControllerDecision)
        else set()
    )
    dropped_filter_fields = (
        set(controller_decision.proposed_filter_plan.dropped_filter_fields)
        if isinstance(controller_decision, SearchControllerDecision)
        else set()
    )
    filter_advice = previous_reflection.filter_advice
    suggested_filter_fields = unique_strings(
        [
            *filter_advice.suggested_keep_filter_fields,
            *filter_advice.suggested_drop_filter_fields,
            *filter_advice.suggested_add_filter_fields,
        ]
    )
    accepted_keep_filter_fields = [
        field
        for field in filter_advice.suggested_keep_filter_fields
        if field in active_filter_fields and field not in dropped_filter_fields
    ]
    ignored_keep_filter_fields = [
        field
        for field in filter_advice.suggested_keep_filter_fields
        if field not in active_filter_fields or field in dropped_filter_fields
    ]
    accepted_add_filter_fields = [field for field in filter_advice.suggested_add_filter_fields if field in active_filter_fields]
    ignored_add_filter_fields = [field for field in filter_advice.suggested_add_filter_fields if field not in active_filter_fields]
    accepted_drop_filter_fields = [
        field for field in filter_advice.suggested_drop_filter_fields if field in dropped_filter_fields
    ]
    ignored_drop_filter_fields = [
        field for field in filter_advice.suggested_drop_filter_fields if field not in dropped_filter_fields
    ]
    return {
        "suggested_activate_terms": keyword_advice.suggested_activate_terms,
        "suggested_keep_terms": keyword_advice.suggested_keep_terms,
        "suggested_deprioritize_terms": keyword_advice.suggested_deprioritize_terms,
        "suggested_drop_terms": keyword_advice.suggested_drop_terms,
        "suggested_filter_fields": suggested_filter_fields,
        "accepted_activate_terms": accepted_activate_terms,
        "ignored_activate_terms": ignored_activate_terms,
        "accepted_keep_terms": accepted_keep_terms,
        "ignored_keep_terms": ignored_keep_terms,
        "accepted_deprioritize_terms": accepted_deprioritize_terms,
        "ignored_deprioritize_terms": ignored_deprioritize_terms,
        "accepted_drop_terms": accepted_drop_terms,
        "ignored_drop_terms": ignored_drop_terms,
        "accepted_terms": unique_strings([*accepted_activate_terms, *accepted_keep_terms]),
        "ignored_terms": unique_strings([*ignored_activate_terms, *ignored_keep_terms]),
        "accepted_keep_filter_fields": accepted_keep_filter_fields,
        "ignored_keep_filter_fields": ignored_keep_filter_fields,
        "accepted_add_filter_fields": accepted_add_filter_fields,
        "ignored_add_filter_fields": ignored_add_filter_fields,
        "accepted_drop_filter_fields": accepted_drop_filter_fields,
        "ignored_drop_filter_fields": ignored_drop_filter_fields,
        "accepted_filter_fields": unique_strings(
            [*accepted_keep_filter_fields, *accepted_add_filter_fields, *accepted_drop_filter_fields]
        ),
        "ignored_filter_fields": unique_strings(
            [*ignored_keep_filter_fields, *ignored_add_filter_fields, *ignored_drop_filter_fields]
        ),
        "controller_response": controller_decision.response_to_reflection,
    }


def _reflection_advice_application(*, run_state: RunState, round_state: RoundState) -> dict[str, object]:
    return _reflection_advice_application_for_decision(
        run_state=run_state,
        round_no=round_state.round_no,
        controller_decision=round_state.controller_decision,
    )


def _build_round_search_diagnostics(*, run_state: RunState, round_state: RoundState) -> dict[str, object]:
    if round_state.search_observation is None:
        raise ValueError("round_state.search_observation is required for search diagnostics")
    reflection = round_state.reflection_advice
    scored_this_round = [
        candidate for candidate in run_state.scorecards_by_resume_id.values() if candidate.source_round == round_state.round_no
    ]
    sent_queries = [item for item in run_state.retrieval_state.sent_query_history if item.round_no == round_state.round_no]
    audit_labels = _round_audit_labels(run_state=run_state, round_state=round_state)
    return {
        "round_no": round_state.round_no,
        "query_terms": round_state.retrieval_plan.query_terms,
        "keyword_query": round_state.retrieval_plan.keyword_query,
        "failure_labels": audit_labels,
        "audit_labels": audit_labels,
        "query_term_details": _query_term_details(
            terms=round_state.retrieval_plan.query_terms,
            query_term_pool=run_state.retrieval_state.query_term_pool,
        ),
        "sent_queries": [
            {
                "query_role": item.query_role,
                "city": item.city,
                "phase": item.phase,
                "batch_no": item.batch_no,
                "requested_count": item.requested_count,
                "query_terms": item.query_terms,
                "keyword_query": item.keyword_query,
            }
            for item in sent_queries
        ],
        "filters": {
            "projected_provider_filters": round_state.retrieval_plan.projected_provider_filters,
            "runtime_only_constraints": [
                item.model_dump(mode="json") for item in round_state.retrieval_plan.runtime_only_constraints
            ],
            "adapter_notes": (
                round_state.constraint_projection_result.adapter_notes
                if round_state.constraint_projection_result is not None
                else []
            ),
        },
        "search": {
            "raw_candidate_count": round_state.search_observation.raw_candidate_count,
            "unique_new_count": round_state.search_observation.unique_new_count,
            "shortage_count": round_state.search_observation.shortage_count,
            "duplicate_count": sum(item.batch_duplicate_count for item in round_state.search_attempts),
            "fetch_attempt_count": round_state.search_observation.fetch_attempt_count,
            "exhausted_reason": round_state.search_observation.exhausted_reason,
        },
        "scoring": {
            "newly_scored_count": len(scored_this_round),
            "top_pool_count": len(round_state.top_candidates),
            "fit_count": sum(1 for item in scored_this_round if item.fit_bucket == "fit"),
            "not_fit_count": sum(1 for item in scored_this_round if item.fit_bucket == "not_fit"),
            "top_pool_snapshot": [
                {
                    "resume_id": item.resume_id,
                    "fit_bucket": item.fit_bucket,
                    "overall_score": item.overall_score,
                    "must_have_match_score": item.must_have_match_score,
                    "risk_score": item.risk_score,
                    "source_round": item.source_round,
                }
                for item in round_state.top_candidates
            ],
        },
        "reflection": {
            "suggest_stop": reflection.suggest_stop if reflection is not None else False,
            "suggested_activate_terms": (
                reflection.keyword_advice.suggested_activate_terms if reflection is not None else []
            ),
            "suggested_drop_terms": reflection.keyword_advice.suggested_drop_terms if reflection is not None else [],
            "suggested_drop_filter_fields": (
                reflection.filter_advice.suggested_drop_filter_fields if reflection is not None else []
            ),
            "reflection_summary": reflection.reflection_summary if reflection is not None else None,
        },
        "controller_response_to_previous_reflection": round_state.controller_decision.response_to_reflection,
        "reflection_advice_application": _reflection_advice_application(
            run_state=run_state,
            round_state=round_state,
        ),
    }


def _round_audit_labels(*, run_state: RunState, round_state: RoundState) -> list[str]:
    if round_state.round_no != 1:
        return []
    title_anchor_candidates = [
        item
        for item in run_state.retrieval_state.query_term_pool
        if item.queryability == "admitted"
        and item.retrieval_role in {"role_anchor", "primary_role_anchor", "secondary_title_anchor"}
    ]
    if len(title_anchor_candidates) != 2:
        return []
    title_anchor_keys = {normalize_term(item.term).casefold() for item in title_anchor_candidates}
    used_title_anchor_count = sum(
        1 for term in round_state.retrieval_plan.query_terms if normalize_term(term).casefold() in title_anchor_keys
    )
    if used_title_anchor_count >= 2:
        return []
    return ["title_multi_anchor_collapsed"]


def _query_term_details(
    *,
    terms: list[str],
    query_term_pool: list[QueryTermCandidate],
) -> list[dict[str, object]]:
    term_index = {item.term.casefold(): item for item in query_term_pool}
    details: list[dict[str, object]] = []
    for term in terms:
        candidate = term_index.get(term.casefold())
        details.append(
            {
                "term": term,
                "source": candidate.source if candidate is not None else None,
                "category": candidate.category if candidate is not None else None,
                "retrieval_role": candidate.retrieval_role if candidate is not None else None,
                "queryability": candidate.queryability if candidate is not None else None,
                "family": candidate.family if candidate is not None else None,
            }
        )
    return details


def _llm_schema_pressure_item(snapshot: dict[str, object]) -> dict[str, object]:
    return {
        "stage": snapshot["stage"],
        "call_id": snapshot["call_id"],
        "output_retries": snapshot["output_retries"],
        "validator_retry_count": snapshot.get("validator_retry_count", 0),
        "validator_retry_reasons": snapshot.get("validator_retry_reasons", []),
        "repair_attempt_count": snapshot.get("repair_attempt_count", 0),
        "repair_succeeded": snapshot.get("repair_succeeded", False),
        "repair_reason": snapshot.get("repair_reason"),
        "full_retry_count": snapshot.get("full_retry_count", 0),
        "cache_hit": snapshot.get("cache_hit", False),
        "cache_lookup_latency_ms": snapshot.get("cache_lookup_latency_ms", 0),
        "prompt_cache_key": snapshot.get("prompt_cache_key"),
        "prompt_cache_retention": snapshot.get("prompt_cache_retention"),
        "provider_usage": snapshot.get("provider_usage"),
        "cached_input_tokens": snapshot.get("cached_input_tokens", 0),
        "prompt_chars": snapshot.get("prompt_chars", 0),
        "input_payload_chars": snapshot.get("input_payload_chars", 0),
        "output_chars": snapshot.get("output_chars", 0),
        "input_payload_sha256": snapshot.get("input_payload_sha256"),
        "structured_output_sha256": snapshot.get("structured_output_sha256"),
    }
