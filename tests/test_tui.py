from __future__ import annotations

import asyncio
from types import SimpleNamespace

from textual.widgets import DataTable, Static, TextArea

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


def test_textual_app_runs_search_and_renders_results(monkeypatch) -> None:
    async def _exercise() -> None:
        async def fake_run_match_async(**kwargs):
            kwargs["progress_callback"](
                make_progress_event(
                    "controller_decision",
                    "controller: selected core_precision",
                    round_index=0,
                )
            )
            await asyncio.sleep(0)
            return _fake_bundle()

        monkeypatch.setattr("seektalent.tui.run_match_async", fake_run_match_async)

        app = SeekTalentApp()
        async with app.run_test():
            app.query_one("#job-description", TextArea).text = "JD"
            app.query_one("#hiring-notes", TextArea).text = "Notes"
            await app._run_search()
            table = app.query_one("#candidate-table", DataTable)
            assert table.row_count == 2
            summary = app.query_one("#summary", Static).render()
            assert "run_dir: /tmp/runs/test" in str(summary)
            detail = app.query_one("#candidate-detail", Static).render()
            assert "candidate_id: c-1" in str(detail)

    asyncio.run(_exercise())
