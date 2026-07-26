from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from seektalent.diagnostics_registry import (
    COMPONENTS,
    DOMAINS,
    EVENT_DEFINITIONS,
    FAILURE_KINDS,
    PHASES,
    REASON_DEFINITIONS,
    REDACTION_RULES,
)
from seektalent.diagnostics_schema import (
    MAX_ARTIFACT_BYTES,
    MAX_EVENT_BYTES,
    CanonicalEventV1,
    FailureEnvelopeV1,
    JournalAppendAckV1,
    MachineCapabilityReceiptV1,
    OperationEvidenceV1,
    StartupReceiptV1,
    canonical_diagnostics_bytes,
    canonical_diagnostics_hash,
    parse_canonical_event,
    parse_diagnostics_artifact,
    parse_failure_envelope,
    parse_journal_append_ack,
    parse_machine_capability_receipt,
    parse_operation_evidence,
    parse_startup_receipt,
)


TRACE_ID = "1" * 32
SPAN_ID = "2" * 16
EVENT_CANONICAL_HASH = "ad4bcc1b6b8bd7a7e20730533b0056d97f8ca9150b6fbbe67b3c14e2bc7c3987"
EVIDENCE_CONTENT_HASH = "5749067e9928d2731fa81dbf6a39dd6df6059677579df445cf4a2c266100498a"
AT = "2026-07-26T12:00:00Z"
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "diagnostics" / "v1"


def _redaction() -> dict[str, object]:
    return {
        "policy_version": "seektalent.diagnostics-redaction/v1",
        "result": "safe",
        "redacted_field_count": 0,
        "report": [],
    }


def _authority_refs() -> dict[str, object]:
    return {
        "runtime_attempt_fence_ref": "sha256:" + "a" * 64,
        "profile_binding_generation": 1,
        "browser_control_fence_ref": "sha256:" + "b" * 64,
    }


def _event() -> dict[str, object]:
    return {
        "schema_version": "seektalent.canonical-event/v1",
        "event_id": "event-1",
        "journal_seq": 1,
        "correlation_id": "correlation-1",
        "diagnostic_trace_id": TRACE_ID,
        "span_id": SPAN_ID,
        "parent_span_id": None,
        "caused_by_event_id": None,
        "run_id": "run-1",
        "operation_id": "operation-1",
        "attempt_no": 1,
        "component": "main",
        "component_instance_id": "main-instance-1",
        "component_event_seq": 1,
        "release_manifest_ref": "sha256:" + "c" * 64,
        "component_build_ref": "build-main-1",
        "event_name": "operation.accepted",
        "phase": "accept",
        "severity": "info",
        "status": "completed",
        "arrival_class": "on_time",
        "reason_code": "operation_accepted",
        "occurred_at": AT,
        "observed_at": AT,
        "authority_refs": _authority_refs(),
        "correlation_refs": {"browser_control_scope_id": "browser-scope-1"},
        "attributes": {"operation_kind": "verify_session", "source_id": "liepin"},
        "redaction": _redaction(),
    }


def _failure() -> dict[str, object]:
    return {
        "schema_version": "seektalent.failure-envelope/v1",
        "failure_id": "failure-1",
        "revision": 1,
        "correlation_id": "correlation-1",
        "run_id": "run-1",
        "operation_id": "operation-1",
        "attempt_no": 1,
        "diagnostic_trace_id": TRACE_ID,
        "first_failure_event_id": "event-1",
        "last_observed_event_id": "event-1",
        "component": "main",
        "component_instance_id": "main-instance-1",
        "component_build_ref": "build-main-1",
        "phase": "execute",
        "domain": "source",
        "failure_kind": "operation_failure",
        "reason_code": "source_operation_failed",
        "cause_ref": {
            "kind": "event",
            "ref_id": "event-1",
            "code": None,
            "certainty": "observed",
            "derivation_rule_id": None,
        },
        "detail": {"operation_kind": "verify_session"},
        "boundary_facts": {
            "acceptance": {"state": "observed", "ref": "acceptance-1"},
            "dispatch": {"state": "observed", "ref": "dispatch-1"},
            "side_effect": {"state": "unknown", "ref": None},
            "result_persistence": {"state": "not_observed", "ref": None},
            "main_commit": {"state": "not_started", "ref": None},
            "cleanup": {"state": "unknown", "ref": None},
        },
        "last_safe_boundary": "acceptance-1",
        "authority_refs": _authority_refs(),
        "correlation_refs": {"browser_control_scope_id": "browser-scope-1"},
        "diagnostic_gap": None,
        "observed_boundary_ref": None,
        "source_coverage": {
            "source_id": "liepin",
            "state": "partial",
            "safe_count": 0,
        },
        "current_outcome": None,
        "user_action": None,
        "support_action": {
            "code": "contact_support",
            "instruction_key": "support.contact",
        },
        "occurred_at": AT,
        "observed_at": AT,
        "redaction": _redaction(),
    }


