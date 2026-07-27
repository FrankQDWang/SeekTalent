"""Closed v1 token registry for privacy-safe diagnostics artifacts."""

from __future__ import annotations

from dataclasses import dataclass

from seektalent.diagnostics_scalar import (
    NON_NEGATIVE_INTEGER,
    SHA256_REFERENCE,
    ScalarContract,
    enum_values,
)
from seektalent.user_action import USER_ACTION_INSTRUCTIONS as USER_ACTION_INSTRUCTIONS


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
AUTHORITY_REF_FIELDS = frozenset(
    {"runtime_attempt_fence_ref", "profile_binding_generation", "browser_control_fence_ref"}
)
CAUSE_CODES = frozenset(
    {
        "sqlite_full",
        "sqlite_corrupt",
        "sqlite_readonly",
        "sqlite_cantopen",
        "sqlite_busy",
        "os_access_denied",
        "os_resource_exhausted",
        "http_401",
        "http_403",
        "http_429",
        "http_5xx",
        "chrome_not_reachable",
        "chrome_protocol_rejected",
        "producer_contract_rejected",
        "producer_process_exited",
    }
)
EXTERNAL_CAUSE_REASONS = {
    "sqlite_full": frozenset({"sqlite_full"}),
    "sqlite_corrupt": frozenset({"sqlite_corrupt"}),
    "sqlite_readonly": frozenset({"sqlite_readonly"}),
    "sqlite_cantopen": frozenset({"sqlite_cantopen"}),
    "sqlite_busy": frozenset({"sqlite_busy"}),
    "os_access_denied": frozenset({"component_startup_failed"}),
    "os_resource_exhausted": frozenset({"component_startup_failed"}),
    "http_401": frozenset({"provider_auth_required"}),
    "http_403": frozenset({"provider_risk_control"}),
    "http_429": frozenset({"provider_risk_control"}),
    "http_5xx": frozenset({"provider_http_unavailable"}),
    "chrome_not_reachable": frozenset(
        {"browser_unreachable", "browser_connection_lost"}
    ),
    "chrome_protocol_rejected": frozenset({"browser_protocol_rejected"}),
    "producer_contract_rejected": frozenset({"component_protocol_rejected"}),
    "producer_process_exited": frozenset(
        {"component_process_exited", "browser_process_exited"}
    ),
}
if set(EXTERNAL_CAUSE_REASONS) != set(CAUSE_CODES):
    raise RuntimeError("diagnostics_external_cause_registry_incomplete")

DIAGNOSTIC_GAP_REASONS = frozenset({"diagnostic_gap_detected", "external_trace_rejected"})
SUPPORT_ACTION_INSTRUCTIONS = {
    "contact_support": "support.contact",
    "collect_diagnostics": "support.collect_diagnostics",
}
CAUSE_KIND_SHAPES = {
    "event": (True, False, frozenset({"observed", "derived"})),
    "failure": (True, False, frozenset({"observed", "derived"})),
    "durable_fact": (True, False, frozenset({"observed", "derived"})),
    "external_code": (False, True, frozenset({"observed", "derived"})),
    "unknown": (False, False, frozenset({"unknown"})),
}


@dataclass(frozen=True)
class ReasonDefinition:
    reason_code: str
    domain: str
    failure_kind: str
    artifacts: frozenset[str]
    event_statuses: frozenset[str] = frozenset()
    failure_components: frozenset[str] = frozenset()
    failure_phases: frozenset[str] = frozenset()


def _reason(reason_code: str, domain: str, failure_kind: str) -> ReasonDefinition:
    return ReasonDefinition(reason_code, domain, failure_kind, frozenset({"event"}))


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
        _reason("operation_dispatch_started", "source", "operation_failure"),
        _reason("operation_dispatch_completed", "source", "operation_failure"),
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
        _reason("provider_http_unavailable", "provider", "operation_failure"),
        _reason("browser_unreachable", "browser", "startup_failure"),
        _reason("browser_connection_lost", "browser", "operation_failure"),
        _reason("browser_process_exited", "browser", "process_exit"),
        _reason("browser_protocol_rejected", "browser", "protocol_violation"),
        _reason("network_offline", "network", "operation_failure"),
        _reason("policy_refused", "policy", "operation_failure"),
        _reason("user_action_required", "user_action", "operation_failure"),
        _reason("unknown_failure", "unknown", "unknown"),
    )
}

