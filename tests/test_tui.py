from __future__ import annotations

from io import StringIO
from types import SimpleNamespace

from prompt_toolkit.buffer import Buffer
from rich.console import Console

from seektalent.progress import make_progress_event
from seektalent.tui import COMPOSER_MIN_LINES, _build_composer_window, run_chat_session


def _fake_bundle() -> object:
    return SimpleNamespace(
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


def _rendered_text(console: Console, stream: StringIO) -> str:
    console.file.flush()
    return stream.getvalue()


def test_chat_session_starts_with_codex_like_intro() -> None:
    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None)
    prompts: list[str] = []

    def fake_ask(prompt_text: str) -> str:
        prompts.append(prompt_text)
        return "JD"

    async def fake_run_search(**kwargs):
        return _fake_bundle()

    assert run_chat_session(ask=fake_ask, console=console, run_search=fake_run_search) == 0
    output = _rendered_text(console, stream)
    assert ">_ SeekTalent" in output
    assert "Paste Job Description." in output
    assert "Job Description" in output
    assert "\nJD\n" in output
    assert "Ctrl+J" in output
    assert prompts == ["› ", "› "]


def test_composer_window_keeps_fixed_min_height() -> None:
    window = _build_composer_window(Buffer(multiline=True), "› ")
    assert window.height.min == COMPOSER_MIN_LINES
    assert window.height.preferred == COMPOSER_MIN_LINES


def test_empty_jd_reprompts_before_running() -> None:
    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None)
    answers = iter(["", "JD", ""])

    def fake_ask(prompt_text: str) -> str:
        return next(answers)

    async def fake_run_search(**kwargs):
        return _fake_bundle()

    assert run_chat_session(ask=fake_ask, console=console, run_search=fake_run_search) == 0
    output = _rendered_text(console, stream)
    assert "Job Description cannot be empty." in output
    assert "Paste Hiring Notes" in output
    assert "(optional)" in output
    assert "\nJD\n" in output


def test_chat_session_streams_progress_and_final_result() -> None:
    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None)
    answers = iter(["JD text", ""])

    def fake_ask(prompt_text: str) -> str:
        return next(answers)

    async def fake_run_search(**kwargs):
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

    assert run_chat_session(ask=fake_ask, console=console, run_search=fake_run_search) == 0
    output = _rendered_text(console, stream)
    assert "JD text" in output
    assert "Working" in output
    assert "· controller: selected core_precision" in output
    assert "· rerank: built 2 candidate cards" in output
    assert "Final result" in output
    assert "1 advance-ready, 1 need manual review, 0 reject" in output
    assert "1. c-1 · advance" in output
    assert "gap: Only weak evidence for retrieval" in output
    assert "Session complete. Re-run seektalent to start a new session." in output


def test_chat_session_surfaces_specific_failure_reason() -> None:
    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None)
    answers = iter(["JD text", ""])

    def fake_ask(prompt_text: str) -> str:
        return next(answers)

    async def fake_run_search(**kwargs):
        raise RuntimeError(
            "branch_evaluation_output_invalid: branch_evaluation requires branch_exhausted=true when node_shortlist_candidate_ids is empty"
        )

    assert run_chat_session(ask=fake_ask, console=console, run_search=fake_run_search) == 1
    output = _rendered_text(console, stream)
    assert "JD text" in output
    assert "Failed" in output
    assert "branch_evaluation_output_invalid:" in output


def test_chat_session_echoes_hiring_notes_when_provided() -> None:
    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None)
    answers = iter(["JD text", "Need startup experience"])

    def fake_ask(prompt_text: str) -> str:
        return next(answers)

    async def fake_run_search(**kwargs):
        return _fake_bundle()

    assert run_chat_session(ask=fake_ask, console=console, run_search=fake_run_search) == 0
    output = _rendered_text(console, stream)
    assert "Hiring Notes" in output
    assert "Need startup experience" in output
