from __future__ import annotations

import json
import hashlib
import os
import random
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from seektalent.opencli_browser.automation import OpenCliBrowserAutomation
from seektalent.opencli_browser.contracts import (
    OpenCliBrowserConfig,
    OpenCliBrowserError,
    OpenCliBrowserResult,
)
from seektalent.opencli_browser.runtime import (
    ALLOWED_BROWSER_COMMANDS,
    FORBIDDEN_BROWSER_COMMANDS,
)
from seektalent.providers.liepin.opencli_filter_planning import (
    LIEPIN_FILTER_SECTION_LABELS,
    RETRYABLE_NATIVE_FILTER_REASONS,
    liepin_filter_actions,
    native_filter_city_search_input_ref,
    liepin_filter_menu_label,
    native_filter_control_ref_in_section,
    native_filter_is_required,
    native_filter_option_ref_in_section,
    native_filter_option_visible_in_section,
    native_filter_selection_applied,
    skipped_liepin_filter_names,
)
from seektalent.providers.liepin.opencli_card_text import looks_like_liepin_card
from seektalent.providers.liepin.liepin_opencli_policy import (
    LIEPIN_RECRUITER_SEARCH_URL,
    liepin_error_from_opencli_error,
    liepin_result_from_opencli_result,
)
from seektalent.providers.liepin import liepin_site_payloads
from seektalent.providers.liepin.liepin_site_parsing import (
    ALLOWED_CLICK_TARGET_FRAGMENTS,
    FIXED_READONLY_EVAL_PROBES,
    FORBIDDEN_ACTION_TARGET_FRAGMENTS,
    OWNED_PAGE_MARKER_TTL_SECONDS,
    _LiepinDetailTarget,
    _bounded_public_text,
    _detail_provider_key_material,
    _detail_targets_payload,
    _fixed_readonly_eval_probe_script,
    _is_liepin_detail_url,
    _is_safe_page_id,
    _looks_like_liepin_detail_resume_state,
    _looks_like_liepin_search_result_page,
    _merge_liepin_detail_targets,
    _opencli_result_text,
    _parse_page_id as _parse_page_id,
    _positive_int,
    _positive_int_or_none,
    _rank_liepin_detail_targets,
    _rank_liepin_result_card_targets,
    _safe_artifact_segment,
    _safe_card_summary_from_block,
    _safe_detail_payload_from_probe_output,
    _safe_filename,
    _safe_visible_card_text,
    _state_url as _state_url,
    _string_key_mapping_or_none,
    _tab_page_id,
    _tab_urls_by_page_id,
    _target_ref,
    _url_matches_start_or_detail_surface,
    _url_matches_start_surface,
    _validate_native_filter_label,
    bucket_text,
    build_observation,
    classify_liepin_state,
    extract_allowed_click_refs as extract_allowed_click_refs,
    extract_known_modal_close_ref,
    extract_liepin_card_summaries,
    extract_liepin_search_button_ref,
    extract_liepin_search_input_ref,
)


@dataclass(frozen=True)
class LiepinOpenCliSiteConfig:
    allowed_hosts: tuple[str, ...]
    allowed_start_urls: tuple[str, ...]
    max_keyword_chars: int = 80
    allowed_click_refs: tuple[str, ...] = ()
    lease_dir: Path | None = None
    artifact_root: Path | None = None
    detail_open_timeout_seconds: int = 90
    idle_close_seconds: int = 120
    close_blank_window: bool = False
    cleanup_worker_enabled: bool = True


_RECOVERABLE_CONNECTION_REASONS = {
    "liepin_opencli_extension_disconnected",
    "liepin_opencli_daemon_stale",
    "liepin_opencli_status_unavailable",
}


