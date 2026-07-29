from __future__ import annotations

import ast
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from seektalent.diagnostics_schema import (
    canonical_diagnostics_bytes,
    parse_failure_envelope,
)
from seektalent.source_port.authenticated_verify_session_frames import (
    PostHandshakeVerifySessionSession,
    ReceivedVerifySessionResult,
    VerifySessionAcceptedAckV1,
    require_authenticated_verify_session_result,
)
from seektalent.source_port.operation_dispatch import (
    OperationIdentityV1,
    RelativeMonotonicDeadlineV1,
)
from seektalent.source_port.verify_session_contract import (
    VerifySessionResultV1,
    VerifySessionUserActionV1,
)
from seektalent.user_action import (
    USER_ACTION_INSTRUCTIONS,
    USER_ACTION_SCOPES,
    UserActionV1,
)
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.models import RuntimeCheckpoint, RuntimeRunRecord
from seektalent_runtime_control.checkpoint_participant import (
    write_checkpoint_participant,
)
from seektalent_runtime_control.store import RUNTIME_CONTROL_SCHEMA_VERSION, RuntimeControlStore
from seektalent_runtime_control.source_reconciliation import (
    SourceOperationReconciliationDecision,
)
from seektalent_runtime_control.user_action_mapping import (
    map_verify_session_user_action,
)
from tests.test_diagnostics_schema import _failure


def test_needs_attention_apis_have_zero_production_callers() -> None:
    root = Path(__file__).parents[1] / "src"
    api_names = {
        "admit_action_satisfaction",
        "admit_needs_attention",
        "commit_needs_attention",
        "resolve_needs_attention",
        "cancel_needs_attention",
        "fail_needs_attention",
        "write_checkpoint_for_recovery",
    }
    callers: list[str] = []
    for path in root.rglob("*.py"):
        if (
            path.parent.name == "seektalent_runtime_control"
            and path.name in {"store.py", "needs_attention_store.py"}
        ):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else None
            )
            if name in api_names:
                callers.append(f"{path.relative_to(root)}:{node.lineno}")
    assert callers == []


RUN_ID = "3" * 32
OPERATION_ID = "4" * 32
RESOLUTION_OPERATION_ID = "e" * 32
CHECKPOINT_ID = "8" * 32
ACTION_ID = "9" * 32
ENTERED_AT = "2026-07-27T04:00:00Z"
RESOLVED_AT = "2026-07-27T04:05:00Z"
MAIN_TO_SIDECAR_KEY = bytes(range(32))
SIDECAR_TO_MAIN_KEY = bytes(range(32, 64))


def _downgrade_v15_to_v14(conn: sqlite3.Connection) -> None:
    for trigger in (
        "runtime_action_checkpoints_delete_forbidden",
        "runtime_action_checkpoints_update_forbidden",
        "runtime_authenticated_observations_delete_forbidden",
        "runtime_authenticated_observations_immutable",
        "runtime_user_actions_delete_forbidden",
        "runtime_user_actions_one_way_resolution",
        "runtime_user_actions_immutable_binding",
    ):
        conn.execute(f"DROP TRIGGER {trigger}")
    conn.execute("DROP INDEX idx_runtime_user_actions_run_created")
    conn.execute("DROP INDEX idx_runtime_user_actions_one_pending")
    conn.execute("DROP TABLE runtime_control_user_actions")
    conn.execute("DROP TABLE runtime_control_authenticated_observations")
    conn.execute(
        "ALTER TABLE runtime_control_runs DROP COLUMN current_action_id"
    )
    conn.execute("PRAGMA user_version = 14")


def _action(code: str = "open_liepin_host") -> UserActionV1:
    return map_verify_session_user_action(
        VerifySessionUserActionV1(
            code={
                "open_liepin_host": "liepin_host_tab_missing",
                "complete_identity_check": "liepin_opencli_identity_intercept",
                "log_in_to_liepin": "liepin_opencli_login_required",
                "complete_liepin_risk_check": "liepin_opencli_risk_page",
                "resolve_liepin_modal": "liepin_opencli_unknown_modal",
            }[code],
            instruction_key={
                "open_liepin_host": "verify_session.open_liepin_host",
                "complete_identity_check": "verify_session.complete_identity_check",
                "log_in_to_liepin": "verify_session.log_in",
                "complete_liepin_risk_check": "verify_session.complete_risk_check",
                "resolve_liepin_modal": "verify_session.dismiss_or_resolve_modal",
            }[code],
        ),
        affected_scope_ref="6" * 32,
    )


def _verify_result(
    *,
    ready: bool,
    code: str = "open_liepin_host",
) -> VerifySessionResultV1:
    source_action = None
    if not ready:
        canonical = _action(code)
        source_action = VerifySessionUserActionV1(
            code={
                "open_liepin_host": "liepin_host_tab_missing",
                "complete_identity_check": "liepin_opencli_identity_intercept",
                "log_in_to_liepin": "liepin_opencli_login_required",
                "complete_liepin_risk_check": "liepin_opencli_risk_page",
                "resolve_liepin_modal": "liepin_opencli_unknown_modal",
            }[canonical.code],
            instruction_key={
                "open_liepin_host": "verify_session.open_liepin_host",
                "complete_identity_check": "verify_session.complete_identity_check",
                "log_in_to_liepin": "verify_session.log_in",
                "complete_liepin_risk_check": "verify_session.complete_risk_check",
                "resolve_liepin_modal": "verify_session.dismiss_or_resolve_modal",
            }[canonical.code],
        )
    identity = OperationIdentityV1(
        run_id=RUN_ID,
        operation_id=OPERATION_ID,
        attempt_no=1,
        source="liepin",
        operation_kind="verify_session",
        request_hash="a" * 64,
        idempotency_key="verify-session-action",
        correlation_id="2" * 32,
        accepted_requirement_revision_id="reqapproved_test",
        runtime_attempt_fence_ref="b" * 64,
        profile_binding_generation=1,
        browser_control_scope_id="6" * 32,
        deadline=RelativeMonotonicDeadlineV1(
            value=30_000,
            clock="relative_monotonic",
            unit="milliseconds",
        ),
        expected_source_operation_ledger_revision=1,
        expected_reconciliation_revision=0,
    )
    return VerifySessionResultV1(
        contract_version="seektalent.source.verify-session.result/v1",
        identity=identity,
        process_readiness="ready",
        bridge_readiness="ready",
        extension_readiness="ready",
        profile_lock_readiness="ready",
        account_readiness="ready",
        search_surface_readiness="ready" if ready else "not_ready",
        risk_state="clear",
        session_readiness="ready" if ready else "not_ready",
        actual_profile_binding_ref="5" * 32,
        actual_provider_account_ref="4" * 32,
        actual_profile_binding_generation=2,
        safe_reason_code=None if ready else source_action.code,
        user_action=source_action,
        component_receipt_refs=(),
    )


