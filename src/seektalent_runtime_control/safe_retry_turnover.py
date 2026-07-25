from __future__ import annotations

from collections.abc import Callable
import sqlite3
import unicodedata

from pydantic import ValidationError

from seektalent.source_port.operation_dispatch import (
    DispatchAuthorizationV1,
    OperationIdentityV1,
    RelativeMonotonicDeadlineV1,
)
from seektalent_runtime_control.clock import timestamp_lte
from seektalent_runtime_control.errors import (
    RuntimeControlError,
    RuntimeControlLookupError,
)
from seektalent_runtime_control.source_operations import (
    AcceptedSourceOperation,
    SourceDispatchMetadata,
    SourceOperationAdmissionExpectation,
    SourceOperationRecord,
    dispatch_matches_operation,
    expectation_matches_operation,
    source_dispatch_from_row,
    source_operation_admission_expectation_from_row,
    source_operation_from_row,
    validate_source_operation_admission_expectation,
)
from seektalent_runtime_control.source_reconciliation import (
    SourceOperationReconciliationRecord,
    source_reconciliation_from_row,
)


_JSON_SAFE_INTEGER_MAX = 2**53 - 1


class _SafeRetryTurnoverAuthority:
    """Sealed current-attempt facts issued and owned by one store instance."""

    __slots__ = ("_facts", "_issuer")
    _facts: tuple[str, str, str, int, str, str, str, int, str, str | None]
    _issuer: _SafeRetryTurnoverAuthorityIssuer

    def __init__(
        self,
        *,
        issuer: _SafeRetryTurnoverAuthorityIssuer,
        facts: tuple[str, str, str, int, str, str, str, int, str, str | None],
    ) -> None:
        object.__setattr__(self, "_issuer", issuer)
        object.__setattr__(self, "_facts", facts)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("safe-retry turnover authority is immutable")

    @property
    def runtime_run_id(self) -> str:
        return self._facts[0]

    @property
    def executor_id(self) -> str:
        return self._facts[1]

    @property
    def lease_id(self) -> str:
        return self._facts[2]

    @property
    def attempt_no(self) -> int:
        return self._facts[3]

    @property
    def observed_at(self) -> str:
        return self._facts[4]

    @property
    def runtime_attempt_authority_ref(self) -> str:
        return self._facts[5]

    @property
    def runtime_attempt_fence_ref(self) -> str:
        return self._facts[6]

    @property
    def profile_binding_generation(self) -> int:
        return self._facts[7]

    @property
    def browser_control_scope_id(self) -> str:
        return self._facts[8]

    @property
    def controller_fence_ref(self) -> str | None:
        return self._facts[9]


class _SafeRetryTurnoverAuthorityIssuer:
    """Owns capability provenance by object identity, never caller equality."""

    __slots__ = ("_issued",)

    def __init__(self) -> None:
        self._issued: dict[int, _SafeRetryTurnoverAuthority] = {}

    def issue(
        self,
        *,
        runtime_run_id: str,
        executor_id: str,
        lease_id: str,
        attempt_no: int,
        observed_at: str,
        runtime_attempt_authority_ref: str,
        runtime_attempt_fence_ref: str,
        profile_binding_generation: int,
        browser_control_scope_id: str,
        controller_fence_ref: str | None,
    ) -> _SafeRetryTurnoverAuthority:
        _require_opaque(executor_id, "executor_id", max_bytes=256)
        _require_opaque(lease_id, "lease_id", max_bytes=96)
        _require_opaque(observed_at, "authority_observed_at", max_bytes=64)
        if not isinstance(browser_control_scope_id, str):
            raise RuntimeControlError("source_safe_retry_browser_control_scope_invalid")
        validate_source_operation_admission_expectation(
            runtime_run_id=runtime_run_id,
            operation_id="safe_retry_capability_validation",
            dispatch_authorization_ordinal=2,
            runtime_attempt_no=attempt_no,
            runtime_attempt_authority_ref=runtime_attempt_authority_ref,
            runtime_attempt_fence_ref=runtime_attempt_fence_ref,
            profile_binding_generation=profile_binding_generation,
            browser_control_scope_id=browser_control_scope_id,
            controller_fence_ref=controller_fence_ref,
        )
        authority = _SafeRetryTurnoverAuthority(
            issuer=self,
            facts=(
                runtime_run_id,
                executor_id,
                lease_id,
                attempt_no,
                observed_at,
                runtime_attempt_authority_ref,
                runtime_attempt_fence_ref,
                profile_binding_generation,
                browser_control_scope_id,
                controller_fence_ref,
            ),
        )
        self._issued[id(authority)] = authority
        return authority

    def require(self, value: object) -> _SafeRetryTurnoverAuthority:
        if type(value) is not _SafeRetryTurnoverAuthority:
            raise RuntimeControlError("source_safe_retry_authority_invalid")
        if value._issuer is not self or self._issued.get(id(value)) is not value:
            raise RuntimeControlError("source_safe_retry_authority_invalid")
        return value


