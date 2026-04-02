from __future__ import annotations

from pathlib import Path

import pytest

from cv_match.config import AppSettings
from cv_match.controller.react_controller import ReActController
from cv_match.finalize.finalizer import Finalizer
from cv_match.models import (
    ControllerStateView,
    SearchObservation,
    SearchObservationView,
    SearchStrategy,
)
from cv_match.prompting import LoadedPrompt
from cv_match.reflection.critic import ReflectionCritic


def _prompt(name: str) -> LoadedPrompt:
    return LoadedPrompt(name=name, path=Path(f"{name}.md"), content=f"{name} prompt", sha256="hash")


def _settings(monkeypatch: pytest.MonkeyPatch) -> AppSettings:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    return AppSettings()


def _strategy() -> SearchStrategy:
    return SearchStrategy(
        must_have_keywords=["python"],
        preferred_keywords=["trace"],
        negative_keywords=[],
        hard_filters=[],
        soft_filters=[],
        search_rationale="test strategy",
    )


def _state_view() -> ControllerStateView:
    return ControllerStateView(
        round_no=1,
        min_rounds=1,
        max_rounds=3,
        target_new=10,
        jd_summary="jd",
        notes_summary="notes",
        current_strategy=_strategy(),
        current_top_pool=[],
        latest_search_observation=SearchObservationView(
            unique_new_count=0,
            shortage_count=0,
            fetch_attempt_count=0,
            exhausted_reason=None,
            new_candidate_summaries=[],
            adapter_notes=[],
        ),
        previous_reflection=None,
        shortage_history=[],
        consecutive_shortage_rounds=0,
        tool_capability_notes=[],
    )


def test_controller_decide_raises_live_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    controller = ReActController(_settings(monkeypatch), _prompt("controller"))

    async def boom(*, state_view):  # noqa: ARG001
        raise RuntimeError("controller boom")

    controller._decide_live = boom  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="controller boom"):
        controller.decide(state_view=_state_view())


def test_reflection_reflect_raises_live_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    critic = ReflectionCritic(_settings(monkeypatch), _prompt("reflection"))

    async def boom(**kwargs):  # noqa: ARG001
        raise RuntimeError("reflection boom")

    critic._reflect_live = boom  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="reflection boom"):
        critic.reflect(
            round_no=1,
            strategy=_strategy(),
            search_observation=SearchObservation(
                round_no=1,
                requested_count=10,
                raw_candidate_count=0,
                unique_new_count=0,
                shortage_count=10,
                fetch_attempt_count=1,
                exhausted_reason="cts_exhausted",
                new_resume_ids=[],
                new_candidate_summaries=[],
                adapter_notes=[],
            ),
            search_attempts=[],
            new_candidate_summaries=[],
            scored_candidates=[],
            top_candidates=[],
            dropped_candidates=[],
            shortage_count=10,
            scoring_failure_count=0,
        )


def test_finalizer_uses_live_path_and_raises_for_empty_ranked_list(monkeypatch: pytest.MonkeyPatch) -> None:
    finalizer = Finalizer(_settings(monkeypatch), _prompt("finalize"))

    async def boom(**kwargs):  # noqa: ARG001
        raise RuntimeError("finalizer boom")

    finalizer._finalize_live = boom  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="finalizer boom"):
        finalizer.finalize(
            run_id="run-1",
            run_dir="/tmp/run-1",
            rounds_executed=1,
            stop_reason="controller_stop",
            ranked_candidates=[],
        )
