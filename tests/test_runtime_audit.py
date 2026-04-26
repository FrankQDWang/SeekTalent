import asyncio
import json
from pathlib import Path
from typing import Any, cast

from seektalent.core.retrieval.provider_contract import SearchResult
from seektalent.company_discovery.models import (
    CompanyEvidence,
    CompanyDiscoveryInput,
    CompanyDiscoveryResult,
    CompanySearchTask,
    PageReadResult,
    SearchRerankResult,
    TargetCompanyCandidate,
    TargetCompanyPlan,
    WebSearchResult,
)
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
from seektalent.progress import ProgressEvent
from seektalent.runtime import WorkflowRuntime
from seektalent.tracing import LLMCallSnapshot, ProviderUsageSnapshot, RunTracer, json_sha256, provider_usage_from_result
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


def _provider_usage_snapshot() -> ProviderUsageSnapshot:
    return ProviderUsageSnapshot(
        input_tokens=10,
        output_tokens=2,
        total_tokens=12,
        cache_read_tokens=7,
        cache_write_tokens=1,
        details={"reasoning_tokens": 3},
    )


def _aux_call_artifact(
    *,
    stage: str,
    prompt_name: str,
    model_id: str = "openai-chat:qwen3.5-flash",
    user_payload: dict[str, Any] | None = None,
    user_prompt_text: str = "stub prompt",
    output_payload: Any | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    return {
        "stage": stage,
        "prompt_name": prompt_name,
        "model_id": model_id,
        "user_payload": user_payload or {"stub": True},
        "user_prompt_text": user_prompt_text,
        "structured_output": output_payload,
        "started_at": "2026-01-01T00:00:00+00:00",
        "latency_ms": 5,
        "status": "failed" if error_message else "succeeded",
        "retries": 0,
        "output_retries": 2,
        "error_message": error_message,
        "provider_usage": _provider_usage_snapshot().model_dump(mode="json"),
    }


def _single_run_dir(runs_root: Path) -> Path:
    run_dirs = sorted(runs_root.iterdir())
    assert len(run_dirs) == 1
    return run_dirs[0]


def test_run_config_records_sanitized_rescue_settings(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        bocha_api_key="bocha-secret",
        candidate_feedback_enabled=True,
        candidate_feedback_model="openai-chat:qwen3.5-flash",
        candidate_feedback_reasoning_effort="off",
        target_company_enabled=False,
        company_discovery_enabled=True,
        company_discovery_model="openai-chat:qwen3.5-flash",
        company_discovery_reasoning_effort="off",
        company_discovery_max_search_calls=3,
        company_discovery_max_results_per_query=40,
        company_discovery_max_open_pages=6,
        company_discovery_timeout_seconds=18,
        company_discovery_accepted_company_limit=7,
        company_discovery_min_confidence=0.7,
    )
    runtime = WorkflowRuntime(settings)
    tracer = RunTracer(settings.runs_path)
    try:
        runtime._write_run_preamble(tracer=tracer, job_title="Agent Engineer", jd="JD", notes="Notes")
    finally:
        tracer.close()

    run_config = _read_json(tracer.run_dir / "run_config.json")
    serialized = json.dumps(run_config, ensure_ascii=False)

    assert "bocha_api_key" not in serialized
    assert "bocha-secret" not in serialized
    assert run_config["settings"]["candidate_feedback_enabled"] is True
    assert run_config["settings"]["candidate_feedback_model"] == "openai-chat:qwen3.5-flash"
    assert run_config["settings"]["candidate_feedback_reasoning_effort"] == "off"
    assert run_config["settings"]["target_company_enabled"] is False
    assert run_config["settings"]["company_discovery_enabled"] is True
    assert run_config["settings"]["company_discovery_provider"] == "bocha"
    assert run_config["settings"]["has_bocha_key"] is True
    assert run_config["settings"]["company_discovery_model"] == "openai-chat:qwen3.5-flash"
    assert run_config["settings"]["company_discovery_reasoning_effort"] == "off"
    assert run_config["settings"]["company_discovery_max_search_calls"] == 3
    assert run_config["settings"]["company_discovery_max_results_per_query"] == 40
    assert run_config["settings"]["company_discovery_max_open_pages"] == 6
    assert run_config["settings"]["company_discovery_timeout_seconds"] == 18
    assert run_config["settings"]["company_discovery_accepted_company_limit"] == 7
    assert run_config["settings"]["company_discovery_min_confidence"] == 0.7


def test_run_config_records_latency_engineering_settings(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        requirements_enable_thinking=False,
        controller_enable_thinking=False,
        reflection_enable_thinking=False,
        structured_repair_model="openai-chat:qwen3.5-repair",
        structured_repair_reasoning_effort="low",
        llm_cache_dir="tmp/latency-cache",
        openai_prompt_cache_enabled=True,
        openai_prompt_cache_retention="12h",
    )
    runtime = WorkflowRuntime(settings)

    run_config = runtime._build_public_run_config()
    run_settings = cast(dict[str, object], run_config["settings"])

    assert run_settings["requirements_enable_thinking"] is False
    assert run_settings["controller_enable_thinking"] is False
    assert run_settings["reflection_enable_thinking"] is False
    assert run_settings["structured_repair_model"] == "openai-chat:qwen3.5-repair"
    assert run_settings["structured_repair_reasoning_effort"] == "low"
    assert run_settings["runtime_mode"] == "dev"
    assert run_settings["runs_dir"] == str(tmp_path / "runs")
    assert run_settings["llm_cache_dir"] == "tmp/latency-cache"
    assert run_settings["openai_prompt_cache_enabled"] is True
    assert run_settings["openai_prompt_cache_retention"] == "12h"


def test_llm_call_snapshot_accepts_cache_repair_and_prompt_cache_metadata() -> None:
    snapshot = LLMCallSnapshot(
        stage="requirements",
        call_id="call-1",
        model_id="openai-chat:qwen3.5-flash",
        provider="openai-chat",
        prompt_hash="prompt-hash",
        prompt_snapshot_path="prompt_snapshots/requirements.md",
        retries=0,
        output_retries=0,
        started_at="2026-01-01T00:00:00+00:00",
        status="succeeded",
        input_payload_sha256="payload-hash",
        prompt_chars=120,
        input_payload_chars=30,
        output_chars=40,
        input_summary="input",
        provider_usage=ProviderUsageSnapshot(
            input_tokens=12,
            output_tokens=4,
            total_tokens=16,
            cache_read_tokens=11,
            cache_write_tokens=2,
            details={"reasoning_tokens": 7},
        ),
        cache_hit=True,
        cache_key="cache-key",
        cache_lookup_latency_ms=3,
        prompt_cache_key="prompt-cache-key",
        prompt_cache_retention="24h",
        cached_input_tokens=11,
        repair_attempt_count=2,
        repair_succeeded=True,
        repair_model="openai-chat:qwen3.5-repair",
        repair_reason="tooling",
        full_retry_count=1,
    )
    dump = snapshot.model_dump(mode="json")

    assert dump["cache_hit"] is True
    assert dump["cache_key"] == "cache-key"
    assert dump["cache_lookup_latency_ms"] == 3
    assert dump["prompt_cache_key"] == "prompt-cache-key"
    assert dump["prompt_cache_retention"] == "24h"
    assert dump["cached_input_tokens"] == 11
    assert dump["provider_usage"] == {
        "input_tokens": 12,
        "output_tokens": 4,
        "total_tokens": 16,
        "cache_read_tokens": 11,
        "cache_write_tokens": 2,
        "details": {"reasoning_tokens": 7},
    }
    assert dump["repair_attempt_count"] == 2
    assert dump["repair_succeeded"] is True
    assert dump["repair_model"] == "openai-chat:qwen3.5-repair"
    assert dump["repair_reason"] == "tooling"
    assert dump["full_retry_count"] == 1


def test_provider_usage_from_result_extracts_cache_tokens() -> None:
    class FakeUsage:
        def __init__(self) -> None:
            self.input_tokens = "12"
            self.output_tokens = 4.0
            self.total_tokens = 999
            self.cache_read_tokens = "8"
            self.cache_write_tokens = 3.0
            self.details = {"reasoning_tokens": 7, "ignored": "nope"}

    class FakeResult:
        def usage(self) -> FakeUsage:
            return FakeUsage()

    usage = provider_usage_from_result(FakeResult())
    assert usage is not None

    assert usage.model_dump(mode="json") == {
        "input_tokens": 12,
        "output_tokens": 4,
        "total_tokens": 16,
        "cache_read_tokens": 8,
        "cache_write_tokens": 3,
        "details": {"reasoning_tokens": 7},
    }


def test_provider_usage_from_result_returns_none_without_usage_method() -> None:
    class FakeResult:
        output = object()

    assert provider_usage_from_result(FakeResult()) is None


def test_runtime_snapshot_builder_accepts_reflection_cache_and_repair_metadata(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
    )
    runtime = WorkflowRuntime(settings)

    snapshot = runtime._build_llm_call_snapshot(
        stage="reflection",
        call_id="reflection-r01",
        model_id=settings.reflection_model,
        prompt_name="reflection",
        user_payload={
            "REFLECTION_CONTEXT": {
                "round_no": 1,
                "search_observation": {"unique_new_count": 2},
                "top_candidates": [],
            }
        },
        user_prompt_text="reflection prompt payload",
        input_artifact_refs=["rounds/round_01/reflection_context.json"],
        output_artifact_refs=["rounds/round_01/reflection_advice.json"],
        started_at="2026-01-01T00:00:00+00:00",
        latency_ms=10,
        status="succeeded",
        retries=0,
        output_retries=2,
        structured_output=ReflectionAdvice(
            keyword_advice=ReflectionKeywordAdvice(),
            filter_advice=ReflectionFilterAdvice(),
            reflection_rationale="Continue search.",
            suggest_stop=False,
            suggested_stop_reason=None,
            reflection_summary="Continue.",
        ).model_dump(mode="json"),
        round_no=1,
        validator_retry_count=1,
        validator_retry_reasons=["validator retry"],
        prompt_cache_key="reflection-cache-key",
        prompt_cache_retention="24h",
        provider_usage={"cache_read_tokens": 8},
        repair_attempt_count=1,
        repair_succeeded=True,
        repair_model="openai-chat:qwen3.5-repair",
        repair_reason="missing stop reason",
        full_retry_count=1,
    )
    dump = snapshot.model_dump(mode="json")

    assert dump["prompt_cache_key"] == "reflection-cache-key"
    assert dump["prompt_cache_retention"] == "24h"
    assert dump["provider_usage"]["cache_read_tokens"] == 8
    assert dump["cached_input_tokens"] == 8
    assert dump["repair_attempt_count"] == 1
    assert dump["repair_succeeded"] is True
    assert dump["repair_model"] == "openai-chat:qwen3.5-repair"
    assert dump["repair_reason"] == "missing stop reason"
    assert dump["full_retry_count"] == 1


def test_llm_schema_pressure_includes_cache_repair_and_full_retry() -> None:
    runtime = WorkflowRuntime(make_settings(runs_dir="/tmp/seek-runs"))

    pressure_item = runtime._llm_schema_pressure_item(
        {
            "stage": "requirements",
            "call_id": "requirements-r01",
            "output_retries": 1,
            "validator_retry_count": 2,
            "validator_retry_reasons": ["score mismatch", "tooling timeout"],
            "prompt_chars": 1200,
            "input_payload_chars": 3400,
            "output_chars": 560,
            "input_payload_sha256": "input-hash",
            "structured_output_sha256": "output-hash",
            "repair_attempt_count": 1,
            "repair_succeeded": True,
            "repair_reason": "repair by fallback",
            "full_retry_count": 3,
            "cache_hit": True,
            "cache_lookup_latency_ms": 8,
            "prompt_cache_key": "requirements:openai-chat:gpt:hash",
            "prompt_cache_retention": "24h",
            "cached_input_tokens": 17,
            "provider_usage": {"cache_read_tokens": 8},
        }
    )

    assert pressure_item["stage"] == "requirements"
    assert pressure_item["cache_hit"] is True
    assert pressure_item["cache_lookup_latency_ms"] == 8
    assert pressure_item["validator_retry_reasons"] == ["score mismatch", "tooling timeout"]
    assert pressure_item["repair_attempt_count"] == 1
    assert pressure_item["repair_succeeded"] is True
    assert pressure_item["repair_reason"] == "repair by fallback"
    assert pressure_item["full_retry_count"] == 3
    assert pressure_item["prompt_cache_key"] == "requirements:openai-chat:gpt:hash"
    assert pressure_item["prompt_cache_retention"] == "24h"
    assert pressure_item["cached_input_tokens"] == 17
    assert pressure_item["provider_usage"] == {"cache_read_tokens": 8}
def test_runtime_preflight_passes_rescue_models_from_top_level_settings(monkeypatch) -> None:
    captured_extra_specs: list[tuple[str, str | None, str | None]] | None = None

    def fake_preflight_models(settings, *, extra_model_specs=None):  # noqa: ANN001
        nonlocal captured_extra_specs
        del settings
        captured_extra_specs = extra_model_specs

    monkeypatch.setattr("seektalent.runtime.orchestrator.preflight_models", fake_preflight_models)
    settings = make_settings(
        candidate_feedback_enabled=True,
        candidate_feedback_model="openai-chat:qwen-feedback",
        company_discovery_enabled=True,
        bocha_api_key="bocha-key",
        company_discovery_model="openai-chat:qwen-discovery",
    )
    runtime = WorkflowRuntime(settings)

    runtime._require_live_llm_config()

    assert captured_extra_specs == [
        ("openai-chat:qwen-feedback", None, None),
        ("openai-chat:qwen-discovery", None, None),
    ]


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


class DuplicatePagingCTS:
    async def search(
        self,
        *,
        query_terms,
        query_role,
        provider_filters,
        runtime_constraints,
        page_size,
        round_no,
        trace_id,
        fetch_mode="summary",
        cursor=None,
    ) -> SearchResult:
        del query_terms, query_role, provider_filters, runtime_constraints, round_no, trace_id, fetch_mode
        page = int(cursor or "1")
        if page == 1:
            candidates = [_make_candidate("dup-1"), _make_candidate("dup-1")]
        elif page == 2:
            candidates = [_make_candidate("uniq-2")]
        else:
            candidates = []
        return SearchResult(
            candidates=candidates,
            diagnostics=[f"served page {page}"],
            request_payload={"page": page, "pageSize": page_size},
            raw_candidate_count=len(candidates),
            latency_ms=1,
        )


class StubController:
    last_validator_retry_count = 0
    last_validator_retry_reasons: list[str] = []

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
    last_validator_retry_reasons: list[str] = []

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
        self.last_validator_retry_reasons: list[str] = []

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


class SearchTwiceController:
    def __init__(self) -> None:
        self.calls = 0
        self.last_validator_retry_count = 0
        self.last_validator_retry_reasons: list[str] = []

    async def decide(self, *, context):
        del context
        self.calls += 1
        response_to_reflection = None
        proposed_filter_plan = ProposedFilterPlan()
        if self.calls == 2:
            response_to_reflection = "Accepted previous reflection filter guidance."
            proposed_filter_plan = ProposedFilterPlan(
                added_filter_fields=["position", "work_content"],
                dropped_filter_fields=["company_names"],
            )
        return SearchControllerDecision(
            thought_summary="Continue retrieval with the current requirement truth.",
            action="search_cts",
            decision_rationale="Need another retrieval round for the audit fixture.",
            proposed_query_terms=["python", "resume matching"],
            proposed_filter_plan=proposed_filter_plan,
            response_to_reflection=response_to_reflection,
        )


class StubRequirementExtractor:
    async def extract_with_draft(self, *, input_truth) -> tuple[RequirementExtractionDraft, RequirementSheet]:
        del input_truth
        draft = RequirementExtractionDraft(
            role_title="Senior Python Engineer",
            title_anchor_terms=["python"],
            title_anchor_rationale="Python is the stable searchable anchor from the title.",
            jd_query_terms=["resume matching", "trace"],
            role_summary="Build resume matching workflows.",
            must_have_capabilities=["python", "resume matching"],
            locations=["上海"],
            preferred_query_terms=["python", "resume matching"],
            scoring_rationale="Score Python fit first.",
        )
        return draft, RequirementSheet(
            role_title="Senior Python Engineer",
            title_anchor_terms=["python"],
            title_anchor_rationale="Python is the stable searchable anchor from the title.",
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
                    retrieval_role="primary_role_anchor",
                    queryability="admitted",
                    family="role.python",
                ),
                QueryTermCandidate(
                    term="resume matching",
                    source="jd",
                    category="domain",
                    priority=2,
                    evidence="JD body",
                    first_added_round=0,
                    retrieval_role="core_skill",
                    queryability="admitted",
                    family="skill.resume_matching",
                ),
                QueryTermCandidate(
                    term="trace",
                    source="jd",
                    category="tooling",
                    priority=3,
                    evidence="JD body",
                    first_added_round=0,
                    retrieval_role="framework_tool",
                    queryability="admitted",
                    family="skill.trace",
                ),
            ],
            scoring_rationale="Score Python fit first.",
        )