def issue_safe_retry_turnover_authority(
    conn: sqlite3.Connection,
    issuer: _SafeRetryTurnoverAuthorityIssuer,
    *,
    runtime_run_id: str,
    executor_id: str,
    attempt_no: int,
    observed_at: str,
    runtime_attempt_authority_ref: str,
    runtime_attempt_fence_ref: str,
    profile_binding_generation: int,
    browser_control_scope_id: str,
    controller_fence_ref: str | None,
) -> object:
    _require_opaque(
        observed_at,
        "authority_observed_at",
        max_bytes=64,
    )
    try:
        timestamp_lte(observed_at, observed_at)
    except (TypeError, ValueError):
        raise RuntimeControlError("source_safe_retry_authority_observed_at_invalid") from None
    lease_row = _require_active_executor(
        conn,
        runtime_run_id,
        executor_id,
        attempt_no=attempt_no,
        observed_at=observed_at,
    )
    return issuer.issue(
        runtime_run_id=runtime_run_id,
        executor_id=executor_id,
        lease_id=lease_row["lease_id"],
        attempt_no=attempt_no,
        observed_at=observed_at,
        runtime_attempt_authority_ref=runtime_attempt_authority_ref,
        runtime_attempt_fence_ref=runtime_attempt_fence_ref,
        profile_binding_generation=profile_binding_generation,
        browser_control_scope_id=browser_control_scope_id,
        controller_fence_ref=controller_fence_ref,
    )


