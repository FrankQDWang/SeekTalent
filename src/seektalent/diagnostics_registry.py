"""Closed v1 token registry for privacy-safe diagnostics artifacts."""

from __future__ import annotations

from dataclasses import dataclass


COMPONENTS = frozenset(
    {
        "main",
        "controller",
        "sidecar",
        "worker",
        "wtscli",
        "extension",
        "chrome",
        "provider",
        "llm",
        "sqlite",
        "installer",
        "exporter",
    }
)
PHASES = frozenset(
    {
        "capability",
        "startup",
        "accept",
        "dispatch",
        "execute",
        "observe",
        "commit",
        "cleanup",
        "shutdown",
        "export",
    }
)
DOMAINS = frozenset(
    {
        "install",
        "runtime",
        "storage",
        "browser",
        "source",
        "network",
        "provider",
        "policy",
        "user_action",
        "cleanup",
        "unknown",
    }
)
FAILURE_KINDS = frozenset(
    {
        "capability_mismatch",
        "startup_failure",
        "process_exit",
        "protocol_violation",
        "authority_rejected",
        "model_failure",
        "package_integrity_failure",
        "resource_exhausted",
        "operation_failure",
        "unknown",
    }
)
SEVERITIES = frozenset({"debug", "info", "warn", "error", "fatal"})
STATUSES = frozenset({"started", "completed", "partial", "rejected", "failed", "unknown"})
ARRIVAL_CLASSES = frozenset({"on_time", "late", "replayed"})
REDACTION_RULES = frozenset(
    {
        "field_not_allowlisted",
        "credential_field",
        "business_content_field",
        "account_subject_field",
        "browser_content_field",
        "network_identity_field",
        "machine_identity_field",
        "raw_diagnostic_field",
        "sensitive_value",
    }
)


@dataclass(frozen=True)
class ReasonDefinition:
    reason_code: str
    domain: str
    failure_kind: str


def _reason(reason_code: str, domain: str, failure_kind: str) -> ReasonDefinition:
    return ReasonDefinition(reason_code, domain, failure_kind)


REASON_DEFINITIONS = {
    definition.reason_code: definition
    for definition in (
        _reason("machine_capability_supported", "install", "capability_mismatch"),
        _reason("machine_capability_unsupported", "install", "capability_mismatch"),
        _reason("machine_capability_indeterminate", "install", "capability_mismatch"),
        _reason("component_starting", "runtime", "startup_failure"),
        _reason("component_ready", "runtime", "startup_failure"),
        _reason("component_startup_failed", "runtime", "startup_failure"),
        _reason("component_readiness_observed", "runtime", "startup_failure"),
        _reason("component_process_exited", "runtime", "process_exit"),
        _reason("component_protocol_rejected", "runtime", "protocol_violation"),
        _reason("operation_accepted", "source", "operation_failure"),
        _reason("operation_" + "dispatch_started", "source", "operation_failure"),
        _reason("operation_" + "dispatch_completed", "source", "operation_failure"),
        _reason("operation_side_effect_observed", "source", "operation_failure"),
        _reason("operation_result_persisted", "source", "operation_failure"),
        _reason("operation_main_commit_completed", "source", "operation_failure"),
        _reason("operation_cleanup_completed", "cleanup", "operation_failure"),
        _reason("operation_cleanup_failed", "cleanup", "operation_failure"),
        _reason("source_operation_failed", "source", "operation_failure"),
        _reason("authority_write_rejected", "runtime", "authority_rejected"),
        _reason("storage_transaction_failed", "storage", "operation_failure"),
        _reason("storage_integrity_observed", "storage", "operation_failure"),
        _reason("support_bundle_export_started", "runtime", "operation_failure"),
        _reason("support_bundle_export_completed", "runtime", "operation_failure"),
        _reason("support_bundle_export_failed", "runtime", "operation_failure"),
        _reason("diagnostic_projection_oversize", "runtime", "resource_exhausted"),
        _reason("diagnostic_event_id_conflict", "runtime", "protocol_violation"),
        _reason("diagnostic_gap_detected", "runtime", "operation_failure"),
        _reason("external_trace_rejected", "runtime", "protocol_violation"),
        _reason("sqlite_full", "storage", "resource_exhausted"),
        _reason("sqlite_corrupt", "storage", "operation_failure"),
        _reason("sqlite_readonly", "storage", "operation_failure"),
        _reason("sqlite_cantopen", "storage", "operation_failure"),
        _reason("sqlite_busy", "storage", "resource_exhausted"),
        _reason("browser_control_fence_rejected", "browser", "authority_rejected"),
        _reason("profile_binding_generation_rejected", "browser", "authority_rejected"),
        _reason("browser_scope_mismatch", "browser", "protocol_violation"),
        _reason("provider_auth_required", "provider", "operation_failure"),
        _reason("provider_risk_control", "provider", "operation_failure"),
        _reason("network_offline", "network", "operation_failure"),
        _reason("policy_refused", "policy", "operation_failure"),
        _reason("user_action_required", "user_action", "operation_failure"),
        _reason("unknown_failure", "unknown", "unknown"),
    )
}