_FAILURE_ONLY_REASONS = frozenset(
    {
        "browser_scope_mismatch",
        "diagnostic_event_id_conflict",
        "diagnostic_gap_detected",
        "diagnostic_projection_oversize",
        "external_trace_rejected",
        "network_offline",
        "policy_refused",
        "provider_auth_required",
        "provider_http_unavailable",
        "provider_risk_control",
        "source_operation_failed",
        "unknown_failure",
        "user_action_required",
    }
)
_REASON_EVENT_STATUSES = {
    "machine_capability_supported": {"completed"},
    "machine_capability_unsupported": {"partial", "failed"},
    "machine_capability_indeterminate": {"partial", "unknown"},
    "component_starting": {"started"},
    "component_ready": {"completed"},
    "component_startup_failed": {"failed"},
    "component_readiness_observed": {"completed", "partial", "failed", "unknown"},
    "component_process_exited": {"completed", "failed", "unknown"},
    "component_protocol_rejected": {"rejected"},
    "operation_accepted": {"completed"},
    "operation_dispatch_started": {"started"},
    "operation_dispatch_completed": {"completed", "partial", "unknown"},
    "operation_side_effect_observed": {"completed", "partial", "unknown"},
    "operation_result_persisted": {"completed"},
    "operation_main_commit_completed": {"completed"},
    "operation_cleanup_completed": {"completed"},
    "operation_cleanup_failed": {"failed", "partial"},
    "source_operation_failed": set(),
    "authority_write_rejected": {"rejected"},
    "storage_transaction_failed": {"failed"},
    "storage_integrity_observed": {"completed", "unknown"},
    "support_bundle_export_started": {"started"},
    "support_bundle_export_completed": {"completed"},
    "support_bundle_export_failed": {"failed"},
    "diagnostic_projection_oversize": set(),
    "diagnostic_event_id_conflict": set(),
    "diagnostic_gap_detected": set(),
    "external_trace_rejected": set(),
    "sqlite_full": {"failed"},
    "sqlite_corrupt": {"failed"},
    "sqlite_readonly": {"failed"},
    "sqlite_cantopen": {"failed"},
    "sqlite_busy": {"failed"},
    "browser_control_fence_rejected": {"rejected"},
    "profile_binding_generation_rejected": {"rejected"},
    "browser_scope_mismatch": set(),
    "provider_auth_required": set(),
    "provider_risk_control": set(),
    "provider_http_unavailable": set(),
    "browser_unreachable": {"failed"},
    "browser_connection_lost": {"failed", "unknown"},
    "browser_process_exited": {"failed", "unknown"},
    "browser_protocol_rejected": {"rejected"},
    "network_offline": set(),
    "policy_refused": set(),
    "user_action_required": set(),
    "unknown_failure": set(),
}
if set(_REASON_EVENT_STATUSES) != set(REASON_DEFINITIONS):
    raise RuntimeError("diagnostics_reason_status_registry_incomplete")
REASON_DEFINITIONS = {
    code: ReasonDefinition(
        definition.reason_code,
        definition.domain,
        definition.failure_kind,
        (
            definition.artifacts
            if code not in _FAILURE_ONLY_REASONS
            else definition.artifacts - {"event"}
        ),
        frozenset(_REASON_EVENT_STATUSES[code]),
    )
    for code, definition in REASON_DEFINITIONS.items()
}

