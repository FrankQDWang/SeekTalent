from __future__ import annotations

from pathlib import Path

from prompt_toolkit.formatted_text import StyleAndTextTuples

from seektalent.models import FinalCandidate, FinalResult
from seektalent.progress import ProgressEvent


async def _fake_run_search(**kwargs):
    del kwargs
    raise AssertionError("run_search should not be called by construction tests")


def _fragment_text(fragments: StyleAndTextTuples) -> str:
    return "".join(str(fragment[1]) for fragment in fragments)


def test_tui_session_uses_fullscreen_application() -> None:
    from seektalent.tui import TuiSession

    session = TuiSession(run_search=_fake_run_search, cwd=Path("/tmp"))

    assert session.app.full_screen is True


def test_tui_session_renders_visual_header_box() -> None:
    from seektalent.tui import TuiSession

    session = TuiSession(run_search=_fake_run_search, cwd=Path("/tmp/project"))

    rendered = _fragment_text(session.header_fragments())

    assert "╭" in rendered
    assert "╰" in rendered
    assert ">_ SeekTalent" in rendered
    assert "mode:" in rendered
    assert "interactive candidate search" in rendered
    assert "cwd:" in rendered
    assert "/tmp/project" in rendered


def test_tui_session_input_prompt_keeps_composer_shortcuts() -> None:
    from seektalent.tui import TuiSession

    session = TuiSession(run_search=_fake_run_search, cwd=Path("/tmp"))

    rendered = _fragment_text(session.input_label_fragments())

    assert "Paste Job Title." in rendered
    assert "Enter submit" in rendered
    assert "Ctrl+J newline" in rendered
    assert "Ctrl+C quit" in rendered


def test_tui_session_keeps_status_hidden_before_search_starts() -> None:
    from seektalent.tui import TuiSession

    session = TuiSession(run_search=_fake_run_search, cwd=Path("/tmp"))

    assert session.state.status_text == ""
    assert session.status_fragments() == []
    assert session.app.refresh_interval is None


def test_tui_session_renders_input_errors_without_shimmer() -> None:
    from seektalent.tui import TuiSession

    session = TuiSession(run_search=_fake_run_search, cwd=Path("/tmp"))
    session.state.status_text = "Job Title cannot be empty."

    assert session.status_fragments() == [("class:status", "Job Title cannot be empty.")]


def test_tui_session_submission_clears_input_buffer() -> None:
    from seektalent.tui import TuiSession

    session = TuiSession(run_search=_fake_run_search, cwd=Path("/tmp"))
    session.buffer.text = "AI agent开发"

    session.submit_current_input(view_height=10)

    assert session.state.input_step == "jd"
    assert session.buffer.text == ""
    assert "\n".join(session.state.transcript_lines).count("AI agent开发") == 1


def test_tui_session_progress_preserves_markup_and_renders_plain_text() -> None:
    from seektalent.tui import TuiSession

    session = TuiSession(run_search=_fake_run_search, cwd=Path("/tmp"))
    event = ProgressEvent(type="run_completed", message="done", payload={})

    session.handle_progress(event, app=session.app)

    assert any("[dim]" in line for line in session.state.transcript_lines)

    rendered = _fragment_text(session.transcript_fragments())
    assert "[dim]" not in rendered
    assert "[blink]" not in rendered
    assert "业务 trace 完成" in rendered


def test_transcript_fragments_render_supported_rich_markup() -> None:
    from seektalent.tui import TuiSession

    session = TuiSession(run_search=_fake_run_search, cwd=Path("/tmp"))
    session.state.transcript_lines = ["[bold]最终结果[/]", "[dim]· done[/]"]

    fragments = session.transcript_fragments()
    rendered = _fragment_text(fragments)

    assert "[bold]" not in rendered
    assert "[dim]" not in rendered
    assert "最终结果" in rendered
    assert "· done" in rendered
    assert ("class:transcript.bold", "最终结果") in fragments
    assert ("class:transcript.dim", "· done") in fragments


