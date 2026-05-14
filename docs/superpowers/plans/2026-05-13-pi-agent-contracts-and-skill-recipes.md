# PI Agent Contracts And Skill Recipes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the typed PI Agent contract layer and Liepin provider skill recipes that every later DokoBot plan depends on.

**Architecture:** Keep the PI Agent core focused on strict boundary models: typed tasks, actions, results, grants, backend modes, failure codes, stop reasons, artifact refs, and consistency validators. Keep Liepin-specific host, pre/post route, action, redaction, pacing, and evidence rules in a small provider skill registry that imports the contract enums so drift fails in tests.

**Tech Stack:** Python 3.12, Pydantic v2, dataclasses, pytest, ty as the repo's current secondary type check. Pytest is the required gate for this plan; ty checks only the new `src/` modules here because the current `pyproject.toml` ignores `tests/**` for ty.

**Spec:** `docs/superpowers/specs/2026-05-13-provider-interaction-agent-dokobot-design.md`

---

## File Structure

- Add: `src/seektalent/providers/pi_agent/__init__.py`
  - Package marker for the PI Agent boundary.
- Add: `src/seektalent/providers/pi_agent/contracts.py`
  - Typed PI task/action/result models, detail-open grants, failure enums, stop reasons, backend modes, artifact refs, validation secrecy config, and consistency validators.
- Add: `src/seektalent/providers/liepin/pi_skills.py`
  - Liepin skill recipes: allowed hosts, pre/post routes, allowed actions, forbidden actions, URL matching, failure codes, redaction policy, pacing policy, and evidence requirements.
- Test: `tests/test_pi_agent_contracts.py`
  - Contract validation, schema-version, non-empty identity fields, grant-signature, hidden validation inputs, action-union, task-union, JSON round-trip, stop-reason, artifact-class, artifact-ref safety, timezone-aware audit timestamps, and audit-trace tests.
- Test: `tests/test_liepin_pi_skills.py`
  - Liepin skill registry, route matcher, forbidden DokoBot/DevTools action, and contract-enum consistency tests.

### Task 1: Add Typed PI Agent Contracts

**Files:**
- Create: `src/seektalent/providers/pi_agent/__init__.py`
- Create: `src/seektalent/providers/pi_agent/contracts.py`
- Test: `tests/test_pi_agent_contracts.py`

- [x] **Step 1: Write failing contract tests**

Add `tests/test_pi_agent_contracts.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import TypeAdapter, ValidationError

from seektalent.providers.pi_agent.contracts import (
    DetailOpenReasonCode,
    DetailOpenGrant,
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
            backend_mode="dokobot_read_only",
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
        backend_mode="dokobot_read_only",
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
            backend_mode="dokobot_read_only",
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
        "backend_mode": "dokobot_read_only",
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
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_pi_agent_contracts.py -q
```

Expected: import failure for `seektalent.providers.pi_agent`.

- [x] **Step 3: Create the package marker**

Add `src/seektalent/providers/pi_agent/__init__.py`:

```python
"""Provider interaction agent boundary contracts and runtime helpers."""
```

- [x] **Step 4: Implement contracts**

Add `src/seektalent/providers/pi_agent/contracts.py`:

