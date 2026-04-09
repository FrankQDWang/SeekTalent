from __future__ import annotations

import asyncio

import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.models.test import TestModel

from seektalent.controller_llm import request_search_controller_decision_draft
from seektalent.models import FitGateConstraints, SearchControllerContext_t


def _context() -> SearchControllerContext_t:
    return SearchControllerContext_t.model_validate(
        {
            "active_frontier_node_summary": {
                "frontier_node_id": "seed_agent_core",
                "selected_operator_name": "must_have_alias",
                "node_query_term_pool": ["agent engineer", "python", "workflow", "backend"],
                "node_shortlist_candidate_ids": ["c1"],
            },
            "donor_candidate_node_summaries": [
                {
                    "frontier_node_id": "child_search_domain_01",
                    "shared_anchor_terms": ["python"],
                    "expected_incremental_coverage": ["ranking"],
                    "reward_score": 2.5,
                }
            ],
            "frontier_head_summary": {
                "open_node_count": 2,
                "remaining_budget": 2,
                "highest_priority_score": 3.4,
            },
            "unmet_requirement_weights": [
                {"capability": "python", "weight": 0.3},
                {"capability": "ranking", "weight": 1.0},
            ],
            "operator_statistics_summary": {
                "must_have_alias": {"average_reward": 0.0, "times_selected": 0}
            },
            "allowed_operator_names": [
                "must_have_alias",
                "strict_core",
                "crossover_compose",
            ],
            "term_budget_range": [2, 5],
            "fit_gate_constraints": FitGateConstraints().model_dump(mode="python"),
        }
    )


def test_request_search_controller_decision_draft_records_strict_audit() -> None:
    draft, audit = asyncio.run(
        request_search_controller_decision_draft(
            _context(),
            model=TestModel(
                custom_output_args={
                    "action": "search_cts",
                    "selected_operator_name": "strict_core",
                    "operator_args": {"additional_terms": ["ranking"]},
                    "expected_gain_hypothesis": "Expand ranking coverage.",
                }
            ),
        )
    )

    assert draft.selected_operator_name == "strict_core"
    assert audit.output_mode == "NativeOutput(strict=True)"
    assert audit.retries == 0
    assert audit.output_retries == 1
    assert audit.validator_retry_count == 0
    assert audit.model_name == "test"
    assert audit.message_history_mode == "fresh"
    assert audit.tools_enabled is False
    assert audit.model_settings_snapshot == {
        "allow_text_output": False,
        "allow_image_output": False,
        "native_output_strict": True,
    }


def test_request_search_controller_decision_draft_retries_once_for_empty_non_crossover_patch() -> None:
    draft, audit = asyncio.run(
        request_search_controller_decision_draft(
            _context(),
            model=TestModel(
                custom_output_args=[
                    {
                        "action": "search_cts",
                        "selected_operator_name": "strict_core",
                        "operator_args": {"additional_terms": ["", " "]},
                        "expected_gain_hypothesis": "Expand ranking coverage.",
                    },
                    {
                        "action": "search_cts",
                        "selected_operator_name": "strict_core",
                        "operator_args": {"additional_terms": ["ranking"]},
                        "expected_gain_hypothesis": "Expand ranking coverage.",
                    },
                ]
            ),
        )
    )

    assert draft.operator_args == {"additional_terms": ["ranking"]}
    assert audit.validator_retry_count == 1


def test_request_search_controller_decision_draft_fails_after_single_validator_retry() -> None:
    with pytest.raises(ModelRetry, match="requires non-empty additional_terms"):
        asyncio.run(
            request_search_controller_decision_draft(
                _context(),
                model=TestModel(
                    custom_output_args=[
                        {
                            "action": "search_cts",
                            "selected_operator_name": "strict_core",
                            "operator_args": {"additional_terms": []},
                            "expected_gain_hypothesis": "Expand ranking coverage.",
                        },
                        {
                            "action": "search_cts",
                            "selected_operator_name": "strict_core",
                            "operator_args": {"additional_terms": [""]},
                            "expected_gain_hypothesis": "Expand ranking coverage.",
                        },
                    ]
                ),
            )
        )
