from __future__ import annotations

from dataclasses import replace

from seektalent.opencli_browser.contracts import (
    OpenCliBrowserError,
    OpenCliBrowserResult,
)
from seektalent.opencli_browser.reason_codes import (
    OPENCLI_COMMAND_MISSING,
    OPENCLI_DAEMON_NOT_RUNNING,
    OPENCLI_DAEMON_STALE,
    OPENCLI_EXTENSION_DISCONNECTED,
    OPENCLI_FORBIDDEN_COMMAND,
    OPENCLI_SELECTOR_AMBIGUOUS,
    OPENCLI_SELECTOR_NOT_FOUND,
    OPENCLI_STALE_REF,
    OPENCLI_STATUS_UNAVAILABLE,
    OPENCLI_TARGET_NOT_FOUND,
    OPENCLI_TIMEOUT,
    OPENCLI_WINDOW_POLICY_BLOCKED,
)


LIEPIN_OPENCLI_ALLOWED_HOSTS = ("www.liepin.com", "h.liepin.com", "c.liepin.com", "lpt.liepin.com")
LIEPIN_RECRUITER_SEARCH_SURFACE_PATHS = ("/search/getConditionItem", "/resume/search")
LIEPIN_RECRUITER_SEARCH_URLS = (
    "https://h.liepin.com/search/getConditionItem#session",
    "https://h.liepin.com/resume/search",
)
LIEPIN_RECRUITER_SEARCH_URL = LIEPIN_RECRUITER_SEARCH_URLS[0]

OPENCLI_TO_LIEPIN_REASON = {
    OPENCLI_COMMAND_MISSING: "liepin_opencli_command_missing",
    OPENCLI_TIMEOUT: "liepin_opencli_timeout",
    OPENCLI_EXTENSION_DISCONNECTED: "liepin_opencli_extension_disconnected",
    OPENCLI_STATUS_UNAVAILABLE: "liepin_opencli_status_unavailable",
    OPENCLI_DAEMON_NOT_RUNNING: "liepin_opencli_daemon_not_running",
    OPENCLI_DAEMON_STALE: "liepin_opencli_daemon_stale",
    OPENCLI_FORBIDDEN_COMMAND: "liepin_opencli_forbidden_command",
    OPENCLI_WINDOW_POLICY_BLOCKED: "liepin_opencli_window_policy_blocked",
    OPENCLI_STALE_REF: "liepin_opencli_stale_ref",
    OPENCLI_SELECTOR_NOT_FOUND: "liepin_opencli_selector_not_found",
    OPENCLI_SELECTOR_AMBIGUOUS: "liepin_opencli_selector_ambiguous",
    OPENCLI_TARGET_NOT_FOUND: "liepin_opencli_target_not_found",
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