def mint_safe_retry_dispatch_epoch(
    conn: sqlite3.Connection,
    issuer: _SafeRetryTurnoverAuthorityIssuer,
    *,
    runtime_run_id: str,
    operation_id: str,
    reconciliation_id: str,
    expected_reconciliation_ledger_revision: int,
    expected_reconciliation_revision: int,
    outbox_id: str,
    dispatch_intent_id: str,
    authority: object,
    fault_injector: Callable[[str], None] | None,
) -> AcceptedSourceOperation:
    """Consume one exact safe-retry reconciliation and mint its next epoch."""
    _validate_turnover_request(
        runtime_run_id=runtime_run_id,
        operation_id=operation_id,
        reconciliation_id=reconciliation_id,
        expected_reconciliation_ledger_revision=(expected_reconciliation_ledger_revision),
        expected_reconciliation_revision=expected_reconciliation_revision,
        outbox_id=outbox_id,
        dispatch_intent_id=dispatch_intent_id,
    )
    current_authority = issuer.require(authority)
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing_row = _dispatch_row_for_safe_retry(
            conn,
            runtime_run_id,
            operation_id,
            reconciliation_id,
        )
        if existing_row is not None:
            committed = _require_replay(
                conn,
                existing_row=existing_row,
                runtime_run_id=runtime_run_id,
                operation_id=operation_id,
                reconciliation_id=reconciliation_id,
                expected_reconciliation_ledger_revision=(expected_reconciliation_ledger_revision),
                expected_reconciliation_revision=(expected_reconciliation_revision),
                outbox_id=outbox_id,
                dispatch_intent_id=dispatch_intent_id,
                authority=current_authority,
            )
            conn.commit()
            _inject_fault(fault_injector, "after_commit")
            return committed

        if _run_row(conn, runtime_run_id) is None:
            raise RuntimeControlLookupError("runtime_run_not_found")
        operation_row = _operation_row(
            conn,
            runtime_run_id,
            operation_id,
        )
        if operation_row is None:
            raise RuntimeControlLookupError("source_operation_not_found")
        operation = source_operation_from_row(operation_row)
        latest_dispatch_row = latest_source_dispatch_row(
            conn,
            runtime_run_id,
            operation_id,
        )
        if (
            operation.retry_posture != "safe_retry"
            and latest_dispatch_row is not None
            and latest_dispatch_row["safe_retry_commit_ref"] is not None
        ):
            raise RuntimeControlError("source_safe_retry_idempotency_conflict")
        reconciliation_row = _reconciliation_row(
            conn,
            reconciliation_id,
        )
        if reconciliation_row is None:
            raise RuntimeControlLookupError("source_safe_retry_reconciliation_not_found")
        reconciliation = source_reconciliation_from_row(reconciliation_row)
        _require_reconciliation(
            operation=operation,
            reconciliation=reconciliation,
            runtime_run_id=runtime_run_id,
            operation_id=operation_id,
            expected_reconciliation_ledger_revision=(expected_reconciliation_ledger_revision),
            expected_reconciliation_revision=(expected_reconciliation_revision),
        )
        if latest_dispatch_row is None:
            raise RuntimeControlError("source_safe_retry_previous_epoch_missing")
        previous_dispatch = source_dispatch_from_row(latest_dispatch_row)
        if not dispatch_matches_operation(previous_dispatch, operation):
            raise RuntimeControlError("source_safe_retry_previous_epoch_invalid")
        previous_expectation_row = _expectation_row(
            conn,
            runtime_run_id,
            operation_id,
            previous_dispatch.dispatch_authorization_ordinal,
        )
        if previous_expectation_row is None:
            raise RuntimeControlError("source_safe_retry_previous_epoch_invalid")
        previous_expectation = _expectation_from_row(previous_expectation_row)
        _require_current_lease(
            conn,
            current_authority,
            runtime_run_id=runtime_run_id,
        )
        if current_authority.attempt_no <= previous_expectation.runtime_attempt_no:
            raise RuntimeControlError("source_safe_retry_attempt_stale")

        next_ordinal = previous_dispatch.dispatch_authorization_ordinal + 1
        if next_ordinal > _JSON_SAFE_INTEGER_MAX:
            raise RuntimeControlError("source_safe_retry_ordinal_overflow")
        next_intent_revision = previous_dispatch.dispatch_intent_revision + 1
        if next_intent_revision > _JSON_SAFE_INTEGER_MAX:
            raise RuntimeControlError("source_safe_retry_dispatch_intent_revision_overflow")
        next_ledger_revision = operation.ledger_revision + 1
        if next_ledger_revision > _JSON_SAFE_INTEGER_MAX:
            raise RuntimeControlError("source_safe_retry_revision_overflow")
        validate_source_operation_admission_expectation(
            runtime_run_id=runtime_run_id,
            operation_id=operation_id,
            dispatch_authorization_ordinal=next_ordinal,
            runtime_attempt_no=current_authority.attempt_no,
            runtime_attempt_authority_ref=(current_authority.runtime_attempt_authority_ref),
            runtime_attempt_fence_ref=(current_authority.runtime_attempt_fence_ref),
            profile_binding_generation=(current_authority.profile_binding_generation),
            browser_control_scope_id=(current_authority.browser_control_scope_id),
            controller_fence_ref=current_authority.controller_fence_ref,
        )
        if source_dispatch_identity_exists(
            conn,
            outbox_id,
            dispatch_intent_id,
        ):
            raise RuntimeControlError("source_safe_retry_dispatch_identity_conflict")
        safe_retry_commit_ref = reconciliation.reconciliation_id
        authorization = _create_authorization(
            operation=operation,
            attempt_no=current_authority.attempt_no,
            runtime_attempt_fence_ref=(current_authority.runtime_attempt_fence_ref),
            profile_binding_generation=(current_authority.profile_binding_generation),
            browser_control_scope_id=(current_authority.browser_control_scope_id),
            dispatch_intent_id=dispatch_intent_id,
            dispatch_intent_revision=next_intent_revision,
            dispatch_authorization_ordinal=next_ordinal,
            safe_retry_commit_ref=safe_retry_commit_ref,
            source_operation_acceptance_ref=(previous_dispatch.source_operation_acceptance_ref),
            expected_ledger_revision=next_ledger_revision,
            expected_reconciliation_revision=(reconciliation.committed_reconciliation_revision),
        )

        _inject_fault(fault_injector, "before_expectation_insert")
        conn.execute(
            """
            INSERT INTO runtime_control_source_operation_admission_expectations (
                runtime_run_id, operation_id,
                dispatch_authorization_ordinal, runtime_attempt_no,
                runtime_attempt_authority_ref, runtime_attempt_fence_ref,
                profile_binding_generation, browser_control_scope_id,
                controller_fence_ref
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                runtime_run_id,
                operation_id,
                next_ordinal,
                current_authority.attempt_no,
                current_authority.runtime_attempt_authority_ref,
                current_authority.runtime_attempt_fence_ref,
                current_authority.profile_binding_generation,
                current_authority.browser_control_scope_id,
                current_authority.controller_fence_ref,
            ),
        )
        _inject_fault(fault_injector, "after_expectation_insert")
        _inject_fault(fault_injector, "before_outbox_insert")
        conn.execute(
            """
            INSERT INTO runtime_control_source_dispatch_outbox (
                outbox_id, runtime_run_id, operation_id,
                canonical_request_hash, dispatch_intent_id,
                dispatch_intent_revision, dispatch_intent_digest,
                dispatch_authorization_ordinal, safe_retry_commit_ref,
                source_operation_acceptance_ref,
                expected_ledger_revision,
                expected_reconciliation_revision,
                status, outbox_revision,
                accepted_sidecar_generation,
                accepted_sidecar_journal_revision,
                ack_ref, ack_kind, acknowledged_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    'pending', 1, NULL, NULL, NULL, NULL, NULL)
            """,
            (
                outbox_id,
                runtime_run_id,
                operation_id,
                operation.canonical_request_hash,
                dispatch_intent_id,
                next_intent_revision,
                authorization.dispatch_intent_digest,
                next_ordinal,
                safe_retry_commit_ref,
                previous_dispatch.source_operation_acceptance_ref,
                next_ledger_revision,
                reconciliation.committed_reconciliation_revision,
            ),
        )
        _inject_fault(fault_injector, "after_outbox_insert")
        _inject_fault(fault_injector, "before_operation_update")
        updated = conn.execute(
            """
            UPDATE runtime_control_source_operations
            SET retry_posture = 'no_retry', ledger_revision = ?
            WHERE runtime_run_id = ? AND operation_id = ?
              AND operation_phase = 'reconciled'
              AND retry_posture = 'safe_retry'
              AND main_commit_ref IS NULL
              AND ledger_revision = ?
              AND reconciliation_revision = ?
            """,
            (
                next_ledger_revision,
                runtime_run_id,
                operation_id,
                reconciliation.committed_ledger_revision,
                reconciliation.committed_reconciliation_revision,
            ),
        )
        if updated.rowcount != 1:
            raise RuntimeControlError("source_safe_retry_revision_conflict")
        _inject_fault(fault_injector, "after_operation_update")
        committed = _committed_acceptance(
            conn,
            runtime_run_id=runtime_run_id,
            operation_id=operation_id,
            dispatch_authorization_ordinal=next_ordinal,
        )
        _inject_fault(fault_injector, "before_commit")
        conn.commit()
        _inject_fault(fault_injector, "after_commit")
    except Exception:
        conn.rollback()
        raise
    return committed


def require_safe_retry_dispatch_authorization(
    *,
    operation: SourceOperationRecord,
    expectation: SourceOperationAdmissionExpectation,
    dispatch: SourceDispatchMetadata,
) -> DispatchAuthorizationV1:
    safe_retry_commit_ref = dispatch.safe_retry_commit_ref
    browser_control_scope_id = expectation.browser_control_scope_id
    if (
        safe_retry_commit_ref is None
        or browser_control_scope_id is None
        or expectation.dispatch_authorization_ordinal != dispatch.dispatch_authorization_ordinal
    ):
        raise RuntimeControlError("source_safe_retry_authorization_invalid")
    authorization = _create_authorization(
        operation=operation,
        attempt_no=expectation.runtime_attempt_no,
        runtime_attempt_fence_ref=expectation.runtime_attempt_fence_ref,
        profile_binding_generation=expectation.profile_binding_generation,
        browser_control_scope_id=browser_control_scope_id,
        dispatch_intent_id=dispatch.dispatch_intent_id,
        dispatch_intent_revision=dispatch.dispatch_intent_revision,
        dispatch_authorization_ordinal=(dispatch.dispatch_authorization_ordinal),
        safe_retry_commit_ref=safe_retry_commit_ref,
        source_operation_acceptance_ref=(dispatch.source_operation_acceptance_ref),
        expected_ledger_revision=dispatch.expected_ledger_revision,
        expected_reconciliation_revision=(dispatch.expected_reconciliation_revision),
    )
    if authorization.dispatch_intent_digest != dispatch.dispatch_intent_digest:
        raise RuntimeControlError("source_safe_retry_authorization_invalid")
    return authorization


def latest_source_dispatch_row(
    conn: sqlite3.Connection,
    runtime_run_id: str,
    operation_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM runtime_control_source_dispatch_outbox
        WHERE runtime_run_id = ? AND operation_id = ?
        ORDER BY dispatch_authorization_ordinal DESC
        LIMIT 1
        """,
        (runtime_run_id, operation_id),
    ).fetchone()


