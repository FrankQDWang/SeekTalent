import asyncio
import json
from pathlib import Path

from cv_match.clients.cts_client import CTSClientProtocol, CTSFetchResult
from cv_match.config import AppSettings
from cv_match.models import (
    CTSQuery,
    FinalCandidate,
    FinalResult,
    HardConstraintSlots,
    ProposedFilterPlan,
    QueryTermCandidate,
    ReflectionAdvice,
    ReflectionFilterAdvice,
    ReflectionKeywordAdvice,
    RequirementExtractionDraft,
    RequirementSheet,
    ResumeCandidate,
    ScoredCandidate,
    ScoringFailure,
    SearchControllerDecision,
    StopControllerDecision,
)
from cv_match.runtime import WorkflowRuntime
from cv_match.tracing import RunTracer


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[object]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [json.loads(line) for line in lines]


def _sample_inputs() -> tuple[str, str]:
    return (
        "Senior Python Engineer responsible for resume matching workflows.",
        "Prefer retrieval experience and shipping production AI features.",
    )


def _make_candidate(resume_id: str, *, location: str = "上海") -> ResumeCandidate:
    return ResumeCandidate(
        resume_id=resume_id,
        dedup_key=resume_id,
        now_location=location,
        expected_location=location,
        expected_job_category="Python Engineer",
        work_year=6,
        education_summaries=["复旦大学 计算机 本科"],
        work_experience_summaries=["Example Co | Python Engineer | Built retrieval and tracing workflows."],
        project_names=["Resume search"],
        work_summaries=["python", "retrieval", "trace"],
        search_text="python retrieval trace resume search",
        raw={"resume_id": resume_id, "candidate_name": resume_id},
    )


class DuplicatePagingCTS(CTSClientProtocol):
    async def search(self, query: CTSQuery, *, round_no: int, trace_id: str) -> CTSFetchResult:
        del round_no, trace_id
        if query.page == 1:
            candidates = [_make_candidate("dup-1"), _make_candidate("dup-1")]
        elif query.page == 2:
            candidates = [_make_candidate("uniq-2")]
        else:
            candidates = []
        return CTSFetchResult(
            request_payload={"page": query.page, "pageSize": query.page_size},
            candidates=candidates,
            raw_candidate_count=len(candidates),
            adapter_notes=[f"served page {query.page}"],
            latency_ms=1,
            response_message="ok",
        )


class StubController:
    last_validator_retry_count = 0

    async def decide(self, *, context):
        return SearchControllerDecision(
            thought_summary="Continue retrieval with the current requirement truth.",
            action="search_cts",
            decision_rationale="Need one live retrieval round for the audit fixture.",
            proposed_query_terms=["python", "resume matching"],
            proposed_filter_plan=ProposedFilterPlan(),
        )


class StopOnSecondController:
    def __init__(self) -> None:
        self.calls = 0
        self.last_validator_retry_count = 0

    async def decide(self, *, context):
        self.calls += 1
        if self.calls == 1:
            return SearchControllerDecision(
                thought_summary="Continue retrieval with the current requirement truth.",
                action="search_cts",
                decision_rationale="Need one live retrieval round for the audit fixture.",
                proposed_query_terms=["python", "resume matching"],
                proposed_filter_plan=ProposedFilterPlan(),
            )
        return StopControllerDecision(
            thought_summary="Stop after the first completed retrieval round.",
            action="stop",
            decision_rationale="The pool is stable enough for the stop-round audit fixture.",
            response_to_reflection="Accepted the reflection recommendation to stop.",
            stop_reason="controller_stop",
        )


class StubRequirementExtractor:
    async def extract_with_draft(self, *, input_truth) -> tuple[RequirementExtractionDraft, RequirementSheet]:
        del input_truth
        draft = RequirementExtractionDraft(
            role_title="Senior Python Engineer",
            role_summary="Build resume matching workflows.",
            must_have_capabilities=["python", "resume matching"],
            locations=["上海"],
            preferred_query_terms=["python", "resume matching"],
            scoring_rationale="Score Python fit first.",
        )
        return draft, await self.extract(input_truth=None)

    async def extract(self, *, input_truth) -> RequirementSheet:
        del input_truth
        return RequirementSheet(
            role_title="Senior Python Engineer",
            role_summary="Build resume matching workflows.",
            must_have_capabilities=["python", "resume matching"],
            hard_constraints=HardConstraintSlots(locations=["上海"]),
            initial_query_term_pool=[
                QueryTermCandidate(
                    term="python",
                    source="jd",
                    category="role_anchor",
                    priority=1,
                    evidence="JD title",
                    first_added_round=0,
                ),
                QueryTermCandidate(
                    term="resume matching",
                    source="notes",
                    category="domain",
                    priority=2,
                    evidence="Notes mention resume matching.",
                    first_added_round=0,
                ),
            ],
            scoring_rationale="Score Python fit first.",
        )


