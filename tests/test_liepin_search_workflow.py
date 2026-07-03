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
    open_ok: bool = True
    open_safe_reason_code: str = "liepin_opencli_detail_not_opened"
    capture_ok: bool = True
    capture_safe_reason_code: str = "liepin_opencli_detail_not_opened"
    wait_ok: bool = True
    wait_safe_reason_code: str = "liepin_opencli_detail_not_opened"
    restore_ok: bool = True
    detail_urls_available: bool = True
    search_states: list[OpenCliBrowserResult] = field(
        default_factory=lambda: [
            _search_state_with_detail_targets("70", "71", "72")
        ]
    )
    detail_states: list[OpenCliBrowserResult] = field(
        default_factory=lambda: [OpenCliBrowserResult(ok=True, action="state")]
    )
    calls: list[str] = field(default_factory=list)
    events: list[dict[str, object]] = field(default_factory=list)
    resumes: list[dict[str, object]] = field(default_factory=list)
    capture_require_ready_values: list[bool] = field(default_factory=list)
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

    def observe_liepin_search_state(self) -> OpenCliBrowserResult:
        self.calls.append("observe_liepin_search_state")
        if self.search_states:
            return self.search_states.pop(0)
        return _search_state_with_detail_targets("70", "71", "72")

    def observe_liepin_detail_state(self) -> OpenCliBrowserResult:
        self.calls.append("observe_liepin_detail_state")
        if self.detail_states:
            return self.detail_states.pop(0)
        return OpenCliBrowserResult(ok=True, action="state")

    def extract_structured_liepin_cards(self, *, source_run_id: str, max_cards: int) -> OpenCliBrowserResult:
        del source_run_id, max_cards
        self.calls.append("extract_structured_liepin_cards")
        capture_index = len([call for call in self.calls if call == "extract_structured_liepin_cards"]) - 1
        index = min(capture_index, len(self.structured_cards) - 1)
        return OpenCliBrowserResult(
            ok=True,
            action="extract_structured_liepin_cards",
            observation={"cards": self.structured_cards[index]},
        )

    def safe_liepin_detail_url_for_ref(self, ref: str) -> str | None:
        self.calls.append("safe_liepin_detail_url_for_ref")
        if not self.detail_urls_available:
            return None
        return f"https://h.liepin.com/resume/showresumedetail/?res_id_encode={ref}"

    def open_liepin_detail(self, *, source_run_id: str, ref: str, rank: int) -> OpenCliBrowserResult:
        del source_run_id, ref
        self.calls.append("open_liepin_detail")
        if not self.open_ok:
            return OpenCliBrowserResult(
                ok=False,
                action="open_liepin_detail",
                safe_reason_code=self.open_safe_reason_code,
            )
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
        if not self.open_ok:
            return OpenCliBrowserResult(
                ok=False,
                action="open_liepin_detail",
                safe_reason_code=self.open_safe_reason_code,
            )
        return OpenCliBrowserResult(ok=True, action="open_liepin_detail", counts={"rank": rank})

    def wait_liepin_detail_ready(self, *, source_run_id: str, rank: int) -> OpenCliBrowserResult:
        del source_run_id
        self.calls.append("wait_liepin_detail_ready")
        if not self.wait_ok:
            return OpenCliBrowserResult(
                ok=False,
                action="wait_liepin_detail_ready",
                safe_reason_code=self.wait_safe_reason_code,
            )
        return OpenCliBrowserResult(ok=True, action="wait_liepin_detail_ready", counts={"rank": rank})

    def capture_liepin_detail_resume(
        self,
        *,
        source_run_id: str,
        rank: int,
        require_ready: bool = True,
    ) -> OpenCliBrowserResult:
        del source_run_id
        self.calls.append("capture_liepin_detail_resume")
        self.capture_require_ready_values.append(require_ready)
        if not self.capture_ok:
            return OpenCliBrowserResult(
                ok=False,
                action="capture_liepin_detail_resume",
                safe_reason_code=self.capture_safe_reason_code,
            )
        self.resumes.append({"provider_rank": rank, "detail_payload": {"rank": rank}})
        return OpenCliBrowserResult(ok=True, action="capture_liepin_detail_resume", counts={"rank": rank})

    def discard_liepin_detail_resume(self, *, source_run_id: str, rank: int) -> None:
        del source_run_id
        self.calls.append("discard_liepin_detail_resume")
        self.resumes = [resume for resume in self.resumes if resume.get("provider_rank") != rank]

    def restore_liepin_search_page(self) -> str | None:
        self.calls.append("restore_liepin_search_page")
        if not self.restore_ok:
            return None
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