def source_dispatch_identity_exists(
    conn: sqlite3.Connection,
    outbox_id: str,
    dispatch_intent_id: str,
) -> bool:
    return (
        conn.execute(
            """
            SELECT 1
            FROM runtime_control_source_dispatch_outbox
            WHERE outbox_id = ? OR dispatch_intent_id = ?
            LIMIT 1
            """,
            (outbox_id, dispatch_intent_id),
        ).fetchone()
        is not None
    )


def _validate_turnover_request(
    *,
    runtime_run_id: str,
    operation_id: str,
    reconciliation_id: str,
    expected_reconciliation_ledger_revision: int,
    expected_reconciliation_revision: int,
    outbox_id: str,
    dispatch_intent_id: str,
) -> None:
    _require_opaque(runtime_run_id, "runtime_run_id", max_bytes=96)
    _require_opaque(operation_id, "operation_id", max_bytes=96)
    _require_opaque(
        reconciliation_id,
        "reconciliation_id",
        max_bytes=96,
    )
    _require_positive_json_integer(
        expected_reconciliation_ledger_revision,
        "expected_reconciliation_ledger_revision",
    )
    _require_positive_json_integer(
        expected_reconciliation_revision,
        "expected_reconciliation_revision",
    )
    _require_opaque(outbox_id, "outbox_id", max_bytes=96)
    _require_opaque(
        dispatch_intent_id,
        "dispatch_intent_id",
        max_bytes=96,
    )