class StubScorer:
    async def score_candidates_parallel(self, *, contexts, tracer):
        scored: list[ScoredCandidate] = []
        for context in contexts:
            candidate = context.normalized_resume
            call_id = f"scoring-r{context.round_no:02d}-stub-{candidate.resume_id}"
            tracer.append_jsonl(
                f"rounds/round_{context.round_no:02d}/scoring_calls.jsonl",
                {
                    "stage": "scoring",
                    "call_id": call_id,
                    "round_no": context.round_no,
                    "resume_id": candidate.resume_id,
                    "branch_id": f"r{context.round_no}-{candidate.resume_id}",
                    "model_id": "stub-scorer",
                    "provider": "stub",
                    "prompt_hash": "stub",
                    "prompt_snapshot_path": "prompt_snapshots/scoring.md",
                    "output_mode": "native_strict",
                    "retries": 0,
                    "output_retries": 1,
                    "started_at": "stub",
                    "latency_ms": 1,
                    "status": "succeeded",
                    "user_payload": {},
                    "structured_output": {"resume_id": candidate.resume_id},
                    "error_message": None,
                    "validator_retry_count": 0,
                },
            )
            tracer.emit(
                "score_branch_completed",
                round_no=context.round_no,
                resume_id=candidate.resume_id,
                branch_id=f"r{context.round_no}-{candidate.resume_id}",
                model="stub-scorer",
                call_id=call_id,
                status="succeeded",
                summary="stub score",
                artifact_paths=[f"rounds/round_{context.round_no:02d}/scoring_calls.jsonl"],
                payload={},
            )
            scored.append(
                ScoredCandidate(
                    resume_id=candidate.resume_id,
                    fit_bucket="fit",
                    overall_score=90,
                    must_have_match_score=88,
                    preferred_match_score=70,
                    risk_score=8,
                    risk_flags=[],
                    reasoning_summary="Stub scorer accepted the candidate.",
                    evidence=["python"],
                    confidence="high",
                    matched_must_haves=["python"],
                    missing_must_haves=[],
                    matched_preferences=["trace"],
                    negative_signals=[],
                    strengths=["Strong backend match."],
                    weaknesses=[],
                    source_round=candidate.source_round or context.round_no,
                )
            )
        return scored, []


class FailingScorer:
    async def score_candidates_parallel(self, *, contexts, tracer):
        candidate = contexts[0].normalized_resume
        failure = ScoringFailure(
            resume_id=candidate.resume_id,
            branch_id=f"r{contexts[0].round_no}-b1-{candidate.resume_id}",
            round_no=contexts[0].round_no,
            attempts=1,
            error_message="forced scoring failure",
            latency_ms=1,
        )
        tracer.append_jsonl(
            f"rounds/round_{contexts[0].round_no:02d}/scoring_calls.jsonl",
            {
                "stage": "scoring",
                "call_id": f"scoring-r{contexts[0].round_no:02d}-stub-{candidate.resume_id}",
                "round_no": contexts[0].round_no,
                "resume_id": failure.resume_id,
                "branch_id": failure.branch_id,
                "model_id": "stub-scorer",
                "provider": "stub",
                "prompt_hash": "stub",
                "prompt_snapshot_path": "prompt_snapshots/scoring.md",
                "output_mode": "native_strict",
                "retries": 0,
                "output_retries": 1,
                "started_at": "stub",
                "latency_ms": 1,
                "status": "failed",
                "user_payload": {},
                "structured_output": None,
                "error_message": failure.error_message,
                "validator_retry_count": 0,
            },
        )
        tracer.emit(
            "score_branch_failed",
            round_no=contexts[0].round_no,
            resume_id=failure.resume_id,
            branch_id=failure.branch_id,
            model="stub-scorer",
            call_id=f"scoring-r{contexts[0].round_no:02d}-stub-{candidate.resume_id}",
            status="failed",
            latency_ms=1,
            summary=failure.error_message,
            error_message=failure.error_message,
            artifact_paths=[f"rounds/round_{contexts[0].round_no:02d}/scoring_calls.jsonl"],
            payload={"attempts": 1},
        )
        return [], [failure]