def _search_state_with_detail_targets(*refs: str, text: str = "visible cards without raw refs") -> OpenCliBrowserResult:
    return OpenCliBrowserResult(
        ok=True,
        action="state",
        observation={
            "text": text,
            "detailTargets": tuple({"rank": index, "ref": ref} for index, ref in enumerate(refs, start=1)),
        },
    )


def test_workflow_opens_details_until_target_count() -> None:
    site = FakeLiepinSearchWorkflowSite()

    envelope = LiepinSearchWorkflow(site=site).search_detail_backed_resumes(_request())

    assert envelope["status"] == "succeeded"
    assert envelope["resumes_returned"] == 2
    assert site.calls.count("open_liepin_detail") == 2
    assert "search_liepin_cards" in site.calls
    assert "extract_structured_liepin_cards" in site.calls
    assert "finalize_liepin_resumes" in site.calls


def test_workflow_initial_card_extraction_uses_state_probe_before_and_after() -> None:
    site = FakeLiepinSearchWorkflowSite()

    envelope = LiepinSearchWorkflow(site=site).search_detail_backed_resumes(_request(target_resumes=1))

    assert envelope["status"] == "succeeded"
    extract_index = site.calls.index("extract_structured_liepin_cards")
    assert site.calls[extract_index - 1] == "observe_liepin_search_state"
    assert site.calls[extract_index + 1] == "observe_liepin_search_state"
    assert any(event.get("action_kind") == "extract_structured_cards" and event.get("ok") is True for event in site.events)


def test_workflow_refresh_card_extraction_uses_state_probe_before_and_after() -> None:
    site = FakeLiepinSearchWorkflowSite(
        structured_cards=[
            [{"ref": "70", "provider_rank": 1}, {"ref": "71", "provider_rank": 2}],
            [{"ref": "71", "provider_rank": 2}],
        ]
    )

    envelope = LiepinSearchWorkflow(site=site).search_detail_backed_resumes(_request(target_resumes=2))

    assert envelope["status"] == "succeeded"
    extract_indexes = [index for index, call in enumerate(site.calls) if call == "extract_structured_liepin_cards"]
    assert len(extract_indexes) == 2
    refresh_index = extract_indexes[1]
    assert site.calls[refresh_index - 1] == "observe_liepin_search_state"
    assert site.calls[refresh_index + 1] == "observe_liepin_search_state"
    assert any(
        event.get("action_kind") == "visible_cards_refreshed_after_return" and event.get("ok") is True
        for event in site.events
    )


def test_workflow_detail_operations_use_transition_state_probes_before_and_after() -> None:
    site = FakeLiepinSearchWorkflowSite()

    envelope = LiepinSearchWorkflow(site=site).search_detail_backed_resumes(_request(target_resumes=2))

    assert envelope["status"] == "succeeded"
    open_index = site.calls.index("open_liepin_detail")
    assert site.calls[open_index - 1] == "observe_liepin_search_state"
    assert site.calls[open_index + 1] == "observe_liepin_detail_state"
    capture_index = site.calls.index("capture_liepin_detail_resume")
    assert site.calls[capture_index - 1] == "observe_liepin_detail_state"
    assert site.calls[capture_index + 1] == "observe_liepin_detail_state"
    detail_calls = [
        call
        for call in site.calls
        if call
        in {
            "open_liepin_detail",
            "observe_liepin_detail_state",
            "wait_liepin_detail_ready",
            "capture_liepin_detail_resume",
        }
    ]
    assert detail_calls[:8] == [
        "open_liepin_detail",
        "observe_liepin_detail_state",
        "observe_liepin_detail_state",
        "wait_liepin_detail_ready",
        "observe_liepin_detail_state",
        "observe_liepin_detail_state",
        "capture_liepin_detail_resume",
        "observe_liepin_detail_state",
    ]
    assert site.capture_require_ready_values[0] is False
    restore_index = site.calls.index("restore_liepin_search_page")
    assert site.calls[restore_index - 1] == "observe_liepin_detail_state"
    assert site.calls[restore_index + 1] == "observe_liepin_search_state"
    assert any(event.get("action_kind") == "open_detail_succeeded" for event in site.events)
    assert any(event.get("action_kind") == "wait_detail_ready" and event.get("ok") is True for event in site.events)
    assert any(event.get("action_kind") == "observe_detail" and event.get("ok") is True for event in site.events)
    assert any(event.get("action_kind") == "capture_detail_succeeded" for event in site.events)
    assert any(event.get("action_kind") == "return_to_search_after_capture" for event in site.events)


