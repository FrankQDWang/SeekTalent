from __future__ import annotations

from typing import Any

from rich.markup import escape
from textual import events
from textual.app import App, ComposeResult
from textual.widgets import RichLog, Static, TextArea

from seektalent.api import run_match_async
from seektalent.models import SearchRunBundle
from seektalent.progress import ProgressEvent


class SeekTalentApp(App[None]):
    CSS = """
    Screen {
        layout: vertical;
        background: #0b0d10;
        color: #e6e7eb;
        padding: 1 2;
    }

    #transcript {
        height: 1fr;
        border: solid #1f2329;
        background: #0f1115;
        padding: 0 1;
    }

    #status {
        color: #8b949e;
        padding: 0 1;
        height: auto;
    }

    #composer {
        height: 8;
        border: solid #1f2329;
        background: #11161c;
        color: #e6e7eb;
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.phase = "awaiting_jd"
        self.job_description = ""
        self.hiring_notes = ""
        self.current_bundle: SearchRunBundle | None = None
        self.messages: list[dict[str, str]] = []
        self.working_message_index: int | None = None
        self.status_text = ""

    def compose(self) -> ComposeResult:
        yield RichLog(id="transcript", markup=True, wrap=True)
        yield Static("", id="status")
        yield TextArea("", id="composer")

    def on_mount(self) -> None:
        composer = self.query_one("#composer", TextArea)
        composer.border_title = "Message"
        composer.focus()
        self._set_status("Shift+Enter submit · Enter newline")
        self._append_assistant(
            "Paste [bold]Job Description[/] below, then press [bold]Shift+Enter[/]. "
            "After that I will ask for optional [bold]Hiring Notes[/]."
        )

    def on_key(self, event: events.Key) -> None:
        if event.key != "shift+enter":
            return
        if self.focused is not self.query_one("#composer", TextArea):
            return
        event.stop()
        self.action_submit_message()

    def action_submit_message(self) -> None:
        composer = self.query_one("#composer", TextArea)
        if self.phase in {"running", "completed"}:
            return
        text = composer.text.rstrip()
        if self.phase == "awaiting_jd":
            if not text.strip():
                self._append_assistant(
                    "Job Description cannot be empty. Paste the JD and press [bold]Shift+Enter[/]."
                )
                return
            self.job_description = text.strip()
            self._append_user(self.job_description)
            composer.text = ""
            self.phase = "awaiting_notes"
            self._append_assistant(
                "Paste [bold]Hiring Notes[/] if you have them. Leave the box empty and press "
                "[bold]Shift+Enter[/] to skip."
            )
            self._set_status("Optional notes · Shift+Enter submit · Enter newline")
            return
        notes = text.strip()
        if notes:
            self._append_user(notes)
        else:
            self._append_assistant("No hiring notes supplied. Starting search.")
        self.hiring_notes = notes
        composer.text = ""
        composer.disabled = True
        composer.read_only = True
        self.phase = "running"
        self._set_status("Working…")
        self.working_message_index = self._append_assistant(
            "Working:\n- starting search pipeline"
        )
        self.run_worker(self._run_search(), exclusive=True, group="search")

    async def _run_search(self) -> None:
        try:
            bundle = await run_match_async(
                job_description=self.job_description,
                hiring_notes=self.hiring_notes,
                top_k=10,
                round_budget=None,
                env_file=".env",
                progress_callback=self._record_progress,
            )
        except Exception as exc:  # noqa: BLE001
            self._append_working_line(f"- failed: {escape(str(exc))}")
            self._append_assistant(f"Run failed.\n{escape(str(exc))}")
            self._finish_session()
            return
        self.current_bundle = bundle
        self._append_assistant(_result_message(bundle))
        self._finish_session()

    def _record_progress(self, event: ProgressEvent) -> None:
        self._append_working_line(f"- {escape(event.message)}")

    def _append_working_line(self, line: str) -> None:
        if self.working_message_index is None:
            self.working_message_index = self._append_assistant(f"Working:\n{line}")
            return
        self.messages[self.working_message_index]["text"] += f"\n{line}"
        self._refresh_transcript()

    def _finish_session(self) -> None:
        self.phase = "completed"
        self._set_status("Session complete. Re-run seektalent to start a new session.")
        composer = self.query_one("#composer", TextArea)
        composer.disabled = True
        composer.read_only = True

    def _append_user(self, text: str) -> int:
        return self._append_message("you", escape(text))

    def _append_assistant(self, text: str) -> int:
        return self._append_message("assistant", text)

    def _append_message(self, role: str, text: str) -> int:
        self.messages.append({"role": role, "text": text})
        self._refresh_transcript()
        return len(self.messages) - 1

    def _refresh_transcript(self) -> None:
        transcript = self.query_one("#transcript", RichLog)
        transcript.clear()
        for message in self.messages:
            label = "[bold #79c0ff]you[/]" if message["role"] == "you" else "[bold #e6e7eb]assistant[/]"
            transcript.write(f"{label}\n{message['text']}\n")

    def _set_status(self, text: str) -> None:
        self.status_text = text
        self.query_one("#status", Static).update(text)

    def transcript_text(self) -> str:
        return "\n\n".join(f"{message['role']}\n{message['text']}" for message in self.messages)


def _result_message(bundle: SearchRunBundle) -> str:
    cards = bundle.final_result.final_candidate_cards[:10]
    lines = [
        "Run complete.",
        f"stop_reason: {escape(bundle.final_result.stop_reason)}",
        f"reviewer_summary: {escape(bundle.final_result.reviewer_summary)}",
        f"run_summary: {escape(bundle.final_result.run_summary)}",
    ]
    if not cards:
        lines.append("top_candidates: none")
        return "\n".join(lines)
    lines.append("top_candidates:")
    for index, card in enumerate(cards, start=1):
        lines.append(
            f"{index}. {escape(_value(card, 'candidate_id'))} | {escape(_value(card, 'review_recommendation'))}"
        )
        lines.append(f"   {escape(_value(card, 'card_summary'))}")
        gap_text = _signal_summary(_value(card, "gap_signals"))
        risk_text = _signal_summary(_value(card, "risk_signals"))
        if gap_text:
            lines.append(f"   gaps: {gap_text}")
        if risk_text:
            lines.append(f"   risks: {risk_text}")
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


__all__ = ["SeekTalentApp"]
