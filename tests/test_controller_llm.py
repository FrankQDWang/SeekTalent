from __future__ import annotations

import asyncio
from hashlib import sha1

import pytest
from pydantic_ai import ModelRetry
from pydantic_ai.models.test import TestModel

from seektalent.controller_llm import request_search_controller_decision_draft
from seektalent.frontier_ops import generate_search_controller_decision
from seektalent.models import FitGateConstraints, SearchControllerContext_t
from seektalent.prompts import load_prompt


def _context(
    *,
    node_query_term_pool: list[str] | None = None,
    term_budget_range: tuple[int, int] = (2, 5),
) -> SearchControllerContext_t:
    return SearchControllerContext_t.model_validate(
        {
            "active_frontier_node_summary": {
                "frontier_node_id": "seed_agent_core",
                "selected_operator_name": "must_have_alias",
                "node_query_term_pool": (
                    ["agent engineer", "python", "workflow", "backend"]
                    if node_query_term_pool is None
                    else node_query_term_pool
                ),
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
                "core_precision",
                "crossover_compose",
            ],
            "term_budget_range": list(term_budget_range),
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
                    "selected_operator_name": "core_precision",
                    "operator_args": {"additional_terms": ["ranking"]},
                    "expected_gain_hypothesis": "Expand ranking coverage.",
                }
            ),
        )
    )

    assert draft.selected_operator_name == "core_precision"
    assert audit.output_mode == "NativeOutput(strict=True)"
    assert audit.retries == 0
    assert audit.output_retries == 1
    assert audit.validator_retry_count == 0
    assert audit.model_name == "test"
    assert audit.message_history_mode == "fresh"
    assert audit.tools_enabled is False
    assert audit.instruction_id_or_hash == sha1(
        load_prompt("search_controller_decision.md").encode("utf-8")
    ).hexdigest()
    assert audit.model_settings_snapshot == {
        "allow_text_output": False,
        "allow_image_output": False,
        "native_output_strict": True,
    }


def test_request_search_controller_decision_draft_retries_once_for_empty_non_crossover_patch() -> None:
    draft, audit = asyncio.run(
        request_search_controller_decision_draft(
            _context(node_query_term_pool=[]),
            model=TestModel(
                custom_output_args=[
                    {
                        "action": "search_cts",
                        "selected_operator_name": "core_precision",
                        "operator_args": {"additional_terms": ["", " "]},
                        "expected_gain_hypothesis": "Expand ranking coverage.",
                    },
                    {
                        "action": "search_cts",
                        "selected_operator_name": "core_precision",
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
    with pytest.raises(ModelRetry, match="requires materializable non-empty query terms"):
        asyncio.run(
            request_search_controller_decision_draft(
                _context(node_query_term_pool=[]),
                model=TestModel(
                    custom_output_args=[
                        {
                            "action": "search_cts",
                            "selected_operator_name": "core_precision",
                            "operator_args": {"additional_terms": []},
                            "expected_gain_hypothesis": "Expand ranking coverage.",
                        },
                        {
                            "action": "search_cts",
                            "selected_operator_name": "core_precision",
                            "operator_args": {"additional_terms": [""]},
                            "expected_gain_hypothesis": "Expand ranking coverage.",
                        },
                    ]
                ),
            )
        )


def test_request_search_controller_decision_draft_accepts_budget_clamped_non_crossover_query() -> None:
    context = _context(
        node_query_term_pool=["agent engineer", "python", "workflow", "backend"],
        term_budget_range=(2, 4),
    )
    draft, audit = asyncio.run(
        request_search_controller_decision_draft(
            context,
            model=TestModel(
                custom_output_args={
                    "action": "search_cts",
                    "selected_operator_name": "core_precision",
                    "operator_args": {"additional_terms": ["ranking"]},
                    "expected_gain_hypothesis": "Keep the current core query intact.",
                }
            ),
        )
    )

    normalized = generate_search_controller_decision(context, draft)

    assert normalized.operator_args == {"additional_terms": []}
    assert audit.validator_retry_count == 0
