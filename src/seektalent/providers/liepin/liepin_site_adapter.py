from __future__ import annotations

import json
import hashlib
import os
import random
import re
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlparse
from seektalent.core.retrieval.provider_contract import ProviderSearchContinuation
from seektalent.providers.liepin.first_page_continuation import (
    CandidateState, LiepinFirstPageCandidate, LiepinFirstPageContinuationStore,
)

from seektalent.opencli_browser.automation import OpenCliBrowserAutomation
from seektalent.opencli_browser.contracts import (
    OpenCliBrowserConfig,
    OpenCliBrowserError,
    OpenCliBrowserResult,
    OpenCliBrowserTiming,
)
from seektalent.opencli_browser.runtime import (
    ALLOWED_BROWSER_COMMANDS,
    FORBIDDEN_BROWSER_COMMANDS,
)
from seektalent.providers.liepin.detail_payload_text import structured_liepin_detail_text
from seektalent.source_contracts.detail_open_claims import DetailOpenClaimSearchContext
from seektalent.providers.liepin.opencli_filter_planning import (
    LIEPIN_FILTER_SECTION_LABELS,
    RETRYABLE_NATIVE_FILTER_REASONS,
    liepin_filter_actions,
    native_filter_city_confirm_ref,
    native_filter_city_overseas_tab_ref,
    native_filter_city_picker_selection_contains,
    native_filter_city_search_input_ref,
    native_filter_clear_filters_ref,
    liepin_filter_menu_label,
    native_filter_control_ref_in_section,
    native_filter_is_required,
    native_filter_option_ref_in_section,
    native_filter_option_visible_in_section,
    native_filter_selection_applied,
    skipped_liepin_filter_names,
)
from seektalent.providers.liepin.liepin_opencli_policy import (
    LIEPIN_OPENCLI_ALLOWED_HOSTS,
    LIEPIN_RECRUITER_SEARCH_URL,
    liepin_error_from_opencli_error,
    liepin_result_from_opencli_result,
)
from seektalent.providers.liepin.opencli_local_state import locked_json_update, opencli_state_lock
from seektalent.providers.liepin.worker_contracts import (
    OPENCLI_LOCAL_BROWSER_PROFILE_SUBJECT,
    SessionStatus,
)
from seektalent.providers.liepin.liepin_state_machine import (
    LiepinStateSnapshot,
    LiepinTransition,
    LiepinTransitionRunner,
    TransitionResult,
)
from seektalent.providers.liepin import liepin_site_payloads
from seektalent.providers.liepin.liepin_site_parsing import (
    ALLOWED_CLICK_TARGET_FRAGMENTS,
    FIXED_READONLY_EVAL_PROBES,
    FORBIDDEN_ACTION_TARGET_FRAGMENTS,
    OWNED_PAGE_MARKER_TTL_SECONDS,
    _LiepinDetailTarget,
    _detail_provider_key_material,
    _detail_targets_payload,
    _fixed_readonly_eval_probe_script,
    _is_blank_tab_url,
    _is_liepin_recruiter_search_surface,
    _is_liepin_detail_url,
    _is_safe_page_id,
    _liepin_structured_cards_payload_probe_script,
    _looks_like_liepin_detail_resume_state,
    _looks_like_liepin_search_result_page,
    _looks_like_liepin_search_result_surface,
    _merge_liepin_detail_targets,
    _opencli_result_text,
    _parse_page_id as _parse_page_id,
    _positive_int_or_none,
    _rank_liepin_detail_targets,
    _rank_liepin_result_card_targets,
    _safe_artifact_segment,
    _safe_detail_payload_from_probe_output,
    _safe_filename,
    _safe_structured_cards_from_probe_output,
    stable_liepin_detail_candidate_key_hash,
    _state_url as _state_url,
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
    extract_liepin_card_summaries,  # noqa: F401 - public re-export for callers/tests.
    extract_liepin_search_button_ref,
    extract_liepin_search_input_ref,
)


