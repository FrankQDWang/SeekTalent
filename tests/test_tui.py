from __future__ import annotations

from pathlib import Path

from seektalent.models import FinalCandidate, FinalResult
from seektalent.progress import ProgressEvent


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
