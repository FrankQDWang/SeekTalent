from __future__ import annotations

import json
import ast
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
    MACHINE_CAPABILITY_FIELD_COVERAGE,
    MachineCapabilityReceiptV1,
    OPERATION_EVIDENCE_FIELD_COVERAGE,
    OperationEvidenceV1,
    STARTUP_RECEIPT_FIELD_COVERAGE,
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
AT = "2026-07-26T12:00:00Z"
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "diagnostics" / "v1"


def _id(char: str) -> str:
    return char * 32


def _sha(char: str) -> str:
    return "sha256:" + char * 64


def _embedded_hash(payload: dict[str, object]) -> str:
    body = dict(payload)
    body.pop("canonical_hash", None)
    raw = json.dumps(
        body,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return sha256(raw).hexdigest()


def _redaction() -> dict[str, object]:
    return {
        "policy_version": "seektalent.diagnostics-redaction/v1",
        "result": "safe",
        "redacted_field_count": 0,
        "report": [],
    }


def _authority_refs() -> dict[str, object]:
    return {
        "runtime_attempt_fence_ref": _sha("a"),
        "profile_binding_generation": 1,
        "browser_control_fence_ref": _sha("b"),
    }


def _event() -> dict[str, object]:
    return {
        "schema_version": "seektalent.canonical-event/v1",
        "event_id": _id("1"),
        "journal_seq": 1,
        "correlation_id": _id("2"),
        "diagnostic_trace_id": TRACE_ID,
        "span_id": SPAN_ID,
        "parent_span_id": None,
        "caused_by_event_id": None,
        "run_id": _id("3"),
        "operation_id": _id("4"),
        "attempt_no": 1,
        "component": "main",
        "component_instance_id": _id("5"),
        "component_event_seq": 1,
        "release_manifest_ref": _sha("c"),
        "component_build_ref": _sha("d"),
        "event_name": "operation.accepted",
        "phase": "accept",
        "severity": "info",
        "status": "completed",
        "arrival_class": "on_time",
        "reason_code": "operation_accepted",
        "occurred_at": AT,
        "observed_at": AT,
        "authority_refs": _authority_refs(),
        "correlation_refs": {"browser_control_scope_id": _id("6"), "sidecar_command_ref": None},
        "attributes": {"operation_kind": "verify_session", "source_id": "liepin"},
        "redaction": _redaction(),
    }


def _failure() -> dict[str, object]:
    return {
        "schema_version": "seektalent.failure-envelope/v1",
        "failure_id": _id("7"),
        "revision": 1,
        "correlation_id": _id("2"),
        "run_id": _id("3"),
        "operation_id": _id("4"),
        "attempt_no": 1,
        "diagnostic_trace_id": TRACE_ID,
        "first_failure_event_id": _id("1"),
        "last_observed_event_id": _id("1"),
        "component": "main",
        "component_instance_id": _id("5"),
        "component_build_ref": _sha("d"),
        "phase": "execute",
        "domain": "source",
        "failure_kind": "operation_failure",
        "reason_code": "source_operation_failed",
        "cause_ref": {
            "kind": "event",
            "ref_id": _id("1"),
            "code": None,
            "certainty": "observed",
            "derivation_rule_id": None,
        },
        "detail": {"operation_kind": "verify_session"},
        "boundary_facts": {
            "acceptance": {"state": "observed", "ref": _id("8")},
            "dispatch": {"state": "observed", "ref": _id("9")},
            "side_effect": {"state": "unknown", "ref": None},
            "result_persistence": {"state": "not_observed", "ref": None},
            "main_commit": {"state": "not_started", "ref": None},
            "cleanup": {"state": "unknown", "ref": None},
        },
        "last_safe_boundary": _id("8"),
        "authority_refs": _authority_refs(),
        "correlation_refs": {"browser_control_scope_id": _id("6"), "sidecar_command_ref": None},
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
    payload = {
        "schema_version": "seektalent.machine-capability-receipt/v1",
        "receipt_id": _id("a"),
        "revision": 1,
        "canonical_hash": "",
        "generated_at": AT,
        "created_at": AT,
        "observed_at": AT,
        "release_manifest_ref": _sha("c"),
        "product_version": "0.7.49",
        "product_build_ref": _sha("e"),
        "domi_version": "1.4.0",
        "domi_build_ref": _sha("6"),
        "install_channel": "candidate",
        "platform": "macos",
        "architecture": "arm64",
        "os_family": "macos",
        "os_build": "24.5.0",
        "os_version_bucket": "15.0",
        "runtime_versions": {"python": "3.12", "sqlite": "3.49", "chrome": "126.0"},
        "chrome_channel": "stable",
        "active_slot_ref": _sha("7"),
        "previous_slot_ref": _sha("8"),
        "switch_status": "completed",
        "rollback_status": "available",
        "manifest_hash": "9" * 64,
        "artifact_hash": "a" * 64,
        "manifest_signature_status": "verified",
        "artifact_signature_status": "verified",
        "component_build_refs": {"main": _sha("d"), "sidecar": _sha("f")},
        "bridge_implementation": "wtscli",
        "bridge_build_ref": _sha("b"),
        "bridge_protocol_ref": _sha("c"),
        "bridge_capabilities": ["authenticated_framing", "source_port_v1", "browser_control"],
        "profile_mode": "isolated",
        "profile_binding_hash": "d" * 64,
        "profile_binding_generation": 1,
        "extension_version": "1.0.0",
        "extension_id_hash": "e" * 64,
        "provider_account_hash": "f" * 64,
        "endpoint_ownership": "verified",
        "database_logical_name": "runtime_control",
        "database_schema_version": 1,
        "database_journal_mode": "wal",
        "database_integrity": "ok",
        "database_file_size_bucket": "small",
        "database_wal_size_bucket": "empty",
        "database_shm_size_bucket": "small",
        "disk_free_size_bucket": "adequate",
        "disk_writable": True,
        "disk_executable": True,
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
    payload["canonical_hash"] = _embedded_hash(payload)
    return payload


def _startup() -> dict[str, object]:
    payload = {
        "schema_version": "seektalent.startup-receipt/v1",
        "startup_receipt_id": _id("b"),
        "revision": 1,
        "canonical_hash": "",
        "created_at": AT,
        "observed_at": AT,
        "component": "sidecar",
        "component_instance_id": _id("c"),
        "parent_instance_id": _id("5"),
        "capability_receipt_ref": _id("a"),
        "release_manifest_ref": _sha("c"),
        "component_build_ref": _sha("f"),
        "protocol_refs": [_sha("1")],
        "capability_refs": [_sha("2")],
        "startup_kind": "fresh",
        "started_at": AT,
        "readiness_observed_at": AT,
        "exited_at": None,
        "readiness": "ready",
        "reason_code": "component_ready",
        "restart_count": 0,
        "restart_budget_ref": _sha("3"),
        "previous_instance_ref": None,
        "last_exit_cause_ref": None,
        "profile_binding_generation": 1,
        "browser_control_scope_id": None,
        "extension_install_generation": None,
        "service_worker_generation": None,
        "database_refs": [_sha("4")],
        "endpoint_ownership_ref": _sha("5"),
        "redaction": _redaction(),
    }
    payload["canonical_hash"] = _embedded_hash(payload)
    return payload


def _operation_evidence() -> dict[str, object]:
    payload = {
        "schema_version": "seektalent.operation-evidence/v1",
        "operation_evidence_id": _id("d"),
        "revision": 1,
        "canonical_hash": "",
        "release_manifest_ref": _sha("c"),
        "correlation_id": _id("2"),
        "run_id": _id("3"),
        "operation_id": _id("4"),
        "attempt_no": 1,
        "diagnostic_trace_id": TRACE_ID,
        "authority_refs": _authority_refs(),
        "capability_receipt_refs": [_id("a")],
        "startup_receipt_refs": [_id("b")],
        "source_id": "liepin",
        "operation_kind": "verify_session",
        "first_event_ref": _id("1"),
        "last_event_ref": _id("e"),
        "failure_envelope_ref": _id("7"),
        "checkpoint_ref": None,
        "boundary_facts": _failure()["boundary_facts"],
        "summary": {"result_count": 0, "coverage": "partial"},
        "source_operation_disposition": None,
        "product_outcome": None,
        "missing_evidence_refs": [],
        "rejected_stale_write_count": 0,
        "journal_truncation": "none",
        "created_at": AT,
        "observed_at": AT,
        "redaction": _redaction(),
    }
    payload["canonical_hash"] = _embedded_hash(payload)
    return payload


def _ack() -> dict[str, object]:
    return {
        "schema_version": "seektalent.journal-append-ack/v1",
        "event_id": _id("1"),
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
    assert model.from_trusted_fields(**payload) == parsed

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
            json.dumps(_event()).replace(f'"{_id("1")}"', '"\\ud800"', 1).encode()
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
    event = parse_canonical_event(json.dumps(_event()).encode())
    assert event.run_id != event.operation_id
    assert event.attempt_no == 1
    assert event.correlation_id == _id("2")
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
        with pytest.raises(ValueError) as exc_info:
            parse_canonical_event(json.dumps(payload).encode())
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
        assert definition.artifacts <= {"event", "failure"}
        assert definition.event_statuses


def test_unknown_registry_tokens_fail_closed() -> None:
    for field, value in (
        ("component", "unknown-component"),
        ("phase", "unknown-phase"),
        ("event_name", "unknown.event"),
        ("reason_code", "generic_unavailable"),
    ):
        payload = _event()
        payload[field] = value
        with pytest.raises(ValueError):
            parse_canonical_event(json.dumps(payload).encode())

    payload = _failure()
    payload["domain"] = "unknown-domain"
    with pytest.raises(ValueError):
        parse_failure_envelope(json.dumps(payload).encode())


def test_canonical_known_answer_and_determinism() -> None:
    event = parse_canonical_event(json.dumps(_event()).encode())
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
    evidence = parse_operation_evidence(json.dumps(_operation_evidence()).encode())
    assert evidence.canonical_hash == _operation_evidence()["canonical_hash"]

    payload = _operation_evidence()
    payload["summary"] = {"coverage": "completed", "result_count": 0}
    with pytest.raises(ValueError):
        parse_operation_evidence(json.dumps(payload).encode())


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
        with pytest.raises(ValueError) as exc_info:
            parse_canonical_event(json.dumps(payload).encode())
        messages.append(str(exc_info.value))
        assert path not in str(exc_info.value)
        assert path not in repr(exc_info.value)
    assert messages[0] == messages[1]


def test_valid_artifacts_are_frozen_and_safe_to_repr() -> None:
    for model, _parser, factory in ARTIFACTS:
        artifact = _parser(json.dumps(factory()).encode())
        with pytest.raises(ValidationError):
            artifact.schema_version = "changed"
        assert "Bearer " not in repr(artifact)


def test_event_attributes_reject_business_content_under_safe_alias() -> None:
    canary = "Private Candidate Jane Doe resume content"
    payload = _event()
    payload["attributes"] = {"coverage": canary}
    with pytest.raises(ValueError) as exc_info:
        parse_canonical_event(json.dumps(payload).encode())
    assert canary not in str(exc_info.value)
    assert canary not in repr(exc_info.value)
    assert canary not in repr(exc_info.value.__dict__)


def test_untrusted_locations_never_retain_unknown_input_keys() -> None:
    canary = "Private Candidate Jane Doe full resume"
    payload = _event()
    payload[canary] = "x"
    with pytest.raises(ValueError) as exc_info:
        parse_canonical_event(json.dumps(payload).encode())
    assert exc_info.value.location == ("<redacted-field>",)
    assert canary not in repr(exc_info.value.__dict__)

    raw = json.dumps(_event(), separators=(",", ":"))
    duplicate = raw[:-1] + f',"{canary}":1,"{canary}":2}}'
    with pytest.raises(ValueError) as duplicate_info:
        parse_canonical_event(duplicate.encode())
    assert duplicate_info.value.location == ("<redacted-field>",)
    assert canary not in repr(duplicate_info.value.__dict__)


def test_redaction_report_rejects_untrusted_free_form_path() -> None:
    payload = _event()
    payload["redaction"] = {
        "policy_version": "seektalent.diagnostics-redaction/v1",
        "result": "redacted",
        "redacted_field_count": 1,
        "report": [{
            "rule": "field_not_allowlisted",
            "path": "Private Candidate Jane Doe full resume",
            "count": 1,
        }],
    }
    with pytest.raises(ValueError):
        parse_canonical_event(json.dumps(payload).encode())


def test_cause_codes_are_closed_and_failure_reasons_are_failure_only() -> None:
    payload = _failure()
    payload["cause_ref"]["code"] = "completely_unregistered_vendor_code"
    with pytest.raises(ValueError):
        parse_failure_envelope(json.dumps(payload).encode())

    for reason_code in ("machine_capability_supported", "component_ready", "operation_accepted"):
        definition = REASON_DEFINITIONS[reason_code]
        payload = _failure()
        payload["reason_code"] = reason_code
        payload["domain"] = definition.domain
        payload["failure_kind"] = definition.failure_kind
        with pytest.raises(ValueError):
            parse_failure_envelope(json.dumps(payload).encode())

    payload = _event()
    payload["status"] = "started"
    with pytest.raises(ValueError):
        parse_canonical_event(json.dumps(payload).encode())


def test_event_authority_refs_are_event_specific() -> None:
    payload = _event()
    payload.update({
        "event_name": "machine.capability.evaluated",
        "component": "main",
        "phase": "capability",
        "status": "completed",
        "reason_code": "machine_capability_supported",
        "correlation_id": None,
        "run_id": None,
        "operation_id": None,
        "attempt_no": None,
        "attributes": {
            "capability": "browser_bridge",
            "result": "supported",
        },
    })
    with pytest.raises(ValueError):
        parse_canonical_event(json.dumps(payload).encode())


@pytest.mark.parametrize(
    "value",
    ("private-macbook", "private-user", "candidate-jane-doe", "acme-confidential"),
)
def test_random_identity_refs_reject_human_semantic_slugs(value: str) -> None:
    payload = _event()
    payload["component_instance_id"] = value
    with pytest.raises(ValueError) as exc_info:
        parse_canonical_event(json.dumps(payload).encode())
    assert value not in repr(exc_info.value.__dict__)


def test_random_identity_and_sha256_reference_types_are_not_interchangeable() -> None:
    payload = _event()
    payload["component_instance_id"] = _sha("a")
    with pytest.raises(ValueError):
        parse_canonical_event(json.dumps(payload).encode())

    payload = _event()
    payload["component_build_ref"] = _id("a")
    with pytest.raises(ValueError):
        parse_canonical_event(json.dumps(payload).encode())


def test_mapping_model_validate_cannot_bypass_bytes_admission() -> None:
    for model, _parser, factory in ARTIFACTS:
        with pytest.raises(ValueError, match="diagnostics_raw_input_required"):
            model.model_validate(factory())


def test_machine_capability_aggregate_cannot_contradict_capabilities() -> None:
    payload = _capability()
    payload["capabilities"]["browser_bridge"] = "unsupported"
    with pytest.raises(ValueError):
        parse_machine_capability_receipt(json.dumps(payload).encode())


def test_startup_readiness_reason_and_time_order_are_consistent() -> None:
    payload = _startup()
    payload["reason_code"] = "component_startup_failed"
    with pytest.raises(ValueError):
        parse_startup_receipt(json.dumps(payload).encode())


@pytest.mark.parametrize(
    ("model", "coverage"),
    (
        (MachineCapabilityReceiptV1, MACHINE_CAPABILITY_FIELD_COVERAGE),
        (StartupReceiptV1, STARTUP_RECEIPT_FIELD_COVERAGE),
        (OperationEvidenceV1, OPERATION_EVIDENCE_FIELD_COVERAGE),
    ),
)
def test_frozen_receipt_evidence_fields_have_one_to_one_coverage(model, coverage) -> None:
    mapped = [field for fields in coverage.values() for field in fields]
    assert len(mapped) == len(set(mapped))
    assert set(mapped) == set(model.model_fields) - {"schema_version", "redaction"}


def test_diagnostics_modules_have_one_way_responsibilities_and_thin_facade() -> None:
    source_root = Path(__file__).parents[1] / "src" / "seektalent"
    facade = source_root / "diagnostics_schema.py"
    admission = source_root / "diagnostics_admission.py"
    common = source_root / "diagnostics_model_common.py"
    event_models = source_root / "diagnostics_event_models.py"
    receipt_models = source_root / "diagnostics_receipt_models.py"

    assert len(facade.read_text(encoding="utf-8").splitlines()) < 200
    assert len(event_models.read_text(encoding="utf-8").splitlines()) < 350
    assert len(receipt_models.read_text(encoding="utf-8").splitlines()) < 450

    def imports(path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        return {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }

    assert "seektalent.diagnostics_schema" not in imports(admission)
    assert "seektalent.diagnostics_admission" not in imports(common)
    assert "seektalent.diagnostics_receipt_models" not in imports(event_models)
    assert "seektalent.diagnostics_event_models" not in imports(receipt_models)

    payload = _startup()
    payload["exited_at"] = "2026-07-26T11:59:59Z"
    with pytest.raises(ValueError):
        parse_startup_receipt(json.dumps(payload).encode())