def _authenticated_result(
    *,
    ready: bool,
    code: str = "open_liepin_host",
    attempt_no: int = 1,
    requirement_revision_id: str = "reqapproved_test",
    browser_scope_id: str = "6" * 32,
    profile_generation: int = 1,
    session_suffix: str = "entry",
    operation_id: str = OPERATION_ID,
    idempotency_key: str = "verify-session-action",
    dispatch_intent_id: str = "dispatch-intent-action",
    dispatch_intent_revision: int = 1,
    source_operation_acceptance_ref: str = "source-acceptance-action",
    dispatch_authorization_ordinal: int = 1,
    expected_ledger_revision: int = 1,
    expected_reconciliation_revision: int = 0,
    safe_retry_commit_ref: str | None = None,
) -> ReceivedVerifySessionResult:
    from seektalent.source_port.verify_session_contract import (
        VerifySessionRequestV1,
    )

    request = VerifySessionRequestV1.create(
        run_id=RUN_ID,
        operation_id=operation_id,
        attempt_no=attempt_no,
        idempotency_key=idempotency_key,
        correlation_id=f"correlation-{session_suffix}",
        accepted_requirement_revision_id=requirement_revision_id,
        runtime_attempt_fence_token=(
            f"fence-token-{attempt_no}-{profile_generation}-{browser_scope_id}"
        ),
        profile_binding_generation=profile_generation,
        browser_control_scope_id=browser_scope_id,
        deadline_value=30_000,
        expected_source_operation_ledger_revision=expected_ledger_revision,
        expected_reconciliation_revision=expected_reconciliation_revision,
        delivery_mode="initial",
        dispatch_intent_id=dispatch_intent_id,
        dispatch_intent_revision=dispatch_intent_revision,
        dispatch_authorization_ordinal=dispatch_authorization_ordinal,
        safe_retry_commit_ref=safe_retry_commit_ref,
        source_operation_acceptance_ref=source_operation_acceptance_ref,
        profile_binding_ref="5" * 32,
        provider_account_ref="4" * 32,
        required_capabilities=(
            "bridge",
            "extension",
            "profile_lock",
            "search_surface",
        ),
        user_interaction_policy="observe_only",
        verify_search_surface=True,
    )
    source_action = None
    if not ready:
        canonical = _action(code)
        source_action = VerifySessionUserActionV1(
            code={
                "open_liepin_host": "liepin_host_tab_missing",
                "complete_identity_check": "liepin_opencli_identity_intercept",
                "log_in_to_liepin": "liepin_opencli_login_required",
                "complete_liepin_risk_check": "liepin_opencli_risk_page",
                "resolve_liepin_modal": "liepin_opencli_unknown_modal",
            }[canonical.code],
            instruction_key={
                "open_liepin_host": "verify_session.open_liepin_host",
                "complete_identity_check": "verify_session.complete_identity_check",
                "log_in_to_liepin": "verify_session.log_in",
                "complete_liepin_risk_check": "verify_session.complete_risk_check",
                "resolve_liepin_modal": "verify_session.dismiss_or_resolve_modal",
            }[canonical.code],
        )
    result = VerifySessionResultV1(
        contract_version="seektalent.source.verify-session.result/v1",
        identity=request.identity,
        process_readiness="ready",
        bridge_readiness="ready",
        extension_readiness="ready",
        profile_lock_readiness="ready",
        account_readiness="ready",
        search_surface_readiness="ready" if ready else "not_ready",
        risk_state="clear",
        session_readiness="ready" if ready else "not_ready",
        actual_profile_binding_ref="5" * 32,
        actual_provider_account_ref="4" * 32,
        actual_profile_binding_generation=profile_generation,
        safe_reason_code=None if ready else source_action.code,
        user_action=source_action,
        component_receipt_refs=(),
    )
    main = PostHandshakeVerifySessionSession.for_main(
        session_id=f"session-{session_suffix}",
        protocol_minor=0,
        main_to_sidecar_key=MAIN_TO_SIDECAR_KEY,
        sidecar_to_main_key=SIDECAR_TO_MAIN_KEY,
    )
    sidecar = PostHandshakeVerifySessionSession.for_sidecar(
        session_id=f"session-{session_suffix}",
        protocol_minor=0,
        main_to_sidecar_key=MAIN_TO_SIDECAR_KEY,
        sidecar_to_main_key=SIDECAR_TO_MAIN_KEY,
    )
    submit = main.encode_submit(
        message_id=f"submit-{session_suffix}",
        correlation_id=f"correlation-{session_suffix}",
        payload=request,
    )
    sidecar.feed(submit)
    ack = VerifySessionAcceptedAckV1(
        contract_version="seektalent.source.verify-session.accepted-ack/v1",
        identity=request.identity,
        dispatch_authorization=request.delivery.authorization,
        accepted_generation=1,
        accepted_journal_revision=1,
        accepted_fact=(
            "dispatch_authorized"
            if dispatch_authorization_ordinal == 1
            else "accepted_no_dispatch"
        ),
    )
    main.feed(
        sidecar.encode_accepted_ack(
            message_id=f"ack-{session_suffix}",
            reply_to=f"submit-{session_suffix}",
            payload=ack,
        )
    )
    [received] = main.feed(
        sidecar.encode_result(
            message_id=f"result-{session_suffix}",
            reply_to=f"submit-{session_suffix}",
            payload=result,
        )
    )
    assert isinstance(received, ReceivedVerifySessionResult)
    return received


def _envelope(
    *,
    outcome: str = "needs_attention",
    action: UserActionV1 | None = None,
    failure_id: str = "7" * 32,
    revision: int = 1,
    occurred_at: str = ENTERED_AT,
    observed_at: str | None = None,
    attempt_no: int = 1,
):
    payload = _failure()
    payload.update(
        {
            "run_id": RUN_ID,
            "operation_id": OPERATION_ID,
            "attempt_no": attempt_no,
            "failure_id": failure_id,
            "revision": revision,
            "current_outcome": outcome,
            "reason_code": "user_action_required" if outcome == "needs_attention" else "source_operation_failed",
            "component": "main",
            "phase": "observe" if outcome == "needs_attention" else "execute",
            "domain": "user_action" if outcome == "needs_attention" else "source",
            "failure_kind": "operation_failure",
            "detail": {} if outcome == "needs_attention" else payload["detail"],
            "occurred_at": occurred_at,
            "observed_at": observed_at or occurred_at,
            "user_action": None if action is None else action.model_dump(mode="json"),
        }
    )
    return parse_failure_envelope(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    )


def _checkpoint() -> RuntimeCheckpoint:
    return RuntimeCheckpoint(
        checkpoint_id=CHECKPOINT_ID,
        runtime_run_id=RUN_ID,
        stage="round",
        round_no=1,
        safe_boundary="after_round_controller",
        run_state={"round": 1},
        source_plan={"sourceIds": ["liepin"]},
        pending_commands=[],
        artifact_manifest_ref=None,
        schema_version="runtime-control-checkpoint/v1",
        created_at="2026-07-27T03:59:00Z",
    )


def _store(tmp_path: Path, *, status: str = "resume_requested") -> RuntimeControlStore:
    store = RuntimeControlStore(tmp_path / "runtime_control.sqlite3")
    store.initialize()
    store.create_run(
        RuntimeRunRecord(
            runtime_run_id=RUN_ID,
            run_intent_id=f"intent_{RUN_ID}",
            start_idempotency_key=f"start_{RUN_ID}",
            approved_requirement_revision_id="reqapproved_test",
            status="running",
            current_stage="running",
            source_ids=["liepin"],
            created_at="2026-07-27T03:00:00Z",
            updated_at="2026-07-27T03:00:00Z",
        )
    )
    accepted_result = _authenticated_result(
        ready=False,
        session_suffix="acceptance",
    )
    authenticated = require_authenticated_verify_session_result(
        accepted_result
    )
    identity = authenticated.result.identity
    authorization = authenticated.dispatch_authorization
    store.accept_source_operation(
        runtime_run_id=RUN_ID,
        operation_id=OPERATION_ID,
        source_id="liepin",
        operation_kind="verify_session",
        canonical_request_hash=identity.request_hash,
        idempotency_key=identity.idempotency_key,
        accepted_requirement_revision_id=(
            identity.accepted_requirement_revision_id
        ),
        runtime_attempt_no=identity.attempt_no,
        runtime_attempt_authority_ref="runtime-attempt-authority-1",
        runtime_attempt_fence_ref=identity.runtime_attempt_fence_ref,
        profile_binding_generation=identity.profile_binding_generation,
        browser_control_scope_id=identity.browser_control_scope_id,
        controller_fence_ref=None,
        outbox_id="source-outbox-action",
        dispatch_intent_id=authorization.dispatch_intent_id,
        dispatch_intent_revision=1,
        dispatch_intent_digest=authorization.dispatch_intent_digest,
        dispatch_authorization_ordinal=(
            authorization.dispatch_authorization_ordinal
        ),
        source_operation_acceptance_ref=(
            authorization.source_operation_acceptance_ref
        ),
        expected_ledger_revision=1,
        expected_reconciliation_revision=0,
    )
    if status != "running":
        store.update_run_status(
            runtime_run_id=RUN_ID,
            status=status,
            updated_at="2026-07-27T03:01:00Z",
        )
    return store


def _entry_admission(
    store: RuntimeControlStore,
    checkpoint: RuntimeCheckpoint,
    *,
    received: ReceivedVerifySessionResult | None = None,
):
    received = received or _authenticated_result(
        ready=False,
        session_suffix="entry",
    )
    run = store.get_run(RUN_ID)
    if run.status == "resume_requested":
        authenticated = require_authenticated_verify_session_result(received)
        identity = authenticated.result.identity
        history_digest = "1" * 64
        store.commit_no_owner_source_reconciliation(
            SourceOperationReconciliationDecision(
                reconciliation_id=f"source-history-{history_digest}",
                runtime_run_id=RUN_ID,
                operation_id=OPERATION_ID,
                source_id="liepin",
                operation_kind="verify_session",
                canonical_request_hash=identity.request_hash,
                idempotency_key=identity.idempotency_key,
                accepted_requirement_revision_id=(
                    identity.accepted_requirement_revision_id
                ),
                runtime_attempt_no=identity.attempt_no,
                runtime_attempt_authority_ref="runtime-attempt-authority-1",
                history_result_ref=f"sha256:{history_digest}",
                history_result_digest=history_digest,
                decision_kind="conclusive_observation",
                history_outcome="matched",
                history_conclusion="observed_result",
                dispatch_intent_ref="dispatch-intent-action",
                conclusive_observation_ref=authenticated.result_digest,
                source_operation_disposition="user_action_required",
                retry_posture="no_retry",
                expected_ledger_revision=1,
                expected_reconciliation_revision=0,
                committed_at="2026-07-27T03:58:00Z",
            )
        )
    return store.admit_needs_attention(
        received=received,
        checkpoint=checkpoint,
    )


