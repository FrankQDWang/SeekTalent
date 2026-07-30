from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Protocol, cast

from seektalent.opencli_browser.contracts import OpenCliBrowserError, OpenCliBrowserResult
from seektalent.core.retrieval.provider_contract import ProviderSearchContinuation
from seektalent.providers.liepin.first_page_continuation import CandidateState, LiepinFirstPageCandidate, LiepinFirstPageContinuation
from seektalent.source_contracts.detail_open_claims import DetailOpenClaimLedger, DetailOpenClaimSearchContext
from seektalent.providers.liepin.liepin_site_parsing import stable_liepin_detail_candidate_key_hash
from seektalent.providers.liepin.liepin_state_machine import (
    LiepinStateSnapshot,
    LiepinTransition,
    LiepinTransitionRunner,
    TransitionResult,
)

_DETAIL_EFFECT_POSTURES = frozenset({"not_attempted", "attempted", "unknown"})
_DETAIL_EFFECT_UNKNOWN_REASON = "liepin_details_effect_unknown"
_DETAIL_RECONCILIATION_UNKNOWN_REASON = "liepin_details_reconciliation_unknown"
_DETAIL_NOT_OPENED_REASON = "liepin_opencli_detail_not_opened"


@dataclass(frozen=True, kw_only=True)
class LiepinSearchWorkflowRequest:
    source_run_id: str
    query: str
    target_resumes: int
    max_pages: int
    max_cards: int
    native_filters: Mapping[str, object] | None = None


