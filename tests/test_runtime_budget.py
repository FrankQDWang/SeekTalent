from __future__ import annotations

from seektalent.models import RuntimeSearchBudget
from seektalent.runtime_budget import (
    build_runtime_budget_state,
    resolve_runtime_search_budget,
)


def test_resolve_runtime_search_budget_clamps_requested_value() -> None:
    low = resolve_runtime_search_budget(RuntimeSearchBudget(initial_round_budget=5), 3)
    high = resolve_runtime_search_budget(RuntimeSearchBudget(initial_round_budget=5), 20)

    assert low.initial_round_budget == 5
    assert high.initial_round_budget == 12


def test_build_runtime_budget_state_marks_near_budget_end_from_used_ratio() -> None:
    early = build_runtime_budget_state(
        initial_round_budget=10,
        runtime_round_index=1,
        remaining_budget=9,
    )
    late = build_runtime_budget_state(
        initial_round_budget=10,
        runtime_round_index=8,
        remaining_budget=2,
    )

    assert early.used_ratio == 0.1
    assert early.near_budget_end is False
    assert early.phase_progress == 1 / 9
    assert early.search_phase == "explore"
    assert late.used_ratio == 0.8
    assert late.remaining_ratio == 0.2
    assert late.phase_progress == 8 / 9
    assert late.search_phase == "harvest"
    assert late.near_budget_end is True


def test_build_runtime_budget_state_uses_expected_five_round_phase_sequence() -> None:
    phases = [
        build_runtime_budget_state(
            initial_round_budget=5,
            runtime_round_index=round_index,
            remaining_budget=max(0, 5 - round_index),
        ).search_phase
        for round_index in range(5)
    ]

    assert phases == ["explore", "explore", "balance", "harvest", "harvest"]


def test_build_runtime_budget_state_uses_expected_twelve_round_phase_sequence() -> None:
    phases = [
        build_runtime_budget_state(
            initial_round_budget=12,
            runtime_round_index=round_index,
            remaining_budget=max(0, 12 - round_index),
        ).search_phase
        for round_index in range(12)
    ]

    assert phases == [
        "explore",
        "explore",
        "explore",
        "explore",
        "balance",
        "balance",
        "balance",
        "balance",
        "harvest",
        "harvest",
        "harvest",
        "harvest",
    ]


def test_build_runtime_budget_state_switches_phase_at_fixed_boundaries() -> None:
    explore = build_runtime_budget_state(
        initial_round_budget=101,
        runtime_round_index=33,
        remaining_budget=68,
    )
    balance = build_runtime_budget_state(
        initial_round_budget=101,
        runtime_round_index=34,
        remaining_budget=67,
    )
    harvest = build_runtime_budget_state(
        initial_round_budget=101,
        runtime_round_index=67,
        remaining_budget=34,
    )

    assert explore.phase_progress == 0.33
    assert explore.search_phase == "explore"
    assert balance.phase_progress == 0.34
    assert balance.search_phase == "balance"
    assert harvest.phase_progress == 0.67
    assert harvest.search_phase == "harvest"
