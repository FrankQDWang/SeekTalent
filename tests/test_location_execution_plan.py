import asyncio
from pathlib import Path

from seektalent.core.retrieval.provider_contract import SearchResult
from seektalent.models import (
    HardConstraintSlots,
    PreferenceSlots,
    QueryTermCandidate,
    RequirementSheet,
    ResumeCandidate,
    SentQueryRecord,
)
from seektalent.retrieval import build_location_execution_plan, build_round_retrieval_plan
from seektalent.runtime import WorkflowRuntime
from seektalent.tracing import RunTracer
from tests.settings_factory import make_settings


def _candidate(resume_id: str, city: str) -> ResumeCandidate:
    return ResumeCandidate(
        resume_id=resume_id,
        dedup_key=resume_id,
        now_location=city,
        expected_location=city,
        expected_job_category="Python Engineer",
        work_year=5,
        education_summaries=[],
        work_experience_summaries=[f"{city} Company | Python Engineer | Built retrieval flows."],
        project_names=[f"{city} project"],
        work_summaries=["python", "retrieval"],
        search_text=f"{city} python retrieval",
        raw={"resume_id": resume_id},
    )


def _requirement_sheet(locations: list[str], preferred_locations: list[str]) -> RequirementSheet:
    return RequirementSheet(
        role_title="Python Engineer",
        title_anchor_terms=["python"],
        title_anchor_rationale="Title maps directly to the Python role anchor.",
        role_summary="Build retrieval systems.",
        must_have_capabilities=["python"],
        hard_constraints=HardConstraintSlots(locations=locations),
        preferences=PreferenceSlots(preferred_locations=preferred_locations),
        initial_query_term_pool=[
            QueryTermCandidate(
                term="python",
                source="job_title",
                category="role_anchor",
                priority=1,
                evidence="Job title",
                first_added_round=0,
            ),
            QueryTermCandidate(
                term="retrieval",
                source="jd",
                category="domain",
                priority=2,
                evidence="JD body",
                first_added_round=0,
            ),
            QueryTermCandidate(
                term="trace",
                source="notes",
                category="expansion",
                priority=3,
                evidence="Notes",
                first_added_round=0,
            ),
        ],
        scoring_rationale="test",
    )


class RecordingCTS:
    def __init__(self, pages: dict[str, dict[int, list[str]]]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, int, int]] = []

    async def search(
        self,
        *,
        query_terms,
        query_role,
        keyword_query,
        adapter_notes,
        provider_filters,
        runtime_constraints,
        page_size,
        round_no,
        trace_id,
        fetch_mode="summary",
        cursor=None,
    ) -> SearchResult:
        del query_terms, query_role, keyword_query, adapter_notes, runtime_constraints, round_no, trace_id, fetch_mode
        locations = provider_filters.get("location")
        city = locations[0] if isinstance(locations, list) else ""
        page = int(cursor or "1")
        self.calls.append((city, page, page_size))
        resume_ids = self.pages.get(city, {}).get(page, [])
        candidates = [_candidate(resume_id, city) for resume_id in resume_ids[:page_size]]
        return SearchResult(
            candidates=candidates,
            diagnostics=[f"city={city}", f"page={page}"],
            request_payload={"location": city, "page": page, "pageSize": page_size},
            raw_candidate_count=len(candidates),
            latency_ms=1,
            exhausted=len(candidates) < page_size,
            next_cursor=None if len(candidates) < page_size else str(page + 1),
        )


class DualQueryCTS:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int]] = []

    async def search(
        self,
        *,
        query_terms,
        query_role,
        keyword_query,
        adapter_notes,
        provider_filters,
        runtime_constraints,
        page_size,
        round_no,
        trace_id,
        fetch_mode="summary",
        cursor=None,
    ) -> SearchResult:
        del query_terms, keyword_query, adapter_notes, provider_filters, runtime_constraints, round_no, trace_id, fetch_mode
        page = int(cursor or "1")
        self.calls.append((query_role, page, page_size))
        pages = {
            ("primary", 1): ["exp-1", "exp-2", "exp-3", "exp-4", "exp-5", "exp-6", "exp-7"],
            ("expansion", 1): ["exp-2", "exp-5", "new-1", "new-2", "new-3"],
            ("expansion", 2): ["new-4", "new-5", "new-6", "new-7", "new-8"],
        }
        candidates = [_candidate(resume_id, "上海") for resume_id in pages.get((query_role, page), [])]
        selected = candidates[:page_size]
        return SearchResult(
            candidates=selected,
            diagnostics=[f"role={query_role}", f"page={page}"],
            request_payload={"query_role": query_role, "page": page, "pageSize": page_size},
            raw_candidate_count=min(len(candidates), page_size),
            latency_ms=1,
            exhausted=len(selected) < page_size,
            next_cursor=None if len(selected) < page_size else str(page + 1),
        )