class SurfaceRequirementExtractor:
    async def extract_with_draft(self, *, input_truth) -> tuple[RequirementExtractionDraft, RequirementSheet]:
        del input_truth
        draft = RequirementExtractionDraft(
            role_title="AI Agent Engineer",
            title_anchor_terms=["AI Agent", "Agent Engineer"],
            title_anchor_rationale="AI Agent is the fixed title direction and Agent Engineer is the closest alternate resume-side title.",
            jd_query_terms=["MultiAgent 架构"],
            role_summary="Build agent applications.",
            must_have_capabilities=["AI Agent", "MultiAgent 架构"],
            locations=["上海"],
            preferred_query_terms=["AI Agent", "MultiAgent 架构"],
            scoring_rationale="Score agent fit first.",
        )
        return draft, RequirementSheet(
            role_title="AI Agent Engineer",
            title_anchor_terms=["AI Agent", "Agent Engineer"],
            title_anchor_rationale="AI Agent is the fixed title direction and Agent Engineer is the closest alternate resume-side title.",
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
                    retrieval_role="primary_role_anchor",
                    queryability="admitted",
                    family="role.agent",
                ),
                QueryTermCandidate(
                    term="Agent Engineer",
                    source="job_title",
                    category="role_anchor",
                    priority=2,
                    evidence="Job title alternate title",
                    first_added_round=0,
                    retrieval_role="secondary_title_anchor",
                    queryability="admitted",
                    family="role.agent_engineer",
                ),
                QueryTermCandidate(
                    term="MultiAgent 架构",
                    source="jd",
                    category="domain",
                    priority=3,
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
            keyword_advice=ReflectionKeywordAdvice(
                suggested_activate_terms=["python"],
                suggested_keep_terms=["django"],
                suggested_deprioritize_terms=["legacy systems", "python"],
                suggested_drop_terms=["perl", "resume matching"],
            ),
            filter_advice=ReflectionFilterAdvice(
                suggested_keep_filter_fields=["position"],
                suggested_drop_filter_fields=["company_names", "degree_requirement"],
                suggested_add_filter_fields=["work_content", "school_names"],
            ),
            suggest_stop=False,
            suggested_stop_reason=None,
            reflection_summary="No reflection changes.",
        )


