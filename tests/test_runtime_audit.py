import asyncio
import json
from pathlib import Path
from typing import Any, cast

from seektalent.clients.cts_client import CTSClientProtocol, CTSFetchResult
from seektalent.evaluation import EvaluationArtifacts, EvaluationResult, EvaluationStageResult
from seektalent.models import (
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
from seektalent.runtime import WorkflowRuntime
from seektalent.tracing import RunTracer
from tests.settings_factory import make_settings


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[Any]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [json.loads(line) for line in lines]


def _sample_inputs() -> tuple[str, str, str]:
    return (
        "Senior Python Engineer",
        "Senior Python Engineer responsible for resume matching workflows.",
        "Prefer retrieval experience and shipping production AI features.",
    )


def _make_candidate(resume_id: str, *, location: str = "上海") -> ResumeCandidate:
    return ResumeCandidate(
        resume_id=resume_id,
        source_resume_id=resume_id,
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


async def _stub_evaluation_runner(*, run_id: str, run_dir: Path, **kwargs) -> EvaluationArtifacts:  # noqa: ANN003
    del kwargs
    result = EvaluationResult(
        run_id=run_id,
        judge_model="stub-judge",
        jd_sha256="jd-hash",
        round_01=EvaluationStageResult(
            stage="round_01",
            ndcg_at_10=0.5,
            precision_at_10=0.4,
            total_score=0.43,
            candidates=[],
        ),
        final=EvaluationStageResult(
            stage="final",
            ndcg_at_10=0.7,
            precision_at_10=0.6,
            total_score=0.63,
            candidates=[],
        ),
    )
    path = run_dir / "evaluation" / "evaluation.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.model_dump(mode="json"), ensure_ascii=False), encoding="utf-8")
    raw_dir = run_dir / "raw_resumes"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "resume-1.json").write_text("{}", encoding="utf-8")
    return EvaluationArtifacts(result=result, path=path)


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


class SurfaceController:
    last_validator_retry_count = 0

    async def decide(self, *, context):
        del context
        return SearchControllerDecision(
            thought_summary="Probe the current agent-domain surface forms.",
            action="search_cts",
            decision_rationale="Need one retrieval round for term surface audit coverage.",
            proposed_query_terms=["AI Agent", "MultiAgent 架构"],
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
            title_anchor_term="python",
            jd_query_terms=["resume matching", "trace"],
            role_summary="Build resume matching workflows.",
            must_have_capabilities=["python", "resume matching"],
            locations=["上海"],
            preferred_query_terms=["python", "resume matching"],
            scoring_rationale="Score Python fit first.",
        )
        return draft, RequirementSheet(
            role_title="Senior Python Engineer",
            title_anchor_term="python",
            role_summary="Build resume matching workflows.",
            must_have_capabilities=["python", "resume matching"],
            hard_constraints=HardConstraintSlots(locations=["上海"]),
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
                    term="resume matching",
                    source="jd",
                    category="domain",
                    priority=2,
                    evidence="JD body",
                    first_added_round=0,
                ),
                QueryTermCandidate(
                    term="trace",
                    source="jd",
                    category="tooling",
                    priority=3,
                    evidence="JD body",
                    first_added_round=0,
                ),
            ],
            scoring_rationale="Score Python fit first.",
        )