def test_transcript_fragments_keep_escaped_user_brackets() -> None:
    from seektalent.tui import TuiSession

    session = TuiSession(run_search=_fake_run_search, cwd=Path("/tmp"))
    session.state.submit_input("JD", "Use [literal] tags", view_height=10)

    rendered = _fragment_text(session.transcript_fragments())

    assert "Use [literal] tags" in rendered


def test_status_refresh_does_not_change_scroll_position() -> None:
    from seektalent.tui import TuiSession

    session = TuiSession(run_search=_fake_run_search, cwd=Path("/tmp"))
    session.state.input_step = "running"
    session.state.status_text = "业务 trace 等待第一步输出"
    session.state.append_lines([f"line {index}" for index in range(20)], view_height=5)
    session.state.scroll_to_bottom(view_height=5)
    session.state.scroll_up(3, view_height=5)
    before = session.state.scroll_offset

    session.status_fragments()

    assert session.state.follow is False
    assert session.state.scroll_offset == before


def test_transcript_state_stops_following_when_user_scrolls_up() -> None:
    from seektalent.tui import TuiState

    state = TuiState()
    state.append_lines([f"line {index}" for index in range(20)], view_height=5)
    state.scroll_to_bottom(view_height=5)
    state.scroll_up(3, view_height=5)
    before = state.scroll_offset

    state.append_lines(["new line"], view_height=5)

    assert state.follow is False
    assert state.scroll_offset == before


def test_transcript_state_resumes_following_near_bottom() -> None:
    from seektalent.tui import TuiState

    state = TuiState()
    state.append_lines([f"line {index}" for index in range(20)], view_height=5)
    state.scroll_to_bottom(view_height=5)
    state.scroll_up(5, view_height=5)

    state.scroll_down(4, view_height=5)

    assert state.follow is True


def test_submitted_inputs_render_once_in_transcript_state() -> None:
    from seektalent.tui import TuiState

    state = TuiState()
    state.submit_input("Job Title", "AI agent开发", view_height=10)

    assert "\n".join(state.transcript_lines).count("AI agent开发") == 1


def test_shimmer_config_is_faster() -> None:
    from seektalent.tui import SHIMMER_CHARS_PER_SECOND, SHIMMER_REFRESH_PER_SECOND

    assert SHIMMER_REFRESH_PER_SECOND >= 20
    assert SHIMMER_CHARS_PER_SECOND >= 20


def test_submitted_message_keeps_full_pasted_text() -> None:
    from seektalent.tui import _submitted_message

    text = "\n".join([f"Line {index}" for index in range(1, 7)])

    rendered = _submitted_message("JD", text)

    assert rendered.count("Line 1") == 1
    assert "Line 2\nLine 3\nLine 4\nLine 5\nLine 6" in rendered
    assert "完整回显" not in rendered


def test_round_completed_progress_is_business_summary_first() -> None:
    from seektalent.tui import _render_progress_lines

    event = ProgressEvent(
        type="round_completed",
        message="round 2 completed",
        round_no=2,
        payload={
            "query_terms": ["python", "推荐系统"],
            "raw_candidate_count": 12,
            "unique_new_count": 7,
            "newly_scored_count": 5,
            "fit_count": 3,
            "not_fit_count": 2,
            "top_pool_selected_count": 2,
            "top_pool_retained_count": 6,
            "top_pool_dropped_count": 1,
            "representative_candidates": [
                "r1 · 92 分 · 后端工程师 | 上海 | 6y · Python 与推荐系统匹配",
                "r2 · 84 分 · 算法工程师 | 杭州 | 4y · 有召回和排序经验",
            ],
            "reflection_summary": "本轮关键词有效，下一轮保留 python，增加搜索架构方向。",
        },
    )

    lines = _render_progress_lines(event)
    rendered = "\n".join(lines)

    assert lines[0].startswith("[bold]第 2 轮")
    assert "检索词：python、推荐系统" in rendered
    assert "本轮搜到 12 人，新增 7 人，5 人进入评分" in rendered
    assert "fit 3 / not_fit 2" in rendered
    assert "top pool：新增 2，保留 6，移出 1" in rendered
    assert "代表候选人" in rendered
    assert "r1 · 92 分" in rendered
    assert "本轮反思：本轮关键词有效" in rendered