def _require_reconciliation(
    *,
    operation: SourceOperationRecord,
    reconciliation: SourceOperationReconciliationRecord,
    runtime_run_id: str,
    operation_id: str,
    expected_reconciliation_ledger_revision: int,
    expected_reconciliation_revision: int,
) -> None:
    _require_reconciliation_identity(
        operation=operation,
        reconciliation=reconciliation,
        runtime_run_id=runtime_run_id,
        operation_id=operation_id,
        expected_reconciliation_ledger_revision=(expected_reconciliation_ledger_revision),
        expected_reconciliation_revision=expected_reconciliation_revision,
    )
    if (
        operation.operation_phase != "reconciled"
        or operation.retry_posture != "safe_retry"
        or operation.dispatch_intent_ref is not None
        or operation.conclusive_observation_ref is not None
        or operation.source_operation_disposition is not None
        or operation.main_commit_ref is not None
    ):
        raise RuntimeControlError("source_safe_retry_posture_conflict")
    if (
        operation.ledger_revision != reconciliation.committed_ledger_revision
        or operation.reconciliation_revision != reconciliation.committed_reconciliation_revision
    ):
        raise RuntimeControlError("source_safe_retry_revision_conflict")


def _require_reconciliation_identity(
    *,
    operation: SourceOperationRecord,
    reconciliation: SourceOperationReconciliationRecord,
    runtime_run_id: str,
    operation_id: str,
    expected_reconciliation_ledger_revision: int,
    expected_reconciliation_revision: int,
) -> None:
    if (
        reconciliation.runtime_run_id != runtime_run_id
        or reconciliation.operation_id != operation_id
        or reconciliation.runtime_run_id != operation.runtime_run_id
        or reconciliation.operation_id != operation.operation_id
        or reconciliation.source_id != operation.source_id
        or reconciliation.operation_kind != operation.operation_kind
        or reconciliation.canonical_request_hash != operation.canonical_request_hash
        or reconciliation.idempotency_key != operation.idempotency_key
        or reconciliation.accepted_requirement_revision_id != operation.accepted_requirement_revision_id
        or reconciliation.runtime_attempt_no != operation.runtime_attempt_no
        or reconciliation.runtime_attempt_authority_ref != operation.runtime_attempt_authority_ref
    ):
        raise RuntimeControlError("source_safe_retry_identity_conflict")
    if (
        reconciliation.decision_kind != "no_dispatch_proved"
        or reconciliation.retry_posture != "safe_retry"
        or reconciliation.dispatch_intent_ref is not None
        or reconciliation.conclusive_observation_ref is not None
        or reconciliation.source_operation_disposition is not None
        or reconciliation.committed_operation_phase != "reconciled"
    ):
        raise RuntimeControlError("source_safe_retry_reconciliation_conflict")
    if (
        reconciliation.committed_ledger_revision != expected_reconciliation_ledger_revision
        or reconciliation.committed_reconciliation_revision != expected_reconciliation_revision
        or reconciliation.committed_ledger_revision != reconciliation.expected_ledger_revision + 1
        or reconciliation.committed_reconciliation_revision != reconciliation.expected_reconciliation_revision + 1
    ):
        raise RuntimeControlError("source_safe_retry_revision_conflict")


