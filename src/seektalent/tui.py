from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable, Coroutine, Mapping
from pathlib import Path
from typing import Any, cast

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, Window
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.dimension import Dimension
from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.text import Text

from seektalent.api import MatchRunResult, run_match_async
from seektalent.progress import ProgressEvent

COMPOSER_MIN_LINES = 3
INTRO_BOX_MAX_WIDTH = 64
SHIMMER_REFRESH_PER_SECOND = 20
SHIMMER_CHARS_PER_SECOND = 24
SHIMMER_HIGHLIGHT_WIDTH = 4
PromptFn = Callable[[str], str]
RunSearchFn = Callable[..., Coroutine[Any, Any, MatchRunResult]]


def run_chat_session(
    *,
    ask: PromptFn | None = None,
    console: Console | None = None,
    run_search: RunSearchFn = run_match_async,
    cwd: Path | None = None,
) -> int:
    console = console or Console()
    prompt = ask or _build_prompt()
    try:
        _print_intro(console, cwd or Path.cwd())
        job_title = _read_required_text(console, prompt, label="Job Title")

        console.print("Paste [bold]JD[/].")
        console.print("[dim]Enter submit · Ctrl+J newline · Ctrl+C quit[/]")
        jd = _read_required_text(console, prompt, label="JD")

        console.print("Paste [bold]Notes[/] [dim](optional)[/]. Press [bold]Enter[/] to skip.")
        notes = _read_prompt_text(console, prompt).strip()

        status = _ShimmerStatus(console)
        status.start("业务 trace 等待第一步输出")
        try:
            result = asyncio.run(
                run_search(
                    job_title=job_title,
                    jd=jd,
                    notes=notes,
                    env_file=".env",
                    progress_callback=lambda event: _print_progress(console, event, status=status),
                )
            )
        finally:
            status.stop()
    except KeyboardInterrupt:
        console.print("\n[bold]Interrupted[/]")
        console.print("[dim]Re-run seektalent to start a new session.[/]")
        return 130
    except Exception as exc:  # noqa: BLE001
        console.print("[bold red]Failed[/]")
        console.print(escape(str(exc)))
        console.print("[dim]Session complete. Re-run seektalent to start a new session.[/]")
        return 1
    console.print()
    console.print(_result_message(result))
    console.print()
    console.print("[dim]Session complete. Re-run seektalent to start a new session.[/]")
    return 0


def _build_prompt() -> PromptFn:
    if os.environ.get("TERM") == "dumb":
        os.environ.setdefault("PROMPT_TOOLKIT_NO_CPR", "1")

    def _prompt(prompt_text: str) -> str:
        buffer = Buffer(multiline=True)
        app = Application(
            full_screen=False,
            erase_when_done=True,
            key_bindings=_composer_bindings(),
            layout=Layout(_build_composer_window(buffer, prompt_text)),
        )
        return app.run()

    return _prompt


def _composer_bindings() -> KeyBindings:
    bindings = KeyBindings()

    @bindings.add("enter")
    def _submit(event) -> None:
        event.app.exit(result=event.app.current_buffer.text)

    @bindings.add("c-j")
    def _newline(event) -> None:
        event.app.current_buffer.insert_text("\n")

    @bindings.add("c-c")
    def _interrupt(event) -> None:
        raise KeyboardInterrupt

    return bindings


def _build_composer_window(buffer: Buffer, prompt_text: str) -> Window:
    return Window(
        BufferControl(buffer=buffer),
        height=Dimension(min=COMPOSER_MIN_LINES, preferred=COMPOSER_MIN_LINES),
        wrap_lines=True,
        get_line_prefix=lambda line_number, wrap_count: prompt_text if line_number == 0 and wrap_count == 0 else "  ",
    )


