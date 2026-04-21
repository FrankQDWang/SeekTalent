from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType
from prompt_toolkit.styles import Style
from rich.markup import escape

from seektalent.api import MatchRunResult, run_match_async
from seektalent.progress import ProgressEvent

COMPOSER_MIN_LINES = 3
HEADER_HEIGHT = 6
INPUT_LABEL_HEIGHT = 2
INTRO_BOX_MAX_WIDTH = 64
FOLLOW_RESUME_THRESHOLD_LINES = 4
SHIMMER_REFRESH_PER_SECOND = 20
SHIMMER_CHARS_PER_SECOND = 24
SHIMMER_HIGHLIGHT_WIDTH = 6
MARKUP_TAG_RE = re.compile(r"\[(/?)([a-zA-Z_][a-zA-Z0-9_ -]*|#[0-9a-fA-F]{3,6})?\]")
MARKUP_STYLES = {
    "dim": "dim",
    "bold": "bold",
    "blink": "",
    "bold red": "ansired bold",
}
RunSearchFn = Callable[..., Coroutine[Any, Any, MatchRunResult]]


@dataclass
class TuiState:
    transcript_lines: list[str] = field(default_factory=list)
    scroll_offset: int = 0
    follow: bool = True
    status_text: str = ""
    input_step: str = "job_title"

    def submit_input(self, label: str, text: str, *, view_height: int) -> None:
        lines = _submitted_message(label, text).splitlines()
        lines.append("")
        self.append_lines(lines, view_height=view_height)

    def append_lines(self, lines: list[str], *, view_height: int) -> None:
        self.transcript_lines.extend(lines)
        if self.follow:
            self.scroll_to_bottom(view_height=view_height)

    def scroll_to_bottom(self, *, view_height: int) -> None:
        self.scroll_offset = self._bottom_offset(view_height)
        self.follow = True

    def scroll_up(self, amount: int, *, view_height: int) -> None:
        del view_height
        self.scroll_offset = max(0, self.scroll_offset - amount)
        self.follow = False

    def scroll_down(self, amount: int, *, view_height: int) -> None:
        self.scroll_offset = min(self._bottom_offset(view_height), self.scroll_offset + amount)
        self.follow = self._distance_to_bottom(view_height) <= FOLLOW_RESUME_THRESHOLD_LINES
        if self.follow:
            self.scroll_to_bottom(view_height=view_height)

    def visible_lines(self, *, view_height: int) -> list[str]:
        start = max(0, min(self.scroll_offset, self._bottom_offset(view_height)))
        end = start + max(1, view_height)
        return self.transcript_lines[start:end]

    def _bottom_offset(self, view_height: int) -> int:
        return max(0, len(self.transcript_lines) - max(1, view_height))

    def _distance_to_bottom(self, view_height: int) -> int:
        return self._bottom_offset(view_height) - self.scroll_offset


def run_chat_session(
    *,
    ask: object | None = None,
    console: object | None = None,
    run_search: RunSearchFn = run_match_async,
    cwd: Path | None = None,
) -> int:
    del ask, console
    return TuiSession(run_search=run_search, cwd=cwd or Path.cwd()).run()


class TranscriptControl(FormattedTextControl):
    def __init__(self, session: "TuiSession") -> None:
        super().__init__(session.transcript_fragments, focusable=True, show_cursor=False)
        self.session = session

    def mouse_handler(self, mouse_event: MouseEvent):
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            self.session.scroll_up(3)
            return None
        if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            self.session.scroll_down(3)
            return None
        return NotImplemented


