from __future__ import annotations

from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Footer, Header, Input, RichLog, Static, TextArea

from seektalent.api import run_match_async
from seektalent.config import AppSettings
from seektalent.models import CandidateEvidenceCard_t, SearchRunBundle
from seektalent.progress import ProgressEvent, make_progress_event


class SeekTalentApp(App[None]):
    CSS = """
    Screen {
        layout: vertical;
    }

    #settings {
        height: 3;
    }

    #inputs {
        height: 16;
    }

    TextArea {
        width: 1fr;
        border: round $accent;
    }

    #body {
        height: 1fr;
    }

    #trace {
        width: 1fr;
        border: round $accent;
    }

    #results {
        width: 1fr;
    }

    #candidate-table {
        height: 12;
        border: round $accent;
    }

    #candidate-detail, #summary {
        height: 1fr;
        border: round $accent;
        padding: 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.cards_by_candidate_id: dict[str, CandidateEvidenceCard_t] = {}
        self.current_bundle: SearchRunBundle | None = None
        self.search_in_flight = False
        self.default_round_budget = AppSettings(_env_file=".env").round_budget

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="settings"):
            yield Input(value="10", placeholder="Top K", id="top-k")
            yield Input(value=str(self.default_round_budget), placeholder="Round Budget", id="round-budget")
            yield Input(value=".env", placeholder="Env File", id="env-file")
            yield Button("Run Search", id="run")
        with Horizontal(id="inputs"):
            yield TextArea("", id="job-description")
            yield TextArea("", id="hiring-notes")
        with Horizontal(id="body"):
            yield RichLog(id="trace", markup=False, wrap=True)
            with Vertical(id="results"):
                yield DataTable(id="candidate-table")
                yield Static("No run yet.", id="summary")
                yield Static("Select a candidate to inspect details.", id="candidate-detail")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#job-description", TextArea).border_title = "Job Description"
        self.query_one("#hiring-notes", TextArea).border_title = "Hiring Notes (optional)"
        table = self.query_one("#candidate-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Candidate", "Recommendation", "Summary")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "run" or self.search_in_flight:
            return
        self.search_in_flight = True
        event.button.disabled = True
        self._clear_results()
        try:
            await self._run_search()
        finally:
            self.search_in_flight = False
            event.button.disabled = False

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row_key = getattr(event.row_key, "value", event.row_key)
        candidate_id = str(row_key)
        card = self.cards_by_candidate_id.get(candidate_id)
        if card is None:
            return
        self.query_one("#candidate-detail", Static).update(_card_detail_text(card))

    async def _run_search(self) -> None:
        job_description = self.query_one("#job-description", TextArea).text
        hiring_notes = self.query_one("#hiring-notes", TextArea).text
        env_file = self.query_one("#env-file", Input).value.strip() or ".env"
        try:
            top_k = int((self.query_one("#top-k", Input).value or "10").strip())
            round_budget_raw = (self.query_one("#round-budget", Input).value or "").strip()
            round_budget = int(round_budget_raw) if round_budget_raw else None
        except ValueError as exc:
            self._record_progress(
                make_progress_event(
                    "run_failed",
                    f"failed: invalid numeric input ({exc})",
                )
            )
            self.query_one("#summary", Static).update(f"error: {exc}")
            return
        try:
            bundle = await run_match_async(
                job_description=job_description,
                hiring_notes=hiring_notes,
                top_k=top_k,
                round_budget=round_budget,
                env_file=env_file,
                progress_callback=self._record_progress,
            )
        except Exception as exc:  # noqa: BLE001
            self._record_progress(
                make_progress_event(
                    "run_failed",
                    f"failed: {exc}",
                )
            )
            self.query_one("#summary", Static).update(f"error: {exc}")
            return
        self.current_bundle = bundle
        self._render_bundle(bundle)

    def _record_progress(self, event: ProgressEvent) -> None:
        self.query_one("#trace", RichLog).write(f"[{event.timestamp}] {event.message}")

    def _clear_results(self) -> None:
        self.cards_by_candidate_id = {}
        self.query_one("#trace", RichLog).clear()
        self.query_one("#summary", Static).update("Running search...")
        self.query_one("#candidate-detail", Static).update("Select a candidate to inspect details.")
        table = self.query_one("#candidate-table", DataTable)
        table.clear(columns=False)

    def _render_bundle(self, bundle: SearchRunBundle) -> None:
        self.cards_by_candidate_id = {
            card.candidate_id: card for card in bundle.final_result.final_candidate_cards
        }
        table = self.query_one("#candidate-table", DataTable)
        table.clear(columns=False)
        for card in bundle.final_result.final_candidate_cards:
            table.add_row(
                card.candidate_id,
                card.review_recommendation,
                card.card_summary,
                key=card.candidate_id,
            )
        self.query_one("#summary", Static).update(
            "\n".join(
                [
                    f"run_dir: {bundle.run_dir}",
                    f"stop_reason: {bundle.final_result.stop_reason}",
                    f"reviewer_summary: {bundle.final_result.reviewer_summary}",
                    f"run_summary: {bundle.final_result.run_summary}",
                ]
            )
        )
        if bundle.final_result.final_candidate_cards:
            first_card = bundle.final_result.final_candidate_cards[0]
            self.query_one("#candidate-detail", Static).update(_card_detail_text(first_card))
        else:
            self.query_one("#candidate-detail", Static).update("No candidate cards returned.")


def _card_detail_text(card: CandidateEvidenceCard_t) -> str:
    lines = [
        f"candidate_id: {card.candidate_id}",
        f"review_recommendation: {card.review_recommendation}",
        f"card_summary: {card.card_summary}",
        "",
        "must_have_matrix:",
    ]
    if not card.must_have_matrix:
        lines.append("- none")
    for row in card.must_have_matrix:
        lines.append(f"- {row.capability}: {row.verdict} | {row.evidence_summary}")
    lines.append("")
    lines.append("preferred_evidence:")
    if not card.preferred_evidence:
        lines.append("- none")
    for signal in card.preferred_evidence:
        lines.append(f"- {signal.display_text or signal.signal}")
    lines.append("")
    lines.append("gap_signals:")
    if not card.gap_signals:
        lines.append("- none")
    for signal in card.gap_signals:
        lines.append(f"- {signal.display_text or signal.signal}")
    lines.append("")
    lines.append("risk_signals:")
    if not card.risk_signals:
        lines.append("- none")
    for signal in card.risk_signals:
        lines.append(f"- {signal.display_text or signal.signal}")
    return "\n".join(lines)


__all__ = ["SeekTalentApp"]
