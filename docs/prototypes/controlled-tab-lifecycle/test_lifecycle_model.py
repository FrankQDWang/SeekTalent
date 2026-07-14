from __future__ import annotations

import pytest

from lifecycle_model import initial_state, reduce_state


def test_diagnostics_and_reclaim_failures_do_not_change_business_results() -> None:
    state = reduce_state(
        initial_state(),
        {
            "kind": "business_result",
            "source": "liepin",
            "result": {"status": "completed", "candidateCount": 1},
        },
    )
    state = reduce_state(
        state,
        {
            "kind": "diagnostic",
            "code": "exact_close_failed",
            "scopeId": "scope-a",
            "tabToken": "tab-a",
        },
    )

    assert state["businessResults"]["liepin"] == {
        "status": "completed",
        "candidateCount": 1,
    }


def test_scope_can_own_more_than_two_tabs() -> None:
    state = initial_state()
    for token in ("tab-a", "tab-b", "tab-c"):
        state = reduce_state(
            state,
            {
                "kind": "tab_created",
                "scopeId": "scope-a",
                "tabToken": token,
                "tabKind": "detail",
            },
        )

    assert set(state["tabs"]) == {"tab-a", "tab-b", "tab-c"}


def test_unknown_event_fails_explicitly() -> None:
    with pytest.raises(ValueError, match="unknown prototype event"):
        reduce_state(initial_state(), {"kind": "surprise"})