def _capability() -> dict[str, object]:
    return {
        "schema_version": "seektalent.machine-capability-receipt/v1",
        "receipt_id": "capability-1",
        "revision": 1,
        "generated_at": AT,
        "release_manifest_ref": "sha256:" + "c" * 64,
        "product_version": "0.7.49",
        "product_build_ref": "build-product-1",
        "install_channel": "candidate",
        "platform": "macos",
        "architecture": "arm64",
        "os_version_bucket": "15.x",
        "runtime_versions": {"python": "3.12", "sqlite": "3.49", "chrome": "stable"},
        "component_build_refs": {"main": "build-main-1", "sidecar": "build-sidecar-1"},
        "capabilities": {"source_port": "supported", "browser_bridge": "supported"},
        "network_posture": {
            "offline": False,
            "system_proxy_present": False,
            "custom_ca_present": False,
            "chrome_managed": False,
        },
        "result": "supported",
        "gap_codes": [],
        "redaction": _redaction(),
    }


def _startup() -> dict[str, object]:
    return {
        "schema_version": "seektalent.startup-receipt/v1",
        "startup_receipt_id": "startup-1",
        "revision": 1,
        "component": "sidecar",
        "component_instance_id": "sidecar-instance-1",
        "parent_instance_id": "main-instance-1",
        "capability_receipt_ref": "capability-1",
        "release_manifest_ref": "sha256:" + "c" * 64,
        "component_build_ref": "build-sidecar-1",
        "protocol_refs": ["source-port-v1"],
        "capability_refs": ["authenticated-framing"],
        "startup_kind": "fresh",
        "started_at": AT,
        "readiness_observed_at": AT,
        "exited_at": None,
        "readiness": "ready",
        "reason_code": "component_ready",
        "restart_count": 0,
        "restart_budget_ref": "restart-budget-1",
        "previous_instance_ref": None,
        "last_exit_cause_ref": None,
        "profile_binding_generation": 1,
        "browser_control_scope_id": None,
        "extension_install_generation": None,
        "service_worker_generation": None,
        "database_refs": ["runtime-control-schema-1"],
        "endpoint_ownership_ref": "endpoint-owner-1",
        "redaction": _redaction(),
    }


def _operation_evidence() -> dict[str, object]:
    return {
        "schema_version": "seektalent.operation-evidence/v1",
        "operation_evidence_id": "evidence-1",
        "revision": 1,
        "canonical_hash": EVIDENCE_CONTENT_HASH,
        "correlation_id": "correlation-1",
        "run_id": "run-1",
        "operation_id": "operation-1",
        "attempt_no": 1,
        "diagnostic_trace_id": TRACE_ID,
        "authority_refs": _authority_refs(),
        "capability_receipt_refs": ["capability-1"],
        "startup_receipt_refs": ["startup-1"],
        "source_id": "liepin",
        "operation_kind": "verify_session",
        "first_event_ref": "event-1",
        "last_event_ref": "event-2",
        "failure_envelope_ref": "failure-1:1",
        "checkpoint_ref": None,
        "boundary_facts": _failure()["boundary_facts"],
        "summary": {"result_count": 0, "coverage": "partial"},
        "source_operation_disposition": None,
        "product_outcome": None,
        "missing_evidence_refs": [],
        "rejected_stale_write_count": 0,
        "journal_truncation": "none",
        "created_at": AT,
        "redaction": _redaction(),
    }


def _ack() -> dict[str, object]:
    return {
        "schema_version": "seektalent.journal-append-ack/v1",
        "event_id": "event-1",
        "journal_seq": 1,
        "canonical_hash": EVENT_CANONICAL_HASH,
        "accepted_at": AT,
    }


ARTIFACTS = (
    (CanonicalEventV1, parse_canonical_event, _event),
    (FailureEnvelopeV1, parse_failure_envelope, _failure),
    (MachineCapabilityReceiptV1, parse_machine_capability_receipt, _capability),
    (StartupReceiptV1, parse_startup_receipt, _startup),
    (OperationEvidenceV1, parse_operation_evidence, _operation_evidence),
    (JournalAppendAckV1, parse_journal_append_ack, _ack),
)