_FAILURE_REASONS = frozenset(
    {
        "machine_capability_unsupported",
        "machine_capability_indeterminate",
        "component_startup_failed",
        "component_process_exited",
        "component_protocol_rejected",
        "operation_cleanup_failed",
        "source_operation_failed",
        "authority_write_rejected",
        "storage_transaction_failed",
        "support_bundle_export_failed",
        "diagnostic_projection_oversize",
        "diagnostic_event_id_conflict",
        "diagnostic_gap_detected",
        "external_trace_rejected",
        "sqlite_full",
        "sqlite_corrupt",
        "sqlite_readonly",
        "sqlite_cantopen",
        "sqlite_busy",
        "browser_control_fence_rejected",
        "profile_binding_generation_rejected",
        "browser_scope_mismatch",
        "provider_auth_required",
        "provider_risk_control",
        "provider_http_unavailable",
        "browser_unreachable",
        "browser_connection_lost",
        "browser_process_exited",
        "browser_protocol_rejected",
        "network_offline",
        "policy_refused",
        "user_action_required",
        "unknown_failure",
    }
)
_RUNTIME_COMPONENTS = frozenset(
    {"main", "controller", "sidecar", "worker", "wtscli", "extension"}
)
_BROWSER_COMPONENTS = frozenset({"wtscli", "extension", "chrome"})
_FAILURE_CONTEXTS = {
    "machine_capability_unsupported": (
        frozenset({"main", "installer"}),
        frozenset({"capability"}),
    ),
    "machine_capability_indeterminate": (
        frozenset({"main", "installer"}),
        frozenset({"capability"}),
    ),
    "component_startup_failed": (_RUNTIME_COMPONENTS, frozenset({"startup"})),
    "browser_unreachable": (
        _BROWSER_COMPONENTS,
        frozenset({"startup"}),
    ),
    "browser_connection_lost": (
        _BROWSER_COMPONENTS,
        frozenset({"execute", "observe"}),
    ),
    "browser_process_exited": (
        _BROWSER_COMPONENTS,
        frozenset({"shutdown"}),
    ),
    "component_process_exited": (_RUNTIME_COMPONENTS, frozenset({"shutdown"})),
    "component_protocol_rejected": (_RUNTIME_COMPONENTS, frozenset({"execute"})),
    "browser_protocol_rejected": (
        _BROWSER_COMPONENTS,
        frozenset({"execute"}),
    ),
    "operation_cleanup_failed": (
        frozenset(
            {
                "main",
                "controller",
                "sidecar",
                "worker",
                "wtscli",
                "extension",
                "chrome",
                "provider",
                "sqlite",
            }
        ),
        frozenset({"cleanup"}),
    ),
    "source_operation_failed": (
        frozenset({"main", "controller", "sidecar", "worker", "wtscli", "provider"}),
        frozenset({"execute", "observe", "commit"}),
    ),
    "authority_write_rejected": (
        frozenset({"main", "controller", "sidecar"}),
        frozenset({"commit"}),
    ),
    "storage_transaction_failed": (
        frozenset({"main", "sidecar", "sqlite"}),
        frozenset({"commit"}),
    ),
    "support_bundle_export_failed": (
        frozenset({"main", "exporter"}),
        frozenset({"export"}),
    ),
    "diagnostic_projection_oversize": (
        frozenset(COMPONENTS),
        frozenset({"observe", "export"}),
    ),
    "diagnostic_event_id_conflict": (
        frozenset({"main"}),
        frozenset({"commit"}),
    ),
    "diagnostic_gap_detected": (
        frozenset({"main"}),
        frozenset({"observe"}),
    ),
    "external_trace_rejected": (
        frozenset({"main", "controller", "sidecar"}),
        frozenset({"accept"}),
    ),
    **{
        reason_code: (
            frozenset({"main", "sidecar", "sqlite"}),
            frozenset({"commit"}),
        )
        for reason_code in {
            "sqlite_full",
            "sqlite_corrupt",
            "sqlite_readonly",
            "sqlite_cantopen",
            "sqlite_busy",
        }
    },
    "sqlite_corrupt": (
        frozenset({"main", "sidecar", "sqlite"}),
        frozenset({"commit", "observe"}),
    ),
    "browser_control_fence_rejected": (
        frozenset({"main", "controller", "sidecar", "wtscli", "extension", "chrome"}),
        frozenset({"execute", "commit"}),
    ),
    "profile_binding_generation_rejected": (
        frozenset({"main", "controller", "sidecar", "wtscli", "extension", "chrome"}),
        frozenset({"execute", "commit"}),
    ),
    "browser_scope_mismatch": (
        frozenset({"wtscli", "extension", "chrome"}),
        frozenset({"execute"}),
    ),
    "provider_auth_required": (
        frozenset({"sidecar", "wtscli", "provider"}),
        frozenset({"execute", "observe"}),
    ),
    "provider_risk_control": (
        frozenset({"sidecar", "wtscli", "provider"}),
        frozenset({"execute", "observe"}),
    ),
    "provider_http_unavailable": (
        frozenset({"sidecar", "wtscli", "provider"}),
        frozenset({"execute", "observe"}),
    ),
    "network_offline": (
        frozenset({"main", "sidecar", "provider"}),
        frozenset({"capability", "execute"}),
    ),
    "policy_refused": (
        frozenset({"main", "controller"}),
        frozenset({"accept"}),
    ),
    "user_action_required": (
        frozenset({"main", "provider"}),
        frozenset({"observe"}),
    ),
    "unknown_failure": (frozenset(COMPONENTS), frozenset(PHASES)),
}
if set(_FAILURE_CONTEXTS) != _FAILURE_REASONS:
    raise RuntimeError("diagnostics_failure_context_registry_incomplete")