class LiepinSiteAdapter:
    def __init__(
        self,
        *,
        browser_config: OpenCliBrowserConfig,
        site_config: LiepinOpenCliSiteConfig,
        automation: OpenCliBrowserAutomation,
    ) -> None:
        self._browser_config = browser_config
        self._site_config = site_config
        self._automation = automation

    @property
    def _commands(self):
        return self._automation.commands

    @property
    def _window_counter(self):
        return self._automation.window_counter

    @property
    def _blank_window_closer(self):
        return self._automation.blank_window_closer

    def _pace_before_action(self, action: str) -> None:
        if action in {"fill", "click", "scroll"}:
            self._automation.pace_before_action(action)
            return
        if not self._browser_config.pacing_enabled:
            return
        if action not in {"apply_liepin_filters", "open_liepin_detail"}:
            return
        low = max(0, self._browser_config.pacing_min_ms) / 1000
        high = max(self._browser_config.pacing_max_ms, self._browser_config.pacing_min_ms) / 1000
        if high <= 0:
            return
        time.sleep(random.uniform(low, high))

    def status(self) -> OpenCliBrowserResult:
        return liepin_result_from_opencli_result(self._automation.status())

    def recover_connection(self) -> OpenCliBrowserResult:
        status = self.status()
        if status.ok:
            return OpenCliBrowserResult(ok=True, action="recover_connection", counts={"already_ready": 1})
        if status.safe_reason_code not in _RECOVERABLE_CONNECTION_REASONS:
            return OpenCliBrowserResult(
                ok=False,
                action="recover_connection",
                safe_reason_code=status.safe_reason_code,
                private_output=status.private_output,
            )
        opened = self._automation.current_tab_opener.open_tab(LIEPIN_RECRUITER_SEARCH_URL)
        if not opened:
            return OpenCliBrowserResult(
                ok=False,
                action="recover_connection",
                safe_reason_code=status.safe_reason_code,
                private_output=status.private_output,
            )
        last_status = status
        for _attempt in range(5):
            time.sleep(1)
            last_status = self.status()
            if last_status.ok:
                return OpenCliBrowserResult(ok=True, action="recover_connection", counts={"opened": 1})
        return OpenCliBrowserResult(
            ok=False,
            action="recover_connection",
            safe_reason_code=last_status.safe_reason_code,
            private_output=last_status.private_output,
        )

    def open_liepin_tab(self, url: str) -> OpenCliBrowserResult:
        self._validate_start_url(url)
        lease = self._read_lease_for_reuse()
        if lease is not None:
            if str(lease.get("url") or "") == url:
                page_id = self._verified_owned_lease_page_id(lease)
                if page_id is not None:
                    self._reuse_liepin_search_page(page_id=page_id, url=url)
                    self._touch_lease()
                    return OpenCliBrowserResult(
                        ok=True,
                        action="open_liepin_tab",
                        counts={"reused": 1},
                    )
                self._delete_lease()
            else:
                self._delete_lease()
        page_id = self._select_canonical_liepin_search_page(expected_url=url)
        if page_id is not None:
            self._reset_liepin_search_tab(page_id=page_id, url=url)
            return OpenCliBrowserResult(
                ok=True,
                action="open_liepin_tab",
                counts={"reused": 1},
            )
        page_id = self._select_existing_liepin_search_tab(expected_url=url)
        if page_id is not None:
            self._select_and_mark_owned_liepin_tab(page_id=page_id, url=url)
            self._reset_liepin_search_tab(page_id=page_id, url=url)
            return OpenCliBrowserResult(
                ok=True,
                action="open_liepin_tab",
                counts={"reused": 1},
            )
        page_id = self._open_new_liepin_tab(url=url)
        return OpenCliBrowserResult(ok=True, action="open_liepin_tab", private_output=page_id)

    def state(self) -> OpenCliBrowserResult:
        current_url = self._current_url()
        url_terminal_reason = classify_liepin_state(url=current_url, text="")
        if url_terminal_reason:
            observation = build_observation("")
            observation["terminal"] = True
            return OpenCliBrowserResult(
                ok=False,
                action="state",
                safe_reason_code=url_terminal_reason,
                observation=observation,
            )
        output = self._run_browser_command("state", ())
        observation = build_observation(output)
        terminal_reason = classify_liepin_state(url=current_url, text=output)
        observation["terminal"] = terminal_reason is not None
        result_card_targets = self._find_liepin_result_card_detail_targets(
            state_text=output,
            max_cards=20,
        )
        if result_card_targets:
            existing_targets = _rank_liepin_detail_targets(
                output,
                max_cards=20,
            )
            observation["detailTargets"] = _detail_targets_payload(
                _merge_liepin_detail_targets(existing_targets, result_card_targets, max_cards=20)
            )
        if terminal_reason:
            return OpenCliBrowserResult(
                ok=False,
                action="state",
                safe_reason_code=terminal_reason,
                observation=observation,
                private_output=output,
            )
        self._touch_lease()
        return OpenCliBrowserResult(ok=True, action="state", observation=observation, private_output=output)

    def get_url(self) -> OpenCliBrowserResult:
        output = self._run_browser_command("get", ("url",))
        self._touch_lease()
        return OpenCliBrowserResult(
            ok=True,
            action="get_url",
            observation=build_observation(output),
            private_output=output,
        )

    def find(self, *, query: str) -> OpenCliBrowserResult:
        self._validate_keyword_text(query)
        output = self._run_browser_command("find", (query,))
        self._touch_lease()
        return OpenCliBrowserResult(
            ok=True, action="find", observation=build_observation(output), private_output=output
        )

    def fill(self, *, target: str, text: str) -> OpenCliBrowserResult:
        self._validate_action_target(target)
        self._validate_keyword_text(text)
        self._pace_before_action("fill")
        output = self._run_browser_command("fill", self._fill_args_for_target(target=target, text=text))
        self._touch_lease()
        return OpenCliBrowserResult(ok=True, action="fill", counts=bucket_text(text), private_output=output)

    def click(self, *, target: str) -> OpenCliBrowserResult:
        self._validate_click_target(target)
        self._pace_before_action("click")
        output = self._run_browser_command("click", self._click_args_for_target(target))
        self._touch_lease()
        return OpenCliBrowserResult(ok=True, action="click", private_output=output)

    def _click_native_filter_option(self, label: str, *, state_text: str, section: str = "legacy") -> None:
        _validate_native_filter_label(label)
        if section not in LIEPIN_FILTER_SECTION_LABELS:
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        ref = native_filter_option_ref_in_section(state_text, section=section, label=label)
        if ref is not None:
            self._click_native_filter_ref(ref)
            return
        if section != "legacy":
            raise OpenCliBrowserError("liepin_opencli_filter_option_unavailable")
        self._run_browser_command("click", ("--role", "button", "--text", label))
        self._touch_lease()

    def _click_native_filter_ref(self, ref: str) -> None:
        self._run_stale_ref_retry_once(lambda: self._automation.click_ref(ref))
        self._touch_lease()

    def _click_native_filter_menu(self, filter_name: str, *, section: str = "legacy") -> None:
        menu_label = liepin_filter_menu_label(filter_name, section)
        if menu_label is None:
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        self._run_browser_command("click", ("--role", "button", "--name", menu_label))
        self._touch_lease()

    def scroll(self, *, direction: str) -> OpenCliBrowserResult:
        if direction not in {"up", "down"}:
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        self._pace_before_action("scroll")
        output = self._run_browser_command("scroll", (direction,))
        self._touch_lease()
        return OpenCliBrowserResult(ok=True, action="scroll", private_output=output)

    def wait_time(self, *, seconds: int) -> OpenCliBrowserResult:
        if seconds < 1 or seconds > 10:
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        output = self._run_browser_command("wait", ("time", str(seconds)))
        self._touch_lease()
        return OpenCliBrowserResult(ok=True, action="wait_time", private_output=output)

    def apply_liepin_native_filters(
        self,
        *,
        source_run_id: str,
        native_filters: Mapping[str, object],
    ) -> OpenCliBrowserResult:
        events: list[dict[str, object]] = []
        try:
            self._pace_before_action("apply_liepin_filters")
            current_state = self.state()
            if not current_state.ok:
                return current_state
            result = self._apply_liepin_native_filters(
                native_filters=native_filters,
                current_state=current_state,
                events=events,
            )
            for event in events:
                self._append_agent_event(source_run_id, event)
            return result
        except OpenCliBrowserError as exc:
            return OpenCliBrowserResult(ok=False, action="apply_liepin_filters", safe_reason_code=exc.safe_reason_code)

    def extract_visible_liepin_cards(self, *, source_run_id: str, max_cards: int) -> OpenCliBrowserResult:
        try:
            if max_cards < 1 or max_cards > 50:
                raise OpenCliBrowserError("liepin_opencli_forbidden_command")
            state = self.state()
            if not state.ok:
                return state
            state_text = state.private_output or str(state.observation.get("text") or "")
            targets = _merge_liepin_detail_targets(
                _rank_liepin_detail_targets(state_text, max_cards=max_cards),
                self._find_liepin_result_card_detail_targets(state_text=state_text, max_cards=max_cards),
                max_cards=max_cards,
            )
            cards: list[dict[str, object]] = []
            for index, target in enumerate(targets, start=1):
                summary: dict[str, object] = {}
                if looks_like_liepin_card(target.block_text):
                    summary = _safe_card_summary_from_block(target.block_text)
                visible_text = str(summary.get("normalized_card_text") or target.block_text)
                cards.append(
                    {
                        "provider_rank": index,
                        "ref": target.ref,
                        "visible_text": _safe_visible_card_text(visible_text),
                        "display_title": summary.get("display_title"),
                        "current_or_recent_company": summary.get("current_or_recent_company"),
                        "current_or_recent_title": summary.get("current_or_recent_title"),
                        "city": summary.get("city"),
                        "expected_city": summary.get("expected_city"),
                        "education_level": summary.get("education_level"),
                        "work_years": summary.get("work_years"),
                        "age": summary.get("age"),
                        "school_names": summary.get("school_names") or [],
                        "skill_tags": summary.get("skill_tags") or [],
                        "job_intention": summary.get("job_intention"),
                        "recent_experience_text": summary.get("recent_experience_text"),
                    }
                )
            payload = {
                "schema_version": "seektalent.opencli_liepin_visible_cards.v1",
                "source_run_id": source_run_id,
                "cards": cards,
                "card_count": len(cards),
            }
            return OpenCliBrowserResult(
                ok=True,
                action="extract_visible_liepin_cards",
                counts={"cards": len(cards)},
                observation=payload,
                private_output=json.dumps(payload, ensure_ascii=False),
            )
        except OpenCliBrowserError as exc:
            return OpenCliBrowserResult(
                ok=False,
                action="extract_visible_liepin_cards",
                safe_reason_code=exc.safe_reason_code,
            )

    def open_liepin_detail(self, *, source_run_id: str, ref: str, rank: int) -> OpenCliBrowserResult:
        try:
            if rank < 1 or rank > 100 or not _is_safe_page_id(ref):
                raise OpenCliBrowserError("liepin_opencli_forbidden_command")
            self._pace_before_action("open_liepin_detail")
            open_state = self._detail_ref_open_state(source_run_id=source_run_id, ref=ref, rank=rank)
            if open_state in {"captured", "succeeded"}:
                return OpenCliBrowserResult(
                    ok=True,
                    action="open_liepin_detail",
                    counts={"rank": rank, "reused": 1},
                )
            state = self._state_with_liepin_detail_ref(ref)
            if state is None:
                raise OpenCliBrowserError("liepin_opencli_forbidden_command")
            if not state.ok:
                return state
            self._append_agent_event(
                source_run_id,
                {"action_kind": "open_detail", "route_kind": "detail", "ref": ref, "rank": rank},
            )
            if self._open_liepin_detail_ref_controlled(ref, source_run_id=source_run_id):
                self._append_agent_event(
                    source_run_id,
                    {"action_kind": "open_detail_succeeded", "route_kind": "detail", "ref": ref, "rank": rank},
                )
                return OpenCliBrowserResult(ok=True, action="open_liepin_detail", counts={"rank": rank})
            tabs_before_click = self._safe_list_tabs()
            safe_reason_code = "liepin_opencli_timeout"
            try:
                self._click_liepin_detail_ref(ref)
            except OpenCliBrowserError as exc:
                if exc.safe_reason_code != "liepin_opencli_timeout":
                    self._append_agent_event(
                        source_run_id,
                        {
                            "action_kind": "open_detail_failed",
                            "route_kind": "detail",
                            "ref": ref,
                            "rank": rank,
                            "safe_reason_code": exc.safe_reason_code,
                        },
                    )
                    raise
                safe_reason_code = exc.safe_reason_code
            if not self._claim_liepin_tab_after_detail_click(tabs_before_click, source_run_id=source_run_id):
                self._append_agent_event(
                    source_run_id,
                    {
                        "action_kind": "open_detail_timeout",
                        "route_kind": "detail",
                        "ref": ref,
                        "rank": rank,
                        "safe_reason_code": safe_reason_code,
                    },
                )
                return OpenCliBrowserResult(
                    ok=False,
                    action="open_liepin_detail",
                    safe_reason_code=safe_reason_code,
                    counts={"rank": rank},
                )
            self._append_agent_event(
                source_run_id,
                {"action_kind": "open_detail_succeeded", "route_kind": "detail", "ref": ref, "rank": rank},
            )
            return OpenCliBrowserResult(ok=True, action="open_liepin_detail", counts={"rank": rank})
        except OpenCliBrowserError as exc:
            return OpenCliBrowserResult(ok=False, action="open_liepin_detail", safe_reason_code=exc.safe_reason_code)

    def _open_liepin_detail_cached_url(
        self,
        *,
        source_run_id: str,
        ref: str,
        rank: int,
        detail_url: str,
    ) -> OpenCliBrowserResult:
        try:
            if rank < 1 or rank > 100 or not _is_safe_page_id(ref) or not _is_liepin_detail_url(detail_url):
                raise OpenCliBrowserError("liepin_opencli_forbidden_command")
            self._pace_before_action("open_liepin_detail")
            self._append_agent_event(
                source_run_id,
                {
                    "action_kind": "open_detail",
                    "route_kind": "detail",
                    "ref": ref,
                    "rank": rank,
                    "open_mode": "cached_url",
                },
            )
            if not self._open_liepin_detail_url_controlled(detail_url, source_run_id=source_run_id):
                return OpenCliBrowserResult(
                    ok=False,
                    action="open_liepin_detail",
                    safe_reason_code="liepin_opencli_forbidden_command",
                    counts={"rank": rank},
                )
            self._append_agent_event(
                source_run_id,
                {
                    "action_kind": "open_detail_succeeded",
                    "route_kind": "detail",
                    "ref": ref,
                    "rank": rank,
                    "open_mode": "cached_url",
                },
            )
            return OpenCliBrowserResult(ok=True, action="open_liepin_detail", counts={"rank": rank})
        except OpenCliBrowserError as exc:
            return OpenCliBrowserResult(ok=False, action="open_liepin_detail", safe_reason_code=exc.safe_reason_code)

    def capture_liepin_detail_resume(self, *, source_run_id: str, rank: int) -> OpenCliBrowserResult:
        try:
            if rank < 1 or rank > 100:
                raise OpenCliBrowserError("liepin_opencli_forbidden_command")
            safe_run_id = _safe_artifact_segment(source_run_id)
            detail_text = self._detail_state_text_until_resume_ready()
            detail_payload_text = self._run_fixed_readonly_eval_probe(
                probe_name="liepin_detail_resume_payload",
                ref="current",
            )
            payload = _safe_detail_payload_from_probe_output(detail_payload_text)
            url_result = self.get_url()
            page_url_hash = None
            source_url = None
            if url_result.ok:
                current_url = url_result.private_output.strip()
                page_url_hash = hashlib.sha256(current_url.encode("utf-8")).hexdigest()
                if _is_liepin_detail_url(current_url):
                    source_url = current_url
            detail_payload = dict(payload)
            if source_url is not None:
                detail_payload["sourceUrl"] = source_url
            raw_snapshot_ref = self._write_pi_artifact(
                "protected",
                f"liepin-opencli/raw/{safe_run_id}/{rank}.json",
                {
                    "schema_version": "seektalent.liepin_opencli_detail_raw.v1",
                    "source_run_id": source_run_id,
                    "provider_rank": rank,
                    "page_text": _bounded_public_text(detail_text, max_chars=20_000),
                    "page_url_hash": page_url_hash,
                },
            )
            normalized_snapshot_ref = self._write_pi_artifact(
                "protected",
                f"liepin-opencli/normalized/{safe_run_id}/{rank}.json",
                {
                    "schema_version": "seektalent.liepin_opencli_detail_normalized.v1",
                    "source_run_id": source_run_id,
                    "provider_rank": rank,
                    **payload,
                },
            )
            provider_material_ref = self._write_pi_artifact(
                "protected",
                f"liepin-opencli/provider-key/{safe_run_id}/{rank}.txt",
                _detail_provider_key_material(safe_run_id=safe_run_id, rank=rank, payload=payload),
            )
            resume: dict[str, object] = {
                "provider_rank": rank,
                "provider_candidate_key_material_ref": provider_material_ref,
                "candidate_resume_id": f"liepin-opencli-detail-{safe_run_id}-{rank}",
                "protected_snapshot_ref": raw_snapshot_ref,
                "normalized_snapshot_ref": normalized_snapshot_ref,
                "detail_payload": detail_payload,
                "normalized_text": str(payload["fullText"]),
            }
            resumes = [item for item in self._read_collected_resumes(safe_run_id) if item.get("provider_rank") != rank]
            resumes.append(resume)
            resumes.sort(key=lambda item: _positive_int_or_none(item.get("provider_rank")) or 0)
            self._write_collected_resumes(safe_run_id, resumes)
            self._append_agent_event(
                source_run_id,
                {"action_kind": "observe_detail", "route_kind": "detail", "ok": True, "rank": rank},
            )
            return OpenCliBrowserResult(
                ok=True,
                action="capture_liepin_detail_resume",
                counts={"resumes": len(resumes), "rank": rank},
            )
        except OpenCliBrowserError as exc:
            return OpenCliBrowserResult(
                ok=False,
                action="capture_liepin_detail_resume",
                safe_reason_code=exc.safe_reason_code,
            )

    def search_liepin_resumes(
        self,
        *,
        source_run_id: str,
        query: str,
        target_resumes: int,
        max_pages: int,
        max_cards: int,
        native_filters: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        if target_resumes < 1 or target_resumes > 10:
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        self._append_agent_event(
            source_run_id,
            {"action_kind": "search_cards_started", "route_kind": "search", "ok": True},
        )
        if native_filters:
            self._append_agent_event(
                source_run_id,
                {"action_kind": "apply_filters_started", "route_kind": "search", "ok": True},
            )
        cards = self.search_liepin_cards(
            source_run_id=source_run_id,
            query=query,
            max_pages=max_pages,
            max_cards=max_cards,
            native_filters=native_filters,
        )
        self._append_agent_event(
            source_run_id,
            {
                "action_kind": "search_submitted",
                "route_kind": "search",
                "ok": cards.get("status") == "succeeded",
                "cards_seen": _positive_int(cards.get("cards_seen")),
                "safe_reason_code": (
                    str(cards.get("safe_reason_code") or cards.get("stop_reason") or "")
                    if cards.get("status") != "succeeded"
                    else None
                ),
            },
        )
        if native_filters:
            self._append_agent_event(
                source_run_id,
                {
                    "action_kind": "apply_filters_completed",
                    "route_kind": "search",
                    "ok": cards.get("status") == "succeeded",
                },
            )
        cards_seen = _positive_int(cards.get("cards_seen"))
        if cards.get("status") != "succeeded":
            return self._blocked_resumes_envelope(
                source_run_id=source_run_id,
                query=query,
                safe_reason_code=str(
                    cards.get("safe_reason_code") or cards.get("stop_reason") or "failed_provider_error"
                ),
                cards_seen=cards_seen,
            )
        visible = self.extract_visible_liepin_cards(source_run_id=source_run_id, max_cards=max_cards)
        if not visible.ok:
            return self._blocked_resumes_envelope(
                source_run_id=source_run_id,
                query=query,
                safe_reason_code=visible.safe_reason_code,
                cards_seen=cards_seen,
            )
        raw_cards = visible.observation.get("cards") if isinstance(visible.observation, Mapping) else None
        card_items = raw_cards if isinstance(raw_cards, list) else []
        self._append_agent_event(
            source_run_id,
            {
                "action_kind": "visible_cards_observed",
                "route_kind": "search",
                "ok": True,
                "visible_cards": len(card_items),
                "target_resumes": target_resumes,
                "cards_seen": cards_seen or len(card_items),
            },
        )
        cards_seen_for_resume = max(cards_seen, len(card_items))
        detail_urls_by_rank: dict[int, str] = {}

        def remember_detail_urls(cards_to_cache: Sequence[object]) -> None:
            for card in cards_to_cache:
                card_payload = _string_key_mapping_or_none(card)
                if card_payload is None:
                    continue
                ref = card_payload.get("ref")
                if not isinstance(ref, str) or not ref:
                    continue
                rank = _positive_int_or_none(card_payload.get("provider_rank") or 0)
                if rank is None or rank in detail_urls_by_rank:
                    continue
                detail_url = self._safe_liepin_detail_url_for_ref(ref)
                if detail_url is not None:
                    detail_urls_by_rank[rank] = detail_url

        remember_detail_urls(card_items)
        self._append_agent_event(
            source_run_id,
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
        while opened < target_resumes:
            selected_card: Mapping[str, object] | None = None
            selected_ref: str | None = None
            selected_rank: int | None = None
            for card in card_items:
                card_payload = _string_key_mapping_or_none(card)
                if card_payload is None:
                    continue
                ref = card_payload.get("ref")
                rank = _positive_int_or_none(card_payload.get("provider_rank") or opened + 1)
                if rank is None:
                    continue
                if rank in attempted_ranks:
                    continue
                if not isinstance(ref, str) or not ref:
                    continue
                selected_card = card_payload
                selected_ref = ref
                selected_rank = rank
                break
            if selected_card is None or selected_ref is None or selected_rank is None:
                break
            attempted_ranks.add(selected_rank)
            self._append_agent_event(
                source_run_id,
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
                open_result = self._open_liepin_detail_cached_url(
                    source_run_id=source_run_id,
                    ref=selected_ref,
                    rank=selected_rank,
                    detail_url=cached_detail_url,
                )
            else:
                open_result = self.open_liepin_detail(
                    source_run_id=source_run_id,
                    ref=selected_ref,
                    rank=selected_rank,
                )
            if not open_result.ok:
                self._append_agent_event(
                    source_run_id,
                    {
                        "action_kind": "open_detail_failed",
                        "route_kind": "detail",
                        "ok": False,
                        "rank": selected_rank,
                        "ref": selected_ref,
                        "safe_reason_code": open_result.safe_reason_code,
                    },
                )
                continue
            capture_result = self.capture_liepin_detail_resume(source_run_id=source_run_id, rank=selected_rank)
            if capture_result.ok:
                opened += 1
                self._append_agent_event(
                    source_run_id,
                    {
                        "action_kind": "capture_detail_succeeded",
                        "route_kind": "detail",
                        "ok": True,
                        "rank": selected_rank,
                    },
                )
                if opened < target_resumes:
                    restored_page_id = self._select_canonical_liepin_search_page()
                    self._append_agent_event(
                        source_run_id,
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
                    refreshed = self.extract_visible_liepin_cards(source_run_id=source_run_id, max_cards=max_cards)
                    if not refreshed.ok:
                        self._append_agent_event(
                            source_run_id,
                            {
                                "action_kind": "visible_cards_refresh_failed_after_return",
                                "route_kind": "search",
                                "ok": False,
                                "safe_reason_code": refreshed.safe_reason_code,
                            },
                        )
                        break
                    raw_refreshed_cards = (
                        refreshed.observation.get("cards") if isinstance(refreshed.observation, Mapping) else None
                    )
                    refreshed_card_items = raw_refreshed_cards if isinstance(raw_refreshed_cards, list) else []
                    if refreshed_card_items:
                        card_items = refreshed_card_items
                        using_cached_card_items = False
                        remember_detail_urls(card_items)
                    else:
                        using_cached_card_items = True
                    cards_seen_for_resume = max(cards_seen_for_resume, len(refreshed_card_items))
                    self._append_agent_event(
                        source_run_id,
                        {
                            "action_kind": "visible_cards_refreshed_after_return",
                            "route_kind": "search",
                            "ok": True,
                            "visible_cards": len(refreshed_card_items),
                            "cards_seen": cards_seen_for_resume,
                        },
                    )
            else:
                self._append_agent_event(
                    source_run_id,
                    {
                        "action_kind": "capture_detail_failed",
                        "route_kind": "detail",
                        "ok": False,
                        "rank": selected_rank,
                        "safe_reason_code": capture_result.safe_reason_code,
                    },
                )
        if opened == 0:
            return self._blocked_resumes_envelope(
                source_run_id=source_run_id,
                query=query,
                safe_reason_code="liepin_opencli_detail_not_opened",
                cards_seen=cards_seen_for_resume,
            )
        if opened < target_resumes:
            self._append_agent_event(
                source_run_id,
                {
                    "action_kind": "detail_target_not_met",
                    "route_kind": "detail",
                    "ok": False,
                    "target_resumes": target_resumes,
                    "resumes_returned": opened,
                    "visible_cards": len(card_items),
                },
            )
        return self.finalize_liepin_resumes(
            source_run_id=source_run_id,
            query=query,
            max_pages=max_pages,
            max_cards=max_cards,
            cards_seen=cards_seen_for_resume,
            target_resumes=target_resumes,
        )

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
        safe_run_id = _safe_artifact_segment(source_run_id)
        resumes = self._read_collected_resumes(safe_run_id)
        protected_snapshot_refs = [
            str(resume["protected_snapshot_ref"])
            for resume in resumes
            if isinstance(resume.get("protected_snapshot_ref"), str)
        ]
        events = self._read_agent_events(safe_run_id)
        envelope = self._resumes_envelope(
            source_run_id=source_run_id,
            query=query,
            safe_run_id=safe_run_id,
            pages_visited=max(1, min(max_pages, max_pages or 1)),
            events=events,
            cards_seen=min(max_cards, max(cards_seen or len(resumes), len(resumes))),
            max_cards=max_cards,
            resumes=resumes,
            protected_snapshot_refs=protected_snapshot_refs,
            target_resumes=target_resumes,
        )
        return envelope

    def search_liepin_cards(
        self,
        *,
        source_run_id: str,
        query: str,
        max_pages: int,
        max_cards: int,
        native_filters: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        safe_run_id = _safe_artifact_segment(source_run_id)
        events: list[dict[str, object]] = []
        pages_visited = 0
        try:
            self._validate_keyword_text(query)
            events.append({"action_kind": "open_search", "route_kind": "search"})
            opened = self.open_liepin_tab(LIEPIN_RECRUITER_SEARCH_URL)
            if not opened.ok:
                return self._blocked_cards_envelope(
                    source_run_id=source_run_id,
                    query=query,
                    safe_reason_code=opened.safe_reason_code,
                    safe_run_id=safe_run_id,
                    pages_visited=pages_visited,
                    events=events,
                )
            pages_visited = 1
            events.append({"action_kind": "wait_search_ready", "route_kind": "search"})
            self.wait_time(seconds=3)
            first_state = self.state()
            events.append({"action_kind": "observe", "route_kind": "search", "ok": first_state.ok})
            if not first_state.ok and first_state.safe_reason_code in {
                "liepin_opencli_risk_page",
                "liepin_opencli_status_unavailable",
            }:
                events.append(
                    {
                        "action_kind": "observe_retry_after_unready",
                        "route_kind": "search",
                        "safe_reason_code": first_state.safe_reason_code,
                    }
                )
                self.wait_time(seconds=2)
                first_state = self.state()
                events.append(
                    {"action_kind": "observe_after_unready_retry", "route_kind": "search", "ok": first_state.ok}
                )
            if not first_state.ok:
                return self._blocked_cards_envelope(
                    source_run_id=source_run_id,
                    query=query,
                    safe_reason_code=first_state.safe_reason_code,
                    safe_run_id=safe_run_id,
                    pages_visited=pages_visited,
                    events=events,
                )
            first_state_text = first_state.private_output or str(first_state.observation.get("text") or "")
            modal_close_ref = extract_known_modal_close_ref(first_state_text)
            if modal_close_ref is not None:
                events.append({"action_kind": "close_known_modal", "route_kind": "search"})
                self._click_known_modal_close_ref(modal_close_ref)
                self.wait_time(seconds=1)
                first_state = self.state()
                events.append(
                    {"action_kind": "observe_after_modal_close", "route_kind": "search", "ok": first_state.ok}
                )
                if not first_state.ok:
                    return self._blocked_cards_envelope(
                        source_run_id=source_run_id,
                        query=query,
                        safe_reason_code=first_state.safe_reason_code,
                        safe_run_id=safe_run_id,
                        pages_visited=pages_visited,
                        events=events,
                    )
                first_state_text = first_state.private_output or str(first_state.observation.get("text") or "")
            events.append({"action_kind": "fill_search", "route_kind": "search", "chars": len(query)})
            search_input_ref = extract_liepin_search_input_ref(first_state_text)
            fill_target = search_input_ref or "搜索"
            for attempt_index in range(3):
                try:
                    self.fill(target=fill_target, text=query)
                    break
                except OpenCliBrowserError as exc:
                    if (
                        exc.safe_reason_code
                        not in {
                            "liepin_opencli_stale_ref",
                            "liepin_opencli_status_unavailable",
                        }
                        or attempt_index == 2
                    ):
                        raise
                    retry_event: dict[str, object] = {
                        "action_kind": "fill_search_retry",
                        "route_kind": "search",
                        "chars": len(query),
                    }
                    if exc.safe_reason_code == "liepin_opencli_stale_ref":
                        retry_event["safe_reason_code"] = exc.safe_reason_code
                    events.append(retry_event)
                    self.wait_time(seconds=2)
                    retry_state = self.state()
                    events.append(
                        {"action_kind": "observe_before_fill_retry", "route_kind": "search", "ok": retry_state.ok}
                    )
                    if not retry_state.ok:
                        return self._blocked_cards_envelope(
                            source_run_id=source_run_id,
                            query=query,
                            safe_reason_code=retry_state.safe_reason_code,
                            safe_run_id=safe_run_id,
                            pages_visited=pages_visited,
                            events=events,
                        )
                    retry_state_text = retry_state.private_output or str(retry_state.observation.get("text") or "")
                    modal_close_ref = extract_known_modal_close_ref(retry_state_text)
                    if modal_close_ref is not None:
                        events.append({"action_kind": "close_known_modal_before_fill_retry", "route_kind": "search"})
                        self._click_known_modal_close_ref(modal_close_ref)
                        self.wait_time(seconds=1)
                        retry_state = self.state()
                        events.append(
                            {
                                "action_kind": "observe_after_retry_modal_close",
                                "route_kind": "search",
                                "ok": retry_state.ok,
                            }
                        )
                        if not retry_state.ok:
                            return self._blocked_cards_envelope(
                                source_run_id=source_run_id,
                                query=query,
                                safe_reason_code=retry_state.safe_reason_code,
                                safe_run_id=safe_run_id,
                                pages_visited=pages_visited,
                                events=events,
                            )
                        retry_state_text = retry_state.private_output or str(retry_state.observation.get("text") or "")
                    retry_input_ref = extract_liepin_search_input_ref(retry_state_text)
                    fill_target = retry_input_ref or fill_target
            click_ready_state = self.state()
            events.append(
                {
                    "action_kind": "observe_before_click_search",
                    "route_kind": "search",
                    "ok": click_ready_state.ok,
                }
            )
            if not click_ready_state.ok:
                return self._blocked_cards_envelope(
                    source_run_id=source_run_id,
                    query=query,
                    safe_reason_code=click_ready_state.safe_reason_code,
                    safe_run_id=safe_run_id,
                    pages_visited=pages_visited,
                    events=events,
                )
            click_ready_state_text = click_ready_state.private_output or str(
                click_ready_state.observation.get("text") or ""
            )
            modal_close_ref = extract_known_modal_close_ref(click_ready_state_text)
            if modal_close_ref is not None:
                events.append({"action_kind": "close_known_modal_before_click_search", "route_kind": "search"})
                self._click_known_modal_close_ref(modal_close_ref)
                self.wait_time(seconds=1)
                click_ready_state = self.state()
                events.append(
                    {
                        "action_kind": "observe_after_click_search_modal_close",
                        "route_kind": "search",
                        "ok": click_ready_state.ok,
                    }
                )
                if not click_ready_state.ok:
                    return self._blocked_cards_envelope(
                        source_run_id=source_run_id,
                        query=query,
                        safe_reason_code=click_ready_state.safe_reason_code,
                        safe_run_id=safe_run_id,
                        pages_visited=pages_visited,
                        events=events,
                    )
                click_ready_state_text = click_ready_state.private_output or str(
                    click_ready_state.observation.get("text") or ""
                )
            events.append({"action_kind": "click_search", "route_kind": "search"})
            search_click_state_text = click_ready_state_text
            for attempt_index in range(3):
                try:
                    self._click_liepin_search_button(search_click_state_text)
                    break
                except OpenCliBrowserError as exc:
                    if (
                        exc.safe_reason_code
                        not in {
                            "liepin_opencli_stale_ref",
                            "liepin_opencli_status_unavailable",
                        }
                        or attempt_index == 2
                    ):
                        raise
                    events.append(
                        {
                            "action_kind": "click_search_retry",
                            "route_kind": "search",
                            "safe_reason_code": exc.safe_reason_code,
                        }
                    )
                    self.wait_time(seconds=2)
                    retry_state = self.state()
                    events.append(
                        {
                            "action_kind": "observe_before_click_retry",
                            "route_kind": "search",
                            "ok": retry_state.ok,
                        }
                    )
                    if not retry_state.ok:
                        return self._blocked_cards_envelope(
                            source_run_id=source_run_id,
                            query=query,
                            safe_reason_code=retry_state.safe_reason_code,
                            safe_run_id=safe_run_id,
                            pages_visited=pages_visited,
                            events=events,
                        )
                    search_click_state_text = retry_state.private_output or str(
                        retry_state.observation.get("text") or ""
                    )
                    modal_close_ref = extract_known_modal_close_ref(search_click_state_text)
                    if modal_close_ref is not None:
                        events.append({"action_kind": "close_known_modal_before_click_retry", "route_kind": "search"})
                        self._click_known_modal_close_ref(modal_close_ref)
                        self.wait_time(seconds=1)
                        retry_state = self.state()
                        events.append(
                            {
                                "action_kind": "observe_after_click_retry_modal_close",
                                "route_kind": "search",
                                "ok": retry_state.ok,
                            }
                        )
                        if not retry_state.ok:
                            return self._blocked_cards_envelope(
                                source_run_id=source_run_id,
                                query=query,
                                safe_reason_code=retry_state.safe_reason_code,
                                safe_run_id=safe_run_id,
                                pages_visited=pages_visited,
                                events=events,
                            )
                        search_click_state_text = retry_state.private_output or str(
                            retry_state.observation.get("text") or ""
                        )
            final_state: OpenCliBrowserResult | None = None
            for attempt_index in range(3):
                try:
                    self.wait_time(seconds=3 if attempt_index == 0 else 2)
                    observed_state = self.state()
                except OpenCliBrowserError as exc:
                    events.append(
                        {
                            "action_kind": "observe_results_retry",
                            "route_kind": "search",
                            "safe_reason_code": exc.safe_reason_code,
                        }
                    )
                    if (
                        exc.safe_reason_code
                        not in {
                            "liepin_opencli_stale_ref",
                            "liepin_opencli_status_unavailable",
                        }
                        or attempt_index == 2
                    ):
                        return self._blocked_cards_envelope(
                            source_run_id=source_run_id,
                            query=query,
                            safe_reason_code=exc.safe_reason_code,
                            safe_run_id=safe_run_id,
                            pages_visited=pages_visited,
                            events=events,
                        )
                    continue
                events.append(
                    {
                        "action_kind": "observe_results" if attempt_index == 0 else "observe_results_after_retry",
                        "route_kind": "search",
                        "ok": observed_state.ok,
                    }
                )
                if observed_state.ok:
                    final_state = observed_state
                    break
                if observed_state.safe_reason_code == "liepin_opencli_status_unavailable" and attempt_index < 2:
                    events.append(
                        {
                            "action_kind": "observe_results_retry",
                            "route_kind": "search",
                            "safe_reason_code": observed_state.safe_reason_code,
                        }
                    )
                    continue
                return self._blocked_cards_envelope(
                    source_run_id=source_run_id,
                    query=query,
                    safe_reason_code=observed_state.safe_reason_code,
                    safe_run_id=safe_run_id,
                    pages_visited=pages_visited,
                    events=events,
                )
            if final_state is None:
                return self._blocked_cards_envelope(
                    source_run_id=source_run_id,
                    query=query,
                    safe_reason_code="liepin_opencli_status_unavailable",
                    safe_run_id=safe_run_id,
                    pages_visited=pages_visited,
                    events=events,
                )
            if native_filters:
                final_state = self._apply_liepin_native_filters(
                    native_filters=native_filters,
                    current_state=final_state,
                    events=events,
                )
                if not final_state.ok:
                    return self._blocked_cards_envelope(
                        source_run_id=source_run_id,
                        query=query,
                        safe_reason_code=final_state.safe_reason_code,
                        safe_run_id=safe_run_id,
                        pages_visited=pages_visited,
                        events=events,
                    )
            state_text = final_state.private_output
            cards = extract_liepin_card_summaries(state_text, max_cards=max_cards)
            return self._cards_envelope(
                source_run_id=source_run_id,
                query=query,
                safe_run_id=safe_run_id,
                pages_visited=pages_visited,
                events=events,
                state_text=state_text,
                cards=cards,
            )
        except OpenCliBrowserError as exc:
            return self._blocked_cards_envelope(
                source_run_id=source_run_id,
                query=query,
                safe_reason_code=exc.safe_reason_code,
                safe_run_id=safe_run_id,
                pages_visited=pages_visited,
                events=events,
            )

    def _apply_liepin_native_filters(
        self,
        *,
        native_filters: Mapping[str, object],
        current_state: OpenCliBrowserResult,
        events: list[dict[str, object]],
    ) -> OpenCliBrowserResult:
        working_state = current_state
        for filter_name, section, label in liepin_filter_actions(native_filters):
            try:
                working_state = self._select_liepin_native_filter(
                    filter_name=filter_name,
                    section=section,
                    label=label,
                    current_state=working_state,
                    events=events,
                )
                events.append(
                    {
                        "action_kind": "apply_native_filter",
                        "filter": filter_name,
                        "section": section,
                        "value": label,
                        "ok": True,
                    }
                )
            except OpenCliBrowserError as exc:
                safe_reason_code = exc.safe_reason_code
                if exc.safe_reason_code in {
                    "liepin_opencli_filter_option_unavailable",
                    "liepin_opencli_filter_unapplied",
                    "liepin_opencli_selector_ambiguous",
                    "liepin_opencli_selector_not_found",
                    "liepin_opencli_stale_ref",
                    "liepin_opencli_status_unavailable",
                    "liepin_opencli_target_not_found",
                    "liepin_opencli_timeout",
                }:
                    safe_reason_code = "liepin_opencli_filter_unapplied"
                blocking = native_filter_is_required(native_filters, filter_name)
                events.append(
                    {
                        "action_kind": "apply_native_filter",
                        "filter": filter_name,
                        "section": section,
                        "value": label,
                        "ok": False,
                        "safe_reason_code": safe_reason_code,
                        "blocking": blocking,
                    }
                )
                if blocking:
                    events.append({"action_kind": "observe_after_native_filters", "route_kind": "search", "ok": False})
                    return OpenCliBrowserResult(
                        ok=False,
                        action="apply_liepin_filters",
                        safe_reason_code=safe_reason_code,
                    )
                events.append(
                    {
                        "action_kind": "skip_native_filter",
                        "filter": filter_name,
                        "ok": True,
                        "safe_reason_code": safe_reason_code,
                    }
                )
                try:
                    refreshed = self.state()
                except OpenCliBrowserError:
                    refreshed = None
                if refreshed is not None and refreshed.ok:
                    working_state = refreshed
        for filter_name in skipped_liepin_filter_names(native_filters):
            events.append({"action_kind": "skip_native_filter", "filter": filter_name, "ok": True})
        events.append({"action_kind": "observe_after_native_filters", "route_kind": "search", "ok": working_state.ok})
        return working_state

    def _select_liepin_native_filter(
        self,
        *,
        filter_name: str,
        section: str,
        label: str,
        current_state: OpenCliBrowserResult,
        events: list[dict[str, object]],
    ) -> OpenCliBrowserResult:
        state = current_state
        for attempt_index in range(3):
            try:
                state_text = _opencli_result_text(state)
                if native_filter_selection_applied(state_text, section=section, label=label):
                    events.append(
                        {
                            "action_kind": "verify_native_filter",
                            "filter": filter_name,
                            "section": section,
                            "value": label,
                            "ok": True,
                            "already_applied": True,
                        }
                    )
                    return state
                if not native_filter_option_visible_in_section(state_text, section=section, label=label):
                    control_ref = native_filter_control_ref_in_section(state_text, section=section)
                    if control_ref is not None:
                        self._click_native_filter_ref(control_ref)
                    else:
                        self._click_native_filter_menu(filter_name, section=section)
                    events.append(
                        {
                            "action_kind": "open_native_filter_menu",
                            "filter": filter_name,
                            "section": section,
                            "value": label,
                            "ok": True,
                        }
                    )
                    self.wait_time(seconds=1)
                    state = self.state()
                    events.append(
                        {
                            "action_kind": "observe_native_filter_menu",
                            "filter": filter_name,
                            "section": section,
                            "ok": state.ok,
                        }
                    )
                    if not state.ok:
                        raise OpenCliBrowserError(state.safe_reason_code)
                    state_text = _opencli_result_text(state)
                if (
                    filter_name == "city"
                    and section in {"current", "expected"}
                    and not native_filter_option_visible_in_section(state_text, section=section, label=label)
                ):
                    if (input_ref := native_filter_city_search_input_ref(state_text)) is None:
                        raise OpenCliBrowserError("liepin_opencli_filter_option_unavailable")
                    self.fill(target=input_ref, text=label)
                    events.append(
                        {"action_kind": "fill_native_city_filter_search", "filter": "city", "value": label, "ok": True}
                    )
                    self.wait_time(seconds=1)
                    state = self.state()
                    events.append(
                        {"action_kind": "observe_native_city_filter_search", "filter": "city", "ok": state.ok}
                    )
                    if not state.ok:
                        raise OpenCliBrowserError(state.safe_reason_code)
                    state_text = _opencli_result_text(state)
                self._click_native_filter_option(label, state_text=state_text, section=section)
                self.wait_time(seconds=1)
                state = self.state()
                events.append(
                    {
                        "action_kind": "observe_after_native_filter",
                        "filter": filter_name,
                        "section": section,
                        "ok": state.ok,
                    }
                )
                if not state.ok:
                    raise OpenCliBrowserError(state.safe_reason_code)
                state_text = _opencli_result_text(state)
                if not native_filter_selection_applied(state_text, section=section, label=label):
                    events.append(
                        {
                            "action_kind": "verify_native_filter",
                            "filter": filter_name,
                            "section": section,
                            "value": label,
                            "ok": False,
                            "safe_reason_code": "liepin_opencli_filter_unapplied",
                            "attempt": attempt_index + 1,
                        }
                    )
                    raise OpenCliBrowserError("liepin_opencli_filter_unapplied")
                events.append(
                    {
                        "action_kind": "verify_native_filter",
                        "filter": filter_name,
                        "section": section,
                        "value": label,
                        "ok": True,
                    }
                )
                return state
            except OpenCliBrowserError as exc:
                if exc.safe_reason_code not in RETRYABLE_NATIVE_FILTER_REASONS or attempt_index == 2:
                    raise
                events.append(
                    {
                        "action_kind": "apply_native_filter_retry",
                        "filter": filter_name,
                        "section": section,
                        "value": label,
                        "safe_reason_code": exc.safe_reason_code,
                    }
                )
                self.wait_time(seconds=2)
                state = self.state()
                events.append(
                    {
                        "action_kind": "observe_before_native_filter_retry",
                        "filter": filter_name,
                        "section": section,
                        "ok": state.ok,
                    }
                )
                if not state.ok:
                    raise OpenCliBrowserError(state.safe_reason_code)
        return state

    def _blocked_cards_envelope(
        self,
        *,
        source_run_id: str,
        query: str,
        safe_reason_code: str,
        safe_run_id: str,
        pages_visited: int,
        events: list[dict[str, object]],
    ) -> dict[str, object]:
        return liepin_site_payloads.blocked_cards_envelope(
            source_run_id=source_run_id,
            query=query,
            safe_reason_code=safe_reason_code,
            safe_run_id=safe_run_id,
            pages_visited=pages_visited,
            events=events,
            write_pi_artifact=self._write_pi_artifact,
        )

    def _cards_envelope(
        self,
        *,
        source_run_id: str,
        query: str,
        safe_run_id: str,
        pages_visited: int,
        events: list[dict[str, object]],
        state_text: str,
        cards: tuple[dict[str, object], ...],
    ) -> dict[str, object]:
        return liepin_site_payloads.cards_envelope(
            source_run_id=source_run_id,
            query=query,
            safe_run_id=safe_run_id,
            pages_visited=pages_visited,
            events=events,
            state_text=state_text,
            cards=cards,
            write_pi_artifact=self._write_pi_artifact,
        )

    def _resumes_envelope(
        self,
        *,
        source_run_id: str,
        query: str,
        safe_run_id: str,
        pages_visited: int,
        events: list[dict[str, object]],
        cards_seen: int,
        max_cards: int,
        resumes: list[dict[str, object]],
        protected_snapshot_refs: list[str],
        target_resumes: int | None = None,
    ) -> dict[str, object]:
        return liepin_site_payloads.resumes_envelope(
            source_run_id=source_run_id,
            query=query,
            safe_run_id=safe_run_id,
            pages_visited=pages_visited,
            events=events,
            cards_seen=cards_seen,
            max_cards=max_cards,
            resumes=resumes,
            protected_snapshot_refs=protected_snapshot_refs,
            target_resumes=target_resumes,
            write_pi_artifact=self._write_pi_artifact,
        )

    def _blocked_resumes_envelope(
        self,
        *,
        source_run_id: str,
        query: str,
        safe_reason_code: str,
        cards_seen: int,
    ) -> dict[str, object]:
        return liepin_site_payloads.blocked_resumes_envelope(
            source_run_id=source_run_id,
            query=query,
            safe_reason_code=safe_reason_code,
            cards_seen=cards_seen,
            write_pi_artifact=self._write_pi_artifact,
            read_agent_events=self._read_agent_events,
        )

    def _write_pi_artifact(self, scope: str, relative_path: str, payload: object) -> str:
        target = self._pi_artifact_path(scope, relative_path)
        if isinstance(payload, str):
            target.write_text(payload, encoding="utf-8")
        else:
            target.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return f"artifact://{scope}/{Path(relative_path).as_posix()}"

    def _pi_artifact_path(self, scope: str, relative_path: str) -> Path:
        env_root = os.environ.get("SEEKTALENT_PI_ARTIFACT_ROOT")
        root = self._site_config.artifact_root or (Path(env_root) if env_root else None)
        if root is None:
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        if scope not in {"protected", "public-summary"}:
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        target = (root / scope / relative).resolve()
        allowed_root = (root / scope).resolve()
        if allowed_root not in target.parents:
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def _append_agent_event(self, source_run_id: str, event: Mapping[str, object]) -> None:
        safe_run_id = _safe_artifact_segment(source_run_id)
        events = self._read_agent_events(safe_run_id)
        events.append(dict(event))
        self._write_pi_artifact(
            "protected",
            f"pi-trace/{safe_run_id}/agent-events.json",
            {"schema_version": "seektalent.opencli_agent_events.v1", "events": events},
        )

    def _read_agent_events(self, safe_run_id: str) -> list[dict[str, object]]:
        path = self._pi_artifact_path("protected", f"pi-trace/{safe_run_id}/agent-events.json")
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except json.JSONDecodeError as exc:
            raise OpenCliBrowserError("liepin_opencli_malformed_state") from exc
        raw_events = loaded.get("events") if isinstance(loaded, dict) else None
        if not isinstance(raw_events, list):
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        return [dict(item) for item in raw_events if isinstance(item, dict)]

    def _detail_ref_open_state(self, *, source_run_id: str, ref: str, rank: int) -> str | None:
        safe_run_id = _safe_artifact_segment(source_run_id)
        if any(
            (_positive_int_or_none(item.get("provider_rank")) or 0) == rank
            for item in self._read_collected_resumes(safe_run_id)
        ):
            return "captured"
        state: str | None = None
        for event in self._read_agent_events(safe_run_id):
            if event.get("ref") != ref:
                continue
            if event.get("action_kind") == "open_detail_succeeded":
                state = "succeeded"
            elif event.get("action_kind") in {"open_detail_failed", "open_detail_timeout"}:
                state = "failed"
        return state

    def _read_collected_resumes(self, safe_run_id: str) -> list[dict[str, object]]:
        path = self._pi_artifact_path("protected", f"pi-detail/{safe_run_id}/collected-resumes.json")
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except json.JSONDecodeError as exc:
            raise OpenCliBrowserError("liepin_opencli_malformed_state") from exc
        raw_resumes = loaded.get("resumes") if isinstance(loaded, dict) else None
        if not isinstance(raw_resumes, list):
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        return [dict(item) for item in raw_resumes if isinstance(item, dict)]

    def _write_collected_resumes(self, safe_run_id: str, resumes: Sequence[Mapping[str, object]]) -> None:
        self._write_pi_artifact(
            "protected",
            f"pi-detail/{safe_run_id}/collected-resumes.json",
            {"schema_version": "seektalent.opencli_collected_resumes.v1", "resumes": [dict(item) for item in resumes]},
        )

    def _find_liepin_result_card_detail_targets(
        self,
        *,
        state_text: str,
        max_cards: int,
    ) -> tuple[_LiepinDetailTarget, ...]:
        if max_cards < 1 or not _looks_like_liepin_search_result_page(state_text):
            return ()
        try:
            output = self._run_opencli_call(
                lambda: self._automation.find_css(
                    "#resultList .detail-resume-card-wrap",
                    limit=min(max_cards, 100),
                    text_max=1200,
                )
            )
            return _rank_liepin_result_card_targets(
                output,
                max_cards=max_cards,
            )
        except OpenCliBrowserError:
            return ()

    def cleanup_idle_lease(self, *, force: bool = False) -> OpenCliBrowserResult:
        lease = self._read_lease()
        if lease is None:
            return OpenCliBrowserResult(ok=True, action="cleanup_idle_lease", counts={"leases": 0})
        if not force and not self._lease_is_idle(lease):
            return OpenCliBrowserResult(ok=True, action="cleanup_idle_lease", counts={"leases": 1, "closed": 0})
        self._delete_lease()
        return OpenCliBrowserResult(
            ok=True,
            action="cleanup_idle_lease",
            counts={"leases": 1, "closed": 0},
        )

    def watch_idle_lease(self) -> OpenCliBrowserResult:
        while True:
            lease = self._read_lease()
            if lease is None:
                return OpenCliBrowserResult(ok=True, action="watch_idle_lease", counts={"leases": 0})
            remaining_seconds = self._lease_remaining_seconds(lease)
            if remaining_seconds <= 0:
                return self.cleanup_idle_lease(force=True)
            time.sleep(min(max(remaining_seconds, 1), 30))

    def cleanup_orphaned_tabs(self, *, force: bool = False) -> OpenCliBrowserResult:
        lease = self._read_lease()
        if lease is not None:
            return self.cleanup_idle_lease(force=force)
        if not force:
            return OpenCliBrowserResult(
                ok=True,
                action="cleanup_orphaned_tabs",
                counts={"leases": 0, "closedTabs": 0, "blankWindows": 0},
            )
        skipped = self._forget_orphaned_owned_page_markers()
        return OpenCliBrowserResult(
            ok=True,
            action="cleanup_orphaned_tabs",
            counts={"leases": 0, "closedTabs": 0, "blankWindows": 0, "skipped": skipped},
        )

    def _forget_orphaned_owned_page_markers(self) -> int:
        markers = self._read_owned_page_markers()
        for page_id in tuple(markers):
            self._forget_owned_page_marker(page_id)
        return len(markers)

    def _list_tabs(self) -> list[dict[str, object]]:
        output = self._run_browser_command("tab", ("list",))
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError as exc:
            raise OpenCliBrowserError("liepin_opencli_malformed_state") from exc
        if not isinstance(parsed, list):
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        return [tab for tab in parsed if isinstance(tab, dict)]

    def _is_owned_liepin_tab(self, tab_url: str) -> bool:
        tab = urlparse(tab_url)
        if (tab.hostname or "") not in self._site_config.allowed_hosts:
            return False
        if any(_url_matches_start_surface(tab_url, start_url) for start_url in self._site_config.allowed_start_urls):
            return True
        path = tab.path or ""
        if path.startswith("/resume/showresumedetail"):
            return True
        return False

    def _current_url(self) -> str:
        return self._run_browser_command("get", ("url",)).strip()

    def _run_browser_command(self, command: str, args: tuple[str, ...]) -> str:
        if command not in ALLOWED_BROWSER_COMMANDS or command in FORBIDDEN_BROWSER_COMMANDS:
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        self._validate_command_shape(command, args)
        return self._run_opencli_call(lambda: self._automation.run_browser_command(command, args))

    def _run_opencli_call(self, call: Callable[[], str]) -> str:
        try:
            return call()
        except OpenCliBrowserError as exc:
            raise liepin_error_from_opencli_error(exc) from exc

    def _run_stale_ref_retry_once(self, call: Callable[[], str]) -> str:
        try:
            return self._run_opencli_call(call)
        except OpenCliBrowserError as exc:
            if exc.safe_reason_code != "liepin_opencli_stale_ref":
                raise
            self.state()
            return self._run_opencli_call(call)

    def _click_known_modal_close_ref(self, ref: str) -> None:
        if not _is_safe_page_id(ref):
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        self._run_opencli_call(lambda: self._automation.click_ref(ref))
        self._touch_lease()

    def _click_liepin_search_button(self, state_text: str) -> None:
        ref = extract_liepin_search_button_ref(state_text)
        if ref is None:
            self.click(target="搜索")
            return
        if not _is_safe_page_id(ref):
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        self._run_opencli_call(lambda: self._automation.click_ref(ref))
        self._touch_lease()

    def _click_liepin_detail_ref(self, ref: str) -> None:
        if not _is_safe_page_id(ref):
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        self._run_stale_ref_retry_once(lambda: self._automation.click_ref(ref))
        self._touch_lease()

    def _open_liepin_detail_ref_controlled(self, ref: str, *, source_run_id: str) -> bool:
        detail_url = self._liepin_detail_url_for_ref(ref)
        if detail_url is None:
            return False
        return self._open_liepin_detail_url_controlled(detail_url, source_run_id=source_run_id)

    def _open_liepin_detail_url_controlled(self, detail_url: str, *, source_run_id: str) -> bool:
        if not _is_liepin_detail_url(detail_url):
            return False
        self._open_new_liepin_tab(url=detail_url, source_run_id=source_run_id)
        self._touch_lease()
        return True

    def _liepin_detail_url_for_ref(self, ref: str) -> str | None:
        if not _is_safe_page_id(ref):
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        output = self._run_fixed_readonly_eval_probe(
            probe_name="liepin_detail_url_for_card",
            ref=ref,
        ).strip()
        if output == "null" or not output:
            return None
        if not _is_liepin_detail_url(output):
            return None
        return output

    def _safe_liepin_detail_url_for_ref(self, ref: str) -> str | None:
        try:
            return self._liepin_detail_url_for_ref(ref)
        except OpenCliBrowserError:
            return None

    def _run_fixed_readonly_eval_probe(self, *, probe_name: str, ref: str) -> str:
        if probe_name not in FIXED_READONLY_EVAL_PROBES or not _is_safe_page_id(ref):
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        script = _fixed_readonly_eval_probe_script(probe_name=probe_name, ref=ref)
        output = self._run_opencli_call(lambda: self._automation.readonly_eval(script))
        self._touch_lease()
        return output

    def _safe_list_tabs(self) -> tuple[dict[str, object], ...]:
        try:
            return tuple(self._list_tabs())
        except OpenCliBrowserError:
            return ()

    def _claim_liepin_tab_after_detail_click(
        self,
        before_tabs: Sequence[Mapping[str, object]],
        *,
        source_run_id: str,
    ) -> bool:
        before_urls = _tab_urls_by_page_id(before_tabs)
        if not before_urls:
            return False
        attempts = max(1, int(self._site_config.detail_open_timeout_seconds))
        for attempt_index in range(attempts):
            candidate = self._liepin_tab_claim_candidate(before_urls=before_urls)
            if candidate is not None:
                page_id, url = candidate
                self._select_and_mark_owned_liepin_tab(page_id=page_id, url=url, source_run_id=source_run_id)
                return True
            if attempt_index < attempts - 1:
                time.sleep(1)
        return False

    def _liepin_tab_claim_candidate(self, *, before_urls: Mapping[str, str]) -> tuple[str, str] | None:
        try:
            after_tabs = self._list_tabs()
            markers = self._read_owned_page_markers()
        except OpenCliBrowserError:
            return None
        candidates: list[tuple[int, str, str]] = []
        for tab in after_tabs:
            page_id = _tab_page_id(tab)
            url = str(tab.get("url") or "")
            if not _is_safe_page_id(page_id) or not self._is_owned_liepin_tab(url):
                continue
            before_url = before_urls.get(page_id)
            marker = markers.get(page_id)
            is_new_tab = page_id not in before_urls
            is_owned_navigation = marker is not None and before_url is not None and before_url != url
            if not is_new_tab and not is_owned_navigation:
                continue
            score = 0
            if tab.get("active") is True:
                score += 100
            if _is_liepin_detail_url(url):
                score += 50
            if is_new_tab:
                score += 10
            candidates.append((score, page_id, url))
        if not candidates:
            return None
        _, page_id, url = max(candidates, key=lambda item: item[0])
        return page_id, url

    def _select_and_mark_owned_liepin_tab(self, *, page_id: str, url: str, source_run_id: str | None = None) -> None:
        self._run_browser_command("tab", ("select", page_id))
        owner_nonce = self._owned_page_marker_nonce(page_id) or uuid.uuid4().hex
        self._write_lease(page_id=page_id, url=url, owner_nonce=owner_nonce)
        self._write_owned_page_marker(
            page_id=page_id,
            url=url,
            source_run_id=source_run_id,
            runtime_run_id=None,
            source_lane_run_id=None,
            owner_nonce=owner_nonce,
        )

    def _owned_page_marker_nonce(self, page_id: str) -> str | None:
        try:
            marker = self._read_owned_page_markers().get(page_id)
        except OpenCliBrowserError:
            return None
        if marker is None:
            return None
        owner_nonce = marker.get("owner_nonce")
        if isinstance(owner_nonce, str) and owner_nonce:
            return owner_nonce
        return None

    def _current_tab_page_id(self, current_url: str) -> str | None:
        tabs = self._list_tabs()
        for tab in tabs:
            page_id = _tab_page_id(tab)
            if tab.get("active") is True and str(tab.get("url") or "") == current_url:
                if not _is_safe_page_id(page_id):
                    raise OpenCliBrowserError("liepin_opencli_malformed_state")
                return page_id
        for tab in tabs:
            page_id = _tab_page_id(tab)
            if str(tab.get("url") or "") == current_url:
                if not _is_safe_page_id(page_id):
                    raise OpenCliBrowserError("liepin_opencli_malformed_state")
                return page_id
        return None

    def _state_has_liepin_detail_ref(self, state: OpenCliBrowserResult, ref: str) -> bool:
        if not state.ok:
            return False
        state_text = state.private_output or str(state.observation.get("text") or "")
        targets = _merge_liepin_detail_targets(
            _rank_liepin_detail_targets(state_text, max_cards=100),
            self._find_liepin_result_card_detail_targets(
                state_text=state_text,
                max_cards=100,
            ),
            max_cards=100,
        )
        return ref in {target.ref for target in targets}

    def _state_with_liepin_detail_ref(self, ref: str) -> OpenCliBrowserResult | None:
        first_state = self.state()
        if self._state_has_liepin_detail_ref(first_state, ref):
            return first_state
        page_id = self._select_canonical_liepin_search_page()
        if page_id is not None:
            restored_state = self.state()
            if self._state_has_liepin_detail_ref(restored_state, ref):
                return restored_state
        if not first_state.ok:
            return first_state
        return None

    def _is_liepin_search_context_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if (parsed.hostname or "") not in self._site_config.allowed_hosts:
            return False
        path = parsed.path or ""
        if path.startswith("/resume/showresumedetail"):
            return False
        return "resume" not in path.lower() and "detail" not in path.lower()

    def _owned_liepin_search_page_id(self) -> str | None:
        page_id = self._canonical_owned_liepin_search_page_id()
        if page_id is not None:
            return page_id
        try:
            current_url = self._current_url()
        except OpenCliBrowserError:
            return None
        if not self._is_liepin_search_context_url(current_url):
            return None
        try:
            return self._current_tab_page_id(current_url)
        except OpenCliBrowserError:
            return None

    def _canonical_owned_liepin_search_page_id(self, *, expected_url: str | None = None) -> str | None:
        try:
            markers = self._read_owned_page_markers()
        except OpenCliBrowserError:
            return None
        candidates: list[tuple[float, str]] = []
        for page_id, marker in markers.items():
            if not _is_safe_page_id(page_id):
                continue
            opened_at = marker.get("opened_at")
            if not isinstance(opened_at, int | float) or time.time() - float(opened_at) > OWNED_PAGE_MARKER_TTL_SECONDS:
                continue
            marker_url = str(marker.get("url") or "")
            if expected_url is not None and marker_url != expected_url:
                continue
            if marker.get("session") != self._browser_config.session or marker.get("page_id") != page_id:
                continue
            if not self._is_liepin_search_context_url(marker_url):
                continue
            candidates.append((float(opened_at), page_id))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    def _select_canonical_liepin_search_page(self, *, expected_url: str | None = None) -> str | None:
        page_id = self._canonical_owned_liepin_search_page_id(expected_url=expected_url)
        if page_id is None:
            return None
        if self._select_owned_liepin_search_page(page_id):
            return page_id
        self._forget_owned_page_marker(page_id)
        return None

    def _select_existing_liepin_search_tab(self, *, expected_url: str) -> str | None:
        try:
            tabs = self._list_tabs()
        except OpenCliBrowserError:
            return None
        candidates: list[tuple[int, str]] = []
        for tab in tabs:
            page_id = _tab_page_id(tab)
            tab_url = str(tab.get("url") or "")
            if not _is_safe_page_id(page_id):
                continue
            if not _url_matches_start_surface(tab_url, expected_url):
                continue
            if tab.get("active") is True:
                continue
            score = 1
            candidates.append((score, page_id))
        if not candidates:
            return None
        _, page_id = max(candidates, key=lambda item: item[0])
        for tab in tabs:
            if _tab_page_id(tab) == page_id and tab.get("active") is True:
                return page_id
        try:
            self._run_browser_command("tab", ("select", page_id))
        except OpenCliBrowserError:
            return None
        return page_id

    def _open_new_liepin_tab(self, *, url: str, source_run_id: str | None = None) -> str:
        return self._open_opencli_managed_liepin_tab(url=url, source_run_id=source_run_id)

    def _open_opencli_managed_liepin_tab(self, *, url: str, source_run_id: str | None = None) -> str:
        self._validate_start_or_detail_url(url)
        before_urls = _tab_urls_by_page_id(self._list_tabs())
        try:
            output = self._run_browser_command("tab", ("new", url))
        except OpenCliBrowserError as exc:
            if exc.safe_reason_code != "liepin_opencli_window_policy_blocked":
                raise
            self._run_browser_command("unbind", ())
            before_urls = _tab_urls_by_page_id(self._list_tabs())
            output = self._run_browser_command("tab", ("new", url))
        page_id = self._parse_opened_tab_page_id(output=output, url=url, before_urls=before_urls)
        owner_nonce = uuid.uuid4().hex
        self._write_lease(page_id=page_id, url=url, owner_nonce=owner_nonce)
        self._write_owned_page_marker(
            page_id=page_id,
            url=url,
            source_run_id=source_run_id,
            runtime_run_id=None,
            source_lane_run_id=None,
            owner_nonce=owner_nonce,
        )
        return page_id

    def _parse_opened_tab_page_id(self, *, output: str, url: str, before_urls: Mapping[str, str]) -> str:
        try:
            return _parse_page_id(output)
        except OpenCliBrowserError as exc:
            if exc.safe_reason_code != "liepin_opencli_tab_response_malformed":
                raise
        after_tabs = self._list_tabs()
        candidates: list[tuple[int, str]] = []
        for tab in after_tabs:
            page_id = _tab_page_id(tab)
            tab_url = str(tab.get("url") or "")
            if not _is_safe_page_id(page_id) or tab_url != url:
                continue
            score = 0
            if page_id not in before_urls:
                score += 100
            if tab.get("active") is True:
                score += 10
            candidates.append((score, page_id))
        if not candidates:
            raise OpenCliBrowserError("liepin_opencli_tab_response_malformed")
        return max(candidates, key=lambda item: item[0])[1]

    def _reuse_liepin_search_page(self, *, page_id: str, url: str) -> None:
        try:
            self._run_browser_command("tab", ("select", page_id))
            self._reset_liepin_search_tab(page_id=page_id, url=url)
            return
        except OpenCliBrowserError as exc:
            if exc.safe_reason_code != "liepin_opencli_window_policy_blocked":
                raise
        self._forget_owned_page_marker(page_id)
        self._delete_lease()
        self._open_opencli_managed_liepin_tab(url=url)

    def _reset_liepin_search_tab(self, *, page_id: str, url: str) -> None:
        if not _is_safe_page_id(page_id):
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        self._validate_start_url(url)
        self._run_browser_command("open", ("--tab", page_id, url))
        self._touch_lease()

    def _owned_liepin_search_page_ids(self) -> tuple[str, ...]:
        page_ids: list[str] = []
        seen: set[str] = set()
        lease = self._read_lease()
        if lease is not None and self._is_liepin_search_context_url(str(lease.get("url") or "")):
            page_id = self._verified_owned_lease_page_id(lease)
            if page_id is not None:
                page_ids.append(page_id)
                seen.add(page_id)
        try:
            markers = self._read_owned_page_markers()
        except OpenCliBrowserError:
            markers = {}
        for page_id, marker in sorted(
            markers.items(),
            key=lambda item: float(item[1].get("opened_at") or 0),
            reverse=True,
        ):
            if page_id in seen:
                continue
            opened_at = marker.get("opened_at")
            if not isinstance(opened_at, int | float) or time.time() - float(opened_at) > OWNED_PAGE_MARKER_TTL_SECONDS:
                continue
            marker_url = str(marker.get("url") or "")
            if marker.get("session") != self._browser_config.session or marker.get("page_id") != page_id:
                continue
            if not self._is_liepin_search_context_url(marker_url):
                continue
            page_ids.append(page_id)
            seen.add(page_id)
        if page_ids:
            return tuple(page_ids)
        try:
            current_url = self._current_url()
        except OpenCliBrowserError:
            return ()
        if not self._is_liepin_search_context_url(current_url):
            return ()
        try:
            current_page_id = self._current_tab_page_id(current_url)
        except OpenCliBrowserError:
            return ()
        if current_page_id is None:
            return ()
        return (current_page_id,)

    def _select_owned_liepin_search_page(self, page_id: str) -> bool:
        if not _is_safe_page_id(page_id):
            return False
        try:
            marker = self._read_owned_page_markers().get(page_id)
        except OpenCliBrowserError:
            return False
        if marker is None:
            return False
        opened_at = marker.get("opened_at")
        if not isinstance(opened_at, int | float) or time.time() - float(opened_at) > OWNED_PAGE_MARKER_TTL_SECONDS:
            return False
        search_url = str(marker.get("url") or "")
        if marker.get("session") != self._browser_config.session or marker.get("page_id") != page_id:
            return False
        if not self._is_liepin_search_context_url(search_url):
            return False
        owner_nonce = marker.get("owner_nonce")
        if not isinstance(owner_nonce, str) or not owner_nonce:
            return False
        try:
            self._run_browser_command("tab", ("select", page_id))
            if self._current_url() != search_url:
                return False
            self._write_lease(page_id=page_id, url=search_url, owner_nonce=owner_nonce)
        except OpenCliBrowserError:
            return False
        return True

    def _restore_liepin_search_results_state(self, page_id: str) -> OpenCliBrowserResult | None:
        if not self._select_owned_liepin_search_page(page_id):
            return None
        state = self.state()
        if not state.ok:
            return None
        state_text = state.private_output or str(state.observation.get("text") or "")
        if not _rank_liepin_detail_targets(state_text, max_cards=1):
            return None
        return state

    def _detail_state_text(self) -> str:
        output = self._run_browser_command("state", ())
        self._touch_lease()
        return output

    def _detail_state_text_until_resume_ready(self) -> str:
        attempts = max(4, min(30, int(self._site_config.detail_open_timeout_seconds) // 2))
        for attempt_index in range(attempts):
            output = self._detail_state_text()
            if _looks_like_liepin_detail_resume_state(output):
                return output
            if attempt_index < attempts - 1:
                self.wait_time(seconds=2)
        raise OpenCliBrowserError("liepin_opencli_detail_not_opened")

    def _fill_args_for_target(self, *, target: str, text: str) -> tuple[str, ...]:
        normalized = " ".join(target.strip().lower().split())
        ref = _target_ref(normalized)
        if ref is not None:
            return (ref, text)
        if "搜索" in target or "keyword" in normalized:
            return ("--role", "combobox", "--nth", "0", text)
        return (target, text)

    def _click_args_for_target(self, target: str) -> tuple[str, ...]:
        normalized = " ".join(target.strip().lower().split())
        ref = _target_ref(normalized)
        if ref is not None:
            if ref not in self._site_config.allowed_click_refs:
                raise OpenCliBrowserError("liepin_opencli_forbidden_command")
            return ("--role", "button", "--name", "搜 索")
        if "搜索" in target or "search" in normalized:
            return ("--role", "button", "--name", "搜 索")
        if "下一页" in target or "下页" in target or "next" in normalized:
            return ("--role", "button", "--name", "下一页")
        raise OpenCliBrowserError("liepin_opencli_forbidden_command")

    def _lease_path(self) -> Path:
        directory = self._site_config.lease_dir or (Path(tempfile.gettempdir()) / "seektalent-opencli-leases")
        return directory / f"{_safe_filename(self._browser_config.session)}.json"

    def _owned_pages_path(self) -> Path:
        directory = self._site_config.lease_dir or (Path(tempfile.gettempdir()) / "seektalent-opencli-leases")
        return directory / f"{_safe_filename(self._browser_config.session)}-owned-pages.json"

    def _read_lease(self) -> dict[str, object] | None:
        try:
            loaded = json.loads(self._lease_path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except json.JSONDecodeError as exc:
            raise OpenCliBrowserError("liepin_opencli_lease_malformed") from exc
        if not isinstance(loaded, dict):
            raise OpenCliBrowserError("liepin_opencli_lease_malformed")
        return loaded

    def _read_lease_for_reuse(self) -> dict[str, object] | None:
        try:
            return self._read_lease()
        except OpenCliBrowserError as exc:
            if exc.safe_reason_code != "liepin_opencli_lease_malformed":
                raise
            self._quarantine_lease_file()
            return None

    def _write_lease(self, *, page_id: str, url: str, owner_nonce: str | None = None) -> None:
        if not _is_safe_page_id(page_id):
            raise OpenCliBrowserError("liepin_opencli_tab_response_malformed")
        now = time.time()
        path = self._lease_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "seektalent.opencli_lease.v1",
            "session": self._browser_config.session,
            "page_id": page_id,
            "url": url,
            "created_at": now,
            "last_activity_at": now,
            "owner_nonce": owner_nonce,
        }
        self._write_lease_payload(payload)

    def _touch_lease(self) -> None:
        lease = self._read_lease()
        if lease is None:
            return
        lease["last_activity_at"] = time.time()
        self._write_lease_payload(lease)

    def _write_lease_payload(self, payload: Mapping[str, object]) -> None:
        path = self._lease_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(dict(payload), sort_keys=True), encoding="utf-8")
        tmp.replace(path)

    def _delete_lease(self) -> None:
        try:
            self._lease_path().unlink()
        except FileNotFoundError:
            return

    def _quarantine_lease_file(self) -> None:
        path = self._lease_path()
        if not path.exists():
            return
        target = path.with_name(f"{path.name}.malformed-{int(time.time())}-{uuid.uuid4().hex[:8]}")
        try:
            path.replace(target)
        except OSError:
            path.unlink(missing_ok=True)

    def _read_owned_page_markers(self) -> dict[str, dict[str, object]]:
        try:
            loaded = json.loads(self._owned_pages_path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError as exc:
            raise OpenCliBrowserError("liepin_opencli_owned_marker_malformed") from exc
        if not isinstance(loaded, dict):
            raise OpenCliBrowserError("liepin_opencli_owned_marker_malformed")
        markers: dict[str, dict[str, object]] = {}
        for page_id, marker in loaded.items():
            if not _is_safe_page_id(str(page_id)) or not isinstance(marker, dict):
                raise OpenCliBrowserError("liepin_opencli_owned_marker_malformed")
            if marker.get("schema_version") != "seektalent.opencli_owned_page.v1":
                raise OpenCliBrowserError("liepin_opencli_owned_marker_malformed")
            if marker.get("session") != self._browser_config.session:
                continue
            if marker.get("page_id") != page_id:
                raise OpenCliBrowserError("liepin_opencli_owned_marker_malformed")
            markers[str(page_id)] = dict(marker)
        return markers

    def _read_owned_page_markers_for_write(self) -> dict[str, dict[str, object]]:
        try:
            return self._read_owned_page_markers()
        except OpenCliBrowserError as exc:
            if exc.safe_reason_code != "liepin_opencli_owned_marker_malformed":
                raise
            self._quarantine_owned_page_marker_file()
            return {}

    def _quarantine_owned_page_marker_file(self) -> None:
        path = self._owned_pages_path()
        if not path.exists():
            return
        target = path.with_name(f"{path.name}.malformed-{int(time.time())}-{uuid.uuid4().hex[:8]}")
        try:
            path.replace(target)
        except OSError:
            path.unlink(missing_ok=True)

    def _write_owned_page_marker(
        self,
        *,
        page_id: str,
        url: str,
        source_run_id: str | None = None,
        runtime_run_id: str | None,
        source_lane_run_id: str | None,
        owner_nonce: str,
        opened_at: float | None = None,
    ) -> None:
        if not _is_safe_page_id(page_id) or not self._is_owned_liepin_tab(url):
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        markers = self._read_owned_page_markers_for_write()
        markers[page_id] = {
            "schema_version": "seektalent.opencli_owned_page.v1",
            "session": self._browser_config.session,
            "page_id": page_id,
            "url": url,
            "opened_at": opened_at or time.time(),
            "source_run_id": source_run_id,
            "runtime_run_id": runtime_run_id,
            "source_lane_run_id": source_lane_run_id,
            "owner_nonce": owner_nonce,
        }
        path = self._owned_pages_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(markers, sort_keys=True), encoding="utf-8")
        tmp.replace(path)

    def _forget_owned_page_marker(self, page_id: str) -> None:
        markers = self._read_owned_page_markers_for_write()
        if page_id not in markers:
            return
        markers.pop(page_id)
        path = self._owned_pages_path()
        if markers:
            path.write_text(json.dumps(markers, sort_keys=True), encoding="utf-8")
        else:
            path.unlink(missing_ok=True)

    def _lease_is_idle(self, lease: Mapping[str, object]) -> bool:
        return self._lease_remaining_seconds(lease) <= 0

    def _lease_page_id(self, lease: Mapping[str, object]) -> str:
        page_id = str(lease.get("page_id") or "")
        if not _is_safe_page_id(page_id):
            self._quarantine_lease_file()
            raise OpenCliBrowserError("liepin_opencli_lease_malformed")
        return page_id

    def _verified_owned_lease_page_id(self, lease: Mapping[str, object]) -> str | None:
        if lease.get("session") not in {None, self._browser_config.session}:
            return None
        page_id = self._lease_page_id(lease)
        lease_url = str(lease.get("url") or "")
        if not self._is_owned_liepin_tab(lease_url):
            return None
        try:
            marker = self._read_owned_page_markers().get(page_id)
        except OpenCliBrowserError:
            return None
        if marker is None:
            return None
        opened_at = marker.get("opened_at")
        if not isinstance(opened_at, int | float):
            return None
        if time.time() - float(opened_at) > OWNED_PAGE_MARKER_TTL_SECONDS:
            return None
        if marker.get("session") != self._browser_config.session or marker.get("page_id") != page_id:
            return None
        if marker.get("url") != lease_url:
            return None
        lease_nonce = lease.get("owner_nonce")
        marker_nonce = marker.get("owner_nonce")
        if isinstance(lease_nonce, str) and lease_nonce and marker_nonce != lease_nonce:
            return None
        try:
            tabs = self._list_tabs()
        except OpenCliBrowserError:
            return None
        for tab in tabs:
            tab_id = _tab_page_id(tab)
            if tab_id != page_id:
                continue
            if str(tab.get("url") or "") == lease_url:
                return page_id
            return None
        return None

    def _lease_remaining_seconds(self, lease: Mapping[str, object]) -> int:
        last_activity = lease.get("last_activity_at")
        if not isinstance(last_activity, int | float):
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        return int(last_activity + self._site_config.idle_close_seconds - time.time())

    def _close_blank_window_if_enabled(self) -> bool:
        if not self._site_config.close_blank_window:
            return False
        return self._blank_window_closer.close_blank()

    def _launch_idle_cleanup_worker(self) -> None:
        if not self._site_config.cleanup_worker_enabled:
            return
        env = os.environ.copy()
        if self._site_config.lease_dir is not None:
            env["SEEKTALENT_LIEPIN_OPENCLI_LEASE_DIR"] = str(self._site_config.lease_dir)
        env["SEEKTALENT_LIEPIN_OPENCLI_IDLE_CLOSE_SECONDS"] = str(self._site_config.idle_close_seconds)
        env["SEEKTALENT_LIEPIN_OPENCLI_WINDOW_MODE"] = self._browser_config.window_mode
        env["SEEKTALENT_LIEPIN_OPENCLI_CLOSE_BLANK_WINDOW"] = (
            "true" if self._site_config.close_blank_window else "false"
        )
        try:
            # shell=False with fixed argv; site config values are only child env.
            # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit
            subprocess.Popen(
                (sys.executable, "-m", "seektalent.providers.liepin.opencli_browser_cli", "watch_idle_lease"),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                start_new_session=True,
            )
        except OSError:
            return

    def _validate_command_shape(self, command: str, args: tuple[str, ...]) -> None:
        valid = {
            "state": len(args) == 0,
            "get": args == ("url",),
            "open": len(args) == 1 or (len(args) == 3 and args[0] == "--tab" and bool(args[1].strip())),
            "find": len(args) == 1,
            "click": len(args) == 1 or _is_role_button_command(args),
            "fill": len(args) == 2 or _is_role_fill_command(args),
            "scroll": args in {("up",), ("down",)},
            "wait": len(args) == 2 and args[0] in {"time", "text", "selector"},
            "bind": len(args) == 0,
            "unbind": len(args) == 0,
            "tab": (
                args == ("list",)
                or (len(args) == 2 and args[0] in {"new", "select", "close"} and bool(args[1].strip()))
            ),
        }.get(command, False)
        if not valid:
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        if command == "click":
            if len(args) == 1:
                self._validate_click_target(args[0])
        if command == "fill":
            if len(args) == 2:
                self._validate_action_target(args[0])
                self._validate_keyword_text(args[1])
            else:
                self._validate_keyword_text(args[-1])
        if command == "open":
            if len(args) == 1:
                self._validate_start_url(args[0])
            else:
                if not _is_safe_page_id(args[1]):
                    raise OpenCliBrowserError("liepin_opencli_forbidden_command")
                self._validate_start_url(args[2])
        if command == "tab" and args[0] == "new":
            self._validate_tab_new_url(args[1])
        if command == "tab" and args[0] in {"select", "close"} and not _is_safe_page_id(args[1]):
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")

    def _validate_start_or_detail_url(self, url: str) -> None:
        if _is_liepin_detail_url(url):
            return
        self._validate_start_url(url)

    def _validate_start_url(self, url: str) -> None:
        host = urlparse(url).hostname or ""
        if host not in self._site_config.allowed_hosts:
            raise OpenCliBrowserError("liepin_opencli_host_blocked")
        if url not in self._site_config.allowed_start_urls:
            raise OpenCliBrowserError("liepin_opencli_start_url_blocked")

    def _validate_tab_new_url(self, url: str) -> None:
        host = urlparse(url).hostname or ""
        if host not in self._site_config.allowed_hosts:
            raise OpenCliBrowserError("liepin_opencli_host_blocked")
        if url in self._site_config.allowed_start_urls or _is_liepin_detail_url(url):
            return
        raise OpenCliBrowserError("liepin_opencli_start_url_blocked")

    def _validate_keyword_text(self, text: str) -> None:
        if not text.strip() or len(text) > self._site_config.max_keyword_chars:
            raise OpenCliBrowserError("liepin_opencli_forbidden_text")
        forbidden_fragments = ("cookie", "Authorization", "Bearer", "storageState", "\n", "\r", "\x00")
        if any(fragment in text for fragment in forbidden_fragments):
            raise OpenCliBrowserError("liepin_opencli_forbidden_text")

    def _validate_action_target(self, target: str) -> None:
        normalized = " ".join(target.strip().lower().split())
        if not normalized or len(normalized) > 120:
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        if any(fragment in normalized for fragment in FORBIDDEN_ACTION_TARGET_FRAGMENTS):
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")

    def _validate_click_target(self, target: str) -> None:
        self._validate_action_target(target)
        normalized = " ".join(target.strip().lower().split())
        ref = _target_ref(normalized)
        if ref is not None:
            if ref in self._site_config.allowed_click_refs:
                return
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        if not any(fragment in normalized for fragment in ALLOWED_CLICK_TARGET_FRAGMENTS):
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")


def _is_role_button_command(args: tuple[str, ...]) -> bool:
    return (
        len(args) == 4
        and args[0] == "--role"
        and args[1] == "button"
        and args[2] in {"--name", "--text"}
        and bool(args[3].strip())
    )


def _is_role_fill_command(args: tuple[str, ...]) -> bool:
    if len(args) != 5 or args[0] != "--role" or args[2] != "--nth":
        return False
    if args[1] not in {"textbox", "combobox"}:
        return False
    try:
        nth = int(args[3])
    except ValueError:
        return False
    return 0 <= nth <= 20 and bool(args[4].strip())