class StubReflection:
    async def reflect(self, *, context) -> ReflectionAdvice:
        del context
        return ReflectionAdvice(
            strategy_assessment="Current strategy is acceptable.",
            quality_assessment="The top pool quality is acceptable.",
            coverage_assessment="Coverage is sufficient for one round.",
            keyword_advice=ReflectionKeywordAdvice(),
            filter_advice=ReflectionFilterAdvice(suggested_keep_filter_fields=["position"]),
            suggest_stop=False,
            suggested_stop_reason=None,
            reflection_summary="No reflection changes.",
        )


class StubFinalizer:
    last_validator_retry_count = 0

    async def finalize(self, *, run_id, run_dir, rounds_executed, stop_reason, ranked_candidates) -> FinalResult:
        candidates = [
            FinalCandidate(
                resume_id=item.resume_id,
                rank=index,
                final_score=item.overall_score,
                fit_bucket=item.fit_bucket,
                match_summary="Must 88/100, preferred 70/100, risk 8/100.",
                strengths=item.strengths,
                weaknesses=item.weaknesses,
                matched_must_haves=item.matched_must_haves,
                matched_preferences=item.matched_preferences,
                risk_flags=item.risk_flags,
                why_selected=item.reasoning_summary,
                source_round=item.source_round,
            )
            for index, item in enumerate(ranked_candidates, start=1)
        ]
        return FinalResult(
            run_id=run_id,
            run_dir=run_dir,
            rounds_executed=rounds_executed,
            stop_reason=stop_reason,
            candidates=candidates,
            summary=f"Returned {len(candidates)} candidates after {rounds_executed} rounds.",
        )


def test_execute_search_tool_refills_after_batch_dedup(tmp_path: Path) -> None:
    settings = AppSettings(_env_file=None).with_overrides(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        search_max_pages_per_round=3,
        search_max_attempts_per_round=3,
        search_no_progress_limit=2,
    )
    runtime = WorkflowRuntime(settings)
    runtime.cts_client = DuplicatePagingCTS()
    tracer = RunTracer(tmp_path / "trace-runs")
    query = CTSQuery(
        query_terms=["python", "retrieval"],
        keyword_query="python retrieval",
        native_filters={},
        page=1,
        page_size=2,
        rationale="test refill after dedup",
    )

    try:
        new_candidates, observation, attempts, duplicate_count = asyncio.run(
            runtime._execute_search_tool(
                round_no=1,
                query=query,
                target_new=2,
                seen_resume_ids=set(),
                seen_dedup_keys=set(),
                tracer=tracer,
            )
        )
    finally:
        tracer.close()

    assert [candidate.resume_id for candidate in new_candidates] == ["dup-1", "uniq-2"]
    assert duplicate_count == 1
    assert len(attempts) == 2
    assert attempts[0].batch_duplicate_count == 1
    assert attempts[0].batch_unique_new_count == 1
    assert attempts[0].continue_refill is True
    assert attempts[1].cumulative_unique_new_count == 2
    assert observation.unique_new_count == 2
    assert observation.shortage_count == 0
    assert observation.new_resume_ids == ["dup-1", "uniq-2"]


