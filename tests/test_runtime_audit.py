import asyncio
import json
from pathlib import Path
from tempfile import mkdtemp
from types import SimpleNamespace
from typing import Any, cast

import pytest

from seektalent.core.retrieval.provider_contract import SearchResult
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
    ScoredCandidateDraft,
    ScoringFailure,
    SearchControllerDecision,
    SearchObservation,
    StopControllerDecision,
)
from seektalent.finalize.finalizer import render_finalize_prompt
from seektalent.normalization import normalize_resume
from seektalent.prompting import LoadedPrompt
from seektalent.runtime.context_builder import build_controller_context, build_finalize_context, build_reflection_context
from seektalent.runtime.scoring_context import build_scoring_context
from seektalent.artifacts import ArtifactStore
from seektalent.runtime.runtime_diagnostics import (
    build_search_diagnostics as build_search_diagnostics_direct,
    collect_llm_schema_pressure,
    slim_controller_context as slim_controller_context_direct,
    slim_finalize_context as slim_finalize_context_direct,
    slim_reflection_context as slim_reflection_context_direct,
    slim_scored_candidate as slim_scored_candidate_direct,
    slim_search_attempt as slim_search_attempt_direct,
    slim_top_pool_snapshot as slim_top_pool_snapshot_direct,
)
import seektalent.artifacts.store as artifact_store_module
from seektalent.progress import ProgressEvent
from seektalent.runtime import WorkflowRuntime
from seektalent.scoring.scorer import ResumeScorer
from seektalent.tracing import LLMCallSnapshot, ProviderUsageSnapshot, RunTracer, json_sha256, provider_usage_from_result
from tests.settings_factory import make_settings
from tests.test_context_builder import _run_state_for_stop_gate, _scored_candidate


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[Any]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [json.loads(line) for line in lines]


def _runtime_artifact(run_dir: Path, name: str, *, extension: str = "json") -> Path:
    return run_dir / "runtime" / f"{name}.{extension}"


def _input_artifact(run_dir: Path, name: str, *, extension: str = "json") -> Path:
    return run_dir / "input" / f"{name}.{extension}"


def _output_artifact(run_dir: Path, name: str, *, extension: str = "json") -> Path:
    return run_dir / "output" / f"{name}.{extension}"


def _round_artifact(run_dir: Path, round_no: int, subsystem: str, name: str, *, extension: str = "json") -> Path:
    return run_dir / "rounds" / f"{round_no:02d}" / subsystem / f"{name}.{extension}"


def _prompt_asset(run_dir: Path, name: str) -> Path:
    return run_dir / "assets" / "prompts" / f"{name}.md"


def _freeze_artifact_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifact_store_module, "utc_now", lambda: "2026-04-28T05:06:07Z")


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


def test_outputs_doc_mentions_prf_v1_5_artifacts() -> None:
    text = Path("docs/outputs.md").read_text(encoding="utf-8")

    assert "rounds/01/retrieval/prf_span_candidates.json" in text
    assert "rounds/01/retrieval/prf_expression_families.json" in text
    assert "rounds/01/retrieval/prf_policy_decision.json" in text


def test_run_tracer_creates_partitioned_run_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_artifact_clock(monkeypatch)
    settings = make_settings(artifacts_dir=str(tmp_path / "artifacts"), mock_cts=True)
    tracer = RunTracer(settings.artifacts_path)
    try:
        assert "artifacts" in str(tracer.run_dir)
        assert tracer.run_dir.parts[-5] == "runs"
        assert tracer.trace_log_path == tracer.run_dir / "runtime" / "trace.log"
        assert tracer.events_path == tracer.run_dir / "runtime" / "events.jsonl"
    finally:
        tracer.close(status="failed", failure_summary="test cleanup")