class LiepinSearchWorkflowSite(Protocol):
    def load_liepin_first_page_continuation(self, opaque_ref: str) -> LiepinFirstPageContinuation: ...
    def discard_liepin_first_page_continuation(self, opaque_ref: str) -> None: ...
    def save_liepin_first_page_continuation(self, *, source_run_id: str, logical_round_no: int,
        query_instance_id: str, keyword_query: str, visible_candidate_count: int,
        candidates: Sequence[LiepinFirstPageCandidate]) -> ProviderSearchContinuation: ...

    def mark_liepin_first_page_candidate(self, *, opaque_ref: str, rank: int,
        state: CandidateState) -> None: ...
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

    def run_liepin_details_operation(
        self,
        *,
        source_run_id: str,
        card_ref: str,
        rank: int,
        open_mode: str,
        provider_candidate_key_hash: str | None = None,
        expected_provider_candidate_key_hash: str | None = None,
    ) -> tuple[dict[str, object], OpenCliBrowserResult]: ...

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
        return self._search_detail_backed_resumes(request)

    def expand_first_page_continuation(self, *, continuation_ref: str,
            detail_open_claim_context: DetailOpenClaimSearchContext) -> dict[str, object]:
        continuation = self._site.load_liepin_first_page_continuation(continuation_ref)
        ledger = detail_open_claim_context.detail_open_claim_ledger
        initial_opened_count = sum(item.state == "opened" for item in continuation.candidates)
        opened_ranks: set[int] = set()
        skipped_seen = terminal_failures = 0
        last_reason: str | None = None
        interrupted = False

        for candidate in continuation.candidates:
            if candidate.state != "remaining":
                continue
            key = candidate.provider_candidate_key_hash
            if not ledger.try_claim(key):
                self._site.mark_liepin_first_page_candidate(opaque_ref=continuation_ref,
                    rank=candidate.rank, state="skipped_seen")
                skipped_seen += 1
                continue
            raised = False
            try:
                opened_envelope, opened = self._site.run_liepin_details_operation(
                    source_run_id=continuation.source_run_id,
                    card_ref=candidate.ref,
                    rank=candidate.rank,
                    open_mode="cached_locator",
                    provider_candidate_key_hash=key,
                    expected_provider_candidate_key_hash=key,
                )
            except (OpenCliBrowserError, RuntimeError) as exc:
                raised = True
                effect = _unknown_detail_effect(exc)
            else:
                effect = _detail_effect_from_result(opened_envelope, opened)
            state = apply_detail_claim_from_result(
                ledger=ledger,
                provider_candidate_key_hash=key,
                effect=effect,
            )
            self._site.mark_liepin_first_page_candidate(opaque_ref=continuation_ref,
                rank=candidate.rank, state=state)
            if state == "opened":
                opened_ranks.add(candidate.rank)
                continue
            last_reason = effect.safe_reason_code
            if state == "terminal_failed":
                terminal_failures += 1
                if not raised:
                    continue
            else:
                interrupted = True
            break

        finalized = self._site.finalize_liepin_resumes(source_run_id=continuation.source_run_id,
            query=continuation.keyword_query, max_pages=1, max_cards=len(continuation.candidates),
            cards_seen=len(continuation.candidates), target_resumes=None)
        expansion_resumes = [cast(Mapping[str, object], item)
            for item in cast(Sequence[object], finalized.get("resumes", []))
            if isinstance(item, Mapping)
            and cast(Mapping[str, object], item).get("provider_rank") in opened_ranks]
        remaining = sum(item.state == "remaining" for item in
            self._site.load_liepin_first_page_continuation(continuation_ref).candidates)
        status = "completed" if not interrupted and terminal_failures == 0 and remaining == 0 else "partial"
        self._append_event(continuation.source_run_id, {"action_kind": "first_page_expansion_completed",
            "expansion_opened_count": len(opened_ranks)})
        return {**finalized, "status": status,
            "safe_reason_code": "liepin_first_page_expansion_partial" if status == "partial" else None,
            "resumes": expansion_resumes, "resumes_returned": len(expansion_resumes),
            "first_page_visible_count": continuation.visible_candidate_count,
            "first_page_eligible_count": len(continuation.candidates),
            "initial_opened_count": initial_opened_count, "expansion_opened_count": len(opened_ranks),
            "expansion_skipped_seen_count": skipped_seen,
            "expansion_terminal_failure_count": terminal_failures, "last_safe_reason_code": last_reason}

    def _search_detail_backed_resumes_with_detail_open_claim_context(
        self,
        request: LiepinSearchWorkflowRequest,
        *,
        detail_open_claim_context: DetailOpenClaimSearchContext,
    ) -> dict[str, object]:
        return self._search_detail_backed_resumes(
            request,
            detail_open_claim_context=detail_open_claim_context,
        )

    def _search_detail_backed_resumes(
        self,
        request: LiepinSearchWorkflowRequest,
        *,
        detail_open_claim_context: DetailOpenClaimSearchContext | None = None,
    ) -> dict[str, object]:
        if detail_open_claim_context is not None and (
            detail_open_claim_context.logical_round_no < 1
            or not detail_open_claim_context.query_instance_id.strip()
        ):
            raise ValueError("detail_open_claim_context_missing_logical_provenance")
        if request.target_resumes < 1 or request.target_resumes > 10:
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")

        detail_claim_outcomes = (
            {
                "detail_claim_granted_count": 0,
                "detail_opened_count": 0,
                "detail_open_skipped_seen_count": 0,
                "detail_open_terminal_failure_count": 0,
            }
            if detail_open_claim_context is not None
            else None
        )
        detail_claim_outcomes_emitted = False
        private_continuation: ProviderSearchContinuation | None = None

        def emit_detail_claim_outcomes() -> None:
            nonlocal detail_claim_outcomes_emitted
            if detail_claim_outcomes is None or detail_claim_outcomes_emitted:
                return
            self._append_event(
                request.source_run_id,
                {"action_kind": "detail_claim_outcomes", **detail_claim_outcomes},
            )
            detail_claim_outcomes_emitted = True

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
            emit_detail_claim_outcomes()
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
            emit_detail_claim_outcomes()
            return self._site.blocked_resumes_envelope(
                source_run_id=request.source_run_id,
                query=request.query,
                safe_reason_code=structured_cards.safe_reason_code or "failed_provider_error",
                cards_seen=cards_seen,
            )

        card_items = _structured_card_items(structured_cards)
        visible_card_count = len(card_items)
        self._append_event(
            request.source_run_id,
            {
                "action_kind": "visible_cards_observed",
                "route_kind": "search",
                "ok": True,
                "visible_cards": visible_card_count,
                "target_resumes": request.target_resumes,
                "cards_seen": cards_seen or visible_card_count,
            },
        )
        cards_seen_for_resume = max(cards_seen, len(card_items))
        detail_urls_by_rank: dict[int, str] = {}
        detail_hashes_by_rank: dict[int, str] = {}

        def remember_detail_urls(cards_to_cache: Sequence[Mapping[str, object]]) -> None:
            for card in cards_to_cache:
                selected = _card_ref_and_rank(card)
                if selected is None:
                    continue
                ref, rank = selected
                if rank in detail_urls_by_rank and detail_open_claim_context is None:
                    continue
                if detail_open_claim_context is not None:
                    detail_urls_by_rank.pop(rank, None)
                    detail_hashes_by_rank.pop(rank, None)
                envelope, result = self._site.run_liepin_details_operation(
                    source_run_id=request.source_run_id,
                    card_ref=ref,
                    rank=rank,
                    open_mode="resolve_locator",
                )
                if not result.ok:
                    continue
                detail_url = envelope.get("detail_url")
                key_hash = envelope.get("provider_candidate_key_hash")
                if (
                    isinstance(detail_url, str)
                    and detail_url
                    and isinstance(key_hash, str)
                    and (
                        detail_open_claim_context is None
                        or stable_liepin_detail_candidate_key_hash(detail_url) is not None
                    )
                ):
                    detail_urls_by_rank[rank] = detail_url
                    detail_hashes_by_rank[rank] = key_hash

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

        baseline_candidates: tuple[LiepinFirstPageCandidate, ...] = ()
        last_detail_safe_reason = "liepin_opencli_detail_not_opened"
        if detail_open_claim_context is not None:
            baseline_candidates = tuple(
                LiepinFirstPageCandidate(
                    rank=rank,
                    ref=ref,
                    detail_url=detail_url,
                    provider_candidate_key_hash=provider_candidate_key_hash,
                )
                for card in card_items
                if (selected := _card_ref_and_rank(card)) is not None
                for ref, rank in (selected,)
                if (detail_url := detail_urls_by_rank.get(rank)) is not None
                if (
                    provider_candidate_key_hash := detail_hashes_by_rank.get(rank)
                    or stable_liepin_detail_candidate_key_hash(detail_url)
                )
                is not None
            )
            save_continuation = getattr(self._site, "save_liepin_first_page_continuation", None)
            if callable(save_continuation):
                private_continuation = save_continuation(
                    source_run_id=request.source_run_id,
                    logical_round_no=detail_open_claim_context.logical_round_no,
                    query_instance_id=detail_open_claim_context.query_instance_id,
                    keyword_query=request.query,
                    visible_candidate_count=len(card_items),
                    candidates=baseline_candidates,
                )
            card_items = tuple(
                {"ref": candidate.ref, "provider_rank": candidate.rank}
                for candidate in baseline_candidates
            )
            if not baseline_candidates:
                last_detail_safe_reason = "liepin_opencli_candidate_identity_missing"

        if visible_card_count == 0 and cards_seen_for_resume == 0:
            emit_detail_claim_outcomes()
            envelope = self._site.finalize_liepin_resumes(
                source_run_id=request.source_run_id,
                query=request.query,
                max_pages=request.max_pages,
                max_cards=request.max_cards,
                cards_seen=0,
                target_resumes=request.target_resumes,
            )
            envelope["_private_first_page_continuations"] = (
                (replace(private_continuation, initial_opened_count=0),)
                if private_continuation is not None
                else ()
            )
            return envelope

        def mark_candidate(rank: int, state: CandidateState) -> None:
            if private_continuation is not None:
                mark = getattr(self._site, "mark_liepin_first_page_candidate", None)
                if callable(mark):
                    mark(
                        opaque_ref=private_continuation.opaque_ref, rank=rank, state=state
                    )

        def apply_detail_claim(
            *,
            rank: int,
            provider_candidate_key_hash: str,
            effect: _DetailEffect,
        ) -> CandidateState:
            assert detail_open_claim_context is not None
            assert detail_claim_outcomes is not None
            state = apply_detail_claim_from_result(
                ledger=detail_open_claim_context.detail_open_claim_ledger,
                provider_candidate_key_hash=provider_candidate_key_hash,
                effect=effect,
            )
            if state == "opened":
                detail_claim_outcomes["detail_opened_count"] += 1
            elif state == "terminal_failed":
                detail_claim_outcomes["detail_open_terminal_failure_count"] += 1
            mark_candidate(rank, state)
            return state

        opened = 0
        attempted_ranks: set[int] = set()
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
            provider_candidate_key_hash: str | None = detail_hashes_by_rank.get(selected_rank)
            if provider_candidate_key_hash is None and cached_detail_url is not None:
                # resolve_locator must have produced a durable locator first.
                provider_candidate_key_hash = (
                    stable_liepin_detail_candidate_key_hash(cached_detail_url)
                )
            if provider_candidate_key_hash is None:
                last_detail_safe_reason = "liepin_opencli_candidate_identity_missing"
                continue
            if detail_open_claim_context is not None:
                if not detail_open_claim_context.detail_open_claim_ledger.try_claim(provider_candidate_key_hash):
                    assert detail_claim_outcomes is not None
                    detail_claim_outcomes["detail_open_skipped_seen_count"] += 1
                    mark_candidate(selected_rank, "skipped_seen")
                    continue
                assert detail_claim_outcomes is not None
                detail_claim_outcomes["detail_claim_granted_count"] += 1

            try:
                detail_envelope, capture_result = self._site.run_liepin_details_operation(
                    source_run_id=request.source_run_id,
                    card_ref=selected_ref,
                    rank=selected_rank,
                    open_mode="cached_locator",
                    provider_candidate_key_hash=provider_candidate_key_hash,
                    expected_provider_candidate_key_hash=(
                        provider_candidate_key_hash
                        if detail_open_claim_context is not None
                        else None
                    ),
                )
            except Exception as exc:
                if detail_open_claim_context is not None:
                    apply_detail_claim(
                        rank=selected_rank,
                        provider_candidate_key_hash=provider_candidate_key_hash,
                        effect=_unknown_detail_effect(exc),
                    )
                raise

            effect = _detail_effect_from_result(detail_envelope, capture_result)
            if detail_open_claim_context is None:
                if not capture_result.ok:
                    last_detail_safe_reason = effect.safe_reason_code
                    continue
            elif (
                apply_detail_claim(
                    rank=selected_rank,
                    provider_candidate_key_hash=provider_candidate_key_hash,
                    effect=effect,
                )
                != "opened"
            ):
                last_detail_safe_reason = effect.safe_reason_code
                continue

            opened += 1
            if opened >= request.target_resumes:
                continue

            restored_page_id = self._restore_search_transition(
                source_run_id=request.source_run_id,
                rank=selected_rank,
            )
            if restored_page_id == "source-port-managed":
                continue
            if restored_page_id is None:
                if has_cached_url_for_remaining_candidate():
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
            if refreshed_card_items and detail_open_claim_context is None:
                card_items = refreshed_card_items
                remember_detail_urls(card_items)
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
            emit_detail_claim_outcomes()
            envelope = self._site.blocked_resumes_envelope(
                source_run_id=request.source_run_id,
                query=request.query,
                safe_reason_code=last_detail_safe_reason,
                cards_seen=cards_seen_for_resume,
            )
            envelope["_private_first_page_continuations"] = (
                (replace(private_continuation, initial_opened_count=0),)
                if private_continuation is not None else ()
            )
            return envelope
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
        emit_detail_claim_outcomes()
        envelope = self._site.finalize_liepin_resumes(
            source_run_id=request.source_run_id,
            query=request.query,
            max_pages=request.max_pages,
            max_cards=request.max_cards,
            cards_seen=cards_seen_for_resume,
            target_resumes=request.target_resumes,
        )
        envelope["_private_first_page_continuations"] = (
            (replace(private_continuation, initial_opened_count=opened),)
            if private_continuation is not None else ()
        )
        return envelope

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