class StubResumeQualityCommenter:
    async def comment(self, **kwargs) -> str:
        assert kwargs["round_no"] == 1
        assert kwargs["query_terms"] == ["python", "resume matching"]
        assert kwargs["candidates"]
        return "本轮简历整体质量较好，Python 和检索经验集中，少数候选人管理经验仍需复核。"


class AuditResumeQualityCommenter:
    def __init__(self) -> None:
        self.last_call_artifact: dict[str, Any] | None = None

    async def comment(self, **kwargs) -> str:
        comment = "本轮简历整体质量较好，Python 和检索经验集中，少数候选人管理经验仍需复核。"
        self.last_call_artifact = _aux_call_artifact(
            stage="tui_summary",
            prompt_name="tui_summary",
            model_id="openai-responses:gpt-5.4-mini",
            user_payload={
                "ROUND_RESUME_QUALITY_CONTEXT": {
                    "round_no": kwargs["round_no"],
                    "query_terms": kwargs["query_terms"],
                    "candidate_count": len(kwargs["candidates"]),
                }
            },
            user_prompt_text="ROUND_RESUME_QUALITY_CONTEXT",
            output_payload={"comment": comment},
        )
        return comment


class FailingResumeQualityCommenter:
    async def comment(self, **kwargs) -> str:
        del kwargs
        raise RuntimeError("quality comment failed")


