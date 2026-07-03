from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from seektalent.opencli_browser.contracts import OpenCliBrowserError, OpenCliBrowserResult


@dataclass(frozen=True, kw_only=True)
class LiepinSearchWorkflowRequest:
    source_run_id: str
    query: str
    target_resumes: int
    max_pages: int
    max_cards: int
    native_filters: Mapping[str, object] | None = None


class LiepinSearchWorkflowSite(Protocol):
    def append_agent_event(self, source_run_id: str, event: Mapping[str, object]) -> None: ...

    def search_liepin_cards(
        self,
        *,
        source_run_id: str,
        query: str,
        max_pages: int,
        max_cards: int,
        native_filters: Mapping[str, object] | None = None,
    ) -> dict[str, object]: ...

    def extract_structured_liepin_cards(self, *, source_run_id: str, max_cards: int) -> OpenCliBrowserResult: ...

    def safe_liepin_detail_url_for_ref(self, ref: str) -> str | None: ...

    def open_liepin_detail(self, *, source_run_id: str, ref: str, rank: int) -> OpenCliBrowserResult: ...

    def open_liepin_detail_cached_url(
        self,
        *,
        source_run_id: str,
        ref: str,
        rank: int,
        detail_url: str,
    ) -> OpenCliBrowserResult: ...

    def capture_liepin_detail_resume(self, *, source_run_id: str, rank: int) -> OpenCliBrowserResult: ...

    def restore_liepin_search_page(self) -> str | None: ...

    def finalize_liepin_resumes(
        self,
        *,
        source_run_id: str,
        query: str,
        max_pages: int,
        max_cards: int,
        cards_seen: int | None = None,
        target_resumes: int | None = None,
    ) -> dict[str, object]: ...

    def blocked_resumes_envelope(
        self,
        *,
        source_run_id: str,
        query: str,
        safe_reason_code: str | None,
        cards_seen: int,
    ) -> dict[str, object]: ...


