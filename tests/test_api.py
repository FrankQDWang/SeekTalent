from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from pydantic_ai.models.test import TestModel

from seektalent import AppSettings, __version__, run_match, run_match_async
from seektalent.bootstrap_assets import default_bootstrap_assets
from seektalent.models import StopGuardThresholds
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
        "exclusion_signal_candidates": [],
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


class FakeRerankRequest:
    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores

    async def __call__(self, request):
        ranked = sorted(self.scores.items(), key=lambda item: item[1], reverse=True)
        return RerankResponse(
            model="test-reranker",
            results=[
                RerankResult(id=item_id, index=index, score=score, rank=index + 1)
                for index, (item_id, score) in enumerate(ranked)
            ],
        )


def _controller_stop_outputs(count: int = 3) -> list[dict[str, object]]:
    return [
        {
            "action": "stop",
            "selected_operator_name": "must_have_alias",
            "operator_args": {},
            "expected_gain_hypothesis": "Stop.",
        }
        for _ in range(count)
    ]


def _active_assets():
    return replace(
        default_bootstrap_assets(),
        stop_guard_thresholds=StopGuardThresholds(),
    )


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(_env_file=None, mock_cts=True, runs_dir=str(tmp_path / "runs"))


def _env_file(tmp_path: Path, *, round_budget: int) -> Path:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "SEEKTALENT_MOCK_CTS=true",
                f"SEEKTALENT_RUNS_DIR={tmp_path / 'runs'}",
                f"SEEKTALENT_ROUND_BUDGET={round_budget}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return env_file


def test_run_match_returns_search_run_bundle(tmp_path: Path) -> None:
    result = run_match(
        job_description="Senior Python / LLM Engineer",
        hiring_notes="Shanghai preferred",
        round_budget=3,
        settings=_settings(tmp_path),
        env_file=None,
        assets=_active_assets(),
        rerank_request=FakeRerankRequest(
            {
                "llm_agent_rag_engineering": 1.2,
                "search_ranking_retrieval_engineering": 0.2,
                "finance_risk_control_ai": 0.1,
            }
        ),
        requirement_extraction_model=TestModel(custom_output_args=_requirement_draft_payload()),
        bootstrap_keyword_generation_model=TestModel(
            custom_output_args=_bootstrap_keyword_draft_payload()
        ),
        search_controller_decision_model=TestModel(custom_output_args=_controller_stop_outputs()),
        search_run_finalization_model=TestModel(
            custom_output_args={"run_summary": "Controller stop accepted."}
        ),
    )

    assert result.phase == "v0.3.2_offline_artifacts_active"
    assert result.bootstrap.routing_result.routing_mode == "inferred_single_pack"
    assert result.bootstrap.runtime_search_budget.initial_round_budget == 5
    assert result.bootstrap.frontier_state.remaining_budget == 5
    assert result.final_result.stop_reason == "controller_stop"
    assert result.final_result.run_summary == "Controller stop accepted."
    assert Path(result.run_dir).joinpath("bundle.json").exists()
    assert result.eval is not None


def test_run_match_async_returns_search_run_bundle(tmp_path: Path) -> None:
    result = asyncio.run(
        run_match_async(
            job_description="Senior Python / LLM Engineer",
            hiring_notes="Shanghai preferred",
            round_budget=20,
            settings=_settings(tmp_path),
            env_file=None,
            assets=_active_assets(),
            rerank_request=FakeRerankRequest(
                {
                    "llm_agent_rag_engineering": 1.2,
                    "search_ranking_retrieval_engineering": 0.2,
                    "finance_risk_control_ai": 0.1,
                }
            ),
            requirement_extraction_model=TestModel(custom_output_args=_requirement_draft_payload()),
            bootstrap_keyword_generation_model=TestModel(
                custom_output_args=_bootstrap_keyword_draft_payload()
            ),
            search_controller_decision_model=TestModel(
                custom_output_args=_controller_stop_outputs()
            ),
            search_run_finalization_model=TestModel(
                custom_output_args={"run_summary": "Controller stop accepted."}
            ),
        )
    )

    assert result.bootstrap.runtime_search_budget.initial_round_budget == 12
    assert result.bootstrap.frontier_state.remaining_budget == 12
    assert result.final_result.stop_reason == "controller_stop"
    assert Path(result.run_dir).joinpath("eval.json").exists()


def test_run_match_uses_env_round_budget_when_cli_override_is_missing(tmp_path: Path) -> None:
    result = run_match(
        job_description="Senior Python / LLM Engineer",
        hiring_notes="Shanghai preferred",
        settings=None,
        env_file=_env_file(tmp_path, round_budget=8),
        assets=_active_assets(),
        rerank_request=FakeRerankRequest(
            {
                "llm_agent_rag_engineering": 1.2,
                "search_ranking_retrieval_engineering": 0.2,
                "finance_risk_control_ai": 0.1,
            }
        ),
        requirement_extraction_model=TestModel(custom_output_args=_requirement_draft_payload()),
        bootstrap_keyword_generation_model=TestModel(
            custom_output_args=_bootstrap_keyword_draft_payload()
        ),
        search_controller_decision_model=TestModel(
            custom_output_args=_controller_stop_outputs()
        ),
        search_run_finalization_model=TestModel(
            custom_output_args={"run_summary": "Controller stop accepted."}
        ),
    )

    assert result.bootstrap.runtime_search_budget.initial_round_budget == 8
    assert result.bootstrap.frontier_state.remaining_budget == 8


def test_run_match_explicit_round_budget_overrides_env_round_budget(tmp_path: Path) -> None:
    result = run_match(
        job_description="Senior Python / LLM Engineer",
        hiring_notes="Shanghai preferred",
        round_budget=20,
        settings=None,
        env_file=_env_file(tmp_path, round_budget=8),
        assets=_active_assets(),
        rerank_request=FakeRerankRequest(
            {
                "llm_agent_rag_engineering": 1.2,
                "search_ranking_retrieval_engineering": 0.2,
                "finance_risk_control_ai": 0.1,
            }
        ),
        requirement_extraction_model=TestModel(custom_output_args=_requirement_draft_payload()),
        bootstrap_keyword_generation_model=TestModel(
            custom_output_args=_bootstrap_keyword_draft_payload()
        ),
        search_controller_decision_model=TestModel(
            custom_output_args=_controller_stop_outputs()
        ),
        search_run_finalization_model=TestModel(
            custom_output_args={"run_summary": "Controller stop accepted."}
        ),
    )

    assert result.bootstrap.runtime_search_budget.initial_round_budget == 12
    assert result.bootstrap.frontier_state.remaining_budget == 12


def test_top_level_exports_remain_available() -> None:
    settings = AppSettings(_env_file=None, mock_cts=True)
    assert settings.mock_cts is True
    assert __version__ == "0.3.2"