class StubFinalizer:
    last_validator_retry_count = 0
    last_validator_retry_reasons: list[str] = []

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


class RepairAwareRequirementExtractor(StubRequirementExtractor):
    def __init__(self) -> None:
        self.last_provider_usage = _provider_usage_snapshot()
        self.last_repair_attempt_count = 1
        self.last_repair_succeeded = True
        self.last_repair_reason = "missing title_anchor_term"
        self.last_full_retry_count = 0
        self.last_repair_call_artifact = _aux_call_artifact(
            stage="repair_requirements",
            prompt_name="repair_requirements",
            user_payload={"REPAIR_REASON": {"reason": "missing title_anchor_term"}},
            user_prompt_text="repair requirements prompt",
            output_payload={"role_title": "Senior Python Engineer"},
        )


class RepairAwareController(StubController):
    def __init__(self) -> None:
        self.last_validator_retry_count = 1
        self.last_validator_retry_reasons = ["response_to_reflection is required"]
        self.last_provider_usage = _provider_usage_snapshot()
        self.last_repair_attempt_count = 1
        self.last_repair_succeeded = True
        self.last_repair_reason = "response_to_reflection is required"
        self.last_full_retry_count = 0
        self.last_repair_call_artifact = _aux_call_artifact(
            stage="repair_controller",
            prompt_name="repair_controller",
            user_payload={"REPAIR_REASON": {"reason": "response_to_reflection is required"}},
            user_prompt_text="repair controller prompt",
            output_payload={"action": "search_cts", "proposed_query_terms": ["python"]},
        )


class RepairAwareReflection(StubReflection):
    def __init__(self) -> None:
        self.last_validator_retry_count = 1
        self.last_validator_retry_reasons = ["suggested_stop_reason is required"]
        self.last_provider_usage = _provider_usage_snapshot()
        self.last_repair_attempt_count = 1
        self.last_repair_succeeded = True
        self.last_repair_reason = "suggested_stop_reason is required"
        self.last_full_retry_count = 0
        self.last_repair_call_artifact = _aux_call_artifact(
            stage="repair_reflection",
            prompt_name="repair_reflection",
            user_payload={"REPAIR_REASON": {"reason": "suggested_stop_reason is required"}},
            user_prompt_text="repair reflection prompt",
            output_payload={"suggest_stop": False, "reflection_summary": "No reflection changes."},
        )


class StubCompanyDiscoveryService:
    def __init__(self) -> None:
        candidate = TargetCompanyCandidate(
            name="Example Robotics",
            aliases=["Example"],
            source="web_inferred",
            intent="target",
            confidence=0.91,
            fit_axes=["industry"],
            search_usage="keyword_term",
            evidence=[
                CompanyEvidence(
                    title="Example Robotics hiring",
                    url="https://example.com/jobs",
                    snippet="Hiring AI engineers.",
                    source_type="web",
                )
            ],
            rationale="Direct role overlap.",
        )
        self.last_call_artifacts = [
            _aux_call_artifact(
                stage="company_discovery_plan",
                prompt_name="company_discovery_plan",
                user_payload={"DISCOVERY_INPUT": {"role_title": "Senior Python Engineer"}},
                user_prompt_text="plan discovery prompt",
                output_payload={"tasks": [{"query": "example robotics ai engineer"}]},
            ),
            _aux_call_artifact(
                stage="company_discovery_extract",
                prompt_name="company_discovery_extract",
                user_payload={"PAGE_COUNT": 1, "SEARCH_RESULT_COUNT": 1},
                user_prompt_text="extract company evidence prompt",
                output_payload={"candidates": [candidate.model_dump(mode="json")]},
            ),
            _aux_call_artifact(
                stage="company_discovery_reduce",
                prompt_name="company_discovery_reduce",
                user_payload={"CANDIDATE_COUNT": 1, "STOP_REASON": "completed"},
                user_prompt_text="reduce company plan prompt",
                output_payload={"inferred_targets": [candidate.model_dump(mode="json")]},
            ),
        ]
        self._result = CompanyDiscoveryResult(
            plan=TargetCompanyPlan(
                inferred_targets=[candidate],
                web_discovery_attempted=True,
                stop_reason="completed",
            ),
            discovery_input=CompanyDiscoveryInput(
                role_title="Senior Python Engineer",
                title_anchor_term="python",
                must_have_capabilities=["python"],
                preferred_domains=[],
                preferred_backgrounds=[],
                locations=["上海"],
                exclusions=[],
            ),
            search_tasks=[
                CompanySearchTask(
                    query_id="q1",
                    query="example robotics ai engineer",
                    intent="role_evidence",
                    rationale="Find peer companies.",
                )
            ],
            search_results=[
                WebSearchResult(
                    rank=1,
                    title="Example Robotics hiring",
                    url="https://example.com/jobs",
                    site_name="Example",
                    snippet="Hiring AI engineers.",
                    summary="",
                    published_at=None,
                )
            ],
            reranked_results=[
                SearchRerankResult(
                    rank=1,
                    source_index=0,
                    score=0.95,
                    title="Example Robotics hiring",
                    url="https://example.com/jobs",
                )
            ],
            page_reads=[
                PageReadResult(
                    url="https://example.com/jobs",
                    title="Jobs",
                    text="Senior AI engineer role.",
                )
            ],
            evidence_candidates=[candidate],
            search_result_count=1,
            opened_page_count=1,
            trigger_reason="shortage",
        )

    async def discover_web(self, *, requirement_sheet, round_no: int, trigger_reason: str) -> CompanyDiscoveryResult:  # noqa: ANN001
        del requirement_sheet, round_no, trigger_reason
        return self._result