```python
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AnyUrl, BaseModel, ConfigDict, Field, field_validator, model_validator


NonEmptyStr = Annotated[str, Field(min_length=1)]


class PiAgentTaskType(StrEnum):
    LIEPIN_SEARCH_CARDS = "liepin.search_cards"
    LIEPIN_READ_CARD_PAGE = "liepin.read_card_page"
    LIEPIN_CLASSIFY_CARD_SUMMARY = "liepin.classify_card_summary"
    LIEPIN_REQUEST_DETAIL_OPEN = "liepin.request_detail_open"
    LIEPIN_OPEN_DETAIL_AFTER_APPROVAL = "liepin.open_detail_after_approval"
    LIEPIN_EXTRACT_DETAIL_RESUME = "liepin.extract_detail_resume"
    LIEPIN_DETECT_LOGIN_OR_RISK_STATE = "liepin.detect_login_or_risk_state"


class PiAgentActionType(StrEnum):
    LIEPIN_NAVIGATE_TO_SEARCH = "liepin.navigate_to_search"
    LIEPIN_SUBMIT_KEYWORD_SEARCH = "liepin.submit_keyword_search"
    LIEPIN_READ_CARD_PAGE = "liepin.read_card_page"
    LIEPIN_TURN_PAGE = "liepin.turn_page"
    LIEPIN_CLASSIFY_CARD_SUMMARY = "liepin.classify_card_summary"
    LIEPIN_REQUEST_DETAIL_OPEN = "liepin.request_detail_open"
    LIEPIN_OPEN_DETAIL_AFTER_APPROVAL = "liepin.open_detail_after_approval"
    LIEPIN_EXTRACT_DETAIL_RESUME = "liepin.extract_detail_resume"
    LIEPIN_DETECT_LOGIN_OR_RISK_STATE = "liepin.detect_login_or_risk_state"


class DetailOpenReasonCode(StrEnum):
    STRONG_CARD_MATCH = "strong_card_match"
    HUMAN_SELECTED = "human_selected"
    RUNTIME_RULE_SELECTED = "runtime_rule_selected"
    MANUAL_REVIEW = "manual_review"
    POLICY_SELECTED = "policy_selected"


class PiAgentResultStatus(StrEnum):
    SUCCEEDED = "succeeded"
    NEEDS_APPROVAL = "needs_approval"
    BLOCKED = "blocked"
    FAILED = "failed"
    PARTIAL = "partial"


class PiAgentFailureCode(StrEnum):
    LOGIN_EXPIRED = "login_expired"
    VERIFICATION_REQUIRED = "verification_required"
    RISK_CONTROL = "risk_control"
    SELECTOR_DRIFT = "selector_drift"
    EXTRACTION_FAILURE = "extraction_failure"
    PAGE_TIMEOUT = "page_timeout"
    DOKOBOT_ACTION_CAPABILITY_UNAVAILABLE = "dokobot_action_capability_unavailable"
    DETAIL_OPEN_GRANT_MISSING = "detail_open_grant_missing"
    DETAIL_BUDGET_RESERVATION_FAILED = "detail_budget_reservation_failed"
    DETAIL_OPEN_GRANT_EXPIRED = "detail_open_grant_expired"
    DETAIL_OPEN_GRANT_CANDIDATE_MISMATCH = "detail_open_grant_candidate_mismatch"
    DETAIL_OPEN_GRANT_SOURCE_RUN_MISMATCH = "detail_open_grant_source_run_mismatch"
    DETAIL_OPEN_DUPLICATE = "detail_open_duplicate"
    PROVIDER_CONNECTION_LOCKED = "provider_connection_locked"


class PiAgentCompletionReason(StrEnum):
    PAGE_EXHAUSTED = "page_exhausted"
    ENOUGH_STRONG_CARDS = "enough_strong_cards"
    DETAIL_BUDGET_EXHAUSTED = "detail_budget_exhausted"
    DETAIL_BUDGET_WAITING_FOR_HUMAN = "detail_budget_waiting_for_human"
    COMPLETED = "completed"
    USER_STOPPED = "user_stopped"


class PiBackendMode(StrEnum):
    DISABLED = "disabled"
    DOKOBOT_READ_ONLY = "dokobot_read_only"
    DOKOBOT_ACTION = "dokobot_action"
    LEGACY_WORKER_COMPAT = "legacy_worker_compat"
    FAKE_FIXTURE = "fake_fixture"


class ProtectedArtifactClass(StrEnum):
    SAFE_SUMMARY = "safe_summary_artifact"
    REDACTED_EVIDENCE = "redacted_evidence_artifact"
    PROTECTED_PROVIDER_SNAPSHOT = "protected_provider_snapshot"


PI_MODEL_CONFIG = ConfigDict(extra="forbid", hide_input_in_errors=True)


class PiBoundaryModel(BaseModel):
    model_config = PI_MODEL_CONFIG


def _require_timezone_aware(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


class PiArtifactRef(PiBoundaryModel):
    artifact_class: ProtectedArtifactClass
    artifact_ref: NonEmptyStr
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    redaction_policy_id: str | None = None
    protection_policy_id: str | None = None

    @model_validator(mode="after")
    def validate_ref_and_policy_ids(self) -> "PiArtifactRef":
        if self.artifact_ref.startswith("/") or "://" in self.artifact_ref:
            raise ValueError("artifact_ref must be an opaque artifact store ref, not a path or URI")
        if any(part == ".." for part in self.artifact_ref.split("/")):
            raise ValueError("artifact_ref must not contain path traversal segments")
        if self.artifact_class in {ProtectedArtifactClass.SAFE_SUMMARY, ProtectedArtifactClass.REDACTED_EVIDENCE}:
            if not self.redaction_policy_id:
                raise ValueError("safe summary and redacted evidence artifacts require redaction_policy_id")
            if self.protection_policy_id is not None:
                raise ValueError("safe summary and redacted evidence artifacts must not carry protection_policy_id")
        if self.artifact_class == ProtectedArtifactClass.PROTECTED_PROVIDER_SNAPSHOT:
            if self.redaction_policy_id is not None:
                raise ValueError("protected provider snapshots must not claim redaction_policy_id")
            if not self.protection_policy_id:
                raise ValueError("protected provider snapshots require protection_policy_id")
        return self


class DetailOpenGrant(PiBoundaryModel):
    schema_version: Literal["detail-open-grant-v1"]
    approval_id: NonEmptyStr
    budget_reservation_id: NonEmptyStr
    candidate_ref: NonEmptyStr
    source_run_id: NonEmptyStr
    provider: Literal["liepin"]
    max_detail_opens: int = Field(default=1, ge=1, le=1)
    expires_at: datetime
    issued_by: Literal["workflow_runtime"]
    idempotency_key: NonEmptyStr
    grant_signature: str = Field(min_length=1, repr=False)

    @field_validator("expires_at")
    @classmethod
    def expires_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        return _require_timezone_aware(value, field_name="expires_at")


class PiAgentTaskBase(PiBoundaryModel):
    schema_version: Literal["pi-agent-task-v1"]
    task_type: PiAgentTaskType
    session_id: NonEmptyStr
    source_run_id: NonEmptyStr
    connection_id: NonEmptyStr
    artifact_policy: Literal["protected_snapshots_only"]


class LiepinSearchCardsTask(PiAgentTaskBase):
    task_type: Literal[PiAgentTaskType.LIEPIN_SEARCH_CARDS]
    query_terms: list[NonEmptyStr] = Field(min_length=1)
    keyword_query: str = Field(min_length=1)
    max_pages: int = Field(ge=1, le=20)
    max_cards: int = Field(ge=1, le=500)
    stop_conditions: list[
        Literal["page_exhausted", "enough_strong_cards", "risk_control", "detail_budget_exhausted"]
    ] = Field(min_length=1)


class LiepinReadCardPageTask(PiAgentTaskBase):
    task_type: Literal[PiAgentTaskType.LIEPIN_READ_CARD_PAGE]
    current_url: AnyUrl
    page_index: int = Field(ge=1, le=20)


class LiepinClassifyCardSummaryTask(PiAgentTaskBase):
    task_type: Literal[PiAgentTaskType.LIEPIN_CLASSIFY_CARD_SUMMARY]
    candidate_ref: NonEmptyStr
    summary_ref: NonEmptyStr
    classification_policy_id: NonEmptyStr


class LiepinRequestDetailOpenTask(PiAgentTaskBase):
    task_type: Literal[PiAgentTaskType.LIEPIN_REQUEST_DETAIL_OPEN]
    candidate_ref: NonEmptyStr
    summary_ref: NonEmptyStr
    reason_code: DetailOpenReasonCode


class LiepinOpenDetailAfterApprovalTask(PiAgentTaskBase):
    task_type: Literal[PiAgentTaskType.LIEPIN_OPEN_DETAIL_AFTER_APPROVAL]
    candidate_ref: NonEmptyStr
    detail_open_grant: DetailOpenGrant


class LiepinExtractDetailResumeTask(PiAgentTaskBase):
    task_type: Literal[PiAgentTaskType.LIEPIN_EXTRACT_DETAIL_RESUME]
    candidate_ref: NonEmptyStr
    detail_snapshot_ref: NonEmptyStr


class LiepinDetectLoginOrRiskStateTask(PiAgentTaskBase):
    task_type: Literal[PiAgentTaskType.LIEPIN_DETECT_LOGIN_OR_RISK_STATE]
    current_url: AnyUrl


PiAgentTask = Annotated[
    LiepinSearchCardsTask
    | LiepinReadCardPageTask
    | LiepinClassifyCardSummaryTask
    | LiepinRequestDetailOpenTask
    | LiepinOpenDetailAfterApprovalTask
    | LiepinExtractDetailResumeTask
    | LiepinDetectLoginOrRiskStateTask,
    Field(discriminator="task_type"),
]


class NavigateToSearchInput(PiBoundaryModel):
    query_home_url: AnyUrl


class SubmitKeywordSearchInput(PiBoundaryModel):
    keyword_query: str = Field(min_length=1)
    query_terms: list[NonEmptyStr] = Field(min_length=1)


class ReadCardPageInput(PiBoundaryModel):
    page_index: int = Field(ge=1, le=20)


class TurnPageInput(PiBoundaryModel):
    next_page_index: int = Field(ge=1, le=20)


class ClassifyCardSummaryInput(PiBoundaryModel):
    candidate_ref: NonEmptyStr
    summary_ref: NonEmptyStr
    classification_policy_id: NonEmptyStr


class RequestDetailOpenInput(PiBoundaryModel):
    candidate_ref: NonEmptyStr
    summary_ref: NonEmptyStr
    reason_code: DetailOpenReasonCode


class OpenDetailAfterApprovalInput(PiBoundaryModel):
    candidate_ref: NonEmptyStr
    detail_open_grant: DetailOpenGrant


class ExtractDetailResumeInput(PiBoundaryModel):
    candidate_ref: NonEmptyStr
    detail_snapshot_ref: NonEmptyStr


class DetectLoginOrRiskStateInput(PiBoundaryModel):
    current_url: AnyUrl


class PiAgentActionBase(PiBoundaryModel):
    schema_version: Literal["pi-agent-action-v1"]
    action_type: PiAgentActionType
    target_url: AnyUrl
    safe_target_descriptor: NonEmptyStr


class LiepinNavigateToSearchAction(PiAgentActionBase):
    action_type: Literal[PiAgentActionType.LIEPIN_NAVIGATE_TO_SEARCH]
    input_payload: NavigateToSearchInput


class LiepinSubmitKeywordSearchAction(PiAgentActionBase):
    action_type: Literal[PiAgentActionType.LIEPIN_SUBMIT_KEYWORD_SEARCH]
    input_payload: SubmitKeywordSearchInput


class LiepinReadCardPageAction(PiAgentActionBase):
    action_type: Literal[PiAgentActionType.LIEPIN_READ_CARD_PAGE]
    input_payload: ReadCardPageInput


class LiepinTurnPageAction(PiAgentActionBase):
    action_type: Literal[PiAgentActionType.LIEPIN_TURN_PAGE]
    input_payload: TurnPageInput


class LiepinClassifyCardSummaryAction(PiAgentActionBase):
    action_type: Literal[PiAgentActionType.LIEPIN_CLASSIFY_CARD_SUMMARY]
    input_payload: ClassifyCardSummaryInput


class LiepinRequestDetailOpenAction(PiAgentActionBase):
    action_type: Literal[PiAgentActionType.LIEPIN_REQUEST_DETAIL_OPEN]
    input_payload: RequestDetailOpenInput


class LiepinOpenDetailAfterApprovalAction(PiAgentActionBase):
    action_type: Literal[PiAgentActionType.LIEPIN_OPEN_DETAIL_AFTER_APPROVAL]
    input_payload: OpenDetailAfterApprovalInput


class LiepinExtractDetailResumeAction(PiAgentActionBase):
    action_type: Literal[PiAgentActionType.LIEPIN_EXTRACT_DETAIL_RESUME]
    input_payload: ExtractDetailResumeInput


class LiepinDetectLoginOrRiskStateAction(PiAgentActionBase):
    action_type: Literal[PiAgentActionType.LIEPIN_DETECT_LOGIN_OR_RISK_STATE]
    input_payload: DetectLoginOrRiskStateInput


PiAgentAction = Annotated[
    LiepinNavigateToSearchAction
    | LiepinSubmitKeywordSearchAction
    | LiepinReadCardPageAction
    | LiepinTurnPageAction
    | LiepinClassifyCardSummaryAction
    | LiepinRequestDetailOpenAction
    | LiepinOpenDetailAfterApprovalAction
    | LiepinExtractDetailResumeAction
    | LiepinDetectLoginOrRiskStateAction,
    Field(discriminator="action_type"),
]


class PiAgentActionTraceEntry(PiBoundaryModel):
    schema_version: Literal["pi-agent-action-trace-v1"]
    timestamp: datetime
    provider_skill_id: NonEmptyStr
    interaction_id: NonEmptyStr
    source_run_id: NonEmptyStr
    connection_id: NonEmptyStr
    action_sequence: int = Field(ge=1)
    action_type: PiAgentActionType
    backend_mode: PiBackendMode
    capability_version: NonEmptyStr
    safe_target_descriptor: NonEmptyStr
    result_code: Literal["ok", "blocked", "failed", "partial"]
    duration_ms: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    redaction_policy_id: NonEmptyStr
    redacted_evidence_ref: NonEmptyStr | None
    evidence_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    failure_code: PiAgentFailureCode | None = None

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        return _require_timezone_aware(value, field_name="timestamp")

    @model_validator(mode="after")
    def validate_trace_consistency(self) -> "PiAgentActionTraceEntry":
        if self.result_code == "ok" and self.failure_code is not None:
            raise ValueError("ok trace cannot carry failure_code")
        if self.result_code in {"blocked", "failed"} and self.failure_code is None:
            raise ValueError("blocked/failed trace requires failure_code")
        if bool(self.redacted_evidence_ref) != bool(self.evidence_sha256):
            raise ValueError("redacted_evidence_ref and evidence_sha256 must appear together")
        return self


class PiAgentResult(PiBoundaryModel):
    schema_version: Literal["pi-agent-result-v1"]
    status: PiAgentResultStatus
    cards_seen: int = Field(default=0, ge=0)
    cards_selected: int = Field(default=0, ge=0)
    detail_requests: int = Field(default=0, ge=0)
    details_opened: int = Field(default=0, ge=0)
    stop_reason: PiAgentFailureCode | PiAgentCompletionReason | None = None
    action_trace_ref: PiArtifactRef
    protected_snapshot_refs: list[PiArtifactRef] = Field(default_factory=list)
    safe_summary_refs: list[PiArtifactRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_result_contract(self) -> "PiAgentResult":
        if self.action_trace_ref.artifact_class != ProtectedArtifactClass.REDACTED_EVIDENCE:
            raise ValueError("action_trace_ref must be redacted_evidence_artifact")
        if self.status in {PiAgentResultStatus.BLOCKED, PiAgentResultStatus.FAILED}:
            if not isinstance(self.stop_reason, PiAgentFailureCode):
                raise ValueError("blocked/failed results require a failure stop_reason")
        if self.status == PiAgentResultStatus.SUCCEEDED:
            if self.stop_reason is not None and not isinstance(self.stop_reason, PiAgentCompletionReason):
                raise ValueError("succeeded results cannot use failure stop_reason")
        if self.status == PiAgentResultStatus.NEEDS_APPROVAL:
            if self.stop_reason != PiAgentCompletionReason.DETAIL_BUDGET_WAITING_FOR_HUMAN:
                raise ValueError("needs_approval requires detail_budget_waiting_for_human")
        if self.status == PiAgentResultStatus.PARTIAL and self.stop_reason is None:
            raise ValueError("partial results require stop_reason")
        for ref in self.protected_snapshot_refs:
            if ref.artifact_class != ProtectedArtifactClass.PROTECTED_PROVIDER_SNAPSHOT:
                raise ValueError("protected_snapshot_refs must only contain protected_provider_snapshot")
        for ref in self.safe_summary_refs:
            if ref.artifact_class != ProtectedArtifactClass.SAFE_SUMMARY:
                raise ValueError("safe_summary_refs must only contain safe_summary_artifact")
        return self
```