REASON_DEFINITIONS = {
    code: ReasonDefinition(
        definition.reason_code,
        definition.domain,
        definition.failure_kind,
        definition.artifacts | (frozenset({"failure"}) if code in _FAILURE_REASONS else frozenset()),
        definition.event_statuses,
        *(_FAILURE_CONTEXTS[code] if code in _FAILURE_REASONS else (frozenset(), frozenset())),
    )
    for code, definition in REASON_DEFINITIONS.items()
}


@dataclass(frozen=True)
class EventDefinition:
    event_name: str
    components: frozenset[str]
    phases: frozenset[str]
    statuses: frozenset[str]
    reason_codes: frozenset[str]
    requires_operation: bool
    attribute_contracts: dict[str, ScalarContract]
    required_attribute_fields: frozenset[str]
    authority_ref_fields: frozenset[str]

    @property
    def attribute_fields(self) -> frozenset[str]:
        return frozenset(self.attribute_contracts)


_ATTRIBUTE_CONTRACTS = {
    "operation_kind": enum_values(
        "verify_session", "search", "cards", "details", "continuation", "cleanup"
    ),
    "source_id": enum_values("liepin"),
    "safe_count": NON_NEGATIVE_INTEGER,
    "coverage": enum_values("started", "completed", "partial", "unknown"),
    "capability": enum_values(
        "source_port",
        "browser_bridge",
        "endpoint_ownership",
        "database_integrity",
        "disk_access",
        "network_posture",
        "release_integrity",
    ),
    "result": enum_values(
        "supported", "unsupported", "indeterminate", "ok", "failed", "unknown"
    ),
    "gap_code": enum_values(
        "browser_bridge_unsupported",
        "source_port_unsupported",
        "endpoint_owner_conflict",
        "endpoint_owner_unknown",
        "database_integrity_failed",
        "database_integrity_unknown",
        "disk_not_writable",
        "disk_not_executable",
        "network_offline",
        "manifest_signature_failed",
        "artifact_signature_failed",
        "signature_verification_missing",
    ),
    "startup_kind": enum_values("fresh", "restart", "upgrade_rebind", "wake"),
    "readiness": enum_values("ready", "not_ready"),
    "exit_class": enum_values("clean", "failure", "signal", "unknown"),
    "exit_code": NON_NEGATIVE_INTEGER,
    "protocol_ref": SHA256_REFERENCE,
    "code": enum_values(*CAUSE_CODES),
    "authority_kind": enum_values(
        "runtime_attempt_fence",
        "profile_binding_generation",
        "browser_control_fence",
    ),
    "database": enum_values("runtime_control", "source_port"),
    "transaction_boundary": enum_values("begin", "write", "commit", "rollback"),
    "projection_version": enum_values("seektalent.diagnostics-redaction/v1"),
    "artifact_count": NON_NEGATIVE_INTEGER,
}

@dataclass(frozen=True)
class FailureDetailDefinition:
    contracts: dict[str, ScalarContract]
    required_fields: frozenset[str]


FAILURE_DETAIL_CONTRACTS = {
    reason_code: FailureDetailDefinition({}, frozenset())
    for reason_code, definition in REASON_DEFINITIONS.items()
    if "failure" in definition.artifacts
}
FAILURE_DETAIL_CONTRACTS.update(
    {
        "source_operation_failed": FailureDetailDefinition(
            {
                name: _ATTRIBUTE_CONTRACTS[name]
                for name in {"operation_kind", "source_id", "safe_count", "coverage"}
            },
            frozenset({"operation_kind", "source_id"}),
        ),
        **{
            reason_code: FailureDetailDefinition(
                {
                    "database": _ATTRIBUTE_CONTRACTS["database"],
                    "code": _ATTRIBUTE_CONTRACTS["code"],
                    "transaction_boundary": _ATTRIBUTE_CONTRACTS["transaction_boundary"],
                },
                frozenset({"database", "code", "transaction_boundary"}),
            )
            for reason_code in {
                "sqlite_full",
                "sqlite_corrupt",
                "sqlite_readonly",
                "sqlite_cantopen",
                "sqlite_busy",
                "storage_transaction_failed",
            }
        },
    }
)
if set(FAILURE_DETAIL_CONTRACTS) != _FAILURE_REASONS:
    raise RuntimeError("diagnostics_failure_detail_registry_incomplete")