def _accept_authenticated_operation(
    store: RuntimeControlStore,
    received: ReceivedVerifySessionResult,
    *,
    suffix: str,
) -> None:
    authenticated = require_authenticated_verify_session_result(received)
    identity = authenticated.result.identity
    authorization = authenticated.dispatch_authorization
    store.accept_source_operation(
        runtime_run_id=RUN_ID,
        operation_id=identity.operation_id,
        source_id="liepin",
        operation_kind="verify_session",
        canonical_request_hash=identity.request_hash,
        idempotency_key=identity.idempotency_key,
        accepted_requirement_revision_id=(
            identity.accepted_requirement_revision_id
        ),
        runtime_attempt_no=identity.attempt_no,
        runtime_attempt_authority_ref="runtime-attempt-authority-1",
        runtime_attempt_fence_ref=identity.runtime_attempt_fence_ref,
        profile_binding_generation=identity.profile_binding_generation,
        browser_control_scope_id=identity.browser_control_scope_id,
        controller_fence_ref=None,
        outbox_id=f"source-outbox-{suffix}",
        dispatch_intent_id=authorization.dispatch_intent_id,
        dispatch_intent_revision=authorization.dispatch_intent_revision,
        dispatch_intent_digest=authorization.dispatch_intent_digest,
        dispatch_authorization_ordinal=(
            authorization.dispatch_authorization_ordinal
        ),
        source_operation_acceptance_ref=(
            authorization.source_operation_acceptance_ref
        ),
        expected_ledger_revision=(
            identity.expected_source_operation_ledger_revision
        ),
        expected_reconciliation_revision=(
            identity.expected_reconciliation_revision
        ),
    )


def _satisfaction_admission(
    store: RuntimeControlStore,
    *,
    code: str = "open_liepin_host",
    session_suffix: str = "resolution",
    received: ReceivedVerifySessionResult | None = None,
):
    del code
    if received is None:
        resolution_operation_id = (
            RESOLUTION_OPERATION_ID
            if session_suffix == "resolution"
            else sha256(session_suffix.encode()).hexdigest()[:32]
        )
        received = _authenticated_result(
            ready=True,
            session_suffix=session_suffix,
            operation_id=resolution_operation_id,
            idempotency_key=f"verify-session-{session_suffix}",
            dispatch_intent_id=f"dispatch-intent-{session_suffix}",
            source_operation_acceptance_ref=(
                f"source-acceptance-{session_suffix}"
            ),
        )
    authenticated = require_authenticated_verify_session_result(received)
    identity = authenticated.result.identity
    authorization = authenticated.dispatch_authorization
    _accept_authenticated_operation(
        store,
        received,
        suffix=session_suffix,
    )
    history_digest = sha256(
        f"resolution-history:{session_suffix}".encode()
    ).hexdigest()
    store.commit_no_owner_source_reconciliation(
        SourceOperationReconciliationDecision(
            reconciliation_id=f"source-history-{history_digest}",
            runtime_run_id=RUN_ID,
            operation_id=identity.operation_id,
            source_id="liepin",
            operation_kind="verify_session",
            canonical_request_hash=identity.request_hash,
            idempotency_key=identity.idempotency_key,
            accepted_requirement_revision_id=(
                identity.accepted_requirement_revision_id
            ),
            runtime_attempt_no=identity.attempt_no,
            runtime_attempt_authority_ref="runtime-attempt-authority-1",
            history_result_ref=f"sha256:{history_digest}",
            history_result_digest=history_digest,
            decision_kind="conclusive_observation",
            history_outcome="matched",
            history_conclusion="observed_result",
            dispatch_intent_ref=authorization.dispatch_intent_id,
            conclusive_observation_ref=authenticated.result_digest,
            source_operation_disposition="completed",
            retry_posture="no_retry",
            expected_ledger_revision=(
                identity.expected_source_operation_ledger_revision
            ),
            expected_reconciliation_revision=(
                identity.expected_reconciliation_revision
            ),
            committed_at="2026-07-27T04:04:00Z",
        )
    )
    return store.admit_action_satisfaction(
        action_id=ACTION_ID,
        received=received,
    )


def _entry_crash_child(path: str, hook_index: int) -> None:
    store = RuntimeControlStore(path)
    checkpoint = _checkpoint()

    def crash(index: int, _phase: str) -> None:
        if index == hook_index:
            os._exit(93)

    store.commit_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=_entry_admission(store, checkpoint),
        checkpoint=checkpoint,
        envelope=_envelope(action=_action()),
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        entered_at=ENTERED_AT,
        statement_hook=crash,
    )


def _resolution_crash_child(path: str, hook_index: int) -> None:
    store = RuntimeControlStore(path)
    def crash(index: int, _phase: str) -> None:
        if index == hook_index:
            os._exit(94)

    store.resolve_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=_satisfaction_admission(store),
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        resolved_at=RESOLVED_AT,
        statement_hook=crash,
    )


def _cancel_crash_child(path: str, hook_index: int) -> None:
    store = RuntimeControlStore(path)

    def crash(index: int, _phase: str) -> None:
        if index == hook_index:
            os._exit(95)

    store.cancel_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        cancelled_at=RESOLVED_AT,
        cancellation_evidence_ref="c" * 64,
        statement_hook=crash,
    )


def _failure_crash_child(path: str, hook_index: int) -> None:
    store = RuntimeControlStore(path)

    def crash(index: int, _phase: str) -> None:
        if index == hook_index:
            os._exit(96)

    store.fail_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        envelope=_envelope(
            outcome="failed",
            action=None,
            failure_id="d" * 32,
            occurred_at=RESOLVED_AT,
        ),
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        terminal_reason_code="source_operation_failed",
        terminal_at=RESOLVED_AT,
        statement_hook=crash,
    )


def test_verify_session_mapping_is_total_closed_and_scope_bound() -> None:
    expected = {
        "liepin_host_tab_missing": "open_liepin_host",
        "liepin_opencli_identity_intercept": "complete_identity_check",
        "liepin_opencli_login_required": "log_in_to_liepin",
        "liepin_opencli_risk_page": "complete_liepin_risk_check",
    }
    for source_code, canonical_code in expected.items():
        source_action = VerifySessionUserActionV1(
            code=source_code,
            instruction_key={
                "liepin_host_tab_missing": "verify_session.open_liepin_host",
                "liepin_opencli_identity_intercept": "verify_session.complete_identity_check",
                "liepin_opencli_login_required": "verify_session.log_in",
                "liepin_opencli_risk_page": "verify_session.complete_risk_check",
            }[source_code],
        )
        mapped = map_verify_session_user_action(
            source_action,
            affected_scope_ref="6" * 32,
        )
        assert mapped.code == canonical_code
        assert mapped.instruction_key == USER_ACTION_INSTRUCTIONS[canonical_code]
        assert mapped.scope == USER_ACTION_SCOPES[canonical_code]
        assert mapped.affected_scope_ref == "6" * 32

    with pytest.raises(ValueError, match="verify_session_user_action_unsupported"):
        map_verify_session_user_action(
            VerifySessionUserActionV1(
                code="liepin_opencli_unknown_modal",
                instruction_key="verify_session.dismiss_or_resolve_modal",
            ),
            affected_scope_ref="6" * 32,
        )

    with pytest.raises((TypeError, ValueError)):
        map_verify_session_user_action(object(), affected_scope_ref="6" * 32)


def test_needs_attention_envelope_requires_one_canonical_action() -> None:
    action = _action()
    assert _envelope(action=action).user_action == action
    with pytest.raises(ValueError):
        _envelope(action=None)
    with pytest.raises(ValueError):
        _envelope(outcome="failed", action=action)


def test_runtime_control_v16_fresh_schema_has_action_history_and_pointer(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    with sqlite3.connect(store.path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        run_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(runtime_control_runs)")
        }
        action_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'runtime_control_user_actions'"
        ).fetchone()
    assert RUNTIME_CONTROL_SCHEMA_VERSION == version == 16
    assert "current_action_id" in run_columns
    assert action_sql is not None


def test_no_owner_entry_and_resolution_retain_history(tmp_path: Path) -> None:
    store = _store(tmp_path)
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    assert checkpoint.schema_version == "runtime-control-checkpoint/v2"
    assert store.get_latest_checkpoint(runtime_run_id=RUN_ID).schema_version == (
        "runtime-control-checkpoint/v2"
    )
    action = _action()
    admission = _entry_admission(store, checkpoint)
    entered = store.commit_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=admission,
        checkpoint=checkpoint,
        envelope=_envelope(action=action),
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        entered_at=ENTERED_AT,
    )
    assert entered.status == "needs_attention"
    assert entered.product_outcome == "needs_attention"
    assert entered.current_action_id == ACTION_ID

    satisfaction = _satisfaction_admission(store)
    resumed = store.resolve_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=satisfaction,
        expected_state_revision=entered.state_revision,
        resolved_at=RESOLVED_AT,
    )
    replay = store.resolve_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=satisfaction,
        expected_state_revision=entered.state_revision,
        resolved_at=RESOLVED_AT,
    )
    assert replay == resumed
    assert resumed.status == "resume_requested"
    assert resumed.product_outcome is None
    assert resumed.current_action_id is None
    assert resumed.current_failure_id is None
    [historical] = store.list_user_actions(runtime_run_id=RUN_ID)
    assert historical.status == "resolved"
    assert historical.resolution_evidence_ref is not None


