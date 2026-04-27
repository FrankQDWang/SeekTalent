from __future__ import annotations

import json
import math
from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, TypedDict

from seektalent.config import AppSettings
from seektalent.core.retrieval.provider_contract import SearchResult
from seektalent.core.retrieval.service import RetrievalService
from seektalent.models import (
    CanonicalQuerySpec,
    ConstraintValue,
    CTSQuery,
    CitySearchSummary,
    LaneType,
    LocationExecutionPlan,
    LocationExecutionPhase,
    QueryOutcomeClassification,
    QueryOutcomeThresholds,
    QueryResumeHit,
    QueryRole,
    RoundRetrievalPlan,
    ResumeCandidate,
    RuntimeConstraint,
    SearchAttempt,
    SearchObservation,
    ScoredCandidate,
    SentQueryRecord,
    unique_strings,
)
from seektalent.providers.cts.query_builder import CTSQueryBuildInput, build_cts_query
from seektalent.retrieval import allocate_balanced_city_targets, serialize_keyword_query
from seektalent.retrieval.query_identity import build_query_fingerprint, build_query_instance_id
from seektalent.runtime.runtime_diagnostics import classify_query_outcome
from seektalent.tracing import RunTracer


def _provider_query_role(query_role: QueryRole) -> Literal["primary", "expansion"]:
    if query_role == "exploit":
        return "primary"
    return "expansion"


def _location_hit_fields(*, city: str | None) -> tuple[str | None, str | None]:
    if city:
        return city, "city"
    return None, None


def _provider_score_if_any(candidate: ResumeCandidate) -> float | None:
    for key in ("provider_score", "score"):
        value = candidate.raw.get(key)
        if isinstance(value, int | float):
            return float(value)
    return None


def _apply_first_hit_attribution(
    *,
    candidate: ResumeCandidate,
    query: CTSQuery,
    round_no: int,
    location_key: str | None,
    location_type: str | None,
    batch_no: int,
) -> ResumeCandidate:
    if candidate.first_query_instance_id is not None:
        return candidate
    return candidate.model_copy(
        update={
            "first_query_instance_id": query.query_instance_id,
            "first_query_fingerprint": query.query_fingerprint,
            "first_round_no": round_no,
            "first_lane_type": query.lane_type,
            "first_location_key": location_key,
            "first_location_type": location_type,
            "first_batch_no": batch_no,
        }
    )


@dataclass
class CityExecutionState:
    next_page: int = 1
    exhausted: bool = False


@dataclass
class LogicalQueryState:
    query_role: QueryRole
    lane_type: LaneType
    query_terms: list[str]
    keyword_query: str
    query_instance_id: str
    query_fingerprint: str
    next_page: int = 1
    exhausted: bool = False
    adapter_notes: list[str] = field(default_factory=list)
    city_states: dict[str, CityExecutionState] = field(default_factory=dict)


@dataclass
class LaneOutcomeState:
    provider_returned_count: int = 0
    new_candidates: list[ResumeCandidate] = field(default_factory=list)
    scored_candidates: list[ScoredCandidate] = field(default_factory=list)
    latest: QueryOutcomeClassification | None = None


