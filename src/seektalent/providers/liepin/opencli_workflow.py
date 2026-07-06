from __future__ import annotations

from collections.abc import Mapping, Sequence
import re

WorkflowStepPayload = dict[str, object]

_ACTION_TO_STEP_EVENT: dict[str, tuple[str, str, str]] = {
    "search_cards_started": ("prepare_search", "source_workflow_step_started", "running"),
    "open_search": ("prepare_search", "source_workflow_step_started", "running"),
    "wait_search_ready": ("prepare_search", "source_workflow_step_completed", "completed"),
    "fill_search": ("prepare_search", "source_workflow_step_completed", "completed"),
    "search_submitted": ("submit_search", "source_workflow_step_completed", "completed"),
    "click_search": ("submit_search", "source_workflow_step_completed", "completed"),
    "observe_results": ("submit_search", "source_workflow_step_completed", "completed"),
    "observe_results_after_retry": ("submit_search", "source_workflow_step_completed", "completed"),
    "apply_filters_started": ("apply_filters", "source_workflow_step_started", "running"),
    "apply_filters_completed": ("apply_filters", "source_workflow_step_completed", "completed"),
    "clear_native_filters": ("apply_filters", "source_workflow_step_completed", "completed"),
    "open_native_filter_menu": ("apply_filters", "source_workflow_step_started", "running"),
    "apply_native_filter": ("apply_filters", "source_workflow_step_completed", "completed"),
    "verify_native_filter": ("apply_filters", "source_workflow_step_completed", "completed"),
    "skip_native_filter": ("apply_filters", "source_workflow_step_completed", "completed"),
    "extract_structured_cards": ("observe_cards", "source_workflow_step_completed", "completed"),
    "visible_cards_observed": ("observe_cards", "source_workflow_step_completed", "completed"),
    "detail_urls_cached": ("cache_detail_urls", "source_workflow_step_completed", "completed"),
    "detail_candidate_selected": ("open_detail", "source_workflow_step_started", "running"),
    "open_detail": ("open_detail", "source_workflow_step_started", "running"),
    "open_detail_retry_scheduled": ("open_detail", "source_workflow_step_started", "running"),
    "open_detail_succeeded": ("open_detail", "source_workflow_step_completed", "completed"),
    "open_detail_failed": ("open_detail", "source_workflow_step_failed", "failed"),
    "open_detail_retry_exhausted": ("open_detail", "source_workflow_step_failed", "failed"),
    "open_detail_timeout": ("open_detail", "source_workflow_step_failed", "failed"),
    "wait_detail_ready": ("wait_detail_ready", "source_workflow_step_completed", "completed"),
    "observe_detail": ("capture_detail", "source_workflow_step_completed", "completed"),
    "capture_detail_succeeded": ("capture_detail", "source_workflow_step_completed", "completed"),
    "capture_detail_failed": ("capture_detail", "source_workflow_step_failed", "failed"),
    "return_to_search_after_capture": ("observe_cards", "source_workflow_step_completed", "completed"),
    "visible_cards_refreshed_after_return": ("observe_cards", "source_workflow_step_completed", "completed"),
    "visible_cards_refresh_failed_after_return": ("observe_cards", "source_workflow_step_failed", "failed"),
    "detail_target_not_met": ("finalize", "source_workflow_step_failed", "failed"),
}
_SAFE_COUNT_KEYS = {
    "cached_detail_urls",
    "cards_seen",
    "resumes_returned",
    "target_resumes",
    "visible_cards",
    "attempts",
}
_SAFE_METADATA_KEYS = {"rank", "open_mode", "attempt", "next_attempt"}
_SENSITIVE_TEXT = re.compile(r"(?:cookie|secret|token|password|authorization|bearer|raw_resume|provider_id)", re.I)
_SAFE_REASON_CODE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_SAFE_METADATA_TEXT = re.compile(r"^[a-z0-9_:-]{1,80}$")


def workflow_steps_from_action_events(
    events: Sequence[Mapping[str, object]],
    *,
    final_status: str,
    final_reason_code: str | None = None,
    resumes_returned: int,
    action_trace_ref: str | None,
) -> list[WorkflowStepPayload]:
    steps: list[WorkflowStepPayload] = []
    for event in events:
        action_kind = event.get("action_kind")
        mapped = _ACTION_TO_STEP_EVENT.get(action_kind) if isinstance(action_kind, str) else None
        if mapped is None:
            continue
        step_name, event_type, status = mapped
        if event.get("ok") is False:
            event_type = "source_workflow_step_failed"
            status = "failed"
        step: WorkflowStepPayload = {
            "event_type": event_type,
            "step_name": step_name,
            "status": status,
            "safe_counts": _safe_counts(event),
            "safe_metadata": _safe_metadata(event),
            "artifact_refs": [],
        }
        reason_code = _safe_reason_code(event.get("safe_reason_code"))
        if reason_code is not None:
            step["safe_reason_code"] = reason_code
        steps.append(step)

    final_completed = final_status == "succeeded"
    final_step: WorkflowStepPayload = {
        "event_type": "source_workflow_step_completed" if final_completed else "source_workflow_step_failed",
        "step_name": "finalize",
        "status": "completed" if final_completed else _final_failure_status(final_status),
        "safe_counts": {"resumes_returned": max(0, resumes_returned)},
        "safe_metadata": {},
        "artifact_refs": [ref] if (ref := _safe_artifact_ref(action_trace_ref)) is not None else [],
    }
    reason_code = _safe_reason_code(final_reason_code)
    if not final_completed and reason_code is not None:
        final_step["safe_reason_code"] = reason_code
    steps.append(final_step)
    return steps


def _final_failure_status(final_status: str) -> str:
    return final_status if final_status in {"partial", "blocked", "failed", "cancelled"} else "failed"


def _safe_counts(event: Mapping[str, object]) -> dict[str, int]:
    result: dict[str, int] = {}
    for key in _SAFE_COUNT_KEYS:
        value = event.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            continue
        result[key] = value
    return result


def _safe_metadata(event: Mapping[str, object]) -> dict[str, str | int]:
    result: dict[str, str | int] = {}
    for key in _SAFE_METADATA_KEYS:
        value = event.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            if value >= 0:
                result[key] = value
            continue
        if isinstance(value, str):
            clean = value.strip().casefold()
            if _SAFE_METADATA_TEXT.fullmatch(clean) and _SENSITIVE_TEXT.search(clean) is None:
                result[key] = clean
    return result


def _safe_reason_code(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    clean = value.strip().casefold()
    if _SAFE_REASON_CODE.fullmatch(clean) is None or _SENSITIVE_TEXT.search(clean):
        return None
    return clean


def _safe_artifact_ref(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    clean = value.strip()
    if _SENSITIVE_TEXT.search(clean) or ".." in clean or any(character.isspace() for character in clean):
        return None
    return clean if clean.startswith("artifact://protected/") else None