class FailingCompanyDiscoveryService:
    def __init__(self) -> None:
        self.last_call_artifacts = [
            _aux_call_artifact(
                stage="company_discovery_plan",
                prompt_name="company_discovery_plan",
                user_payload={"DISCOVERY_INPUT": {"role_title": "Senior Python Engineer"}},
                user_prompt_text="plan discovery prompt",
                error_message="company discovery planning failed",
            )
        ]

    async def discover_web(self, *, requirement_sheet, round_no: int, trigger_reason: str) -> CompanyDiscoveryResult:  # noqa: ANN001
        del requirement_sheet, round_no, trigger_reason
        raise RuntimeError("company discovery planning failed")


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
    runtime.retrieval_service = DuplicatePagingCTS()
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
        openai_prompt_cache_enabled=True,
        openai_prompt_cache_retention="12h",
        cts_tenant_key="tenant-key",
        cts_tenant_secret="tenant-secret",
    )
    runtime = WorkflowRuntime(settings)
    _install_runtime_stubs(runtime, controller=StubController(), resume_scorer=StubScorer())
    provider_usage = ProviderUsageSnapshot(
        input_tokens=10,
        output_tokens=2,
        total_tokens=12,
        cache_read_tokens=7,
        cache_write_tokens=1,
        details={"reasoning_tokens": 3},
    )
    runtime_any = cast(Any, runtime)
    runtime_any.requirement_extractor.last_provider_usage = provider_usage
    runtime_any.controller.last_provider_usage = provider_usage
    runtime_any.reflection_critic.last_provider_usage = provider_usage
    runtime_any.finalizer.last_provider_usage = provider_usage
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
    requirement_sheet = _read_json(artifacts.run_dir / "requirement_sheet.json")
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
    assert reflection_context["query_term_pool"][0]["term"] == "python"
    assert "full_jd" not in reflection_context
    assert "full_notes" not in reflection_context
    assert all("evidence" not in item for item in reflection_context["top_candidates"])
    assert "top_candidates" in finalizer_context
    assert all("evidence" not in item for item in finalizer_context["top_candidates"])
    assert final_candidates["summary"]
    assert all(candidate["match_summary"] for candidate in final_candidates["candidates"])
    assert "user_payload" not in requirements_call
    assert "structured_output" not in requirements_call
    assert requirements_call["provider_usage"] == provider_usage.model_dump(mode="json")
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
    assert controller_call["validator_retry_reasons"] == []
    assert controller_call["prompt_cache_key"] == (
        f"controller:{settings.controller_model}:{json_sha256(requirement_sheet)}"
    )
    assert controller_call["prompt_cache_retention"] == "12h"
    assert controller_call["provider_usage"] == provider_usage.model_dump(mode="json")
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
    assert reflection_call["validator_retry_count"] == 0
    assert reflection_call["validator_retry_reasons"] == []
    assert reflection_call["prompt_cache_key"] is not None
    assert reflection_call["prompt_cache_key"].startswith(f"reflection:{settings.reflection_model}:")
    assert reflection_call["prompt_cache_key"] != (
        f"reflection:{settings.reflection_model}:{json_sha256(requirement_sheet)}"
    )
    assert reflection_call["prompt_cache_retention"] == "12h"
    assert reflection_call["provider_usage"] == provider_usage.model_dump(mode="json")
    assert reflection_call["repair_attempt_count"] == 0
    assert reflection_call["repair_succeeded"] is False
    assert reflection_call["repair_model"] is None
    assert reflection_call["repair_reason"] is None
    assert reflection_call["full_retry_count"] == 0
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
    assert finalizer_call["validator_retry_reasons"] == []
    assert finalizer_call["provider_usage"] == provider_usage.model_dump(mode="json")
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
    assert diagnostic_round["failure_labels"] == []
    assert diagnostic_round["audit_labels"] == []
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
    assert "Reflection decision" in round_review
    assert "Strategy assessment" not in round_review
    assert "Quality assessment" not in round_review
    assert "Coverage assessment" not in round_review
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
    assert run_config["settings"]["controller_enable_thinking"] is True
    assert run_config["settings"]["reflection_enable_thinking"] is True
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


