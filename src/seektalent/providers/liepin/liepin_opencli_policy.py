from __future__ import annotations

from dataclasses import replace

from seektalent.opencli_browser.contracts import (
    OpenCliBrowserError,
    OpenCliBrowserResult,
)


LIEPIN_RECRUITER_SEARCH_URL = "https://h.liepin.com/search/getConditionItem#session"
LIEPIN_RECRUITER_SEARCH_TAB_REUSE_FRAGMENTS = ("h.liepin.com/search/getConditionItem",)

OPENCLI_TO_LIEPIN_REASON = {
    "opencli_command_missing": "liepin_opencli_command_missing",
    "opencli_timeout": "liepin_opencli_timeout",
    "opencli_extension_disconnected": "liepin_opencli_extension_disconnected",
    "opencli_status_unavailable": "liepin_opencli_status_unavailable",
    "opencli_daemon_not_running": "liepin_opencli_daemon_not_running",
    "opencli_daemon_stale": "liepin_opencli_daemon_stale",
    "opencli_forbidden_command": "liepin_opencli_forbidden_command",
    "opencli_window_policy_blocked": "liepin_opencli_window_policy_blocked",
    "opencli_stale_ref": "liepin_opencli_stale_ref",
    "opencli_selector_not_found": "liepin_opencli_selector_not_found",
    "opencli_selector_ambiguous": "liepin_opencli_selector_ambiguous",
    "opencli_target_not_found": "liepin_opencli_target_not_found",
}


def liepin_reason_from_opencli_reason(reason: str) -> str:
    mapped = OPENCLI_TO_LIEPIN_REASON.get(reason)
    if mapped is not None:
        return mapped
    if reason.startswith("opencli_"):
        return "liepin_opencli_status_unavailable"
    return reason


def liepin_result_from_opencli_result(result: OpenCliBrowserResult) -> OpenCliBrowserResult:
    return replace(result, safe_reason_code=liepin_reason_from_opencli_reason(result.safe_reason_code))


def liepin_error_from_opencli_error(error: OpenCliBrowserError) -> OpenCliBrowserError:
    return OpenCliBrowserError(liepin_reason_from_opencli_reason(error.safe_reason_code))
