from datetime import UTC, datetime, timedelta

import pytest
from pydantic import TypeAdapter, ValidationError

from seektalent.providers.pi_agent.contracts import (
    DetailOpenGrant,
    DetailOpenReasonCode,
    DokoBotReadResult,
    LiepinOpenDetailAfterApprovalTask,
    LiepinTurnPageAction,
    PiAgentAction,
    PiAgentActionTraceEntry,
    PiAgentCompletionReason,
    PiAgentFailureCode,
    PiAgentResult,
    PiAgentResultStatus,
    PiAgentTask,
    PiArtifactRef,
    ProtectedArtifactClass,
)
from seektalent.providers.pi_agent.validation_errors import render_safe_validation_error


def _grant() -> DetailOpenGrant:
    return DetailOpenGrant(
        schema_version="detail-open-grant-v1",
        approval_id="approval_1",
        budget_reservation_id="budget_1",
        candidate_ref="candidate_1",
        source_run_id="source_run_1",
        provider="liepin",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        issued_by="workflow_runtime",
        idempotency_key="detail_candidate_1_approval_1",
        grant_signature="signature_1",
    )


def _artifact_ref() -> PiArtifactRef:
    return PiArtifactRef(
        artifact_class=ProtectedArtifactClass.REDACTED_EVIDENCE,
        artifact_ref="artifact_trace_1",
        content_sha256="0" * 64,
        redaction_policy_id="liepin-trace-redaction-v1",
    )


def _protected_snapshot_ref() -> PiArtifactRef:
    return PiArtifactRef(
        artifact_class=ProtectedArtifactClass.PROTECTED_PROVIDER_SNAPSHOT,
        artifact_ref="snapshot_1",
        content_sha256="1" * 64,
        protection_policy_id="liepin-protected-snapshot-v1",
    )


def _safe_summary_ref() -> PiArtifactRef:
    return PiArtifactRef(
        artifact_class=ProtectedArtifactClass.SAFE_SUMMARY,
        artifact_ref="summary_1",
        content_sha256="2" * 64,
        redaction_policy_id="liepin-summary-redaction-v1",
    )


def test_boundary_models_require_explicit_schema_version() -> None:
    payload = {
        "task_type": "liepin.search_cards",
        "session_id": "session_1",
        "source_run_id": "source_run_1",
        "connection_id": "connection_1",
        "artifact_policy": "protected_snapshots_only",
        "query_terms": ["Python"],
        "keyword_query": "Python",
        "max_pages": 2,
        "max_cards": 20,
        "stop_conditions": ["page_exhausted"],
    }

    with pytest.raises(ValidationError):
        TypeAdapter(PiAgentTask).validate_python(payload)


def test_detail_open_grant_requires_signature() -> None:
    with pytest.raises(ValidationError):
        DetailOpenGrant(
            schema_version="detail-open-grant-v1",
            approval_id="approval_1",
            budget_reservation_id="budget_1",
            candidate_ref="candidate_1",
            source_run_id="source_run_1",
            provider="liepin",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            issued_by="workflow_runtime",
            idempotency_key="detail_candidate_1_approval_1",
        )


def test_detail_open_grant_rejects_blank_signature() -> None:
    with pytest.raises(ValidationError):
        DetailOpenGrant(
            schema_version="detail-open-grant-v1",
            approval_id="approval_1",
            budget_reservation_id="budget_1",
            candidate_ref="candidate_1",
            source_run_id="source_run_1",
            provider="liepin",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            issued_by="workflow_runtime",
            idempotency_key="detail_candidate_1_approval_1",
            grant_signature="",
        )


def test_validation_errors_hide_raw_input_values() -> None:
    with pytest.raises(ValidationError) as error:
        DetailOpenGrant(
            schema_version="detail-open-grant-v1",
            approval_id="approval_1",
            budget_reservation_id="budget_1",
            candidate_ref="candidate_1",
            source_run_id="source_run_1",
            provider="liepin",
            max_detail_opens="candidate_secret_value",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            issued_by="workflow_runtime",
            idempotency_key="detail_candidate_1_approval_1",
            grant_signature="signature_1",
        )

    assert "candidate_secret_value" not in str(error.value)