def test_workflow_opens_from_structured_detail_targets_without_raw_ref_token() -> None:
    site = FakeLiepinSearchWorkflowSite(
        structured_cards=[
            [{"ref": "70", "provider_rank": 1}],
        ],
        search_states=[
            _search_state_with_detail_targets("70", text="cards visible"),
            _search_state_with_detail_targets("70", text="cards visible"),
            _search_state_with_detail_targets("70", text="cards visible"),
        ],
    )

    envelope = LiepinSearchWorkflow(site=site).search_detail_backed_resumes(_request(target_resumes=1))

    assert envelope["status"] == "succeeded"
    assert envelope["resumes_returned"] == 1
    assert "open_liepin_detail" in site.calls
    assert any(event.get("action_kind") == "open_detail_succeeded" for event in site.events)


def test_workflow_blocks_when_initial_card_extraction_pre_state_fails_without_debug_reason() -> None:
    site = FakeLiepinSearchWorkflowSite(
        search_states=[
            OpenCliBrowserResult(
                ok=False,
                action="state",
                safe_reason_code="liepin_opencli_results_not_ready",
            )
        ]
    )

    envelope = LiepinSearchWorkflow(site=site).search_detail_backed_resumes(_request())

    assert envelope["status"] == "blocked"
    assert envelope["safe_reason_code"] == "liepin_opencli_results_not_ready"
    assert "extract_structured_liepin_cards" not in site.calls
    assert "precondition_failed" not in repr(site.events)
    assert "postcondition_failed" not in repr(site.events)


def test_workflow_blocks_when_initial_card_extraction_post_state_fails_without_debug_reason() -> None:
    site = FakeLiepinSearchWorkflowSite(
        search_states=[
            OpenCliBrowserResult(ok=True, action="state"),
            OpenCliBrowserResult(
                ok=False,
                action="state",
                safe_reason_code="liepin_opencli_results_not_ready",
            ),
        ]
    )

    envelope = LiepinSearchWorkflow(site=site).search_detail_backed_resumes(_request())

    assert envelope["status"] == "blocked"
    assert envelope["safe_reason_code"] == "liepin_opencli_results_not_ready"
    assert "extract_structured_liepin_cards" in site.calls
    assert "precondition_failed" not in repr(site.events)
    assert "postcondition_failed" not in repr(site.events)


def test_workflow_blocks_when_no_detail_can_be_captured() -> None:
    site = FakeLiepinSearchWorkflowSite(capture_ok=False)

    envelope = LiepinSearchWorkflow(site=site).search_detail_backed_resumes(_request(target_resumes=1))

    assert envelope["status"] == "blocked"
    assert envelope["safe_reason_code"] == "liepin_opencli_detail_not_opened"
    assert "blocked_resumes_envelope" in site.calls
    assert any(event.get("action_kind") == "capture_detail_failed" for event in site.events)


