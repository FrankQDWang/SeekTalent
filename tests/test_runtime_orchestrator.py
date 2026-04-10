from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from pathlib import Path

from pydantic_ai.models.test import TestModel

from seektalent.bootstrap_assets import default_bootstrap_assets
from seektalent.clients.cts_client import CTSFetchResult
from seektalent.config import AppSettings
from seektalent.models import RetrievedCandidate_t, StopGuardThresholds
from seektalent.runtime.orchestrator import WorkflowRuntime
from seektalent_rerank.models import RerankResponse, RerankResult


def _requirement_draft_payload() -> dict[str, object]:
    return {
        "role_title_candidate": "Senior Python / LLM Engineer",
        "role_summary_candidate": "Build Python and retrieval systems.",
        "must_have_capability_candidates": [
            "Python backend",
            "LLM application",
            "retrieval pipeline",
        ],
        "preferred_capability_candidates": ["workflow orchestration"],
        "exclusion_signal_candidates": ["frontend"],
        "preference_candidates": {
            "preferred_domains": [],
            "preferred_backgrounds": [],
        },
        "hard_constraint_candidates": {
            "locations": ["Shanghai"],
            "min_years": 5,
            "max_years": 10,
        },
        "scoring_rationale_candidate": "Prioritize core must-have fit.",
    }


def _bootstrap_keyword_draft_payload() -> dict[str, object]:
    return {
        "candidate_seeds": [
            {
                "intent_type": "core_precision",
                "keywords": ["agent engineer", "rag", "python backend"],
                "source_knowledge_pack_ids": [],
                "reasoning": "anchor the route",
            },
            {
                "intent_type": "must_have_alias",
                "keywords": ["llm application", "retrieval pipeline"],
                "source_knowledge_pack_ids": [],
                "reasoning": "cover aliases",
            },
            {
                "intent_type": "relaxed_floor",
                "keywords": ["python backend", "retrieval"],
                "source_knowledge_pack_ids": [],
                "reasoning": "widen recall",
            },
            {
                "intent_type": "pack_expansion",
                "keywords": ["workflow orchestration", "tool calling"],
                "source_knowledge_pack_ids": ["llm_agent_rag_engineering"],
                "reasoning": "use pack hints",
            },
            {
                "intent_type": "generic_expansion",
                "keywords": ["backend engineer", "agent workflow"],
                "source_knowledge_pack_ids": [],
                "reasoning": "extra route",
            },
        ],
        "negative_keywords": ["frontend"],
    }


def _candidate(candidate_id: str, *, search_text: str) -> RetrievedCandidate_t:
    return RetrievedCandidate_t(
        candidate_id=candidate_id,
        now_location="Shanghai",
        expected_location="Shanghai",
        years_of_experience_raw=6,
        education_summaries=["复旦大学 计算机 本科"],
        work_experience_summaries=[
            "TestCo | Python Engineer | Built retrieval ranking systems."
        ],
        project_names=["retrieval platform"],
        work_summaries=["python", "ranking"],
        search_text=search_text,
        raw_payload={"title": "Python Engineer", "workExperienceList": []},
    )


@dataclass
class FakeCTSClient:
    results: list[CTSFetchResult]
    seen_plans: list[object] = field(default_factory=list)

    async def search(self, plan, *, trace_id: str = "") -> CTSFetchResult:
        del trace_id
        self.seen_plans.append(plan)
        if not self.results:
            raise AssertionError("unexpected_cts_call")
        return self.results.pop(0)


@dataclass
class FakeRerankRequest:
    pack_scores: dict[str, float]
    candidate_scores: list[dict[str, float]]
    seen_requests: list[object] = field(default_factory=list)

    async def __call__(self, request):
        self.seen_requests.append(request)
        document_ids = [document.id for document in request.documents]
        if document_ids and document_ids[0] in self.pack_scores:
            scores = self.pack_scores
        else:
            if not self.candidate_scores:
                raise AssertionError("unexpected_candidate_rerank_call")
            scores = self.candidate_scores.pop(0)
        return RerankResponse(
            model="test-reranker",
            results=[
                RerankResult(id=item_id, index=index, score=scores[item_id], rank=index + 1)
                for index, item_id in enumerate(document_ids)
            ],
        )


