from __future__ import annotations

import asyncio
from types import SimpleNamespace

from textual.widgets import TextArea

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
                    gap_signals=[SimpleNamespace(display_text="Only weak evidence for retrieval", signal="gap")],
                    risk_signals=[],
                    card_summary="Advance: explicit coverage on 4/5 must-haves",
                ),
                SimpleNamespace(
                    candidate_id="c-2",
                    review_recommendation="hold",
                    must_have_matrix=[],
                    preferred_evidence=[],
                    gap_signals=[],
                    risk_signals=[SimpleNamespace(display_text="Below minimum years of experience", signal="risk")],
                    card_summary="Hold: explicit coverage on 2/5 must-haves",
                ),
            ],
            reviewer_summary="1 advance-ready, 1 need manual review, 0 reject",
            run_summary="Strong first shortlist with one remaining gap cluster.",
            stop_reason="controller_stop",
        ),
    )


def test_textual_app_starts_with_chat_prompt() -> None:
    async def _exercise() -> None:
        app = SeekTalentApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            composer = app.query_one("#composer", TextArea)
            assert app.phase == "awaiting_jd"
            assert composer.disabled is False
            transcript = app.transcript_text()
            assert "assistant" in transcript
            assert "Job Description" in transcript
            assert "Shift+Enter" in transcript

    asyncio.run(_exercise())


def test_enter_inserts_newline_without_submitting() -> None:
    async def _exercise() -> None:
        app = SeekTalentApp()
        async with app.run_test() as pilot:
            composer = app.query_one("#composer", TextArea)
            composer.text = "JD"
            await pilot.press("enter")
            await pilot.pause()
            assert app.phase == "awaiting_jd"
            assert composer.text == "\nJD"

    asyncio.run(_exercise())


def test_shift_enter_drives_jd_then_notes_then_run(monkeypatch) -> None:
    async def fake_run_match_async(**kwargs):
        kwargs["progress_callback"](
            make_progress_event(
                "controller_decision",
                "controller: selected core_precision",
                round_index=0,
            )
        )
        kwargs["progress_callback"](
            make_progress_event(
                "rerank_completed",
                "rerank: built 2 candidate cards",
                round_index=0,
            )
        )
        return _fake_bundle()

    monkeypatch.setattr("seektalent.tui.run_match_async", fake_run_match_async)

    async def _exercise() -> None:
        app = SeekTalentApp()
        async with app.run_test() as pilot:
            composer = app.query_one("#composer", TextArea)
            composer.text = "JD text"
            await pilot.press("shift+enter")
            await pilot.pause()
            assert app.phase == "awaiting_notes"
            assert "Hiring Notes" in app.transcript_text()

            composer.text = ""
            await pilot.press("shift+enter")
            await pilot.pause()
            await pilot.pause()

            assert app.phase == "completed"
            assert composer.disabled is True
            transcript = app.transcript_text()
            assert "controller: selected core_precision" in transcript
            assert "rerank: built 2 candidate cards" in transcript
            assert "reviewer_summary: 1 advance-ready, 1 need manual review, 0 reject" in transcript
            assert "c-1 | advance" in transcript
            assert "gaps: Only weak evidence for retrieval" in transcript
            assert app.status_text == "Session complete. Re-run seektalent to start a new session."

    asyncio.run(_exercise())