def _require_current_lease(
    conn: sqlite3.Connection,
    authority: _SafeRetryTurnoverAuthority,
    *,
    runtime_run_id: str,
) -> sqlite3.Row:
    if authority.runtime_run_id != runtime_run_id:
        raise RuntimeControlError("source_safe_retry_authority_conflict")
    lease_row = _active_lease_row(conn, runtime_run_id)
    if lease_row is None:
        raise RuntimeControlError("source_safe_retry_lease_missing")
    if timestamp_lte(
        lease_row["lease_expires_at"],
        authority.observed_at,
    ):
        raise RuntimeControlError("source_safe_retry_lease_expired")
    if (
        lease_row["lease_id"] != authority.lease_id
        or lease_row["executor_id"] != authority.executor_id
        or lease_row["attempt_no"] != authority.attempt_no
    ):
        raise RuntimeControlError("source_safe_retry_authority_stale")
    return lease_row


def _create_authorization(
    *,
    operation: SourceOperationRecord,
    attempt_no: int,
    runtime_attempt_fence_ref: str,
    profile_binding_generation: int,
    browser_control_scope_id: str,
    dispatch_intent_id: str,
    dispatch_intent_revision: int,
    dispatch_authorization_ordinal: int,
    safe_retry_commit_ref: str,
    source_operation_acceptance_ref: str,
    expected_ledger_revision: int,
    expected_reconciliation_revision: int,
) -> DispatchAuthorizationV1:
    try:
        identity = OperationIdentityV1(
            run_id=operation.runtime_run_id,
            operation_id=operation.operation_id,
            attempt_no=attempt_no,
            source=operation.source_id,
            operation_kind=operation.operation_kind,
            request_hash=operation.canonical_request_hash,
            idempotency_key=operation.idempotency_key,
            correlation_id=dispatch_intent_id,
            accepted_requirement_revision_id=(operation.accepted_requirement_revision_id),
            runtime_attempt_fence_ref=runtime_attempt_fence_ref,
            profile_binding_generation=profile_binding_generation,
            browser_control_scope_id=browser_control_scope_id,
            deadline=RelativeMonotonicDeadlineV1(
                value=1,
                clock="relative_monotonic",
                unit="milliseconds",
            ),
            expected_source_operation_ledger_revision=(expected_ledger_revision),
            expected_reconciliation_revision=(expected_reconciliation_revision),
        )
        return DispatchAuthorizationV1.create_safe_retry(
            identity=identity,
            dispatch_intent_id=dispatch_intent_id,
            dispatch_intent_revision=dispatch_intent_revision,
            dispatch_authorization_ordinal=(dispatch_authorization_ordinal),
            safe_retry_commit_ref=safe_retry_commit_ref,
            source_operation_acceptance_ref=(source_operation_acceptance_ref),
        )
    except (TypeError, ValueError, ValidationError):
        raise RuntimeControlError("source_safe_retry_authorization_invalid") from None


