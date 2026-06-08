from __future__ import annotations


OPENCLI_COMMAND_MISSING = "opencli_command_missing"
OPENCLI_TIMEOUT = "opencli_timeout"
OPENCLI_EXTENSION_DISCONNECTED = "opencli_extension_disconnected"
OPENCLI_STATUS_UNAVAILABLE = "opencli_status_unavailable"
OPENCLI_DAEMON_NOT_RUNNING = "opencli_daemon_not_running"
OPENCLI_DAEMON_STALE = "opencli_daemon_stale"
OPENCLI_FORBIDDEN_COMMAND = "opencli_forbidden_command"
OPENCLI_WINDOW_POLICY_BLOCKED = "opencli_window_policy_blocked"
OPENCLI_STALE_REF = "opencli_stale_ref"
OPENCLI_SELECTOR_NOT_FOUND = "opencli_selector_not_found"
OPENCLI_SELECTOR_AMBIGUOUS = "opencli_selector_ambiguous"
OPENCLI_TARGET_NOT_FOUND = "opencli_target_not_found"

OPENCLI_ERROR_CODE_TO_REASON = {
    "bound_tab_mutation_blocked": OPENCLI_WINDOW_POLICY_BLOCKED,
    "stale_ref": OPENCLI_STALE_REF,
    "selector_not_found": OPENCLI_SELECTOR_NOT_FOUND,
    "selector_ambiguous": OPENCLI_SELECTOR_AMBIGUOUS,
    "target_not_found": OPENCLI_TARGET_NOT_FOUND,
    "not_found": OPENCLI_TARGET_NOT_FOUND,
}