def _runtime(tmp_path: Path, retrieval_service) -> WorkflowRuntime:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        search_max_pages_per_round=3,
        search_max_attempts_per_round=3,
        search_no_progress_limit=2,
    )
    runtime = WorkflowRuntime(settings)
    runtime.retrieval_service = retrieval_service
    return runtime


def test_build_location_execution_plan_rotates_balanced_order() -> None:
    plan = build_location_execution_plan(
        allowed_locations=["上海", "北京", "深圳"],
        preferred_locations=[],
        round_no=2,
        target_new=5,
    )

    assert plan.mode == "balanced_all"
    assert plan.rotation_offset == 1
    assert plan.balanced_order == ["北京", "深圳", "上海"]


def test_build_location_execution_plan_splits_priority_and_fallback() -> None:
    plan = build_location_execution_plan(
        allowed_locations=["上海", "北京", "深圳"],
        preferred_locations=["深圳", "上海"],
        round_no=2,
        target_new=5,
    )

    assert plan.mode == "priority_then_fallback"
    assert plan.priority_order == ["深圳", "上海"]
    assert plan.balanced_order == ["北京"]


def test_execute_location_search_plan_stops_after_priority_city_hits_target(tmp_path: Path) -> None:
    cts_client = RecordingCTS({"上海": {1: ["sh-1", "sh-2", "sh-3"]}})
    runtime = _runtime(tmp_path, cts_client)
    requirement_sheet = _requirement_sheet(["上海", "北京"], ["上海"])
    retrieval_plan = build_round_retrieval_plan(
        plan_version=1,
        round_no=1,
        query_terms=["python", "retrieval"],
        title_anchor_terms=requirement_sheet.title_anchor_terms,
        query_term_pool=requirement_sheet.initial_query_term_pool,
        projected_provider_filters={},
        runtime_only_constraints=[],
        location_execution_plan=build_location_execution_plan(
            allowed_locations=requirement_sheet.hard_constraints.locations,
            preferred_locations=requirement_sheet.preferences.preferred_locations,
            round_no=1,
            target_new=3,
        ),
        target_new=3,
        rationale="priority test",
    )
    tracer = RunTracer(tmp_path / "trace-priority")
    query_states = runtime._build_round_query_states(
        round_no=1,
        retrieval_plan=retrieval_plan,
        title_anchor_terms=requirement_sheet.title_anchor_terms,
        query_term_pool=requirement_sheet.initial_query_term_pool,
        sent_query_history=[],
    )

    try:
        cts_queries, sent_query_records, new_candidates, search_observation, _ = asyncio.run(
            runtime._execute_location_search_plan(
                round_no=1,
                retrieval_plan=retrieval_plan,
                query_states=query_states,
                base_adapter_notes=[],
                target_new=3,
                seen_resume_ids=set(),
                seen_dedup_keys=set(),
                tracer=tracer,
            )
        )
    finally:
        tracer.close()

    assert [call[0] for call in cts_client.calls] == ["上海"]
    assert [record.city for record in sent_query_records] == ["上海"]
    assert [record.query_role for record in sent_query_records] == ["exploit"]
    assert [query.native_filters["location"] for query in cts_queries] == [["上海"]]
    assert [candidate.resume_id for candidate in new_candidates] == ["sh-1", "sh-2", "sh-3"]
    assert search_observation.shortage_count == 0
    assert len(search_observation.city_search_summaries) == 1