def test_safe_validation_error_renderer_does_not_expose_raw_error_payloads() -> None:
    with pytest.raises(ValidationError) as error:
        DetailOpenGrant(
            schema_version="detail-open-grant-v1",
            approval_id="approval_1",
            budget_reservation_id="budget_1",
            candidate_ref="candidate_secret_value",
            source_run_id="source_run_1",
            provider="liepin",
            max_detail_opens="candidate_secret_value",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            issued_by="workflow_runtime",
            idempotency_key="detail_candidate_1_approval_1",
            grant_signature="signature_1",
        )

    assert "candidate_secret_value" in str(error.value.errors())
    issues = render_safe_validation_error(
        error.value,
        model_name="DetailOpenGrant",
        schema_version="detail-open-grant-v1",
        correlation_id="corr_1",
    )

    rendered = [issue.model_dump(mode="json") for issue in issues]
    assert rendered == [
        {
            "model_name": "DetailOpenGrant",
            "field_path": "max_detail_opens",
            "error_type": "int_parsing",
            "schema_version": "detail-open-grant-v1",
            "correlation_id": "corr_1",
        }
    ]
    assert "candidate_secret_value" not in str(rendered)


def test_safe_validation_error_renderer_redacts_extra_field_names() -> None:
    payload = {
        "schema_version": "detail-open-grant-v1",
        "approval_id": "approval_1",
        "budget_reservation_id": "budget_1",
        "candidate_ref": "candidate_1",
        "source_run_id": "source_run_1",
        "provider": "liepin",
        "expires_at": datetime.now(UTC) + timedelta(minutes=5),
        "issued_by": "workflow_runtime",
        "idempotency_key": "detail_candidate_1_approval_1",
        "grant_signature": "signature_1",
        "candidate_secret_value": "raw provider material",
    }
    with pytest.raises(ValidationError) as error:
        DetailOpenGrant(**payload)

    assert "candidate_secret_value" in str(error.value.errors())
    issues = render_safe_validation_error(
        error.value,
        model_name="DetailOpenGrant",
        schema_version="detail-open-grant-v1",
        correlation_id="corr_1",
    )
    rendered = [issue.model_dump(mode="json") for issue in issues]

    assert rendered == [
        {
            "model_name": "DetailOpenGrant",
            "field_path": "__extra__",
            "error_type": "extra_forbidden",
            "schema_version": "detail-open-grant-v1",
            "correlation_id": "corr_1",
        }
    ]
    assert "candidate_secret_value" not in str(rendered)
    assert "raw provider material" not in str(rendered)


def test_union_validation_errors_hide_nested_raw_input_values() -> None:
    grant_payload = {
        "schema_version": "detail-open-grant-v1",
        "approval_id": "approval_1",
        "budget_reservation_id": "budget_1",
        "candidate_ref": "candidate_1",
        "source_run_id": "source_run_1",
        "provider": "liepin",
        "max_detail_opens": "candidate_secret_value",
        "expires_at": datetime.now(UTC) + timedelta(minutes=5),
        "issued_by": "workflow_runtime",
        "idempotency_key": "detail_candidate_1_approval_1",
        "grant_signature": "signature_1",
    }
    task_payload = {
        "schema_version": "pi-agent-task-v1",
        "task_type": "liepin.open_detail_after_approval",
        "session_id": "session_1",
        "source_run_id": "source_run_1",
        "connection_id": "connection_1",
        "artifact_policy": "protected_snapshots_only",
        "candidate_ref": "candidate_1",
        "detail_open_grant": grant_payload,
    }
    action_payload = {
        "schema_version": "pi-agent-action-v1",
        "action_type": "liepin.open_detail_after_approval",
        "target_url": "https://www.liepin.com/zhaopin/",
        "safe_target_descriptor": "Liepin detail open",
        "input_payload": {
            "candidate_ref": "candidate_1",
            "detail_open_grant": grant_payload,
        },
    }

    with pytest.raises(ValidationError) as error:
        TypeAdapter(PiAgentTask).validate_python(task_payload)

    assert "candidate_secret_value" not in str(error.value)

    with pytest.raises(ValidationError) as error:
        TypeAdapter(PiAgentAction).validate_python(action_payload)

    assert "candidate_secret_value" not in str(error.value)