def _is_provider_candidate_key_hash(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


@dataclass(frozen=True)
class LiepinOpenCliSiteConfig:
    allowed_hosts: tuple[str, ...]
    allowed_start_urls: tuple[str, ...]
    max_keyword_chars: int = 80
    allowed_click_refs: tuple[str, ...] = ()
    lease_dir: Path | None = None
    artifact_root: Path | None = None
    detail_open_timeout_seconds: int = 90


@dataclass(frozen=True)
class LiepinOpenCliTimingRecorder:
    artifact_root: Path | None = None
    writes_local_debug_artifacts: bool = False

    def record(self, timing: OpenCliBrowserTiming) -> None:
        if not self.writes_local_debug_artifacts:
            return
        env_root = os.environ.get("SEEKTALENT_PI_ARTIFACT_ROOT")
        root = self.artifact_root or (Path(env_root) if env_root else None)
        if root is None:
            return
        try:
            trace_dir = root / "protected" / "opencli-timing"
            trace_dir.mkdir(parents=True, exist_ok=True)
            record: dict[str, object] = {
                "schema_version": "seektalent.opencli_timing.v1",
                "ts": time.time(),
                "pid": os.getpid(),
                "command": timing.command,
                "session": timing.session,
                "argv_len": timing.argv_len,
                "duration_ms": timing.duration_ms,
                "ok": timing.ok,
            }
            if timing.safe_reason_code is not None:
                record["safe_reason_code"] = timing.safe_reason_code
            with (trace_dir / f"{os.getpid()}.jsonl").open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError:
            return


_RECOVERABLE_CONNECTION_REASONS = {
    "liepin_opencli_daemon_not_running",
    "liepin_opencli_extension_disconnected",
    "liepin_opencli_daemon_stale",
    "liepin_opencli_status_unavailable",
}

_RECOVERABLE_TAB_REUSE_REASONS = {
    "liepin_opencli_status_unavailable",
    "liepin_opencli_window_policy_blocked",
    "liepin_opencli_stale_ref",
}

_LIEPIN_SESSION_LOGIN_REQUIRED_REASONS = {
    "liepin_opencli_login_required",
    "liepin_opencli_identity_intercept",
    "liepin_opencli_risk_page",
    "liepin_opencli_unknown_modal",
}

_SessionStatusValue = Literal["missing", "login_required", "ready", "revoked"]


def _session_status_for_liepin_reason(reason: str) -> Literal["missing", "login_required"]:
    if reason in _LIEPIN_SESSION_LOGIN_REQUIRED_REASONS:
        return "login_required"
    return "missing"


def _session_status(
    *,
    connection_id: str,
    status: _SessionStatusValue,
    provider_account_hash: str | None = None,
    safe_reason_code: str | None = None,
    current_url: str | None = None,
    search_surface_ready: bool | None = None,
    result_surface_ready: bool | None = None,
) -> SessionStatus:
    payload: dict[str, object] = {
        "connectionId": connection_id,
        "status": status,
    }
    if provider_account_hash is not None:
        payload["providerAccountHash"] = provider_account_hash
    if safe_reason_code is not None:
        payload["safeReasonCode"] = safe_reason_code
    if current_url is not None:
        payload["currentUrl"] = current_url
    if search_surface_ready is not None:
        payload["searchSurfaceReady"] = search_surface_ready
    if result_surface_ready is not None:
        payload["resultSurfaceReady"] = result_surface_ready
    return SessionStatus.model_validate(payload)


def _opencli_safe_reason(reason: str | None, *, default: str) -> str:
    clean = str(reason or "").strip()
    if clean and clean != "configured":
        return clean
    return default


def _state_text_for_probe(result: OpenCliBrowserResult) -> str:
    if result.private_output:
        return result.private_output
    observation_text = result.observation.get("text")
    if isinstance(observation_text, str):
        return observation_text
    return _opencli_result_text(result)


def _native_filter_clear_scope(source_run_id: str) -> str:
    clean = source_run_id.strip()
    if not clean:
        return "__default__"
    for separator in (":source:", "-source-"):
        if separator in clean:
            return clean.split(separator, 1)[0]
    return clean


def _native_filter_clear_signature(native_filters: Mapping[str, object] | None) -> str:
    if not native_filters:
        return "[]"
    return json.dumps(liepin_filter_actions(native_filters), ensure_ascii=False, separators=(",", ":"))


def _snapshot_from_result(result: OpenCliBrowserResult) -> LiepinStateSnapshot:
    text = _opencli_result_text(result)
    url = _state_url(text)
    if url is None:
        private_text = (result.private_output or "").strip()
        if private_text.startswith(("http://", "https://")):
            url = private_text
    return LiepinStateSnapshot(
        ok=result.ok,
        text=text,
        url=url,
        safe_reason_code=result.safe_reason_code,
        observation=_safe_snapshot_observation(result.observation),
    )


def _safe_snapshot_observation(observation: Mapping[str, object]) -> dict[str, object] | None:
    safe_observation = {key: value for key, value in observation.items() if key != "text"}
    return safe_observation or None


def _result_from_opencli(result: OpenCliBrowserResult, *, event: dict[str, object] | None = None) -> TransitionResult:
    return TransitionResult(
        ok=result.ok,
        safe_reason_code=result.safe_reason_code,
        event=event,
    )


def _result_from_error(error: OpenCliBrowserError) -> TransitionResult:
    return TransitionResult(ok=False, safe_reason_code=error.safe_reason_code)


def _search_url_ready(snapshot: LiepinStateSnapshot) -> bool:
    return snapshot.url is not None and _is_liepin_recruiter_search_surface(snapshot.url)


def _search_state_nonterminal(snapshot: LiepinStateSnapshot) -> bool:
    return bool(snapshot.text.strip())


def _search_query_matches(actual: str, expected: str) -> bool:
    return _normalized_search_query(actual) == _normalized_search_query(expected)


def _normalized_search_query(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _search_state_ready_for_card_extraction(snapshot: LiepinStateSnapshot) -> bool:
    if not snapshot.ok:
        return False
    if not _search_state_nonterminal(snapshot):
        return False
    if _search_state_is_loading(snapshot.text):
        return False
    return _search_state_has_result_evidence(snapshot.text)


def _search_state_has_result_evidence(text: str) -> bool:
    return (
        _looks_like_liepin_search_result_page(text)
        or bool(extract_liepin_card_summaries(text, max_cards=1))
        or bool(_rank_liepin_detail_targets(text, max_cards=1))
        or _looks_like_liepin_candidate_result_text(text)
    )


def _looks_like_liepin_candidate_result_text(text: str) -> bool:
    compact = " ".join(text.split())
    if "求职期望" in compact and "工作" in compact:
        return True
    return re.search(r"(?:男|女|[\u4e00-\u9fa5*＊]{1,12}).{0,30}\d+\s*岁.{0,30}工作\s*\d+\s*年", compact) is not None


def _search_state_is_loading(text: str) -> bool:
    compact = "".join(text.split())
    return any(marker in compact for marker in ("正在加载", "加载中", "请稍候", "稍后"))


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
        self._native_filter_clear_signatures_by_scope: dict[str, str] = {}
        self._continuation_store: LiepinFirstPageContinuationStore | None = None

    def _first_page_continuation_store(self) -> LiepinFirstPageContinuationStore:
        root = self._site_config.artifact_root
        if root is None:
            raise OpenCliBrowserError("liepin_protected_artifact_root_missing")
        if self._continuation_store is None:
            self._continuation_store = LiepinFirstPageContinuationStore(root / "protected")
            self._continuation_store.delete_expired()
        return self._continuation_store

    def _save_liepin_first_page_continuation(self, *, source_run_id: str, logical_round_no: int,
        query_instance_id: str, keyword_query: str, visible_candidate_count: int,
        candidates: Sequence[LiepinFirstPageCandidate]) -> ProviderSearchContinuation:
        saved = self._first_page_continuation_store().create(
            source_run_id=source_run_id, logical_round_no=logical_round_no,
            query_instance_id=query_instance_id, keyword_query=keyword_query,
            visible_candidate_count=visible_candidate_count, candidates=list(candidates),
        )
        return ProviderSearchContinuation(kind="first_page_detail_expansion",
            continuation_id=source_run_id, opaque_ref=saved.opaque_ref, source_kind="liepin",
            round_no=logical_round_no, query_instance_id=query_instance_id,
            visible_candidate_count=saved.visible_candidate_count,
            eligible_candidate_count=len(saved.candidates), initial_opened_count=0)

    def _mark_liepin_first_page_candidate(self, *, opaque_ref: str, rank: int,
        state: CandidateState) -> None:
        self._first_page_continuation_store().mark_candidate(opaque_ref, rank=rank, state=state)

    def _load_liepin_first_page_continuation(self, opaque_ref: str):
        return self._first_page_continuation_store().load(opaque_ref)

    def _discard_liepin_first_page_continuation(self, opaque_ref: str) -> None:
        self._first_page_continuation_store().delete(opaque_ref)

    def _liepin_first_page_continuation_exists(self, opaque_ref: str) -> bool:
        try:
            self._first_page_continuation_store().load(opaque_ref)
        except FileNotFoundError:
            return False
        return True

    def _handle_liepin_first_page_continuation(self, *, continuation_ref: str,
            detail_open_claim_context: DetailOpenClaimSearchContext) -> dict[str, object]:
        from seektalent.providers.liepin.liepin_search_workflow import LiepinSearchWorkflow
        return LiepinSearchWorkflow(site=_LiepinSearchWorkflowSite(self)).expand_first_page_continuation(
            continuation_ref=continuation_ref, detail_open_claim_context=detail_open_claim_context)

    @property
    def _commands(self):
        return self._automation.commands

    def _run_liepin_transition(self, transition: LiepinTransition) -> TransitionResult:
        return LiepinTransitionRunner().run(transition)

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

    def session_status_probe(
        self,
        *,
        connection_id: str,
        provider_account_hash: str | None,
    ) -> SessionStatus:
        del provider_account_hash
        status = self.status()
        if not status.ok:
            reason = _opencli_safe_reason(status.safe_reason_code, default="liepin_opencli_status_unavailable")
            if reason not in _RECOVERABLE_CONNECTION_REASONS:
                return _session_status(
                    connection_id=connection_id,
                    status=_session_status_for_liepin_reason(reason),
                    safe_reason_code=reason,
                )
        try:
            opened = self.open_liepin_tab(LIEPIN_RECRUITER_SEARCH_URL)
        except OpenCliBrowserError as exc:
            reason = _opencli_safe_reason(exc.safe_reason_code, default="liepin_opencli_status_unavailable")
            return _session_status(
                connection_id=connection_id,
                status=_session_status_for_liepin_reason(reason),
                safe_reason_code=reason,
            )
        if not opened.ok:
            reason = _opencli_safe_reason(opened.safe_reason_code, default="liepin_opencli_status_unavailable")
            return _session_status(
                connection_id=connection_id,
                status=_session_status_for_liepin_reason(reason),
                safe_reason_code=reason,
            )
        reset_attempted = False
        try:
            state = self.state()
        except OpenCliBrowserError as exc:
            reason = _opencli_safe_reason(exc.safe_reason_code, default="liepin_opencli_status_unavailable")
            current_url = self._current_url_or_none()
            if reason == "liepin_opencli_host_blocked" and self._reset_opened_search_page(opened):
                reset_attempted = True
                try:
                    state = self.state()
                except OpenCliBrowserError as reset_exc:
                    reset_reason = _opencli_safe_reason(
                        reset_exc.safe_reason_code,
                        default="liepin_opencli_status_unavailable",
                    )
                    return _session_status(
                        connection_id=connection_id,
                        status=_session_status_for_liepin_reason(reset_reason),
                        safe_reason_code=reset_reason,
                        current_url=current_url,
                    )
            else:
                return _session_status(
                    connection_id=connection_id,
                    status=_session_status_for_liepin_reason(reason),
                    safe_reason_code=reason,
                    current_url=current_url,
                )

        state_text = _state_text_for_probe(state)
        current_url = _state_url(state_text) or self._current_url_or_none()
        if not state.ok:
            reason = _opencli_safe_reason(state.safe_reason_code, default="liepin_opencli_status_unavailable")
            if (
                not reset_attempted
                and reason == "liepin_opencli_host_blocked"
                and self._reset_opened_search_page(opened)
            ):
                reset_attempted = True
                try:
                    state = self.state()
                except OpenCliBrowserError as reset_exc:
                    reset_reason = _opencli_safe_reason(
                        reset_exc.safe_reason_code,
                        default="liepin_opencli_status_unavailable",
                    )
                    return _session_status(
                        connection_id=connection_id,
                        status=_session_status_for_liepin_reason(reset_reason),
                        safe_reason_code=reset_reason,
                        current_url=current_url,
                    )
                state_text = _state_text_for_probe(state)
                current_url = _state_url(state_text) or self._current_url_or_none()
                reason = _opencli_safe_reason(state.safe_reason_code, default="liepin_opencli_status_unavailable")
            if not state.ok:
                if reason == "liepin_opencli_host_blocked":
                    reason = "liepin_opencli_search_not_ready"
                return _session_status(
                    connection_id=connection_id,
                    status=_session_status_for_liepin_reason(reason),
                    safe_reason_code=reason,
                    current_url=current_url,
                )

        search_ready = current_url is not None and _is_liepin_recruiter_search_surface(current_url)
        result_ready = _looks_like_liepin_search_result_surface(state_text)
        if not search_ready and not reset_attempted and self._reset_opened_search_page(opened):
            try:
                state = self.state()
            except OpenCliBrowserError as reset_exc:
                reset_reason = _opencli_safe_reason(
                    reset_exc.safe_reason_code,
                    default="liepin_opencli_status_unavailable",
                )
                return _session_status(
                    connection_id=connection_id,
                    status=_session_status_for_liepin_reason(reset_reason),
                    safe_reason_code=reset_reason,
                    current_url=current_url,
                )
            state_text = _state_text_for_probe(state)
            current_url = _state_url(state_text) or self._current_url_or_none()
            search_ready = current_url is not None and _is_liepin_recruiter_search_surface(current_url)
            result_ready = _looks_like_liepin_search_result_surface(state_text)
            if not state.ok:
                reason = _opencli_safe_reason(state.safe_reason_code, default="liepin_opencli_status_unavailable")
                if reason == "liepin_opencli_host_blocked":
                    reason = "liepin_opencli_search_not_ready"
                return _session_status(
                    connection_id=connection_id,
                    status=_session_status_for_liepin_reason(reason),
                    safe_reason_code=reason,
                    current_url=current_url,
                )
        if not search_ready:
            return _session_status(
                connection_id=connection_id,
                status="missing",
                safe_reason_code="liepin_opencli_search_not_ready",
                current_url=current_url,
                search_surface_ready=False,
                result_surface_ready=result_ready,
            )

        return _session_status(
            connection_id=connection_id,
            status="ready",
            provider_account_hash=OPENCLI_LOCAL_BROWSER_PROFILE_SUBJECT,
            safe_reason_code="configured",
            current_url=current_url,
            search_surface_ready=True,
            result_surface_ready=result_ready,
        )

    def _reset_opened_search_page(self, opened: OpenCliBrowserResult) -> bool:
        page_id = opened.private_output.strip()
        if not _is_safe_page_id(page_id):
            return False
        try:
            return self._try_reset_liepin_search_tab(page_id=page_id, url=LIEPIN_RECRUITER_SEARCH_URL)
        except OpenCliBrowserError:
            self._forget_owned_page_marker(page_id)
            self._delete_lease()
            return False

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
        restarted = liepin_result_from_opencli_result(self._automation.restart_daemon())
        if not restarted.ok:
            return OpenCliBrowserResult(
                ok=False,
                action="recover_connection",
                safe_reason_code=restarted.safe_reason_code,
                private_output=restarted.private_output,
            )
        last_status = status
        for _attempt in range(5):
            time.sleep(1)
            last_status = self.status()
            if last_status.ok:
                return OpenCliBrowserResult(ok=True, action="recover_connection", counts={"restarted": 1})
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
                    if self._reuse_liepin_search_page(page_id=page_id, url=url):
                        self._touch_lease()
                        return OpenCliBrowserResult(
                            ok=True,
                            action="open_liepin_tab",
                            counts={"reused": 1},
                        )
                    self._forget_owned_page_marker(page_id)
                    self._delete_lease()
                self._delete_lease()
            else:
                self._delete_lease()
        page_id = self._select_canonical_liepin_search_page(expected_url=url)
        if page_id is not None:
            if self._try_reset_liepin_search_tab(page_id=page_id, url=url):
                return OpenCliBrowserResult(
                    ok=True,
                    action="open_liepin_tab",
                    counts={"reused": 1},
                )
            self._forget_owned_page_marker(page_id)
            self._delete_lease()
        page_id, before_urls = self._select_existing_liepin_search_tab(expected_url=url)
        if page_id is not None:
            self._select_and_mark_owned_liepin_tab(page_id=page_id, url=url)
            if self._try_reset_liepin_search_tab(page_id=page_id, url=url):
                return OpenCliBrowserResult(
                    ok=True,
                    action="open_liepin_tab",
                    counts={"reused": 1},
                )
            self._forget_owned_page_marker(page_id)
            self._delete_lease()
        page_id = self._open_new_liepin_tab(url=url, before_urls=before_urls)
        counts = {"opened": 1}
        if page_id is None:
            counts["unleased"] = 1
        return OpenCliBrowserResult(ok=True, action="open_liepin_tab", counts=counts, private_output=page_id or "")

    def state(self) -> OpenCliBrowserResult:
        current_url = self._current_url()
        current_host = urlparse(current_url).hostname or ""
        if current_host not in LIEPIN_OPENCLI_ALLOWED_HOSTS:
            url_terminal_reason = classify_liepin_state(url=current_url, text="")
            safe_reason = url_terminal_reason or "liepin_opencli_host_blocked"
            observation = build_observation("")
            observation["terminal"] = True
            return OpenCliBrowserResult(
                ok=False,
                action="state",
                safe_reason_code=safe_reason,
                observation=observation,
                private_output=f"URL: {current_url}",
            )
        output = self._run_browser_command("state", ())
        observation = build_observation(output)
        observed_url = _state_url(output) or current_url
        terminal_reason = classify_liepin_state(url=observed_url, text=output)
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

    def _current_url_or_none(self) -> str | None:
        try:
            current_url = self._current_url().strip()
        except OpenCliBrowserError:
            return None
        return current_url or None

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

    def _wait_for_text_condition(self, text: str) -> None:
        _validate_native_filter_label(text)
        self._run_browser_command("wait", ("text", text))
        self._touch_lease()

    def _wait_for_detail_resume_condition(self) -> None:
        self._run_browser_command("wait", ("selector", "#resume-detail-basic-info"))
        self._touch_lease()

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

    def extract_structured_liepin_cards(self, *, source_run_id: str, max_cards: int) -> OpenCliBrowserResult:
        try:
            if max_cards < 1 or max_cards > 50:
                raise OpenCliBrowserError("liepin_opencli_forbidden_command")
            state = self.state()
            if not state.ok:
                safe_reason_code = state.safe_reason_code
                if not safe_reason_code or safe_reason_code == "configured":
                    safe_reason_code = "liepin_opencli_terminal_state"
                return OpenCliBrowserResult(
                    ok=False,
                    action="extract_structured_liepin_cards",
                    safe_reason_code=safe_reason_code,
                )
            probe_output = self._run_opencli_call(
                lambda: self._automation.readonly_eval(
                    _liepin_structured_cards_payload_probe_script(max_cards=max_cards)
                )
            )
            self._touch_lease()
            cards = _safe_structured_cards_from_probe_output(probe_output, max_cards=max_cards)
            payload_cards = json.loads(json.dumps(cards, ensure_ascii=False))
            payload = {
                "schema_version": "seektalent.opencli_liepin_structured_cards.v1",
                "source_run_id": source_run_id,
                "cards": payload_cards,
                "card_count": len(cards),
            }
            return OpenCliBrowserResult(
                ok=True,
                action="extract_structured_liepin_cards",
                counts={"cards": len(cards)},
                observation=payload,
                private_output=json.dumps(payload, ensure_ascii=False),
            )
        except OpenCliBrowserError as exc:
            return OpenCliBrowserResult(
                ok=False,
                action="extract_structured_liepin_cards",
                safe_reason_code=exc.safe_reason_code,
            )

    def extract_visible_liepin_cards(self, *, source_run_id: str, max_cards: int) -> OpenCliBrowserResult:
        result = self.extract_structured_liepin_cards(source_run_id=source_run_id, max_cards=max_cards)
        if not result.ok:
            safe_reason_code = result.safe_reason_code
            if not safe_reason_code or safe_reason_code == "configured":
                safe_reason_code = "liepin_opencli_terminal_state"
            return OpenCliBrowserResult(
                ok=False,
                action="extract_visible_liepin_cards",
                safe_reason_code=safe_reason_code,
                counts=result.counts,
            )
        return OpenCliBrowserResult(
            ok=result.ok,
            action="extract_visible_liepin_cards",
            safe_reason_code=result.safe_reason_code,
            counts=result.counts,
            observation=result.observation,
            private_output=result.private_output,
        )

    def open_liepin_detail(self, *, source_run_id: str, ref: str, rank: int) -> OpenCliBrowserResult:
        return self._open_liepin_detail(source_run_id=source_run_id, ref=ref, rank=rank, emit_events=True)

    def _open_liepin_detail(
        self,
        *,
        source_run_id: str,
        ref: str,
        rank: int,
        emit_events: bool,
    ) -> OpenCliBrowserResult:
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
            if emit_events:
                self._append_agent_event(
                    source_run_id,
                    {"action_kind": "open_detail", "route_kind": "detail", "ref": ref, "rank": rank},
                )
            if self._open_liepin_detail_ref_controlled(ref, source_run_id=source_run_id):
                if emit_events:
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
                    if emit_events:
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
                if emit_events:
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
            if emit_events:
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
        emit_events: bool = True,
    ) -> OpenCliBrowserResult:
        try:
            if rank < 1 or rank > 100 or not _is_safe_page_id(ref) or not _is_liepin_detail_url(detail_url):
                raise OpenCliBrowserError("liepin_opencli_forbidden_command")
            self._pace_before_action("open_liepin_detail")
            if emit_events:
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
            if emit_events:
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

    def wait_liepin_detail_ready(self, *, source_run_id: str, rank: int) -> OpenCliBrowserResult:
        try:
            if rank < 1 or rank > 100:
                raise OpenCliBrowserError("liepin_opencli_forbidden_command")
            self._detail_state_text_until_resume_ready()
            return OpenCliBrowserResult(
                ok=True,
                action="wait_liepin_detail_ready",
                counts={"rank": rank},
            )
        except OpenCliBrowserError as exc:
            return OpenCliBrowserResult(
                ok=False,
                action="wait_liepin_detail_ready",
                safe_reason_code=exc.safe_reason_code,
                counts={"rank": rank},
            )

    def capture_liepin_detail_resume(self, *, source_run_id: str, rank: int) -> OpenCliBrowserResult:
        return self._capture_liepin_detail_resume(
            source_run_id=source_run_id,
            rank=rank,
            require_ready=True,
            emit_events=True,
        )

    def _capture_liepin_detail_resume(
        self,
        *,
        source_run_id: str,
        rank: int,
        require_ready: bool,
        emit_events: bool,
        claim_aware: bool = False,
        expected_provider_candidate_key_hash: str | None = None,
    ) -> OpenCliBrowserResult:
        try:
            if rank < 1 or rank > 100:
                raise OpenCliBrowserError("liepin_opencli_forbidden_command")
            safe_run_id = _safe_artifact_segment(source_run_id)
            if require_ready:
                self._detail_state_text_until_resume_ready()
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
            if claim_aware:
                provider_candidate_key_hash = (
                    stable_liepin_detail_candidate_key_hash(source_url) if source_url is not None else None
                )
                if (
                    not _is_provider_candidate_key_hash(expected_provider_candidate_key_hash)
                    or provider_candidate_key_hash is None
                    or provider_candidate_key_hash != expected_provider_candidate_key_hash
                ):
                    raise OpenCliBrowserError("liepin_opencli_candidate_identity_mismatch")
            elif source_url is not None:
                detail_payload["sourceUrl"] = source_url
            raw_snapshot_ref = self._write_pi_artifact(
                "protected",
                f"liepin-opencli/raw/{safe_run_id}/{rank}.json",
                {
                    "schema_version": "seektalent.liepin_opencli_detail_raw.v1",
                    "source_run_id": source_run_id,
                    "provider_rank": rank,
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
            resume: dict[str, object] = {
                "provider_rank": rank,
                "protected_snapshot_ref": raw_snapshot_ref,
                "normalized_snapshot_ref": normalized_snapshot_ref,
                "detail_payload": detail_payload,
                "normalized_text": structured_liepin_detail_text(payload),
            }
            if claim_aware:
                resume["provider_candidate_key_hash"] = provider_candidate_key_hash
                resume["claim_aware"] = True
            else:
                provider_material_ref = self._write_pi_artifact(
                    "protected",
                    f"liepin-opencli/provider-key/{safe_run_id}/{rank}.txt",
                    _detail_provider_key_material(safe_run_id=safe_run_id, rank=rank, payload=payload),
                )
                resume["provider_candidate_key_material_ref"] = provider_material_ref
                resume["candidate_resume_id"] = f"liepin-opencli-detail-{safe_run_id}-{rank}"
            resumes = self._upsert_collected_resume(safe_run_id, rank=rank, resume=resume)
            if emit_events:
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

    def _capture_liepin_detail_resume_claim_aware(
        self,
        *,
        source_run_id: str,
        rank: int,
        expected_provider_candidate_key_hash: str,
        require_ready: bool = True,
        emit_events: bool = False,
    ) -> OpenCliBrowserResult:
        return self._capture_liepin_detail_resume(
            source_run_id=source_run_id,
            rank=rank,
            require_ready=require_ready,
            emit_events=emit_events,
            claim_aware=True,
            expected_provider_candidate_key_hash=expected_provider_candidate_key_hash,
        )

    def _discard_collected_liepin_detail_resume(self, *, source_run_id: str, rank: int) -> None:
        if rank < 1 or rank > 100:
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        safe_run_id = _safe_artifact_segment(source_run_id)
        self._delete_collected_resume(safe_run_id, rank=rank)

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
        from seektalent.providers.liepin.liepin_search_workflow import (
            LiepinSearchWorkflow,
            LiepinSearchWorkflowRequest,
        )

        return LiepinSearchWorkflow(site=_LiepinSearchWorkflowSite(self)).search_detail_backed_resumes(
            LiepinSearchWorkflowRequest(
                source_run_id=source_run_id,
                query=query,
                target_resumes=target_resumes,
                max_pages=max_pages,
                max_cards=max_cards,
                native_filters=native_filters,
            )
        )

    def _search_liepin_resumes_with_detail_open_claim_context(
        self,
        *,
        source_run_id: str,
        query: str,
        target_resumes: int,
        max_pages: int,
        max_cards: int,
        native_filters: Mapping[str, object] | None,
        detail_open_claim_context: DetailOpenClaimSearchContext,
    ) -> dict[str, object]:
        from seektalent.providers.liepin.liepin_search_workflow import (
            LiepinSearchWorkflow,
            LiepinSearchWorkflowRequest,
        )

        return LiepinSearchWorkflow(site=_LiepinSearchWorkflowSite(self))._search_detail_backed_resumes_with_detail_open_claim_context(
            LiepinSearchWorkflowRequest(
                source_run_id=source_run_id,
                query=query,
                target_resumes=target_resumes,
                max_pages=max_pages,
                max_cards=max_cards,
                native_filters=native_filters,
            ),
            detail_open_claim_context=detail_open_claim_context,
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
            opened_result: OpenCliBrowserResult | None = None
            open_post_snapshot: LiepinStateSnapshot | None = None

            def open_search_action() -> TransitionResult:
                nonlocal opened_result
                opened_result = self.open_liepin_tab(LIEPIN_RECRUITER_SEARCH_URL)
                return _result_from_opencli(opened_result)

            def observe_after_open_search() -> LiepinStateSnapshot:
                nonlocal open_post_snapshot
                open_post_snapshot = _snapshot_from_result(self.get_url())
                return open_post_snapshot

            opened = self._run_liepin_transition(
                LiepinTransition(
                    name="open_search",
                    phase="search",
                    observe_pre_state=lambda: LiepinStateSnapshot(ok=True, text="open_search_ready"),
                    precondition=lambda snapshot: snapshot.ok,
                    action=open_search_action,
                    observe_post_state=observe_after_open_search,
                    postcondition=_search_url_ready,
                    safe_reason_code="liepin_opencli_search_not_ready",
                    trace_event="liepin.search.open",
                )
            )
            if not opened.ok:
                events[-1]["ok"] = False
                events[-1]["safe_reason_code"] = opened.safe_reason_code
                return self._blocked_cards_envelope(
                    source_run_id=source_run_id,
                    query=query,
                    safe_reason_code=opened.safe_reason_code or "liepin_opencli_search_not_ready",
                    safe_run_id=safe_run_id,
                    pages_visited=pages_visited,
                    events=events,
                )
            pages_visited = 1
            events.append({"action_kind": "wait_search_ready", "route_kind": "search"})
            first_state: OpenCliBrowserResult | None = None

            def observe_search_ready() -> LiepinStateSnapshot:
                nonlocal first_state
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
                    first_state = self.state()
                    events.append(
                        {"action_kind": "observe_after_unready_retry", "route_kind": "search", "ok": first_state.ok}
                    )
                return _snapshot_from_result(first_state)

            ready_result = self._run_liepin_transition(
                LiepinTransition(
                    name="wait_search_ready",
                    phase="search",
                    observe_pre_state=lambda: (
                        open_post_snapshot
                        or LiepinStateSnapshot(
                            ok=False,
                            text="",
                            safe_reason_code="liepin_opencli_search_not_ready",
                        )
                    ),
                    precondition=_search_url_ready,
                    action=lambda: TransitionResult(ok=True),
                    observe_post_state=observe_search_ready,
                    postcondition=_search_state_nonterminal,
                    safe_reason_code="liepin_opencli_search_not_ready",
                    trace_event="liepin.search.ready",
                )
            )
            if not ready_result.ok or first_state is None:
                events[-1]["ok"] = False
                events[-1]["safe_reason_code"] = ready_result.safe_reason_code
                return self._blocked_cards_envelope(
                    source_run_id=source_run_id,
                    query=query,
                    safe_reason_code=ready_result.safe_reason_code or "liepin_opencli_search_not_ready",
                    safe_run_id=safe_run_id,
                    pages_visited=pages_visited,
                    events=events,
                )
            first_state_text = first_state.private_output or str(first_state.observation.get("text") or "")
            clear_state = first_state

            def clear_native_filters_action() -> TransitionResult:
                nonlocal clear_state
                clear_state = self._clear_liepin_native_filters_if_needed(
                    source_run_id=source_run_id,
                    native_filters=native_filters,
                    current_state=clear_state,
                    events=events,
                )
                return _result_from_opencli(clear_state)

            clear_result = self._run_liepin_transition(
                LiepinTransition(
                    name="clear_native_filters",
                    phase="search",
                    observe_pre_state=lambda: _snapshot_from_result(clear_state),
                    precondition=lambda snapshot: snapshot.ok,
                    action=clear_native_filters_action,
                    observe_post_state=lambda: _snapshot_from_result(clear_state),
                    postcondition=lambda snapshot: snapshot.ok,
                    safe_reason_code="liepin_opencli_filter_clear_failed",
                    trace_event="liepin.filter.clear",
                )
            )
            first_state = clear_state
            if not clear_result.ok or not first_state.ok:
                return self._blocked_cards_envelope(
                    source_run_id=source_run_id,
                    query=query,
                    safe_reason_code=clear_result.safe_reason_code
                    or first_state.safe_reason_code
                    or "liepin_opencli_filter_clear_failed",
                    safe_run_id=safe_run_id,
                    pages_visited=pages_visited,
                    events=events,
                )
            first_state_text = first_state.private_output or str(first_state.observation.get("text") or "")
            events.append({"action_kind": "fill_search", "route_kind": "search", "chars": len(query)})
            search_input_ref = extract_liepin_search_input_ref(first_state_text)
            fill_target = search_input_ref or "搜索"
            fill_retry_state: OpenCliBrowserResult | None = None
            click_ready_state: OpenCliBrowserResult | None = None

            def fill_search_action() -> TransitionResult:
                nonlocal fill_target, fill_retry_state
                for attempt_index in range(3):
                    try:
                        self.fill(target=fill_target, text=query)
                        return TransitionResult(ok=True)
                    except OpenCliBrowserError as exc:
                        if (
                            exc.safe_reason_code
                            not in {
                                "liepin_opencli_stale_ref",
                                "liepin_opencli_status_unavailable",
                            }
                            or attempt_index == 2
                        ):
                            return _result_from_error(exc)
                        retry_event: dict[str, object] = {
                            "action_kind": "fill_search_retry",
                            "route_kind": "search",
                            "chars": len(query),
                        }
                        if exc.safe_reason_code == "liepin_opencli_stale_ref":
                            retry_event["safe_reason_code"] = exc.safe_reason_code
                        events.append(retry_event)
                        fill_retry_state = self.state()
                        events.append(
                            {
                                "action_kind": "observe_before_fill_retry",
                                "route_kind": "search",
                                "ok": fill_retry_state.ok,
                            }
                        )
                        if not fill_retry_state.ok:
                            return _result_from_opencli(fill_retry_state)
                        retry_state_text = fill_retry_state.private_output or str(
                            fill_retry_state.observation.get("text") or ""
                        )
                        retry_input_ref = extract_liepin_search_input_ref(retry_state_text)
                        fill_target = retry_input_ref or fill_target
                return TransitionResult(ok=False, safe_reason_code="liepin_opencli_search_not_ready")

            def observe_after_fill() -> LiepinStateSnapshot:
                nonlocal click_ready_state
                applied_query = self._liepin_search_query_value_from_dom()
                if not _search_query_matches(applied_query, query):
                    events.append(
                        {
                            "action_kind": "verify_search_input",
                            "route_kind": "search",
                            "ok": False,
                            "expected_chars": len(query),
                            "actual_chars": len(applied_query),
                            "safe_reason_code": "liepin_opencli_search_input_unapplied",
                        }
                    )
                    return LiepinStateSnapshot(
                        ok=False,
                        text="",
                        safe_reason_code="liepin_opencli_search_input_unapplied",
                    )
                events.append(
                    {
                        "action_kind": "verify_search_input",
                        "route_kind": "search",
                        "ok": True,
                        "chars": len(query),
                    }
                )
                click_ready_state = fill_retry_state or first_state
                events.append(
                    {
                        "action_kind": "observe_before_click_search",
                        "route_kind": "search",
                        "ok": click_ready_state.ok,
                    }
                )
                return _snapshot_from_result(click_ready_state)

            fill_result = self._run_liepin_transition(
                LiepinTransition(
                    name="fill_search",
                    phase="search",
                    observe_pre_state=lambda: _snapshot_from_result(fill_retry_state or first_state),
                    precondition=lambda snapshot: snapshot.ok,
                    action=fill_search_action,
                    observe_post_state=observe_after_fill,
                    postcondition=_search_state_nonterminal,
                    safe_reason_code="liepin_opencli_search_not_ready",
                    trace_event="liepin.search.fill",
                )
            )
            if not fill_result.ok or click_ready_state is None or not click_ready_state.ok:
                return self._blocked_cards_envelope(
                    source_run_id=source_run_id,
                    query=query,
                    safe_reason_code=fill_result.safe_reason_code
                    or (click_ready_state.safe_reason_code if click_ready_state is not None else None)
                    or "liepin_opencli_search_not_ready",
                    safe_run_id=safe_run_id,
                    pages_visited=pages_visited,
                    events=events,
                )
            click_ready_state_text = click_ready_state.private_output or str(
                click_ready_state.observation.get("text") or ""
            )
            events.append({"action_kind": "click_search", "route_kind": "search"})
            search_click_state_text = click_ready_state_text
            click_retry_state: OpenCliBrowserResult | None = None
            click_post_snapshot: LiepinStateSnapshot | None = None

            def click_search_action() -> TransitionResult:
                nonlocal search_click_state_text, click_retry_state
                for attempt_index in range(3):
                    try:
                        self._click_liepin_search_button(search_click_state_text)
                        return TransitionResult(ok=True)
                    except OpenCliBrowserError as exc:
                        if (
                            exc.safe_reason_code
                            not in {
                                "liepin_opencli_stale_ref",
                                "liepin_opencli_status_unavailable",
                            }
                            or attempt_index == 2
                        ):
                            return _result_from_error(exc)
                        events.append(
                            {
                                "action_kind": "click_search_retry",
                                "route_kind": "search",
                                "safe_reason_code": exc.safe_reason_code,
                            }
                        )
                        click_retry_state = self.state()
                        events.append(
                            {
                                "action_kind": "observe_before_click_retry",
                                "route_kind": "search",
                                "ok": click_retry_state.ok,
                            }
                        )
                        if not click_retry_state.ok:
                            return _result_from_opencli(click_retry_state)
                        search_click_state_text = click_retry_state.private_output or str(
                            click_retry_state.observation.get("text") or ""
                        )
                return TransitionResult(ok=False, safe_reason_code="liepin_opencli_search_not_ready")

            def observe_after_click_search() -> LiepinStateSnapshot:
                nonlocal click_post_snapshot
                click_post_snapshot = _snapshot_from_result(self.get_url())
                return click_post_snapshot

            click_result = self._run_liepin_transition(
                LiepinTransition(
                    name="click_search",
                    phase="search",
                    observe_pre_state=lambda: _snapshot_from_result(click_retry_state or click_ready_state),
                    precondition=lambda snapshot: snapshot.ok,
                    action=click_search_action,
                    observe_post_state=observe_after_click_search,
                    postcondition=_search_url_ready,
                    safe_reason_code="liepin_opencli_search_not_ready",
                    trace_event="liepin.search.submit",
                )
            )
            if not click_result.ok:
                return self._blocked_cards_envelope(
                    source_run_id=source_run_id,
                    query=query,
                    safe_reason_code=click_result.safe_reason_code or "liepin_opencli_search_not_ready",
                    safe_run_id=safe_run_id,
                    pages_visited=pages_visited,
                    events=events,
                )
            final_state: OpenCliBrowserResult | None = None

            def observe_results_action() -> TransitionResult:
                return TransitionResult(ok=True)

            def observe_results_post_state() -> LiepinStateSnapshot:
                nonlocal final_state
                for attempt_index in range(3):
                    try:
                        observed_state = self.state()
                    except OpenCliBrowserError as exc:
                        events.append(
                            {
                                "action_kind": "observe_results_retry",
                                "route_kind": "search",
                                "safe_reason_code": exc.safe_reason_code,
                            }
                        )
                        if exc.safe_reason_code not in {
                            "liepin_opencli_stale_ref",
                            "liepin_opencli_status_unavailable",
                        }:
                            return LiepinStateSnapshot(
                                ok=False,
                                text="",
                                safe_reason_code=exc.safe_reason_code,
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
                        snapshot = _snapshot_from_result(observed_state)
                        if _search_state_ready_for_card_extraction(snapshot):
                            final_state = observed_state
                            return snapshot
                        if attempt_index < 2:
                            try:
                                self._run_browser_command("wait", ("selector", "#resultList"))
                                self._touch_lease()
                                events.append(
                                    {
                                        "action_kind": "wait_results_condition",
                                        "route_kind": "search",
                                        "condition": "selector:#resultList",
                                        "ok": True,
                                    }
                                )
                            except OpenCliBrowserError as exc:
                                events.append(
                                    {
                                        "action_kind": "observe_results_retry",
                                        "route_kind": "search",
                                        "safe_reason_code": exc.safe_reason_code,
                                    }
                                )
                                if exc.safe_reason_code not in {
                                    "liepin_opencli_stale_ref",
                                    "liepin_opencli_status_unavailable",
                                }:
                                    return LiepinStateSnapshot(
                                        ok=False,
                                        text="",
                                        safe_reason_code=exc.safe_reason_code,
                                    )
                            continue
                        final_state = observed_state
                        return snapshot
                    if observed_state.safe_reason_code == "liepin_opencli_status_unavailable" and attempt_index < 2:
                        events.append(
                            {
                                "action_kind": "observe_results_retry",
                                "route_kind": "search",
                                "safe_reason_code": observed_state.safe_reason_code,
                            }
                        )
                        continue
                    return _snapshot_from_result(observed_state)
                return LiepinStateSnapshot(
                    ok=False,
                    text="",
                    safe_reason_code="liepin_opencli_status_unavailable",
                )

            observe_results = self._run_liepin_transition(
                LiepinTransition(
                    name="observe_results",
                    phase="search",
                    observe_pre_state=lambda: (
                        click_post_snapshot
                        or LiepinStateSnapshot(
                            ok=False,
                            text="",
                            safe_reason_code="liepin_opencli_search_not_ready",
                        )
                    ),
                    precondition=_search_url_ready,
                    action=observe_results_action,
                    observe_post_state=observe_results_post_state,
                    postcondition=_search_state_ready_for_card_extraction,
                    safe_reason_code="liepin_opencli_status_unavailable",
                    trace_event="liepin.search.observe_results",
                )
            )
            if not observe_results.ok or final_state is None:
                return self._blocked_cards_envelope(
                    source_run_id=source_run_id,
                    query=query,
                    safe_reason_code=observe_results.safe_reason_code or "liepin_opencli_status_unavailable",
                    safe_run_id=safe_run_id,
                    pages_visited=pages_visited,
                    events=events,
                )
            if native_filters:
                filter_state = final_state

                def apply_native_filter_action() -> TransitionResult:
                    nonlocal filter_state
                    filter_state = self._apply_liepin_native_filters(
                        native_filters=native_filters,
                        current_state=filter_state,
                        events=events,
                    )
                    return _result_from_opencli(filter_state)

                apply_filter_result = self._run_liepin_transition(
                    LiepinTransition(
                        name="apply_native_filter",
                        phase="search",
                        observe_pre_state=lambda: _snapshot_from_result(filter_state),
                        precondition=lambda snapshot: snapshot.ok,
                        action=apply_native_filter_action,
                        observe_post_state=lambda: _snapshot_from_result(filter_state),
                        postcondition=lambda snapshot: snapshot.ok,
                        safe_reason_code="liepin_opencli_filter_unapplied",
                        trace_event="liepin.filter.apply_native",
                    )
                )
                final_state = filter_state
                if not apply_filter_result.ok or not final_state.ok:
                    return self._blocked_cards_envelope(
                        source_run_id=source_run_id,
                        query=query,
                        safe_reason_code=apply_filter_result.safe_reason_code
                        or final_state.safe_reason_code
                        or "liepin_opencli_filter_unapplied",
                        safe_run_id=safe_run_id,
                        pages_visited=pages_visited,
                        events=events,
                    )
                final_state = self._ensure_liepin_results_ready_after_native_filters(
                    current_state=final_state,
                    events=events,
                )
                if not final_state.ok:
                    return self._blocked_cards_envelope(
                        source_run_id=source_run_id,
                        query=query,
                        safe_reason_code=final_state.safe_reason_code or "liepin_opencli_results_not_ready",
                        safe_run_id=safe_run_id,
                        pages_visited=pages_visited,
                        events=events,
                    )
            state_text = final_state.private_output
            structured_cards: OpenCliBrowserResult | None = None
            events.append({"action_kind": "extract_structured_cards", "route_kind": "search"})

            def extract_structured_cards_action() -> TransitionResult:
                nonlocal structured_cards
                structured_cards = self.extract_structured_liepin_cards(
                    source_run_id=source_run_id, max_cards=max_cards
                )
                return _result_from_opencli(structured_cards)

            extract_result = self._run_liepin_transition(
                LiepinTransition(
                    name="extract_structured_cards",
                    phase="search",
                    observe_pre_state=lambda: _snapshot_from_result(final_state),
                    precondition=lambda snapshot: snapshot.ok,
                    action=extract_structured_cards_action,
                    observe_post_state=lambda: _snapshot_from_result(
                        structured_cards
                        or OpenCliBrowserResult(
                            ok=False,
                            action="extract_structured_liepin_cards",
                            safe_reason_code="liepin_opencli_malformed_state",
                        )
                    ),
                    postcondition=lambda snapshot: snapshot.ok,
                    safe_reason_code="liepin_opencli_card_extract_failed",
                    trace_event="liepin.search.extract_cards",
                )
            )
            if not extract_result.ok or structured_cards is None or not structured_cards.ok:
                events[-1]["ok"] = False
                events[-1]["safe_reason_code"] = extract_result.safe_reason_code
                return self._blocked_cards_envelope(
                    source_run_id=source_run_id,
                    query=query,
                    safe_reason_code=extract_result.safe_reason_code
                    or (structured_cards.safe_reason_code if structured_cards is not None else None)
                    or "liepin_opencli_card_extract_failed",
                    safe_run_id=safe_run_id,
                    pages_visited=pages_visited,
                    events=events,
                )
            events[-1]["ok"] = True
            raw_cards = structured_cards.observation.get("cards")
            cards = (
                tuple(dict(item) for item in raw_cards if isinstance(item, Mapping))
                if isinstance(raw_cards, Sequence)
                else ()
            )
            events.append(
                {
                    "action_kind": "visible_cards_observed",
                    "route_kind": "search",
                    "visible_cards": len(cards),
                }
            )
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

    def _ensure_liepin_results_ready_after_native_filters(
        self,
        *,
        current_state: OpenCliBrowserResult,
        events: list[dict[str, object]],
    ) -> OpenCliBrowserResult:
        ready_state = current_state

        def observe_state() -> LiepinStateSnapshot:
            return _snapshot_from_result(ready_state)

        def observe_until_ready() -> TransitionResult:
            nonlocal ready_state
            snapshot = observe_state()
            if _search_state_ready_for_card_extraction(snapshot):
                events.append(
                    {
                        "action_kind": "observe_results_after_native_filters",
                        "route_kind": "search",
                        "ok": ready_state.ok,
                        "ready": True,
                        "attempt": 0,
                    }
                )
                return TransitionResult(ok=True)
            events.append(
                {
                    "action_kind": "observe_results_after_native_filters",
                    "route_kind": "search",
                    "ok": ready_state.ok,
                    "ready": False,
                    "attempt": 1,
                }
            )
            try:
                self._run_browser_command("wait", ("selector", "#resultList"))
                self._touch_lease()
                events.append(
                    {
                        "action_kind": "wait_results_after_native_filters",
                        "route_kind": "search",
                        "ok": True,
                        "condition": "selector:#resultList",
                    }
                )
                ready_state = self.state()
            except OpenCliBrowserError as exc:
                events.append(
                    {
                        "action_kind": "wait_results_after_native_filters",
                        "route_kind": "search",
                        "ok": False,
                        "condition": "selector:#resultList",
                        "safe_reason_code": exc.safe_reason_code,
                    }
                )
                return TransitionResult(ok=False, safe_reason_code=exc.safe_reason_code)
            snapshot = observe_state()
            if not snapshot.ok:
                return _result_from_opencli(ready_state)
            events.append(
                {
                    "action_kind": "observe_results_after_native_filters",
                    "route_kind": "search",
                    "ok": True,
                    "ready": _search_state_ready_for_card_extraction(snapshot),
                    "attempt": 1,
                }
            )
            if _search_state_ready_for_card_extraction(snapshot):
                return TransitionResult(ok=True)
            return TransitionResult(ok=False, safe_reason_code="liepin_opencli_results_not_ready")

        result = self._run_liepin_transition(
            LiepinTransition(
                name="observe_results_after_native_filters",
                phase="search",
                observe_pre_state=observe_state,
                precondition=lambda snapshot: snapshot.ok,
                action=observe_until_ready,
                observe_post_state=observe_state,
                postcondition=_search_state_ready_for_card_extraction,
                safe_reason_code="liepin_opencli_results_not_ready",
                trace_event="liepin.search.after_native_filters_ready",
            )
        )
        if result.ok:
            return ready_state
        return OpenCliBrowserResult(
            ok=False,
            action="observe_results_after_native_filters",
            safe_reason_code=result.safe_reason_code or "liepin_opencli_results_not_ready",
        )

    def _clear_liepin_native_filters_if_needed(
        self,
        *,
        source_run_id: str,
        native_filters: Mapping[str, object] | None,
        current_state: OpenCliBrowserResult,
        events: list[dict[str, object]],
    ) -> OpenCliBrowserResult:
        scope = _native_filter_clear_scope(source_run_id)
        signature = _native_filter_clear_signature(native_filters)
        if self._native_filter_clear_signatures_by_scope.get(scope) == signature:
            return current_state
        state_text = _opencli_result_text(current_state)
        clear_ref = native_filter_clear_filters_ref(state_text)
        if clear_ref is None:
            self._native_filter_clear_signatures_by_scope[scope] = signature
            return current_state
        self._click_native_filter_ref(clear_ref)
        events.append({"action_kind": "clear_native_filters", "route_kind": "search", "ok": True})
        state = self.state()
        events.append({"action_kind": "observe_after_clear_native_filters", "route_kind": "search", "ok": state.ok})
        if state.ok:
            self._native_filter_clear_signatures_by_scope[scope] = signature
        return state

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
            clicked_option = False
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
                force_city_picker = filter_name == "city" and section in {"current", "expected"} and attempt_index > 0
                if force_city_picker or not native_filter_option_visible_in_section(
                    state_text, section=section, label=label
                ):
                    control_ref = native_filter_control_ref_in_section(state_text, section=section)
                    if control_ref is None and filter_name == "city" and section in {"current", "expected"}:
                        control_ref = self._liepin_city_choose_ref_from_dom(section=section)
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
                    self._wait_for_text_condition(label)
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
                    state = self._find_liepin_city_filter_option(
                        section=section,
                        label=label,
                        current_state=state,
                        events=events,
                    )
                    state_text = _opencli_result_text(state)
                self._click_native_filter_option(label, state_text=state_text, section=section)
                clicked_option = True
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
                if (
                    filter_name == "city"
                    and section in {"current", "expected"}
                    and not native_filter_selection_applied(state_text, section=section, label=label)
                    and native_filter_city_picker_selection_contains(state_text, label=label)
                    and (confirm_ref := native_filter_city_confirm_ref(state_text)) is not None
                ):
                    self._click_native_filter_ref(confirm_ref)
                    events.append(
                        {
                            "action_kind": "confirm_native_city_filter",
                            "filter": "city",
                            "section": section,
                            "value": label,
                            "ok": True,
                        }
                    )
                    state = self.state()
                    events.append(
                        {
                            "action_kind": "observe_after_native_city_filter_confirm",
                            "filter": "city",
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
                    if filter_name == "schoolTypes":
                        state = self.state()
                        events.append(
                            {
                                "action_kind": "observe_after_unverified_toggle_filter",
                                "filter": filter_name,
                                "section": section,
                                "ok": state.ok,
                            }
                        )
                    if not state.ok:
                        raise OpenCliBrowserError(state.safe_reason_code)
                    state_text = _opencli_result_text(state)
                    if native_filter_selection_applied(state_text, section=section, label=label):
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
                if (
                    filter_name == "schoolTypes"
                    and clicked_option
                    and exc.safe_reason_code == "liepin_opencli_filter_unapplied"
                ):
                    raise
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

    def _find_liepin_city_filter_option(
        self,
        *,
        section: str,
        label: str,
        current_state: OpenCliBrowserResult,
        events: list[dict[str, object]],
    ) -> OpenCliBrowserResult:
        state = current_state
        state_text = _opencli_result_text(state)
        if native_filter_option_visible_in_section(state_text, section=section, label=label):
            return state
        if (input_ref := native_filter_city_search_input_ref(state_text)) is not None:
            self.fill(target=input_ref, text=label)
            events.append(
                {"action_kind": "fill_native_city_filter_search", "filter": "city", "value": label, "ok": True}
            )
            self._wait_for_text_condition(label)
            state = self.state()
            events.append({"action_kind": "observe_native_city_filter_search", "filter": "city", "ok": state.ok})
            if not state.ok:
                raise OpenCliBrowserError(state.safe_reason_code)
            state_text = _opencli_result_text(state)
            if native_filter_option_visible_in_section(state_text, section=section, label=label):
                return state
        if (overseas_ref := native_filter_city_overseas_tab_ref(state_text)) is not None:
            self._click_native_filter_ref(overseas_ref)
            events.append(
                {"action_kind": "open_native_city_overseas_tab", "filter": "city", "value": label, "ok": True}
            )
            self._wait_for_text_condition(label)
            state = self.state()
            events.append({"action_kind": "observe_native_city_overseas_tab", "filter": "city", "ok": state.ok})
            if not state.ok:
                raise OpenCliBrowserError(state.safe_reason_code)
            state_text = _opencli_result_text(state)
            if native_filter_option_visible_in_section(state_text, section=section, label=label):
                return state
        raise OpenCliBrowserError("liepin_opencli_filter_option_unavailable")

    def _liepin_city_choose_ref_from_dom(self, *, section: str) -> str | None:
        if section not in {"current", "expected"}:
            return None
        try:
            output = self._run_fixed_readonly_eval_probe(probe_name="liepin_city_choose_ref", ref=section).strip()
        except OpenCliBrowserError:
            return None
        if not output or output == "null":
            return None
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError:
            parsed = output
        if isinstance(parsed, str) and parsed.strip():
            return parsed.strip()
        return None

    def _liepin_search_query_value_from_dom(self) -> str:
        output = self._run_fixed_readonly_eval_probe(probe_name="liepin_search_query_value", ref="current")
        try:
            payload = json.loads(output)
        except json.JSONDecodeError as exc:
            raise OpenCliBrowserError("liepin_opencli_malformed_state") from exc
        if not isinstance(payload, Mapping):
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        if payload.get("schema_version") != "seektalent.liepin_search_query_value.v1":
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        if payload.get("ok") is False:
            reason = payload.get("safeReasonCode")
            if isinstance(reason, str) and reason.startswith("liepin_opencli_"):
                raise OpenCliBrowserError(reason)
            raise OpenCliBrowserError("liepin_opencli_search_input_unapplied")
        value = payload.get("value")
        if not isinstance(value, str):
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        return value

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

    def _write_json_atomic(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)

    def _append_agent_event(self, source_run_id: str, event: Mapping[str, object]) -> None:
        safe_run_id = _safe_artifact_segment(source_run_id)
        path = self._pi_artifact_path("protected", f"pi-trace/{safe_run_id}/agent-events.json")

        def update(state: object) -> dict[str, object]:
            if not isinstance(state, dict):
                raise OpenCliBrowserError("liepin_opencli_malformed_state")
            state_dict = cast(dict[str, object], state)
            raw_events = state_dict.get("events")
            if not isinstance(raw_events, list):
                raise OpenCliBrowserError("liepin_opencli_malformed_state")
            events = [dict(item) for item in raw_events if isinstance(item, dict)]
            events.append(dict(event))
            return {
                "schema_version": "seektalent.opencli_agent_events.v1",
                "events": events,
            }

        try:
            locked_json_update(
                path,
                {"schema_version": "seektalent.opencli_agent_events.v1", "events": []},
                update,
            )
        except json.JSONDecodeError as exc:
            raise OpenCliBrowserError("liepin_opencli_malformed_state") from exc

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
        path = self._pi_artifact_path("protected", f"pi-detail/{safe_run_id}/collected-resumes.json")
        with opencli_state_lock(path):
            self._write_json_atomic(
                path,
                {
                    "schema_version": "seektalent.opencli_collected_resumes.v1",
                    "resumes": [dict(item) for item in resumes],
                },
            )

    def _upsert_collected_resume(
        self,
        safe_run_id: str,
        *,
        rank: int,
        resume: Mapping[str, object],
    ) -> list[dict[str, object]]:
        path = self._pi_artifact_path("protected", f"pi-detail/{safe_run_id}/collected-resumes.json")

        def update(state: object) -> dict[str, object]:
            if not isinstance(state, dict):
                raise OpenCliBrowserError("liepin_opencli_malformed_state")
            state_dict = cast(dict[str, object], state)
            raw_resumes = state_dict.get("resumes")
            if not isinstance(raw_resumes, list):
                raise OpenCliBrowserError("liepin_opencli_malformed_state")
            resumes: list[dict[str, object]] = []
            for item in raw_resumes:
                if not isinstance(item, dict):
                    continue
                item_dict = cast(dict[str, object], item)
                if _positive_int_or_none(item_dict.get("provider_rank")) != rank:
                    resumes.append(dict(item_dict))
            resumes.append(dict(resume))
            resumes.sort(key=lambda item: _positive_int_or_none(item.get("provider_rank")) or 0)
            return {
                "schema_version": "seektalent.opencli_collected_resumes.v1",
                "resumes": resumes,
            }

        try:
            updated = locked_json_update(
                path,
                {"schema_version": "seektalent.opencli_collected_resumes.v1", "resumes": []},
                update,
            )
        except json.JSONDecodeError as exc:
            raise OpenCliBrowserError("liepin_opencli_malformed_state") from exc
        raw_resumes = updated.get("resumes")
        if not isinstance(raw_resumes, list):
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        return [dict(cast(dict[str, object], item)) for item in raw_resumes if isinstance(item, dict)]

    def _delete_collected_resume(self, safe_run_id: str, *, rank: int) -> list[dict[str, object]]:
        path = self._pi_artifact_path("protected", f"pi-detail/{safe_run_id}/collected-resumes.json")

        def update(state: object) -> dict[str, object]:
            if not isinstance(state, dict):
                raise OpenCliBrowserError("liepin_opencli_malformed_state")
            state_dict = cast(dict[str, object], state)
            raw_resumes = state_dict.get("resumes")
            if not isinstance(raw_resumes, list):
                raise OpenCliBrowserError("liepin_opencli_malformed_state")
            resumes: list[dict[str, object]] = []
            for item in raw_resumes:
                if not isinstance(item, dict):
                    continue
                item_dict = cast(dict[str, object], item)
                if _positive_int_or_none(item_dict.get("provider_rank")) != rank:
                    resumes.append(dict(item_dict))
            return {
                "schema_version": "seektalent.opencli_collected_resumes.v1",
                "resumes": resumes,
            }

        try:
            updated = locked_json_update(
                path,
                {"schema_version": "seektalent.opencli_collected_resumes.v1", "resumes": []},
                update,
            )
        except json.JSONDecodeError as exc:
            raise OpenCliBrowserError("liepin_opencli_malformed_state") from exc
        raw_resumes = updated.get("resumes")
        if not isinstance(raw_resumes, list):
            raise OpenCliBrowserError("liepin_opencli_malformed_state")
        return [dict(cast(dict[str, object], item)) for item in raw_resumes if isinstance(item, dict)]

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
        if _is_liepin_recruiter_search_surface(tab_url):
            return True
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
        page_id = self._open_new_liepin_tab(url=detail_url, source_run_id=source_run_id)
        if page_id is None:
            self._delete_lease()
            return True
        self._wait_for_controlled_detail_navigation(page_id=page_id)
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
        if _is_liepin_recruiter_search_surface(url):
            return True
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

    def _select_existing_liepin_search_tab(self, *, expected_url: str) -> tuple[str | None, dict[str, str]]:
        try:
            tabs = self._list_tabs()
        except OpenCliBrowserError:
            return None, {}
        before_urls = _tab_urls_by_page_id(tabs)
        candidates: list[tuple[int, str]] = []
        for tab in tabs:
            page_id = _tab_page_id(tab)
            tab_url = str(tab.get("url") or "")
            if not _is_safe_page_id(page_id):
                continue
            if not _is_liepin_recruiter_search_surface(tab_url) and not _url_matches_start_surface(
                tab_url, expected_url
            ):
                continue
            if tab.get("active") is True:
                continue
            score = 1
            candidates.append((score, page_id))
        if not candidates:
            return None, before_urls
        _, page_id = max(candidates, key=lambda item: item[0])
        for tab in tabs:
            if _tab_page_id(tab) == page_id and tab.get("active") is True:
                return page_id, before_urls
        try:
            self._run_browser_command("tab", ("select", page_id))
        except OpenCliBrowserError:
            return None, before_urls
        return page_id, before_urls

    def _open_new_liepin_tab(
        self,
        *,
        url: str,
        source_run_id: str | None = None,
        before_urls: Mapping[str, str] | None = None,
    ) -> str | None:
        return self._open_opencli_managed_liepin_tab(url=url, source_run_id=source_run_id, before_urls=before_urls)

    def _open_opencli_managed_liepin_tab(
        self,
        *,
        url: str,
        source_run_id: str | None = None,
        before_urls: Mapping[str, str] | None = None,
    ) -> str | None:
        self._validate_start_or_detail_url(url)
        if before_urls is None:
            before_urls = self._tab_urls_before_open()
        try:
            output = self._run_browser_command("tab", ("new", url))
        except OpenCliBrowserError as exc:
            if exc.safe_reason_code == "liepin_opencli_status_unavailable":
                output = ""
            elif exc.safe_reason_code != "liepin_opencli_window_policy_blocked":
                raise
            else:
                self._run_browser_command("unbind", ())
                output = self._run_browser_command("tab", ("new", url))
        try:
            page_id = self._parse_opened_tab_page_id(output=output, url=url, before_urls=before_urls)
        except OpenCliBrowserError as exc:
            if exc.safe_reason_code != "liepin_opencli_tab_response_malformed":
                raise
            if self._open_current_liepin_page(url):
                return None
            raise
        if page_id is None:
            return None
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

    def _tab_urls_before_open(self) -> dict[str, str]:
        try:
            return _tab_urls_by_page_id(self._list_tabs())
        except OpenCliBrowserError:
            return {}

    def _parse_opened_tab_page_id(self, *, output: str, url: str, before_urls: Mapping[str, str]) -> str | None:
        try:
            return _parse_page_id(output)
        except OpenCliBrowserError as exc:
            if exc.safe_reason_code != "liepin_opencli_tab_response_malformed":
                raise
        page_id = self._opened_tab_page_id_from_list(url=url, before_urls=before_urls)
        if page_id is not None:
            return page_id
        try:
            self._run_browser_command("bind", ())
        except OpenCliBrowserError:
            raise OpenCliBrowserError("liepin_opencli_tab_response_malformed")
        page_id = self._opened_tab_page_id_from_list(url=url, before_urls=before_urls)
        if page_id is None:
            if self._current_bound_page_matches_requested_url(url):
                return None
            raise OpenCliBrowserError("liepin_opencli_tab_response_malformed")
        return page_id

    def _opened_tab_page_id_from_list(self, *, url: str, before_urls: Mapping[str, str]) -> str | None:
        try:
            after_tabs = self._list_tabs()
        except OpenCliBrowserError:
            return None
        candidates: list[tuple[int, str]] = []
        for tab in after_tabs:
            page_id = _tab_page_id(tab)
            tab_url = str(tab.get("url") or "")
            if not _is_safe_page_id(page_id) or not self._opened_tab_url_matches_requested_url(tab_url, url):
                continue
            score = 0
            if page_id not in before_urls:
                score += 100
            if tab.get("active") is True:
                score += 10
            candidates.append((score, page_id))
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[0])[1]

    def _open_current_liepin_page(self, url: str) -> bool:
        self._validate_start_or_detail_url(url)
        try:
            self._run_opencli_call(lambda: self._automation.run_browser_command("open", (url,)))
        except OpenCliBrowserError:
            return False
        return self._current_bound_page_matches_requested_url(url)

    def _opened_tab_url_matches_requested_url(self, tab_url: str, requested_url: str) -> bool:
        if _is_liepin_recruiter_search_surface(tab_url) and _is_liepin_recruiter_search_surface(requested_url):
            return True
        if _url_matches_start_or_detail_surface(tab_url, requested_url):
            return True
        if _is_liepin_detail_url(requested_url):
            return False
        return self._is_liepin_search_context_url(tab_url)

    def _current_bound_page_matches_requested_url(self, requested_url: str) -> bool:
        try:
            current_url = self._current_url()
        except OpenCliBrowserError:
            return False
        return self._opened_tab_url_matches_requested_url(current_url, requested_url)

    def _wait_for_controlled_detail_navigation(self, *, page_id: str) -> None:
        attempts = max(1, int(self._site_config.detail_open_timeout_seconds))
        for attempt_index in range(attempts):
            current_url = self._current_url()
            if _is_liepin_detail_url(current_url):
                return
            tab_url = self._tab_url_for_page_id(page_id)
            if tab_url is not None and _is_liepin_detail_url(tab_url):
                self._run_browser_command("tab", ("select", page_id))
                return
            if not _is_blank_tab_url(current_url):
                safe_reason_code = classify_liepin_state(url=current_url, text="")
                if safe_reason_code:
                    raise OpenCliBrowserError(safe_reason_code)
            if attempt_index < attempts - 1:
                time.sleep(1)
        raise OpenCliBrowserError("liepin_opencli_detail_not_opened")

    def _tab_url_for_page_id(self, page_id: str) -> str | None:
        if not _is_safe_page_id(page_id):
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        try:
            tabs = self._list_tabs()
        except OpenCliBrowserError:
            return None
        for tab in tabs:
            if _tab_page_id(tab) == page_id:
                return str(tab.get("url") or "")
        return None

    def _reuse_liepin_search_page(self, *, page_id: str, url: str) -> bool:
        try:
            self._run_browser_command("tab", ("select", page_id))
            return self._try_reset_liepin_search_tab(page_id=page_id, url=url)
        except OpenCliBrowserError as exc:
            if exc.safe_reason_code == "liepin_opencli_window_policy_blocked":
                self._forget_owned_page_marker(page_id)
                self._delete_lease()
                self._open_opencli_managed_liepin_tab(url=url, before_urls={})
                return True
            if exc.safe_reason_code not in _RECOVERABLE_TAB_REUSE_REASONS:
                raise
        self._forget_owned_page_marker(page_id)
        self._delete_lease()
        return False

    def _reset_liepin_search_tab(self, *, page_id: str, url: str) -> None:
        if not _is_safe_page_id(page_id):
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        self._validate_start_url(url)
        self._run_browser_command("open", ("--tab", page_id, url))
        self._touch_lease()

    def _try_reset_liepin_search_tab(self, *, page_id: str, url: str) -> bool:
        try:
            self._reset_liepin_search_tab(page_id=page_id, url=url)
        except OpenCliBrowserError as exc:
            if exc.safe_reason_code not in _RECOVERABLE_TAB_REUSE_REASONS:
                raise
            return False
        return True

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
                self._wait_for_detail_resume_condition()
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
        path = self._lease_path()
        with opencli_state_lock(path):
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return
            except json.JSONDecodeError as exc:
                raise OpenCliBrowserError("liepin_opencli_lease_malformed") from exc
            if not isinstance(loaded, dict):
                raise OpenCliBrowserError("liepin_opencli_lease_malformed")
            loaded["last_activity_at"] = time.time()
            self._write_json_atomic(path, loaded)

    def _write_lease_payload(self, payload: Mapping[str, object]) -> None:
        path = self._lease_path()
        with opencli_state_lock(path):
            self._write_json_atomic(path, dict(payload))

    def _delete_lease(self) -> None:
        path = self._lease_path()
        with opencli_state_lock(path):
            try:
                path.unlink()
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
        path = self._owned_pages_path()
        with opencli_state_lock(path):
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
            self._write_json_atomic(path, markers)

    def _forget_owned_page_marker(self, page_id: str) -> None:
        path = self._owned_pages_path()
        with opencli_state_lock(path):
            markers = self._read_owned_page_markers_for_write()
            if page_id not in markers:
                return
            markers.pop(page_id)
            if markers:
                self._write_json_atomic(path, markers)
            else:
                path.unlink(missing_ok=True)

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
        if url in self._site_config.allowed_start_urls or _is_liepin_recruiter_search_surface(url):
            return
        raise OpenCliBrowserError("liepin_opencli_start_url_blocked")

    def _validate_tab_new_url(self, url: str) -> None:
        host = urlparse(url).hostname or ""
        if host not in self._site_config.allowed_hosts:
            raise OpenCliBrowserError("liepin_opencli_host_blocked")
        if (
            url in self._site_config.allowed_start_urls
            or _is_liepin_recruiter_search_surface(url)
            or _is_liepin_detail_url(url)
        ):
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


@dataclass(frozen=True)
class _LiepinSearchWorkflowSite:
    adapter: LiepinSiteAdapter

    def save_liepin_first_page_continuation(self, **kwargs: object) -> ProviderSearchContinuation:
        return self.adapter._save_liepin_first_page_continuation(**cast(dict, kwargs))

    def load_liepin_first_page_continuation(self, opaque_ref: str):
        return self.adapter._load_liepin_first_page_continuation(opaque_ref)

    def discard_liepin_first_page_continuation(self, opaque_ref: str) -> None:
        self.adapter._discard_liepin_first_page_continuation(opaque_ref)

    def mark_liepin_first_page_candidate(self, *, opaque_ref: str, rank: int,
        state: CandidateState) -> None:
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

    def safe_liepin_detail_url_for_ref(self, ref: str) -> str | None:
        return self.adapter._safe_liepin_detail_url_for_ref(ref)

    def open_liepin_detail(self, *, source_run_id: str, ref: str, rank: int) -> OpenCliBrowserResult:
        return self.adapter._open_liepin_detail(
            source_run_id=source_run_id,
            ref=ref,
            rank=rank,
            emit_events=False,
        )

    def open_liepin_detail_cached_url(
        self,
        *,
        source_run_id: str,
        ref: str,
        rank: int,
        detail_url: str,
    ) -> OpenCliBrowserResult:
        return self.adapter._open_liepin_detail_cached_url(
            source_run_id=source_run_id,
            ref=ref,
            rank=rank,
            detail_url=detail_url,
            emit_events=False,
        )

    def wait_liepin_detail_ready(self, *, source_run_id: str, rank: int) -> OpenCliBrowserResult:
        return self.adapter.wait_liepin_detail_ready(source_run_id=source_run_id, rank=rank)

    def capture_liepin_detail_resume(
        self,
        *,
        source_run_id: str,
        rank: int,
        require_ready: bool = True,
    ) -> OpenCliBrowserResult:
        if require_ready:
            return self.adapter.capture_liepin_detail_resume(source_run_id=source_run_id, rank=rank)
        return self.adapter._capture_liepin_detail_resume(
            source_run_id=source_run_id,
            rank=rank,
            require_ready=False,
            emit_events=False,
        )

    def _capture_liepin_detail_resume_claim_aware(
        self,
        *,
        source_run_id: str,
        rank: int,
        expected_provider_candidate_key_hash: str,
        require_ready: bool = True,
    ) -> OpenCliBrowserResult:
        return self.adapter._capture_liepin_detail_resume_claim_aware(
            source_run_id=source_run_id,
            rank=rank,
            expected_provider_candidate_key_hash=expected_provider_candidate_key_hash,
            require_ready=require_ready,
            emit_events=False,
        )

    def discard_liepin_detail_resume(self, *, source_run_id: str, rank: int) -> None:
        self.adapter._discard_collected_liepin_detail_resume(source_run_id=source_run_id, rank=rank)

    def restore_liepin_search_page(self) -> str | None:
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
