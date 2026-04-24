from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Any

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.dimension import Dimension
from rich.console import Console

from seektalent.models import FinalCandidate, FinalResult
from seektalent.progress import ProgressEvent


def _console(width: int = 80) -> Console:
    return Console(record=True, width=width, force_terminal=False, color_system=None)


def _answering_ask(answers: list[str]):
    remaining = iter(answers)

    def ask(prompt_text: str) -> str:
        del prompt_text
        return next(remaining)

    return ask


def test_print_intro_matches_051_header_and_job_title_prompt() -> None:
    from seektalent.tui import _print_intro

    console = _console()

    _print_intro(console, Path("/tmp/project"))

    rendered = console.export_text()
    assert "╭" in rendered
    assert "╰" in rendered
    assert ">_ SeekTalent" in rendered
    assert "mode:" in rendered
    assert "interactive candidate search" in rendered
    assert "cwd:" in rendered
    assert "/tmp/project" in rendered
    assert rendered.count("Paste Job Title.") == 1
    assert "Paste JD." not in rendered


def test_build_prompt_uses_non_fullscreen_composer(monkeypatch) -> None:
    import seektalent.tui as tui

    captured: dict[str, Any] = {}

    class FakeApplication:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def run(self) -> str:
            return "typed text"

    monkeypatch.setattr(tui, "Application", FakeApplication)

    prompt = tui._build_prompt()

    assert prompt("› ") == "typed text"
    assert captured["full_screen"] is False
    assert captured["erase_when_done"] is True


def test_build_composer_window_keeps_prefix_and_buffer() -> None:
    from seektalent.tui import _build_composer_window

    buffer = Buffer(multiline=True)

    window = _build_composer_window(buffer, "› ")

    assert isinstance(window.content, BufferControl)
    assert window.content.buffer is buffer
    assert isinstance(window.height, Dimension)
    assert window.get_line_prefix is not None
    assert window.height.min == 3
    assert window.height.preferred == 3
    assert window.get_line_prefix(0, 0) == "› "
    assert window.get_line_prefix(1, 0) == "  "


def test_run_chat_session_prints_natural_transcript_without_duplicate_title_or_empty_notes() -> None:
    from seektalent.api import MatchRunResult
    from seektalent.tui import run_chat_session

    console = _console()

    async def fake_run_search(**kwargs):
        assert kwargs["job_title"] == "AI Agent开发工程师"
        assert kwargs["jd"] == "职位描述\nAI Agent JD"
        assert kwargs["notes"] == ""
        return MatchRunResult(
            final_result=FinalResult(
                run_id="run-1",
                run_dir="/tmp/run-1",
                rounds_executed=1,
                stop_reason="controller_stop",
                summary="共推荐 0 位候选人。",
                candidates=[],
            ),
            final_markdown="# result",
            run_id="run-1",
            run_dir=Path("/tmp/run-1"),
            trace_log_path=Path("/tmp/run-1/trace.log"),
            evaluation_result=None,
            terminal_stop_guidance=None,
        )

    exit_code = run_chat_session(
        ask=_answering_ask(["AI Agent开发工程师", "职位描述\nAI Agent JD", ""]),
        console=console,
        run_search=fake_run_search,
        cwd=Path("/tmp/project"),
    )

    rendered = console.export_text()
    assert exit_code == 0
    assert rendered.count("Paste Job Title.") == 1
    assert rendered.count("Paste JD.") == 1
    assert rendered.count("AI Agent开发工程师") == 1
    assert rendered.count("职位描述") == 1
    assert rendered.count("AI Agent JD") == 1
    assert "› 职位描述\n  AI Agent JD" in rendered
    assert rendered.count("Paste Notes (optional). Press Enter to skip.") == 1
    assert "\nJob Title\n" not in rendered
    assert "\nJD\n" not in rendered
    assert "\nNotes\n" not in rendered
    assert "最终结果" in rendered
    assert "Session complete. Re-run seektalent to start a new session." in rendered


