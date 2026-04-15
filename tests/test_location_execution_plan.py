import asyncio
from pathlib import Path

from seektalent.clients.cts_client import CTSClientProtocol, CTSFetchResult
from seektalent.config import AppSettings
from seektalent.models import CTSQuery, HardConstraintSlots, QueryTermCandidate, RequirementSheet, ResumeCandidate
from seektalent.retrieval import build_location_execution_plan, build_round_retrieval_plan
from seektalent.runtime import WorkflowRuntime
from seektalent.tracing import RunTracer


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
        title_anchor_term="python",
        role_summary="Build retrieval systems.",
        must_have_capabilities=["python"],
        hard_constraints=HardConstraintSlots(locations=locations),
        preferences={"preferred_locations": preferred_locations},
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


class RecordingCTS(CTSClientProtocol):
    def __init__(self, pages: dict[str, dict[int, list[str]]]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, int, int]] = []

    async def search(self, query: CTSQuery, *, round_no: int, trace_id: str) -> CTSFetchResult:
        del round_no, trace_id
        locations = query.native_filters.get("location")
        city = locations[0] if isinstance(locations, list) else ""
        self.calls.append((city, query.page, query.page_size))
        resume_ids = self.pages.get(city, {}).get(query.page, [])
        candidates = [_candidate(resume_id, city) for resume_id in resume_ids[: query.page_size]]
        return CTSFetchResult(
            request_payload={"location": city, "page": query.page, "pageSize": query.page_size},
            candidates=candidates,
            raw_candidate_count=len(candidates),
            adapter_notes=[f"city={city}", f"page={query.page}"],
            latency_ms=1,
            response_message="ok",
        )


def _runtime(tmp_path: Path, cts_client: CTSClientProtocol) -> WorkflowRuntime:
    settings = AppSettings(_env_file=None).with_overrides(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        search_max_pages_per_round=3,
        search_max_attempts_per_round=3,
        search_no_progress_limit=2,
    )
    runtime = WorkflowRuntime(settings)
    runtime.cts_client = cts_client
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
        title_anchor_term=requirement_sheet.title_anchor_term,
        query_term_pool=requirement_sheet.initial_query_term_pool,
        projected_cts_filters={},
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

    try:
        cts_queries, sent_query_records, new_candidates, search_observation, _ = asyncio.run(
            runtime._execute_location_search_plan(
                round_no=1,
                retrieval_plan=retrieval_plan,
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
        title_anchor_term=requirement_sheet.title_anchor_term,
        query_term_pool=requirement_sheet.initial_query_term_pool,
        projected_cts_filters={},
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

    try:
        _, sent_query_records, new_candidates, search_observation, _ = asyncio.run(
            runtime._execute_location_search_plan(
                round_no=1,
                retrieval_plan=retrieval_plan,
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