def test_quality_comment_renders_after_candidates_before_reflection() -> None:
    from seektalent.tui import _render_progress_lines

    event = ProgressEvent(
        type="round_completed",
        message="round 2 completed",
        round_no=2,
        payload={
            "query_terms": ["python", "推荐系统"],
            "raw_candidate_count": 12,
            "unique_new_count": 7,
            "newly_scored_count": 5,
            "fit_count": 3,
            "not_fit_count": 2,
            "top_pool_selected_count": 2,
            "top_pool_retained_count": 6,
            "top_pool_dropped_count": 1,
            "representative_candidates": ["r1 · 92 分", "r2 · 84 分"],
            "resume_quality_comment": "整体相关度较高，但少数候选人 Agent 深度不足。",
            "reflection_summary": "本轮关键词有效，下一轮保留 python。",
        },
    )

    rendered = "\n".join(_render_progress_lines(event))

    assert rendered.index("代表候选人") < rendered.index("本轮简历质量")
    assert rendered.index("本轮简历质量") < rendered.index("本轮反思")


def test_quality_comment_failure_renders_before_reflection() -> None:
    from seektalent.tui import _render_progress_lines

    event = ProgressEvent(
        type="round_completed",
        message="round 2 completed",
        round_no=2,
        payload={
            "representative_candidates": ["r1 · 92 分"],
            "resume_quality_comment": None,
            "resume_quality_comment_error": "model failed",
            "reflection_summary": "继续扩大搜索。",
        },
    )

    rendered = "\n".join(_render_progress_lines(event))

    assert "本轮简历质量短评生成失败" in rendered
    assert rendered.index("本轮简历质量短评生成失败") < rendered.index("本轮反思")


def test_thinking_progress_uses_blinking_status_line() -> None:
    from seektalent.tui import _render_progress_lines

    event = ProgressEvent(
        type="controller_started",
        message="Planning round 3 action.",
        round_no=3,
        payload={"stage": "controller"},
    )

    assert _render_progress_lines(event) == ["[dim][blink]controller 正在思考：第 3 轮策略判断[/][/]"]


def test_result_message_lists_final_shortlist_for_business_review(tmp_path: Path) -> None:
    from seektalent.api import MatchRunResult
    from seektalent.tui import _result_message

    final_result = FinalResult(
        run_id="run-1",
        run_dir=str(tmp_path),
        rounds_executed=2,
        stop_reason="controller_stop",
        summary="共推荐 1 位候选人。",
        candidates=[
            FinalCandidate(
                resume_id="resume-1",
                rank=1,
                final_score=91,
                fit_bucket="fit",
                match_summary="后端搜索经验强，和岗位核心要求匹配。",
                strengths=["Python 后端", "推荐系统"],
                weaknesses=["管理经验不明"],
                matched_must_haves=["Python", "检索"],
                matched_preferences=["推荐系统"],
                risk_flags=["最近一段经历略短"],
                why_selected="must-have 覆盖充分，风险可控。",
                source_round=2,
            )
        ],
    )
    result = MatchRunResult(
        final_result=final_result,
        final_markdown="# result",
        run_id="run-1",
        run_dir=tmp_path,
        trace_log_path=tmp_path / "trace.log",
        evaluation_result=None,
    )

    rendered = _result_message(result)

    assert "最终结果" in rendered
    assert "共推荐 1 位候选人" in rendered
    assert "1. resume-1 · 91 分 · fit · 第 2 轮" in rendered
    assert "匹配点：Python、检索" in rendered
    assert "风险：最近一段经历略短" in rendered