class SurfaceRequirementExtractor:
    async def extract_with_draft(self, *, input_truth) -> tuple[RequirementExtractionDraft, RequirementSheet]:
        del input_truth
        draft = RequirementExtractionDraft(
            role_title="AI Agent Engineer",
            title_anchor_term="AI Agent",
            jd_query_terms=["MultiAgent 架构"],
            role_summary="Build agent applications.",
            must_have_capabilities=["AI Agent", "MultiAgent 架构"],
            locations=["上海"],
            preferred_query_terms=["AI Agent", "MultiAgent 架构"],
            scoring_rationale="Score agent fit first.",
        )
        return draft, RequirementSheet(
            role_title="AI Agent Engineer",
            title_anchor_term="AI Agent",
            role_summary="Build agent applications.",
            must_have_capabilities=["AI Agent", "MultiAgent 架构"],
            hard_constraints=HardConstraintSlots(locations=["上海"]),
            initial_query_term_pool=[
                QueryTermCandidate(
                    term="AI Agent",
                    source="job_title",
                    category="role_anchor",
                    priority=1,
                    evidence="Job title",
                    first_added_round=0,
                    retrieval_role="role_anchor",
                    queryability="admitted",
                    family="role.agent",
                ),
                QueryTermCandidate(
                    term="MultiAgent 架构",
                    source="jd",
                    category="domain",
                    priority=2,
                    evidence="JD body",
                    first_added_round=0,
                    retrieval_role="domain_context",
                    queryability="admitted",
                    family="domain.multi_agent",
                ),
            ],
            scoring_rationale="Score agent fit first.",
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
                    "output_retries": 2,
                    "started_at": "stub",
                    "latency_ms": 1,
                    "status": "succeeded",
                    "input_artifact_refs": [
                        f"rounds/round_{context.round_no:02d}/scoring_input_refs.jsonl",
                        f"resumes/{candidate.resume_id}.json",
                    ],
                    "output_artifact_refs": [
                        f"rounds/round_{context.round_no:02d}/scorecards.jsonl#resume_id={candidate.resume_id}"
                    ],
                    "input_payload_sha256": "stub-input",
                    "structured_output_sha256": "stub-output",
                    "prompt_chars": 0,
                    "input_payload_chars": 0,
                    "output_chars": 0,
                    "input_summary": f"round={context.round_no}; resume_id={candidate.resume_id}",
                    "output_summary": "fit_bucket=fit; score=90; risk=8",
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
                    "output_retries": 2,
                    "started_at": "stub",
                    "latency_ms": 1,
                    "status": "failed",
                    "input_artifact_refs": [
                        f"rounds/round_{contexts[0].round_no:02d}/scoring_input_refs.jsonl",
                        f"resumes/{candidate.resume_id}.json",
                    ],
                    "output_artifact_refs": [],
                    "input_payload_sha256": "stub-input",
                    "structured_output_sha256": None,
                    "prompt_chars": 0,
                    "input_payload_chars": 0,
                    "output_chars": 0,
                    "input_summary": f"round={contexts[0].round_no}; resume_id={candidate.resume_id}",
                    "output_summary": None,
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


def _install_runtime_stubs(runtime: WorkflowRuntime, *, controller: object, resume_scorer: object) -> None:
    runtime_any = cast(Any, runtime)
    runtime_any.requirement_extractor = StubRequirementExtractor()
    runtime_any.controller = controller
    runtime_any.resume_scorer = resume_scorer
    runtime_any.reflection_critic = StubReflection()
    runtime_any.finalizer = StubFinalizer()


def test_execute_search_tool_refills_after_batch_dedup(tmp_path: Path) -> None:
    settings = make_settings(
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
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=1,
        enable_eval=True,
        cts_tenant_key="tenant-key",
        cts_tenant_secret="tenant-secret",
    )
    runtime = WorkflowRuntime(settings)
    _install_runtime_stubs(runtime, controller=StubController(), resume_scorer=StubScorer())
    cast(Any, runtime).evaluation_runner = _stub_evaluation_runner
    job_title, jd, notes = _sample_inputs()

    artifacts = runtime.run(job_title=job_title, jd=jd, notes=notes)

    round_dir = artifacts.run_dir / "rounds" / "round_01"
    controller_decision = _read_json(round_dir / "controller_decision.json")
    retrieval_plan = _read_json(round_dir / "retrieval_plan.json")
    projection_result = _read_json(round_dir / "constraint_projection_result.json")
    sent_query_records = _read_json(round_dir / "sent_query_records.json")
    cts_queries = _read_json(round_dir / "cts_queries.json")
    search_observation = _read_json(round_dir / "search_observation.json")
    search_attempts = _read_json(round_dir / "search_attempts.json")
    requirements_call = _read_json(artifacts.run_dir / "requirements_call.json")
    requirement_draft = _read_json(artifacts.run_dir / "requirement_extraction_draft.json")
    controller_call = _read_json(round_dir / "controller_call.json")
    reflection_call = _read_json(round_dir / "reflection_call.json")
    scoring_calls = _read_jsonl(round_dir / "scoring_calls.jsonl")
    finalizer_call = _read_json(artifacts.run_dir / "finalizer_call.json")
    judge_packet = _read_json(artifacts.run_dir / "judge_packet.json")
    evaluation = _read_json(artifacts.run_dir / "evaluation" / "evaluation.json")
    scorecards = _read_jsonl(round_dir / "scorecards.jsonl")
    top_pool_snapshot = _read_json(round_dir / "top_pool_snapshot.json")
    sent_query_history = _read_json(artifacts.run_dir / "sent_query_history.json")
    run_config = _read_json(artifacts.run_dir / "run_config.json")
    final_candidates = _read_json(artifacts.run_dir / "final_candidates.json")
    controller_context = _read_json(round_dir / "controller_context.json")
    reflection_context = _read_json(round_dir / "reflection_context.json")
    finalizer_context = _read_json(artifacts.run_dir / "finalizer_context.json")
    search_diagnostics = _read_json(artifacts.run_dir / "search_diagnostics.json")
    term_surface_audit = _read_json(artifacts.run_dir / "term_surface_audit.json")
    run_summary = (artifacts.run_dir / "run_summary.md").read_text(encoding="utf-8")
    round_review = (round_dir / "round_review.md").read_text(encoding="utf-8")
    events = _read_jsonl(artifacts.run_dir / "events.jsonl")

    assert len(controller_decision["proposed_query_terms"]) == 2
    assert retrieval_plan["query_terms"] == controller_decision["proposed_query_terms"]
    assert retrieval_plan["location_execution_plan"]["mode"] == "single"
    assert len(sent_query_records) == 1
    assert len(cts_queries) == 1
    assert sent_query_records[0]["query_role"] == "exploit"
    assert sent_query_records[0]["query_terms"] == retrieval_plan["query_terms"]
    assert sent_query_records[0]["keyword_query"] == retrieval_plan["keyword_query"]
    assert sent_query_records[0]["city"] == "上海"
    assert cts_queries[0]["query_role"] == "exploit"
    assert cts_queries[0]["query_terms"] == retrieval_plan["query_terms"]
    assert cts_queries[0]["native_filters"] == {
        **projection_result["cts_native_filters"],
        "location": ["上海"],
    }
    assert sent_query_history == sent_query_records

    assert len(search_observation["new_resume_ids"]) == len(set(search_observation["new_resume_ids"]))
    assert search_observation["city_search_summaries"][0]["query_role"] == "exploit"
    assert search_observation["city_search_summaries"][0]["city"] == "上海"
    assert artifacts.candidate_store
    assert artifacts.normalized_store
    assert set(artifacts.normalized_store) <= set(artifacts.candidate_store)
    assert artifacts.evaluation_result is not None
    assert artifacts.evaluation_result.final.total_score == 0.63
    assert evaluation["final"]["total_score"] == 0.63
    assert any((artifacts.run_dir / "raw_resumes").iterdir())

    scorecard_ids = [item["resume_id"] for item in scorecards]
    assert len(scorecard_ids) == len(set(scorecard_ids))
    assert {item["resume_id"] for item in top_pool_snapshot} == set(scorecard_ids)
    assert [item["resume_id"] for item in top_pool_snapshot] == [
        item["resume_id"] for item in final_candidates["candidates"]
    ]
    assert all("sort_key" in item for item in top_pool_snapshot)
    assert not (round_dir / "normalized_resumes.jsonl").exists()
    assert (round_dir / "scoring_input_refs.jsonl").exists()
    assert "full_jd" not in controller_context
    assert "full_notes" not in controller_context
    assert controller_context["input"]["jd_sha256"]
    assert controller_context["budget"]["is_final_allowed_round"] is True
    assert "top_candidates" in reflection_context
    assert "full_jd" not in reflection_context
    assert "full_notes" not in reflection_context
    assert all("evidence" not in item for item in reflection_context["top_candidates"])
    assert "top_candidates" in finalizer_context
    assert all("evidence" not in item for item in finalizer_context["top_candidates"])
    assert final_candidates["summary"]
    assert all(candidate["match_summary"] for candidate in final_candidates["candidates"])
    assert "user_payload" not in requirements_call
    assert "structured_output" not in requirements_call
    assert requirements_call["input_payload_sha256"]
    assert requirements_call["structured_output_sha256"]
    assert requirements_call["prompt_chars"] > 0
    assert requirements_call["input_payload_chars"] > 0
    assert requirements_call["output_chars"] > 0
    assert "input_truth.json" in requirements_call["input_artifact_refs"]
    assert "requirement_extraction_draft.json" in requirements_call["output_artifact_refs"]
    assert requirements_call["retries"] == 0
    assert requirements_call["output_retries"] == 2
    assert requirement_draft["role_title"] == "Senior Python Engineer"
    assert "user_payload" not in controller_call
    assert "structured_output" not in controller_call
    assert controller_call["input_payload_sha256"]
    assert controller_call["structured_output_sha256"]
    assert controller_call["prompt_chars"] > 0
    assert controller_call["input_payload_chars"] > 0
    assert controller_call["output_chars"] > 0
    assert "round=1" in controller_call["input_summary"]
    assert "action=search_cts" in controller_call["output_summary"]
    assert "rounds/round_01/controller_context.json" in controller_call["input_artifact_refs"]
    assert "rounds/round_01/controller_decision.json" in controller_call["output_artifact_refs"]
    assert controller_call["retries"] == 0
    assert controller_call["output_retries"] == 2
    assert "user_payload" not in reflection_call
    assert "structured_output" not in reflection_call
    assert reflection_call["input_payload_sha256"]
    assert reflection_call["structured_output_sha256"]
    assert reflection_call["prompt_chars"] > 0
    assert reflection_call["input_payload_chars"] > 0
    assert reflection_call["output_chars"] > 0
    assert "round=1" in reflection_call["input_summary"]
    assert "No reflection changes." in reflection_call["output_summary"]
    assert "rounds/round_01/reflection_context.json" in reflection_call["input_artifact_refs"]
    assert "rounds/round_01/reflection_advice.json" in reflection_call["output_artifact_refs"]
    assert reflection_call["retries"] == 0
    assert reflection_call["output_retries"] == 2
    assert len(scoring_calls) == len(scorecards)
    assert scoring_calls[0]["resume_id"] == "mock-r001"
    assert scoring_calls[0]["status"] == "succeeded"
    assert scoring_calls[0]["retries"] == 0
    assert scoring_calls[0]["output_retries"] == 2
    assert "user_payload" not in scoring_calls[0]
    assert "structured_output" not in scoring_calls[0]
    assert scoring_calls[0]["input_payload_sha256"]
    assert scoring_calls[0]["structured_output_sha256"]
    assert "scoring_input_refs.jsonl" in scoring_calls[0]["input_artifact_refs"][0]
    assert "user_payload" not in finalizer_call
    assert "structured_output" not in finalizer_call
    assert finalizer_call["input_payload_sha256"]
    assert finalizer_call["structured_output_sha256"]
    assert finalizer_call["prompt_chars"] > 0
    assert finalizer_call["input_payload_chars"] > 0
    assert finalizer_call["output_chars"] > 0
    assert "ranked_candidates" in finalizer_call["input_summary"]
    assert "candidates=" in finalizer_call["output_summary"]
    assert "finalizer_context.json" in finalizer_call["input_artifact_refs"]
    assert "final_candidates.json" in finalizer_call["output_artifact_refs"]
    assert finalizer_call["retries"] == 0
    assert finalizer_call["output_retries"] == 2
    assert judge_packet["requirements"]["requirement_sheet"]["role_title"] == "Senior Python Engineer"
    assert judge_packet["rounds"][0]["controller_decision"]["action"] == "search_cts"
    assert judge_packet["final"]["final_result"]["summary"] == final_candidates["summary"]

    diagnostic_round = search_diagnostics["rounds"][0]
    schema_pressure_stages = {item["stage"] for item in search_diagnostics["llm_schema_pressure"]}
    assert search_diagnostics["run_id"] == artifacts.run_id
    assert search_diagnostics["input"]["job_title"] == job_title
    assert search_diagnostics["input"]["jd_sha256"]
    assert search_diagnostics["summary"]["rounds_executed"] == 1
    assert search_diagnostics["summary"]["total_sent_queries"] == len(sent_query_history)
    assert search_diagnostics["summary"]["total_raw_candidates"] == search_observation["raw_candidate_count"]
    assert search_diagnostics["summary"]["total_unique_new_candidates"] == search_observation["unique_new_count"]
    assert search_diagnostics["summary"]["final_candidate_count"] == len(final_candidates["candidates"])
    assert search_diagnostics["summary"]["stop_reason"] == final_candidates["stop_reason"]
    assert diagnostic_round["query_terms"] == retrieval_plan["query_terms"]
    assert diagnostic_round["keyword_query"] == retrieval_plan["keyword_query"]
    assert diagnostic_round["query_term_details"][0]["term"] == "python"
    assert "active" not in diagnostic_round["query_term_details"][0]
    assert diagnostic_round["filters"]["projected_cts_filters"] == retrieval_plan["projected_cts_filters"]
    assert diagnostic_round["search"]["duplicate_count"] == sum(
        item["batch_duplicate_count"] for item in search_attempts
    )
    assert diagnostic_round["search"]["unique_new_count"] == search_observation["unique_new_count"]
    assert diagnostic_round["scoring"]["newly_scored_count"] == len(scorecards)
    assert diagnostic_round["scoring"]["top_pool_count"] == len(top_pool_snapshot)
    assert diagnostic_round["scoring"]["fit_count"] == len(scorecards)
    assert diagnostic_round["reflection"]["reflection_summary"] == "No reflection changes."
    assert diagnostic_round["controller_response_to_previous_reflection"] is None
    assert {"requirements", "controller", "scoring", "reflection", "finalize"} <= schema_pressure_stages
    assert all("output_retries" in item for item in search_diagnostics["llm_schema_pressure"])
    assert all("validator_retry_count" in item for item in search_diagnostics["llm_schema_pressure"])
    assert all("prompt_chars" in item for item in search_diagnostics["llm_schema_pressure"])
    assert all("input_payload_chars" in item for item in search_diagnostics["llm_schema_pressure"])
    assert all("output_chars" in item for item in search_diagnostics["llm_schema_pressure"])

    audit_terms = {item["term"]: item for item in term_surface_audit["terms"]}
    assert term_surface_audit["run_id"] == artifacts.run_id
    assert term_surface_audit["input"]["job_title"] == job_title
    assert term_surface_audit["summary"] == {
        "term_count": 3,
        "used_term_count": 2,
        "candidate_surface_rule_count": 0,
        "eval_enabled": True,
    }
    assert audit_terms["python"]["used_rounds"] == [1]
    assert audit_terms["python"]["sent_query_count"] == 1
    assert audit_terms["python"]["queries_containing_term_raw_candidate_count"] == search_observation["raw_candidate_count"]
    assert audit_terms["python"]["queries_containing_term_unique_new_count"] == search_observation["unique_new_count"]
    assert audit_terms["python"]["queries_containing_term_duplicate_count"] == sum(
        item["batch_duplicate_count"] for item in search_attempts
    )
    assert audit_terms["python"]["final_candidate_count_from_used_rounds"] == len(final_candidates["candidates"])
    assert audit_terms["python"]["judge_positive_count_from_used_rounds"] == 0
    assert audit_terms["python"]["human_label"] is None
    assert audit_terms["trace"]["used_rounds"] == []
    assert term_surface_audit["surfaces"] == []
    assert term_surface_audit["candidate_surface_rules"] == []

    assert "## Controller" in round_review
    assert "## Location Execution" in round_review
    assert "## City Dispatches" in round_review
    assert "Requested new candidates" in round_review
    assert "Unique new candidates" in round_review
    assert "Newly scored this round" in round_review
    assert "Current global top pool" in round_review
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
    assert run_config["settings"]["enable_eval"] is True
    assert run_config["settings"]["requirements_model"] == "openai-responses:gpt-5.4-mini"
    assert run_config["settings"]["controller_model"] == "openai-responses:gpt-5.4-mini"
    assert (artifacts.run_dir / "prompt_snapshots" / "requirements.md").exists()
    assert (artifacts.run_dir / "prompt_snapshots" / "controller.md").exists()
    assert (artifacts.run_dir / "prompt_snapshots" / "scoring.md").exists()
    assert (artifacts.run_dir / "prompt_snapshots" / "reflection.md").exists()
    assert (artifacts.run_dir / "prompt_snapshots" / "finalize.md").exists()
    assert (artifacts.run_dir / "prompt_snapshots" / "judge.md").exists()
    event_types = {item["event_type"] for item in events}
    assert "requirements_started" in event_types
    assert "requirements_completed" in event_types
    assert "controller_started" in event_types
    assert "controller_completed" in event_types
    assert "reflection_started" in event_types
    assert "reflection_completed" in event_types
    assert "finalizer_started" in event_types
    assert "finalizer_completed" in event_types
    assert "evaluation_completed" in event_types
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
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=2,
        enable_eval=True,
        cts_tenant_key="tenant-key",
        cts_tenant_secret="tenant-secret",
    )
    runtime = WorkflowRuntime(settings)
    _install_runtime_stubs(runtime, controller=StopOnSecondController(), resume_scorer=StubScorer())
    cast(Any, runtime).evaluation_runner = _stub_evaluation_runner

    artifacts = runtime.run(job_title="Senior Python Engineer", jd="JD", notes="Notes")

    run_summary = (artifacts.run_dir / "run_summary.md").read_text(encoding="utf-8")
    judge_packet = _read_json(artifacts.run_dir / "judge_packet.json")
    search_diagnostics = _read_json(artifacts.run_dir / "search_diagnostics.json")
    events = _read_jsonl(artifacts.run_dir / "events.jsonl")
    round_02_dir = artifacts.run_dir / "rounds" / "round_02"

    assert (round_02_dir / "controller_decision.json").exists()
    assert not (round_02_dir / "retrieval_plan.json").exists()
    assert judge_packet["run"]["rounds_executed"] == 1
    assert judge_packet["run"]["stop_decision_round"] == 2
    assert len(judge_packet["rounds"]) == 1
    assert judge_packet["terminal_controller_round"]["round_no"] == 2
    assert judge_packet["terminal_controller_round"]["controller_decision"]["action"] == "stop"
    assert judge_packet["terminal_controller_round"]["stop_guidance"]["can_stop"] is True
    assert search_diagnostics["summary"]["terminal_controller"]["round_no"] == 2
    assert search_diagnostics["summary"]["terminal_controller"]["response_to_reflection"]
    assert search_diagnostics["summary"]["terminal_controller"]["stop_guidance"]["can_stop"] is True
    assert "- Stop decision round: `2`" in run_summary
    assert "Terminal decision: The pool is stable enough for the stop-round audit fixture." in run_summary
    run_finished_event = next(item for item in events if item["event_type"] == "run_finished")
    assert run_finished_event["summary"] == "Run completed after 1 retrieval rounds; controller stopped in round 2."