def test_workflow_open_action_failure_skips_wait_and_capture_without_debug_reason() -> None:
    site = FakeLiepinSearchWorkflowSite(
        open_ok=False,
        open_safe_reason_code="liepin_opencli_detail_not_opened",
        structured_cards=[
            [{"ref": "70", "provider_rank": 1}],
        ],
    )

    envelope = LiepinSearchWorkflow(site=site).search_detail_backed_resumes(_request(target_resumes=1))

    assert envelope["status"] == "blocked"
    assert envelope["safe_reason_code"] == "liepin_opencli_detail_not_opened"
    assert "open_liepin_detail" in site.calls
    assert "wait_liepin_detail_ready" not in site.calls
    assert "capture_liepin_detail_resume" not in site.calls
    failed_events = [event for event in site.events if event.get("action_kind") == "open_detail_failed"]
    assert failed_events
    assert failed_events[-1]["safe_reason_code"] == "liepin_opencli_detail_not_opened"
    assert not any(event.get("action_kind") == "open_detail_succeeded" for event in site.events)
    assert "precondition_failed" not in repr(site.events)
    assert "postcondition_failed" not in repr(site.events)


def test_workflow_open_post_observe_failure_skips_wait_and_capture_without_debug_reason() -> None:
    site = FakeLiepinSearchWorkflowSite(
        structured_cards=[
            [{"ref": "70", "provider_rank": 1}],
        ],
        detail_states=[
            OpenCliBrowserResult(
                ok=False,
                action="state",
                safe_reason_code="liepin_opencli_detail_not_opened",
            ),
        ],
    )

    envelope = LiepinSearchWorkflow(site=site).search_detail_backed_resumes(_request(target_resumes=1))

    assert envelope["status"] == "blocked"
    assert envelope["safe_reason_code"] == "liepin_opencli_detail_not_opened"
    assert "open_liepin_detail" in site.calls
    assert "wait_liepin_detail_ready" not in site.calls
    assert "capture_liepin_detail_resume" not in site.calls
    failed_events = [event for event in site.events if event.get("action_kind") == "open_detail_failed"]
    assert failed_events
    assert failed_events[-1]["safe_reason_code"] == "liepin_opencli_detail_not_opened"
    assert not any(event.get("action_kind") == "open_detail_succeeded" for event in site.events)
    assert "precondition_failed" not in repr(site.events)
    assert "postcondition_failed" not in repr(site.events)


def test_workflow_wait_detail_ready_failure_skips_capture_without_debug_reason() -> None:
    site = FakeLiepinSearchWorkflowSite(
        wait_ok=False,
        wait_safe_reason_code="liepin_opencli_detail_not_opened",
    )

    envelope = LiepinSearchWorkflow(site=site).search_detail_backed_resumes(_request(target_resumes=1))

    assert envelope["status"] == "blocked"
    assert envelope["safe_reason_code"] == "liepin_opencli_detail_not_opened"
    assert "wait_liepin_detail_ready" in site.calls
    assert "capture_liepin_detail_resume" not in site.calls
    failed_events = [event for event in site.events if event.get("action_kind") == "wait_detail_ready"]
    assert failed_events
    assert failed_events[-1]["ok"] is False
    assert failed_events[-1]["safe_reason_code"] == "liepin_opencli_detail_not_opened"
    assert "precondition_failed" not in repr(site.events)
    assert "postcondition_failed" not in repr(site.events)


def test_workflow_wait_detail_ready_post_observe_failure_skips_capture_without_debug_reason() -> None:
    site = FakeLiepinSearchWorkflowSite(
        structured_cards=[
            [{"ref": "70", "provider_rank": 1}],
        ],
        detail_states=[
            OpenCliBrowserResult(ok=True, action="state"),
            OpenCliBrowserResult(ok=True, action="state"),
            OpenCliBrowserResult(
                ok=False,
                action="state",
                safe_reason_code="liepin_opencli_detail_not_opened",
            ),
        ]
    )

    envelope = LiepinSearchWorkflow(site=site).search_detail_backed_resumes(_request(target_resumes=1))

    assert envelope["status"] == "blocked"
    assert envelope["safe_reason_code"] == "liepin_opencli_detail_not_opened"
    assert "wait_liepin_detail_ready" in site.calls
    assert "capture_liepin_detail_resume" not in site.calls
    failed_events = [event for event in site.events if event.get("action_kind") == "wait_detail_ready"]
    assert failed_events
    assert failed_events[-1]["ok"] is False
    assert failed_events[-1]["safe_reason_code"] == "liepin_opencli_detail_not_opened"
    assert "precondition_failed" not in repr(site.events)
    assert "postcondition_failed" not in repr(site.events)


