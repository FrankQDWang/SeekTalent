from __future__ import annotations


OPENCLI_COMMAND_MISSING = "opencli_command_missing"
OPENCLI_TIMEOUT = "opencli_timeout"
OPENCLI_EXTENSION_DISCONNECTED = "opencli_extension_disconnected"
OPENCLI_STATUS_UNAVAILABLE = "opencli_status_unavailable"
OPENCLI_BOOTSTRAP_FAILED = "opencli_bootstrap_failed"
OPENCLI_DAEMON_NOT_RUNNING = "opencli_daemon_not_running"
OPENCLI_DAEMON_STALE = "opencli_daemon_stale"
OPENCLI_FORBIDDEN_COMMAND = "opencli_forbidden_command"
OPENCLI_WINDOW_POLICY_BLOCKED = "opencli_window_policy_blocked"
OPENCLI_STALE_REF = "opencli_stale_ref"
OPENCLI_SELECTOR_NOT_FOUND = "opencli_selector_not_found"
OPENCLI_SELECTOR_AMBIGUOUS = "opencli_selector_ambiguous"
OPENCLI_TARGET_NOT_FOUND = "opencli_target_not_found"
OPENCLI_BRIDGE_INTEGRITY_FAILED = "opencli_bridge_integrity_failed"
OPENCLI_BRIDGE_WRONG_IMPLEMENTATION = "opencli_bridge_wrong_implementation"
OPENCLI_BRIDGE_BUILD_MISMATCH = "opencli_bridge_build_mismatch"
OPENCLI_BRIDGE_PROTOCOL_MISMATCH = "opencli_bridge_protocol_mismatch"
OPENCLI_BRIDGE_CAPABILITY_MISSING = "opencli_bridge_capability_missing"
OPENCLI_COMMAND_RESULT_UNKNOWN = "opencli_command_result_unknown"

OPENCLI_ERROR_CODE_TO_REASON = {
    "bound_tab_mutation_blocked": OPENCLI_WINDOW_POLICY_BLOCKED,
    "stale_ref": OPENCLI_STALE_REF,
    "selector_not_found": OPENCLI_SELECTOR_NOT_FOUND,
    "selector_ambiguous": OPENCLI_SELECTOR_AMBIGUOUS,
    "target_not_found": OPENCLI_TARGET_NOT_FOUND,
    "not_found": OPENCLI_TARGET_NOT_FOUND,
}