def test_runtime_writes_v02_audit_outputs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    settings = AppSettings(_env_file=None).with_overrides(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=1,
        cts_tenant_key="tenant-key",
        cts_tenant_secret="tenant-secret",
    )
    runtime = WorkflowRuntime(settings)
    runtime.requirement_extractor = StubRequirementExtractor()
    runtime.controller = StubController()
    runtime.resume_scorer = StubScorer()
    runtime.reflection_critic = StubReflection()
    runtime.finalizer = StubFinalizer()
    jd, notes = _sample_inputs()

    artifacts = runtime.run(jd=jd, notes=notes)

    round_dir = artifacts.run_dir / "rounds" / "round_01"
    controller_decision = _read_json(round_dir / "controller_decision.json")
    retrieval_plan = _read_json(round_dir / "retrieval_plan.json")
    projection_result = _read_json(round_dir / "constraint_projection_result.json")
    sent_query_records = _read_json(round_dir / "sent_query_records.json")
    cts_queries = _read_json(round_dir / "cts_queries.json")
    search_observation = _read_json(round_dir / "search_observation.json")
    requirements_call = _read_json(artifacts.run_dir / "requirements_call.json")
    requirement_draft = _read_json(artifacts.run_dir / "requirement_extraction_draft.json")
    controller_call = _read_json(round_dir / "controller_call.json")
    reflection_call = _read_json(round_dir / "reflection_call.json")
    scoring_calls = _read_jsonl(round_dir / "scoring_calls.jsonl")
    finalizer_call = _read_json(artifacts.run_dir / "finalizer_call.json")
    judge_packet = _read_json(artifacts.run_dir / "judge_packet.json")
    scorecards = _read_jsonl(round_dir / "scorecards.jsonl")
    sent_query_history = _read_json(artifacts.run_dir / "sent_query_history.json")
    run_config = _read_json(artifacts.run_dir / "run_config.json")
    final_candidates = _read_json(artifacts.run_dir / "final_candidates.json")
    run_summary = (artifacts.run_dir / "run_summary.md").read_text(encoding="utf-8")
    round_review = (round_dir / "round_review.md").read_text(encoding="utf-8")
    events = _read_jsonl(artifacts.run_dir / "events.jsonl")

    assert len(controller_decision["proposed_query_terms"]) == 2
    assert retrieval_plan["query_terms"] == controller_decision["proposed_query_terms"]
    assert retrieval_plan["location_execution_plan"]["mode"] == "single"
    assert len(sent_query_records) == 1
    assert len(cts_queries) == 1
    assert sent_query_records[0]["query_terms"] == retrieval_plan["query_terms"]
    assert sent_query_records[0]["keyword_query"] == retrieval_plan["keyword_query"]
    assert sent_query_records[0]["city"] == "上海"
    assert cts_queries[0]["query_terms"] == retrieval_plan["query_terms"]
    assert cts_queries[0]["native_filters"] == {
        **projection_result["cts_native_filters"],
        "location": ["上海"],
    }
    assert sent_query_history == sent_query_records

    assert len(search_observation["new_resume_ids"]) == len(set(search_observation["new_resume_ids"]))
    assert search_observation["city_search_summaries"][0]["city"] == "上海"
    assert artifacts.candidate_store
    assert artifacts.normalized_store
    assert set(artifacts.normalized_store) <= set(artifacts.candidate_store)

    scorecard_ids = [item["resume_id"] for item in scorecards]
    assert len(scorecard_ids) == len(set(scorecard_ids))
    assert final_candidates["summary"]
    assert all(candidate["match_summary"] for candidate in final_candidates["candidates"])
    assert requirements_call["user_payload"]["INPUT_TRUTH"]["jd"]
    assert requirement_draft["role_title"] == "Senior Python Engineer"
    assert controller_call["user_payload"]["CONTROLLER_CONTEXT"]["round_no"] == 1
    assert controller_call["structured_output"]["action"] == "search_cts"
    assert reflection_call["user_payload"]["REFLECTION_CONTEXT"]["round_no"] == 1
    assert reflection_call["structured_output"]["reflection_summary"] == "No reflection changes."
    assert len(scoring_calls) == len(scorecards)
    assert scoring_calls[0]["resume_id"] == "mock-r001"
    assert scoring_calls[0]["status"] == "succeeded"
    assert (
        finalizer_call["user_payload"]["FINALIZATION_CONTEXT"]["ranked_candidates"][0]["resume_id"]
        == final_candidates["candidates"][0]["resume_id"]
    )
    assert judge_packet["requirements"]["requirement_sheet"]["role_title"] == "Senior Python Engineer"
    assert judge_packet["rounds"][0]["controller_decision"]["action"] == "search_cts"
    assert judge_packet["final"]["final_result"]["summary"] == final_candidates["summary"]

    assert "## Controller" in round_review
    assert "## Location Execution" in round_review
    assert "## City Dispatches" in round_review
    assert "Requested new candidates" in round_review
    assert "Unique new candidates" in round_review
    assert "Common drop reasons" in round_review
    assert "Reflection summary" in round_review
    assert "Next step" in round_review
    assert "# Run Summary" in run_summary
    assert "Judge packet" in run_summary
    assert "## Final Shortlist" in run_summary
    assert "Stop decision round" not in run_summary
    assert judge_packet["terminal_controller_round"] is None

    assert not (artifacts.run_dir / "round_summaries.json").exists()
    assert "cts_tenant_secret" not in json.dumps(run_config, ensure_ascii=False)
    assert "tenant-secret" not in json.dumps(run_config, ensure_ascii=False)
    assert run_config["configured_providers"] == ["openai-responses"]
    assert run_config["settings"]["requirements_model"] == "openai-responses:gpt-5.4-mini"
    assert run_config["settings"]["controller_model"] == "openai-responses:gpt-5.4-mini"
    assert (artifacts.run_dir / "prompt_snapshots" / "requirements.md").exists()
    assert (artifacts.run_dir / "prompt_snapshots" / "controller.md").exists()
    assert (artifacts.run_dir / "prompt_snapshots" / "scoring.md").exists()
    assert (artifacts.run_dir / "prompt_snapshots" / "reflection.md").exists()
    assert (artifacts.run_dir / "prompt_snapshots" / "finalize.md").exists()
    event_types = {item["event_type"] for item in events}
    assert "requirements_started" in event_types
    assert "requirements_completed" in event_types
    assert "controller_started" in event_types
    assert "controller_completed" in event_types
    assert "reflection_started" in event_types
    assert "reflection_completed" in event_types
    assert "finalizer_started" in event_types
    assert "finalizer_completed" in event_types
    controller_event = next(item for item in events if item["event_type"] == "controller_completed")
    finalizer_event = next(item for item in events if item["event_type"] == "finalizer_completed")
    run_finished_event = next(item for item in events if item["event_type"] == "run_finished")
    assert controller_event["status"] == "succeeded"
    assert "rounds/round_01/controller_call.json" in controller_event["artifact_paths"]
    assert finalizer_event["status"] == "succeeded"
    assert "judge_packet.json" in finalizer_event["artifact_paths"]
    assert run_finished_event["summary"] == "Run completed after 1 retrieval rounds."