def _event(
    event_name: str,
    *,
    components: set[str],
    phase: str,
    statuses: set[str],
    reasons: set[str],
    requires_operation: bool,
    attributes: set[str],
    required_attributes: set[str],
) -> EventDefinition:
    return EventDefinition(
        event_name=event_name,
        components=frozenset(components),
        phases=frozenset({phase}),
        statuses=frozenset(statuses),
        reason_codes=frozenset(reasons),
        requires_operation=requires_operation,
        attribute_contracts={name: _ATTRIBUTE_CONTRACTS[name] for name in attributes},
        required_attribute_fields=frozenset(required_attributes),
        authority_ref_fields=AUTHORITY_REF_FIELDS if requires_operation else frozenset(),
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
            required_attributes={"capability", "result"},
        ),
        _event(
            "component.startup.started",
            components=_ALL_COMPONENTS,
            phase="startup",
            statuses={"started"},
            reasons={"component_starting"},
            requires_operation=False,
            attributes={"startup_kind"},
            required_attributes={"startup_kind"},
        ),
        _event(
            "component.startup.completed",
            components=_ALL_COMPONENTS,
            phase="startup",
            statuses={"completed"},
            reasons={"component_ready"},
            requires_operation=False,
            attributes={"startup_kind", "readiness"},
            required_attributes={"startup_kind", "readiness"},
        ),
        _event(
            "component.startup.failed",
            components=set(_RUNTIME_COMPONENTS | _BROWSER_COMPONENTS),
            phase="startup",
            statuses={"failed"},
            reasons={"component_startup_failed", "browser_unreachable"},
            requires_operation=False,
            attributes={"startup_kind", "readiness"},
            required_attributes={"startup_kind", "readiness"},
        ),
        _event(
            "component.readiness.observed",
            components=_ALL_COMPONENTS,
            phase="observe",
            statuses={"completed", "partial", "failed", "unknown"},
            reasons={"component_readiness_observed"},
            requires_operation=False,
            attributes={"readiness", "capability"},
            required_attributes={"readiness", "capability"},
        ),
        _event(
            "operation.accepted",
            components={"main", "controller"},
            phase="accept",
            statuses={"completed"},
            reasons={"operation_accepted"},
            requires_operation=True,
            attributes=_OPERATION_ATTRIBUTES,
            required_attributes={"operation_kind", "source_id"},
        ),
        _event(
            "operation.dispatch.started",
            components={"main", "controller", "sidecar"},
            phase="dispatch",
            statuses={"started"},
            reasons={"operation_dispatch_started"},
            requires_operation=True,
            attributes=_OPERATION_ATTRIBUTES,
            required_attributes={"operation_kind", "source_id"},
        ),
        _event(
            "operation.dispatch.completed",
            components={"main", "controller", "sidecar"},
            phase="dispatch",
            statuses={"completed", "partial", "unknown"},
            reasons={"operation_dispatch_completed"},
            requires_operation=True,
            attributes=_OPERATION_ATTRIBUTES,
            required_attributes={"operation_kind", "source_id"},
        ),
        _event(
            "operation.side_effect.observed",
            components={"sidecar", "worker", "wtscli", "extension", "chrome", "provider"},
            phase="observe",
            statuses={"completed", "partial", "unknown"},
            reasons={"operation_side_effect_observed"},
            requires_operation=True,
            attributes=_OPERATION_ATTRIBUTES,
            required_attributes={"operation_kind", "source_id"},
        ),
        _event(
            "operation.result.persisted",
            components={"main", "sidecar", "sqlite"},
            phase="commit",
            statuses={"completed", "failed"},
            reasons={"operation_result_persisted", "storage_transaction_failed"},
            requires_operation=True,
            attributes=_OPERATION_ATTRIBUTES,
            required_attributes={"operation_kind", "source_id"},
        ),
        _event(
            "operation.main_commit.completed",
            components={"main", "sqlite"},
            phase="commit",
            statuses={"completed"},
            reasons={"operation_main_commit_completed"},
            requires_operation=True,
            attributes=_OPERATION_ATTRIBUTES,
            required_attributes={"operation_kind", "source_id"},
        ),
        _event(
            "operation.cleanup.completed",
            components=_ALL_COMPONENTS,
            phase="cleanup",
            statuses={"completed"},
            reasons={"operation_cleanup_completed"},
            requires_operation=True,
            attributes=_OPERATION_ATTRIBUTES,
            required_attributes={"operation_kind", "source_id"},
        ),
        _event(
            "operation.cleanup.failed",
            components={
                "main",
                "controller",
                "sidecar",
                "worker",
                "wtscli",
                "extension",
                "chrome",
                "provider",
                "sqlite",
            },
            phase="cleanup",
            statuses={"failed", "partial"},
            reasons={"operation_cleanup_failed"},
            requires_operation=True,
            attributes=_OPERATION_ATTRIBUTES,
            required_attributes={"operation_kind", "source_id"},
        ),
        _event(
            "component.process.exited",
            components=set(_RUNTIME_COMPONENTS | _BROWSER_COMPONENTS),
            phase="shutdown",
            statuses={"completed", "failed", "unknown"},
            reasons={"component_process_exited", "browser_process_exited"},
            requires_operation=False,
            attributes={"exit_class", "exit_code"},
            required_attributes={"exit_class"},
        ),
        _event(
            "component.protocol.rejected",
            components=set(_RUNTIME_COMPONENTS | _BROWSER_COMPONENTS),
            phase="execute",
            statuses={"rejected"},
            reasons={"component_protocol_rejected", "browser_protocol_rejected"},
            requires_operation=False,
            attributes={"protocol_ref", "code"},
            required_attributes={"protocol_ref", "code"},
        ),
        _event(
            "browser.connection.lost",
            components=set(_BROWSER_COMPONENTS),
            phase="observe",
            statuses={"failed", "unknown"},
            reasons={"browser_connection_lost"},
            requires_operation=True,
            attributes=_OPERATION_ATTRIBUTES,
            required_attributes={"operation_kind", "source_id"},
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
            required_attributes={"authority_kind"},
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
            required_attributes={"database", "code", "transaction_boundary"},
        ),
        _event(
            "storage.integrity.observed",
            components={"main", "sidecar", "sqlite"},
            phase="observe",
            statuses={"completed", "failed", "unknown"},
            reasons={"storage_integrity_observed", "sqlite_corrupt"},
            requires_operation=False,
            attributes={"database", "result"},
            required_attributes={"database", "result"},
        ),
        _event(
            "support_bundle.export.started",
            components={"main", "exporter"},
            phase="export",
            statuses={"started"},
            reasons={"support_bundle_export_started"},
            requires_operation=False,
            attributes={"projection_version"},
            required_attributes={"projection_version"},
        ),
        _event(
            "support_bundle.export.completed",
            components={"main", "exporter"},
            phase="export",
            statuses={"completed"},
            reasons={"support_bundle_export_completed"},
            requires_operation=False,
            attributes={"projection_version", "artifact_count"},
            required_attributes={"projection_version", "artifact_count"},
        ),
        _event(
            "support_bundle.export.failed",
            components={"main", "exporter"},
            phase="export",
            statuses={"failed"},
            reasons={"support_bundle_export_failed"},
            requires_operation=False,
            attributes={"projection_version"},
            required_attributes={"projection_version"},
        ),
    )
}