def test_runtime_emits_tui_progress_events(tmp_path: Path, monkeypatch) -> None:
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
    _install_runtime_stubs(runtime, controller=StubController(), resume_scorer=StubScorer())
    job_title, jd, notes = _sample_inputs()
    progress_events: list[ProgressEvent] = []

    runtime.run(job_title=job_title, jd=jd, notes=notes, progress_callback=progress_events.append)

    event_types = [event.type for event in progress_events]
    assert "requirements_started" in event_types
    assert "controller_started" in event_types
    assert "search_completed" in event_types
    assert "scoring_completed" in event_types
    assert "reflection_completed" in event_types
    assert "round_completed" in event_types
    assert "finalizer_started" in event_types
    assert "run_completed" in event_types

    round_event = next(event for event in progress_events if event.type == "round_completed")
    assert round_event.round_no == 1
    assert round_event.payload["query_terms"] == ["python", "resume matching"]
    assert round_event.payload["executed_queries"] == [
        {
            "query_role": "exploit",
            "query_terms": ["python", "resume matching"],
            "keyword_query": "python \"resume matching\"",
        }
    ]
    assert round_event.payload["unique_new_count"] > 0
    assert round_event.payload["newly_scored_count"] > 0
    assert round_event.payload["fit_count"] == round_event.payload["newly_scored_count"]
    assert round_event.payload["not_fit_count"] == 0
    assert round_event.payload["top_pool_selected_count"] > 0
    assert round_event.payload["representative_candidates"]
    assert round_event.payload["reflection_summary"] == "No reflection changes."


def test_runtime_round_payload_includes_resume_quality_comment(tmp_path: Path, monkeypatch) -> None:
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
    _install_runtime_stubs(runtime, controller=StubController(), resume_scorer=StubScorer())
    cast(Any, runtime).resume_quality_commenter = StubResumeQualityCommenter()
    progress_events: list[ProgressEvent] = []

    runtime.run(job_title="Senior Python Engineer", jd="JD", notes="Notes", progress_callback=progress_events.append)

    round_event = next(event for event in progress_events if event.type == "round_completed")
    quality_event = next(event for event in progress_events if event.type == "resume_quality_comment_completed")
    event_types = [event.type for event in progress_events]
    assert round_event.payload["resume_quality_comment"] == "本轮简历整体质量较好，Python 和检索经验集中，少数候选人管理经验仍需复核。"
    assert round_event.payload["resume_quality_comment_error"] is None
    assert round_event.payload["reflection_summary"] == "No reflection changes."
    assert quality_event.message == "本轮简历质量：本轮简历整体质量较好，Python 和检索经验集中，少数候选人管理经验仍需复核。"
    assert event_types.index("scoring_completed") < event_types.index("resume_quality_comment_completed")
    assert event_types.index("resume_quality_comment_completed") < event_types.index("reflection_started")


def test_runtime_writes_tui_summary_call_artifact_and_aux_prompt_snapshots(tmp_path: Path, monkeypatch) -> None:
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
    _install_runtime_stubs(runtime, controller=StubController(), resume_scorer=StubScorer())
    cast(Any, runtime).resume_quality_commenter = AuditResumeQualityCommenter()

    artifacts = runtime.run(job_title="Senior Python Engineer", jd="JD", notes="Notes")

    run_config = _read_json(artifacts.run_dir / "run_config.json")
    tui_summary_call = _read_json(artifacts.run_dir / "rounds" / "round_01" / "tui_summary_call.json")

    assert "tui_summary" in run_config["prompt_hashes"]
    assert "company_discovery_plan" in run_config["prompt_hashes"]
    assert "company_discovery_extract" in run_config["prompt_hashes"]
    assert "company_discovery_reduce" in run_config["prompt_hashes"]
    assert "repair_requirements" in run_config["prompt_hashes"]
    assert "repair_controller" in run_config["prompt_hashes"]
    assert "repair_reflection" in run_config["prompt_hashes"]
    assert (artifacts.run_dir / "prompt_snapshots" / "tui_summary.md").exists()
    assert (artifacts.run_dir / "prompt_snapshots" / "company_discovery_plan.md").exists()
    assert (artifacts.run_dir / "prompt_snapshots" / "company_discovery_extract.md").exists()
    assert (artifacts.run_dir / "prompt_snapshots" / "company_discovery_reduce.md").exists()
    assert (artifacts.run_dir / "prompt_snapshots" / "repair_requirements.md").exists()
    assert (artifacts.run_dir / "prompt_snapshots" / "repair_controller.md").exists()
    assert (artifacts.run_dir / "prompt_snapshots" / "repair_reflection.md").exists()
    assert tui_summary_call["stage"] == "tui_summary"
    assert tui_summary_call["prompt_hash"] == run_config["prompt_hashes"]["tui_summary"]
    assert tui_summary_call["prompt_snapshot_path"] == "prompt_snapshots/tui_summary.md"
    assert "rounds/round_01/tui_summary.json" in tui_summary_call["output_artifact_refs"]


def test_runtime_resume_quality_comment_failure_does_not_block_reflection(tmp_path: Path, monkeypatch) -> None:
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
    _install_runtime_stubs(runtime, controller=StubController(), resume_scorer=StubScorer())
    cast(Any, runtime).resume_quality_commenter = FailingResumeQualityCommenter()
    progress_events: list[ProgressEvent] = []

    runtime.run(job_title="Senior Python Engineer", jd="JD", notes="Notes", progress_callback=progress_events.append)

    event_types = [event.type for event in progress_events]
    round_event = next(event for event in progress_events if event.type == "round_completed")
    quality_event = next(event for event in progress_events if event.type == "resume_quality_comment_failed")
    assert "reflection_completed" in event_types
    assert round_event.payload["resume_quality_comment"] is None
    assert round_event.payload["resume_quality_comment_error"] == "quality comment failed"
    assert round_event.payload["reflection_summary"] == "No reflection changes."
    assert quality_event.message == "本轮简历质量短评生成失败，已继续 reflection。"
    assert event_types.index("scoring_completed") < event_types.index("resume_quality_comment_failed")
    assert event_types.index("resume_quality_comment_failed") < event_types.index("reflection_started")


def test_runtime_writes_repair_call_artifacts(tmp_path: Path, monkeypatch) -> None:
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
    _install_runtime_stubs(runtime, controller=RepairAwareController(), resume_scorer=StubScorer())
    runtime_any = cast(Any, runtime)
    runtime_any.requirement_extractor = RepairAwareRequirementExtractor()
    runtime_any.reflection_critic = RepairAwareReflection()

    artifacts = runtime.run(job_title="Senior Python Engineer", jd="JD", notes="Notes")

    run_config = _read_json(artifacts.run_dir / "run_config.json")
    repair_requirements_call = _read_json(artifacts.run_dir / "repair_requirements_call.json")
    repair_controller_call = _read_json(artifacts.run_dir / "rounds" / "round_01" / "repair_controller_call.json")
    repair_reflection_call = _read_json(artifacts.run_dir / "rounds" / "round_01" / "repair_reflection_call.json")

    assert repair_requirements_call["prompt_hash"] == run_config["prompt_hashes"]["repair_requirements"]
    assert repair_controller_call["prompt_hash"] == run_config["prompt_hashes"]["repair_controller"]
    assert repair_reflection_call["prompt_hash"] == run_config["prompt_hashes"]["repair_reflection"]
    assert repair_requirements_call["prompt_snapshot_path"] == "prompt_snapshots/repair_requirements.md"
    assert repair_controller_call["prompt_snapshot_path"] == "prompt_snapshots/repair_controller.md"
    assert repair_reflection_call["prompt_snapshot_path"] == "prompt_snapshots/repair_reflection.md"