def test_workflow_does_not_open_visible_card_when_latest_search_state_lost_ref() -> None:
    site = FakeLiepinSearchWorkflowSite(
        structured_cards=[
            [{"ref": "70", "provider_rank": 1}],
        ],
        search_states=[
            _search_state_with_detail_targets("70"),
            _search_state_with_detail_targets("70"),
            _search_state_with_detail_targets("71"),
        ],
    )

    envelope = LiepinSearchWorkflow(site=site).search_detail_backed_resumes(_request(target_resumes=1))

    assert envelope["status"] == "blocked"
    assert envelope["safe_reason_code"] == "liepin_opencli_detail_not_opened"
    assert "open_liepin_detail" not in site.calls
    assert not any(event.get("action_kind") == "open_detail_succeeded" for event in site.events)
    failed_events = [event for event in site.events if event.get("action_kind") == "open_detail_failed"]
    assert failed_events
    assert failed_events[-1]["safe_reason_code"] == "liepin_opencli_detail_not_opened"
    assert "debug_reason" not in repr(site.events)


def test_workflow_capture_failure_propagates_safe_reason_without_debug_reason() -> None:
    site = FakeLiepinSearchWorkflowSite(
        capture_ok=False,
        capture_safe_reason_code="liepin_opencli_detail_payload_malformed",
    )

    envelope = LiepinSearchWorkflow(site=site).search_detail_backed_resumes(_request(target_resumes=1))

    assert envelope["status"] == "blocked"
    assert envelope["safe_reason_code"] == "liepin_opencli_detail_payload_malformed"
    failed_events = [event for event in site.events if event.get("action_kind") == "capture_detail_failed"]
    assert failed_events
    assert failed_events[-1]["safe_reason_code"] == "liepin_opencli_detail_payload_malformed"
    assert "debug_reason" not in repr(site.events)


def test_workflow_capture_post_observe_failure_is_not_counted_as_success() -> None:
    site = FakeLiepinSearchWorkflowSite(
        structured_cards=[
            [{"ref": "70", "provider_rank": 1}],
        ],
        detail_states=[
            OpenCliBrowserResult(ok=True, action="state"),
            OpenCliBrowserResult(ok=True, action="state"),
            OpenCliBrowserResult(ok=True, action="state"),
            OpenCliBrowserResult(ok=True, action="state"),
            OpenCliBrowserResult(
                ok=False,
                action="state",
                safe_reason_code="liepin_opencli_detail_not_opened",
            ),
        ]
    )

    envelope = LiepinSearchWorkflow(site=site).search_detail_backed_resumes(_request(target_resumes=1))

    assert envelope["status"] == "blocked"
    assert envelope["resumes_returned"] == 0
    assert envelope["safe_reason_code"] == "liepin_opencli_detail_not_opened"
    assert not any(event.get("action_kind") == "capture_detail_succeeded" for event in site.events)
    failed_events = [event for event in site.events if event.get("action_kind") == "capture_detail_failed"]
    assert failed_events
    assert failed_events[-1]["safe_reason_code"] == "liepin_opencli_detail_not_opened"
    assert "debug_reason" not in repr(site.events)