def _require_replay(
    conn: sqlite3.Connection,
    *,
    existing_row: sqlite3.Row,
    runtime_run_id: str,
    operation_id: str,
    reconciliation_id: str,
    expected_reconciliation_ledger_revision: int,
    expected_reconciliation_revision: int,
    outbox_id: str,
    dispatch_intent_id: str,
    authority: _SafeRetryTurnoverAuthority,
) -> AcceptedSourceOperation:
    operation_row = _operation_row(
        conn,
        runtime_run_id,
        operation_id,
    )
    reconciliation_row = _reconciliation_row(
        conn,
        reconciliation_id,
    )
    if operation_row is None or reconciliation_row is None:
        raise RuntimeControlError("source_safe_retry_idempotency_conflict")
    operation = source_operation_from_row(operation_row)
    reconciliation = source_reconciliation_from_row(reconciliation_row)
    try:
        _require_reconciliation_identity(
            operation=operation,
            reconciliation=reconciliation,
            runtime_run_id=runtime_run_id,
            operation_id=operation_id,
            expected_reconciliation_ledger_revision=(expected_reconciliation_ledger_revision),
            expected_reconciliation_revision=(expected_reconciliation_revision),
        )
    except RuntimeControlError:
        raise RuntimeControlError("source_safe_retry_idempotency_conflict") from None
    dispatch = source_dispatch_from_row(existing_row)
    expectation_row = _expectation_row(
        conn,
        runtime_run_id,
        operation_id,
        dispatch.dispatch_authorization_ordinal,
    )
    if expectation_row is None:
        raise RuntimeControlError("source_safe_retry_idempotency_conflict")
    expectation = _expectation_from_row(expectation_row)
    _require_current_lease(
        conn,
        authority,
        runtime_run_id=runtime_run_id,
    )
    if (
        dispatch.outbox_id != outbox_id
        or dispatch.dispatch_intent_id != dispatch_intent_id
        or dispatch.safe_retry_commit_ref != reconciliation_id
        or dispatch.expected_ledger_revision != reconciliation.committed_ledger_revision + 1
        or dispatch.expected_reconciliation_revision != reconciliation.committed_reconciliation_revision
        or operation.ledger_revision != dispatch.expected_ledger_revision
        or operation.reconciliation_revision != dispatch.expected_reconciliation_revision
        or operation.retry_posture != "no_retry"
        or expectation.runtime_attempt_no != authority.attempt_no
        or expectation.runtime_attempt_authority_ref != authority.runtime_attempt_authority_ref
        or expectation.runtime_attempt_fence_ref != authority.runtime_attempt_fence_ref
        or expectation.profile_binding_generation != authority.profile_binding_generation
        or expectation.browser_control_scope_id != authority.browser_control_scope_id
        or expectation.controller_fence_ref != authority.controller_fence_ref
    ):
        raise RuntimeControlError("source_safe_retry_idempotency_conflict")
    try:
        require_safe_retry_dispatch_authorization(
            operation=operation,
            expectation=expectation,
            dispatch=dispatch,
        )
    except RuntimeControlError:
        raise RuntimeControlError("source_safe_retry_idempotency_conflict") from None
    return AcceptedSourceOperation(
        operation=operation,
        expectation=expectation,
        dispatch=dispatch,
    )


def _committed_acceptance(
    conn: sqlite3.Connection,
    *,
    runtime_run_id: str,
    operation_id: str,
    dispatch_authorization_ordinal: int,
) -> AcceptedSourceOperation:
    operation_row = _operation_row(
        conn,
        runtime_run_id,
        operation_id,
    )
    dispatch_row = conn.execute(
        """
        SELECT *
        FROM runtime_control_source_dispatch_outbox
        WHERE runtime_run_id = ? AND operation_id = ?
          AND dispatch_authorization_ordinal = ?
        """,
        (
            runtime_run_id,
            operation_id,
            dispatch_authorization_ordinal,
        ),
    ).fetchone()
    expectation_row = _expectation_row(
        conn,
        runtime_run_id,
        operation_id,
        dispatch_authorization_ordinal,
    )
    if operation_row is None or dispatch_row is None or expectation_row is None:
        raise RuntimeControlError("source_safe_retry_commit_incomplete")
    operation = source_operation_from_row(operation_row)
    dispatch = source_dispatch_from_row(dispatch_row)
    expectation = _expectation_from_row(expectation_row)
    if (
        not dispatch_matches_operation(dispatch, operation)
        or not expectation_matches_operation(expectation, operation)
        or operation.ledger_revision != dispatch.expected_ledger_revision
        or operation.reconciliation_revision != dispatch.expected_reconciliation_revision
        or operation.retry_posture != "no_retry"
    ):
        raise RuntimeControlError("source_safe_retry_commit_incomplete")
    require_safe_retry_dispatch_authorization(
        operation=operation,
        expectation=expectation,
        dispatch=dispatch,
    )
    return AcceptedSourceOperation(
        operation=operation,
        expectation=expectation,
        dispatch=dispatch,
    )