def test_detail_open_grant_rejects_naive_expiry() -> None:
    with pytest.raises(ValidationError):
        DetailOpenGrant(
            schema_version="detail-open-grant-v1",
            approval_id="approval_1",
            budget_reservation_id="budget_1",
            candidate_ref="candidate_1",
            source_run_id="source_run_1",
            provider="liepin",
            expires_at=datetime.now() + timedelta(minutes=5),
            issued_by="workflow_runtime",
            idempotency_key="detail_candidate_1_approval_1",
            grant_signature="signature_1",
        )


def test_boundary_identity_fields_reject_blank_values() -> None:
    with pytest.raises(ValidationError):
        DetailOpenGrant(
            schema_version="detail-open-grant-v1",
            approval_id="approval_1",
            budget_reservation_id="budget_1",
            candidate_ref="",
            source_run_id="source_run_1",
            provider="liepin",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            issued_by="workflow_runtime",
            idempotency_key="detail_candidate_1_approval_1",
            grant_signature="signature_1",
        )

    with pytest.raises(ValidationError):
        PiAgentActionTraceEntry(
            schema_version="pi-agent-action-trace-v1",
            timestamp=datetime.now(UTC),
            provider_skill_id="liepin.search_cards.v1",
            interaction_id="",
            source_run_id="source_run_1",
            connection_id="connection_1",
            action_sequence=1,
            action_type="liepin.read_card_page",
            backend_mode="pi_dokobot_read",
            capability_version="dokobot-cli-2.11.0",
            safe_target_descriptor="Liepin search result page 1",
            result_code="ok",
            duration_ms=240,
            retry_count=0,
            redaction_policy_id="liepin-card-redaction-v1",
            redacted_evidence_ref="artifact_redacted_1",
            evidence_sha256="0" * 64,
        )


def test_artifact_refs_require_policy_matching_artifact_class() -> None:
    with pytest.raises(ValidationError):
        PiArtifactRef(
            artifact_class=ProtectedArtifactClass.REDACTED_EVIDENCE,
            artifact_ref="artifact_redacted_1",
            content_sha256="0" * 64,
        )

    with pytest.raises(ValidationError):
        PiArtifactRef(
            artifact_class=ProtectedArtifactClass.PROTECTED_PROVIDER_SNAPSHOT,
            artifact_ref="snapshot_1",
            content_sha256="1" * 64,
            redaction_policy_id="wrong-policy",
        )


def test_artifact_refs_reject_blank_or_path_like_refs() -> None:
    invalid_refs = ("", "/tmp/provider-snapshot.json", "../snapshot.json", "file:///tmp/snapshot.json")

    for artifact_ref in invalid_refs:
        with pytest.raises(ValidationError):
            PiArtifactRef(
                artifact_class=ProtectedArtifactClass.REDACTED_EVIDENCE,
                artifact_ref=artifact_ref,
                content_sha256="0" * 64,
                redaction_policy_id="liepin-trace-redaction-v1",
            )


def test_dokobot_read_result_requires_schema_version() -> None:
    with pytest.raises(ValidationError):
        DokoBotReadResult(
            url="https://www.liepin.com/zhaopin/",
            text_ref=_protected_snapshot_ref(),
        )


def test_dokobot_read_result_rejects_safe_summary_as_text_ref() -> None:
    with pytest.raises(ValidationError):
        DokoBotReadResult(
            schema_version="dokobot-read-result-v1",
            url="https://www.liepin.com/zhaopin/",
            text_ref=_safe_summary_ref(),
        )