def test_execute_location_search_plan_reuses_city_after_balanced_shortage(tmp_path: Path) -> None:
    cts_client = RecordingCTS(
        {
            "上海": {
                1: ["sh-1", "sh-2"],
                2: ["sh-3", "sh-4"],
            },
            "北京": {
                1: [],
            },
        }
    )
    runtime = _runtime(tmp_path, cts_client)
    requirement_sheet = _requirement_sheet(["上海", "北京"], [])
    retrieval_plan = build_round_retrieval_plan(
        plan_version=1,
        round_no=1,
        query_terms=["python", "retrieval"],
        title_anchor_terms=requirement_sheet.title_anchor_terms,
        query_term_pool=requirement_sheet.initial_query_term_pool,
        projected_provider_filters={},
        runtime_only_constraints=[],
        location_execution_plan=build_location_execution_plan(
            allowed_locations=requirement_sheet.hard_constraints.locations,
            preferred_locations=requirement_sheet.preferences.preferred_locations,
            round_no=1,
            target_new=4,
        ),
        target_new=4,
        rationale="balanced retry test",
    )
    tracer = RunTracer(tmp_path / "trace-balanced")
    query_states = runtime._build_round_query_states(
        round_no=1,
        retrieval_plan=retrieval_plan,
        title_anchor_terms=requirement_sheet.title_anchor_terms,
        query_term_pool=requirement_sheet.initial_query_term_pool,
        sent_query_history=[],
    )

    try:
        _, sent_query_records, new_candidates, search_observation, _ = asyncio.run(
            runtime._execute_location_search_plan(
                round_no=1,
                retrieval_plan=retrieval_plan,
                query_states=query_states,
                base_adapter_notes=[],
                target_new=4,
                seen_resume_ids=set(),
                seen_dedup_keys=set(),
                tracer=tracer,
            )
        )
    finally:
        tracer.close()

    assert cts_client.calls == [("上海", 1, 2), ("北京", 1, 2), ("上海", 2, 2)]
    assert [(record.city, record.batch_no, record.requested_count) for record in sent_query_records] == [
        ("上海", 1, 2),
        ("北京", 1, 2),
        ("上海", 2, 2),
    ]
    assert [candidate.resume_id for candidate in new_candidates] == ["sh-1", "sh-2", "sh-3", "sh-4"]
    assert search_observation.shortage_count == 0
    assert [item.next_page for item in search_observation.city_search_summaries] == [2, 2, 3]


def test_execute_location_search_plan_merges_dual_query_challengers_into_top_10(tmp_path: Path) -> None:
    cts_client = DualQueryCTS()
    runtime = _runtime(tmp_path, cts_client)
    requirement_sheet = _requirement_sheet(["上海"], [])
    requirement_sheet = requirement_sheet.model_copy(
        update={
            "initial_query_term_pool": [
                *requirement_sheet.initial_query_term_pool,
                QueryTermCandidate(
                    term="ranking",
                    source="notes",
                    category="expansion",
                    priority=4,
                    evidence="Notes",
                    first_added_round=0,
                    active=False,
                ),
            ]
        }
    )
    retrieval_plan = build_round_retrieval_plan(
        plan_version=2,
        round_no=2,
        query_terms=["python", "retrieval", "trace"],
        title_anchor_terms=requirement_sheet.title_anchor_terms,
        query_term_pool=requirement_sheet.initial_query_term_pool,
        projected_provider_filters={},
        runtime_only_constraints=[],
        location_execution_plan=build_location_execution_plan(
            allowed_locations=requirement_sheet.hard_constraints.locations,
            preferred_locations=requirement_sheet.preferences.preferred_locations,
            round_no=2,
            target_new=10,
        ),
        target_new=10,
        rationale="dual query test",
    )
    query_states = runtime._build_round_query_states(
        round_no=2,
        retrieval_plan=retrieval_plan,
        title_anchor_terms=requirement_sheet.title_anchor_terms,
        query_term_pool=requirement_sheet.initial_query_term_pool,
        sent_query_history=[
            SentQueryRecord(
                round_no=1,
                query_terms=["python", "retrieval"],
                keyword_query="python retrieval",
                batch_no=1,
                requested_count=10,
                source_plan_version=1,
                rationale="round 1",
            )
        ],
    )
    tracer = RunTracer(tmp_path / "trace-dual")

    try:
        cts_queries, sent_query_records, new_candidates, search_observation, search_attempts = asyncio.run(
            runtime._execute_location_search_plan(
                round_no=2,
                retrieval_plan=retrieval_plan,
                query_states=query_states,
                base_adapter_notes=[],
                target_new=10,
                seen_resume_ids=set(),
                seen_dedup_keys=set(),
                tracer=tracer,
            )
        )
    finally:
        tracer.close()

    assert [item.query_role for item in query_states] == ["exploit", "explore"]
    assert [item.query_role for item in sent_query_records] == ["exploit", "explore"]
    assert [query.query_role for query in cts_queries] == ["exploit", "explore"]
    assert len(new_candidates) == 10
    assert len({candidate.resume_id for candidate in new_candidates}) == 10
    assert search_observation.unique_new_count == 10
    assert search_observation.shortage_count == 0
    assert search_observation.new_resume_ids == [candidate.resume_id for candidate in new_candidates]
    assert any(item.query_role == "explore" and item.attempt_no == 2 for item in search_attempts)