class LiepinSearchWorkflow:
    def __init__(self, *, site: LiepinSearchWorkflowSite) -> None:
        self._site = site

    def search_detail_backed_resumes(self, request: LiepinSearchWorkflowRequest) -> dict[str, object]:
        if request.target_resumes < 1 or request.target_resumes > 10:
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")

        self._append_event(
            request.source_run_id,
            {"action_kind": "search_cards_started", "route_kind": "search", "ok": True},
        )
        if request.native_filters:
            self._append_event(
                request.source_run_id,
                {"action_kind": "apply_filters_started", "route_kind": "search", "ok": True},
            )

        cards = self._site.search_liepin_cards(
            source_run_id=request.source_run_id,
            query=request.query,
            max_pages=request.max_pages,
            max_cards=request.max_cards,
            native_filters=request.native_filters,
        )
        cards_seen = _positive_int(cards.get("cards_seen"))
        cards_succeeded = cards.get("status") == "succeeded"
        self._append_event(
            request.source_run_id,
            {
                "action_kind": "search_submitted",
                "route_kind": "search",
                "ok": cards_succeeded,
                "cards_seen": cards_seen,
                "safe_reason_code": None if cards_succeeded else _envelope_reason(cards),
            },
        )
        if request.native_filters:
            self._append_event(
                request.source_run_id,
                {
                    "action_kind": "apply_filters_completed",
                    "route_kind": "search",
                    "ok": cards_succeeded,
                },
            )
        if not cards_succeeded:
            return self._site.blocked_resumes_envelope(
                source_run_id=request.source_run_id,
                query=request.query,
                safe_reason_code=_envelope_reason(cards),
                cards_seen=cards_seen,
            )

        structured_cards = self._site.extract_structured_liepin_cards(
            source_run_id=request.source_run_id,
            max_cards=request.max_cards,
        )
        if not structured_cards.ok:
            return self._site.blocked_resumes_envelope(
                source_run_id=request.source_run_id,
                query=request.query,
                safe_reason_code=structured_cards.safe_reason_code or "failed_provider_error",
                cards_seen=cards_seen,
            )

        card_items = _structured_card_items(structured_cards)
        self._append_event(
            request.source_run_id,
            {
                "action_kind": "visible_cards_observed",
                "route_kind": "search",
                "ok": True,
                "visible_cards": len(card_items),
                "target_resumes": request.target_resumes,
                "cards_seen": cards_seen or len(card_items),
            },
        )
        cards_seen_for_resume = max(cards_seen, len(card_items))
        detail_urls_by_rank: dict[int, str] = {}

        def remember_detail_urls(cards_to_cache: Sequence[Mapping[str, object]]) -> None:
            for card in cards_to_cache:
                selected = _card_ref_and_rank(card)
                if selected is None:
                    continue
                ref, rank = selected
                if rank in detail_urls_by_rank:
                    continue
                detail_url = self._site.safe_liepin_detail_url_for_ref(ref)
                if detail_url is not None:
                    detail_urls_by_rank[rank] = detail_url

        remember_detail_urls(card_items)
        self._append_event(
            request.source_run_id,
            {
                "action_kind": "detail_urls_cached",
                "route_kind": "search",
                "ok": True,
                "cached_detail_urls": len(detail_urls_by_rank),
            },
        )

        opened = 0
        attempted_ranks: set[int] = set()
        using_cached_card_items = False
        while opened < request.target_resumes:
            selected = _next_unattempted_card(card_items, attempted_ranks)
            if selected is None:
                break
            selected_ref, selected_rank = selected
            attempted_ranks.add(selected_rank)
            self._append_event(
                request.source_run_id,
                {
                    "action_kind": "detail_candidate_selected",
                    "route_kind": "search",
                    "ok": True,
                    "rank": selected_rank,
                    "ref": selected_ref,
                },
            )

            cached_detail_url = detail_urls_by_rank.get(selected_rank)
            if using_cached_card_items and cached_detail_url is not None:
                open_result = self._site.open_liepin_detail_cached_url(
                    source_run_id=request.source_run_id,
                    ref=selected_ref,
                    rank=selected_rank,
                    detail_url=cached_detail_url,
                )
            else:
                open_result = self._site.open_liepin_detail(
                    source_run_id=request.source_run_id,
                    ref=selected_ref,
                    rank=selected_rank,
                )
            if not open_result.ok:
                continue

            capture_result = self._site.capture_liepin_detail_resume(
                source_run_id=request.source_run_id,
                rank=selected_rank,
            )
            if not capture_result.ok:
                continue

            opened += 1
            self._append_event(
                request.source_run_id,
                {
                    "action_kind": "capture_detail_succeeded",
                    "route_kind": "detail",
                    "ok": True,
                    "rank": selected_rank,
                },
            )
            if opened >= request.target_resumes:
                continue

            restored_page_id = self._site.restore_liepin_search_page()
            self._append_event(
                request.source_run_id,
                {
                    "action_kind": "return_to_search_after_capture",
                    "route_kind": "search",
                    "ok": restored_page_id is not None,
                    "rank": selected_rank,
                },
            )
            if restored_page_id is None:
                using_cached_card_items = True
                continue

            refreshed = self._site.extract_structured_liepin_cards(
                source_run_id=request.source_run_id,
                max_cards=request.max_cards,
            )
            if not refreshed.ok:
                self._append_event(
                    request.source_run_id,
                    {
                        "action_kind": "visible_cards_refresh_failed_after_return",
                        "route_kind": "search",
                        "ok": False,
                        "safe_reason_code": refreshed.safe_reason_code,
                    },
                )
                break
            refreshed_card_items = _structured_card_items(refreshed)
            if refreshed_card_items:
                card_items = refreshed_card_items
                using_cached_card_items = False
                remember_detail_urls(card_items)
            else:
                using_cached_card_items = True
            cards_seen_for_resume = max(cards_seen_for_resume, len(refreshed_card_items))
            self._append_event(
                request.source_run_id,
                {
                    "action_kind": "visible_cards_refreshed_after_return",
                    "route_kind": "search",
                    "ok": True,
                    "visible_cards": len(refreshed_card_items),
                    "cards_seen": cards_seen_for_resume,
                },
            )

        if opened == 0:
            return self._site.blocked_resumes_envelope(
                source_run_id=request.source_run_id,
                query=request.query,
                safe_reason_code="liepin_opencli_detail_not_opened",
                cards_seen=cards_seen_for_resume,
            )
        if opened < request.target_resumes:
            self._append_event(
                request.source_run_id,
                {
                    "action_kind": "detail_target_not_met",
                    "route_kind": "detail",
                    "ok": False,
                    "target_resumes": request.target_resumes,
                    "resumes_returned": opened,
                    "visible_cards": len(card_items),
                },
            )
        return self._site.finalize_liepin_resumes(
            source_run_id=request.source_run_id,
            query=request.query,
            max_pages=request.max_pages,
            max_cards=request.max_cards,
            cards_seen=cards_seen_for_resume,
            target_resumes=request.target_resumes,
        )

    def _append_event(self, source_run_id: str, event: Mapping[str, object]) -> None:
        self._site.append_agent_event(source_run_id, event)


def _structured_card_items(result: OpenCliBrowserResult) -> list[Mapping[str, object]]:
    raw_cards = result.observation.get("cards") if isinstance(result.observation, Mapping) else None
    if not isinstance(raw_cards, Sequence) or isinstance(raw_cards, str | bytes | bytearray):
        return []
    return [item for item in raw_cards if isinstance(item, Mapping)]


def _next_unattempted_card(
    card_items: Sequence[Mapping[str, object]],
    attempted_ranks: set[int],
) -> tuple[str, int] | None:
    for card in card_items:
        selected = _card_ref_and_rank(card)
        if selected is None:
            continue
        ref, rank = selected
        if rank not in attempted_ranks:
            return ref, rank
    return None


def _card_ref_and_rank(card: Mapping[str, object]) -> tuple[str, int] | None:
    ref = card.get("ref")
    rank = _positive_int_or_none(card.get("provider_rank"))
    if not isinstance(ref, str) or not ref or rank is None:
        return None
    return ref, rank


def _envelope_reason(envelope: Mapping[str, object]) -> str:
    reason = envelope.get("safe_reason_code") or envelope.get("stop_reason") or "failed_provider_error"
    text = str(reason).strip()
    return text or "failed_provider_error"


def _positive_int(value: object) -> int:
    parsed = _positive_int_or_none(value)
    return parsed or 0


def _positive_int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed > 0 else None
    return None