def test_cancellation_and_failure_terminal_exits_retain_action_history(
    tmp_path: Path,
) -> None:
    for exit_kind in ("cancelled", "failed"):
        case_path = tmp_path / exit_kind
        store = _store(case_path)
        checkpoint = _checkpoint()
        store.write_checkpoint_for_recovery(checkpoint)
        action = _action()
        entered = store.commit_needs_attention(
            runtime_run_id=RUN_ID,
            action_id=ACTION_ID,
            admission=_entry_admission(store, checkpoint),
            checkpoint=checkpoint,
            envelope=_envelope(action=action),
            expected_state_revision=store.get_run(RUN_ID).state_revision,
            entered_at=ENTERED_AT,
        )
        if exit_kind == "cancelled":
            terminal = store.cancel_needs_attention(
                runtime_run_id=RUN_ID,
                action_id=ACTION_ID,
                expected_state_revision=entered.state_revision,
                cancelled_at=RESOLVED_AT,
                cancellation_evidence_ref="c" * 64,
            )
            replay = store.cancel_needs_attention(
                runtime_run_id=RUN_ID,
                action_id=ACTION_ID,
                expected_state_revision=entered.state_revision,
                cancelled_at=RESOLVED_AT,
                cancellation_evidence_ref="c" * 64,
            )
        else:
            terminal = store.fail_needs_attention(
                runtime_run_id=RUN_ID,
                action_id=ACTION_ID,
                envelope=_envelope(
                    outcome="failed",
                    action=None,
                    failure_id="d" * 32,
                    occurred_at=RESOLVED_AT,
                ),
                expected_state_revision=entered.state_revision,
                terminal_reason_code="source_operation_failed",
                terminal_at=RESOLVED_AT,
            )
            replay = store.fail_needs_attention(
                runtime_run_id=RUN_ID,
                action_id=ACTION_ID,
                envelope=_envelope(
                    outcome="failed",
                    action=None,
                    failure_id="d" * 32,
                    occurred_at=RESOLVED_AT,
                ),
                expected_state_revision=entered.state_revision,
                terminal_reason_code="source_operation_failed",
                terminal_at=RESOLVED_AT,
            )
        assert replay == terminal
        assert terminal.status == exit_kind
        assert terminal.product_outcome == exit_kind
        assert terminal.current_action_id is None
        [historical] = store.list_user_actions(runtime_run_id=RUN_ID)
        assert historical.status == exit_kind