def _print_intro(console: Console, cwd: Path) -> None:
    box_width = min(max(console.size.width - 4, 36), INTRO_BOX_MAX_WIDTH)
    content_width = box_width - 4
    cwd_text = _fit_text(str(cwd), content_width - 5)
    mode_text = _fit_text("interactive candidate search", content_width - 6)
    title_text = _fit_text(">_ SeekTalent", content_width)
    console.print(
        "\n".join(
            [
                f"[dim]╭{'─' * (box_width - 2)}╮[/]",
                f"[dim]│[/] [bold]{escape(title_text):<{content_width}}[/] [dim]│[/]",
                f"[dim]│[/] {'':<{content_width}} [dim]│[/]",
                f"[dim]│[/] [dim]mode:[/] {escape(mode_text):<{content_width - 6}} [dim]│[/]",
                f"[dim]│[/] [dim]cwd:[/]  {escape(cwd_text):<{content_width - 6}} [dim]│[/]",
                f"[dim]╰{'─' * (box_width - 2)}╯[/]",
                "",
                "Paste [bold]Job Title[/].",
                "[dim]Enter submit · Ctrl+J newline · Ctrl+C quit[/]",
            ]
        )
    )


def _fit_text(text: str, width: int) -> str:
    width = max(width, 8)
    if len(text) <= width:
        return text
    return f"...{text[-(width - 3):]}"


def _read_required_text(console: Console, prompt: PromptFn, *, label: str) -> str:
    while True:
        text = _read_prompt_text(console, prompt)
        if text.strip():
            return text.strip()
        console.print(f"{escape(label)} cannot be empty. Paste it and press [bold]Enter[/].")


def _read_prompt_text(console: Console, prompt: PromptFn) -> str:
    text = prompt("› ").rstrip()
    if text:
        console.print(_submitted_prompt_text("› ", text))
    console.print()
    return text


def _submitted_prompt_text(prompt_text: str, text: str) -> str:
    lines = text.splitlines() or [""]
    return "\n".join(
        [f"{escape(prompt_text)}{escape(lines[0])}", *[f"  {escape(line)}" for line in lines[1:]]]
    )


def _print_progress(console: Console, event: ProgressEvent, *, status: "_ShimmerStatus | None" = None) -> None:
    if status is not None and event.type in {"requirements_started", "controller_started", "reflection_started", "finalizer_started"}:
        status.set(_status_text(event))
        return
    for line in _render_progress_lines(event):
        console.print(line)
    if status is not None:
        status.set(_idle_status_text(event))


def _result_message(result: MatchRunResult) -> str:
    final_result = result.final_result
    lines = [
        "[bold]最终结果[/]",
        f"[dim]run_id:[/] {escape(result.run_id)}",
        f"[dim]结束原因:[/] {escape(final_result.stop_reason)}",
        f"[dim]结果目录:[/] {escape(str(result.run_dir))}",
        "",
        escape(final_result.summary),
    ]
    if not final_result.candidates:
        lines.extend(["", "[dim]候选人[/]", "None"])
        return "\n".join(lines)

    lines.extend(["", "[dim]候选人[/]"])
    for candidate in final_result.candidates[:10]:
        lines.append(
            f"{candidate.rank}. {escape(candidate.resume_id)} · {candidate.final_score} 分 · "
            f"{escape(candidate.fit_bucket)} · 第 {candidate.source_round} 轮"
        )
        lines.append(f"   {escape(candidate.match_summary)}")
        if candidate.matched_must_haves:
            lines.append(f"   匹配点：{escape(_join_list(candidate.matched_must_haves))}")
        if candidate.matched_preferences:
            lines.append(f"   加分项：{escape(_join_list(candidate.matched_preferences))}")
        if candidate.risk_flags:
            lines.append(f"   风险：{escape(_join_list(candidate.risk_flags))}")
        lines.append(f"   选择理由：{escape(candidate.why_selected)}")
    return "\n".join(lines)