def _location_execution_key(location_execution_plan: LocationExecutionPlan) -> str:
    return json.dumps(
        {
            "mode": location_execution_plan.mode,
            "allowed_locations": location_execution_plan.allowed_locations,
            "preferred_locations": location_execution_plan.preferred_locations,
            "priority_order": location_execution_plan.priority_order,
            "balanced_order": location_execution_plan.balanced_order,
            "rotation_offset": location_execution_plan.rotation_offset,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )


def build_logical_query_state(
    *,
    run_id: str,
    round_no: int,
    lane_type: LaneType,
    query_terms: list[str],
    job_intent_fingerprint: str,
    source_plan_version: str,
    provider_filters: dict[str, ConstraintValue],
    location_execution_plan: LocationExecutionPlan,
) -> LogicalQueryState:
    keyword_query = serialize_keyword_query(query_terms)
    spec = CanonicalQuerySpec(
        lane_type=lane_type,
        anchors=query_terms[:1],
        expansion_terms=query_terms[1:],
        promoted_prf_expression=query_terms[1] if lane_type == "prf_probe" and len(query_terms) > 1 else None,
        generic_explore_terms=query_terms[1:] if lane_type == "generic_explore" else [],
        required_terms=query_terms[:1],
        optional_terms=query_terms[1:],
        excluded_terms=[],
        location_key=_location_execution_key(location_execution_plan),
        provider_filters=provider_filters,
        boolean_template="required_plus_optional",
        rendered_provider_query=keyword_query,
        provider_name="cts",
        source_plan_version=source_plan_version,
    )
    query_fingerprint = build_query_fingerprint(
        job_intent_fingerprint=job_intent_fingerprint,
        lane_type=lane_type,
        canonical_query_spec=spec,
        policy_version="typed-second-lane-v1",
    )
    return LogicalQueryState(
        query_role="explore" if lane_type != "exploit" else "exploit",
        lane_type=lane_type,
        query_terms=query_terms,
        keyword_query=keyword_query,
        query_instance_id=build_query_instance_id(
            run_id=run_id,
            round_no=round_no,
            lane_type=lane_type,
            query_fingerprint=query_fingerprint,
            source_plan_version=source_plan_version,
        ),
        query_fingerprint=query_fingerprint,
    )


def allocate_initial_lane_targets(*, query_states: list[LogicalQueryState], target_new: int) -> dict[LaneType, int]:
    if not query_states:
        return {}
    if len(query_states) == 1:
        return {query_states[0].lane_type: max(target_new, 0)}

    lane_types = {query_state.lane_type for query_state in query_states}
    if "exploit" not in lane_types:
        first_lane = query_states[0].lane_type
        second_lane = query_states[1].lane_type
        first_target = max(0, math.ceil(target_new * 0.7))
        second_target = max(0, target_new - first_target)
        return {first_lane: first_target, second_lane: second_target}

    second_lane_type = next((lane_type for lane_type in lane_types if lane_type != "exploit"), "exploit")
    exploit_target = min(target_new, max(1, math.ceil(target_new * 0.7))) if target_new > 0 else 0
    second_target = max(0, target_new - exploit_target)
    return {
        "exploit": exploit_target,
        second_lane_type: second_target,
    }


def allow_lane_refill(*, lane_type: LaneType, outcome: QueryOutcomeClassification | None) -> bool:
    if lane_type == "exploit" or outcome is None:
        return True
    blocked_labels = {"zero_recall", "duplicate_only", "broad_noise", "drift_suspected"}
    return not blocked_labels.intersection(outcome.labels)


@dataclass(frozen=True)
class RetrievalExecutionResult:
    cts_queries: list[CTSQuery]
    sent_query_records: list[SentQueryRecord]
    new_candidates: list[ResumeCandidate]
    search_observation: SearchObservation
    search_attempts: list[SearchAttempt]
    query_resume_hits: list[QueryResumeHit] = field(default_factory=list)


class _CityDispatchResult(TypedDict):
    cts_query: CTSQuery
    sent_query_record: SentQueryRecord
    new_candidates: list[ResumeCandidate]
    search_observation: SearchObservation
    search_attempts: list[SearchAttempt]
    city_summary: CitySearchSummary


def _final_exhausted_reason(
    *,
    target_new: int,
    new_candidate_count: int,
    city_search_summaries: list[CitySearchSummary],
) -> str | None:
    if new_candidate_count >= target_new:
        return "target_satisfied"
    if not city_search_summaries:
        return "cts_exhausted"
    for city_summary in reversed(city_search_summaries):
        if city_summary.exhausted_reason:
            return city_summary.exhausted_reason
    return "cts_exhausted"


@dataclass(frozen=True)
class RetrievalRuntime:
    settings: AppSettings
    retrieval_service: RetrievalService

    async def execute_round_search(
        self,
        *,
        round_no: int,
        retrieval_plan: RoundRetrievalPlan,
        query_states: list[LogicalQueryState],
        base_adapter_notes: list[str] | None,
        target_new: int,
        seen_resume_ids: set[str],
        seen_dedup_keys: set[str],
        tracer: RunTracer,
        score_for_query_outcome: Callable[[list[ResumeCandidate]], Awaitable[list[ScoredCandidate]]] | None = None,
        query_outcome_thresholds: QueryOutcomeThresholds | None = None,
    ) -> RetrievalExecutionResult:
        location_plan = retrieval_plan.location_execution_plan
        adapter_notes = list(base_adapter_notes or [])
        for query_state in query_states:
            query_state.adapter_notes = list(adapter_notes)
            query_state.next_page = 1
            query_state.exhausted = False
            query_state.city_states = (
                {city: CityExecutionState() for city in location_plan.allowed_locations}
                if location_plan.mode != "none"
                else {}
            )
        global_seen_resume_ids = set(seen_resume_ids)
        global_seen_dedup_keys = set(seen_dedup_keys)
        cts_queries: list[CTSQuery] = []
        sent_query_records: list[SentQueryRecord] = []
        all_new_candidates: list[ResumeCandidate] = []
        all_search_attempts: list[SearchAttempt] = []
        city_search_summaries: list[CitySearchSummary] = []
        query_resume_hits: list[QueryResumeHit] = []
        raw_candidate_count = 0
        batch_no = 0
        last_exhausted_reason: str | None = None
        lane_outcomes: dict[LaneType, LaneOutcomeState] = {
            query_state.lane_type: LaneOutcomeState() for query_state in query_states
        }
        outcome_thresholds = query_outcome_thresholds or QueryOutcomeThresholds()

        async def collect_candidates_for_query(
            *,
            query_state: LogicalQueryState,
            requested_count: int,
        ) -> tuple[list[ResumeCandidate], int]:
            nonlocal batch_no, raw_candidate_count, last_exhausted_reason
            if requested_count <= 0 or query_state.exhausted:
                return [], 0
            local_new_candidates: list[ResumeCandidate] = []
            local_search_attempts: list[SearchAttempt] = []
            local_city_summaries: list[CitySearchSummary] = []
            local_raw_candidate_count = 0

            async def run_dispatches(
                *,
                phase: LocationExecutionPhase,
                city_targets: list[tuple[str, int]],
            ) -> None:
                nonlocal batch_no, local_raw_candidate_count
                if not city_targets:
                    return
                batch_no += 1
                for city, city_requested_count in city_targets:
                    dispatch = await self._run_city_dispatch(
                        round_no=round_no,
                        retrieval_plan=retrieval_plan,
                        query_state=query_state,
                        city=city,
                        phase=phase,
                        batch_no=batch_no,
                        requested_count=city_requested_count,
                        city_state=query_state.city_states[city],
                        seen_resume_ids=global_seen_resume_ids,
                        seen_dedup_keys=global_seen_dedup_keys,
                        tracer=tracer,
                        record_resume_hit=query_resume_hits.append,
                    )
                    cts_queries.append(dispatch["cts_query"])
                    sent_query_records.append(dispatch["sent_query_record"])
                    local_new_candidates.extend(dispatch["new_candidates"])
                    local_search_attempts.extend(dispatch["search_attempts"])
                    local_city_summaries.append(dispatch["city_summary"])
                    local_raw_candidate_count += dispatch["search_observation"].raw_candidate_count
                    query_state.adapter_notes = unique_strings(
                        query_state.adapter_notes + dispatch["search_observation"].adapter_notes
                    )

            if location_plan.mode == "none":
                batch_no += 1
                query = build_cts_query(
                    CTSQueryBuildInput(
                        query_role=query_state.query_role,
                        query_terms=query_state.query_terms,
                        keyword_query=query_state.keyword_query,
                        base_filters=retrieval_plan.projected_provider_filters,
                        adapter_notes=query_state.adapter_notes,
                        page=query_state.next_page,
                        page_size=requested_count,
                        rationale=retrieval_plan.rationale,
                    )
                )
                query = query.model_copy(
                    update={
                        "lane_type": query_state.lane_type,
                        "query_instance_id": query_state.query_instance_id,
                        "query_fingerprint": query_state.query_fingerprint,
                    }
                )
                sent_query_record = SentQueryRecord(
                    round_no=round_no,
                    query_role=query_state.query_role,
                    lane_type=query_state.lane_type,
                    query_instance_id=query_state.query_instance_id,
                    query_fingerprint=query_state.query_fingerprint,
                    batch_no=batch_no,
                    requested_count=requested_count,
                    query_terms=query_state.query_terms,
                    keyword_query=query_state.keyword_query,
                    source_plan_version=retrieval_plan.plan_version,
                    rationale=retrieval_plan.rationale,
                )
                new_candidates, search_observation, search_attempts, _ = await self.execute_search_tool(
                    round_no=round_no,
                    query=query,
                    runtime_constraints=retrieval_plan.runtime_only_constraints,
                    target_new=requested_count,
                    seen_resume_ids=global_seen_resume_ids,
                    seen_dedup_keys=global_seen_dedup_keys,
                    tracer=tracer,
                    batch_no=batch_no,
                    write_round_artifacts=False,
                    record_resume_hit=query_resume_hits.append,
                )
                cts_queries.append(query)
                sent_query_records.append(sent_query_record)
                local_new_candidates.extend(new_candidates)
                local_search_attempts.extend(search_attempts)
                local_raw_candidate_count += search_observation.raw_candidate_count
                query_state.adapter_notes = unique_strings(query_state.adapter_notes + search_observation.adapter_notes)
                if search_attempts:
                    query_state.next_page = search_attempts[-1].requested_page + 1
                if search_observation.exhausted_reason != "target_satisfied":
                    query_state.exhausted = True
                last_exhausted_reason = search_observation.exhausted_reason or last_exhausted_reason
            else:
                if location_plan.mode == "single":
                    await run_dispatches(
                        phase="balanced",
                        city_targets=[(location_plan.allowed_locations[0], requested_count)],
                    )
                else:
                    if location_plan.mode == "priority_then_fallback":
                        for city in location_plan.priority_order:
                            remaining_gap = requested_count - len(local_new_candidates)
                            if remaining_gap <= 0:
                                break
                            await run_dispatches(
                                phase="priority",
                                city_targets=[(city, remaining_gap)],
                            )
                    while True:
                        remaining_gap = requested_count - len(local_new_candidates)
                        if remaining_gap <= 0:
                            break
                        active_cities = [
                            city
                            for city in location_plan.balanced_order
                            if city in query_state.city_states and not query_state.city_states[city].exhausted
                        ]
                        if not active_cities:
                            break
                        city_targets = allocate_balanced_city_targets(
                            ordered_cities=active_cities,
                            target_new=remaining_gap,
                        )
                        if not city_targets:
                            break
                        await run_dispatches(
                            phase="balanced",
                            city_targets=city_targets,
                        )
                local_exhausted_reason = _final_exhausted_reason(
                    target_new=requested_count,
                    new_candidate_count=len(local_new_candidates),
                    city_search_summaries=local_city_summaries,
                )
                if local_exhausted_reason != "target_satisfied":
                    query_state.exhausted = True
                last_exhausted_reason = local_exhausted_reason or last_exhausted_reason

            raw_candidate_count += local_raw_candidate_count
            all_new_candidates.extend(local_new_candidates)
            all_search_attempts.extend(local_search_attempts)
            city_search_summaries.extend(local_city_summaries)
            adapter_notes[:] = unique_strings(adapter_notes + query_state.adapter_notes)
            for candidate in local_new_candidates:
                global_seen_resume_ids.add(candidate.resume_id)
                global_seen_dedup_keys.add(candidate.dedup_key)
            return local_new_candidates, local_raw_candidate_count

        async def update_lane_outcome(
            *,
            query_state: LogicalQueryState,
            new_candidates: list[ResumeCandidate],
            provider_returned_count: int,
        ) -> QueryOutcomeClassification | None:
            if score_for_query_outcome is None:
                return None
            lane_outcome = lane_outcomes[query_state.lane_type]
            lane_outcome.provider_returned_count += provider_returned_count
            if new_candidates:
                lane_outcome.new_candidates.extend(new_candidates)
                lane_outcome.scored_candidates.extend(await score_for_query_outcome(new_candidates))
            scored_candidates = lane_outcome.scored_candidates
            fit_or_near_fit_count = sum(1 for candidate in scored_candidates if candidate.fit_bucket == "fit")
            fit_rate = fit_or_near_fit_count / len(lane_outcome.new_candidates) if lane_outcome.new_candidates else 0.0
            must_have_match_avg = (
                sum(candidate.must_have_match_score for candidate in scored_candidates) / len(scored_candidates)
                if scored_candidates
                else 0.0
            )
            exploit_scores = lane_outcomes["exploit"].scored_candidates if "exploit" in lane_outcomes else []
            exploit_baseline_must_have_match_avg = (
                sum(candidate.must_have_match_score for candidate in exploit_scores) / len(exploit_scores)
                if exploit_scores
                else must_have_match_avg
            )
            off_intent_reason_count = sum(len(candidate.negative_signals) for candidate in scored_candidates)
            lane_outcome.latest = classify_query_outcome(
                provider_returned_count=lane_outcome.provider_returned_count,
                new_unique_resume_count=len(lane_outcome.new_candidates),
                new_fit_or_near_fit_count=fit_or_near_fit_count,
                fit_rate=fit_rate,
                must_have_match_avg=must_have_match_avg,
                exploit_baseline_must_have_match_avg=exploit_baseline_must_have_match_avg,
                off_intent_reason_count=off_intent_reason_count,
                thresholds=outcome_thresholds,
            )
            return lane_outcome.latest

        initial_targets = allocate_initial_lane_targets(query_states=query_states, target_new=target_new)
        for query_state in query_states:
            new_candidates, provider_returned_count = await collect_candidates_for_query(
                query_state=query_state,
                requested_count=initial_targets.get(query_state.lane_type, 0),
            )
            await update_lane_outcome(
                query_state=query_state,
                new_candidates=new_candidates,
                provider_returned_count=provider_returned_count,
            )
        while len(all_new_candidates) < target_new:
            remaining_gap = target_new - len(all_new_candidates)
            progressed = False
            for query_state in query_states:
                if remaining_gap <= 0:
                    break
                if not allow_lane_refill(
                    lane_type=query_state.lane_type,
                    outcome=lane_outcomes[query_state.lane_type].latest,
                ):
                    continue
                before = len(all_new_candidates)
                new_candidates, provider_returned_count = await collect_candidates_for_query(
                    query_state=query_state,
                    requested_count=remaining_gap,
                )
                await update_lane_outcome(
                    query_state=query_state,
                    new_candidates=new_candidates,
                    provider_returned_count=provider_returned_count,
                )
                if len(all_new_candidates) > before:
                    progressed = True
                remaining_gap = target_new - len(all_new_candidates)
            if not progressed:
                break

        search_observation = SearchObservation(
            round_no=round_no,
            requested_count=target_new,
            raw_candidate_count=raw_candidate_count,
            unique_new_count=len(all_new_candidates),
            shortage_count=max(0, target_new - len(all_new_candidates)),
            fetch_attempt_count=len(all_search_attempts),
            exhausted_reason=(
                "target_satisfied"
                if len(all_new_candidates) >= target_new
                else (
                    _final_exhausted_reason(
                        target_new=target_new,
                        new_candidate_count=len(all_new_candidates),
                        city_search_summaries=city_search_summaries,
                    )
                    if city_search_summaries
                    else (last_exhausted_reason or "cts_exhausted")
                )
            ),
            new_resume_ids=[candidate.resume_id for candidate in all_new_candidates],
            new_candidate_summaries=[candidate.compact_summary() for candidate in all_new_candidates],
            adapter_notes=adapter_notes,
            city_search_summaries=city_search_summaries,
        )
        tracer.write_json(
            f"rounds/round_{round_no:02d}/search_observation.json",
            search_observation.model_dump(mode="json"),
        )
        tracer.write_json(
            f"rounds/round_{round_no:02d}/search_attempts.json",
            [item.model_dump(mode="json") for item in all_search_attempts],
        )
        return RetrievalExecutionResult(
            cts_queries=cts_queries,
            sent_query_records=sent_query_records,
            new_candidates=all_new_candidates,
            search_observation=search_observation,
            search_attempts=all_search_attempts,
            query_resume_hits=query_resume_hits,
        )

    async def execute_search_tool(
        self,
        *,
        round_no: int,
        query: CTSQuery,
        runtime_constraints: list[RuntimeConstraint] | None,
        target_new: int,
        seen_resume_ids: set[str],
        seen_dedup_keys: set[str],
        tracer: RunTracer,
        city: str | None = None,
        phase: LocationExecutionPhase | None = None,
        batch_no: int | None = None,
        write_round_artifacts: bool = True,
        record_resume_hit: Callable[[QueryResumeHit], None] | None = None,
    ) -> tuple[list[ResumeCandidate], SearchObservation, list[SearchAttempt], int]:
        tracer.emit(
            "tool_called",
            round_no=round_no,
            tool_name="search_cts",
            summary=query.keyword_query,
            payload=query.model_dump(mode="json"),
        )
        all_new_candidates: list[ResumeCandidate] = []
        local_seen_keys = set(seen_dedup_keys)
        attempts: list[SearchAttempt] = []
        raw_candidate_count = 0
        duplicate_count = 0
        adapter_notes: list[str] = []
        cumulative_latency_ms = 0
        consecutive_zero_gain_attempts = 0
        exhausted_reason: str | None = None
        page = max(query.page, 1)
        attempt_no = 0
        effective_batch_no = batch_no if batch_no is not None else 0
        location_key, location_type = _location_hit_fields(city=city)

        while True:
            if attempt_no >= self.settings.search_max_attempts_per_round:
                exhausted_reason = "max_attempts_reached"
                break
            if page > self.settings.search_max_pages_per_round:
                exhausted_reason = "max_pages_reached"
                break
            remaining_gap = target_new - len(all_new_candidates)
            if remaining_gap <= 0:
                exhausted_reason = "target_satisfied"
                break
            attempt_no += 1
            attempt_query = query.model_copy(update={"page": page, "page_size": remaining_gap})
            try:
                fetch_result = await self.search_once(
                    attempt_query=attempt_query,
                    runtime_constraints=runtime_constraints or [],
                    round_no=round_no,
                    attempt_no=attempt_no,
                    tracer=tracer,
                )
            except Exception as exc:  # noqa: BLE001
                tracer.emit(
                    "tool_failed",
                    round_no=round_no,
                    tool_name="search_cts",
                    summary=str(exc),
                    payload={
                        "attempt_no": attempt_no,
                        "page": attempt_query.page,
                        "page_size": attempt_query.page_size,
                    },
                )
                raise
            rank_offset = raw_candidate_count
            raw_candidate_count += fetch_result.raw_candidate_count
            cumulative_latency_ms += fetch_result.latency_ms or 0
            adapter_notes = unique_strings(adapter_notes + fetch_result.diagnostics)
            batch_new: list[ResumeCandidate] = []
            batch_duplicates = 0
            for rank_in_batch, candidate in enumerate(fetch_result.candidates, start=1):
                was_new_to_pool = candidate.dedup_key not in local_seen_keys and candidate.resume_id not in seen_resume_ids
                if record_resume_hit is not None:
                    record_resume_hit(
                        QueryResumeHit(
                            run_id=tracer.run_id,
                            query_instance_id=query.query_instance_id or "",
                            query_fingerprint=query.query_fingerprint or "",
                            resume_id=candidate.resume_id,
                            round_no=round_no,
                            lane_type=query.lane_type or "exploit",
                            location_key=location_key,
                            location_type=location_type,
                            batch_no=effective_batch_no,
                            rank_in_query=rank_offset + rank_in_batch,
                            provider_name="cts",
                            provider_page_no=page,
                            provider_fetch_no=attempt_no,
                            provider_score_if_any=_provider_score_if_any(candidate),
                            dedup_key=candidate.dedup_key,
                            was_new_to_pool=was_new_to_pool,
                            was_duplicate=not was_new_to_pool,
                        )
                    )
                if candidate.dedup_key in local_seen_keys:
                    batch_duplicates += 1
                    continue
                local_seen_keys.add(candidate.dedup_key)
                if candidate.resume_id in seen_resume_ids:
                    continue
                batch_new.append(
                    _apply_first_hit_attribution(
                        candidate=candidate,
                        query=query,
                        round_no=round_no,
                        location_key=location_key,
                        location_type=location_type,
                        batch_no=effective_batch_no,
                    )
                )
            duplicate_count += batch_duplicates
            all_new_candidates.extend(batch_new)
            if batch_new:
                consecutive_zero_gain_attempts = 0
            else:
                consecutive_zero_gain_attempts += 1
            continue_refill = True
            if len(all_new_candidates) >= target_new:
                continue_refill = False
                exhausted_reason = "target_satisfied"
            elif fetch_result.raw_candidate_count == 0:
                continue_refill = False
                exhausted_reason = "cts_exhausted"
            elif consecutive_zero_gain_attempts >= self.settings.search_no_progress_limit:
                continue_refill = False
                exhausted_reason = "no_progress_repeated_results"
            elif attempt_no >= self.settings.search_max_attempts_per_round:
                continue_refill = False
                exhausted_reason = "max_attempts_reached"
            elif page >= self.settings.search_max_pages_per_round:
                continue_refill = False
                exhausted_reason = "max_pages_reached"
            attempts.append(
                SearchAttempt(
                    query_role=query.query_role,
                    city=city,
                    phase=phase,
                    batch_no=batch_no,
                    attempt_no=attempt_no,
                    requested_page=attempt_query.page,
                    requested_page_size=attempt_query.page_size,
                    raw_candidate_count=fetch_result.raw_candidate_count,
                    batch_duplicate_count=batch_duplicates,
                    batch_unique_new_count=len(batch_new),
                    cumulative_unique_new_count=len(all_new_candidates),
                    consecutive_zero_gain_attempts=consecutive_zero_gain_attempts,
                    continue_refill=continue_refill,
                    exhausted_reason=None if continue_refill else exhausted_reason,
                    adapter_notes=fetch_result.diagnostics,
                    request_payload=fetch_result.request_payload,
                )
            )
            if not continue_refill:
                break
            page += 1

        search_observation = SearchObservation(
            round_no=round_no,
            requested_count=target_new,
            raw_candidate_count=raw_candidate_count,
            unique_new_count=len(all_new_candidates),
            shortage_count=max(0, target_new - len(all_new_candidates)),
            fetch_attempt_count=len(attempts),
            exhausted_reason=exhausted_reason,
            new_resume_ids=[candidate.resume_id for candidate in all_new_candidates],
            new_candidate_summaries=[candidate.compact_summary() for candidate in all_new_candidates],
            adapter_notes=adapter_notes,
        )
        if write_round_artifacts:
            tracer.write_json(
                f"rounds/round_{round_no:02d}/search_observation.json",
                search_observation.model_dump(mode="json"),
            )
            tracer.write_json(
                f"rounds/round_{round_no:02d}/search_attempts.json",
                [item.model_dump(mode="json") for item in attempts],
            )
        tracer.emit(
            "tool_succeeded",
            round_no=round_no,
            tool_name="search_cts",
            latency_ms=cumulative_latency_ms or None,
            summary=(
                f"search_cts completed; raw_candidate_count={search_observation.raw_candidate_count}; "
                f"unique_new_count={search_observation.unique_new_count}; "
                f"shortage={search_observation.shortage_count}"
            ),
            stop_reason=search_observation.exhausted_reason if search_observation.shortage_count else None,
            payload={
                "round_no": search_observation.round_no,
                "requested_count": search_observation.requested_count,
                "raw_candidate_count": search_observation.raw_candidate_count,
                "unique_new_count": search_observation.unique_new_count,
                "shortage_count": search_observation.shortage_count,
                "fetch_attempt_count": search_observation.fetch_attempt_count,
                "exhausted_reason": search_observation.exhausted_reason,
            },
        )
        return all_new_candidates, search_observation, attempts, duplicate_count

    async def _run_city_dispatch(
        self,
        *,
        round_no: int,
        retrieval_plan: RoundRetrievalPlan,
        query_state: LogicalQueryState,
        city: str,
        phase: LocationExecutionPhase,
        batch_no: int,
        requested_count: int,
        city_state: CityExecutionState,
        seen_resume_ids: set[str],
        seen_dedup_keys: set[str],
        tracer: RunTracer,
        record_resume_hit: Callable[[QueryResumeHit], None] | None = None,
    ) -> _CityDispatchResult:
        cts_query = build_cts_query(
            CTSQueryBuildInput(
                query_role=query_state.query_role,
                query_terms=query_state.query_terms,
                keyword_query=query_state.keyword_query,
                base_filters=retrieval_plan.projected_provider_filters,
                adapter_notes=query_state.adapter_notes,
                page=city_state.next_page,
                page_size=requested_count,
                rationale=retrieval_plan.rationale,
                city=city,
            )
        )
        cts_query = cts_query.model_copy(
            update={
                "lane_type": query_state.lane_type,
                "query_instance_id": query_state.query_instance_id,
                "query_fingerprint": query_state.query_fingerprint,
            }
        )
        sent_query_record = SentQueryRecord(
            round_no=round_no,
            query_role=query_state.query_role,
            lane_type=query_state.lane_type,
            query_instance_id=query_state.query_instance_id,
            query_fingerprint=query_state.query_fingerprint,
            city=city,
            phase=phase,
            batch_no=batch_no,
            requested_count=requested_count,
            query_terms=query_state.query_terms,
            keyword_query=query_state.keyword_query,
            source_plan_version=retrieval_plan.plan_version,
            rationale=retrieval_plan.rationale,
        )
        new_candidates, search_observation, search_attempts, _ = await self.execute_search_tool(
            round_no=round_no,
            query=cts_query,
            runtime_constraints=retrieval_plan.runtime_only_constraints,
            target_new=requested_count,
            seen_resume_ids=seen_resume_ids,
            seen_dedup_keys=seen_dedup_keys,
            tracer=tracer,
            city=city,
            phase=phase,
            batch_no=batch_no,
            write_round_artifacts=False,
            record_resume_hit=record_resume_hit,
        )
        if search_attempts:
            city_state.next_page = search_attempts[-1].requested_page + 1
        if search_observation.exhausted_reason != "target_satisfied":
            city_state.exhausted = True
        city_summary = CitySearchSummary(
            query_role=query_state.query_role,
            city=city,
            phase=phase,
            batch_no=batch_no,
            requested_count=requested_count,
            unique_new_count=search_observation.unique_new_count,
            shortage_count=search_observation.shortage_count,
            start_page=cts_query.page,
            next_page=city_state.next_page,
            fetch_attempt_count=search_observation.fetch_attempt_count,
            exhausted_reason=search_observation.exhausted_reason,
        )
        return {
            "cts_query": cts_query,
            "sent_query_record": sent_query_record,
            "new_candidates": new_candidates,
            "search_observation": search_observation,
            "search_attempts": search_attempts,
            "city_summary": city_summary,
        }

    async def search_once(
        self,
        *,
        attempt_query: CTSQuery,
        runtime_constraints: list[RuntimeConstraint],
        round_no: int,
        attempt_no: int,
        tracer: RunTracer,
    ) -> SearchResult:
        return await self.retrieval_service.search(
            query_terms=attempt_query.query_terms,
            query_role=_provider_query_role(attempt_query.query_role),
            keyword_query=attempt_query.keyword_query,
            adapter_notes=attempt_query.adapter_notes,
            provider_filters=attempt_query.native_filters,
            runtime_constraints=runtime_constraints,
            page_size=attempt_query.page_size,
            round_no=round_no,
            trace_id=f"{tracer.run_id}-r{round_no}-a{attempt_no}",
            cursor=str(attempt_query.page),
        )