def test_action_admissions_are_factory_only_and_mismatch_fails_closed(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    with pytest.raises(TypeError):
        type(_entry_admission(store, checkpoint))()

    with pytest.raises(RuntimeControlError) as exc_info:
        store.commit_needs_attention(
            runtime_run_id=RUN_ID,
            action_id=ACTION_ID,
            admission=_entry_admission(store, checkpoint),
            checkpoint=checkpoint,
            envelope=_envelope(action=_action("log_in_to_liepin")),
            expected_state_revision=store.get_run(RUN_ID).state_revision,
            entered_at=ENTERED_AT,
        )
    assert exc_info.value.reason_code == "runtime_needs_attention_envelope_mismatch"


def test_admission_rejects_unmarked_result_and_stale_attempt_authority(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path, status="running")
    checkpoint = _checkpoint()
    authenticated = _authenticated_result(ready=False)
    forged = ReceivedVerifySessionResult(
        message_id=authenticated.message_id,
        reply_to=authenticated.reply_to,
        correlation_id=authenticated.correlation_id,
        payload=authenticated.payload,
    )
    with pytest.raises(RuntimeControlError) as forged_exc:
        store.admit_needs_attention(
            received=forged,
            checkpoint=checkpoint,
        )
    assert (
        forged_exc.value.reason_code
        == "runtime_needs_attention_admission_rejected"
    )

    first = store.acquire_executor_lease(
        runtime_run_id=RUN_ID,
        executor_id="executor-a",
        acquired_at="2026-07-27T03:20:00Z",
        lease_expires_at="2026-07-27T03:25:00Z",
    )
    store.release_executor_lease(
        runtime_run_id=RUN_ID,
        executor_id=first.executor_id,
        attempt_no=first.attempt_no,
        released_at="2026-07-27T03:25:00Z",
    )
    second = store.acquire_executor_lease(
        runtime_run_id=RUN_ID,
        executor_id="executor-b",
        acquired_at="2026-07-27T03:30:00Z",
        lease_expires_at="2099-01-01T00:00:00Z",
    )
    assert (first.attempt_no, second.attempt_no) == (1, 2)
    with pytest.raises(RuntimeControlError) as stale_exc:
        store.admit_needs_attention(
            received=_authenticated_result(
                ready=False,
                attempt_no=1,
                session_suffix="stale-attempt",
            ),
            checkpoint=checkpoint,
        )
    assert (
        stale_exc.value.reason_code
        == "runtime_needs_attention_admission_rejected"
    )


def test_no_owner_entry_rejects_envelope_for_another_attempt_without_writes(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    before = store.get_run(RUN_ID)
    forged = _envelope(action=_action(), attempt_no=999)

    with pytest.raises(RuntimeControlError) as exc_info:
        store.commit_needs_attention(
            runtime_run_id=RUN_ID,
            action_id=ACTION_ID,
            admission=_entry_admission(store, checkpoint),
            checkpoint=checkpoint,
            envelope=forged,
            expected_state_revision=before.state_revision,
            entered_at=ENTERED_AT,
        )

    assert (
        exc_info.value.reason_code
        == "runtime_needs_attention_envelope_mismatch"
    )
    assert store.get_run(RUN_ID) == before
    assert store.list_user_actions(runtime_run_id=RUN_ID) == []


def test_safe_retry_ordinal_two_result_enters_needs_attention_with_exact_epoch(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    retry_digest = sha256(b"safe-retry-history").hexdigest()
    retry_reconciliation_id = "a" * 32
    store.commit_no_owner_source_reconciliation(
        SourceOperationReconciliationDecision(
            reconciliation_id=retry_reconciliation_id,
            runtime_run_id=RUN_ID,
            operation_id=OPERATION_ID,
            source_id="liepin",
            operation_kind="verify_session",
            canonical_request_hash=store.get_accepted_source_operation_context(
                RUN_ID,
                OPERATION_ID,
            ).operation.canonical_request_hash,
            idempotency_key="verify-session-action",
            accepted_requirement_revision_id="reqapproved_test",
            runtime_attempt_no=1,
            runtime_attempt_authority_ref="runtime-attempt-authority-1",
            history_result_ref=f"sha256:{retry_digest}",
            history_result_digest=retry_digest,
            decision_kind="no_dispatch_proved",
            history_outcome="not_found",
            history_conclusion=None,
            dispatch_intent_ref=None,
            conclusive_observation_ref=None,
            source_operation_disposition=None,
            retry_posture="safe_retry",
            expected_ledger_revision=1,
            expected_reconciliation_revision=0,
            committed_at="2026-07-27T03:10:00Z",
        )
    )
    first = store.acquire_executor_lease(
        runtime_run_id=RUN_ID,
        executor_id="executor-a",
        acquired_at="2026-07-27T03:20:00Z",
        lease_expires_at="2026-07-27T03:25:00Z",
    )
    store.release_executor_lease(
        runtime_run_id=RUN_ID,
        executor_id=first.executor_id,
        attempt_no=first.attempt_no,
        released_at="2026-07-27T03:25:00Z",
    )
    second = store.acquire_executor_lease(
        runtime_run_id=RUN_ID,
        executor_id="executor-b",
        acquired_at="2026-07-27T03:30:00Z",
        lease_expires_at="2099-01-01T00:00:00Z",
    )
    received = _authenticated_result(
        ready=False,
        attempt_no=second.attempt_no,
        profile_generation=2,
        session_suffix="ordinal-two-entry",
        dispatch_intent_id="dispatch-intent-action-retry",
        dispatch_intent_revision=2,
        dispatch_authorization_ordinal=2,
        expected_ledger_revision=3,
        expected_reconciliation_revision=1,
        safe_retry_commit_ref=retry_reconciliation_id,
    )
    authenticated = require_authenticated_verify_session_result(received)
    authority = store._mint_safe_retry_turnover_authority_for_test(
        runtime_run_id=RUN_ID,
        executor_id=second.executor_id,
        attempt_no=second.attempt_no,
        observed_at="2026-07-27T03:31:00Z",
        runtime_attempt_authority_ref="runtime-attempt-authority-2",
        runtime_attempt_fence_ref=(
            authenticated.result.identity.runtime_attempt_fence_ref
        ),
        profile_binding_generation=2,
        browser_control_scope_id="6" * 32,
        controller_fence_ref=None,
    )
    epoch = store.mint_safe_retry_dispatch_epoch(
        runtime_run_id=RUN_ID,
        operation_id=OPERATION_ID,
        reconciliation_id=retry_reconciliation_id,
        expected_reconciliation_ledger_revision=2,
        expected_reconciliation_revision=1,
        outbox_id="source-outbox-action-retry",
        dispatch_intent_id="dispatch-intent-action-retry",
        authority=authority,
    )
    assert epoch.dispatch.dispatch_authorization_ordinal == 2
    assert epoch.dispatch.dispatch_intent_digest == (
        authenticated.dispatch_authorization.dispatch_intent_digest
    )
    store.update_run_status(
        runtime_run_id=RUN_ID,
        status="running",
        updated_at="2026-07-27T03:32:00Z",
    )
    checkpoint = _checkpoint()
    admission = store.admit_needs_attention(
        received=received,
        checkpoint=checkpoint,
    )
    entered = store.commit_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=admission,
        checkpoint=checkpoint,
        envelope=_envelope(action=_action(), attempt_no=2),
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        entered_at=ENTERED_AT,
        executor_id=second.executor_id,
        attempt_no=second.attempt_no,
    )

    assert entered.status == "needs_attention"
    [action] = store.list_user_actions(runtime_run_id=RUN_ID)
    assert action.entry_dispatch_authorization_ordinal == 2
    assert action.dispatch_intent_id == "dispatch-intent-action-retry"
    with sqlite3.connect(store.path) as conn:
        conn.execute("DROP TRIGGER runtime_user_actions_immutable_binding")
        conn.execute("DROP TRIGGER runtime_user_actions_one_way_resolution")
        conn.execute(
            """
            UPDATE runtime_control_user_actions
            SET entry_dispatch_authorization_ordinal = 1
            WHERE action_id = ?
            """,
            (ACTION_ID,),
        )
    with pytest.raises(RuntimeControlError) as swapped:
        store.list_user_actions(runtime_run_id=RUN_ID)
    assert (
        swapped.value.reason_code
        == "runtime_needs_attention_integrity_failed"
    )


def test_action_history_rejects_poisoned_envelope_attempt(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    store.commit_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=_entry_admission(store, checkpoint),
        checkpoint=checkpoint,
        envelope=_envelope(action=_action()),
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        entered_at=ENTERED_AT,
    )
    poisoned = _envelope(action=_action(), attempt_no=999)
    poisoned_bytes = canonical_diagnostics_bytes(poisoned)
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "DROP TRIGGER runtime_control_failure_envelopes_no_update"
        )
        conn.execute(
            """
            UPDATE runtime_control_failure_envelope_revisions
            SET canonical_bytes = ?, canonical_sha256 = ?, attempt_no = 999
            WHERE failure_id = ? AND revision = 1
            """,
            (
                poisoned_bytes,
                sha256(poisoned_bytes).hexdigest(),
                "7" * 32,
            ),
        )

    for read in (
        lambda: store.get_run(RUN_ID),
        lambda: store.list_user_actions(runtime_run_id=RUN_ID),
    ):
        with pytest.raises(RuntimeControlError) as exc_info:
            read()
        assert (
            exc_info.value.reason_code
            == "runtime_needs_attention_integrity_failed"
        )


@pytest.mark.parametrize(
    "received",
    [
        _authenticated_result(
            ready=False,
            requirement_revision_id="reqapproved_wrong",
            session_suffix="wrong-revision",
        ),
        _authenticated_result(
            ready=False,
            browser_scope_id="f" * 32,
            session_suffix="wrong-scope",
        ),
    ],
)
def test_entry_admission_binds_required_revision_and_scope(
    tmp_path: Path,
    received: ReceivedVerifySessionResult,
) -> None:
    store = _store(tmp_path)
    with pytest.raises(RuntimeControlError) as exc_info:
        store.admit_needs_attention(
            received=received,
            checkpoint=_checkpoint(),
        )
    assert (
        exc_info.value.reason_code
        == "runtime_needs_attention_admission_rejected"
    )


@pytest.mark.parametrize(
    ("case_name", "mutation"),
    [
        (
            "run-objective",
            "UPDATE runtime_control_runs "
            "SET approved_requirement_revision_id = 'reqapproved_tampered' "
            "WHERE runtime_run_id = ?",
        ),
        (
            "source-revision",
            "UPDATE runtime_control_source_operations "
            "SET ledger_revision = ledger_revision + 1 "
            "WHERE runtime_run_id = ?",
        ),
        (
            "required-source",
            "UPDATE runtime_control_runs SET source_ids_json = '[\"other\"]' "
            "WHERE runtime_run_id = ?",
        ),
    ],
)
def test_commit_rechecks_minted_admission_against_current_main_truth(
    tmp_path: Path,
    case_name: str,
    mutation: str,
) -> None:
    store = _store(tmp_path / case_name)
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    admission = _entry_admission(store, checkpoint)
    with sqlite3.connect(store.path) as conn:
        conn.execute(mutation, (RUN_ID,))
    with pytest.raises(RuntimeControlError) as exc_info:
        store.commit_needs_attention(
            runtime_run_id=RUN_ID,
            action_id=ACTION_ID,
            admission=admission,
            checkpoint=checkpoint,
            envelope=_envelope(action=_action()),
            expected_state_revision=store.get_run(RUN_ID).state_revision,
            entered_at=ENTERED_AT,
        )
    assert (
        exc_info.value.reason_code
        == "runtime_needs_attention_admission_rejected"
    )


def test_commit_rejects_deleted_authenticated_observation(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    admission = _entry_admission(store, checkpoint)
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "DROP TRIGGER runtime_authenticated_observations_delete_forbidden"
        )
        conn.execute(
            "DELETE FROM runtime_control_authenticated_observations "
            "WHERE session_readiness = 'not_ready'"
        )
    with pytest.raises(RuntimeControlError) as exc_info:
        store.commit_needs_attention(
            runtime_run_id=RUN_ID,
            action_id=ACTION_ID,
            admission=admission,
            checkpoint=checkpoint,
            envelope=_envelope(action=_action()),
            expected_state_revision=store.get_run(RUN_ID).state_revision,
            entered_at=ENTERED_AT,
        )
    assert (
        exc_info.value.reason_code
        == "runtime_needs_attention_admission_rejected"
    )


def test_entry_replay_compares_complete_canonical_envelope(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    admission = _entry_admission(store, checkpoint)
    envelope = _envelope(action=_action())
    expected_revision = store.get_run(RUN_ID).state_revision
    store.commit_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=admission,
        checkpoint=checkpoint,
        envelope=envelope,
        expected_state_revision=expected_revision,
        entered_at=ENTERED_AT,
    )
    with pytest.raises(RuntimeControlError) as exc_info:
        store.commit_needs_attention(
            runtime_run_id=RUN_ID,
            action_id=ACTION_ID,
            admission=admission,
            checkpoint=checkpoint,
            envelope=_envelope(
                action=_action(),
                observed_at="2026-07-27T04:00:01Z",
            ),
            expected_state_revision=expected_revision,
            entered_at=ENTERED_AT,
        )
    assert (
        exc_info.value.reason_code
        == "runtime_needs_attention_replay_conflict"
    )


def test_resolution_requires_current_profile_scope_and_exact_replay_binding(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    entered = store.commit_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=_entry_admission(store, checkpoint),
        checkpoint=checkpoint,
        envelope=_envelope(action=_action()),
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        entered_at=ENTERED_AT,
    )
    with pytest.raises(TypeError):
        store.admit_action_satisfaction(
            action_id=ACTION_ID,
            received=_authenticated_result(
                ready=True,
                session_suffix="caller-action",
            ),
            action=_action("log_in_to_liepin"),  # type: ignore[call-arg]
        )
    for received in (
        _authenticated_result(
            ready=True,
            profile_generation=2,
            session_suffix="stale-profile",
        ),
        _authenticated_result(
            ready=True,
            browser_scope_id="f" * 32,
            session_suffix="stale-scope",
        ),
    ):
        with pytest.raises(RuntimeControlError) as stale_exc:
            store.admit_action_satisfaction(
                action_id=ACTION_ID,
                received=received,
            )
        assert (
            stale_exc.value.reason_code
            == "runtime_needs_attention_satisfaction_rejected"
        )

    first = _satisfaction_admission(
        store,
        session_suffix="resolution-first",
    )
    with pytest.raises(TypeError):
        type(first)()
    conflicting = _satisfaction_admission(
        store,
        session_suffix="resolution-conflicting",
    )
    store.resolve_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=first,
        expected_state_revision=entered.state_revision,
        resolved_at=RESOLVED_AT,
    )
    with pytest.raises(RuntimeControlError) as replay_exc:
        store.resolve_needs_attention(
            runtime_run_id=RUN_ID,
            action_id=ACTION_ID,
            admission=conflicting,
            expected_state_revision=entered.state_revision,
            resolved_at=RESOLVED_AT,
        )
    assert (
        replay_exc.value.reason_code
        == "runtime_needs_attention_replay_conflict"
    )