def test_runtime_skips_eval_artifacts_when_eval_is_disabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=1,
        enable_eval=False,
        cts_tenant_key="tenant-key",
        cts_tenant_secret="tenant-secret",
    )
    runtime = WorkflowRuntime(settings)

    async def _unexpected_evaluation_runner(**kwargs):  # noqa: ANN003
        del kwargs
        raise AssertionError("evaluation runner should not be called when eval is disabled")

    cast(Any, runtime).evaluation_runner = _unexpected_evaluation_runner
    _install_runtime_stubs(runtime, controller=SurfaceController(), resume_scorer=StubScorer())
    cast(Any, runtime).requirement_extractor = SurfaceRequirementExtractor()

    artifacts = runtime.run(job_title="AI Agent Engineer", jd="Build MultiAgent 架构.", notes="Notes")

    events = _read_jsonl(artifacts.run_dir / "events.jsonl")
    run_summary = (artifacts.run_dir / "run_summary.md").read_text(encoding="utf-8")
    run_config = _read_json(artifacts.run_dir / "run_config.json")
    search_diagnostics = _read_json(artifacts.run_dir / "search_diagnostics.json")
    term_surface_audit = _read_json(artifacts.run_dir / "term_surface_audit.json")

    assert artifacts.evaluation_result is None
    assert not (artifacts.run_dir / "judge_packet.json").exists()
    assert not (artifacts.run_dir / "evaluation").exists()
    assert not (artifacts.run_dir / "raw_resumes").exists()
    assert search_diagnostics["summary"]["rounds_executed"] == 1
    assert search_diagnostics["summary"]["final_candidate_count"] > 0
    assert "Judge packet" not in run_summary
    assert "evaluation_completed" not in {item["event_type"] for item in events}
    assert "evaluation_skipped" in {item["event_type"] for item in events}
    finalizer_event = next(item for item in events if item["event_type"] == "finalizer_completed")
    assert "judge_packet.json" not in finalizer_event["artifact_paths"]
    assert run_config["settings"]["enable_eval"] is False
    audit_terms = {item["term"]: item for item in term_surface_audit["terms"]}
    audit_surfaces = {item["original_term"]: item for item in term_surface_audit["surfaces"]}
    assert term_surface_audit["summary"] == {
        "term_count": 2,
        "used_term_count": 2,
        "candidate_surface_rule_count": 2,
        "eval_enabled": False,
    }
    assert audit_terms["AI Agent"]["used_rounds"] == [1]
    assert audit_terms["AI Agent"]["judge_positive_count_from_used_rounds"] is None
    assert audit_surfaces["AI Agent"]["canonical_surface"] == "Agent"
    assert audit_surfaces["AI Agent"]["surface_transform"] == "candidate_alias_not_applied"
    assert audit_surfaces["AI Agent"]["used_in_query"] is True
    assert audit_surfaces["AI Agent"]["judge_positive_count"] is None
    assert audit_surfaces["MultiAgent 架构"]["canonical_surface"] == "MultiAgent"
    assert term_surface_audit["candidate_surface_rules"] == [
        {
            "from_original_term": "AI Agent",
            "to_retrieval_term": "Agent",
            "domain": "agent_llm",
            "applies_to": "retrieval_only",
            "status": "candidate",
            "evidence_status": "needs_surface_probe",
        },
        {
            "from_original_term": "MultiAgent 架构",
            "to_retrieval_term": "MultiAgent",
            "domain": "agent_llm",
            "applies_to": "retrieval_only",
            "status": "candidate",
            "evidence_status": "needs_surface_probe",
        },
    ]


def test_runtime_fails_fast_when_provider_credentials_are_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("seektalent.llm.load_process_env", lambda: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=1,
    )
    runtime = WorkflowRuntime(settings)

    try:
        runtime.run(job_title="Senior Python Engineer", jd="JD", notes="Notes")
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
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=1,
    )
    runtime = WorkflowRuntime(settings)
    _install_runtime_stubs(runtime, controller=StubController(), resume_scorer=FailingScorer())

    try:
        runtime.run(job_title="Senior Python Engineer", jd="JD", notes="Notes")
    except RuntimeError as exc:
        assert str(exc) == "Scoring failed for 1 resume(s): mock-r001."
    else:  # pragma: no cover
        raise AssertionError("Expected run() to fail after a final scoring failure")

    run_dirs = sorted((tmp_path / "runs").iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    assert not (run_dir / "final_candidates.json").exists()
    assert not (run_dir / "final_answer.md").exists()