@dataclass(frozen=True)
class EventDefinition:
    event_name: str
    components: frozenset[str]
    phases: frozenset[str]
    statuses: frozenset[str]
    reason_codes: frozenset[str]
    requires_operation: bool
    attribute_fields: frozenset[str]


def _event(
    event_name: str,
    *,
    components: set[str],
    phase: str,
    statuses: set[str],
    reasons: set[str],
    requires_operation: bool,
    attributes: set[str],
) -> EventDefinition:
    return EventDefinition(
        event_name=event_name,
        components=frozenset(components),
        phases=frozenset({phase}),
        statuses=frozenset(statuses),
        reason_codes=frozenset(reasons),
        requires_operation=requires_operation,
        attribute_fields=frozenset(attributes),
    )


_ALL_COMPONENTS = set(COMPONENTS)
_OPERATION_ATTRIBUTES = {"operation_kind", "source_id", "safe_count", "coverage"}
EVENT_DEFINITIONS = {
    definition.event_name: definition
    for definition in (
        _event(
            "machine.capability.evaluated",
            components={"main", "installer"},
            phase="capability",
            statuses={"completed", "partial", "failed", "unknown"},
            reasons={
                "machine_capability_supported",
                "machine_capability_unsupported",
                "machine_capability_indeterminate",
            },
            requires_operation=False,
            attributes={"capability", "result", "gap_code"},
        ),
        _event(
            "component.startup.started",
            components=_ALL_COMPONENTS,
            phase="startup",
            statuses={"started"},
            reasons={"component_starting"},
            requires_operation=False,
            attributes={"startup_kind"},
        ),
        _event(
            "component.startup.completed",
            components=_ALL_COMPONENTS,
            phase="startup",
            statuses={"completed"},
            reasons={"component_ready"},
            requires_operation=False,
            attributes={"startup_kind", "readiness"},
        ),
        _event(
            "component.startup.failed",
            components=_ALL_COMPONENTS,
            phase="startup",
            statuses={"failed"},
            reasons={"component_startup_failed"},
            requires_operation=False,
            attributes={"startup_kind", "readiness"},
        ),
        _event(
            "component.readiness.observed",
            components=_ALL_COMPONENTS,
            phase="observe",
            statuses={"completed", "partial", "failed", "unknown"},
            reasons={"component_readiness_observed"},
            requires_operation=False,
            attributes={"readiness", "capability"},
        ),
        _event(
            "operation.accepted",
            components={"main", "controller"},
            phase="accept",
            statuses={"completed"},
            reasons={"operation_accepted"},
            requires_operation=True,
            attributes=_OPERATION_ATTRIBUTES,
        ),
        _event(
            "operation.dispatch.started",
            components={"main", "controller", "sidecar"},
            phase="dispatch",
            statuses={"started"},
            reasons={"operation_" + "dispatch_started"},
            requires_operation=True,
            attributes=_OPERATION_ATTRIBUTES,
        ),
        _event(
            "operation.dispatch.completed",
            components={"main", "controller", "sidecar"},
            phase="dispatch",
            statuses={"completed", "partial", "unknown"},
            reasons={"operation_" + "dispatch_completed"},
            requires_operation=True,
            attributes=_OPERATION_ATTRIBUTES,
        ),
        _event(
            "operation.side_effect.observed",
            components={"sidecar", "worker", "wtscli", "extension", "chrome", "provider"},
            phase="observe",
            statuses={"completed", "partial", "unknown"},
            reasons={"operation_side_effect_observed"},
            requires_operation=True,
            attributes=_OPERATION_ATTRIBUTES,
        ),
        _event(
            "operation.result.persisted",
            components={"main", "sidecar", "sqlite"},
            phase="commit",
            statuses={"completed", "failed"},
            reasons={"operation_result_persisted", "storage_transaction_failed"},
            requires_operation=True,
            attributes=_OPERATION_ATTRIBUTES,
        ),
        _event(
            "operation.main_commit.completed",
            components={"main", "sqlite"},
            phase="commit",
            statuses={"completed"},
            reasons={"operation_main_commit_completed"},
            requires_operation=True,
            attributes=_OPERATION_ATTRIBUTES,
        ),
        _event(
            "operation.cleanup.completed",
            components=_ALL_COMPONENTS,
            phase="cleanup",
            statuses={"completed"},
            reasons={"operation_cleanup_completed"},
            requires_operation=True,
            attributes=_OPERATION_ATTRIBUTES,
        ),
        _event(
            "operation.cleanup.failed",
            components=_ALL_COMPONENTS,
            phase="cleanup",
            statuses={"failed", "partial"},
            reasons={"operation_cleanup_failed"},
            requires_operation=True,
            attributes=_OPERATION_ATTRIBUTES,
        ),
        _event(
            "component.process.exited",
            components=_ALL_COMPONENTS,
            phase="shutdown",
            statuses={"completed", "failed", "unknown"},
            reasons={"component_process_exited"},
            requires_operation=False,
            attributes={"exit_class", "exit_code"},
        ),
        _event(
            "component.protocol.rejected",
            components=_ALL_COMPONENTS,
            phase="execute",
            statuses={"rejected"},
            reasons={"component_protocol_rejected"},
            requires_operation=False,
            attributes={"protocol_ref", "code"},
        ),
        _event(
            "authority.write.rejected",
            components={"main", "controller", "sidecar"},
            phase="commit",
            statuses={"rejected"},
            reasons={
                "authority_write_rejected",
                "browser_control_fence_rejected",
                "profile_binding_generation_rejected",
            },
            requires_operation=True,
            attributes={"authority_kind"},
        ),
        _event(
            "storage.transaction.failed",
            components={"main", "sidecar", "sqlite"},
            phase="commit",
            statuses={"failed"},
            reasons={
                "storage_transaction_failed",
                "sqlite_full",
                "sqlite_corrupt",
                "sqlite_readonly",
                "sqlite_cantopen",
                "sqlite_busy",
            },
            requires_operation=False,
            attributes={"database", "code", "transaction_boundary"},
        ),
        _event(
            "storage.integrity.observed",
            components={"main", "sidecar", "sqlite"},
            phase="observe",
            statuses={"completed", "failed", "unknown"},
            reasons={"storage_integrity_observed", "sqlite_corrupt"},
            requires_operation=False,
            attributes={"database", "result"},
        ),
        _event(
            "support_bundle.export.started",
            components={"main", "exporter"},
            phase="export",
            statuses={"started"},
            reasons={"support_bundle_export_started"},
            requires_operation=False,
            attributes={"projection_version"},
        ),
        _event(
            "support_bundle.export.completed",
            components={"main", "exporter"},
            phase="export",
            statuses={"completed"},
            reasons={"support_bundle_export_completed"},
            requires_operation=False,
            attributes={"projection_version", "artifact_count"},
        ),
        _event(
            "support_bundle.export.failed",
            components={"main", "exporter"},
            phase="export",
            statuses={"failed"},
            reasons={"support_bundle_export_failed"},
            requires_operation=False,
            attributes={"projection_version"},
        ),
    )
}


def require_token(value: str, registry: frozenset[str], kind: str) -> str:
    if value not in registry:
        raise ValueError(f"diagnostics_unknown_{kind}")
    return value


def require_reason_code(value: str) -> str:
    if value not in REASON_DEFINITIONS:
        raise ValueError("diagnostics_unknown_reason_code")
    return value


def require_event_name(value: str) -> str:
    if value not in EVENT_DEFINITIONS:
        raise ValueError("diagnostics_unknown_event_name")
    return value