class TuiSession:
    def __init__(
        self,
        *,
        run_search: RunSearchFn = run_match_async,
        cwd: Path | None = None,
    ) -> None:
        self.run_search = run_search
        self.cwd = cwd or Path.cwd()
        self.state = TuiState()
        self.buffer = Buffer(multiline=True)
        self.job_title = ""
        self.jd = ""
        self.notes = ""
        self.exit_code = 0
        self.search_started = False
        self.app = self._build_app()

    def run(self) -> int:
        result = self.app.run()
        return int(result if result is not None else self.exit_code)

    def _build_app(self) -> Application[int]:
        header_window = Window(
            FormattedTextControl(self.header_fragments),
            height=HEADER_HEIGHT,
            always_hide_cursor=True,
        )
        transcript_window = Window(
            content=TranscriptControl(self),
            wrap_lines=True,
            dont_extend_height=Condition(self.transcript_does_not_extend_height),
            always_hide_cursor=True,
            get_vertical_scroll=lambda _window: self.state.scroll_offset,
        )
        input_control = BufferControl(buffer=self.buffer)
        input_container = ConditionalContainer(
            HSplit(
                [
                    Window(FormattedTextControl(self.input_label_fragments), height=INPUT_LABEL_HEIGHT),
                    Window(
                        input_control,
                        height=Dimension(min=COMPOSER_MIN_LINES, preferred=COMPOSER_MIN_LINES),
                        wrap_lines=True,
                        get_line_prefix=lambda line_number, wrap_count: (
                            "› " if line_number == 0 and wrap_count == 0 else "  "
                        ),
                    ),
                ]
            ),
            filter=Condition(self.input_is_active),
        )
        status_window = ConditionalContainer(
            Window(FormattedTextControl(self.status_fragments), height=1),
            filter=Condition(self.status_is_visible),
        )
        return Application(
            full_screen=True,
            mouse_support=True,
            layout=Layout(
                HSplit([header_window, transcript_window, input_container, status_window]),
                focused_element=input_control,
            ),
            key_bindings=self._key_bindings(),
            style=Style.from_dict(
                {
                    "status": "ansibrightblack",
                    "status.highlight": "ansiwhite bold",
                }
            ),
        )

    def _key_bindings(self) -> KeyBindings:
        bindings = KeyBindings()

        @bindings.add("enter")
        def _submit(event) -> None:
            if self.input_is_active():
                self.submit_current_input(view_height=self.view_height(), app=event.app)
                return
            if self.state.input_step == "done":
                event.app.exit(result=self.exit_code)

        @bindings.add("c-j")
        def _newline(event) -> None:
            if self.input_is_active():
                event.app.current_buffer.insert_text("\n")

        @bindings.add("c-c")
        def _interrupt(event) -> None:
            self.exit_code = 130
            event.app.exit(result=130)

        @bindings.add("q", filter=Condition(lambda: self.state.input_step == "done"))
        def _quit(event) -> None:
            event.app.exit(result=self.exit_code)

        @bindings.add("pageup")
        def _page_up(event) -> None:
            del event
            self.scroll_up(max(1, self.view_height() - 1))

        @bindings.add("pagedown")
        def _page_down(event) -> None:
            del event
            self.scroll_down(max(1, self.view_height() - 1))

        @bindings.add("up", filter=Condition(lambda: not self.input_is_active()))
        def _up(event) -> None:
            del event
            self.scroll_up(1)

        @bindings.add("down", filter=Condition(lambda: not self.input_is_active()))
        def _down(event) -> None:
            del event
            self.scroll_down(1)

        @bindings.add("end")
        def _end(event) -> None:
            del event
            self.state.scroll_to_bottom(view_height=self.view_height())

        return bindings

    def input_is_active(self) -> bool:
        return self.state.input_step in {"job_title", "jd", "notes"}

    def status_is_visible(self) -> bool:
        return bool(self.state.status_text)

    def transcript_does_not_extend_height(self) -> bool:
        return self.input_is_active()

    def header_fragments(self) -> StyleAndTextTuples:
        box_width = self._header_width()
        content_width = box_width - 4
        cwd_text = _fit_text(str(self.cwd), content_width - 5)
        mode_text = _fit_text("interactive candidate search", content_width - 6)
        title_text = _fit_text(">_ SeekTalent", content_width)
        lines: StyleAndTextTuples = [
            ("dim", f"╭{'─' * (box_width - 2)}╮\n"),
            ("dim", "│ "),
            ("bold", f"{title_text:<{content_width}}"),
            ("dim", " │\n"),
            ("dim", "│ "),
            ("", f"{'':<{content_width}}"),
            ("dim", " │\n"),
            ("dim", "│ "),
            ("dim", "mode:"),
            ("", f" {mode_text:<{content_width - 6}}"),
            ("dim", " │\n"),
            ("dim", "│ "),
            ("dim", "cwd:"),
            ("", f"  {cwd_text:<{content_width - 6}}"),
            ("dim", " │\n"),
            ("dim", f"╰{'─' * (box_width - 2)}╯"),
        ]
        return lines

    def _header_width(self) -> int:
        columns = self.app.output.get_size().columns if hasattr(self, "app") else 80
        return min(max(columns - 4, 36), INTRO_BOX_MAX_WIDTH)

    def input_label_fragments(self) -> StyleAndTextTuples:
        fragments: StyleAndTextTuples = [("", "Paste ")]
        if self.state.input_step == "notes":
            fragments.extend(
                [
                    ("bold", "Notes"),
                    ("", " (optional). Enter to skip."),
                ]
            )
        else:
            fragments.extend(
                [
                    ("bold", self._input_label()),
                    ("", "."),
                ]
            )
        fragments.extend(
            [
                ("", "\n"),
                ("dim", "Enter submit · Ctrl+J newline · Ctrl+C quit"),
            ]
        )
        return fragments

    def transcript_fragments(self) -> StyleAndTextTuples:
        fragments: StyleAndTextTuples = []
        for line in self.state.transcript_lines:
            fragments.extend(_markup_fragments(line))
            fragments.append(("", "\n"))
        return fragments

    def status_fragments(self) -> StyleAndTextTuples:
        text = self.state.status_text
        if not text:
            return []
        if self.state.input_step != "running":
            return [("class:status", text)]
        band_start = int(time.monotonic() * SHIMMER_CHARS_PER_SECOND) % (len(text) + SHIMMER_HIGHLIGHT_WIDTH + 2) - 3
        fragments: StyleAndTextTuples = []
        for index, char in enumerate(text):
            style = "class:status.highlight" if band_start <= index < band_start + SHIMMER_HIGHLIGHT_WIDTH else "class:status"
            fragments.append((style, char))
        return fragments

    def view_height(self) -> int:
        rows = self.app.output.get_size().rows if hasattr(self, "app") else 24
        input_height = COMPOSER_MIN_LINES + INPUT_LABEL_HEIGHT if self.input_is_active() else 0
        status_height = 1 if self.status_is_visible() else 0
        return max(1, rows - HEADER_HEIGHT - input_height - status_height)

    def scroll_up(self, amount: int) -> None:
        self.state.scroll_up(amount, view_height=self.view_height())
        self.app.invalidate()

    def scroll_down(self, amount: int) -> None:
        self.state.scroll_down(amount, view_height=self.view_height())
        self.app.invalidate()

    def submit_current_input(self, *, view_height: int, app: Application[int] | None = None) -> None:
        text = self.buffer.text.rstrip()
        step = self.state.input_step
        if step in {"job_title", "jd"} and not text.strip():
            self.state.status_text = f"{self._input_label()} cannot be empty."
            if app is not None:
                app.invalidate()
            return
        if step == "job_title":
            self.job_title = text.strip()
            self.state.submit_input("Job Title", self.job_title, view_height=view_height)
            self.state.input_step = "jd"
        elif step == "jd":
            self.jd = text.strip()
            self.state.submit_input("JD", self.jd, view_height=view_height)
            self.state.input_step = "notes"
        elif step == "notes":
            self.notes = text.strip()
            if self.notes:
                self.state.submit_input("Notes", self.notes, view_height=view_height)
            self.state.input_step = "running"
            self.state.status_text = "业务 trace 等待第一步输出"
            if app is not None and not self.search_started:
                self.search_started = True
                app.create_background_task(self._refresh_status_until_done(app))
                app.create_background_task(self._run_search(app))
        self.buffer.text = ""
        if app is not None:
            if self.input_is_active():
                app.layout.focus(self.buffer)
            app.invalidate()

    async def _refresh_status_until_done(self, app: Application[int]) -> None:
        while self.state.input_step == "running":
            app.invalidate()
            await asyncio.sleep(1 / SHIMMER_REFRESH_PER_SECOND)

    async def _run_search(self, app: Application[int]) -> None:
        try:
            result = await self.run_search(
                job_title=self.job_title,
                jd=self.jd,
                notes=self.notes,
                env_file=".env",
                progress_callback=lambda event: self.handle_progress(event, app=app),
            )
        except Exception as exc:  # noqa: BLE001
            self.exit_code = 1
            self.state.append_lines(["[bold red]Failed[/]", escape(str(exc)), ""], view_height=self.view_height())
            self.state.status_text = "业务 trace 失败 · Enter/q 退出"
            self.state.input_step = "done"
            app.invalidate()
            return
        self.state.append_lines(["", *_result_message(result).splitlines(), ""], view_height=self.view_height())
        self.exit_code = 0
        self.state.status_text = "业务 trace 完成 · Enter/q 退出"
        self.state.input_step = "done"
        app.invalidate()

    def handle_progress(self, event: ProgressEvent, *, app: Application[int]) -> None:
        if event.type in {"requirements_started", "controller_started", "reflection_started", "finalizer_started"}:
            self.state.status_text = _status_text(event)
            app.invalidate()
            return
        self.state.append_lines(_render_progress_lines(event), view_height=self.view_height())
        self.state.status_text = _idle_status_text(event)
        app.invalidate()

    def _input_label(self) -> str:
        return {"job_title": "Job Title", "jd": "JD", "notes": "Notes"}.get(self.state.input_step, "")

    def _input_prompt(self) -> str:
        if self.state.input_step == "notes":
            return "Paste Notes (optional). Enter to skip."
        return f"Paste {self._input_label()}."

