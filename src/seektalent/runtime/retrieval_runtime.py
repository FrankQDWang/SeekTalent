from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from seektalent.config import AppSettings
from seektalent.core.retrieval.provider_contract import SearchResult
from seektalent.core.retrieval.service import RetrievalService
from seektalent.models import (
    CTSQuery,
    LocationExecutionPhase,
    QueryRole,
    ResumeCandidate,
    RuntimeConstraint,
    SearchAttempt,
    SearchObservation,
    unique_strings,
)
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


@dataclass(frozen=True)
class RetrievalRuntime:
    settings: AppSettings
    retrieval_service: RetrievalService

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