_CONSUMED_EVENT_REASONS = frozenset(
    reason_code
    for definition in EVENT_DEFINITIONS.values()
    for reason_code in definition.reason_codes
)
_REGISTERED_EVENT_REASONS = frozenset(
    reason_code
    for reason_code, definition in REASON_DEFINITIONS.items()
    if "event" in definition.artifacts
)
if _CONSUMED_EVENT_REASONS != _REGISTERED_EVENT_REASONS:
    raise RuntimeError("diagnostics_event_reason_registry_incomplete")

for event_definition in EVENT_DEFINITIONS.values():
    if any(
        "event" not in REASON_DEFINITIONS[reason_code].artifacts
        for reason_code in event_definition.reason_codes
    ):
        raise RuntimeError("diagnostics_event_reason_artifact_mismatch")
    for component in event_definition.components:
        for phase in event_definition.phases:
            for status in event_definition.statuses & {"failed", "rejected", "unknown"}:
                if not any(
                    status in REASON_DEFINITIONS[reason_code].event_statuses
                    and (
                        "failure" not in REASON_DEFINITIONS[reason_code].artifacts
                        or (
                            component
                            in REASON_DEFINITIONS[reason_code].failure_components
                            and phase in REASON_DEFINITIONS[reason_code].failure_phases
                        )
                    )
                    for reason_code in event_definition.reason_codes
                ):
                    raise RuntimeError("diagnostics_event_context_registry_incomplete")


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