def test_run_tracer_manifest_is_marked_completed_on_close(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_artifact_clock(monkeypatch)
    settings = make_settings(artifacts_dir=str(tmp_path / "artifacts"), mock_cts=True)
    tracer = RunTracer(settings.artifacts_path)

    tracer.close(status="completed")

    manifest = json.loads((tracer.run_dir / "manifests" / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    assert manifest["completed_at"].endswith("Z")


def test_run_tracer_partition_index_upserts_artifact_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_artifact_clock(monkeypatch)
    settings = make_settings(artifacts_dir=str(tmp_path / "artifacts"), mock_cts=True)
    tracer = RunTracer(settings.artifacts_path)
    tracer.write_text("output.run_summary", "done")

    tracer.close(status="completed")

    index_path = tracer.run_dir.parent / "_index.jsonl"
    rows = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert rows == [
        {
            "artifact_id": tracer.run_id,
            "artifact_kind": "run",
            "created_at": "2026-04-28T05:06:07Z",
            "status": "completed",
            "display_name": "seek talent workflow run",
            "producer": "WorkflowRuntime",
            "summary_logical_artifact": "output.run_summary",
        }
    ]


def test_run_tracer_fallback_writes_are_recorded_in_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_artifact_clock(monkeypatch)
    settings = make_settings(artifacts_dir=str(tmp_path / "artifacts"), mock_cts=True)
    tracer = RunTracer(settings.artifacts_path)
    try:
        path = tracer.write_json("run_config.json", {"mock": True})
        manifest = json.loads((tracer.run_dir / "manifests" / "run_manifest.json").read_text(encoding="utf-8"))
    finally:
        tracer.close(status="failed", failure_summary="test cleanup")

    assert path == tracer.run_dir / "run_config.json"
    assert manifest["logical_artifacts"]["run_config.json"] == {
        "path": "run_config.json",
        "content_type": "application/json",
        "schema_version": "v1",
        "collection": False,
    }


def test_run_tracer_runtime_failure_marks_run_manifest_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_artifact_clock(monkeypatch)
    settings = make_settings(artifacts_dir=str(tmp_path / "artifacts"), mock_cts=True)
    runtime = WorkflowRuntime(settings)

    monkeypatch.setattr(runtime, "_write_run_preamble", lambda **kwargs: None)
    monkeypatch.setattr(runtime, "_require_live_llm_config", lambda: (_ for _ in ()).throw(RuntimeError("boom failure")))

    with pytest.raises(RuntimeError, match="boom failure"):
        runtime.run(job_title="Python Engineer", jd="JD", notes="")

    run_dir = _single_run_dir(settings.artifacts_path)
    manifest = json.loads((run_dir / "manifests" / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["failure_summary"] == "boom failure"
    assert manifest["completed_at"].endswith("Z")


def test_real_scorer_success_path_writes_scoring_calls_to_migrated_round_layout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        artifacts_dir=str(tmp_path / "artifacts"),
        llm_cache_dir=str(tmp_path / "cache"),
        mock_cts=True,
    )
    scorer = ResumeScorer(
        settings,
        LoadedPrompt(name="scoring", path=tmp_path / "scoring.md", content="scoring prompt", sha256="hash"),
    )
    run_state = _run_state_for_stop_gate(
        candidates=[_scored_candidate("resume-1", round_no=1)],
        completed_rounds=1,
        include_untried_family=False,
    )
    context = build_scoring_context(
        run_state=run_state,
        round_no=1,
        normalized_resume=normalize_resume(_make_candidate("resume-1")),
    )

    monkeypatch.setattr(scorer, "_build_agent", lambda prompt_cache_key=None: cast(Any, object()))

    async def fake_score_one_live(*, prompt: str, agent):  # noqa: ANN001
        del prompt, agent
        return (
            ScoredCandidateDraft(
                fit_bucket="fit",
                overall_score=88,
                must_have_match_score=91,
                preferred_match_score=77,
                risk_score=14,
                risk_flags=[],
                reasoning_summary="Strong fit for migrated scoring writer test.",
                matched_must_haves=["python"],
                missing_must_haves=[],
                matched_preferences=["retrieval"],
                negative_signals=[],
            ),
            _provider_usage_snapshot(),
        )

    monkeypatch.setattr(scorer, "_score_one_live", fake_score_one_live)

    tracer = RunTracer(settings.artifacts_path)
    try:
        scored, failures = asyncio.run(scorer.score_candidates_parallel(contexts=[context], tracer=tracer))
    finally:
        tracer.close(status="failed", failure_summary="test cleanup")

    assert failures == []
    assert [item.resume_id for item in scored] == ["resume-1"]
    migrated_path = _round_artifact(tracer.run_dir, 1, "scoring", "scoring_calls", extension="jsonl")
    legacy_path = tracer.run_dir / "rounds" / "round_01" / "scoring_calls.jsonl"
    assert migrated_path.exists()
    assert not legacy_path.exists()
    snapshot = _read_jsonl(migrated_path)[0]
    assert snapshot["input_artifact_refs"] == [
        "round.01.scoring.scoring_input_refs",
        "resumes/resume-1.json",
        "input.scoring_policy",
    ]
    assert snapshot["output_artifact_refs"] == ["round.01.scoring.scorecards"]


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
        "call_id": f"{stage}-call",
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


def _register_runtime_call_artifact(session: Any, logical_name: str) -> None:
    session.register_path(
        logical_name,
        f"{logical_name.replace('.', '/')}.json",
        content_type="application/json",
        schema_version="v1",
    )


def _single_run_dir(root: Path) -> Path:
    if root.name.startswith("run_") and root.is_dir():
        return root
    search_root = root
    if (root / "runs").exists():
        search_root = root / "runs"
    run_dirs = sorted(path for path in search_root.rglob("run_*") if path.is_dir())
    assert len(run_dirs) == 1
    return run_dirs[0]


def _build_run_state_fixture():
    return _run_state_for_stop_gate(
        candidates=[
            _scored_candidate("resume-1", round_no=1),
            _scored_candidate("resume-2", round_no=1),
        ],
        completed_rounds=1,
        include_untried_family=True,
    )


class _AuditFixtureArtifacts:
    def __init__(self, tracer: RunTracer) -> None:
        self.tracer = tracer


def _build_audit_fixture(
    runtime: WorkflowRuntime,
) -> tuple[_AuditFixtureArtifacts, Any, FinalResult, Any]:
    _install_runtime_stubs(runtime, controller=StubController(), resume_scorer=StubScorer())
    run_root = Path(mkdtemp(prefix="runtime-audit-fixture-")) / "runs"
    tracer = RunTracer(run_root)
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
        top_scored, stop_reason, rounds_executed, terminal_controller_round = asyncio.run(
            runtime._run_rounds(
                run_state=run_state,
                tracer=tracer,
            )
        )
        final_result = asyncio.run(
            runtime.finalizer.finalize(
                run_id=tracer.run_id,
                run_dir=str(tracer.run_dir),
                rounds_executed=rounds_executed,
                stop_reason=stop_reason,
                ranked_candidates=top_scored,
            )
        )
        finalizer_context = build_finalize_context(
            run_state=run_state,
            rounds_executed=rounds_executed,
            stop_reason=stop_reason,
            run_id=tracer.run_id,
            run_dir=str(tracer.run_dir),
        )
        finalizer_payload = {"FINALIZER_CONTEXT": finalizer_context.model_dump(mode="json")}
        tracer.session.register_path(
            "runtime.finalizer_context",
            "runtime/finalizer_context.json",
            content_type="application/json",
            schema_version="v1",
        )
        tracer.session.register_path(
            "runtime.finalizer_call",
            "runtime/finalizer_call.json",
            content_type="application/json",
            schema_version="v1",
        )
        tracer.write_json("runtime.finalizer_context", runtime._slim_finalize_context(finalizer_context))
        tracer.write_json(
            "runtime.finalizer_call",
            runtime._build_llm_call_snapshot(
                stage="finalize",
                call_id="finalize-seam-test",
                model_id=runtime.settings.finalize_model,
                prompt_name="finalize",
                user_payload=finalizer_payload,
                user_prompt_text=render_finalize_prompt(
                    run_id=tracer.run_id,
                    run_dir=str(tracer.run_dir),
                    rounds_executed=rounds_executed,
                    stop_reason=stop_reason,
                    ranked_candidates=top_scored,
                ),
                input_artifact_refs=["runtime.finalizer_context"],
                output_artifact_refs=["output.final_candidates"],
                started_at="2026-01-01T00:00:00+00:00",
                latency_ms=1,
                status="succeeded",
                retries=0,
                output_retries=2,
                structured_output=final_result.model_dump(mode="json"),
                validator_retry_count=runtime.finalizer.last_validator_retry_count,
                validator_retry_reasons=runtime.finalizer.last_validator_retry_reasons,
            ).model_dump(mode="json"),
        )
        tracer.write_json("output.final_candidates", final_result.model_dump(mode="json"))
    finally:
        tracer.close()
    return _AuditFixtureArtifacts(tracer), run_state, final_result, terminal_controller_round


def test_runtime_diagnostics_direct_helpers_match_legacy_outputs() -> None:
    runtime = WorkflowRuntime(make_settings())
    run_state = _build_run_state_fixture()
    round_state = run_state.round_history[0]
    controller_context = build_controller_context(
        run_state=run_state,
        round_no=1,
        min_rounds=1,
        max_rounds=4,
        target_new=10,
    )
    reflection_context = build_reflection_context(run_state=run_state, round_state=round_state)
    finalize_context = build_finalize_context(
        run_state=run_state,
        rounds_executed=1,
        stop_reason="max_rounds_reached",
        run_id="run-1",
        run_dir="/tmp/run-1",
    )

    assert slim_controller_context_direct(
        context=controller_context,
        input_text_refs_builder=runtime._input_text_refs,
    ) == runtime._slim_controller_context(controller_context)
    assert slim_reflection_context_direct(
        context=reflection_context,
        input_text_refs_builder=runtime._input_text_refs,
        slim_search_attempt=slim_search_attempt_direct,
        slim_scored_candidate=slim_scored_candidate_direct,
    ) == runtime._slim_reflection_context(reflection_context)
    assert slim_finalize_context_direct(
        context=finalize_context,
        slim_scored_candidate=slim_scored_candidate_direct,
    ) == runtime._slim_finalize_context(finalize_context)
    assert slim_top_pool_snapshot_direct(reflection_context.top_candidates[:5]) == runtime._slim_top_pool_snapshot(
        reflection_context.top_candidates[:5]
    )


def test_runtime_diagnostics_builder_matches_legacy_search_diagnostics() -> None:
    runtime = WorkflowRuntime(make_settings(mock_cts=True, min_rounds=1, max_rounds=1))
    artifacts, run_state, final_result, terminal_controller_round = _build_audit_fixture(runtime)
    round_state = run_state.round_history[0]

    direct = build_search_diagnostics_direct(
        tracer=artifacts.tracer,
        run_state=run_state,
        final_result=final_result,
        terminal_controller_round=terminal_controller_round,
        collect_llm_schema_pressure=runtime._collect_llm_schema_pressure,
        build_round_search_diagnostics=runtime._build_round_search_diagnostics,
        reflection_advice_application_for_decision=runtime._reflection_advice_application_for_decision,
    )

    legacy = runtime._build_search_diagnostics(
        tracer=artifacts.tracer,
        run_state=run_state,
        final_result=final_result,
        terminal_controller_round=terminal_controller_round,
    )

    assert direct == legacy
    assert direct["run_id"] == artifacts.tracer.run_id
    assert direct["input"] == {
        "job_title": run_state.input_truth.job_title,
        "jd_sha256": run_state.input_truth.jd_sha256,
        "notes_sha256": run_state.input_truth.notes_sha256,
    }
    assert direct["summary"] == {
        "rounds_executed": final_result.rounds_executed,
        "total_sent_queries": len(run_state.retrieval_state.sent_query_history),
        "total_raw_candidates": round_state.search_observation.raw_candidate_count,
        "total_unique_new_candidates": round_state.search_observation.unique_new_count,
        "final_candidate_count": len(final_result.candidates),
        "stop_reason": final_result.stop_reason,
        "terminal_controller": None,
    }
    assert len(direct["rounds"]) == 1
    assert direct["rounds"][0]["query_terms"] == round_state.retrieval_plan.query_terms
    assert direct["rounds"][0]["filters"]["projected_provider_filters"] == (
        round_state.retrieval_plan.projected_provider_filters
    )
    assert direct["rounds"][0]["search"]["raw_candidate_count"] == round_state.search_observation.raw_candidate_count
    assert direct["rounds"][0]["search"]["unique_new_count"] == round_state.search_observation.unique_new_count
    assert direct["rounds"][0]["controller_response_to_previous_reflection"] is None


def test_run_config_excludes_company_discovery_settings(tmp_path: Path) -> None:
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        bocha_api_key="bocha-secret",
        candidate_feedback_enabled=True,
        candidate_feedback_model="openai-chat:qwen3.5-flash",
        candidate_feedback_reasoning_effort="off",
        target_company_enabled=False,
    )
    runtime = WorkflowRuntime(settings)
    tracer = RunTracer(settings.runs_path)
    try:
        runtime._write_run_preamble(tracer=tracer, job_title="Agent Engineer", jd="JD", notes="Notes")
    finally:
        tracer.close()

    run_config = _read_json(_runtime_artifact(tracer.run_dir, "run_config"))
    serialized = json.dumps(run_config, ensure_ascii=False)

    assert "bocha_api_key" not in serialized
    assert "bocha-secret" not in serialized
    assert run_config["settings"]["candidate_feedback_enabled"] is True
    assert run_config["settings"]["candidate_feedback_model"] == "openai-chat:qwen3.5-flash"
    assert run_config["settings"]["candidate_feedback_reasoning_effort"] == "off"
    assert "target_company_enabled" not in run_config["settings"]
    assert "has_bocha_key" not in run_config["settings"]
    assert "company_discovery_enabled" not in run_config["settings"]
    assert "company_discovery_provider" not in run_config["settings"]
    assert "company_discovery_model" not in run_config["settings"]


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
        prompt_snapshot_path="assets/prompts/requirements.md",
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
        input_artifact_refs=["round.01.reflection.reflection_context"],
        output_artifact_refs=["round.01.reflection.reflection_advice"],
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


def test_collect_llm_schema_pressure_tolerates_historical_company_discovery_artifacts(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    session = store.create_root(kind="run", display_name="seek talent workflow run", producer="WorkflowRuntime")
    _register_runtime_call_artifact(session, "runtime.requirements_call")
    _register_runtime_call_artifact(session, "runtime.finalizer_call")
    session.write_json("runtime.requirements_call", _aux_call_artifact(stage="requirements", prompt_name="requirements"))
    session.write_json("round.01.controller.controller_call", _aux_call_artifact(stage="controller", prompt_name="controller"))
    session.write_json(
        "round.01.retrieval.company_discovery_plan_call",
        _aux_call_artifact(stage="company_discovery_plan", prompt_name="company_discovery_plan"),
    )
    session.write_json(
        "round.01.retrieval.company_discovery_extract_call",
        _aux_call_artifact(stage="company_discovery_extract", prompt_name="company_discovery_extract"),
    )
    session.write_json(
        "round.01.retrieval.company_discovery_reduce_call",
        _aux_call_artifact(stage="company_discovery_reduce", prompt_name="company_discovery_reduce"),
    )
    session.write_json("round.01.reflection.reflection_call", _aux_call_artifact(stage="reflection", prompt_name="reflection"))
    session.write_json("runtime.finalizer_call", _aux_call_artifact(stage="finalize", prompt_name="finalize"))

    pressure = collect_llm_schema_pressure(session.root)
    stages = [item["stage"] for item in pressure]

    assert stages[0] == "requirements"
    assert stages[-1] == "finalize"
    assert set(stages) == {
        "requirements",
        "controller",
        "company_discovery_plan",
        "company_discovery_extract",
        "company_discovery_reduce",
        "reflection",
        "finalize",
    }


def test_collect_llm_schema_pressure_ignores_legacy_company_discovery_run_config_fields(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    session = store.create_root(kind="run", display_name="seek talent workflow run", producer="WorkflowRuntime")
    _register_runtime_call_artifact(session, "runtime.requirements_call")
    _register_runtime_call_artifact(session, "runtime.finalizer_call")
    session.write_json(
        "runtime.run_config",
        {
            "prompt_hashes": {
                "requirements": "requirements-hash",
                "company_discovery_plan": "legacy-plan-hash",
                "company_discovery_extract": "legacy-extract-hash",
                "company_discovery_reduce": "legacy-reduce-hash",
            },
            "settings": {
                "candidate_feedback_enabled": True,
                "company_discovery_enabled": True,
                "company_discovery_provider": "bocha",
                "company_discovery_model": "openai-chat:qwen3.5-flash",
            },
        },
    )
    session.write_json("runtime.requirements_call", _aux_call_artifact(stage="requirements", prompt_name="requirements"))
    session.write_json("round.01.controller.controller_call", _aux_call_artifact(stage="controller", prompt_name="controller"))
    session.write_json("round.01.reflection.reflection_call", _aux_call_artifact(stage="reflection", prompt_name="reflection"))
    session.write_json("runtime.finalizer_call", _aux_call_artifact(stage="finalize", prompt_name="finalize"))

    pressure = collect_llm_schema_pressure(session.root)

    assert [item["stage"] for item in pressure] == [
        "requirements",
        "controller",
        "reflection",
        "finalize",
    ]
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
    )
    runtime = WorkflowRuntime(settings)

    runtime._require_live_llm_config()

    assert captured_extra_specs == [
        ("openai-chat:qwen-feedback", None, None),
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
        del query_terms, query_role, keyword_query, adapter_notes, provider_filters, runtime_constraints, round_no, trace_id, fetch_mode
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
                f"round.{context.round_no:02d}.scoring.scoring_calls",
                {
                    "stage": "scoring",
                    "call_id": call_id,
                    "round_no": context.round_no,
                    "resume_id": candidate.resume_id,
                    "branch_id": f"r{context.round_no}-{candidate.resume_id}",
                    "model_id": "stub-scorer",
                    "provider": "stub",
                    "prompt_hash": "stub",
                    "prompt_snapshot_path": "assets/prompts/scoring.md",
                    "output_mode": "native_strict",
                    "retries": 0,
                    "output_retries": 2,
                    "started_at": "stub",
                    "latency_ms": 1,
                    "status": "succeeded",
                    "input_artifact_refs": [
                        f"round.{context.round_no:02d}.scoring.scoring_input_refs",
                        f"resumes/{candidate.resume_id}.json",
                        "input.scoring_policy",
                    ],
                    "output_artifact_refs": [f"round.{context.round_no:02d}.scoring.scorecards"],
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
                artifact_paths=[f"rounds/{context.round_no:02d}/scoring/scoring_calls.jsonl"],
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
        if not hasattr(tracer, "run_dir"):
            return [
                ScoredCandidate(
                    resume_id=context.normalized_resume.resume_id,
                    fit_bucket="fit",
                    overall_score=78,
                    must_have_match_score=75,
                    preferred_match_score=60,
                    risk_score=20,
                    risk_flags=[],
                    reasoning_summary="Preview scoring for query-outcome classification.",
                    evidence=["preview"],
                    confidence="medium",
                    matched_must_haves=["python"],
                    missing_must_haves=[],
                    matched_preferences=[],
                    negative_signals=[],
                    strengths=[],
                    weaknesses=[],
                    source_round=context.round_no,
                )
                for context in contexts
            ], []
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
            f"round.{contexts[0].round_no:02d}.scoring.scoring_calls",
            {
                "stage": "scoring",
                "call_id": f"scoring-r{contexts[0].round_no:02d}-stub-{candidate.resume_id}",
                "round_no": contexts[0].round_no,
                "resume_id": failure.resume_id,
                "branch_id": failure.branch_id,
                "model_id": "stub-scorer",
                "provider": "stub",
                "prompt_hash": "stub",
                "prompt_snapshot_path": "assets/prompts/scoring.md",
                "output_mode": "native_strict",
                "retries": 0,
                    "output_retries": 2,
                    "started_at": "stub",
                    "latency_ms": 1,
                    "status": "failed",
                    "input_artifact_refs": [
                        f"round.{contexts[0].round_no:02d}.scoring.scoring_input_refs",
                        f"resumes/{candidate.resume_id}.json",
                        "input.scoring_policy",
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
            artifact_paths=[f"rounds/{contexts[0].round_no:02d}/scoring/scoring_calls.jsonl"],
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


def test_workflow_runtime_execute_search_tool_delegates_to_retrieval_runtime(tmp_path: Path) -> None:
    runtime = WorkflowRuntime(make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True))
    tracer = RunTracer(tmp_path / "trace-runs")
    query = CTSQuery(
        query_terms=["python", "retrieval"],
        keyword_query="python retrieval",
        native_filters={},
        page=1,
        page_size=2,
        rationale="test delegation",
    )
    captured: dict[str, object] = {}

    class FakeRetrievalRuntime:
        async def execute_search_tool(
            self,
            *,
            round_no,
            query,
            runtime_constraints,
            target_new,
            seen_resume_ids,
            seen_dedup_keys,
            tracer,
            city=None,
            phase=None,
            batch_no=None,
            write_round_artifacts=True,
        ):
            captured["round_no"] = round_no
            captured["query"] = query
            captured["target_new"] = target_new
            return (
                [_make_candidate("resume-1")],
                SearchObservation(
                    round_no=1,
                    requested_count=2,
                    raw_candidate_count=1,
                    unique_new_count=1,
                    shortage_count=1,
                    fetch_attempt_count=1,
                    adapter_notes=["delegated"],
                ),
                [],
                0,
            )

    runtime.retrieval_runtime = FakeRetrievalRuntime()

    try:
        new_candidates, observation, attempts, duplicate_count = asyncio.run(
            runtime._execute_search_tool(
                round_no=1,
                query=query,
                runtime_constraints=[],
                target_new=2,
                seen_resume_ids=set(),
                seen_dedup_keys=set(),
                tracer=tracer,
            )
        )
    finally:
        tracer.close()

    assert captured["query"] is query
    assert [candidate.resume_id for candidate in new_candidates] == ["resume-1"]
    assert observation.adapter_notes == ["delegated"]
    assert duplicate_count == 0


def test_query_resume_hits_are_enriched_after_scoring(tmp_path: Path) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, min_rounds=1, max_rounds=2)
    runtime = WorkflowRuntime(settings)
    _install_runtime_stubs(runtime, controller=SearchTwiceController(), resume_scorer=StubScorer())
    tracer = RunTracer(tmp_path / "trace")

    try:
        job_title, jd, notes = _sample_inputs()
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        asyncio.run(runtime._run_rounds(run_state=run_state, tracer=tracer, progress_callback=None))
    finally:
        tracer.close()

    round_01_path = _round_artifact(tracer.run_dir, 1, "retrieval", "query_resume_hits")
    round_02_path = _round_artifact(tracer.run_dir, 2, "retrieval", "query_resume_hits")
    assert round_01_path.exists()
    assert round_02_path.exists()

    round_01_hits = json.loads(round_01_path.read_text())
    round_02_hits = json.loads(round_02_path.read_text())
    assert round_01_hits
    assert isinstance(round_02_hits, list)
    hit = round_01_hits[0]
    assert hit["scored_fit_bucket"] == "fit"
    assert hit["overall_score"] is not None
    assert hit["must_have_match_score"] is not None
    assert hit["risk_score"] is not None
    assert hit["off_intent_reason_count"] == 0
    assert hit["final_candidate_status"] == "fit"


def test_replay_snapshot_contains_provider_snapshot_and_versions(tmp_path: Path) -> None:
    settings = make_settings(runs_dir=str(tmp_path / "runs"), mock_cts=True, min_rounds=1, max_rounds=2)
    runtime = WorkflowRuntime(settings)
    _install_runtime_stubs(runtime, controller=SearchTwiceController(), resume_scorer=StubScorer())
    tracer = RunTracer(tmp_path / "trace")

    try:
        job_title, jd, notes = _sample_inputs()
        run_state = asyncio.run(runtime._build_run_state(job_title=job_title, jd=jd, notes=notes, tracer=tracer))
        asyncio.run(runtime._run_rounds(run_state=run_state, tracer=tracer, progress_callback=None))
    finally:
        tracer.close()

    snapshot = json.loads(_round_artifact(tracer.run_dir, 2, "retrieval", "replay_snapshot").read_text())
    assert snapshot["retrieval_snapshot_id"]
    assert snapshot["provider_request"]
    assert isinstance(snapshot["provider_response_resume_ids"], list)
    assert isinstance(snapshot["provider_response_raw_rank"], list)
    assert snapshot["dedupe_version"] == "v1"
    assert snapshot["scoring_model_version"] == settings.scoring_model
    assert snapshot["query_plan_version"] == "2"
    assert snapshot["prf_gate_version"]
    assert "generic_explore_version" in snapshot


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

    round_dir = artifacts.run_dir / "rounds" / "01"
    controller_decision = _read_json(_round_artifact(artifacts.run_dir, 1, "controller", "controller_decision"))
    retrieval_plan = _read_json(_round_artifact(artifacts.run_dir, 1, "retrieval", "retrieval_plan"))
    projection_result = _read_json(_round_artifact(artifacts.run_dir, 1, "retrieval", "constraint_projection_result"))
    sent_query_records = _read_json(_round_artifact(artifacts.run_dir, 1, "retrieval", "sent_query_records"))
    cts_queries = _read_json(_round_artifact(artifacts.run_dir, 1, "retrieval", "cts_queries"))
    search_observation = _read_json(_round_artifact(artifacts.run_dir, 1, "retrieval", "search_observation"))
    search_attempts = _read_json(_round_artifact(artifacts.run_dir, 1, "retrieval", "search_attempts"))
    requirements_call = _read_json(_runtime_artifact(artifacts.run_dir, "requirements_call"))
    requirement_sheet = _read_json(_input_artifact(artifacts.run_dir, "requirement_sheet"))
    requirement_draft = _read_json(_input_artifact(artifacts.run_dir, "requirement_extraction_draft"))
    controller_call = _read_json(_round_artifact(artifacts.run_dir, 1, "controller", "controller_call"))
    reflection_call = _read_json(_round_artifact(artifacts.run_dir, 1, "reflection", "reflection_call"))
    scoring_calls = _read_jsonl(_round_artifact(artifacts.run_dir, 1, "scoring", "scoring_calls", extension="jsonl"))
    finalizer_call = _read_json(_runtime_artifact(artifacts.run_dir, "finalizer_call"))
    judge_packet = _read_json(_output_artifact(artifacts.run_dir, "judge_packet"))
    evaluation = _read_json(artifacts.run_dir / "evaluation" / "evaluation.json")
    scorecards = _read_jsonl(_round_artifact(artifacts.run_dir, 1, "scoring", "scorecards", extension="jsonl"))
    top_pool_snapshot = _read_json(_round_artifact(artifacts.run_dir, 1, "scoring", "top_pool_snapshot"))
    sent_query_history = _read_json(_runtime_artifact(artifacts.run_dir, "sent_query_history"))
    run_config = _read_json(_runtime_artifact(artifacts.run_dir, "run_config"))
    final_candidates = _read_json(_output_artifact(artifacts.run_dir, "final_candidates"))
    controller_context = _read_json(_round_artifact(artifacts.run_dir, 1, "controller", "controller_context"))
    reflection_context = _read_json(_round_artifact(artifacts.run_dir, 1, "reflection", "reflection_context"))
    finalizer_context = _read_json(_runtime_artifact(artifacts.run_dir, "finalizer_context"))
    search_diagnostics = _read_json(_runtime_artifact(artifacts.run_dir, "search_diagnostics"))
    term_surface_audit = _read_json(_runtime_artifact(artifacts.run_dir, "term_surface_audit"))
    run_summary = _output_artifact(artifacts.run_dir, "run_summary", extension="md").read_text(encoding="utf-8")
    round_review = _round_artifact(artifacts.run_dir, 1, "reflection", "round_review", extension="md").read_text(encoding="utf-8")
    events = _read_jsonl(_runtime_artifact(artifacts.run_dir, "events", extension="jsonl"))

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
        **projection_result["provider_filters"],
        "location": ["上海"],
    }
    assert "runtime location dispatch: 上海" in cts_queries[0]["adapter_notes"]
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
    assert not _round_artifact(artifacts.run_dir, 1, "scoring", "normalized_resumes", extension="jsonl").exists()
    assert _round_artifact(artifacts.run_dir, 1, "scoring", "scoring_input_refs", extension="jsonl").exists()
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
    assert "input.input_truth" in requirements_call["input_artifact_refs"]
    assert "input.requirement_extraction_draft" in requirements_call["output_artifact_refs"]
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
    assert "round.01.controller.controller_context" in controller_call["input_artifact_refs"]
    assert "round.01.controller.controller_decision" in controller_call["output_artifact_refs"]
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
    assert "round.01.reflection.reflection_context" in reflection_call["input_artifact_refs"]
    assert "round.01.reflection.reflection_advice" in reflection_call["output_artifact_refs"]
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
    assert scoring_calls[0]["input_artifact_refs"] == ["round.01.scoring.scoring_input_refs", "resumes/mock-r001.json", "input.scoring_policy"]
    assert "user_payload" not in finalizer_call
    assert "structured_output" not in finalizer_call
    assert finalizer_call["input_payload_sha256"]
    assert finalizer_call["structured_output_sha256"]
    assert finalizer_call["prompt_chars"] > 0
    assert finalizer_call["input_payload_chars"] > 0
    assert finalizer_call["output_chars"] > 0
    assert "ranked_candidates" in finalizer_call["input_summary"]
    assert "candidates=" in finalizer_call["output_summary"]
    assert "runtime.finalizer_context" in finalizer_call["input_artifact_refs"]
    assert "output.final_candidates" in finalizer_call["output_artifact_refs"]
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
    assert diagnostic_round["filters"]["projected_provider_filters"] == retrieval_plan["projected_provider_filters"]
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
    assert _prompt_asset(artifacts.run_dir, "requirements").exists()
    assert _prompt_asset(artifacts.run_dir, "controller").exists()
    assert _prompt_asset(artifacts.run_dir, "scoring").exists()
    assert _prompt_asset(artifacts.run_dir, "reflection").exists()
    assert _prompt_asset(artifacts.run_dir, "finalize").exists()
    assert _prompt_asset(artifacts.run_dir, "judge").exists()
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
    assert "rounds/01/controller/controller_call.json" in controller_event["artifact_paths"]
    assert finalizer_event["status"] == "succeeded"
    assert finalizer_event["artifact_paths"] == [
        "runtime/finalizer_context.json",
        "runtime/finalizer_call.json",
        "output/final_candidates.json",
        "output/final_answer.md",
        "output/judge_packet.json",
        "runtime/search_diagnostics.json",
        "output/run_summary.md",
    ]
    assert run_finished_event["summary"] == "Run completed after 1 retrieval rounds."


def test_runtime_delegates_post_finalize_shell(tmp_path: Path, monkeypatch) -> None:
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
    job_title, jd, notes = _sample_inputs()
    calls: list[str] = []
    completed_artifact_paths: list[str] | None = None

    def fake_write_post_finalize_artifacts(**kwargs) -> list[str]:  # noqa: ANN003
        nonlocal completed_artifact_paths
        del kwargs
        calls.append("write")
        completed_artifact_paths = [
            "output/judge_packet.json",
            "runtime/search_diagnostics.json",
            "output/run_summary.md",
        ]
        return completed_artifact_paths

    async def fake_run_post_finalize_stage(**kwargs) -> SimpleNamespace:  # noqa: ANN003
        del kwargs
        calls.append("run")
        return SimpleNamespace(evaluation_result=None)

    def fake_finalize_finalizer_stage(*, completed_artifact_paths: list[str], **kwargs) -> None:  # noqa: ANN003
        del kwargs
        calls.append("finalize")
        assert completed_artifact_paths == [
            "output/judge_packet.json",
            "runtime/search_diagnostics.json",
            "output/run_summary.md",
        ]

    monkeypatch.setattr(
        "seektalent.runtime.orchestrator.post_finalize_runtime.write_post_finalize_artifacts",
        fake_write_post_finalize_artifacts,
    )
    monkeypatch.setattr(
        "seektalent.runtime.orchestrator.post_finalize_runtime.run_post_finalize_stage",
        fake_run_post_finalize_stage,
    )
    monkeypatch.setattr(
        "seektalent.runtime.orchestrator.finalize_runtime.finalize_finalizer_stage",
        fake_finalize_finalizer_stage,
    )

    artifacts = runtime.run(job_title=job_title, jd=jd, notes=notes)

    assert completed_artifact_paths == [
        "output/judge_packet.json",
        "runtime/search_diagnostics.json",
        "output/run_summary.md",
    ]
    assert calls == ["write", "finalize", "run"]
    assert artifacts.evaluation_result is None
    assert artifacts.final_result.summary
    assert artifacts.final_markdown


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
            "lane_type": "exploit",
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


def test_runtime_tui_summary_artifacts_exclude_company_discovery_prompts(tmp_path: Path, monkeypatch) -> None:
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

    run_config = _read_json(_runtime_artifact(artifacts.run_dir, "run_config"))
    tui_summary_call = _read_json(_round_artifact(artifacts.run_dir, 1, "scoring", "tui_summary_call"))

    assert "tui_summary" in run_config["prompt_hashes"]
    assert "company_discovery_plan" not in run_config["prompt_hashes"]
    assert "company_discovery_extract" not in run_config["prompt_hashes"]
    assert "company_discovery_reduce" not in run_config["prompt_hashes"]
    assert "repair_requirements" in run_config["prompt_hashes"]
    assert "repair_controller" in run_config["prompt_hashes"]
    assert "repair_reflection" in run_config["prompt_hashes"]
    assert _prompt_asset(artifacts.run_dir, "tui_summary").exists()
    assert not _prompt_asset(artifacts.run_dir, "company_discovery_plan").exists()
    assert not _prompt_asset(artifacts.run_dir, "company_discovery_extract").exists()
    assert not _prompt_asset(artifacts.run_dir, "company_discovery_reduce").exists()
    assert _prompt_asset(artifacts.run_dir, "repair_requirements").exists()
    assert _prompt_asset(artifacts.run_dir, "repair_controller").exists()
    assert _prompt_asset(artifacts.run_dir, "repair_reflection").exists()
    assert tui_summary_call["stage"] == "tui_summary"
    assert tui_summary_call["prompt_hash"] == run_config["prompt_hashes"]["tui_summary"]
    assert tui_summary_call["prompt_snapshot_path"] == "assets/prompts/tui_summary.md"
    assert "round.01.scoring.tui_summary" in tui_summary_call["output_artifact_refs"]


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

    run_config = _read_json(_runtime_artifact(artifacts.run_dir, "run_config"))
    repair_requirements_call = _read_json(_runtime_artifact(artifacts.run_dir, "repair_requirements_call"))
    repair_controller_call = _read_json(_round_artifact(artifacts.run_dir, 1, "controller", "repair_controller_call"))
    repair_reflection_call = _read_json(_round_artifact(artifacts.run_dir, 1, "reflection", "repair_reflection_call"))

    assert repair_requirements_call["prompt_hash"] == run_config["prompt_hashes"]["repair_requirements"]
    assert repair_controller_call["prompt_hash"] == run_config["prompt_hashes"]["repair_controller"]
    assert repair_reflection_call["prompt_hash"] == run_config["prompt_hashes"]["repair_reflection"]
    assert repair_requirements_call["prompt_snapshot_path"] == "assets/prompts/repair_requirements.md"
    assert repair_controller_call["prompt_snapshot_path"] == "assets/prompts/repair_controller.md"
    assert repair_reflection_call["prompt_snapshot_path"] == "assets/prompts/repair_reflection.md"

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

    run_summary = _output_artifact(artifacts.run_dir, "run_summary", extension="md").read_text(encoding="utf-8")
    judge_packet = _read_json(_output_artifact(artifacts.run_dir, "judge_packet"))
    search_diagnostics = _read_json(_runtime_artifact(artifacts.run_dir, "search_diagnostics"))
    events = _read_jsonl(_runtime_artifact(artifacts.run_dir, "events", extension="jsonl"))
    round_02_dir = artifacts.run_dir / "rounds" / "02"

    assert _round_artifact(artifacts.run_dir, 2, "controller", "controller_decision").exists()
    assert not _round_artifact(artifacts.run_dir, 2, "retrieval", "retrieval_plan").exists()
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

    search_diagnostics = _read_json(_runtime_artifact(artifacts.run_dir, "search_diagnostics"))
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

    events = _read_jsonl(_runtime_artifact(artifacts.run_dir, "events", extension="jsonl"))
    run_summary = _output_artifact(artifacts.run_dir, "run_summary", extension="md").read_text(encoding="utf-8")
    run_config = _read_json(_runtime_artifact(artifacts.run_dir, "run_config"))
    search_diagnostics = _read_json(_runtime_artifact(artifacts.run_dir, "search_diagnostics"))
    term_surface_audit = _read_json(_runtime_artifact(artifacts.run_dir, "term_surface_audit"))

    assert artifacts.evaluation_result is None
    assert not _output_artifact(artifacts.run_dir, "judge_packet").exists()
    assert not (artifacts.run_dir / "evaluation").exists()
    assert not (artifacts.run_dir / "raw_resumes").exists()
    assert search_diagnostics["summary"]["rounds_executed"] == 1
    assert search_diagnostics["summary"]["final_candidate_count"] > 0
    assert "Judge packet" not in run_summary
    assert "evaluation_completed" not in {item["event_type"] for item in events}
    assert "evaluation_skipped" in {item["event_type"] for item in events}
    finalizer_event = next(item for item in events if item["event_type"] == "finalizer_completed")
    assert finalizer_event["artifact_paths"] == [
        "runtime/finalizer_context.json",
        "runtime/finalizer_call.json",
        "output/final_candidates.json",
        "output/final_answer.md",
        "runtime/search_diagnostics.json",
        "output/run_summary.md",
    ]
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
    settings = make_settings(runs_dir=str(tmp_path / "runs"), artifacts_dir=str(tmp_path / "artifacts"), mock_cts=True)
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

    requirements_call = _read_json(_runtime_artifact(_single_run_dir(settings.artifacts_path), "requirements_call"))
    assert requirements_call["status"] == "failed"
    assert requirements_call["provider_usage"] == provider_usage.model_dump(mode="json")


def test_controller_failure_snapshot_records_provider_usage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    provider_usage = _provider_usage_snapshot()
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        artifacts_dir=str(tmp_path / "artifacts"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=1,
    )
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

    controller_call = _read_json(_round_artifact(_single_run_dir(settings.artifacts_path), 1, "controller", "controller_call"))
    assert controller_call["status"] == "failed"
    assert controller_call["provider_usage"] == provider_usage.model_dump(mode="json")


def test_reflection_failure_snapshot_records_provider_usage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    provider_usage = _provider_usage_snapshot()
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        artifacts_dir=str(tmp_path / "artifacts"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=1,
    )
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

    reflection_call = _read_json(_round_artifact(_single_run_dir(settings.artifacts_path), 1, "reflection", "reflection_call"))
    assert reflection_call["status"] == "failed"
    assert reflection_call["provider_usage"] == provider_usage.model_dump(mode="json")


def test_finalizer_failure_snapshot_records_provider_usage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    provider_usage = _provider_usage_snapshot()
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        artifacts_dir=str(tmp_path / "artifacts"),
        mock_cts=True,
        min_rounds=1,
        max_rounds=1,
    )
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

    finalizer_call = _read_json(_runtime_artifact(_single_run_dir(settings.artifacts_path), "finalizer_call"))
    assert finalizer_call["status"] == "failed"
    assert finalizer_call["provider_usage"] == provider_usage.model_dump(mode="json")


def test_runtime_fails_fast_when_provider_credentials_are_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("seektalent.llm.load_process_env", lambda: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        artifacts_dir=str(tmp_path / "artifacts"),
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

    run_dir = _single_run_dir(settings.artifacts_path)
    assert _runtime_artifact(run_dir, "run_config").exists()
    assert _input_artifact(run_dir, "input_snapshot").exists()
    assert not _output_artifact(run_dir, "final_candidates").exists()
    assert not _output_artifact(run_dir, "final_answer", extension="md").exists()
    events = _read_jsonl(_runtime_artifact(run_dir, "events", extension="jsonl"))
    assert events[-1]["event_type"] == "run_failed"
    assert events[-1]["payload"]["stage"] == "llm_preflight"
    assert "OPENAI_API_KEY" in events[-1]["payload"]["error_message"]


def test_runtime_aborts_when_scoring_has_a_final_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    settings = make_settings(
        runs_dir=str(tmp_path / "runs"),
        artifacts_dir=str(tmp_path / "artifacts"),
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

    run_dir = _single_run_dir(settings.artifacts_path)
    query_resume_hits = _read_json(_round_artifact(run_dir, 1, "retrieval", "query_resume_hits"))
    assert not _output_artifact(run_dir, "final_candidates").exists()
    assert not _output_artifact(run_dir, "final_answer", extension="md").exists()
    assert query_resume_hits
    assert all(item["final_candidate_status"] == "not_scored" for item in query_resume_hits)
    assert all(item["scored_fit_bucket"] is None for item in query_resume_hits)