def test_task_union_accepts_every_declared_task_type() -> None:
    grant = _grant()
    base = {
        "schema_version": "pi-agent-task-v1",
        "session_id": "session_1",
        "source_run_id": "source_run_1",
        "connection_id": "connection_1",
        "artifact_policy": "protected_snapshots_only",
    }
    payloads = [
        {
            **base,
            "task_type": "liepin.search_cards",
            "query_terms": ["Python"],
            "keyword_query": "Python",
            "max_pages": 2,
            "max_cards": 20,
            "stop_conditions": ["page_exhausted"],
        },
        {
            **base,
            "task_type": "liepin.read_card_page",
            "current_url": "https://www.liepin.com/zhaopin/",
            "page_index": 1,
        },
        {
            **base,
            "task_type": "liepin.classify_card_summary",
            "candidate_ref": "candidate_1",
            "summary_ref": "summary_1",
            "classification_policy_id": "liepin-card-classifier-v1",
        },
        {
            **base,
            "task_type": "liepin.request_detail_open",
            "candidate_ref": "candidate_1",
            "summary_ref": "summary_1",
            "reason_code": DetailOpenReasonCode.STRONG_CARD_MATCH.value,
        },
        {
            **base,
            "task_type": "liepin.open_detail_after_approval",
            "candidate_ref": "candidate_1",
            "detail_open_grant": grant.model_dump(mode="python"),
        },
        {
            **base,
            "task_type": "liepin.extract_detail_resume",
            "candidate_ref": "candidate_1",
            "detail_snapshot_ref": "snapshot_1",
        },
        {
            **base,
            "task_type": "liepin.detect_login_or_risk_state",
            "current_url": "https://www.liepin.com/zhaopin/",
        },
    ]

    for payload in payloads:
        parsed = TypeAdapter(PiAgentTask).validate_python(payload)
        assert parsed.task_type == payload["task_type"]


def test_search_task_rejects_detail_grant_fields() -> None:
    payload = {
        "schema_version": "pi-agent-task-v1",
        "task_type": "liepin.search_cards",
        "session_id": "session_1",
        "source_run_id": "source_run_1",
        "connection_id": "connection_1",
        "artifact_policy": "protected_snapshots_only",
        "query_terms": ["Python"],
        "keyword_query": "Python",
        "max_pages": 2,
        "max_cards": 20,
        "stop_conditions": ["page_exhausted"],
        "detail_open_grant": {"approval_id": "not_allowed"},
    }

    with pytest.raises(ValidationError):
        TypeAdapter(PiAgentTask).validate_python(payload)


def test_open_detail_task_requires_runtime_grant() -> None:
    task = LiepinOpenDetailAfterApprovalTask(
        schema_version="pi-agent-task-v1",
        task_type="liepin.open_detail_after_approval",
        session_id="session_1",
        source_run_id="source_run_1",
        connection_id="connection_1",
        artifact_policy="protected_snapshots_only",
        candidate_ref="candidate_1",
        detail_open_grant=_grant(),
    )

    assert task.detail_open_grant.budget_reservation_id == "budget_1"
    assert task.detail_open_grant.max_detail_opens == 1


def test_action_union_accepts_every_declared_action_type() -> None:
    grant = _grant()
    base = {
        "schema_version": "pi-agent-action-v1",
        "target_url": "https://www.liepin.com/zhaopin/",
        "safe_target_descriptor": "Liepin controlled action",
    }
    payloads = [
        {
            **base,
            "action_type": "liepin.navigate_to_search",
            "input_payload": {"query_home_url": "https://www.liepin.com/zhaopin/"},
        },
        {
            **base,
            "action_type": "liepin.submit_keyword_search",
            "input_payload": {"keyword_query": "Python", "query_terms": ["Python"]},
        },
        {
            **base,
            "action_type": "liepin.read_card_page",
            "input_payload": {"page_index": 1},
        },
        {
            **base,
            "action_type": "liepin.turn_page",
            "input_payload": {"next_page_index": 2},
        },
        {
            **base,
            "action_type": "liepin.classify_card_summary",
            "input_payload": {
                "candidate_ref": "candidate_1",
                "summary_ref": "summary_1",
                "classification_policy_id": "liepin-card-classifier-v1",
            },
        },
        {
            **base,
            "action_type": "liepin.request_detail_open",
            "input_payload": {
                "candidate_ref": "candidate_1",
                "summary_ref": "summary_1",
                "reason_code": DetailOpenReasonCode.STRONG_CARD_MATCH.value,
            },
        },
        {
            **base,
            "action_type": "liepin.open_detail_after_approval",
            "input_payload": {
                "candidate_ref": "candidate_1",
                "detail_open_grant": grant.model_dump(mode="python"),
            },
        },
        {
            **base,
            "action_type": "liepin.extract_detail_resume",
            "input_payload": {
                "candidate_ref": "candidate_1",
                "detail_snapshot_ref": "snapshot_1",
            },
        },
        {
            **base,
            "action_type": "liepin.detect_login_or_risk_state",
            "input_payload": {"current_url": "https://www.liepin.com/zhaopin/"},
        },
    ]

    for payload in payloads:
        parsed = TypeAdapter(PiAgentAction).validate_python(payload)
        assert parsed.action_type == payload["action_type"]