def test_resolution_rechecks_current_committed_scope_after_mint(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    entered = store.commit_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=_entry_admission(store, checkpoint),
        checkpoint=checkpoint,
        envelope=_envelope(action=_action()),
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        entered_at=ENTERED_AT,
    )
    satisfaction = _satisfaction_admission(store)
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "DROP TRIGGER "
            "trg_runtime_source_admission_expectation_no_update"
        )
        conn.execute(
            "UPDATE runtime_control_source_operation_admission_expectations "
            "SET browser_control_scope_id = ? "
            "WHERE runtime_run_id = ? AND operation_id = ?",
            ("f" * 32, RUN_ID, OPERATION_ID),
        )
    with pytest.raises(RuntimeControlError) as exc_info:
        store.resolve_needs_attention(
            runtime_run_id=RUN_ID,
            action_id=ACTION_ID,
            admission=satisfaction,
            expected_state_revision=entered.state_revision,
            resolved_at=RESOLVED_AT,
        )
    assert (
        exc_info.value.reason_code
        == "runtime_needs_attention_integrity_failed"
    )


def test_active_owner_entry_revokes_exact_lease_and_exact_replay_is_read_only(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path, status="running")
    lease = store.acquire_executor_lease(
        runtime_run_id=RUN_ID,
        executor_id="executor-a",
        acquired_at="2026-07-27T03:30:00Z",
        lease_expires_at="2099-01-01T00:00:00Z",
    )
    checkpoint = _checkpoint()
    action = _action()
    expected_revision = store.get_run(RUN_ID).state_revision
    kwargs = {
        "runtime_run_id": RUN_ID,
        "action_id": ACTION_ID,
        "admission": _entry_admission(store, checkpoint),
        "checkpoint": checkpoint,
        "envelope": _envelope(action=action),
        "expected_state_revision": expected_revision,
        "entered_at": ENTERED_AT,
        "executor_id": lease.executor_id,
        "attempt_no": lease.attempt_no,
    }
    entered = store.commit_needs_attention(**kwargs)
    replay = store.commit_needs_attention(**kwargs)
    assert entered == replay
    assert entered.current_failure_authority_mode == "active_owner"
    assert entered.current_failure_owner_lease_id == lease.lease_id
    with sqlite3.connect(store.path) as conn:
        persisted_lease = conn.execute(
            "SELECT status, reason_code FROM runtime_control_executor_leases WHERE lease_id = ?",
            (lease.lease_id,),
        ).fetchone()
    assert persisted_lease == ("revoked", "runtime_needs_attention")

    with pytest.raises(RuntimeControlError) as exc_info:
        store.commit_needs_attention(
            **{
                **kwargs,
                "executor_id": "executor-other",
            }
        )
    assert exc_info.value.reason_code == "runtime_needs_attention_replay_conflict"


def test_active_owner_entry_rejects_expired_lease_authority(tmp_path: Path) -> None:
    store = _store(tmp_path, status="running")
    lease = store.acquire_executor_lease(
        runtime_run_id=RUN_ID,
        executor_id="executor-a",
        acquired_at="2026-07-27T03:30:00Z",
        lease_expires_at=ENTERED_AT,
    )
    checkpoint = _checkpoint()

    with pytest.raises(RuntimeControlError) as exc_info:
        store.commit_needs_attention(
            runtime_run_id=RUN_ID,
            action_id=ACTION_ID,
            admission=_entry_admission(store, checkpoint),
            checkpoint=checkpoint,
            envelope=_envelope(action=_action()),
            expected_state_revision=store.get_run(RUN_ID).state_revision,
            entered_at=ENTERED_AT,
            executor_id=lease.executor_id,
            attempt_no=lease.attempt_no,
        )

    assert exc_info.value.reason_code == "runtime_needs_attention_authority_rejected"
    assert store.get_run(RUN_ID).status == "running"


def test_entry_honours_cancellation_precedence_and_no_owner_reconciliation_gate(
    tmp_path: Path,
) -> None:
    cancelled_store = _store(tmp_path / "cancelled")
    cancelled_checkpoint = _checkpoint()
    cancelled_admission = _entry_admission(
        cancelled_store,
        cancelled_checkpoint,
    )
    with sqlite3.connect(cancelled_store.path) as conn:
        conn.execute(
            "UPDATE runtime_control_runs SET status = 'cancellation_requested' "
            "WHERE runtime_run_id = ?",
            (RUN_ID,),
        )
    with pytest.raises(RuntimeControlError) as cancel_exc:
        cancelled_store.commit_needs_attention(
            runtime_run_id=RUN_ID,
            action_id=ACTION_ID,
            admission=cancelled_admission,
            checkpoint=cancelled_checkpoint,
            envelope=_envelope(action=_action()),
            expected_state_revision=0,
            entered_at=ENTERED_AT,
        )
    assert cancel_exc.value.reason_code == "runtime_needs_attention_cancellation_won"

    store = _store(tmp_path / "reconcile")
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    with pytest.raises(RuntimeControlError) as reconcile_exc:
        store.admit_needs_attention(
            received=_authenticated_result(ready=False),
            checkpoint=checkpoint,
        )
    assert (
        reconcile_exc.value.reason_code
        == "runtime_needs_attention_admission_rejected"
    )