def _expectation_from_row(
    row: sqlite3.Row,
) -> SourceOperationAdmissionExpectation:
    try:
        expectation = source_operation_admission_expectation_from_row(row)
        validate_source_operation_admission_expectation(
            runtime_run_id=expectation.runtime_run_id,
            operation_id=expectation.operation_id,
            dispatch_authorization_ordinal=(expectation.dispatch_authorization_ordinal),
            runtime_attempt_no=expectation.runtime_attempt_no,
            runtime_attempt_authority_ref=(expectation.runtime_attempt_authority_ref),
            runtime_attempt_fence_ref=(expectation.runtime_attempt_fence_ref),
            profile_binding_generation=(expectation.profile_binding_generation),
            browser_control_scope_id=(expectation.browser_control_scope_id),
            controller_fence_ref=expectation.controller_fence_ref,
        )
    except (RuntimeControlError, TypeError, ValueError):
        raise RuntimeControlError("source_operation_acceptance_incomplete") from None
    return expectation


def _run_row(
    conn: sqlite3.Connection,
    runtime_run_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM runtime_control_runs
        WHERE runtime_run_id = ?
        """,
        (runtime_run_id,),
    ).fetchone()


def _operation_row(
    conn: sqlite3.Connection,
    runtime_run_id: str,
    operation_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM runtime_control_source_operations
        WHERE runtime_run_id = ? AND operation_id = ?
        """,
        (runtime_run_id, operation_id),
    ).fetchone()


def _expectation_row(
    conn: sqlite3.Connection,
    runtime_run_id: str,
    operation_id: str,
    dispatch_authorization_ordinal: int,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM runtime_control_source_operation_admission_expectations
        WHERE runtime_run_id = ? AND operation_id = ?
          AND dispatch_authorization_ordinal = ?
        """,
        (
            runtime_run_id,
            operation_id,
            dispatch_authorization_ordinal,
        ),
    ).fetchone()


def _reconciliation_row(
    conn: sqlite3.Connection,
    reconciliation_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM runtime_control_source_reconciliations
        WHERE reconciliation_id = ?
        """,
        (reconciliation_id,),
    ).fetchone()


def _active_lease_row(
    conn: sqlite3.Connection,
    runtime_run_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM runtime_control_executor_leases
        WHERE runtime_run_id = ? AND status = 'active'
        ORDER BY attempt_no DESC
        LIMIT 1
        """,
        (runtime_run_id,),
    ).fetchone()


def _require_active_executor(
    conn: sqlite3.Connection,
    runtime_run_id: str,
    executor_id: str,
    *,
    attempt_no: int,
    observed_at: str,
) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT *
        FROM runtime_control_executor_leases
        WHERE runtime_run_id = ? AND executor_id = ?
          AND status = 'active' AND attempt_no = ?
        ORDER BY attempt_no DESC
        LIMIT 1
        """,
        (runtime_run_id, executor_id, attempt_no),
    ).fetchone()
    if row is None:
        raise RuntimeControlError("runtime_executor_stale")
    if timestamp_lte(row["lease_expires_at"], observed_at):
        raise RuntimeControlError("runtime_executor_lease_expired")
    return row


def _dispatch_row_for_safe_retry(
    conn: sqlite3.Connection,
    runtime_run_id: str,
    operation_id: str,
    safe_retry_commit_ref: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM runtime_control_source_dispatch_outbox
        WHERE runtime_run_id = ? AND operation_id = ?
          AND safe_retry_commit_ref = ?
        """,
        (runtime_run_id, operation_id, safe_retry_commit_ref),
    ).fetchone()


def _require_opaque(
    value: object,
    field: str,
    *,
    max_bytes: int,
) -> str:
    reason_code = f"source_safe_retry_{field}_invalid"
    if not isinstance(value, str) or not value or value != value.strip():
        raise RuntimeControlError(reason_code)
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise RuntimeControlError(reason_code) from None
    if len(encoded) > max_bytes or any(unicodedata.category(character) == "Cc" for character in value):
        raise RuntimeControlError(reason_code)
    return value


def _require_positive_json_integer(
    value: object,
    field: str,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _JSON_SAFE_INTEGER_MAX:
        raise RuntimeControlError(f"source_safe_retry_{field}_invalid")
    return value


def _inject_fault(
    fault_injector: Callable[[str], None] | None,
    point: str,
) -> None:
    if fault_injector is not None:
        fault_injector(point)