@pytest.mark.parametrize(("model", "parser", "factory"), ARTIFACTS)
def test_each_v1_artifact_has_strict_bytes_only_admission(model, parser, factory) -> None:
    payload = factory()
    raw = json.dumps(payload, separators=(",", ":")).encode()
    parsed = parser(raw)

    assert isinstance(parsed, model)
    assert parse_diagnostics_artifact(raw) == parsed
    assert model.model_validate_json(raw) == parsed

    with pytest.raises(ValueError, match="diagnostics_raw_input_required"):
        parser(raw.decode())
    with pytest.raises(ValueError, match="diagnostics_raw_input_required"):
        model.model_validate_json(raw.decode())


@pytest.mark.parametrize(("_model", "parser", "factory"), ARTIFACTS)
def test_each_v1_artifact_rejects_duplicate_extra_and_unknown_schema(
    _model, parser, factory
) -> None:
    payload = factory()
    raw = json.dumps(payload, separators=(",", ":"))
    duplicate = raw[:-1] + ',"schema_version":"seektalent.unknown/v1"}'
    with pytest.raises(ValueError, match="diagnostics_duplicate_key"):
        parser(duplicate.encode())

    payload["extra"] = "not-allowed"
    with pytest.raises(ValueError, match="diagnostics_schema_validation"):
        parser(json.dumps(payload).encode())


@pytest.mark.parametrize(("model", "parser", "factory"), ARTIFACTS)
def test_each_v1_artifact_rejects_invalid_unknown_and_oversize_input(
    model, parser, factory
) -> None:
    with pytest.raises(ValueError, match="diagnostics_invalid_json"):
        parser(b"{")

    payload = factory()
    payload["schema_version"] = "seektalent.unknown/v1"
    with pytest.raises(ValueError, match="diagnostics_schema_validation"):
        parser(json.dumps(payload).encode())

    raw = json.dumps(factory(), separators=(",", ":")).encode()
    limit = (
        MAX_EVENT_BYTES
        if model in {CanonicalEventV1, JournalAppendAckV1}
        else MAX_ARTIFACT_BYTES
    )
    oversize = raw + b" " * (limit + 1 - len(raw))
    with pytest.raises(ValueError, match="diagnostics_payload_too_large"):
        parser(oversize)


def test_generic_parser_rejects_unknown_schema() -> None:
    with pytest.raises(ValueError, match="diagnostics_unknown_schema"):
        parse_diagnostics_artifact(b'{"schema_version":"seektalent.unknown/v1"}')


def test_artifact_admission_rejects_illegal_json_unicode_numbers_and_size() -> None:
    with pytest.raises(ValueError, match="diagnostics_illegal_number"):
        parse_canonical_event(json.dumps(_event()).replace('"journal_seq": 1', '"journal_seq": NaN').encode())
    with pytest.raises(ValueError, match="diagnostics_invalid_unicode"):
        parse_canonical_event(
            json.dumps(_event()).replace('"event-1"', '"\\ud800"', 1).encode()
        )

    payload = _event()
    payload["attributes"] = {"safe_summary": "x" * 17_000}
    with pytest.raises(ValueError, match="diagnostics_payload_too_large"):
        parse_canonical_event(json.dumps(payload).encode())


def test_nested_models_hide_input_and_forbid_extra_fields() -> None:
    canary = "Bearer super-secret-value-that-must-never-leak"
    payload = _failure()
    assert isinstance(payload["cause_ref"], dict)
    payload["cause_ref"]["extra"] = canary

    with pytest.raises(ValueError) as exc_info:
        parse_failure_envelope(json.dumps(payload).encode())
    assert canary not in str(exc_info.value)
    assert canary not in repr(exc_info.value)


def test_failure_envelope_has_no_retry_permission_surface() -> None:
    fields = set(FailureEnvelopeV1.model_fields)
    forbidden = {
        "retryable",
        "safe_to_retry",
        "retry_posture",
        "retry_decision",
        "retry_permission",
        "retry_after",
    }
    assert fields.isdisjoint(forbidden)
    assert not any("retry" in name.lower() for name in dir(FailureEnvelopeV1))

    payload = _failure()
    for name in forbidden:
        mutated = dict(payload)
        mutated[name] = False
        with pytest.raises(ValueError, match="diagnostics_schema_validation"):
            parse_failure_envelope(json.dumps(mutated).encode())