@dataclass
class SequentialTestModel:
    outputs: list[dict[str, object]]
    model_name: str = "test"

    @property
    def custom_output_args(self) -> dict[str, object]:
        if not self.outputs:
            raise AssertionError("unexpected_model_call")
        return self.outputs.pop(0)


def _runtime_assets(*, min_round_index: int):
    return replace(
        default_bootstrap_assets(),
        stop_guard_thresholds=StopGuardThresholds(min_round_index=min_round_index),
    )


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(_env_file=None, mock_cts=True, runs_dir=str(tmp_path / "runs"))


def test_workflow_runtime_uses_same_reranker_for_routing_and_candidate_scoring(tmp_path: Path) -> None:
    rerank = FakeRerankRequest(
        pack_scores={
            "llm_agent_rag_engineering": 1.2,
            "search_ranking_retrieval_engineering": 0.2,
            "finance_risk_control_ai": 0.1,
        },
        candidate_scores=[{"candidate-1": 2.0}],
    )
    runtime = WorkflowRuntime(
        _settings(tmp_path),
        assets=_runtime_assets(min_round_index=1),
        cts_client=FakeCTSClient(
            results=[
                CTSFetchResult(
                    request_payload={},
                    candidates=[_candidate("candidate-1", search_text="python ranking workflow")],
                    raw_candidate_count=1,
                    latency_ms=5,
                )
            ]
        ),
        rerank_request=rerank,
        requirement_extraction_model=TestModel(custom_output_args=_requirement_draft_payload()),
        bootstrap_keyword_generation_model=TestModel(
            custom_output_args=_bootstrap_keyword_draft_payload()
        ),
        search_controller_decision_model=SequentialTestModel(
            outputs=[
                {
                    "action": "search_cts",
                    "selected_operator_name": "core_precision",
                    "operator_args": {"additional_terms": ["ranking"]},
                    "expected_gain_hypothesis": "Expand ranking coverage.",
                },
                {
                    "action": "stop",
                    "selected_operator_name": "core_precision",
                    "operator_args": {},
                    "expected_gain_hypothesis": "Enough evidence.",
                },
            ]
        ),
        branch_outcome_evaluation_model=TestModel(
            custom_output_args={
                "novelty_score": 0.8,
                "usefulness_score": 0.7,
                "branch_exhausted": False,
                "repair_operator_hint": "core_precision",
                "evaluation_notes": "Good expansion.",
            }
        ),
        search_run_finalization_model=TestModel(
            custom_output_args={"run_summary": "The shortlist is ready for review."}
        ),
    )

    result = asyncio.run(
        runtime.run_async(
            job_description="Senior Python / LLM Engineer",
            hiring_notes="Shanghai preferred",
            round_budget=20,
        )
    )

    assert result.bootstrap.routing_result.routing_mode == "inferred_single_pack"
    assert result.bootstrap.runtime_search_budget.initial_round_budget == 12
    assert result.bootstrap.frontier_state.remaining_budget == 12
    assert result.final_result.stop_reason == "controller_stop"
    assert result.final_result.final_shortlist_candidate_ids == ["candidate-1"]
    assert result.rounds[0].runtime_audit_tags == {"candidate-1": ["ranking"]}
    assert result.bootstrap.requirement_extraction_audit.prompt_surface.surface_id == "requirement_extraction"
    assert result.rounds[0].controller_audit.prompt_surface.surface_id == "search_controller_decision"
    assert result.rounds[0].branch_evaluation_audit is not None
    assert result.rounds[0].branch_evaluation_audit.prompt_surface.surface_id == "branch_outcome_evaluation"
    assert result.finalization_audit.prompt_surface.surface_id == "search_run_finalization"
    assert result.rounds[0].controller_context.runtime_budget_state.search_phase == "explore"
    assert result.rounds[0].controller_context.runtime_budget_state.phase_progress == 0.0
    assert [document.id for document in rerank.seen_requests[0].documents] == [
        "llm_agent_rag_engineering",
        "search_ranking_retrieval_engineering",
        "finance_risk_control_ai",
    ]
    assert [document.id for document in rerank.seen_requests[1].documents] == ["candidate-1"]
    metrics = {metric.name: metric.value for metric in result.eval.metrics}
    assert metrics["prompt_surface_count"] == 6
    assert metrics["budget_warning_round_count"] == 0
    assert Path(result.run_dir).joinpath("bundle.json").exists()