def test_workflow_discards_capture_written_before_failed_post_observe_and_returns_only_later_success() -> None:
    site = FakeLiepinSearchWorkflowSite(
        structured_cards=[
            [{"ref": "70", "provider_rank": 1}, {"ref": "71", "provider_rank": 2}],
        ],
        search_states=[
            _search_state_with_detail_targets("70", "71"),
            _search_state_with_detail_targets("70", "71"),
            _search_state_with_detail_targets("70", "71"),
            _search_state_with_detail_targets("70", "71"),
        ],
        detail_states=[
            OpenCliBrowserResult(ok=True, action="state"),
            OpenCliBrowserResult(ok=True, action="state"),
            OpenCliBrowserResult(ok=True, action="state"),
            OpenCliBrowserResult(ok=True, action="state"),
            OpenCliBrowserResult(
                ok=False,
                action="state",
                safe_reason_code="liepin_opencli_detail_not_opened",
            ),
            OpenCliBrowserResult(ok=True, action="state"),
            OpenCliBrowserResult(ok=True, action="state"),
            OpenCliBrowserResult(ok=True, action="state"),
            OpenCliBrowserResult(ok=True, action="state"),
            OpenCliBrowserResult(ok=True, action="state"),
        ],
    )

    envelope = LiepinSearchWorkflow(site=site).search_detail_backed_resumes(_request(target_resumes=1))

    assert envelope["status"] == "succeeded"
    assert envelope["resumes_returned"] == 1
    assert [resume["provider_rank"] for resume in envelope["resumes"]] == [2]
    assert "discard_liepin_detail_resume" in site.calls
    assert site.calls.count("capture_liepin_detail_resume") == 2
    failed_events = [event for event in site.events if event.get("action_kind") == "capture_detail_failed"]
    assert failed_events
    assert failed_events[-1]["rank"] == 1
    assert failed_events[-1]["safe_reason_code"] == "liepin_opencli_detail_not_opened"


def test_workflow_restore_failure_emits_safe_reason_and_does_not_continue_without_cached_urls() -> None:
    site = FakeLiepinSearchWorkflowSite(
        restore_ok=False,
        detail_urls_available=False,
        structured_cards=[
            [{"ref": "70", "provider_rank": 1}, {"ref": "71", "provider_rank": 2}],
        ],
    )

    envelope = LiepinSearchWorkflow(site=site).search_detail_backed_resumes(_request(target_resumes=2))

    assert envelope["status"] == "succeeded"
    assert envelope["resumes_returned"] == 1
    assert site.calls.count("open_liepin_detail") == 1
    assert site.calls.count("observe_liepin_search_state") == 4
    assert "open_liepin_detail_cached_url" not in site.calls
    assert not any(event.get("open_mode") == "cached_url" for event in site.events)
    restore_events = [event for event in site.events if event.get("action_kind") == "return_to_search_after_capture"]
    assert restore_events[-1]["ok"] is False
    assert restore_events[-1]["safe_reason_code"] == "liepin_opencli_search_restore_failed"


def test_workflow_refresh_empty_does_not_enter_cached_mode_without_remaining_cached_urls() -> None:
    site = FakeLiepinSearchWorkflowSite(
        detail_urls_available=False,
        structured_cards=[
            [{"ref": "70", "provider_rank": 1}, {"ref": "71", "provider_rank": 2}],
            [],
        ],
        search_states=[
            _search_state_with_detail_targets("70", "71"),
            _search_state_with_detail_targets("70", "71"),
            _search_state_with_detail_targets("70", "71"),
            _search_state_with_detail_targets("71"),
            _search_state_with_detail_targets(),
            _search_state_with_detail_targets(),
            _search_state_with_detail_targets(),
        ],
    )

    envelope = LiepinSearchWorkflow(site=site).search_detail_backed_resumes(_request(target_resumes=2))

    assert envelope["status"] == "succeeded"
    assert envelope["resumes_returned"] == 1
    assert "open_liepin_detail_cached_url" not in site.calls
    assert not any(event.get("open_mode") == "cached_url" for event in site.events)
    assert site.calls.count("open_liepin_detail") == 1


def test_workflow_restore_failure_continues_with_cached_detail_urls() -> None:
    site = FakeLiepinSearchWorkflowSite(
        restore_ok=False,
        structured_cards=[
            [{"ref": "70", "provider_rank": 1}, {"ref": "71", "provider_rank": 2}],
        ],
    )

    envelope = LiepinSearchWorkflow(site=site).search_detail_backed_resumes(_request(target_resumes=2))

    assert envelope["status"] == "succeeded"
    assert envelope["resumes_returned"] == 2
    assert site.calls.count("open_liepin_detail") == 1
    assert site.calls.count("open_liepin_detail_cached_url") == 1