def test_force_company_discovery_writes_model_call_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        company_discovery_enabled=True,
        bocha_api_key="bocha-key",
    )
    runtime = WorkflowRuntime(settings)
    runtime_any = cast(Any, runtime)
    runtime_any.requirement_extractor = StubRequirementExtractor()
    runtime_any.company_discovery = StubCompanyDiscoveryService()
    tracer = RunTracer(settings.runs_path)
    job_title, jd, notes = _sample_inputs()
    runtime._write_run_preamble(tracer=tracer, job_title=job_title, jd=jd, notes=notes)
    try:
        run_state = asyncio.run(
            runtime._build_run_state(
                job_title=job_title,
                jd=jd,
                notes=notes,
                tracer=tracer,
            )
        )
        decision = asyncio.run(
            runtime._force_company_discovery_decision(
                run_state=run_state,
                round_no=1,
                reason="shortage",
                tracer=tracer,
                progress_callback=None,
            )
        )
    finally:
        tracer.close()

    run_dir = _single_run_dir(settings.runs_path)
    plan_call = _read_json(run_dir / "rounds" / "round_01" / "company_discovery_plan_call.json")
    extract_call = _read_json(run_dir / "rounds" / "round_01" / "company_discovery_extract_call.json")
    reduce_call = _read_json(run_dir / "rounds" / "round_01" / "company_discovery_reduce_call.json")

    assert decision is not None
    assert plan_call["stage"] == "company_discovery_plan"
    assert extract_call["stage"] == "company_discovery_extract"
    assert reduce_call["stage"] == "company_discovery_reduce"
    assert "rounds/round_01/company_search_queries.json" in plan_call["output_artifact_refs"]
    assert "rounds/round_01/company_evidence_cards.json" in extract_call["output_artifact_refs"]
    assert "rounds/round_01/company_discovery_plan.json" in reduce_call["output_artifact_refs"]


def test_force_company_discovery_writes_failed_model_call_artifact(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        company_discovery_enabled=True,
        bocha_api_key="bocha-key",
    )
    runtime = WorkflowRuntime(settings)
    runtime_any = cast(Any, runtime)
    runtime_any.requirement_extractor = StubRequirementExtractor()
    runtime_any.company_discovery = FailingCompanyDiscoveryService()
    tracer = RunTracer(settings.runs_path)
    job_title, jd, notes = _sample_inputs()
    runtime._write_run_preamble(tracer=tracer, job_title=job_title, jd=jd, notes=notes)
    try:
        run_state = asyncio.run(
            runtime._build_run_state(
                job_title=job_title,
                jd=jd,
                notes=notes,
                tracer=tracer,
            )
        )
        try:
            asyncio.run(
                runtime._force_company_discovery_decision(
                    run_state=run_state,
                    round_no=1,
                    reason="shortage",
                    tracer=tracer,
                    progress_callback=None,
                )
            )
        except RuntimeError as exc:
            assert str(exc) == "company discovery planning failed"
        else:  # pragma: no cover
            raise AssertionError("Expected company discovery failure")
    finally:
        tracer.close()

    run_dir = _single_run_dir(settings.runs_path)
    plan_call = _read_json(run_dir / "rounds" / "round_01" / "company_discovery_plan_call.json")

    assert plan_call["stage"] == "company_discovery_plan"
    assert plan_call["status"] == "failed"
    assert plan_call["error_message"] == "company discovery planning failed"
    assert plan_call["output_artifact_refs"] == []


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
    terminal_adoption = search_diagnostics["summary"]["terminal_controller"]["reflection_advice_application"]
    assert terminal_adoption["controller_response"] == "Accepted the reflection recommendation to stop."
    assert terminal_adoption["accepted_deprioritize_terms"] == ["legacy systems", "python"]
    assert terminal_adoption["accepted_drop_terms"] == ["perl", "resume matching"]
    assert terminal_adoption["ignored_keep_filter_fields"] == ["position"]
    assert "- Stop decision round: `2`" in run_summary
    assert "Terminal decision: The pool is stable enough for the stop-round audit fixture." in run_summary
    run_finished_event = next(item for item in events if item["event_type"] == "run_finished")
    assert run_finished_event["summary"] == "Run completed after 1 retrieval rounds; controller stopped in round 2."