def test_workflow_runtime_stops_on_exhausted_low_gain(tmp_path: Path) -> None:
    runtime = WorkflowRuntime(
        _settings(tmp_path),
        assets=_runtime_assets(min_round_index=2),
        cts_client=FakeCTSClient(
            results=[
                CTSFetchResult(
                    request_payload={},
                    candidates=[],
                    raw_candidate_count=0,
                    latency_ms=5,
                )
            ]
        ),
        rerank_request=FakeRerankRequest(
            pack_scores={
                "llm_agent_rag_engineering": 1.2,
                "search_ranking_retrieval_engineering": 0.2,
                "finance_risk_control_ai": 0.1,
            },
            candidate_scores=[],
        ),
        requirement_extraction_model=TestModel(custom_output_args=_requirement_draft_payload()),
        bootstrap_keyword_generation_model=TestModel(
            custom_output_args=_bootstrap_keyword_draft_payload()
        ),
        search_controller_decision_model=TestModel(
            custom_output_args={
                "action": "search_cts",
                "selected_operator_name": "core_precision",
                "operator_args": {"additional_terms": ["ranking"]},
                "expected_gain_hypothesis": "Try one more search.",
            }
        ),
        branch_outcome_evaluation_model=TestModel(
            custom_output_args={
                "novelty_score": 0.1,
                "usefulness_score": 0.1,
                "branch_exhausted": False,
                "repair_operator_hint": "core_precision",
                "evaluation_notes": "No useful new fit.",
            }
        ),
        search_run_finalization_model=TestModel(
            custom_output_args={"run_summary": "The latest branch added too little value."}
        ),
    )

    result = asyncio.run(
        runtime.run_async(
            job_description="Senior Python / LLM Engineer",
            hiring_notes="Shanghai preferred",
        )
    )

    assert result.final_result.stop_reason == "exhausted_low_gain"
    assert result.final_result.final_shortlist_candidate_ids == []
    assert result.rounds[0].reward_breakdown is not None


def test_workflow_runtime_rejects_direct_stop_then_accepts_next_round(tmp_path: Path) -> None:
    rerank = FakeRerankRequest(
        pack_scores={
            "llm_agent_rag_engineering": 1.2,
            "search_ranking_retrieval_engineering": 0.2,
            "finance_risk_control_ai": 0.1,
        },
        candidate_scores=[],
    )
    runtime = WorkflowRuntime(
        _settings(tmp_path),
        assets=_runtime_assets(min_round_index=1),
        cts_client=FakeCTSClient(results=[]),
        rerank_request=rerank,
        requirement_extraction_model=TestModel(custom_output_args=_requirement_draft_payload()),
        bootstrap_keyword_generation_model=TestModel(
            custom_output_args=_bootstrap_keyword_draft_payload()
        ),
        search_controller_decision_model=TestModel(
            custom_output_args=[
                {
                    "action": "stop",
                    "selected_operator_name": "must_have_alias",
                    "operator_args": {},
                    "expected_gain_hypothesis": "Stop now.",
                },
                {
                    "action": "stop",
                    "selected_operator_name": "must_have_alias",
                    "operator_args": {},
                    "expected_gain_hypothesis": "Stop now.",
                },
            ]
        ),
        search_run_finalization_model=TestModel(
            custom_output_args={"run_summary": "Stopped after controller confirmation."}
        ),
    )

    result = asyncio.run(
        runtime.run_async(
            job_description="Senior Python / LLM Engineer",
            hiring_notes="Shanghai preferred",
        )
    )

    assert result.final_result.stop_reason == "controller_stop"
    assert [round_artifact.stop_reason for round_artifact in result.rounds] == [
        None,
        "controller_stop",
    ]
    assert len(rerank.seen_requests) == 1
