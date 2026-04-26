from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypedDict

from seektalent.config import AppSettings
from seektalent.core.retrieval.provider_contract import SearchResult
from seektalent.core.retrieval.service import RetrievalService
from seektalent.models import (
    CTSQuery,
    CitySearchSummary,
    LocationExecutionPhase,
    QueryRole,
    RoundRetrievalPlan,
    ResumeCandidate,
    RuntimeConstraint,
    SearchAttempt,
    SearchObservation,
    SentQueryRecord,
    unique_strings,
)
from seektalent.retrieval import allocate_balanced_city_targets
from seektalent.tracing import RunTracer


def _provider_query_role(query_role: QueryRole) -> Literal["primary", "expansion"]:
    if query_role == "exploit":
        return "primary"
    return "expansion"


def _dedup_batch(
    *,
    candidates: list[ResumeCandidate],
    local_seen_keys: set[str],
) -> tuple[list[ResumeCandidate], int]:
    batch_new: list[ResumeCandidate] = []
    duplicates = 0
    for candidate in candidates:
        if candidate.dedup_key in local_seen_keys:
            duplicates += 1
            continue
        local_seen_keys.add(candidate.dedup_key)
        batch_new.append(candidate)
    return batch_new, duplicates


@dataclass
class CityExecutionState:
    next_page: int = 1
    exhausted: bool = False


@dataclass
class LogicalQueryState:
    query_role: QueryRole
    query_terms: list[str]
    keyword_query: str
    next_page: int = 1
    exhausted: bool = False
    adapter_notes: list[str] = field(default_factory=list)
    city_states: dict[str, CityExecutionState] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalExecutionResult:
    cts_queries: list[CTSQuery]
    sent_query_records: list[SentQueryRecord]
    new_candidates: list[ResumeCandidate]
    search_observation: SearchObservation
    search_attempts: list[SearchAttempt]


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
        raw_candidate_count = 0
        batch_no = 0
        last_exhausted_reason: str | None = None

        async def collect_candidates_for_query(
            *,
            query_state: LogicalQueryState,
            requested_count: int,
        ) -> None:
            nonlocal batch_no, raw_candidate_count, last_exhausted_reason
            if requested_count <= 0 or query_state.exhausted:
                return
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
                query = CTSQuery(
                    query_role=query_state.query_role,
                    query_terms=query_state.query_terms,
                    keyword_query=query_state.keyword_query,
                    native_filters=dict(retrieval_plan.projected_cts_filters),
                    page=query_state.next_page,
                    page_size=requested_count,
                    rationale=retrieval_plan.rationale,
                    adapter_notes=list(query_state.adapter_notes),
                )
                sent_query_record = SentQueryRecord(
                    round_no=round_no,
                    query_role=query_state.query_role,
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

        initial_targets = (
            [target_new]
            if len(query_states) == 1
            else [target_new // 2, target_new - (target_new // 2)]
        )
        for query_state, requested_count in zip(query_states, initial_targets):
            await collect_candidates_for_query(
                query_state=query_state,
                requested_count=requested_count,
            )
        while len(all_new_candidates) < target_new:
            remaining_gap = target_new - len(all_new_candidates)
            progressed = False
            for query_state in query_states:
                if remaining_gap <= 0:
                    break
                before = len(all_new_candidates)
                await collect_candidates_for_query(
                    query_state=query_state,
                    requested_count=remaining_gap,
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
            raw_candidate_count += fetch_result.raw_candidate_count
            cumulative_latency_ms += fetch_result.latency_ms or 0
            adapter_notes = unique_strings(adapter_notes + fetch_result.diagnostics)
            batch_new, batch_duplicates = _dedup_batch(
                candidates=fetch_result.candidates,
                local_seen_keys=local_seen_keys,
            )
            batch_new = [item for item in batch_new if item.resume_id not in seen_resume_ids]
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
    ) -> _CityDispatchResult:
        cts_query = CTSQuery(
            query_role=query_state.query_role,
            query_terms=query_state.query_terms,
            keyword_query=query_state.keyword_query,
            native_filters={**retrieval_plan.projected_cts_filters, "location": [city]},
            page=city_state.next_page,
            page_size=requested_count,
            rationale=retrieval_plan.rationale,
            adapter_notes=unique_strings([*query_state.adapter_notes, f"runtime location dispatch: {city}"]),
        )
        sent_query_record = SentQueryRecord(
            round_no=round_no,
            query_role=query_state.query_role,
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