@dataclass(frozen=True, kw_only=True)
class _DetailEffect:
    """What the Source Port reported about one details browser effect."""

    posture: str
    action_attempted: int
    ok: bool
    safe_reason_code: str


def _detail_effect_from_result(
    envelope: Mapping[str, object],
    result: OpenCliBrowserResult,
) -> _DetailEffect:
    safe_reason_code = _DETAIL_NOT_OPENED_REASON
    if not result.ok:
        safe_reason_code = result.safe_reason_code or _envelope_reason(envelope)
    raw_posture = envelope.get("effect_posture")
    if (
        safe_reason_code == _DETAIL_RECONCILIATION_UNKNOWN_REASON
        or not isinstance(raw_posture, str)
        or raw_posture not in _DETAIL_EFFECT_POSTURES
    ):
        posture = "unknown"
    else:
        posture = raw_posture
    return _DetailEffect(
        posture=posture,
        action_attempted=_positive_int(result.counts.get("action_attempted")),
        ok=result.ok,
        safe_reason_code=safe_reason_code,
    )


def _unknown_detail_effect(exc: BaseException) -> _DetailEffect:
    return _DetailEffect(
        posture="unknown",
        action_attempted=1,
        ok=False,
        safe_reason_code=(
            exc.safe_reason_code
            if isinstance(exc, OpenCliBrowserError)
            else _DETAIL_NOT_OPENED_REASON
        ),
    )


def apply_detail_claim_from_result(
    *,
    ledger: DetailOpenClaimLedger,
    provider_candidate_key_hash: str,
    effect: _DetailEffect,
) -> CandidateState:
    """Move one claim using only what the Source Port observed about the effect.

    An unknown posture must block repeats without claiming a confirmed open, so it
    lands on terminal_failed instead of releasing the claim.
    """
    if effect.posture == "not_attempted" and effect.action_attempted == 0:
        ledger.release_unattempted(provider_candidate_key_hash)
        return "remaining"
    if not ledger.has_browser_open_attempt(provider_candidate_key_hash):
        ledger.record_browser_open_attempt(provider_candidate_key_hash)
    if effect.posture == "attempted" and effect.ok:
        ledger.mark_opened(provider_candidate_key_hash)
        return "opened"
    ledger.mark_terminal_failed(
        provider_candidate_key_hash,
        safe_reason_code=(
            _DETAIL_EFFECT_UNKNOWN_REASON
            if effect.posture == "unknown"
            else effect.safe_reason_code
        ),
    )
    return "terminal_failed"


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