- [x] **Step 5: Run contract tests and type check**

```bash
uv run pytest tests/test_pi_agent_contracts.py -q
uv run ty check src/seektalent/providers/pi_agent/contracts.py
```

Expected: pass.

`ty` is still a 0.0.x tool in this repo, and `pyproject.toml` currently ignores `tests/**` for ty. Treat pytest as the required gate for test behavior; use ty here only for the new `src/` contract module. If `ty` reports a new beta diagnostic that does not correspond to a real contract bug, pinning/escalation belongs in the production guardrails plan, not in a silent local workaround.

- [ ] **Step 6: Commit contract layer**

```bash
git add src/seektalent/providers/pi_agent/__init__.py src/seektalent/providers/pi_agent/contracts.py tests/test_pi_agent_contracts.py
git commit -m "feat: add pi agent contracts"
```

### Task 2: Add Liepin Skill Recipes

**Files:**
- Create: `src/seektalent/providers/liepin/pi_skills.py`
- Test: `tests/test_liepin_pi_skills.py`

- [x] **Step 1: Write failing skill recipe tests**

Add `tests/test_liepin_pi_skills.py`:

```python
import pytest

from seektalent.providers.liepin.pi_skills import (
    DIRECT_REQUEST_FORBIDDEN_ACTIONS,
    get_liepin_pi_skill,
    is_liepin_skill_url_allowed,
)
from seektalent.providers.pi_agent.contracts import (
    PiAgentActionType,
    PiAgentCompletionReason,
    PiAgentFailureCode,
    PiAgentTaskType,
)


def test_search_skill_has_route_redaction_failure_pacing_and_evidence() -> None:
    skill = get_liepin_pi_skill(PiAgentTaskType.LIEPIN_SEARCH_CARDS)

    assert skill.skill_id == "liepin.search_cards.v1"
    assert skill.task_type == PiAgentTaskType.LIEPIN_SEARCH_CARDS
    assert skill.allowed_url_hosts == ("www.liepin.com", "h.liepin.com")
    assert "/zhaopin/" in skill.pre_action_allowed_route_patterns
    assert "/zhaopin/" in skill.post_action_expected_route_patterns
    assert skill.redaction_policy_id == "liepin-card-redaction-v1"
    assert PiAgentFailureCode.RISK_CONTROL in skill.failure_codes
    assert PiAgentCompletionReason.PAGE_EXHAUSTED in skill.completion_reasons
    assert skill.pacing_policy_id == "liepin-search-pacing-v1"
    assert skill.evidence_requirement == "redacted_text_snapshot"


def test_detail_skill_requires_runtime_grant_and_redacted_evidence() -> None:
    skill = get_liepin_pi_skill(PiAgentTaskType.LIEPIN_OPEN_DETAIL_AFTER_APPROVAL)

    assert skill.requires_detail_approval is True
    assert skill.requires_runtime_grant is True
    assert skill.evidence_requirement == "redacted_text_snapshot"
    assert PiAgentFailureCode.DETAIL_OPEN_GRANT_MISSING in skill.failure_codes
    assert skill.allowed_actions == (PiAgentActionType.LIEPIN_OPEN_DETAIL_AFTER_APPROVAL,)
    assert skill.pre_action_allowed_route_patterns == ("/zhaopin/", "/lptjob/")
    assert skill.post_action_expected_route_patterns == ("/resume/showresumedetail/", "/candidate/detail/")


def test_all_skills_forbid_direct_authenticated_request_replay() -> None:
    for task_type in PiAgentTaskType:
        skill = get_liepin_pi_skill(task_type)
        for forbidden in DIRECT_REQUEST_FORBIDDEN_ACTIONS:
            assert forbidden in skill.forbidden_actions

    assert "list_network_requests" in DIRECT_REQUEST_FORBIDDEN_ACTIONS
    assert "get_network_request" in DIRECT_REQUEST_FORBIDDEN_ACTIONS
    assert "evaluate_script" in DIRECT_REQUEST_FORBIDDEN_ACTIONS


def test_every_task_type_has_skill_recipe() -> None:
    for task_type in PiAgentTaskType:
        assert get_liepin_pi_skill(task_type).task_type == task_type


def test_skill_recipes_use_contract_enums_not_free_strings() -> None:
    for task_type in PiAgentTaskType:
        skill = get_liepin_pi_skill(task_type)
        assert isinstance(skill.task_type, PiAgentTaskType)
        assert all(isinstance(action, PiAgentActionType) for action in skill.allowed_actions)
        assert all(isinstance(code, PiAgentFailureCode) for code in skill.failure_codes)
        assert all(isinstance(reason, PiAgentCompletionReason) for reason in skill.completion_reasons)


def test_unknown_skill_raises_key_error() -> None:
    with pytest.raises(KeyError):
        get_liepin_pi_skill("liepin.unknown")


def test_skill_url_matcher_rejects_non_liepin_host_and_api_routes() -> None:
    skill = get_liepin_pi_skill(PiAgentTaskType.LIEPIN_SEARCH_CARDS)

    assert is_liepin_skill_url_allowed(skill, "https://www.liepin.com/zhaopin/")
    assert not is_liepin_skill_url_allowed(skill, "https://evil.com/zhaopin/")
    assert not is_liepin_skill_url_allowed(skill, "https://www.liepin.com/api/search")
    assert not is_liepin_skill_url_allowed(skill, "https://www.liepin.com/zhaopin/api/search")
    assert not is_liepin_skill_url_allowed(skill, "https://www.liepin.com/lptjob/ajax/page")


def test_root_route_pattern_only_matches_root_path() -> None:
    skill = get_liepin_pi_skill(PiAgentTaskType.LIEPIN_DETECT_LOGIN_OR_RISK_STATE)

    assert is_liepin_skill_url_allowed(skill, "https://passport.liepin.com/")
    assert not is_liepin_skill_url_allowed(skill, "https://passport.liepin.com/sensitive/unexpected")


def test_open_detail_skill_uses_pre_and_post_route_phases() -> None:
    skill = get_liepin_pi_skill(PiAgentTaskType.LIEPIN_OPEN_DETAIL_AFTER_APPROVAL)

    assert is_liepin_skill_url_allowed(skill, "https://www.liepin.com/zhaopin/", phase="pre")
    assert not is_liepin_skill_url_allowed(skill, "https://www.liepin.com/zhaopin/", phase="post")
    assert is_liepin_skill_url_allowed(skill, "https://www.liepin.com/resume/showresumedetail/123", phase="post")
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_liepin_pi_skills.py -q
```