def test_identity_and_authority_fields_are_distinct_and_opaque_refs_are_closed() -> None:
    event = CanonicalEventV1.model_validate(_event())
    assert event.run_id != event.operation_id
    assert event.attempt_no == 1
    assert event.correlation_id == "correlation-1"
    assert event.authority_refs.runtime_attempt_fence_ref != event.correlation_id

    for value in (
        "https://example.com/path?token=secret",
        "/Users/private/workspace",
        r"C:\Users\private\workspace",
        "../relative/escape",
        "Bearer secret",
    ):
        payload = _event()
        payload["event_id"] = value
        with pytest.raises(ValidationError) as exc_info:
            CanonicalEventV1.model_validate(payload)
        assert value not in str(exc_info.value)
        assert value not in repr(exc_info.value)


def test_registry_is_bounded_and_exhaustive() -> None:
    assert COMPONENTS == frozenset(
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
    assert PHASES
    assert DOMAINS
    assert FAILURE_KINDS
    assert REDACTION_RULES
    assert EVENT_DEFINITIONS
    assert REASON_DEFINITIONS
    assert {definition.reason_code for definition in REASON_DEFINITIONS.values()} == set(
        REASON_DEFINITIONS
    )
    for event_name, definition in EVENT_DEFINITIONS.items():
        assert event_name == definition.event_name
        assert definition.components <= COMPONENTS
        assert definition.phases <= PHASES
        assert definition.reason_codes <= set(REASON_DEFINITIONS)
    for definition in REASON_DEFINITIONS.values():
        assert definition.domain in DOMAINS
        assert definition.failure_kind in FAILURE_KINDS


def test_unknown_registry_tokens_fail_closed() -> None:
    for field, value in (
        ("component", "unknown-component"),
        ("phase", "unknown-phase"),
        ("event_name", "unknown.event"),
        ("reason_code", "generic_unavailable"),
    ):
        payload = _event()
        payload[field] = value
        with pytest.raises(ValidationError):
            CanonicalEventV1.model_validate(payload)

    payload = _failure()
    payload["domain"] = "unknown-domain"
    with pytest.raises(ValidationError):
        FailureEnvelopeV1.model_validate(payload)


def test_canonical_known_answer_and_determinism() -> None:
    event = CanonicalEventV1.model_validate(_event())
    expected = json.dumps(
        event.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    assert canonical_diagnostics_bytes(event) == expected
    assert canonical_diagnostics_hash(event) == sha256(expected).hexdigest()
    assert canonical_diagnostics_bytes(event) == canonical_diagnostics_bytes(
        parse_canonical_event(json.dumps(_event(), sort_keys=False).encode())
    )
    assert canonical_diagnostics_hash(event) == canonical_diagnostics_hash(event)


def test_operation_evidence_embedded_hash_covers_exact_body() -> None:
    evidence = OperationEvidenceV1.model_validate(_operation_evidence())
    assert evidence.canonical_hash == EVIDENCE_CONTENT_HASH

    payload = _operation_evidence()
    payload["summary"] = {"coverage": "completed", "result_count": 0}
    with pytest.raises(ValidationError, match="diagnostics_operation_evidence_hash_mismatch"):
        OperationEvidenceV1.model_validate(payload)


def test_checked_in_golden_fixtures_match_known_answer_vectors() -> None:
    vectors = json.loads(FIXTURE_ROOT.joinpath("known-answer-sha256.json").read_bytes())

    assert set(vectors) == {
        "canonical-event.json",
        "failure-envelope.json",
        "journal-append-ack.json",
        "machine-capability-receipt.json",
        "operation-evidence.json",
        "startup-receipt.json",
    }
    for filename, expected_hash in vectors.items():
        raw = FIXTURE_ROOT.joinpath(filename).read_bytes()
        artifact = parse_diagnostics_artifact(raw)
        assert raw.rstrip(b"\n") == canonical_diagnostics_bytes(artifact)
        assert expected_hash == canonical_diagnostics_hash(artifact)
        assert expected_hash == sha256(raw.rstrip(b"\n")).hexdigest()


def test_platform_absolute_path_forms_are_equally_rejected() -> None:
    messages = []
    for path in ("/Users/private/workspace", r"C:\Users\private\workspace"):
        payload = _event()
        payload["component_build_ref"] = path
        with pytest.raises(ValidationError) as exc_info:
            CanonicalEventV1.model_validate(payload)
        messages.append(str(exc_info.value))
        assert path not in str(exc_info.value)
        assert path not in repr(exc_info.value)
    assert messages[0] == messages[1]


def test_valid_artifacts_are_frozen_and_safe_to_repr() -> None:
    for model, _parser, factory in ARTIFACTS:
        artifact = model.model_validate(factory())
        with pytest.raises(ValidationError):
            artifact.schema_version = "changed"
        assert "Bearer " not in repr(artifact)