def test_run_chat_session_prints_notes_once_when_present() -> None:
    from seektalent.api import MatchRunResult
    from seektalent.tui import run_chat_session

    console = _console()

    async def fake_run_search(**kwargs):
        assert kwargs["notes"] == "source: benchmark"
        return MatchRunResult(
            final_result=FinalResult(
                run_id="run-1",
                run_dir="/tmp/run-1",
                rounds_executed=1,
                stop_reason="controller_stop",
                summary="共推荐 0 位候选人。",
                candidates=[],
            ),
            final_markdown="# result",
            run_id="run-1",
            run_dir=Path("/tmp/run-1"),
            trace_log_path=Path("/tmp/run-1/trace.log"),
            evaluation_result=None,
            terminal_stop_guidance=None,
        )

    run_chat_session(
        ask=_answering_ask(["AI Agent开发工程师", "JD", "source: benchmark"]),
        console=console,
        run_search=fake_run_search,
        cwd=Path("/tmp/project"),
    )

    rendered = console.export_text()
    assert rendered.count("Paste Notes (optional). Press Enter to skip.") == 1
    assert rendered.count("source: benchmark") == 1
    assert "› source: benchmark" in rendered
    assert "\nNotes\n" not in rendered


def test_prompt_submission_prints_full_multiline_jd_before_next_prompt() -> None:
    from seektalent.api import MatchRunResult
    from seektalent.tui import run_chat_session

    stream = StringIO()
    console = Console(file=stream, width=80, force_terminal=False, color_system=None)
    answers = iter(["AI Agent开发工程师", "第一行\n第二行\n第三行\n第四行\n最后一行", ""])

    def ask(prompt_text: str) -> str:
        del prompt_text
        return next(answers)

    async def fake_run_search(**kwargs):
        return MatchRunResult(
            final_result=FinalResult(
                run_id="run-1",
                run_dir="/tmp/run-1",
                rounds_executed=1,
                stop_reason="controller_stop",
                summary="共推荐 0 位候选人。",
                candidates=[],
            ),
            final_markdown="# result",
            run_id="run-1",
            run_dir=Path("/tmp/run-1"),
            trace_log_path=Path("/tmp/run-1/trace.log"),
            evaluation_result=None,
            terminal_stop_guidance=None,
        )

    run_chat_session(
        ask=ask,
        console=console,
        run_search=fake_run_search,
        cwd=Path("/tmp/project"),
    )

    rendered = stream.getvalue()
    assert "› 第一行\n  第二行\n  第三行\n  第四行\n  最后一行" in rendered
    assert "最后一行Paste Notes" not in rendered


def test_shimmer_status_uses_faster_20hz_live(monkeypatch) -> None:
    import seektalent.tui as tui
    from seektalent.tui import _ShimmerStatus

    captured: dict[str, Any] = {}

    class FakeLive:
        def __init__(self, renderable, *, console, refresh_per_second, transient) -> None:
            captured["renderable"] = renderable
            captured["console"] = console
            captured["refresh_per_second"] = refresh_per_second
            captured["transient"] = transient
            captured["started"] = False
            captured["updates"] = []
            captured["stopped"] = False

        def start(self) -> None:
            captured["started"] = True

        def update(self, renderable) -> None:
            captured["updates"].append(renderable.text)

        def stop(self) -> None:
            captured["stopped"] = True

    monkeypatch.setattr(tui, "Live", FakeLive)
    status = _ShimmerStatus(_console())

    status.start("业务 trace 等待第一步输出")
    status.set("controller 正在思考：第 1 轮策略判断")
    status.stop()

    assert captured["refresh_per_second"] == 20
    assert captured["transient"] is False
    assert captured["started"] is True
    assert captured["updates"] == ["controller 正在思考：第 1 轮策略判断"]
    assert captured["stopped"] is True


