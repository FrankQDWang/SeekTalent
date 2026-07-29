from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from seektalent.core.retrieval.provider_contract import ProviderSearchContinuation
from seektalent.opencli_browser.contracts import OpenCliBrowserResult
from seektalent.providers.liepin.first_page_continuation import CandidateState, LiepinFirstPageCandidate
from seektalent.providers.liepin.liepin_opencli_policy import LIEPIN_RECRUITER_SEARCH_URL

if TYPE_CHECKING:
    from seektalent.providers.liepin.liepin_site_adapter import LiepinSiteAdapter


@dataclass(frozen=True)
class _LiepinSearchWorkflowSite:
    adapter: LiepinSiteAdapter

    def save_liepin_first_page_continuation(
        self,
        *,
        source_run_id: str,
        logical_round_no: int,
        query_instance_id: str,
        keyword_query: str,
        visible_candidate_count: int,
        candidates: Sequence[LiepinFirstPageCandidate],
    ) -> ProviderSearchContinuation:
        return self.adapter._save_liepin_first_page_continuation(
            source_run_id=source_run_id,
            logical_round_no=logical_round_no,
            query_instance_id=query_instance_id,
            keyword_query=keyword_query,
            visible_candidate_count=visible_candidate_count,
            candidates=candidates,
        )

    def load_liepin_first_page_continuation(self, opaque_ref: str):
        return self.adapter._load_liepin_first_page_continuation(opaque_ref)

    def discard_liepin_first_page_continuation(self, opaque_ref: str) -> None:
        self.adapter._discard_liepin_first_page_continuation(opaque_ref)

    def mark_liepin_first_page_candidate(
        self,
        *,
        opaque_ref: str,
        rank: int,
        state: CandidateState,
    ) -> None:
        self.adapter._mark_liepin_first_page_candidate(opaque_ref=opaque_ref, rank=rank, state=state)

    def append_agent_event(self, source_run_id: str, event: Mapping[str, object]) -> None:
        self.adapter._append_agent_event(source_run_id, event)

    def search_liepin_cards(
        self,
        *,
        source_run_id: str,
        query: str,
        max_pages: int,
        max_cards: int,
        native_filters: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        return self.adapter.search_liepin_cards(
            source_run_id=source_run_id,
            query=query,
            max_pages=max_pages,
            max_cards=max_cards,
            native_filters=native_filters,
        )

    def extract_structured_liepin_cards(self, *, source_run_id: str, max_cards: int) -> OpenCliBrowserResult:
        return self.adapter.extract_structured_liepin_cards(source_run_id=source_run_id, max_cards=max_cards)

    def observe_liepin_search_state(self) -> OpenCliBrowserResult:
        return self.adapter.state()

    def observe_liepin_detail_state(self) -> OpenCliBrowserResult:
        return self.adapter.state()

    def run_liepin_details_operation(
        self,
        *,
        source_run_id: str,
        card_ref: str,
        rank: int,
        open_mode: str,
        provider_candidate_key_hash: str | None = None,
        expected_provider_candidate_key_hash: str | None = None,
    ) -> tuple[dict[str, object], OpenCliBrowserResult]:
        return self.adapter.run_liepin_details_operation(
            source_run_id=source_run_id,
            card_ref=card_ref,
            rank=rank,
            open_mode=open_mode,
            provider_candidate_key_hash=provider_candidate_key_hash,
            expected_provider_candidate_key_hash=expected_provider_candidate_key_hash,
        )

    def open_liepin_detail(self, *, source_run_id: str, ref: str, rank: int) -> OpenCliBrowserResult:
        raise RuntimeError("liepin_details_direct_browser_forbidden")

    def open_liepin_detail_cached_url(
        self,
        *,
        source_run_id: str,
        ref: str,
        rank: int,
        detail_url: str,
    ) -> OpenCliBrowserResult:
        raise RuntimeError("liepin_details_direct_browser_forbidden")

    def wait_liepin_detail_ready(self, *, source_run_id: str, rank: int) -> OpenCliBrowserResult:
        raise RuntimeError("liepin_details_direct_browser_forbidden")

    def capture_liepin_detail_resume(
        self,
        *,
        source_run_id: str,
        rank: int,
        require_ready: bool = True,
    ) -> OpenCliBrowserResult:
        raise RuntimeError("liepin_details_direct_browser_forbidden")

    def _capture_liepin_detail_resume_claim_aware(
        self,
        *,
        source_run_id: str,
        rank: int,
        expected_provider_candidate_key_hash: str,
        require_ready: bool = True,
    ) -> OpenCliBrowserResult:
        raise RuntimeError("liepin_details_direct_browser_forbidden")

    def discard_liepin_detail_resume(self, *, source_run_id: str, rank: int) -> None:
        self.adapter._discard_collected_liepin_detail_resume(source_run_id=source_run_id, rank=rank)

    def restore_liepin_search_page(self) -> str | None:
        if self.adapter._cards_operation_executor is not None:
            # Cached-locator details opens do not require main-owned search restore.
            return "source-port-managed"
        if self.adapter._automation.daemon_enabled:
            page_id, _before_urls = self.adapter._select_existing_liepin_search_tab(
                expected_url=LIEPIN_RECRUITER_SEARCH_URL
            )
            return page_id
        return self.adapter._select_canonical_liepin_search_page()

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
        return self.adapter.finalize_liepin_resumes(
            source_run_id=source_run_id,
            query=query,
            max_pages=max_pages,
            max_cards=max_cards,
            cards_seen=cards_seen,
            target_resumes=target_resumes,
        )

    def blocked_resumes_envelope(
        self,
        *,
        source_run_id: str,
        query: str,
        safe_reason_code: str | None,
        cards_seen: int,
    ) -> dict[str, object]:
        return self.adapter._blocked_resumes_envelope(
            source_run_id=source_run_id,
            query=query,
            safe_reason_code=safe_reason_code or "failed_provider_error",
            cards_seen=cards_seen,
        )