def test_checkpoint_participant_never_owns_transaction_timing(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    checkpoint = _checkpoint()
    with sqlite3.connect(store.path) as conn:
        conn.row_factory = sqlite3.Row
        with pytest.raises(RuntimeControlError) as no_transaction:
            write_checkpoint_participant(conn, checkpoint)
        assert (
            no_transaction.value.reason_code
            == "runtime_checkpoint_transaction_required"
        )
        conn.execute("BEGIN IMMEDIATE")
        write_checkpoint_participant(conn, checkpoint)
        assert conn.in_transaction
        conn.rollback()
        assert conn.execute(
            "SELECT 1 FROM runtime_control_checkpoints WHERE checkpoint_id = ?",
            (CHECKPOINT_ID,),
        ).fetchone() is None


def test_v14_to_v15_migration_rejects_incomplete_truth_and_partial_schema(
    tmp_path: Path,
) -> None:
    for poisoning in ("incomplete_truth", "partial_schema"):
        path = tmp_path / poisoning / "runtime_control.sqlite3"
        store = RuntimeControlStore(path)
        store.initialize()
        if poisoning == "incomplete_truth":
            store.create_run(
                RuntimeRunRecord(
                    runtime_run_id=RUN_ID,
                    approved_requirement_revision_id="reqapproved_test",
                    status="queued",
                    current_stage="queued",
                    source_ids=["liepin"],
                    created_at="2026-07-27T03:00:00Z",
                    updated_at="2026-07-27T03:00:00Z",
                )
            )
        with sqlite3.connect(path) as conn:
            _downgrade_v15_to_v14(conn)
            if poisoning == "incomplete_truth":
                conn.execute(
                    "UPDATE runtime_control_runs SET status = 'needs_attention'"
                )
            else:
                conn.execute(
                    "ALTER TABLE runtime_control_runs ADD COLUMN current_action_id TEXT"
                )
        with pytest.raises(RuntimeControlError) as exc_info:
            RuntimeControlStore(path).initialize()
        assert exc_info.value.reason_code in {
            "runtime_needs_attention_incomplete_migration",
            "runtime_needs_attention_schema_collision",
        }
        with sqlite3.connect(path) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 14


@pytest.mark.parametrize("poisoning", ("extra", "reordered", "constraint"))
def test_claimed_v15_poisoned_action_schema_fails_closed(
    tmp_path: Path,
    poisoning: str,
) -> None:
    from seektalent_runtime_control import needs_attention as module

    path = tmp_path / "runtime_control.sqlite3"
    RuntimeControlStore(path).initialize()
    statements = module.NEEDS_ATTENTION_V15_SCHEMA_STATEMENTS
    with sqlite3.connect(path) as conn:
        _downgrade_v15_to_v14(conn)
        conn.execute(statements[0])
        conn.execute(statements[1])
        table_sql = statements[2]
        if poisoning == "extra":
            table_sql = table_sql.replace(
                "      created_at TEXT NOT NULL,",
                "      created_at TEXT NOT NULL,\n      poisoned TEXT,",
            )
        elif poisoning == "reordered":
            table_sql = table_sql.replace(
                "      action_code TEXT NOT NULL,\n"
                "      instruction_key TEXT NOT NULL,",
                "      instruction_key TEXT NOT NULL,\n"
                "      action_code TEXT NOT NULL,",
            )
        else:
            table_sql = table_sql.replace(
                "'pending', 'resolved', 'cancelled', 'failed'",
                "'pending', 'resolved', 'cancelled'",
            )
        conn.execute(table_sql)
        for statement in statements[3:]:
            conn.execute(statement)
        conn.execute("PRAGMA user_version = 15")

    with pytest.raises(RuntimeControlError) as exc_info:
        RuntimeControlStore(path).initialize()
    assert (
        exc_info.value.reason_code
        == "runtime_needs_attention_schema_collision"
    )


@pytest.mark.parametrize(
    "ddl",
    (
        "CREATE INDEX poisoned_extra_index "
        "ON runtime_control_user_actions(action_id)",
        "CREATE TRIGGER poisoned_before_trigger "
        "BEFORE UPDATE ON runtime_control_user_actions "
        "BEGIN SELECT 1; END",
        "CREATE TRIGGER poisoned_after_trigger "
        "AFTER INSERT ON runtime_control_user_actions "
        "BEGIN SELECT 1; END",
    ),
)
def test_v15_reopen_rejects_arbitrarily_named_owned_table_objects(
    tmp_path: Path,
    ddl: str,
) -> None:
    path = tmp_path / "runtime_control.sqlite3"
    RuntimeControlStore(path).initialize()
    with sqlite3.connect(path) as conn:
        conn.execute(ddl)

    with pytest.raises(RuntimeControlError) as exc_info:
        RuntimeControlStore(path).initialize()

    assert (
        exc_info.value.reason_code
        == "runtime_needs_attention_schema_collision"
    )


def test_v14_migration_rejects_extra_checkpoint_trigger_without_partial_ddl(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime_control.sqlite3"
    RuntimeControlStore(path).initialize()
    with sqlite3.connect(path) as conn:
        _downgrade_v15_to_v14(conn)
        conn.execute(
            "CREATE TRIGGER poisoned_checkpoint_trigger "
            "AFTER UPDATE ON runtime_control_checkpoints "
            "BEGIN SELECT 1; END"
        )

    with pytest.raises(RuntimeControlError) as exc_info:
        RuntimeControlStore(path).initialize()

    assert (
        exc_info.value.reason_code
        == "runtime_needs_attention_schema_collision"
    )
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 14
        assert "current_action_id" not in {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(runtime_control_runs)"
            )
        }


@pytest.mark.parametrize("completed_statements", range(0, 12))
def test_v14_to_v15_statement_failure_rolls_back_and_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    completed_statements: int,
) -> None:
    from seektalent_runtime_control import needs_attention_schema as module

    path = tmp_path / "runtime_control.sqlite3"
    RuntimeControlStore(path).initialize()
    statements = module.NEEDS_ATTENTION_V15_SCHEMA_STATEMENTS
    with sqlite3.connect(path) as conn:
        _downgrade_v15_to_v14(conn)
    monkeypatch.setattr(
        module,
        "NEEDS_ATTENTION_V15_SCHEMA_STATEMENTS",
        (
            *statements[:completed_statements],
            "ALTER TABL runtime_control_runs injected_invalid_statement",
        ),
    )
    with pytest.raises(sqlite3.OperationalError):
        RuntimeControlStore(path).initialize()
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 14
        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(runtime_control_runs)"
            )
        }
        assert "current_action_id" not in columns
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name LIKE '%user_actions%'"
        ).fetchone() is None
    monkeypatch.setattr(
        module,
        "NEEDS_ATTENTION_V15_SCHEMA_STATEMENTS",
        statements,
    )
    RuntimeControlStore(path).initialize()
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 16


@pytest.mark.parametrize("hook_index", range(0, 11))
def test_entry_statement_failures_leave_only_old_truth(
    tmp_path: Path,
    hook_index: int,
) -> None:
    store = _store(tmp_path)
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    before = store.get_run(RUN_ID)

    def fail(index: int, _phase: str) -> None:
        if index == hook_index:
            raise RuntimeError("injected")

    with pytest.raises(RuntimeError, match="injected"):
        store.commit_needs_attention(
            runtime_run_id=RUN_ID,
            action_id=ACTION_ID,
            admission=_entry_admission(store, checkpoint),
            checkpoint=checkpoint,
            envelope=_envelope(action=_action()),
            expected_state_revision=before.state_revision,
            entered_at=ENTERED_AT,
            statement_hook=fail,
        )
    assert store.get_run(RUN_ID) == before
    assert store.list_user_actions(runtime_run_id=RUN_ID) == []


def test_exit_rejects_stale_evidence_and_retains_checkpoint_envelope_action(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    action = _action()
    entered = store.commit_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=_entry_admission(store, checkpoint),
        checkpoint=checkpoint,
        envelope=_envelope(action=action),
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        entered_at=ENTERED_AT,
    )
    with pytest.raises(RuntimeControlError) as stale:
        store.resolve_needs_attention(
            runtime_run_id=RUN_ID,
            action_id=ACTION_ID,
            admission=_satisfaction_admission(store),
            expected_state_revision=entered.state_revision - 1,
            resolved_at=RESOLVED_AT,
        )
    assert stale.value.reason_code == "runtime_needs_attention_revision_conflict"
    terminal = store.cancel_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        expected_state_revision=entered.state_revision,
        cancelled_at=RESOLVED_AT,
        cancellation_evidence_ref="c" * 64,
    )
    assert terminal.status == "cancelled"
    assert store.delete_terminal_checkpoints(
        older_than="2099-01-01T00:00:00Z",
        batch_size=100,
    ) == 0
    with sqlite3.connect(store.path) as conn:
        assert conn.execute(
            "SELECT 1 FROM runtime_control_failure_envelope_revisions WHERE failure_id = ?",
            ("7" * 32,),
        ).fetchone() is not None
        assert conn.execute(
            "SELECT 1 FROM runtime_control_checkpoints WHERE checkpoint_id = ?",
            (CHECKPOINT_ID,),
        ).fetchone() is not None


@pytest.mark.parametrize("hook_index", range(0, 12))
def test_entry_subprocess_crash_exposes_only_old_or_complete_new_truth(
    tmp_path: Path,
    hook_index: int,
) -> None:
    store = _store(tmp_path)
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    before_revision = store.get_run(RUN_ID).state_revision
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from tests.test_runtime_control_needs_attention "
                "import _entry_crash_child; "
                f"_entry_crash_child({str(store.path)!r}, {hook_index})"
            ),
        ],
        cwd=Path(__file__).parents[1],
        check=False,
        timeout=20,
    )
    assert completed.returncode == 93
    readback = store.get_run(RUN_ID)
    if hook_index < 11:
        assert readback.status == "resume_requested"
        assert readback.state_revision == before_revision
        assert store.list_user_actions(runtime_run_id=RUN_ID) == []
    else:
        assert readback.status == "needs_attention"
        assert readback.current_action_id == ACTION_ID
        assert len(store.list_user_actions(runtime_run_id=RUN_ID)) == 1


@pytest.mark.parametrize("hook_index", range(2, 8))
def test_resolution_subprocess_crash_exposes_only_old_or_complete_new_truth(
    tmp_path: Path,
    hook_index: int,
) -> None:
    store = _store(tmp_path)
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    action = _action()
    entered = store.commit_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=_entry_admission(store, checkpoint),
        checkpoint=checkpoint,
        envelope=_envelope(action=action),
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        entered_at=ENTERED_AT,
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from tests.test_runtime_control_needs_attention "
                "import _resolution_crash_child; "
                f"_resolution_crash_child({str(store.path)!r}, {hook_index})"
            ),
        ],
        cwd=Path(__file__).parents[1],
        check=False,
        timeout=20,
    )
    assert completed.returncode == 94
    readback = store.get_run(RUN_ID)
    [historical] = store.list_user_actions(runtime_run_id=RUN_ID)
    if hook_index < 7:
        assert readback == entered
        assert historical.status == "pending"
    else:
        assert readback.status == "resume_requested"
        assert readback.current_action_id is None
        assert historical.status == "resolved"


def test_action_history_bindings_cannot_be_updated_or_deleted(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    store.commit_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=_entry_admission(store, checkpoint),
        checkpoint=checkpoint,
        envelope=_envelope(action=_action()),
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        entered_at=ENTERED_AT,
    )
    with sqlite3.connect(store.path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE runtime_control_user_actions SET checkpoint_id = ? WHERE action_id = ?",
                ("poisoned", ACTION_ID),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "DELETE FROM runtime_control_user_actions WHERE action_id = ?",
                (ACTION_ID,),
            )


def test_resolved_history_rejects_dangling_checkpoint(tmp_path: Path) -> None:
    store = _store(tmp_path)
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    entered = store.commit_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=_entry_admission(store, checkpoint),
        checkpoint=checkpoint,
        envelope=_envelope(action=_action()),
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        entered_at=ENTERED_AT,
    )
    store.resolve_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=_satisfaction_admission(store),
        expected_state_revision=entered.state_revision,
        resolved_at=RESOLVED_AT,
    )
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "DROP TRIGGER runtime_action_checkpoints_delete_forbidden"
        )
        conn.execute(
            "DELETE FROM runtime_control_checkpoints WHERE checkpoint_id = ?",
            (CHECKPOINT_ID,),
        )
    with pytest.raises(RuntimeControlError) as exc_info:
        store.list_user_actions(runtime_run_id=RUN_ID)
    assert (
        exc_info.value.reason_code
        == "runtime_needs_attention_integrity_failed"
    )


