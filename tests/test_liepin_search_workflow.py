from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from seektalent.opencli_browser.contracts import OpenCliBrowserResult
from seektalent.providers.liepin.liepin_search_workflow import (
    LiepinSearchWorkflow,
    LiepinSearchWorkflowRequest,
)


@dataclass
class FakeLiepinSearchWorkflowSite:
    capture_ok: bool = True
    calls: list[str] = field(default_factory=list)
    events: list[dict[str, object]] = field(default_factory=list)
    resumes: list[dict[str, object]] = field(default_factory=list)
    structured_cards: list[list[dict[str, object]]] = field(
        default_factory=lambda: [
            [
                {"ref": "70", "provider_rank": 1},
                {"ref": "71", "provider_rank": 2},
                {"ref": "72", "provider_rank": 3},
            ]
        ]
    )

    def append_agent_event(self, source_run_id: str, event: dict[str, object]) -> None:
        del source_run_id
        self.calls.append("append_agent_event")
        self.events.append(event)

    def search_liepin_cards(
        self,
        *,
        source_run_id: str,
        query: str,
        max_pages: int,
        max_cards: int,
        native_filters: dict[str, object] | None = None,
    ) -> dict[str, object]:
        del source_run_id, query, max_pages, native_filters
        self.calls.append("search_liepin_cards")
        return {
            "status": "succeeded",
            "stop_reason": "completed",
            "cards_seen": max_cards,
        }

    def extract_structured_liepin_cards(self, *, source_run_id: str, max_cards: int) -> OpenCliBrowserResult:
        del source_run_id, max_cards
        self.calls.append("extract_structured_liepin_cards")
        index = min(len([call for call in self.calls if call == "extract_structured_liepin_cards"]) - 1, 0)
        return OpenCliBrowserResult(
            ok=True,
            action="extract_structured_liepin_cards",
            observation={"cards": self.structured_cards[index]},
        )

    def safe_liepin_detail_url_for_ref(self, ref: str) -> str | None:
        self.calls.append("safe_liepin_detail_url_for_ref")
        return f"https://h.liepin.com/resume/showresumedetail/?res_id_encode={ref}"

    def open_liepin_detail(self, *, source_run_id: str, ref: str, rank: int) -> OpenCliBrowserResult:
        del source_run_id, ref
        self.calls.append("open_liepin_detail")
        return OpenCliBrowserResult(ok=True, action="open_liepin_detail", counts={"rank": rank})

    def open_liepin_detail_cached_url(
        self,
        *,
        source_run_id: str,
        ref: str,
        rank: int,
        detail_url: str,
    ) -> OpenCliBrowserResult:
        del source_run_id, ref, detail_url
        self.calls.append("open_liepin_detail_cached_url")
        return OpenCliBrowserResult(ok=True, action="open_liepin_detail", counts={"rank": rank})

    def capture_liepin_detail_resume(self, *, source_run_id: str, rank: int) -> OpenCliBrowserResult:
        del source_run_id
        self.calls.append("capture_liepin_detail_resume")
        if not self.capture_ok:
            return OpenCliBrowserResult(
                ok=False,
                action="capture_liepin_detail_resume",
                safe_reason_code="liepin_opencli_detail_not_opened",
            )
        self.resumes.append({"provider_rank": rank, "detail_payload": {"rank": rank}})
        return OpenCliBrowserResult(ok=True, action="capture_liepin_detail_resume", counts={"rank": rank})

    def restore_liepin_search_page(self) -> str | None:
        self.calls.append("restore_liepin_search_page")
        return "page-search"

    def finalize_liepin_resumes(
        self,
        *,
        source_run_id: str,
        query: str,
        max_pages: int,
        max_cards: int,
        cards_seen: int | None = None,
        target_resumes: int | None = None,
    ) -> dict[str, object]:
        del source_run_id, query, max_pages, max_cards, cards_seen, target_resumes
        self.calls.append("finalize_liepin_resumes")
        return {
            "status": "succeeded",
            "stop_reason": "completed",
            "resumes_returned": len(self.resumes),
            "resumes": self.resumes,
        }

    def blocked_resumes_envelope(
        self,
        *,
        source_run_id: str,
        query: str,
        safe_reason_code: str | None,
        cards_seen: int,
    ) -> dict[str, object]:
        del source_run_id, query, cards_seen
        self.calls.append("blocked_resumes_envelope")
        return {
            "status": "blocked",
            "safe_reason_code": safe_reason_code,
            "resumes_returned": 0,
            "resumes": [],
        }


def _request(**overrides: Any) -> LiepinSearchWorkflowRequest:
    values: dict[str, object] = {
        "source_run_id": "run-1",
        "query": "数据开发专家",
        "target_resumes": 2,
        "max_pages": 1,
        "max_cards": 3,
        "native_filters": None,
    }
    values.update(overrides)
    return LiepinSearchWorkflowRequest(**values)


def test_workflow_opens_details_until_target_count() -> None:
    site = FakeLiepinSearchWorkflowSite()

    envelope = LiepinSearchWorkflow(site=site).search_detail_backed_resumes(_request())

    assert envelope["status"] == "succeeded"
    assert envelope["resumes_returned"] == 2
    assert site.calls.count("open_liepin_detail") == 2
    assert "search_liepin_cards" in site.calls
    assert "extract_structured_liepin_cards" in site.calls
    assert "finalize_liepin_resumes" in site.calls


def test_workflow_blocks_when_no_detail_can_be_captured() -> None:
    site = FakeLiepinSearchWorkflowSite(capture_ok=False)

    envelope = LiepinSearchWorkflow(site=site).search_detail_backed_resumes(_request(target_resumes=1))

    assert envelope["status"] == "blocked"
    assert envelope["safe_reason_code"] == "liepin_opencli_detail_not_opened"
    assert "blocked_resumes_envelope" in site.calls
    assert all(event.get("action_kind") != "capture_detail_failed" for event in site.events)