def test_shimmer_line_uses_fast_speed_and_051_visual_shape(monkeypatch) -> None:
    import seektalent.tui as tui
    from seektalent.tui import SHIMMER_CHARS_PER_SECOND, SHIMMER_HIGHLIGHT_WIDTH, _ShimmerLine

    monkeypatch.setattr(tui.time, "monotonic", lambda: 0.25)

    rendered = _ShimmerLine("业务 trace 等待第一步输出").__rich__()

    assert SHIMMER_CHARS_PER_SECOND == 24
    assert SHIMMER_HIGHLIGHT_WIDTH == 4
    assert rendered.plain == "业务 trace 等待第一步输出"
    assert len([span for span in rendered.spans if "bold white" in str(span.style)]) == 4
    assert "/" not in rendered.plain


def test_round_completed_progress_is_business_summary_first() -> None:
    from seektalent.tui import _render_progress_lines

    event = ProgressEvent(
        type="round_completed",
        message="round 2 completed",
        round_no=2,
        payload={
            "query_terms": ["python", "推荐系统"],
            "executed_queries": [
                {"query_role": "exploit", "query_terms": ["python", "推荐系统"]},
                {"query_role": "explore", "query_terms": ["python", "搜索架构"]},
            ],
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
            "reflection_rationale": "新增候选人覆盖了核心 Python 能力，但推荐系统证据仍不足，下一轮需要补搜索架构方向。",
        },
    )

    lines = _render_progress_lines(event)
    rendered = "\n".join(lines)

    assert lines[0].startswith("[bold]第 2 轮")
    assert "检索词：" in rendered
    assert "- 主检索：python、推荐系统" in rendered
    assert "- 探索检索：python、搜索架构" in rendered
    assert "本轮搜到 12 人，新增 7 人，5 人进入评分" in rendered
    assert "fit 3 / not_fit 2" in rendered
    assert "top pool：新增 2，保留 6，移出 1" in rendered
    assert "代表候选人" in rendered
    assert "r1 · 92 分" in rendered
    assert "本轮反思：本轮关键词有效" in rendered
    assert "反思理由：新增候选人覆盖了核心 Python 能力" in rendered


def test_search_progress_shows_dual_query_routes() -> None:
    from seektalent.tui import _render_progress_lines

    started = ProgressEvent(
        type="search_started",
        message='第 2 轮开始检索："AI Agent" LangGraph',
        round_no=2,
        payload={
            "planned_queries": [
                {"query_role": "exploit", "query_terms": ["AI Agent", "LangGraph"]},
                {"query_role": "explore", "query_terms": ["AI Agent", "AutoGen"]},
            ]
        },
    )
    completed = ProgressEvent(
        type="search_completed",
        message="第 2 轮检索完成：搜到 17 人，新增 9 人。",
        round_no=2,
        payload={
            "executed_queries": [
                {"query_role": "exploit", "query_terms": ["AI Agent", "LangGraph"]},
                {"query_role": "explore", "query_terms": ["AI Agent", "AutoGen"]},
            ],
            "raw_candidate_count": 17,
            "unique_new_count": 9,
        },
    )

    started_text = "\n".join(_render_progress_lines(started))
    completed_text = "\n".join(_render_progress_lines(completed))

    assert '· 第 2 轮开始检索：' in started_text
    assert "- 主检索：AI Agent、LangGraph" in started_text
    assert "- 探索检索：AI Agent、AutoGen" in started_text
    assert "· 第 2 轮检索完成：搜到 17 人，新增 9 人。" in completed_text
    assert "- 主检索：AI Agent、LangGraph" in completed_text
    assert "- 探索检索：AI Agent、AutoGen" in completed_text


def test_tui_renders_candidate_feedback_rescue() -> None:
    from seektalent.tui import _render_progress_lines

    lines = _render_progress_lines(
        ProgressEvent(
            type="rescue_lane_completed",
            message="Recall repair: extracted feedback term LangGraph from 3 fit seed resumes.",
            payload={
                "stage": "rescue",
                "selected_lane": "candidate_feedback",
                "accepted_term": "LangGraph",
                "seed_resume_count": 3,
            },
        )
    )

    rendered = "\n".join(lines)
    assert "召回修复：从 3 位高匹配候选人中提取扩展词：LangGraph" in rendered