def test_runtime_audit_records_terminal_controller_round(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    settings = AppSettings(_env_file=None).with_overrides(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=2,
        cts_tenant_key="tenant-key",
        cts_tenant_secret="tenant-secret",
    )
    runtime = WorkflowRuntime(settings)
    runtime.requirement_extractor = StubRequirementExtractor()
    runtime.controller = StopOnSecondController()
    runtime.resume_scorer = StubScorer()
    runtime.reflection_critic = StubReflection()
    runtime.finalizer = StubFinalizer()

    artifacts = runtime.run(jd="JD", notes="Notes")

    run_summary = (artifacts.run_dir / "run_summary.md").read_text(encoding="utf-8")
    judge_packet = _read_json(artifacts.run_dir / "judge_packet.json")
    events = _read_jsonl(artifacts.run_dir / "events.jsonl")
    round_02_dir = artifacts.run_dir / "rounds" / "round_02"

    assert (round_02_dir / "controller_decision.json").exists()
    assert not (round_02_dir / "retrieval_plan.json").exists()
    assert judge_packet["run"]["rounds_executed"] == 1
    assert judge_packet["run"]["stop_decision_round"] == 2
    assert len(judge_packet["rounds"]) == 1
    assert judge_packet["terminal_controller_round"]["round_no"] == 2
    assert judge_packet["terminal_controller_round"]["controller_decision"]["action"] == "stop"
    assert "- Stop decision round: `2`" in run_summary
    assert "Terminal decision: The pool is stable enough for the stop-round audit fixture." in run_summary
    run_finished_event = next(item for item in events if item["event_type"] == "run_finished")
    assert run_finished_event["summary"] == "Run completed after 1 retrieval rounds; controller stopped in round 2."


def test_runtime_fails_fast_when_provider_credentials_are_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("cv_match.llm.load_process_env", lambda: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = AppSettings(_env_file=None).with_overrides(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=1,
    )
    runtime = WorkflowRuntime(settings)

    try:
        runtime.run(jd="JD", notes="Notes")
    except RuntimeError as exc:
        assert "OPENAI_API_KEY" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected run() to fail without provider credentials")

    run_dirs = sorted((tmp_path / "runs").iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    assert (run_dir / "run_config.json").exists()
    assert (run_dir / "input_snapshot.json").exists()
    assert not (run_dir / "final_candidates.json").exists()
    assert not (run_dir / "final_answer.md").exists()
    events = _read_jsonl(run_dir / "events.jsonl")
    assert events[-1]["event_type"] == "run_failed"
    assert events[-1]["payload"]["stage"] == "llm_preflight"
    assert "OPENAI_API_KEY" in events[-1]["payload"]["error_message"]


def test_runtime_aborts_when_scoring_has_a_final_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    settings = AppSettings(_env_file=None).with_overrides(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=1,
    )
    runtime = WorkflowRuntime(settings)
    runtime.requirement_extractor = StubRequirementExtractor()
    runtime.controller = StubController()
    runtime.resume_scorer = FailingScorer()
    runtime.reflection_critic = StubReflection()
    runtime.finalizer = StubFinalizer()

    try:
        runtime.run(jd="JD", notes="Notes")
    except RuntimeError as exc:
        assert str(exc) == "Scoring failed for 1 resume(s): mock-r001."
    else:  # pragma: no cover
        raise AssertionError("Expected run() to fail after a final scoring failure")

    run_dirs = sorted((tmp_path / "runs").iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    assert not (run_dir / "final_candidates.json").exists()
    assert not (run_dir / "final_answer.md").exists()