def _render_progress_lines(event: ProgressEvent) -> list[str]:
    payload = event.payload or {}
    if event.type == "round_completed":
        return _render_round_completed(event, payload)
    if event.type in {"requirements_started", "controller_started", "reflection_started", "finalizer_started"}:
        return [_thinking_line(event)]
    if event.type == "run_completed":
        return [f"[dim]业务 trace 完成：{escape(event.message)}[/]"]
    if event.type == "run_failed":
        return [f"[dim]·[/] 运行失败：{escape(event.message)}"]
    if event.type == "rescue_lane_completed":
        return _render_rescue_lane_completed(payload)
    if event.type == "search_started":
        return _render_search_progress(event, payload, query_key="planned_queries", trim_message=True)
    if event.type == "search_completed":
        return _render_search_progress(event, payload, query_key="executed_queries", trim_message=False)
    if event.type == "resume_quality_comment_failed":
        lines = [f"[dim]· {escape(event.message)}[/]"]
        error = str(payload.get("error") or "").strip()
        if error:
            lines.append(f"[dim]  原因：{escape(_clip_text(error, 180))}[/]")
        return lines
    if event.type in {
        "requirements_completed",
        "controller_completed",
        "scoring_started",
        "scoring_completed",
        "resume_quality_comment_completed",
        "reflection_completed",
        "finalizer_completed",
    }:
        return [f"[dim]· {escape(event.message)}[/]"]
    return [f"[dim]·[/] {escape(event.message)}"]


def _render_rescue_lane_completed(payload: dict[str, Any]) -> list[str]:
    term = str(payload.get("accepted_term") or "")
    count = int(payload.get("seed_resume_count") or 0)
    return [f"召回修复：从 {count} 位高匹配候选人中提取扩展词：{escape(term)}"]


def _render_search_progress(event: ProgressEvent, payload: dict[str, Any], *, query_key: str, trim_message: bool) -> list[str]:
    query_lines = _query_route_lines(payload.get(query_key))
    if not query_lines:
        return [f"[dim]· {escape(event.message)}[/]"]
    message = _trim_search_message(event.message) if trim_message else event.message
    return [f"[dim]· {escape(message)}[/]", *[f"[dim]  {line}[/]" for line in query_lines]]


def _trim_search_message(message: str) -> str:
    head, separator, _ = message.partition("：")
    return f"{head}{separator}" if separator else message


def _thinking_line(event: ProgressEvent) -> str:
    return f"[dim][blink]{escape(_status_text(event))}[/][/]"


def _status_text(event: ProgressEvent) -> str:
    stage = str((event.payload or {}).get("stage") or event.type.removesuffix("_started"))
    if stage == "controller" and event.round_no is not None:
        detail = f"第 {event.round_no} 轮策略判断"
    elif stage == "reflection" and event.round_no is not None:
        detail = f"第 {event.round_no} 轮复盘判断"
    elif stage == "requirements":
        detail = "岗位需求解析"
    elif stage == "finalizer":
        detail = "整理最终名单"
    else:
        detail = event.message
    return f"{stage} 正在思考：{detail}"


def _clip_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 3)].rstrip()}..."


def _idle_status_text(event: ProgressEvent) -> str:
    if event.type == "round_completed":
        return f"业务 trace 已更新：第 {event.round_no} 轮摘要"
    if event.type == "run_completed":
        return "业务 trace 完成"
    if event.type == "run_failed":
        return "业务 trace 失败"
    return event.message or "业务 trace 正在推进"


