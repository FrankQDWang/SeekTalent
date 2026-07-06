from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from seektalent.opencli_browser.contracts import OpenCliBrowserError, OpenCliBrowserResult
from seektalent.providers.liepin.liepin_state_machine import (
    LiepinStateSnapshot,
    LiepinTransition,
    LiepinTransitionRunner,
    TransitionResult,
)

_DETAIL_OPEN_MAX_ATTEMPTS = 2
_DETAIL_OPEN_RETRY_EXHAUSTED_REASON = "liepin_opencli_detail_open_retry_exhausted"
_DETAIL_OPEN_RETRYABLE_REASON_CODES = frozenset(
    {
        "liepin_opencli_detail_not_opened",
        "liepin_opencli_timeout",
    }
)


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

    def observe_liepin_search_state(self) -> OpenCliBrowserResult: ...

    def observe_liepin_detail_state(self) -> OpenCliBrowserResult: ...

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

    def wait_liepin_detail_ready(self, *, source_run_id: str, rank: int) -> OpenCliBrowserResult: ...

    def capture_liepin_detail_resume(
        self,
        *,
        source_run_id: str,
        rank: int,
        require_ready: bool = True,
    ) -> OpenCliBrowserResult: ...

    def discard_liepin_detail_resume(self, *, source_run_id: str, rank: int) -> None: ...

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
        self._transition_runner = LiepinTransitionRunner()

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

        structured_cards = self._extract_cards_transition(
            source_run_id=request.source_run_id,
            max_cards=request.max_cards,
            action_kind="extract_structured_cards",
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

        def has_cached_url_for_remaining_candidate() -> bool:
            for card in card_items:
                selected = _card_ref_and_rank(card)
                if selected is None:
                    continue
                _ref, rank = selected
                if rank not in attempted_ranks and rank in detail_urls_by_rank:
                    return True
            return False

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
        last_detail_safe_reason = "liepin_opencli_detail_not_opened"
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
            open_result = self._open_detail_with_retry(
                source_run_id=request.source_run_id,
                ref=selected_ref,
                rank=selected_rank,
                cached_detail_url=cached_detail_url,
                use_cached=using_cached_card_items,
            )
            if not open_result.ok:
                last_detail_safe_reason = open_result.safe_reason_code or "liepin_opencli_detail_not_opened"
                continue

            wait_result = self._wait_detail_ready_transition(
                source_run_id=request.source_run_id,
                rank=selected_rank,
            )
            if not wait_result.ok:
                last_detail_safe_reason = wait_result.safe_reason_code or "liepin_opencli_detail_not_opened"
                continue

            capture_result = self._capture_detail_transition(
                source_run_id=request.source_run_id,
                rank=selected_rank,
                require_ready=False,
            )
            if not capture_result.ok:
                last_detail_safe_reason = capture_result.safe_reason_code or "liepin_opencli_detail_not_opened"
                continue

            opened += 1
            if opened >= request.target_resumes:
                continue

            restored_page_id = self._restore_search_transition(
                source_run_id=request.source_run_id,
                rank=selected_rank,
            )
            if restored_page_id is None:
                if has_cached_url_for_remaining_candidate():
                    using_cached_card_items = True
                    continue
                break

            refreshed = self._extract_cards_transition(
                source_run_id=request.source_run_id,
                max_cards=request.max_cards,
                action_kind="extract_structured_cards",
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
                using_cached_card_items = has_cached_url_for_remaining_candidate()
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
                safe_reason_code=last_detail_safe_reason,
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

    def _extract_cards_transition(
        self,
        *,
        source_run_id: str,
        max_cards: int,
        action_kind: str = "extract_structured_cards",
    ) -> OpenCliBrowserResult:
        extracted: OpenCliBrowserResult | None = None

        def observe_state() -> LiepinStateSnapshot:
            return _snapshot_from_result(self._site.observe_liepin_search_state())

        def extract_cards() -> TransitionResult:
            nonlocal extracted
            extracted = self._site.extract_structured_liepin_cards(
                source_run_id=source_run_id,
                max_cards=max_cards,
            )
            if extracted.ok:
                return TransitionResult(ok=True)
            return TransitionResult(
                ok=False,
                safe_reason_code=extracted.safe_reason_code or "failed_provider_error",
            )

        result = self._transition_runner.run(
            LiepinTransition(
                name="extract_structured_cards",
                phase="search",
                observe_pre_state=observe_state,
                precondition=lambda snapshot: snapshot.ok,
                action=extract_cards,
                observe_post_state=observe_state,
                postcondition=lambda snapshot: snapshot.ok,
                safe_reason_code="liepin_opencli_results_not_ready",
                trace_event="liepin.search.extract_cards",
            )
        )
        event: dict[str, object] = {
            "action_kind": action_kind,
            "route_kind": "search",
            "ok": result.ok,
        }
        if not result.ok:
            event["safe_reason_code"] = result.safe_reason_code or "liepin_opencli_results_not_ready"
        self._append_event(source_run_id, event)
        if not result.ok:
            return OpenCliBrowserResult(
                ok=False,
                action="extract_structured_liepin_cards",
                safe_reason_code=result.safe_reason_code or "liepin_opencli_results_not_ready",
            )
        if extracted is None:
            return OpenCliBrowserResult(
                ok=False,
                action="extract_structured_liepin_cards",
                safe_reason_code="liepin_opencli_results_not_ready",
            )
        return extracted

    def _open_detail_with_retry(
        self,
        *,
        source_run_id: str,
        ref: str,
        rank: int,
        cached_detail_url: str | None,
        use_cached: bool,
    ) -> OpenCliBrowserResult:
        last_result: OpenCliBrowserResult | None = None
        for attempt in range(1, _DETAIL_OPEN_MAX_ATTEMPTS + 1):
            result = self._open_detail_transition(
                source_run_id=source_run_id,
                ref=ref,
                rank=rank,
                cached_detail_url=cached_detail_url,
                use_cached=use_cached,
                attempt=attempt,
            )
            if result.ok:
                return result
            last_result = result
            reason = result.safe_reason_code or "liepin_opencli_detail_not_opened"
            action_attempted = int(result.counts.get("action_attempted") or 0) > 0
            if (
                not action_attempted
                or reason not in _DETAIL_OPEN_RETRYABLE_REASON_CODES
                or attempt >= _DETAIL_OPEN_MAX_ATTEMPTS
            ):
                break
            self._append_event(
                source_run_id,
                {
                    "action_kind": "open_detail_retry_scheduled",
                    "route_kind": "detail",
                    "ok": True,
                    "rank": rank,
                    "ref": ref,
                    "attempt": attempt,
                    "next_attempt": attempt + 1,
                    "safe_reason_code": reason,
                },
            )

        if (
            last_result is not None
            and int(last_result.counts.get("action_attempted") or 0) > 0
            and (last_result.safe_reason_code or "liepin_opencli_detail_not_opened")
            in _DETAIL_OPEN_RETRYABLE_REASON_CODES
        ):
            self._append_event(
                source_run_id,
                {
                    "action_kind": "open_detail_retry_exhausted",
                    "route_kind": "detail",
                    "ok": False,
                    "rank": rank,
                    "ref": ref,
                    "attempts": _DETAIL_OPEN_MAX_ATTEMPTS,
                    "safe_reason_code": _DETAIL_OPEN_RETRY_EXHAUSTED_REASON,
                },
            )
            return OpenCliBrowserResult(
                ok=False,
                action="open_liepin_detail",
                safe_reason_code=_DETAIL_OPEN_RETRY_EXHAUSTED_REASON,
                counts={"rank": rank, "attempts": _DETAIL_OPEN_MAX_ATTEMPTS, "action_attempted": 1},
            )
        return last_result or OpenCliBrowserResult(
            ok=False,
            action="open_liepin_detail",
            safe_reason_code="liepin_opencli_detail_not_opened",
            counts={"rank": rank, "action_attempted": 0},
        )

    def _open_detail_transition(
        self,
        *,
        source_run_id: str,
        ref: str,
        rank: int,
        cached_detail_url: str | None,
        use_cached: bool,
        attempt: int = 1,
    ) -> OpenCliBrowserResult:
        opened: OpenCliBrowserResult | None = None
        open_mode = "cached_url" if use_cached else "visible_card"

        def observe_search_state() -> LiepinStateSnapshot:
            return _snapshot_from_result(self._site.observe_liepin_search_state())

        def observe_detail_state() -> LiepinStateSnapshot:
            return _snapshot_from_result(self._site.observe_liepin_detail_state())

        def can_open(snapshot: LiepinStateSnapshot) -> bool:
            if not snapshot.ok:
                return False
            if use_cached:
                return cached_detail_url is not None
            return bool(ref and rank > 0 and _search_state_has_detail_target(snapshot.observation, ref))

        def open_detail() -> TransitionResult:
            nonlocal opened
            if use_cached:
                if cached_detail_url is None:
                    return TransitionResult(ok=False, safe_reason_code="liepin_opencli_detail_not_opened")
                opened = self._site.open_liepin_detail_cached_url(
                    source_run_id=source_run_id,
                    ref=ref,
                    rank=rank,
                    detail_url=cached_detail_url,
                )
            else:
                opened = self._site.open_liepin_detail(
                    source_run_id=source_run_id,
                    ref=ref,
                    rank=rank,
                )
            if opened.ok:
                return TransitionResult(ok=True)
            return TransitionResult(
                ok=False,
                safe_reason_code=opened.safe_reason_code or "liepin_opencli_detail_not_opened",
            )

        result = self._transition_runner.run(
            LiepinTransition(
                name="open_detail",
                phase="detail",
                observe_pre_state=observe_search_state,
                precondition=can_open,
                action=open_detail,
                observe_post_state=observe_detail_state,
                postcondition=lambda snapshot: snapshot.ok and bool(opened and opened.ok),
                safe_reason_code="liepin_opencli_detail_not_opened",
                trace_event="liepin.detail.open",
            )
        )
        if opened is not None:
            self._append_event(
                source_run_id,
                {
                    "action_kind": "open_detail",
                    "route_kind": "detail",
                    "ok": True,
                    "rank": rank,
                    "ref": ref,
                    "open_mode": open_mode,
                    "attempt": attempt,
                },
            )
        event: dict[str, object] = {
            "action_kind": "open_detail_succeeded" if result.ok else "open_detail_failed",
            "route_kind": "detail",
            "ok": result.ok,
            "rank": rank,
            "ref": ref,
            "attempt": attempt,
        }
        if opened is not None:
            event["open_mode"] = open_mode
        if not result.ok:
            event["safe_reason_code"] = result.safe_reason_code or "liepin_opencli_detail_not_opened"
        self._append_event(source_run_id, event)
        if not result.ok:
            return OpenCliBrowserResult(
                ok=False,
                action="open_liepin_detail",
                safe_reason_code=result.safe_reason_code or "liepin_opencli_detail_not_opened",
                counts={"rank": rank, "action_attempted": 1 if opened is not None else 0},
            )
        if opened is None:
            return OpenCliBrowserResult(
                ok=False,
                action="open_liepin_detail",
                safe_reason_code="liepin_opencli_detail_not_opened",
                counts={"rank": rank, "action_attempted": 0},
            )
        return opened

    def _wait_detail_ready_transition(self, *, source_run_id: str, rank: int) -> OpenCliBrowserResult:
        waited: OpenCliBrowserResult | None = None

        def observe_detail_state() -> LiepinStateSnapshot:
            return _snapshot_from_result(self._site.observe_liepin_detail_state())

        def wait_detail_ready() -> TransitionResult:
            nonlocal waited
            waited = self._site.wait_liepin_detail_ready(
                source_run_id=source_run_id,
                rank=rank,
            )
            if waited.ok:
                return TransitionResult(ok=True)
            return TransitionResult(
                ok=False,
                safe_reason_code=waited.safe_reason_code or "liepin_opencli_detail_not_opened",
            )

        result = self._transition_runner.run(
            LiepinTransition(
                name="wait_detail_ready",
                phase="detail",
                observe_pre_state=observe_detail_state,
                precondition=lambda snapshot: snapshot.ok,
                action=wait_detail_ready,
                observe_post_state=observe_detail_state,
                postcondition=lambda snapshot: snapshot.ok and bool(waited and waited.ok),
                safe_reason_code="liepin_opencli_detail_not_opened",
                trace_event="liepin.detail.wait_ready",
            )
        )
        event: dict[str, object] = {
            "action_kind": "wait_detail_ready",
            "route_kind": "detail",
            "ok": result.ok,
            "rank": rank,
        }
        if not result.ok:
            event["safe_reason_code"] = result.safe_reason_code or "liepin_opencli_detail_not_opened"
        self._append_event(source_run_id, event)
        if not result.ok:
            return OpenCliBrowserResult(
                ok=False,
                action="wait_liepin_detail_ready",
                safe_reason_code=result.safe_reason_code or "liepin_opencli_detail_not_opened",
            )
        if waited is None:
            return OpenCliBrowserResult(
                ok=False,
                action="wait_liepin_detail_ready",
                safe_reason_code="liepin_opencli_detail_not_opened",
            )
        return waited

    def _capture_detail_transition(
        self,
        *,
        source_run_id: str,
        rank: int,
        require_ready: bool = True,
    ) -> OpenCliBrowserResult:
        captured: OpenCliBrowserResult | None = None

        def observe_detail_state() -> LiepinStateSnapshot:
            return _snapshot_from_result(self._site.observe_liepin_detail_state())

        def capture_detail() -> TransitionResult:
            nonlocal captured
            captured = self._site.capture_liepin_detail_resume(
                source_run_id=source_run_id,
                rank=rank,
                require_ready=require_ready,
            )
            if captured.ok:
                return TransitionResult(ok=True)
            return TransitionResult(
                ok=False,
                safe_reason_code=captured.safe_reason_code or "liepin_opencli_detail_not_opened",
            )

        result = self._transition_runner.run(
            LiepinTransition(
                name="capture_detail",
                phase="detail",
                observe_pre_state=observe_detail_state,
                precondition=lambda snapshot: snapshot.ok,
                action=capture_detail,
                observe_post_state=observe_detail_state,
                postcondition=lambda snapshot: snapshot.ok and bool(captured and captured.ok),
                safe_reason_code="liepin_opencli_detail_not_opened",
                trace_event="liepin.detail.capture",
            )
        )
        observe_event: dict[str, object] = {
            "action_kind": "observe_detail",
            "route_kind": "detail",
            "ok": result.ok,
            "rank": rank,
        }
        if not result.ok:
            observe_event["safe_reason_code"] = result.safe_reason_code or "liepin_opencli_detail_not_opened"
        self._append_event(source_run_id, observe_event)
        event: dict[str, object] = {
            "action_kind": "capture_detail_succeeded" if result.ok else "capture_detail_failed",
            "route_kind": "detail",
            "ok": result.ok,
            "rank": rank,
        }
        if not result.ok:
            event["safe_reason_code"] = result.safe_reason_code or "liepin_opencli_detail_not_opened"
        self._append_event(source_run_id, event)
        if not result.ok:
            if captured is not None and captured.ok:
                self._site.discard_liepin_detail_resume(source_run_id=source_run_id, rank=rank)
            return OpenCliBrowserResult(
                ok=False,
                action="capture_liepin_detail_resume",
                safe_reason_code=result.safe_reason_code or "liepin_opencli_detail_not_opened",
            )
        if captured is None:
            return OpenCliBrowserResult(
                ok=False,
                action="capture_liepin_detail_resume",
                safe_reason_code="liepin_opencli_detail_not_opened",
            )
        return captured

    def _restore_search_transition(self, *, source_run_id: str, rank: int) -> str | None:
        restored_page_id: str | None = None

        def observe_detail_state() -> LiepinStateSnapshot:
            return _snapshot_from_result(self._site.observe_liepin_detail_state())

        def observe_search_state() -> LiepinStateSnapshot:
            return _snapshot_from_result(self._site.observe_liepin_search_state())

        def restore_search() -> TransitionResult:
            nonlocal restored_page_id
            restored_page_id = self._site.restore_liepin_search_page()
            return TransitionResult(ok=True)

        result = self._transition_runner.run(
            LiepinTransition(
                name="return_to_search_after_capture",
                phase="search",
                observe_pre_state=observe_detail_state,
                precondition=lambda snapshot: snapshot.ok,
                action=restore_search,
                observe_post_state=observe_search_state,
                postcondition=lambda snapshot: snapshot.ok and restored_page_id is not None,
                safe_reason_code="liepin_opencli_search_restore_failed",
                trace_event="liepin.search.restore_after_capture",
            )
        )
        event: dict[str, object] = {
            "action_kind": "return_to_search_after_capture",
            "route_kind": "search",
            "ok": result.ok,
            "rank": rank,
        }
        if not result.ok:
            event["safe_reason_code"] = result.safe_reason_code or "liepin_opencli_search_restore_failed"
        self._append_event(source_run_id, event)
        if not result.ok:
            return None
        return restored_page_id


def _structured_card_items(result: OpenCliBrowserResult) -> list[Mapping[str, object]]:
    raw_cards = result.observation.get("cards") if isinstance(result.observation, Mapping) else None
    if not isinstance(raw_cards, Sequence) or isinstance(raw_cards, str | bytes | bytearray):
        return []
    return [cast(Mapping[str, object], item) for item in raw_cards if isinstance(item, Mapping)]


def _snapshot_from_result(result: OpenCliBrowserResult) -> LiepinStateSnapshot:
    text = result.private_output or str(result.observation.get("text") or "")
    return LiepinStateSnapshot(
        ok=result.ok,
        text=text,
        safe_reason_code=result.safe_reason_code,
        observation=_safe_snapshot_observation(result.observation),
    )


def _safe_snapshot_observation(observation: Mapping[str, object]) -> dict[str, object] | None:
    safe_observation = {key: value for key, value in observation.items() if key != "text"}
    return safe_observation or None


def _search_state_has_detail_target(observation: Mapping[str, object] | None, ref: str) -> bool:
    stripped_ref = ref.strip()
    if not stripped_ref or observation is None:
        return False
    targets = observation.get("detailTargets")
    if not isinstance(targets, Sequence) or isinstance(targets, str | bytes | bytearray):
        return False
    for target in targets:
        if not isinstance(target, Mapping):
            continue
        target_ref = cast(Mapping[str, object], target).get("ref")
        if isinstance(target_ref, str) and target_ref.strip() == stripped_ref:
            return True
    return False


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