def test_runtime_search_diagnostics_records_reflection_advice_application(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        mock_cts=True,
        min_rounds=2,
        max_rounds=2,
        enable_eval=False,
        cts_tenant_key="tenant-key",
        cts_tenant_secret="tenant-secret",
    )
    runtime = WorkflowRuntime(settings)
    _install_runtime_stubs(runtime, controller=SearchTwiceController(), resume_scorer=StubScorer())

    artifacts = runtime.run(job_title="Senior Python Engineer", jd="JD", notes="Notes")

    search_diagnostics = _read_json(artifacts.run_dir / "search_diagnostics.json")
    diagnostic_round = search_diagnostics["rounds"][1]
    adoption = diagnostic_round["reflection_advice_application"]
    assert adoption["controller_response"] == "Accepted previous reflection filter guidance."
    assert adoption["suggested_activate_terms"] == ["python"]
    assert adoption["suggested_keep_terms"] == ["django"]
    assert adoption["suggested_deprioritize_terms"] == ["legacy systems", "python"]
    assert adoption["suggested_drop_terms"] == ["perl", "resume matching"]
    assert adoption["accepted_activate_terms"] == ["python"]
    assert adoption["ignored_activate_terms"] == []
    assert adoption["accepted_keep_terms"] == []
    assert adoption["ignored_keep_terms"] == ["django"]
    assert adoption["accepted_deprioritize_terms"] == ["legacy systems"]
    assert adoption["ignored_deprioritize_terms"] == ["python"]
    assert adoption["accepted_drop_terms"] == ["perl"]
    assert adoption["ignored_drop_terms"] == ["resume matching"]
    assert adoption["accepted_terms"] == ["python"]
    assert adoption["ignored_terms"] == ["django"]
    assert adoption["suggested_filter_fields"] == ["position", "company_names", "degree_requirement", "work_content", "school_names"]
    assert adoption["accepted_keep_filter_fields"] == ["position"]
    assert adoption["ignored_keep_filter_fields"] == []
    assert adoption["accepted_add_filter_fields"] == ["work_content"]
    assert adoption["ignored_add_filter_fields"] == ["school_names"]
    assert adoption["accepted_drop_filter_fields"] == ["company_names"]
    assert adoption["ignored_drop_filter_fields"] == ["degree_requirement"]
    assert adoption["accepted_filter_fields"] == ["position", "work_content", "company_names"]
    assert adoption["ignored_filter_fields"] == ["school_names", "degree_requirement"]


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
        "term_count": 3,
        "used_term_count": 2,
        "candidate_surface_rule_count": 2,
        "eval_enabled": False,
    }
    assert audit_terms["AI Agent"]["used_rounds"] == [1]
    assert audit_terms["Agent Engineer"]["used_rounds"] == []
    assert audit_terms["AI Agent"]["judge_positive_count_from_used_rounds"] is None
    assert search_diagnostics["rounds"][0]["failure_labels"] == ["title_multi_anchor_collapsed"]
    assert search_diagnostics["rounds"][0]["audit_labels"] == ["title_multi_anchor_collapsed"]
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


def test_requirements_failure_snapshot_records_provider_usage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    provider_usage = _provider_usage_snapshot()
    settings = make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True)
    runtime = WorkflowRuntime(settings)

    class FailingRequirementExtractor:
        last_provider_usage: ProviderUsageSnapshot | None = None

        async def extract_with_draft(self, *, input_truth):  # noqa: ANN001
            del input_truth
            self.last_provider_usage = provider_usage
            raise RuntimeError("requirements failed")

    cast(Any, runtime).requirement_extractor = FailingRequirementExtractor()

    try:
        runtime.run(job_title="Senior Python Engineer", jd="JD", notes="Notes")
    except RuntimeError as exc:
        assert str(exc) == "requirements failed"
    else:  # pragma: no cover
        raise AssertionError("Expected requirements failure")

    requirements_call = _read_json(_single_run_dir(settings.runs_path) / "requirements_call.json")
    assert requirements_call["status"] == "failed"
    assert requirements_call["provider_usage"] == provider_usage.model_dump(mode="json")


def test_controller_failure_snapshot_records_provider_usage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    provider_usage = _provider_usage_snapshot()
    settings = make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, min_rounds=1, max_rounds=1)
    runtime = WorkflowRuntime(settings)

    class FailingController:
        last_validator_retry_count = 0
        last_validator_retry_reasons: list[str] = []
        last_provider_usage: ProviderUsageSnapshot | None = None

        async def decide(self, *, context):  # noqa: ANN001
            del context
            self.last_provider_usage = provider_usage
            raise RuntimeError("controller failed")

    _install_runtime_stubs(runtime, controller=FailingController(), resume_scorer=StubScorer())

    try:
        runtime.run(job_title="Senior Python Engineer", jd="JD", notes="Notes")
    except RuntimeError as exc:
        assert str(exc) == "controller failed"
    else:  # pragma: no cover
        raise AssertionError("Expected controller failure")

    controller_call = _read_json(_single_run_dir(settings.runs_path) / "rounds" / "round_01" / "controller_call.json")
    assert controller_call["status"] == "failed"
    assert controller_call["provider_usage"] == provider_usage.model_dump(mode="json")


def test_reflection_failure_snapshot_records_provider_usage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    provider_usage = _provider_usage_snapshot()
    settings = make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, min_rounds=1, max_rounds=1)
    runtime = WorkflowRuntime(settings)
    _install_runtime_stubs(runtime, controller=StubController(), resume_scorer=StubScorer())

    class FailingReflection:
        last_provider_usage: ProviderUsageSnapshot | None = None

        async def reflect(self, *, context):  # noqa: ANN001
            del context
            self.last_provider_usage = provider_usage
            raise RuntimeError("reflection failed")

    cast(Any, runtime).reflection_critic = FailingReflection()

    try:
        runtime.run(job_title="Senior Python Engineer", jd="JD", notes="Notes")
    except RuntimeError as exc:
        assert str(exc) == "reflection failed"
    else:  # pragma: no cover
        raise AssertionError("Expected reflection failure")

    reflection_call = _read_json(_single_run_dir(settings.runs_path) / "rounds" / "round_01" / "reflection_call.json")
    assert reflection_call["status"] == "failed"
    assert reflection_call["provider_usage"] == provider_usage.model_dump(mode="json")


def test_finalizer_failure_snapshot_records_provider_usage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    provider_usage = _provider_usage_snapshot()
    settings = make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, min_rounds=1, max_rounds=1)
    runtime = WorkflowRuntime(settings)
    _install_runtime_stubs(runtime, controller=StubController(), resume_scorer=StubScorer())

    class FailingFinalizer:
        last_validator_retry_count = 0
        last_validator_retry_reasons: list[str] = []
        last_provider_usage: ProviderUsageSnapshot | None = None

        async def finalize(self, *, run_id, run_dir, rounds_executed, stop_reason, ranked_candidates):  # noqa: ANN001
            del run_id, run_dir, rounds_executed, stop_reason, ranked_candidates
            self.last_provider_usage = provider_usage
            raise RuntimeError("finalizer failed")

    cast(Any, runtime).finalizer = FailingFinalizer()

    try:
        runtime.run(job_title="Senior Python Engineer", jd="JD", notes="Notes")
    except RuntimeError as exc:
        assert str(exc) == "finalizer failed"
    else:  # pragma: no cover
        raise AssertionError("Expected finalizer failure")

    finalizer_call = _read_json(_single_run_dir(settings.runs_path) / "finalizer_call.json")
    assert finalizer_call["status"] == "failed"
    assert finalizer_call["provider_usage"] == provider_usage.model_dump(mode="json")


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