Expected: import failure for `seektalent.providers.liepin.pi_skills`.

- [x] **Step 3: Implement the skill registry**

Add `src/seektalent/providers/liepin/pi_skills.py`:

```python
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from seektalent.providers.pi_agent.contracts import (
    PiAgentActionType,
    PiAgentCompletionReason,
    PiAgentFailureCode,
    PiAgentTaskType,
)


EvidenceRequirement = Literal["redacted_text_snapshot", "redacted_visual_snapshot", "action_trace_only"]

DIRECT_REQUEST_FORBIDDEN_ACTIONS = (
    "page.request",
    "browserContext.request",
    "APIRequestContext",
    "list_network_requests",
    "get_network_request",
    "evaluate_script",
    "provider_signature_generation",
    "stealth_plugin",
    "proxy_rotation",
    "header_or_cookie_injection",
)


FORBIDDEN_ROUTE_SEGMENTS = {"api", "ajax"}


@dataclass(frozen=True)
class LiepinPiSkill:
    skill_id: str
    task_type: PiAgentTaskType
    allowed_url_hosts: tuple[str, ...]
    pre_action_allowed_route_patterns: tuple[str, ...]
    post_action_expected_route_patterns: tuple[str, ...]
    allowed_actions: tuple[PiAgentActionType, ...]
    forbidden_actions: tuple[str, ...]
    output_schema_version: str
    redaction_policy_id: str
    failure_codes: tuple[PiAgentFailureCode, ...]
    completion_reasons: tuple[PiAgentCompletionReason, ...]
    pacing_policy_id: str
    evidence_requirement: EvidenceRequirement
    risk_circuit_breaker: str
    max_attempts: int = 1
    requires_detail_approval: bool = False
    requires_runtime_grant: bool = False


_COMMON_FAILURE_CODES = (
    PiAgentFailureCode.LOGIN_EXPIRED,
    PiAgentFailureCode.VERIFICATION_REQUIRED,
    PiAgentFailureCode.RISK_CONTROL,
    PiAgentFailureCode.SELECTOR_DRIFT,
    PiAgentFailureCode.PAGE_TIMEOUT,
)

_SEARCH_COMPLETION_REASONS = (
    PiAgentCompletionReason.PAGE_EXHAUSTED,
    PiAgentCompletionReason.ENOUGH_STRONG_CARDS,
    PiAgentCompletionReason.DETAIL_BUDGET_EXHAUSTED,
    PiAgentCompletionReason.DETAIL_BUDGET_WAITING_FOR_HUMAN,
)

_SKILLS = {
    PiAgentTaskType.LIEPIN_SEARCH_CARDS: LiepinPiSkill(
        skill_id="liepin.search_cards.v1",
        task_type=PiAgentTaskType.LIEPIN_SEARCH_CARDS,
        allowed_url_hosts=("www.liepin.com", "h.liepin.com"),
        pre_action_allowed_route_patterns=("/zhaopin/", "/lptjob/"),
        post_action_expected_route_patterns=("/zhaopin/", "/lptjob/"),
        allowed_actions=(
            PiAgentActionType.LIEPIN_NAVIGATE_TO_SEARCH,
            PiAgentActionType.LIEPIN_SUBMIT_KEYWORD_SEARCH,
            PiAgentActionType.LIEPIN_READ_CARD_PAGE,
            PiAgentActionType.LIEPIN_TURN_PAGE,
            PiAgentActionType.LIEPIN_CLASSIFY_CARD_SUMMARY,
            PiAgentActionType.LIEPIN_REQUEST_DETAIL_OPEN,
        ),
        forbidden_actions=DIRECT_REQUEST_FORBIDDEN_ACTIONS,
        output_schema_version="pi-agent-result-v1",
        redaction_policy_id="liepin-card-redaction-v1",
        failure_codes=_COMMON_FAILURE_CODES,
        completion_reasons=_SEARCH_COMPLETION_REASONS,
        pacing_policy_id="liepin-search-pacing-v1",
        evidence_requirement="redacted_text_snapshot",
        risk_circuit_breaker="liepin-risk-stop-v1",
        max_attempts=1,
    ),
    PiAgentTaskType.LIEPIN_READ_CARD_PAGE: LiepinPiSkill(
        skill_id="liepin.read_card_page.v1",
        task_type=PiAgentTaskType.LIEPIN_READ_CARD_PAGE,
        allowed_url_hosts=("www.liepin.com", "h.liepin.com"),
        pre_action_allowed_route_patterns=("/zhaopin/", "/lptjob/"),
        post_action_expected_route_patterns=("/zhaopin/", "/lptjob/"),
        allowed_actions=(PiAgentActionType.LIEPIN_READ_CARD_PAGE,),
        forbidden_actions=DIRECT_REQUEST_FORBIDDEN_ACTIONS,
        output_schema_version="pi-agent-result-v1",
        redaction_policy_id="liepin-card-redaction-v1",
        failure_codes=_COMMON_FAILURE_CODES,
        completion_reasons=(PiAgentCompletionReason.PAGE_EXHAUSTED,),
        pacing_policy_id="liepin-read-page-pacing-v1",
        evidence_requirement="redacted_text_snapshot",
        risk_circuit_breaker="liepin-risk-stop-v1",
        max_attempts=1,
    ),
    PiAgentTaskType.LIEPIN_CLASSIFY_CARD_SUMMARY: LiepinPiSkill(
        skill_id="liepin.classify_card_summary.v1",
        task_type=PiAgentTaskType.LIEPIN_CLASSIFY_CARD_SUMMARY,
        allowed_url_hosts=("www.liepin.com", "h.liepin.com"),
        pre_action_allowed_route_patterns=("/zhaopin/", "/lptjob/"),
        post_action_expected_route_patterns=("/zhaopin/", "/lptjob/"),
        allowed_actions=(PiAgentActionType.LIEPIN_CLASSIFY_CARD_SUMMARY,),
        forbidden_actions=DIRECT_REQUEST_FORBIDDEN_ACTIONS,
        output_schema_version="pi-agent-result-v1",
        redaction_policy_id="liepin-card-redaction-v1",
        failure_codes=(*_COMMON_FAILURE_CODES, PiAgentFailureCode.EXTRACTION_FAILURE),
        completion_reasons=(),
        pacing_policy_id="liepin-classify-card-pacing-v1",
        evidence_requirement="redacted_text_snapshot",
        risk_circuit_breaker="liepin-risk-stop-v1",
        max_attempts=1,
    ),
    PiAgentTaskType.LIEPIN_REQUEST_DETAIL_OPEN: LiepinPiSkill(
        skill_id="liepin.request_detail_open.v1",
        task_type=PiAgentTaskType.LIEPIN_REQUEST_DETAIL_OPEN,
        allowed_url_hosts=("www.liepin.com", "h.liepin.com"),
        pre_action_allowed_route_patterns=("/zhaopin/", "/lptjob/"),
        post_action_expected_route_patterns=("/zhaopin/", "/lptjob/"),
        allowed_actions=(PiAgentActionType.LIEPIN_REQUEST_DETAIL_OPEN,),
        forbidden_actions=DIRECT_REQUEST_FORBIDDEN_ACTIONS,
        output_schema_version="pi-agent-result-v1",
        redaction_policy_id="liepin-card-redaction-v1",
        failure_codes=(*_COMMON_FAILURE_CODES, PiAgentFailureCode.DETAIL_BUDGET_RESERVATION_FAILED),
        completion_reasons=(PiAgentCompletionReason.DETAIL_BUDGET_WAITING_FOR_HUMAN,),
        pacing_policy_id="liepin-request-detail-pacing-v1",
        evidence_requirement="action_trace_only",
        risk_circuit_breaker="liepin-risk-stop-v1",
        max_attempts=1,
    ),
    PiAgentTaskType.LIEPIN_OPEN_DETAIL_AFTER_APPROVAL: LiepinPiSkill(
        skill_id="liepin.open_detail_after_approval.v1",
        task_type=PiAgentTaskType.LIEPIN_OPEN_DETAIL_AFTER_APPROVAL,
        allowed_url_hosts=("www.liepin.com", "h.liepin.com"),
        pre_action_allowed_route_patterns=("/zhaopin/", "/lptjob/"),
        post_action_expected_route_patterns=("/resume/showresumedetail/", "/candidate/detail/"),
        allowed_actions=(PiAgentActionType.LIEPIN_OPEN_DETAIL_AFTER_APPROVAL,),
        forbidden_actions=DIRECT_REQUEST_FORBIDDEN_ACTIONS,
        output_schema_version="pi-agent-result-v1",
        redaction_policy_id="liepin-detail-redaction-v1",
        failure_codes=(
            *_COMMON_FAILURE_CODES,
            PiAgentFailureCode.DETAIL_OPEN_GRANT_MISSING,
            PiAgentFailureCode.DETAIL_OPEN_GRANT_EXPIRED,
            PiAgentFailureCode.DETAIL_OPEN_DUPLICATE,
        ),
        completion_reasons=(PiAgentCompletionReason.COMPLETED,),
        pacing_policy_id="liepin-detail-pacing-v1",
        evidence_requirement="redacted_text_snapshot",
        risk_circuit_breaker="liepin-risk-stop-v1",
        max_attempts=1,
        requires_detail_approval=True,
        requires_runtime_grant=True,
    ),
    PiAgentTaskType.LIEPIN_EXTRACT_DETAIL_RESUME: LiepinPiSkill(
        skill_id="liepin.extract_detail_resume.v1",
        task_type=PiAgentTaskType.LIEPIN_EXTRACT_DETAIL_RESUME,
        allowed_url_hosts=("www.liepin.com", "h.liepin.com"),
        pre_action_allowed_route_patterns=("/resume/showresumedetail/", "/candidate/detail/"),
        post_action_expected_route_patterns=("/resume/showresumedetail/", "/candidate/detail/"),
        allowed_actions=(PiAgentActionType.LIEPIN_EXTRACT_DETAIL_RESUME,),
        forbidden_actions=DIRECT_REQUEST_FORBIDDEN_ACTIONS,
        output_schema_version="pi-agent-result-v1",
        redaction_policy_id="liepin-detail-redaction-v1",
        failure_codes=(*_COMMON_FAILURE_CODES, PiAgentFailureCode.EXTRACTION_FAILURE),
        completion_reasons=(PiAgentCompletionReason.COMPLETED,),
        pacing_policy_id="liepin-extract-detail-pacing-v1",
        evidence_requirement="redacted_text_snapshot",
        risk_circuit_breaker="liepin-risk-stop-v1",
        max_attempts=1,
    ),
    PiAgentTaskType.LIEPIN_DETECT_LOGIN_OR_RISK_STATE: LiepinPiSkill(
        skill_id="liepin.detect_login_or_risk_state.v1",
        task_type=PiAgentTaskType.LIEPIN_DETECT_LOGIN_OR_RISK_STATE,
        allowed_url_hosts=("www.liepin.com", "h.liepin.com", "passport.liepin.com"),
        pre_action_allowed_route_patterns=("/", "/login/", "/zhaopin/"),
        post_action_expected_route_patterns=("/", "/login/", "/zhaopin/"),
        allowed_actions=(PiAgentActionType.LIEPIN_DETECT_LOGIN_OR_RISK_STATE,),
        forbidden_actions=DIRECT_REQUEST_FORBIDDEN_ACTIONS,
        output_schema_version="pi-agent-result-v1",
        redaction_policy_id="liepin-state-redaction-v1",
        failure_codes=_COMMON_FAILURE_CODES,
        completion_reasons=(PiAgentCompletionReason.COMPLETED,),
        pacing_policy_id="liepin-state-pacing-v1",
        evidence_requirement="action_trace_only",
        risk_circuit_breaker="liepin-risk-stop-v1",
        max_attempts=1,
    ),
}


def get_liepin_pi_skill(name: PiAgentTaskType | str) -> LiepinPiSkill:
    try:
        task_type = PiAgentTaskType(name)
    except ValueError as error:
        raise KeyError(name) from error
    return _SKILLS[task_type]


def _route_matches(path: str, patterns: tuple[str, ...]) -> bool:
    normalized = path or "/"
    for pattern in patterns:
        if pattern == "/":
            if normalized == "/":
                return True
            continue
        stripped = pattern.rstrip("/")
        if normalized == stripped or normalized == pattern or normalized.startswith(pattern):
            return True
    return False


def _has_forbidden_route_segment(path: str) -> bool:
    segments = {segment.lower() for segment in path.split("/") if segment}
    return bool(segments & FORBIDDEN_ROUTE_SEGMENTS)


def is_liepin_skill_url_allowed(skill: LiepinPiSkill, url: str, *, phase: Literal["pre", "post"] = "pre") -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.hostname not in skill.allowed_url_hosts:
        return False
    if _has_forbidden_route_segment(parsed.path or "/"):
        return False
    patterns = skill.pre_action_allowed_route_patterns
    if phase == "post":
        patterns = skill.post_action_expected_route_patterns
    return _route_matches(parsed.path or "/", patterns)
```

- [x] **Step 4: Run skill tests and type check**

```bash
uv run pytest tests/test_liepin_pi_skills.py -q
uv run ty check src/seektalent/providers/liepin/pi_skills.py
```

Expected: pass.

`ty` remains a secondary `src/` check here for the same reason as Task 1: keep it visible, but do not imply ty validates tests while `tests/**` is ignored by repo config.

- [ ] **Step 5: Commit skill recipes**

```bash
git add src/seektalent/providers/liepin/pi_skills.py tests/test_liepin_pi_skills.py
git commit -m "feat: register liepin pi skill recipes"
```

## Self-Review

- Spec coverage: all declared PI task types, all declared PI action types, request-detail-open action, runtime detail grants, explicit schema versions, non-empty identity fields, grant signatures, hidden validation inputs, failure codes, completion stop reasons, backend modes, artifact refs, artifact ref safety, artifact class policies, timezone-aware audit timestamps, action trace consistency, and skill recipes are covered.
- Placeholder scan: every task names concrete files, tests, commands, expected outcomes, and complete code.
- Type consistency: `pi_skills.py` imports task, action, and failure enums from `contracts.py`; pre/post route fields and API/AJAX-like route denials are tested through the URL matcher, so skill recipe drift is caught by tests and type checks.