def test_tui_renders_web_company_discovery_rescue() -> None:
    from seektalent.tui import _render_progress_lines

    lines = _render_progress_lines(
        ProgressEvent(
            type="company_discovery_completed",
            message="Target company discovery completed.",
            payload={
                "stage": "company_discovery",
                "search_result_count": 118,
                "reranked_result_count": 8,
                "opened_page_count": 6,
                "accepted_company_count": 5,
                "accepted_companies": ["火山引擎", "腾讯云"],
                "holdout_companies": ["某咨询公司"],
                "rejected_companies": ["无关报道来源"],
                "search_queries": [
                    "大模型平台 推理服务 GPU Kubernetes 招聘 公司",
                    "中国 AI Infra 大模型基础设施 公司",
                ],
                "reranked_pages": ["0.91 火山引擎大模型服务平台"],
                "page_titles": ["AI infra company map"],
                "next_query_terms": ["python", "火山引擎"],
            },
        )
    )

    rendered = "\n".join(lines)
    assert "目标公司发现：找到 118 个网页，重排 8 个，阅读 6 页，接受 5 家。" in rendered
    assert "搜索：" in rendered
    assert "大模型平台 推理服务" in rendered
    assert "重排页面：" in rendered
    assert "0.91 火山引擎大模型服务平台" in rendered
    assert "阅读页面：" in rendered
    assert "AI infra company map" in rendered
    assert "目标公司：火山引擎、腾讯云" in rendered
    assert "观察：某咨询公司" in rendered
    assert "排除：无关报道来源" in rendered
    assert "下一轮公司检索：python、火山引擎" in rendered


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


def test_progress_lines_match_051_bullet_style_with_quality_events() -> None:
    from seektalent.tui import _render_progress_lines

    event_types = [
        "requirements_completed",
        "controller_completed",
        "search_started",
        "search_completed",
        "scoring_started",
        "scoring_completed",
        "resume_quality_comment_completed",
        "resume_quality_comment_failed",
        "reflection_completed",
        "finalizer_completed",
    ]

    for event_type in event_types:
        assert _render_progress_lines(ProgressEvent(type=event_type, message="message", payload={})) == [
            "[dim]· message[/]"
        ]


def test_quality_comment_failure_progress_shows_error_reason() -> None:
    from seektalent.tui import _render_progress_lines

    lines = _render_progress_lines(
        ProgressEvent(
            type="resume_quality_comment_failed",
            message="本轮简历质量短评生成失败，已继续 reflection。",
            payload={"error": "ModelHTTPError: status_code: 404, model_name: deepseek-chat"},
        )
    )

    rendered = "\n".join(lines)
    assert "本轮简历质量短评生成失败，已继续 reflection。" in rendered
    assert "原因：" in rendered
    assert "deepseek-chat" in rendered


def test_thinking_progress_uses_blinking_status_line() -> None:
    from seektalent.tui import _render_progress_lines

    event = ProgressEvent(
        type="controller_started",
        message="Planning round 3 action.",
        round_no=3,
        payload={"stage": "controller"},
    )

    assert _render_progress_lines(event) == ["[dim][blink]controller 正在思考：第 3 轮策略判断[/][/]"]


def test_run_completed_progress_is_static_not_blinking() -> None:
    from seektalent.tui import _render_progress_lines

    lines = _render_progress_lines(
        ProgressEvent(
            type="run_completed",
            message="Run completed after 3 retrieval rounds; controller stopped in round 4.",
            payload={},
        )
    )

    assert lines == ["[dim]业务 trace 完成：Run completed after 3 retrieval rounds; controller stopped in round 4.[/]"]


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
        terminal_stop_guidance=None,
    )

    rendered = _result_message(result)

    assert "最终结果" in rendered
    assert "共推荐 1 位候选人" in rendered
    assert "1. resume-1 · 91 分 · fit · 第 2 轮" in rendered
    assert "匹配点：Python、检索" in rendered
    assert "风险：最近一段经历略短" in rendered