def test_action_payloads_are_typed_and_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError):
        LiepinTurnPageAction(
            schema_version="pi-agent-action-v1",
            action_type="liepin.turn_page",
            target_url="https://www.liepin.com/zhaopin/",
            safe_target_descriptor="Liepin results next page",
            input_payload={"next_page_index": 2, "unexpected": "value"},
        )


def test_task_union_round_trips_through_json() -> None:
    task = LiepinOpenDetailAfterApprovalTask(
        schema_version="pi-agent-task-v1",
        task_type="liepin.open_detail_after_approval",
        session_id="session_1",
        source_run_id="source_run_1",
        connection_id="connection_1",
        artifact_policy="protected_snapshots_only",
        candidate_ref="candidate_1",
        detail_open_grant=_grant(),
    )

    parsed = TypeAdapter(PiAgentTask).validate_json(task.model_dump_json())
    assert parsed.task_type == "liepin.open_detail_after_approval"


def test_action_union_round_trips_through_json() -> None:
    action = LiepinTurnPageAction(
        schema_version="pi-agent-action-v1",
        action_type="liepin.turn_page",
        target_url="https://www.liepin.com/zhaopin/",
        safe_target_descriptor="Liepin results next page",
        input_payload={"next_page_index": 2},
    )

    parsed = TypeAdapter(PiAgentAction).validate_json(action.model_dump_json())
    assert parsed.input_payload.next_page_index == 2


def test_result_rejects_arbitrary_stop_reason() -> None:
    with pytest.raises(ValidationError):
        PiAgentResult(
            schema_version="pi-agent-result-v1",
            status=PiAgentResultStatus.BLOCKED,
            stop_reason="whatever_string",
            action_trace_ref=_artifact_ref(),
        )


def test_result_validates_status_reason_and_artifact_classes() -> None:
    with pytest.raises(ValidationError):
        PiAgentResult(
            schema_version="pi-agent-result-v1",
            status=PiAgentResultStatus.BLOCKED,
            stop_reason=PiAgentCompletionReason.PAGE_EXHAUSTED,
            action_trace_ref=_artifact_ref(),
        )

    with pytest.raises(ValidationError):
        PiAgentResult(
            schema_version="pi-agent-result-v1",
            status=PiAgentResultStatus.SUCCEEDED,
            stop_reason=PiAgentFailureCode.LOGIN_EXPIRED,
            action_trace_ref=_artifact_ref(),
        )

    with pytest.raises(ValidationError):
        PiAgentResult(
            schema_version="pi-agent-result-v1",
            status=PiAgentResultStatus.SUCCEEDED,
            stop_reason=PiAgentCompletionReason.COMPLETED,
            action_trace_ref=_artifact_ref(),
            safe_summary_refs=[_protected_snapshot_ref()],
        )

    result = PiAgentResult(
        schema_version="pi-agent-result-v1",
        status=PiAgentResultStatus.SUCCEEDED,
        stop_reason=PiAgentCompletionReason.COMPLETED,
        action_trace_ref=_artifact_ref(),
        protected_snapshot_refs=[_protected_snapshot_ref()],
        safe_summary_refs=[_safe_summary_ref()],
    )
    assert result.status == PiAgentResultStatus.SUCCEEDED


