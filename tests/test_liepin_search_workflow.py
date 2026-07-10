from __future__ import annotations

from dataclasses import dataclass, field
import inspect
from typing import Any, cast

import pytest

from seektalent.opencli_browser.contracts import OpenCliBrowserResult
from seektalent.providers.liepin.detail_open_claims import DetailOpenClaimLedger, DetailOpenClaimSearchContext
from seektalent.providers.liepin.liepin_search_workflow import (
    LiepinSearchWorkflow,
    LiepinSearchWorkflowRequest,
)
from seektalent.providers.liepin.liepin_site_adapter import LiepinSiteAdapter, _LiepinSearchWorkflowSite
from seektalent.providers.liepin.liepin_site_parsing import stable_liepin_detail_candidate_key_hash


@dataclass
class FakeLiepinSearchWorkflowSite:
    open_ok: bool = True
    open_safe_reason_code: str = "liepin_opencli_detail_not_opened"
    capture_ok: bool = True
    capture_safe_reason_code: str = "liepin_opencli_detail_not_opened"
    claim_aware_capture_ok: bool = True
    claim_aware_capture_safe_reason_code: str = "liepin_opencli_detail_not_opened"
    wait_ok: bool = True
    wait_safe_reason_code: str = "liepin_opencli_detail_not_opened"
    restore_ok: bool = True
    detail_urls_available: bool = True
    detail_urls_by_ref: dict[str, str | None] = field(default_factory=dict)
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
    claim_aware_capture_expected_keys: list[str] = field(default_factory=list)
    opened_refs: list[str] = field(default_factory=list)
    cached_opened_refs: list[str] = field(default_factory=list)
    cached_opened_detail_urls: list[str] = field(default_factory=list)
    ref_probe_opened_subjects: list[str] = field(default_factory=list)
    open_results: list[OpenCliBrowserResult] = field(default_factory=list)
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
        if ref in self.detail_urls_by_ref:
            return self.detail_urls_by_ref[ref]
        if not self.detail_urls_available:
            return None
        return f"https://h.liepin.com/resume/showresumedetail/?res_id_encode={ref}"

    def open_liepin_detail(self, *, source_run_id: str, ref: str, rank: int) -> OpenCliBrowserResult:
        del source_run_id
        self.calls.append("open_liepin_detail")
        self.opened_refs.append(ref)
        if self.open_results:
            return self.open_results.pop(0)
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
        del source_run_id
        self.calls.append("open_liepin_detail_cached_url")
        self.cached_opened_refs.append(ref)
        self.cached_opened_detail_urls.append(detail_url)
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

    def _capture_liepin_detail_resume_claim_aware(
        self,
        *,
        source_run_id: str,
        rank: int,
        expected_provider_candidate_key_hash: str,
        require_ready: bool = True,
    ) -> OpenCliBrowserResult:
        del source_run_id
        self.calls.append("capture_liepin_detail_resume_claim_aware")
        self.capture_require_ready_values.append(require_ready)
        self.claim_aware_capture_expected_keys.append(expected_provider_candidate_key_hash)
        if not self.claim_aware_capture_ok:
            return OpenCliBrowserResult(
                ok=False,
                action="capture_liepin_detail_resume",
                safe_reason_code=self.claim_aware_capture_safe_reason_code,
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


def _private_claim_context(ledger: DetailOpenClaimLedger) -> DetailOpenClaimSearchContext:
    return DetailOpenClaimSearchContext(
        detail_open_claim_ledger=ledger,
        logical_round_no=4,
        query_instance_id="logical-query-4",
    )


def _detail_key(subject: str) -> str:
    key = stable_liepin_detail_candidate_key_hash(
        f"https://h.liepin.com/resume/showresumedetail/?res_id_encode={subject}"
    )
    assert key is not None
    return key


class RecordingDetailOpenClaimLedger(DetailOpenClaimLedger):
    def __init__(self) -> None:
        super().__init__({})
        self.transitions: list[tuple[str, str]] = []

    def try_claim(self, provider_candidate_key_hash: str) -> bool:
        self.transitions.append(("try_claim", provider_candidate_key_hash))
        return super().try_claim(provider_candidate_key_hash)

    def record_browser_open_attempt(self, provider_candidate_key_hash: str) -> None:
        self.transitions.append(("record_browser_open_attempt", provider_candidate_key_hash))
        super().record_browser_open_attempt(provider_candidate_key_hash)

    def mark_opened(self, provider_candidate_key_hash: str) -> None:
        self.transitions.append(("mark_opened", provider_candidate_key_hash))
        super().mark_opened(provider_candidate_key_hash)

    def mark_terminal_failed(self, provider_candidate_key_hash: str, *, safe_reason_code: str) -> None:
        self.transitions.append(("mark_terminal_failed", provider_candidate_key_hash))
        super().mark_terminal_failed(provider_candidate_key_hash, safe_reason_code=safe_reason_code)

    def release_unattempted(self, provider_candidate_key_hash: str) -> None:
        self.transitions.append(("release_unattempted", provider_candidate_key_hash))
        super().release_unattempted(provider_candidate_key_hash)


def test_workflow_opens_details_until_target_count() -> None:
    site = FakeLiepinSearchWorkflowSite()

    envelope = LiepinSearchWorkflow(site=site).search_detail_backed_resumes(_request())

    assert envelope["status"] == "succeeded"
    assert envelope["resumes_returned"] == 2
    assert site.calls.count("open_liepin_detail") == 2
    assert "search_liepin_cards" in site.calls
    assert "extract_structured_liepin_cards" in site.calls
    assert "finalize_liepin_resumes" in site.calls


def test_private_claim_context_route_preserves_current_detail_search_behavior() -> None:
    site = FakeLiepinSearchWorkflowSite()
    ledger = DetailOpenClaimLedger({})

    envelope = LiepinSearchWorkflow(site=site)._search_detail_backed_resumes_with_detail_open_claim_context(
        _request(target_resumes=1),
        detail_open_claim_context=DetailOpenClaimSearchContext(
            detail_open_claim_ledger=ledger,
            logical_round_no=4,
            query_instance_id="logical-query-4",
        ),
    )

    assert envelope["status"] == "succeeded"
    assert envelope["resumes_returned"] == 1
    assert "open_liepin_detail" not in site.calls
    assert site.calls.count("open_liepin_detail_cached_url") == 1
    assert site.calls.count("capture_liepin_detail_resume_claim_aware") == 1
    assert "capture_liepin_detail_resume" not in site.calls
    key = _detail_key("70")
    assert site.claim_aware_capture_expected_keys == [key]
    assert ledger.snapshot()[key].status == "opened"
    assert ledger.snapshot()[key].browser_open_attempt_count == 1


def test_private_claim_context_skips_preclaimed_candidate_before_detail_open() -> None:
    key = _detail_key("70")
    ledger = DetailOpenClaimLedger({})
    assert ledger.try_claim(key) is True
    site = FakeLiepinSearchWorkflowSite(
        structured_cards=[[{"ref": "70", "provider_rank": 1}]],
        search_states=[_search_state_with_detail_targets("70") for _ in range(3)],
    )

    envelope = LiepinSearchWorkflow(site=site)._search_detail_backed_resumes_with_detail_open_claim_context(
        _request(target_resumes=1),
        detail_open_claim_context=_private_claim_context(ledger),
    )

    assert envelope["status"] == "blocked"
    assert "open_liepin_detail" not in site.calls
    assert "open_liepin_detail_cached_url" not in site.calls
    assert ledger.snapshot()[key].status == "claimed"


def test_private_claim_context_skips_opened_subject_after_rank_change() -> None:
    ledger = DetailOpenClaimLedger({})
    detail_url = "https://h.liepin.com/resume/showresumedetail/?res_id_encode=sameSubject"
    first_site = FakeLiepinSearchWorkflowSite(
        structured_cards=[[{"ref": "first", "provider_rank": 1}]],
        detail_urls_by_ref={"first": detail_url},
        search_states=[_search_state_with_detail_targets("first") for _ in range(3)],
    )

    first = LiepinSearchWorkflow(site=first_site)._search_detail_backed_resumes_with_detail_open_claim_context(
        _request(target_resumes=1),
        detail_open_claim_context=_private_claim_context(ledger),
    )

    second_site = FakeLiepinSearchWorkflowSite(
        structured_cards=[[{"ref": "later", "provider_rank": 2}]],
        detail_urls_by_ref={"later": detail_url},
        search_states=[_search_state_with_detail_targets("later") for _ in range(3)],
    )
    second = LiepinSearchWorkflow(site=second_site)._search_detail_backed_resumes_with_detail_open_claim_context(
        _request(target_resumes=1),
        detail_open_claim_context=_private_claim_context(ledger),
    )

    key = _detail_key("sameSubject")
    assert first["status"] == "succeeded"
    assert "open_liepin_detail" not in first_site.calls
    assert first_site.calls.count("open_liepin_detail_cached_url") == 1
    assert second["status"] == "blocked"
    assert "open_liepin_detail" not in second_site.calls
    assert "open_liepin_detail_cached_url" not in second_site.calls
    assert ledger.snapshot()[key].status == "opened"


def test_private_claim_context_drops_stale_rank_url_when_refresh_has_no_identity() -> None:
    ledger = DetailOpenClaimLedger({})
    first_url = "https://h.liepin.com/resume/showresumedetail/?res_id_encode=firstSubject"
    old_rank_two_url = "https://h.liepin.com/resume/showresumedetail/?res_id_encode=oldRankTwoSubject"
    site = FakeLiepinSearchWorkflowSite(
        structured_cards=[
            [
                {"ref": "first", "provider_rank": 1},
                {"ref": "old-rank-two", "provider_rank": 2},
            ],
            [{"ref": "new-rank-two", "provider_rank": 2}],
        ],
        detail_urls_by_ref={
            "first": first_url,
            "old-rank-two": old_rank_two_url,
            "new-rank-two": None,
        },
        search_states=[
            _search_state_with_detail_targets("first", "old-rank-two"),
            _search_state_with_detail_targets("first", "old-rank-two"),
            _search_state_with_detail_targets("first", "old-rank-two"),
            _search_state_with_detail_targets("new-rank-two"),
            _search_state_with_detail_targets("new-rank-two"),
            _search_state_with_detail_targets("new-rank-two"),
            _search_state_with_detail_targets("new-rank-two"),
        ],
    )

    envelope = LiepinSearchWorkflow(site=site)._search_detail_backed_resumes_with_detail_open_claim_context(
        _request(target_resumes=2),
        detail_open_claim_context=_private_claim_context(ledger),
    )

    first_key = _detail_key("firstSubject")
    old_rank_two_key = _detail_key("oldRankTwoSubject")
    claims = ledger.snapshot()
    assert envelope["status"] == "succeeded"
    assert envelope["resumes_returned"] == 1
    assert site.opened_refs == []
    assert site.cached_opened_refs == ["first"]
    assert "new-rank-two" not in site.cached_opened_refs
    assert set(claims) == {first_key}
    assert claims[first_key].status == "opened"
    assert old_rank_two_key not in claims


def test_private_claim_context_opens_validated_url_without_ref_probe_drift() -> None:
    class RefProbeDriftSite(FakeLiepinSearchWorkflowSite):
        def open_liepin_detail(self, *, source_run_id: str, ref: str, rank: int) -> OpenCliBrowserResult:
            del source_run_id
            self.calls.append("open_liepin_detail")
            self.opened_refs.append(ref)
            self.ref_probe_opened_subjects.append("subjectB")
            return OpenCliBrowserResult(ok=True, action="open_liepin_detail", counts={"rank": rank})

    detail_url_for_subject_a = "https://h.liepin.com/resume/showresumedetail/?res_id_encode=subjectA"
    ledger = DetailOpenClaimLedger({})
    site = RefProbeDriftSite(
        structured_cards=[[{"ref": "drifting-ref", "provider_rank": 1}]],
        detail_urls_by_ref={"drifting-ref": detail_url_for_subject_a},
        search_states=[_search_state_with_detail_targets("drifting-ref") for _ in range(3)],
    )

    envelope = LiepinSearchWorkflow(site=site)._search_detail_backed_resumes_with_detail_open_claim_context(
        _request(target_resumes=1),
        detail_open_claim_context=_private_claim_context(ledger),
    )

    subject_a_key = _detail_key("subjectA")
    claims = ledger.snapshot()
    assert envelope["status"] == "succeeded"
    assert site.opened_refs == []
    assert site.ref_probe_opened_subjects == []
    assert site.cached_opened_refs == ["drifting-ref"]
    assert site.cached_opened_detail_urls == [detail_url_for_subject_a]
    assert site.claim_aware_capture_expected_keys == [subject_a_key]
    assert set(claims) == {subject_a_key}
    assert claims[subject_a_key].status == "opened"


@pytest.mark.parametrize(
    "detail_url",
    [
        None,
        "https://h.liepin.com/resume/showresumedetail/?res_id_encode=%73ubject",
    ],
)
def test_private_claim_context_skips_candidate_without_strict_identity(detail_url: str | None) -> None:
    ledger = DetailOpenClaimLedger({})
    site = FakeLiepinSearchWorkflowSite(
        structured_cards=[[{"ref": "70", "provider_rank": 1}]],
        detail_urls_by_ref={"70": detail_url},
        search_states=[_search_state_with_detail_targets("70") for _ in range(3)],
    )

    envelope = LiepinSearchWorkflow(site=site)._search_detail_backed_resumes_with_detail_open_claim_context(
        _request(target_resumes=1),
        detail_open_claim_context=_private_claim_context(ledger),
    )

    assert envelope["status"] == "blocked"
    assert envelope["safe_reason_code"] == "liepin_opencli_candidate_identity_missing"
    assert ledger.snapshot() == {}
    assert "open_liepin_detail" not in site.calls
    assert "open_liepin_detail_cached_url" not in site.calls


@pytest.mark.parametrize(
    ("safe_reason_code", "expected_attempts"),
    [
        ("liepin_opencli_forbidden_command", 1),
        ("liepin_opencli_detail_not_opened", 2),
    ],
)
def test_private_claim_context_terminalizes_attempted_open_before_later_workflow(
    safe_reason_code: str,
    expected_attempts: int,
) -> None:
    ledger = DetailOpenClaimLedger({})
    site = FakeLiepinSearchWorkflowSite(
        open_ok=False,
        open_safe_reason_code=safe_reason_code,
        structured_cards=[[{"ref": "70", "provider_rank": 1}]],
        search_states=[_search_state_with_detail_targets("70") for _ in range(4)],
    )

    first = LiepinSearchWorkflow(site=site)._search_detail_backed_resumes_with_detail_open_claim_context(
        _request(target_resumes=1),
        detail_open_claim_context=_private_claim_context(ledger),
    )

    second_site = FakeLiepinSearchWorkflowSite(
        structured_cards=[[{"ref": "70", "provider_rank": 2}]],
        search_states=[_search_state_with_detail_targets("70") for _ in range(3)],
    )
    second = LiepinSearchWorkflow(site=second_site)._search_detail_backed_resumes_with_detail_open_claim_context(
        _request(target_resumes=1),
        detail_open_claim_context=_private_claim_context(ledger),
    )

    key = _detail_key("70")
    claim = ledger.snapshot()[key]
    assert first["status"] == "blocked"
    assert "open_liepin_detail" not in site.calls
    assert site.calls.count("open_liepin_detail_cached_url") == expected_attempts
    assert claim.status == "terminal_failed"
    assert claim.browser_open_attempt_count == expected_attempts
    assert second["status"] == "blocked"
    assert "open_liepin_detail" not in second_site.calls
    assert "open_liepin_detail_cached_url" not in second_site.calls


def test_private_claim_context_terminalizes_wait_failure_after_browser_open() -> None:
    ledger = DetailOpenClaimLedger({})
    site = FakeLiepinSearchWorkflowSite(
        wait_ok=False,
        structured_cards=[[{"ref": "70", "provider_rank": 1}]],
        search_states=[_search_state_with_detail_targets("70") for _ in range(3)],
    )

    envelope = LiepinSearchWorkflow(site=site)._search_detail_backed_resumes_with_detail_open_claim_context(
        _request(target_resumes=1),
        detail_open_claim_context=_private_claim_context(ledger),
    )

    claim = ledger.snapshot()[_detail_key("70")]
    assert envelope["status"] == "blocked"
    assert claim.status == "terminal_failed"
    assert claim.browser_open_attempt_count == 1


def test_private_claim_context_terminalizes_capture_identity_mismatch_without_candidate() -> None:
    ledger = DetailOpenClaimLedger({})
    site = FakeLiepinSearchWorkflowSite(
        claim_aware_capture_ok=False,
        claim_aware_capture_safe_reason_code="liepin_opencli_candidate_identity_mismatch",
        structured_cards=[[{"ref": "70", "provider_rank": 1}]],
        search_states=[_search_state_with_detail_targets("70") for _ in range(3)],
    )

    envelope = LiepinSearchWorkflow(site=site)._search_detail_backed_resumes_with_detail_open_claim_context(
        _request(target_resumes=1),
        detail_open_claim_context=_private_claim_context(ledger),
    )

    claim = ledger.snapshot()[_detail_key("70")]
    assert envelope["status"] == "blocked"
    assert envelope["resumes"] == []
    assert envelope["safe_reason_code"] == "liepin_opencli_candidate_identity_mismatch"
    assert claim.status == "terminal_failed"
    assert claim.browser_open_attempt_count == 1
    assert site.claim_aware_capture_expected_keys == [_detail_key("70")]
    assert "capture_liepin_detail_resume" not in site.calls


def test_private_claim_context_terminalizes_escaping_open_exception() -> None:
    class ExplodingOpenSite(FakeLiepinSearchWorkflowSite):
        def open_liepin_detail_cached_url(
            self,
            *,
            source_run_id: str,
            ref: str,
            rank: int,
            detail_url: str,
        ) -> OpenCliBrowserResult:
            del source_run_id, ref, rank, detail_url
            self.calls.append("open_liepin_detail_cached_url")
            raise RuntimeError("open exploded")

    ledger = DetailOpenClaimLedger({})
    site = ExplodingOpenSite(
        structured_cards=[[{"ref": "70", "provider_rank": 1}]],
        search_states=[_search_state_with_detail_targets("70") for _ in range(3)],
    )

    with pytest.raises(RuntimeError, match="open exploded"):
        LiepinSearchWorkflow(site=site)._search_detail_backed_resumes_with_detail_open_claim_context(
            _request(target_resumes=1),
            detail_open_claim_context=_private_claim_context(ledger),
        )

    claim = ledger.snapshot()[_detail_key("70")]
    assert claim.status == "terminal_failed"
    assert claim.browser_open_attempt_count == 1


def test_private_claim_context_terminalizes_escaping_capture_exception() -> None:
    class ExplodingCaptureSite(FakeLiepinSearchWorkflowSite):
        def _capture_liepin_detail_resume_claim_aware(
            self,
            *,
            source_run_id: str,
            rank: int,
            expected_provider_candidate_key_hash: str,
            require_ready: bool = True,
        ) -> OpenCliBrowserResult:
            del source_run_id, rank, expected_provider_candidate_key_hash, require_ready
            self.calls.append("capture_liepin_detail_resume_claim_aware")
            raise RuntimeError("capture exploded")

    ledger = DetailOpenClaimLedger({})
    site = ExplodingCaptureSite(
        structured_cards=[[{"ref": "70", "provider_rank": 1}]],
        search_states=[_search_state_with_detail_targets("70") for _ in range(3)],
    )

    with pytest.raises(RuntimeError, match="capture exploded"):
        LiepinSearchWorkflow(site=site)._search_detail_backed_resumes_with_detail_open_claim_context(
            _request(target_resumes=1),
            detail_open_claim_context=_private_claim_context(ledger),
        )

    claim = ledger.snapshot()[_detail_key("70")]
    assert claim.status == "terminal_failed"
    assert claim.browser_open_attempt_count == 1


def test_private_claim_context_releases_preopen_failure_without_browser_action() -> None:
    ledger = RecordingDetailOpenClaimLedger()
    site = FakeLiepinSearchWorkflowSite(
        structured_cards=[[{"ref": "70", "provider_rank": 1}]],
        search_states=[
            _search_state_with_detail_targets("70"),
            _search_state_with_detail_targets("70"),
            OpenCliBrowserResult(
                ok=False,
                action="state",
                safe_reason_code="liepin_opencli_detail_not_opened",
            ),
        ],
    )

    envelope = LiepinSearchWorkflow(site=site)._search_detail_backed_resumes_with_detail_open_claim_context(
        _request(target_resumes=1),
        detail_open_claim_context=_private_claim_context(ledger),
    )

    key = _detail_key("70")
    assert envelope["status"] == "blocked"
    assert "open_liepin_detail" not in site.calls
    assert ledger.transitions == [("try_claim", key), ("release_unattempted", key)]
    assert ledger.try_claim(key) is True


def test_private_workflow_site_forwards_claim_aware_capture_without_widening_public_signature() -> None:
    expected_key = _detail_key("70")

    class PrivateCaptureAdapter:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def _capture_liepin_detail_resume_claim_aware(
            self,
            *,
            source_run_id: str,
            rank: int,
            expected_provider_candidate_key_hash: str,
            require_ready: bool,
            emit_events: bool,
        ) -> OpenCliBrowserResult:
            self.calls.append(
                {
                    "source_run_id": source_run_id,
                    "rank": rank,
                    "expected_provider_candidate_key_hash": expected_provider_candidate_key_hash,
                    "require_ready": require_ready,
                    "emit_events": emit_events,
                }
            )
            return OpenCliBrowserResult(ok=True, action="capture_liepin_detail_resume")

    adapter = PrivateCaptureAdapter()
    site = _LiepinSearchWorkflowSite(adapter=cast(LiepinSiteAdapter, adapter))

    result = site._capture_liepin_detail_resume_claim_aware(
        source_run_id="run-1",
        rank=1,
        expected_provider_candidate_key_hash=expected_key,
        require_ready=False,
    )

    assert result.ok is True
    assert adapter.calls == [
        {
            "source_run_id": "run-1",
            "rank": 1,
            "expected_provider_candidate_key_hash": expected_key,
            "require_ready": False,
            "emit_events": False,
        }
    ]
    assert tuple(inspect.signature(LiepinSiteAdapter.capture_liepin_detail_resume).parameters) == (
        "self",
        "source_run_id",
        "rank",
    )


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
    assert envelope["safe_reason_code"] == "liepin_opencli_detail_open_retry_exhausted"
    assert site.calls.count("open_liepin_detail") == 2
    assert "wait_liepin_detail_ready" not in site.calls
    assert "capture_liepin_detail_resume" not in site.calls
    failed_events = [event for event in site.events if event.get("action_kind") == "open_detail_failed"]
    assert failed_events
    assert failed_events[-1]["safe_reason_code"] == "liepin_opencli_detail_not_opened"
    assert not any(event.get("action_kind") == "open_detail_succeeded" for event in site.events)
    assert "precondition_failed" not in repr(site.events)
    assert "postcondition_failed" not in repr(site.events)


def test_workflow_retries_same_detail_open_after_refreshing_search_state() -> None:
    site = FakeLiepinSearchWorkflowSite(
        structured_cards=[
            [{"ref": "70", "provider_rank": 1}],
        ],
        open_results=[
            OpenCliBrowserResult(
                ok=False,
                action="open_liepin_detail",
                safe_reason_code="liepin_opencli_detail_not_opened",
            ),
            OpenCliBrowserResult(ok=True, action="open_liepin_detail", counts={"rank": 1}),
        ],
        search_states=[
            _search_state_with_detail_targets("70"),
            _search_state_with_detail_targets("70"),
            _search_state_with_detail_targets("70"),
            _search_state_with_detail_targets("70"),
        ],
    )

    envelope = LiepinSearchWorkflow(site=site).search_detail_backed_resumes(_request(target_resumes=1))

    assert envelope["status"] == "succeeded"
    assert envelope["resumes_returned"] == 1
    open_indexes = [index for index, call in enumerate(site.calls) if call == "open_liepin_detail"]
    assert len(open_indexes) == 2
    assert all(site.calls[index - 1] == "observe_liepin_search_state" for index in open_indexes)
    retry_events = [event for event in site.events if event.get("action_kind") == "open_detail_retry_scheduled"]
    assert retry_events[-1]["rank"] == 1
    assert retry_events[-1]["safe_reason_code"] == "liepin_opencli_detail_not_opened"


def test_workflow_reports_detail_open_retry_exhausted_after_retries() -> None:
    site = FakeLiepinSearchWorkflowSite(
        open_ok=False,
        open_safe_reason_code="liepin_opencli_detail_not_opened",
        structured_cards=[
            [{"ref": "70", "provider_rank": 1}],
        ],
    )

    envelope = LiepinSearchWorkflow(site=site).search_detail_backed_resumes(_request(target_resumes=1))

    assert envelope["status"] == "blocked"
    assert envelope["safe_reason_code"] == "liepin_opencli_detail_open_retry_exhausted"
    assert site.calls.count("open_liepin_detail") == 2
    exhausted_events = [event for event in site.events if event.get("action_kind") == "open_detail_retry_exhausted"]
    assert exhausted_events[-1]["safe_reason_code"] == "liepin_opencli_detail_open_retry_exhausted"


def test_workflow_open_post_observe_failure_retries_before_wait_and_capture() -> None:
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

    assert envelope["status"] == "succeeded"
    assert envelope["resumes_returned"] == 1
    assert site.calls.count("open_liepin_detail") == 2
    assert "wait_liepin_detail_ready" in site.calls
    assert "capture_liepin_detail_resume" in site.calls
    failed_events = [event for event in site.events if event.get("action_kind") == "open_detail_failed"]
    assert failed_events
    assert failed_events[-1]["safe_reason_code"] == "liepin_opencli_detail_not_opened"
    assert any(event.get("action_kind") == "open_detail_succeeded" for event in site.events)
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
