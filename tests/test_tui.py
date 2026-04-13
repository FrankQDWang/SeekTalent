from __future__ import annotations

import asyncio
from types import SimpleNamespace

from textual.widgets import Static

from seektalent.progress import make_progress_event
from seektalent.tui import SeekTalentApp


def _fake_bundle() -> object:
    return SimpleNamespace(
        run_dir="/tmp/runs/test",
        final_result=SimpleNamespace(
            final_candidate_cards=[
                SimpleNamespace(
                    candidate_id="c-1",
                    review_recommendation="advance",
                    must_have_matrix=[],
                    preferred_evidence=[],
                    gap_signals=[],
                    risk_signals=[],
                    card_summary="Advance",
                ),
                SimpleNamespace(
                    candidate_id="c-2",
                    review_recommendation="hold",
                    must_have_matrix=[],
                    preferred_evidence=[],
                    gap_signals=[],
                    risk_signals=[],
                    card_summary="Hold",
                ),
            ],
            reviewer_summary="Reviewer summary: 1 advance-ready, 1 need manual review, 0 reject",
            run_summary="Ready for review.",
            stop_reason="controller_stop",
        ),
    )


def test_textual_app_runs_search_and_renders_results() -> None:
    async def _exercise() -> None:
        app = SeekTalentApp()
        async with app.run_test() as pilot:
            bundle = _fake_bundle()
            app._record_progress(
                make_progress_event(
                    "controller_decision",
                    "controller: selected core_precision",
                    round_index=0,
                )
            )
            app.current_bundle = bundle
            app._render_bundle(bundle)
            await pilot.pause()
            assert app.current_bundle is not None
            assert sorted(app.cards_by_candidate_id) == ["c-1", "c-2"]
            summary = app.query_one("#summary", Static).render()
            assert "run_dir: /tmp/runs/test" in str(summary)
            detail = app.query_one("#candidate-detail", Static).render()
            assert "candidate_id: c-1" in str(detail)

    asyncio.run(_exercise())
