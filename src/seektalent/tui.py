from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, Window
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.dimension import Dimension
from rich.console import Console
from rich.markup import escape

from seektalent.api import run_match_async
from seektalent.models import SearchRunBundle
from seektalent.progress import ProgressEvent

COMPOSER_MIN_LINES = 3
INTRO_BOX_MAX_WIDTH = 64
PromptFn = Callable[[str], str]
RunSearchFn = Callable[..., Awaitable[SearchRunBundle]]


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
        job_description = _read_job_description(console, prompt)
        console.print()
        console.print(_submitted_message("Job Description", job_description))
        console.print()
        console.print(
            "Paste [bold]Hiring Notes[/] [dim](optional)[/]. "
            "Press [bold]Enter[/] to skip."
        )
        hiring_notes = prompt("› ").rstrip().strip()
        console.print()
        if hiring_notes:
            console.print(_submitted_message("Hiring Notes", hiring_notes))
            console.print()
        console.print("[dim]Working[/]")
        bundle = asyncio.run(
            run_search(
                job_description=job_description,
                hiring_notes=hiring_notes,
                top_k=10,
                round_budget=None,
                env_file=".env",
                progress_callback=lambda event: _print_progress(console, event),
            )
        )
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
    console.print(_result_message(bundle))
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
    mode_text = _fit_text("chat-first JD search", content_width - 6)
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
                "Paste [bold]Job Description[/].",
                "[dim]Enter submit · Ctrl+J newline · Ctrl+C quit[/]",
            ]
        )
    )


def _fit_text(text: str, width: int) -> str:
    width = max(width, 8)
    if len(text) <= width:
        return text
    return f"…{text[-(width - 1):]}"


def _read_job_description(console: Console, prompt: PromptFn) -> str:
    while True:
        text = prompt("› ").rstrip()
        if text.strip():
            return text.strip()
        console.print("Job Description cannot be empty. Paste the JD and press [bold]Enter[/].")


def _print_progress(console: Console, event: ProgressEvent) -> None:
    console.print(f"[dim]·[/] {escape(event.message)}")


def _submitted_message(label: str, text: str) -> str:
    return "\n".join([f"[dim]{escape(label)}[/]", escape(text)])


def _result_message(bundle: SearchRunBundle) -> str:
    cards = bundle.final_result.final_candidate_cards[:10]
    lines = [
        "[bold]Final result[/]",
        f"[dim]Stop reason:[/] {escape(bundle.final_result.stop_reason)}",
        "",
        escape(bundle.final_result.reviewer_summary),
        "",
        escape(bundle.final_result.run_summary),
    ]
    if not cards:
        lines.extend(
            [
                "",
                "[dim]Top candidates[/]",
                "None",
            ]
        )
        return "\n".join(lines)
    lines.extend(["", "[dim]Top candidates[/]"])
    for index, card in enumerate(cards, start=1):
        lines.append(
            f"{index}. {escape(_value(card, 'candidate_id'))} · {escape(_value(card, 'review_recommendation'))}"
        )
        lines.append(f"   {escape(_value(card, 'card_summary'))}")
        gap_text = _signal_summary(_value(card, "gap_signals"))
        risk_text = _signal_summary(_value(card, "risk_signals"))
        if gap_text:
            lines.append(f"   gap: {gap_text}")
        if risk_text:
            lines.append(f"   risk: {risk_text}")
    return "\n".join(lines)


def _signal_summary(signals: Any) -> str:
    if not signals:
        return ""
    parts = [_signal_text(signal) for signal in signals[:2]]
    return "; ".join(part for part in parts if part)


def _signal_text(signal: Any) -> str:
    if isinstance(signal, dict):
        return escape(str(signal.get("display_text") or signal.get("signal") or ""))
    return escape(str(getattr(signal, "display_text", None) or getattr(signal, "signal", "")))


def _value(item: Any, field: str) -> Any:
    if isinstance(item, dict):
        return item.get(field, "")
    return getattr(item, field, "")


__all__ = ["run_chat_session"]