def test_result_needs_approval_requires_human_wait_reason() -> None:
    with pytest.raises(ValidationError):
        PiAgentResult(
            schema_version="pi-agent-result-v1",
            status=PiAgentResultStatus.NEEDS_APPROVAL,
            stop_reason=PiAgentCompletionReason.PAGE_EXHAUSTED,
            action_trace_ref=_artifact_ref(),
        )

    result = PiAgentResult(
        schema_version="pi-agent-result-v1",
        status=PiAgentResultStatus.NEEDS_APPROVAL,
        stop_reason=PiAgentCompletionReason.DETAIL_BUDGET_WAITING_FOR_HUMAN,
        action_trace_ref=_artifact_ref(),
    )

    assert result.status == PiAgentResultStatus.NEEDS_APPROVAL


def test_action_trace_has_audit_identity_and_evidence_hash() -> None:
    trace = PiAgentActionTraceEntry(
        schema_version="pi-agent-action-trace-v1",
        timestamp=datetime.now(UTC),
        provider_skill_id="liepin.search_cards.v1",
        interaction_id="interaction_1",
        source_run_id="source_run_1",
        connection_id="connection_1",
        action_sequence=1,
        action_type="liepin.read_card_page",
        backend_mode="pi_dokobot_read",
        capability_version="dokobot-cli-2.11.0",
        safe_target_descriptor="Liepin search result page 1",
        result_code="ok",
        duration_ms=240,
        retry_count=0,
        redaction_policy_id="liepin-card-redaction-v1",
        redacted_evidence_ref="artifact_redacted_1",
        evidence_sha256="0" * 64,
    )

    assert trace.provider_skill_id == "liepin.search_cards.v1"
    assert trace.failure_code is None
    assert PiAgentFailureCode.DETAIL_OPEN_GRANT_MISSING.value == "detail_open_grant_missing"


def test_action_trace_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError):
        PiAgentActionTraceEntry(
            schema_version="pi-agent-action-trace-v1",
            timestamp=datetime.now(),
            provider_skill_id="liepin.search_cards.v1",
            interaction_id="interaction_1",
            source_run_id="source_run_1",
            connection_id="connection_1",
            action_sequence=1,
            action_type="liepin.read_card_page",
            backend_mode="pi_dokobot_read",
            capability_version="dokobot-cli-2.11.0",
            safe_target_descriptor="Liepin search result page 1",
            result_code="ok",
            duration_ms=240,
            retry_count=0,
            redaction_policy_id="liepin-card-redaction-v1",
            redacted_evidence_ref="artifact_redacted_1",
            evidence_sha256="0" * 64,
        )


def test_action_trace_rejects_inconsistent_failure_and_evidence_fields() -> None:
    base = {
        "schema_version": "pi-agent-action-trace-v1",
        "timestamp": datetime.now(UTC),
        "provider_skill_id": "liepin.search_cards.v1",
        "interaction_id": "interaction_1",
        "source_run_id": "source_run_1",
        "connection_id": "connection_1",
        "action_sequence": 1,
        "action_type": "liepin.read_card_page",
        "backend_mode": "pi_dokobot_read",
        "capability_version": "dokobot-cli-2.11.0",
        "safe_target_descriptor": "Liepin search result page 1",
        "duration_ms": 240,
        "retry_count": 0,
        "redaction_policy_id": "liepin-card-redaction-v1",
    }

    with pytest.raises(ValidationError):
        PiAgentActionTraceEntry(
            **base,
            result_code="ok",
            failure_code=PiAgentFailureCode.LOGIN_EXPIRED,
            redacted_evidence_ref="artifact_redacted_1",
            evidence_sha256="0" * 64,
        )

    with pytest.raises(ValidationError):
        PiAgentActionTraceEntry(
            **base,
            result_code="blocked",
            redacted_evidence_ref="artifact_redacted_1",
            evidence_sha256="0" * 64,
        )

    with pytest.raises(ValidationError):
        PiAgentActionTraceEntry(
            **base,
            result_code="ok",
            redacted_evidence_ref="artifact_redacted_1",
        )