def _submitted_message(label: str, text: str) -> str:
    return "\n".join([f"[dim]{escape(label)}[/]", escape(text)])


def _markup_fragments(text: str) -> StyleAndTextTuples:
    fragments: StyleAndTextTuples = []
    style_stack: list[str] = []
    position = 0
    for match in MARKUP_TAG_RE.finditer(text):
        if match.start() > 0 and text[match.start() - 1] == "\\":
            if match.start() - 1 > position:
                fragments.append((_joined_style(style_stack), text[position : match.start() - 1]))
            fragments.append((_joined_style(style_stack), match.group(0)))
            position = match.end()
            continue
        if match.start() > position:
            fragments.append((_joined_style(style_stack), text[position : match.start()]))
        closing, tag = match.groups()
        if closing:
            if style_stack:
                style_stack.pop()
        elif tag in MARKUP_STYLES:
            style_stack.append(MARKUP_STYLES[tag])
        elif tag and tag.startswith("#"):
            style_stack.append(f"fg:{tag}")
        position = match.end()
    if position < len(text):
        fragments.append((_joined_style(style_stack), text[position:]))
    return fragments


def _joined_style(styles: list[str]) -> str:
    return " ".join(styles)


def _fit_text(text: str, width: int) -> str:
    width = max(width, 8)
    if len(text) <= width:
        return text
    return f"...{text[-(width - 3):]}"


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
        return [f"[dim][blink]业务 trace 完成：{escape(event.message)}[/][/]"]
    if event.type == "run_failed":
        return [f"[dim]·[/] 运行失败：{escape(event.message)}"]
    if event.type in {
        "requirements_completed",
        "controller_completed",
        "search_started",
        "search_completed",
        "scoring_started",
        "scoring_completed",
        "reflection_completed",
        "finalizer_completed",
    }:
        return [f"[dim]· {escape(event.message)}[/]"]
    return [f"[dim]·[/] {escape(event.message)}"]


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
    query_terms = _join_list(_list_text(payload.get("query_terms")))
    lines = [f"[bold]第 {round_no} 轮摘要[/]"]
    if query_terms:
        lines.append(f"检索词：{escape(query_terms)}")
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
    return lines


def _join_list(values: list[str]) -> str:
    return "、".join(item for item in values if item)


def _list_text(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


__all__ = ["run_chat_session"]