@pytest.mark.parametrize(
    "poisoning",
    ("resolution_observation", "resolution_reconciliation", "resolution_operation"),
)
def test_resolved_history_rejects_dangling_resolution_truth(
    tmp_path: Path,
    poisoning: str,
) -> None:
    store = _store(tmp_path)
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    entered = store.commit_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=_entry_admission(store, checkpoint),
        checkpoint=checkpoint,
        envelope=_envelope(action=_action()),
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        entered_at=ENTERED_AT,
    )
    store.resolve_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=_satisfaction_admission(store),
        expected_state_revision=entered.state_revision,
        resolved_at=RESOLVED_AT,
    )
    [action] = store.list_user_actions(runtime_run_id=RUN_ID)
    with sqlite3.connect(store.path) as conn:
        if poisoning == "resolution_observation":
            conn.execute(
                "DROP TRIGGER "
                "runtime_authenticated_observations_delete_forbidden"
            )
            conn.execute(
                """
                DELETE FROM runtime_control_authenticated_observations
                WHERE observation_ref = ?
                """,
                (action.resolution_evidence_ref,),
            )
        elif poisoning == "resolution_reconciliation":
            conn.execute(
                "DROP TRIGGER "
                "runtime_control_source_reconciliations_no_delete"
            )
            conn.execute(
                """
                DELETE FROM runtime_control_source_reconciliations
                WHERE reconciliation_id = ?
                """,
                (action.resolution_reconciliation_id,),
            )
        else:
            conn.execute(
                """
                DELETE FROM runtime_control_source_operations
                WHERE runtime_run_id = ? AND operation_id = ?
                """,
                (RUN_ID, action.resolution_operation_id),
            )

    for read in (
        lambda: store.get_run(RUN_ID),
        lambda: store.list_user_actions(runtime_run_id=RUN_ID),
    ):
        with pytest.raises(RuntimeControlError) as exc_info:
            read()
        assert (
            exc_info.value.reason_code
            == "runtime_needs_attention_integrity_failed"
        )


@pytest.mark.parametrize(
    ("status", "outcome"),
    (
        ("cancelled", "cancelled"),
        ("completed", "succeeded_with_results"),
        ("completed", "succeeded_empty"),
        ("completed", "degraded_with_results"),
    ),
)
@pytest.mark.parametrize("full_failure_truth", (False, True))
def test_nonfailed_public_reads_reject_forged_failure_truth(
    tmp_path: Path,
    status: str,
    outcome: str,
    full_failure_truth: bool,
) -> None:
    store = _store(tmp_path, status="running")
    with sqlite3.connect(store.path) as conn:
        conn.execute("PRAGMA ignore_check_constraints=ON")
        conn.execute(
            """
            UPDATE runtime_control_runs
            SET status = ?, product_outcome = ?,
                current_failure_id = 'forged',
                current_failure_revision = 1,
                current_failure_owner_lease_id = ?,
                current_failure_authority_mode = ?
            WHERE runtime_run_id = ?
            """,
            (
                status,
                outcome,
                "forged-lease" if full_failure_truth else None,
                "active_owner" if full_failure_truth else None,
                RUN_ID,
            ),
        )

    for read in (
        lambda: store.get_run(RUN_ID),
        lambda: store.list_user_actions(runtime_run_id=RUN_ID),
    ):
        with pytest.raises(RuntimeControlError) as exc_info:
            read()
        assert exc_info.value.reason_code in {
            "runtime_failed_outcome_integrity_failed",
            "runtime_needs_attention_integrity_failed",
        }


def test_cancelled_history_rejects_missing_failure_envelope(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    entered = store.commit_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=_entry_admission(store, checkpoint),
        checkpoint=checkpoint,
        envelope=_envelope(action=_action()),
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        entered_at=ENTERED_AT,
    )
    store.cancel_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        expected_state_revision=entered.state_revision,
        cancelled_at=RESOLVED_AT,
        cancellation_evidence_ref="c" * 64,
    )
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "DROP TRIGGER runtime_control_failure_envelopes_no_delete"
        )
        conn.execute(
            "DELETE FROM runtime_control_failure_envelope_revisions "
            "WHERE failure_id = ? AND revision = 1",
            ("7" * 32,),
        )
    with pytest.raises(RuntimeControlError) as exc_info:
        store.list_user_actions(runtime_run_id=RUN_ID)
    assert (
        exc_info.value.reason_code
        == "runtime_needs_attention_integrity_failed"
    )


def test_failed_history_rejects_tampered_action_binding(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    entered = store.commit_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=_entry_admission(store, checkpoint),
        checkpoint=checkpoint,
        envelope=_envelope(action=_action()),
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        entered_at=ENTERED_AT,
    )
    store.fail_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        envelope=_envelope(
            outcome="failed",
            action=None,
            failure_id="d" * 32,
            occurred_at=RESOLVED_AT,
        ),
        expected_state_revision=entered.state_revision,
        terminal_reason_code="source_operation_failed",
        terminal_at=RESOLVED_AT,
    )
    with sqlite3.connect(store.path) as conn:
        conn.execute("DROP TRIGGER runtime_user_actions_immutable_binding")
        conn.execute("DROP TRIGGER runtime_user_actions_one_way_resolution")
        conn.execute(
            "UPDATE runtime_control_user_actions "
            "SET entry_observation_digest = ? WHERE action_id = ?",
            ("f" * 64, ACTION_ID),
        )
    with pytest.raises(RuntimeControlError) as exc_info:
        store.list_user_actions(runtime_run_id=RUN_ID)
    assert (
        exc_info.value.reason_code
        == "runtime_needs_attention_integrity_failed"
    )


def test_active_owner_history_rejects_tampered_revoked_lease(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path, status="running")
    lease = store.acquire_executor_lease(
        runtime_run_id=RUN_ID,
        executor_id="executor-a",
        acquired_at="2026-07-27T03:30:00Z",
        lease_expires_at="2099-01-01T00:00:00Z",
    )
    checkpoint = _checkpoint()
    entered = store.commit_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=_entry_admission(store, checkpoint),
        checkpoint=checkpoint,
        envelope=_envelope(action=_action()),
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        entered_at=ENTERED_AT,
        executor_id=lease.executor_id,
        attempt_no=lease.attempt_no,
    )
    store.resolve_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=_satisfaction_admission(store),
        expected_state_revision=entered.state_revision,
        resolved_at=RESOLVED_AT,
    )
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "UPDATE runtime_control_executor_leases SET attempt_no = 2 "
            "WHERE lease_id = ?",
            (lease.lease_id,),
        )
    with pytest.raises(RuntimeControlError) as exc_info:
        store.list_user_actions(runtime_run_id=RUN_ID)
    assert (
        exc_info.value.reason_code
        == "runtime_needs_attention_integrity_failed"
    )


@pytest.mark.parametrize(
    ("exit_kind", "hook_index"),
    [
        *(("cancelled", index) for index in range(2, 8)),
        *(("failed", index) for index in range(0, 8)),
    ],
)
def test_terminal_exit_subprocess_crash_exposes_only_old_or_complete_new_truth(
    tmp_path: Path,
    exit_kind: str,
    hook_index: int,
) -> None:
    store = _store(tmp_path)
    checkpoint = _checkpoint()
    store.write_checkpoint_for_recovery(checkpoint)
    action = _action()
    entered = store.commit_needs_attention(
        runtime_run_id=RUN_ID,
        action_id=ACTION_ID,
        admission=_entry_admission(store, checkpoint),
        checkpoint=checkpoint,
        envelope=_envelope(action=action),
        expected_state_revision=store.get_run(RUN_ID).state_revision,
        entered_at=ENTERED_AT,
    )
    child = (
        "_cancel_crash_child"
        if exit_kind == "cancelled"
        else "_failure_crash_child"
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from tests.test_runtime_control_needs_attention "
                f"import {child}; "
                f"{child}({str(store.path)!r}, {hook_index})"
            ),
        ],
        cwd=Path(__file__).parents[1],
        check=False,
        timeout=20,
    )
    assert completed.returncode == (
        95 if exit_kind == "cancelled" else 96
    )
    readback = store.get_run(RUN_ID)
    [historical] = store.list_user_actions(runtime_run_id=RUN_ID)
    after_commit_hook = 7
    if hook_index < after_commit_hook:
        assert readback == entered
        assert historical.status == "pending"
    else:
        assert readback.status == exit_kind
        assert readback.current_action_id is None
        assert historical.status == exit_kind