def _render_round_completed(event: ProgressEvent, payload: dict[str, Any]) -> list[str]:
    round_no = event.round_no or int(payload.get("round_no") or 0)
    lines = [f"[bold]第 {round_no} 轮摘要[/]"]
    lines.extend(_query_summary_lines(payload))
    lines.append(
        "本轮搜到 "
        f"{int(payload.get('raw_candidate_count') or 0)} 人，"
        f"新增 {int(payload.get('unique_new_count') or 0)} 人，"
        f"{int(payload.get('newly_scored_count') or 0)} 人进入评分"
    )
    lines.append(
        "评分结果："
        f"fit {int(payload.get('fit_count') or 0)} / "
        f"not_fit {int(payload.get('not_fit_count') or 0)}"
    )
    lines.append(
        "top pool："
        f"新增 {int(payload.get('top_pool_selected_count') or 0)}，"
        f"保留 {int(payload.get('top_pool_retained_count') or 0)}，"
        f"移出 {int(payload.get('top_pool_dropped_count') or 0)}"
    )
    representative_candidates = _list_text(payload.get("representative_candidates"))[:5]
    if representative_candidates:
        lines.append("代表候选人：")
        lines.extend(f"- {escape(item)}" for item in representative_candidates)
    quality_comment = str(payload.get("resume_quality_comment") or "").strip()
    quality_error = str(payload.get("resume_quality_comment_error") or "").strip()
    if quality_comment:
        lines.append(f"本轮简历质量：{escape(quality_comment)}")
    elif quality_error:
        lines.append("[dim]· 本轮简历质量短评生成失败，已继续 reflection。[/]")
    reflection_summary = str(payload.get("reflection_summary") or "").strip()
    if reflection_summary:
        lines.append(f"本轮反思：{escape(reflection_summary)}")
    reflection_rationale = str(payload.get("reflection_rationale") or "").strip()
    if reflection_rationale:
        lines.append(f"反思理由：{escape(reflection_rationale)}")
    return lines


def _query_summary_lines(payload: dict[str, Any]) -> list[str]:
    executed_queries = payload.get("executed_queries")
    lines = _query_route_lines(executed_queries)
    if lines:
        return ["检索词：", *lines]
    query_terms = _join_list(_list_text(payload.get("query_terms")))
    return [f"检索词：{escape(query_terms)}"] if query_terms else []


def _query_route_lines(queries: object) -> list[str]:
    if not isinstance(queries, list):
        return []
    role_labels = {"exploit": "主检索", "explore": "探索检索"}
    lines: list[str] = []
    for query in queries:
        if not isinstance(query, Mapping):
            continue
        query_data = cast(Mapping[str, Any], query)
        query_terms = _join_list(_list_text(query_data.get("query_terms")))
        if not query_terms:
            continue
        role = str(query_data.get("query_role") or "检索")
        label = role_labels.get(role, role)
        lines.append(f"- {escape(label)}：{escape(query_terms)}")
    return lines


def _indented_list(label: str, values: list[str], *, limit: int = 4) -> list[str]:
    if not values:
        return []
    lines = [f"{escape(label)}："]
    lines.extend(f"- {escape(_clip_text(item, 96))}" for item in values[:limit])
    if len(values) > limit:
        lines.append(f"- 另外 {len(values) - limit} 项")
    return lines


def _join_list(values: list[str]) -> str:
    return "、".join(item for item in values if item)


def _list_text(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


class _ShimmerLine:
    def __init__(self, text: str) -> None:
        self.text = text

    def __rich__(self) -> Text:
        text = self.text or "业务 trace 正在推进"
        rendered = Text(text, style="dim")
        if not text:
            return rendered
        band_start = int(time.monotonic() * SHIMMER_CHARS_PER_SECOND) % (len(text) + 6) - 3
        for offset in range(SHIMMER_HIGHLIGHT_WIDTH):
            index = band_start + offset
            if 0 <= index < len(text):
                rendered.stylize("bold white", index, index + 1)
        return rendered


class _ShimmerStatus:
    def __init__(self, console: Console) -> None:
        self.renderable = _ShimmerLine("")
        self.live: Live | None = None
        self.console = console

    def start(self, text: str) -> None:
        self.renderable.text = text
        self.live = Live(
            self.renderable,
            console=self.console,
            refresh_per_second=SHIMMER_REFRESH_PER_SECOND,
            transient=False,
        )
        self.live.start()

    def set(self, text: str) -> None:
        self.renderable.text = text
        if self.live is not None:
            self.live.update(self.renderable)

    def stop(self) -> None:
        if self.live is not None:
            self.live.stop()
            self.live = None


__all__ = ["run_chat_session"]
