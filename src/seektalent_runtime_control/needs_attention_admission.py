"""Authenticated, main-bound admission for needs-attention lifecycle writes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
import sqlite3
from typing import Never
import weakref

from seektalent.source_port.authenticated_verify_session_frames import (
    ReceivedVerifySessionResult,
    VerifySessionFrameError,
    require_authenticated_verify_session_result,
)
from seektalent.source_port.verify_session_contract import VerifySessionResultV1
from seektalent.source_port.operation_dispatch import DispatchAuthorizationV1
from seektalent.user_action import UserActionV1
from seektalent_runtime_control.errors import RuntimeControlError
from seektalent_runtime_control.models import RuntimeCheckpoint
from seektalent_runtime_control.source_reconciliation import (
    SourceOperationReconciliationDecision,
)
from seektalent_runtime_control.user_action_mapping import (
    map_verify_session_user_action,
)


@dataclass(frozen=True, slots=True)
class NeedsAttentionAdmissionData:
    action: UserActionV1
    runtime_run_id: str
    operation_id: str
    checkpoint_id: str
    entry_observation_ref: str
    entry_observation_digest: str
    accepted_requirement_revision_id: str
    runtime_attempt_no: int
    runtime_attempt_fence_ref: str
    request_hash: str
    request_semantic_digest: str
    profile_binding_generation: int
    browser_control_scope_id: str
    source_ledger_revision: int
    source_reconciliation_revision: int
    dispatch_authorization_ordinal: int
    dispatch_intent_id: str
    dispatch_intent_digest: str
    source_operation_acceptance_ref: str
    reconciliation_id: str | None
    reconciliation_digest: str | None
    authority_mode: str
    owner_lease_id: str | None


@dataclass(frozen=True, slots=True)
class ActionSatisfactionData:
    action: UserActionV1
    runtime_run_id: str
    operation_id: str
    checkpoint_id: str
    authenticated_evidence_ref: str
    authenticated_observation_digest: str
    resolution_binding_digest: str
    current_profile_binding_ref: str
    current_profile_binding_generation: int
    current_browser_control_scope_id: str
    result_digest: str
    request_hash: str
    request_semantic_digest: str
    runtime_attempt_fence_ref: str
    reconciliation_id: str
    reconciliation_digest: str
    source_ledger_revision: int
    source_reconciliation_revision: int
    dispatch_authorization_ordinal: int


@dataclass(frozen=True, slots=True)
class _CommittedObservation:
    result: VerifySessionResultV1
    dispatch_authorization: DispatchAuthorizationV1
    request_semantic_digest: str
    observation_ref: str
    result_digest: str


class NeedsAttentionAdmission:
    __slots__ = ("__weakref__",)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("NeedsAttentionAdmission is factory-only")

    def __copy__(self) -> Never:
        raise TypeError("NeedsAttentionAdmission cannot be copied")

    def __deepcopy__(self, _: dict[int, object]) -> Never:
        raise TypeError("NeedsAttentionAdmission cannot be copied")

    def __reduce_ex__(self, _: object) -> Never:
        raise TypeError("NeedsAttentionAdmission cannot be serialized")


class ActionSatisfactionAdmission:
    __slots__ = ("__weakref__",)

    def __init__(self, *_: object, **__: object) -> None:
        raise TypeError("ActionSatisfactionAdmission is factory-only")

    def __copy__(self) -> Never:
        raise TypeError("ActionSatisfactionAdmission cannot be copied")

    def __deepcopy__(self, _: dict[int, object]) -> Never:
        raise TypeError("ActionSatisfactionAdmission cannot be copied")

    def __reduce_ex__(self, _: object) -> Never:
        raise TypeError("ActionSatisfactionAdmission cannot be serialized")


_ENTRY_ADMISSIONS: weakref.WeakKeyDictionary[
    NeedsAttentionAdmission,
    NeedsAttentionAdmissionData,
] = weakref.WeakKeyDictionary()
_SATISFACTION_ADMISSIONS: weakref.WeakKeyDictionary[
    ActionSatisfactionAdmission,
    ActionSatisfactionData,
] = weakref.WeakKeyDictionary()


def admit_needs_attention(
    conn: sqlite3.Connection,
    *,
    received: ReceivedVerifySessionResult,
    checkpoint: RuntimeCheckpoint,
) -> NeedsAttentionAdmission:
    observation = _commit_authenticated_observation(conn, received)
    result = observation.result
    if result.session_readiness != "not_ready" or result.user_action is None:
        raise RuntimeControlError("runtime_needs_attention_admission_rejected")
    action = map_verify_session_user_action(
        result.user_action,
        affected_scope_ref=result.identity.browser_control_scope_id,
    )
    source = _require_entry_source_truth(
        conn,
        result=result,
        dispatch_authorization=observation.dispatch_authorization,
        result_digest=observation.result_digest,
        request_semantic_digest=observation.request_semantic_digest,
    )
    if checkpoint.runtime_run_id != result.identity.run_id:
        raise RuntimeControlError("runtime_needs_attention_admission_rejected")
    admission = object.__new__(NeedsAttentionAdmission)
    _ENTRY_ADMISSIONS[admission] = NeedsAttentionAdmissionData(
        action=action,
        runtime_run_id=result.identity.run_id,
        operation_id=result.identity.operation_id,
        checkpoint_id=checkpoint.checkpoint_id,
        entry_observation_ref=observation.observation_ref,
        entry_observation_digest=observation.result_digest,
        accepted_requirement_revision_id=(
            result.identity.accepted_requirement_revision_id
        ),
        runtime_attempt_no=result.identity.attempt_no,
        runtime_attempt_fence_ref=result.identity.runtime_attempt_fence_ref,
        request_hash=result.identity.request_hash,
        request_semantic_digest=observation.request_semantic_digest,
        profile_binding_generation=result.identity.profile_binding_generation,
        browser_control_scope_id=result.identity.browser_control_scope_id,
        source_ledger_revision=_required_int(source["ledger_revision"]),
        source_reconciliation_revision=_required_int(
            source["reconciliation_revision"]
        ),
        dispatch_authorization_ordinal=(
            observation.dispatch_authorization.dispatch_authorization_ordinal
        ),
        dispatch_intent_id=_required_string(source["dispatch_intent_id"]),
        dispatch_intent_digest=_required_string(
            source["dispatch_intent_digest"]
        ),
        source_operation_acceptance_ref=_required_string(
            source["source_operation_acceptance_ref"]
        ),
        reconciliation_id=_optional_string(source["reconciliation_id"]),
        reconciliation_digest=_optional_string(source["reconciliation_digest"]),
        authority_mode=str(source["authority_mode"]),
        owner_lease_id=_optional_string(source["owner_lease_id"]),
    )
    return admission


def admit_action_satisfaction(
    conn: sqlite3.Connection,
    *,
    action_id: str,
    received: ReceivedVerifySessionResult,
) -> ActionSatisfactionAdmission:
    observation = _commit_authenticated_observation(conn, received)
    result = observation.result
    run = _run_row(conn, result.identity.run_id)
    action_row = _action_row(conn, action_id)
    if (
        result.session_readiness != "ready"
        or result.user_action is not None
        or result.actual_profile_binding_ref is None
        or run is None
        or run["status"] != "needs_attention"
        or run["current_action_id"] != action_id
        or action_row is None
        or action_row["runtime_run_id"] != result.identity.run_id
    ):
        raise RuntimeControlError(
            "runtime_needs_attention_satisfaction_rejected"
        )
    action = canonical_action_from_row(action_row)
    source = _require_satisfaction_source_truth(
        conn,
        action_row=action_row,
        result=result,
        dispatch_authorization=observation.dispatch_authorization,
        result_digest=observation.result_digest,
        request_hash=result.identity.request_hash,
        request_semantic_digest=observation.request_semantic_digest,
        runtime_attempt_fence_ref=result.identity.runtime_attempt_fence_ref,
    )
    observation_row = observation_row_by_ref(
        conn,
        observation.observation_ref,
    )
    reconciliation_row = _reconciliation_row(
        conn,
        _required_string(source["reconciliation_id"]),
    )
    binding = satisfaction_binding_digest(
        action_row=action_row,
        observation_row=observation_row,
        reconciliation_row=reconciliation_row,
    )
    admission = object.__new__(ActionSatisfactionAdmission)
    _SATISFACTION_ADMISSIONS[admission] = ActionSatisfactionData(
        action=action,
        runtime_run_id=result.identity.run_id,
        operation_id=result.identity.operation_id,
        checkpoint_id=action_row["checkpoint_id"],
        authenticated_evidence_ref=observation.observation_ref,
        authenticated_observation_digest=observation.result_digest,
        resolution_binding_digest=binding,
        current_profile_binding_ref=result.actual_profile_binding_ref,
        current_profile_binding_generation=(
            result.actual_profile_binding_generation
        ),
        current_browser_control_scope_id=(
            result.identity.browser_control_scope_id
        ),
        result_digest=observation.result_digest,
        request_hash=result.identity.request_hash,
        request_semantic_digest=observation.request_semantic_digest,
        runtime_attempt_fence_ref=result.identity.runtime_attempt_fence_ref,
        reconciliation_id=_required_string(source["reconciliation_id"]),
        reconciliation_digest=_required_string(
            source["reconciliation_digest"]
        ),
        source_ledger_revision=_required_int(source["ledger_revision"]),
        source_reconciliation_revision=_required_int(
            source["reconciliation_revision"]
        ),
        dispatch_authorization_ordinal=(
            observation.dispatch_authorization.dispatch_authorization_ordinal
        ),
    )
    return admission


def entry_admission_data(
    admission: NeedsAttentionAdmission,
) -> NeedsAttentionAdmissionData:
    if type(admission) is not NeedsAttentionAdmission:
        raise RuntimeControlError(
            "runtime_needs_attention_admission_rejected"
        )
    data = _ENTRY_ADMISSIONS.get(admission)
    if data is None:
        raise RuntimeControlError(
            "runtime_needs_attention_admission_rejected"
        )
    return data


def satisfaction_admission_data(
    admission: ActionSatisfactionAdmission,
) -> ActionSatisfactionData:
    if type(admission) is not ActionSatisfactionAdmission:
        raise RuntimeControlError(
            "runtime_needs_attention_satisfaction_rejected"
        )
    data = _SATISFACTION_ADMISSIONS.get(admission)
    if data is None:
        raise RuntimeControlError(
            "runtime_needs_attention_satisfaction_rejected"
        )
    return data


def require_committed_entry_admission(
    conn: sqlite3.Connection,
    *,
    data: NeedsAttentionAdmissionData,
    authority_mode: str,
    owner_lease_id: str | None,
) -> None:
    run = _run_row(conn, data.runtime_run_id)
    observation = observation_row_by_ref(conn, data.entry_observation_ref)
    operation, expectation = _operation_and_expectation(
        conn,
        runtime_run_id=data.runtime_run_id,
        operation_id=data.operation_id,
        attempt_no=data.runtime_attempt_no,
        dispatch_authorization_ordinal=data.dispatch_authorization_ordinal,
    )
    dispatch = conn.execute(
        """
        SELECT * FROM runtime_control_source_dispatch_outbox
        WHERE runtime_run_id = ? AND operation_id = ?
          AND dispatch_authorization_ordinal = ?
        """,
        (
            data.runtime_run_id,
            data.operation_id,
            data.dispatch_authorization_ordinal,
        ),
    ).fetchone()
    try:
        source_ids = json.loads(run["source_ids_json"]) if run else None
    except (TypeError, json.JSONDecodeError):
        source_ids = None
    if (
        authority_mode != data.authority_mode
        or owner_lease_id != data.owner_lease_id
        or run is None
        or source_ids != ["liepin"]
        or run["approved_requirement_revision_id"]
        != data.accepted_requirement_revision_id
        or observation is None
        or operation is None
        or expectation is None
        or dispatch is None
        or observation["result_digest"] != data.entry_observation_digest
        or observation["runtime_run_id"] != data.runtime_run_id
        or observation["operation_id"] != data.operation_id
        or observation["dispatch_authorization_ordinal"]
        != data.dispatch_authorization_ordinal
        or observation["dispatch_intent_id"] != data.dispatch_intent_id
        or observation["dispatch_intent_digest"]
        != data.dispatch_intent_digest
        or observation["source_operation_acceptance_ref"]
        != data.source_operation_acceptance_ref
        or observation["source_id"] != "liepin"
        or observation["operation_kind"] != "verify_session"
        or observation["accepted_requirement_revision_id"]
        != data.accepted_requirement_revision_id
        or observation["runtime_attempt_no"] != data.runtime_attempt_no
        or observation["runtime_attempt_fence_ref"]
        != data.runtime_attempt_fence_ref
        or observation["request_hash"] != data.request_hash
        or observation["request_semantic_digest"]
        != data.request_semantic_digest
        or observation["profile_binding_generation"]
        != data.profile_binding_generation
        or observation["browser_control_scope_id"]
        != data.browser_control_scope_id
        or observation["action_digest"]
        != _action_digest(data.action)
        or operation["ledger_revision"] != data.source_ledger_revision
        or operation["reconciliation_revision"]
        != data.source_reconciliation_revision
        or operation["canonical_request_hash"] != data.request_hash
        or operation["source_id"] != observation["source_id"]
        or operation["operation_kind"] != observation["operation_kind"]
        or operation["idempotency_key"] != observation["idempotency_key"]
        or operation["accepted_requirement_revision_id"]
        != data.accepted_requirement_revision_id
        or expectation["runtime_attempt_no"] != data.runtime_attempt_no
        or expectation["runtime_attempt_fence_ref"]
        != data.runtime_attempt_fence_ref
        or expectation["profile_binding_generation"]
        != data.profile_binding_generation
        or expectation["browser_control_scope_id"]
        != data.browser_control_scope_id
        or dispatch["canonical_request_hash"] != data.request_hash
        or dispatch["dispatch_authorization_ordinal"]
        != data.dispatch_authorization_ordinal
        or dispatch["dispatch_intent_id"] != data.dispatch_intent_id
        or dispatch["dispatch_intent_digest"] != data.dispatch_intent_digest
        or dispatch["source_operation_acceptance_ref"]
        != data.source_operation_acceptance_ref
        or dispatch["expected_ledger_revision"]
        != observation["expected_ledger_revision"]
        or dispatch["expected_reconciliation_revision"]
        != observation["expected_reconciliation_revision"]
    ):
        _entry_rejected()
    if authority_mode == "no_owner":
        reconciliation = _reconciliation_row(conn, data.reconciliation_id)
        if (
            reconciliation is None
            or data.reconciliation_digest != _row_digest(reconciliation)
            or not _history_reconciliation_binding_is_valid(reconciliation)
            or reconciliation["conclusive_observation_ref"]
            != data.entry_observation_digest
            or reconciliation["source_operation_disposition"]
            != "user_action_required"
        ):
            _entry_rejected()


def require_committed_satisfaction_admission(
    conn: sqlite3.Connection,
    *,
    action_row: sqlite3.Row,
    data: ActionSatisfactionData,
) -> None:
    run = _run_row(conn, data.runtime_run_id)
    observation = observation_row_by_ref(
        conn,
        data.authenticated_evidence_ref,
    )
    entry_observation = observation_row_by_ref(
        conn,
        action_row["entry_observation_ref"],
    )
    operation, expectation = _operation_and_expectation(
        conn,
        runtime_run_id=data.runtime_run_id,
        operation_id=data.operation_id,
        attempt_no=int(action_row["runtime_attempt_no"]),
        dispatch_authorization_ordinal=data.dispatch_authorization_ordinal,
    )
    dispatch = conn.execute(
        """
        SELECT * FROM runtime_control_source_dispatch_outbox
        WHERE runtime_run_id = ? AND operation_id = ?
          AND dispatch_authorization_ordinal = ?
        """,
        (
            data.runtime_run_id,
            data.operation_id,
            data.dispatch_authorization_ordinal,
        ),
    ).fetchone()
    reconciliation = _reconciliation_row(conn, data.reconciliation_id)
    try:
        source_ids = json.loads(run["source_ids_json"]) if run else None
    except (TypeError, json.JSONDecodeError):
        source_ids = None
    if (
        run is None
        or source_ids != ["liepin"]
        or run["status"] != "needs_attention"
        or run["current_action_id"] != action_row["action_id"]
        or run["approved_requirement_revision_id"]
        != action_row["accepted_requirement_revision_id"]
        or observation is None
        or entry_observation is None
        or operation is None
        or expectation is None
        or dispatch is None
        or reconciliation is None
        or data.operation_id == action_row["operation_id"]
        or observation["result_digest"]
        != data.authenticated_observation_digest
        or observation["result_digest"] != data.result_digest
        or observation["session_readiness"] != "ready"
        or observation["action_digest"] is not None
        or observation["runtime_run_id"] != data.runtime_run_id
        or observation["operation_id"] != data.operation_id
        or observation["dispatch_authorization_ordinal"]
        != data.dispatch_authorization_ordinal
        or observation["browser_control_scope_id"]
        != data.current_browser_control_scope_id
        or observation["actual_profile_binding_ref"]
        != data.current_profile_binding_ref
        or observation["actual_profile_binding_generation"]
        != data.current_profile_binding_generation
        or satisfaction_binding_digest(
            action_row=action_row,
            observation_row=observation,
            reconciliation_row=reconciliation,
        )
        != data.resolution_binding_digest
        or observation["request_hash"] != data.request_hash
        or observation["request_semantic_digest"]
        != data.request_semantic_digest
        or observation["request_semantic_digest"]
        != action_row["entry_request_semantic_digest"]
        or observation["accepted_requirement_revision_id"]
        != action_row["accepted_requirement_revision_id"]
        or observation["runtime_attempt_no"]
        != action_row["runtime_attempt_no"]
        or observation["runtime_attempt_fence_ref"]
        != data.runtime_attempt_fence_ref
        or observation["profile_binding_generation"]
        != action_row["profile_binding_generation"]
        or observation["browser_control_scope_id"]
        != action_row["browser_control_scope_id"]
        or observation["actual_profile_binding_ref"]
        != entry_observation["actual_profile_binding_ref"]
        or operation["canonical_request_hash"] != data.request_hash
        or operation["idempotency_key"] != observation["idempotency_key"]
        or operation["source_id"] != "liepin"
        or operation["operation_kind"] != "verify_session"
        or operation["accepted_requirement_revision_id"]
        != action_row["accepted_requirement_revision_id"]
        or operation["ledger_revision"] != data.source_ledger_revision
        or operation["reconciliation_revision"]
        != data.source_reconciliation_revision
        or operation["operation_phase"] != "reconciled"
        or operation["source_operation_disposition"] != "completed"
        or operation["retry_posture"] != "no_retry"
        or expectation["runtime_attempt_no"]
        != action_row["runtime_attempt_no"]
        or expectation["runtime_attempt_fence_ref"]
        != data.runtime_attempt_fence_ref
        or expectation["profile_binding_generation"]
        != action_row["profile_binding_generation"]
        or expectation["browser_control_scope_id"]
        != action_row["browser_control_scope_id"]
        or dispatch["canonical_request_hash"] != data.request_hash
        or dispatch["dispatch_authorization_ordinal"]
        != data.dispatch_authorization_ordinal
        or dispatch["dispatch_intent_id"] != observation["dispatch_intent_id"]
        or dispatch["dispatch_intent_digest"]
        != observation["dispatch_intent_digest"]
        or dispatch["source_operation_acceptance_ref"]
        != observation["source_operation_acceptance_ref"]
        or dispatch["expected_ledger_revision"]
        != observation["expected_ledger_revision"]
        or dispatch["expected_reconciliation_revision"]
        != observation["expected_reconciliation_revision"]
        or reconciliation["reconciliation_id"] != data.reconciliation_id
        or _row_digest(reconciliation) != data.reconciliation_digest
        or not _history_reconciliation_binding_is_valid(reconciliation)
        or reconciliation["conclusive_observation_ref"] != data.result_digest
        or reconciliation["runtime_run_id"] != data.runtime_run_id
        or reconciliation["operation_id"] != data.operation_id
        or reconciliation["canonical_request_hash"] != data.request_hash
        or reconciliation["accepted_requirement_revision_id"]
        != action_row["accepted_requirement_revision_id"]
        or reconciliation["runtime_attempt_no"]
        != action_row["runtime_attempt_no"]
        or reconciliation["source_operation_disposition"] != "completed"
        or reconciliation["retry_posture"] != "no_retry"
        or reconciliation["committed_ledger_revision"]
        != data.source_ledger_revision
        or reconciliation["committed_reconciliation_revision"]
        != data.source_reconciliation_revision
        or _active_lease_row(conn, data.runtime_run_id) is not None
    ):
        raise RuntimeControlError(
            "runtime_needs_attention_satisfaction_rejected"
        )


def satisfaction_binding_digest(
    *,
    action_row: sqlite3.Row,
    observation_row: sqlite3.Row | None,
    reconciliation_row: sqlite3.Row | None,
) -> str:
    if observation_row is None or reconciliation_row is None:
        raise RuntimeControlError(
            "runtime_needs_attention_satisfaction_rejected"
        )
    return sha256(
        _canonical_json(
            {
                "action": canonical_action_from_row(action_row).model_dump(
                    mode="json"
                ),
                "runtimeRunId": action_row["runtime_run_id"],
                "entryOperationId": action_row["operation_id"],
                "resolutionOperationId": observation_row["operation_id"],
                "checkpointId": action_row["checkpoint_id"],
                "observationRef": observation_row["observation_ref"],
                "observationDigest": observation_row["result_digest"],
                "requestHash": observation_row["request_hash"],
                "requestSemanticDigest": (
                    observation_row["request_semantic_digest"]
                ),
                "acceptedRequirementRevisionId": (
                    observation_row["accepted_requirement_revision_id"]
                ),
                "attemptNo": observation_row["runtime_attempt_no"],
                "runtimeAttemptFenceRef": (
                    observation_row["runtime_attempt_fence_ref"]
                ),
                "profileBindingRef": (
                    observation_row["actual_profile_binding_ref"]
                ),
                "profileBindingGeneration": (
                    observation_row["actual_profile_binding_generation"]
                ),
                "browserControlScopeId": (
                    observation_row["browser_control_scope_id"]
                ),
                "dispatchAuthorizationOrdinal": (
                    observation_row["dispatch_authorization_ordinal"]
                ),
                "dispatchIntentId": observation_row["dispatch_intent_id"],
                "dispatchIntentDigest": (
                    observation_row["dispatch_intent_digest"]
                ),
                "sourceOperationAcceptanceRef": (
                    observation_row["source_operation_acceptance_ref"]
                ),
                "reconciliationId": reconciliation_row["reconciliation_id"],
                "reconciliationDigest": _row_digest(reconciliation_row),
                "sourceLedgerRevision": (
                    reconciliation_row["committed_ledger_revision"]
                ),
                "sourceReconciliationRevision": (
                    reconciliation_row["committed_reconciliation_revision"]
                ),
            }
        )
    ).hexdigest()


def canonical_action_from_row(row: sqlite3.Row) -> UserActionV1:
    try:
        return UserActionV1(
            code=row["action_code"],
            instruction_key=row["instruction_key"],
            scope=row["action_scope"],
            affected_scope_ref=row["affected_scope_ref"],
        )
    except ValueError:
        raise RuntimeControlError(
            "runtime_needs_attention_integrity_failed"
        ) from None


def observation_row_by_ref(
    conn: sqlite3.Connection,
    observation_ref: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM runtime_control_authenticated_observations
        WHERE observation_ref = ?
        """,
        (observation_ref,),
    ).fetchone()


def reconciliation_digest(row: sqlite3.Row) -> str:
    return _row_digest(row)


def _commit_authenticated_observation(
    conn: sqlite3.Connection,
    received: ReceivedVerifySessionResult,
) -> _CommittedObservation:
    try:
        authenticated = require_authenticated_verify_session_result(received)
    except VerifySessionFrameError:
        raise RuntimeControlError(
            "runtime_needs_attention_admission_rejected"
        ) from None
    result = authenticated.result
    action_digest = (
        None
        if result.user_action is None
        else _action_digest(
            map_verify_session_user_action(
                result.user_action,
                affected_scope_ref=result.identity.browser_control_scope_id,
            )
        )
    )
    values = {
        "observation_ref": authenticated.observation_ref,
        "result_digest": authenticated.result_digest,
        "session_id": authenticated.session_id,
        "direction_seq": authenticated.direction_seq,
        "message_id": authenticated.message_id,
        "reply_to": authenticated.reply_to,
        "runtime_run_id": result.identity.run_id,
        "operation_id": result.identity.operation_id,
        "source_id": result.identity.source,
        "operation_kind": result.identity.operation_kind,
        "request_hash": result.identity.request_hash,
        "request_semantic_digest": authenticated.request_semantic_digest,
        "idempotency_key": result.identity.idempotency_key,
        "accepted_requirement_revision_id": (
            result.identity.accepted_requirement_revision_id
        ),
        "runtime_attempt_no": result.identity.attempt_no,
        "runtime_attempt_fence_ref": (
            result.identity.runtime_attempt_fence_ref
        ),
        "expected_ledger_revision": (
            result.identity.expected_source_operation_ledger_revision
        ),
        "expected_reconciliation_revision": (
            result.identity.expected_reconciliation_revision
        ),
        "profile_binding_generation": (
            result.identity.profile_binding_generation
        ),
        "browser_control_scope_id": (
            result.identity.browser_control_scope_id
        ),
        "actual_profile_binding_ref": result.actual_profile_binding_ref,
        "actual_profile_binding_generation": (
            result.actual_profile_binding_generation
        ),
        "session_readiness": result.session_readiness,
        "action_digest": action_digest,
        "dispatch_authorization_ordinal": (
            authenticated.dispatch_authorization.dispatch_authorization_ordinal
        ),
        "dispatch_intent_id": (
            authenticated.dispatch_authorization.dispatch_intent_id
        ),
        "dispatch_intent_digest": (
            authenticated.dispatch_authorization.dispatch_intent_digest
        ),
        "source_operation_acceptance_ref": (
            authenticated.dispatch_authorization.source_operation_acceptance_ref
        ),
    }
    existing = observation_row_by_ref(conn, authenticated.observation_ref)
    if existing is not None:
        if any(existing[key] != value for key, value in values.items()):
            _entry_rejected()
        return _CommittedObservation(
            result=result,
            dispatch_authorization=authenticated.dispatch_authorization,
            request_semantic_digest=authenticated.request_semantic_digest,
            observation_ref=authenticated.observation_ref,
            result_digest=authenticated.result_digest,
        )
    committed_at = datetime.now(UTC).isoformat(timespec="microseconds").replace(
        "+00:00",
        "Z",
    )
    columns = (*values.keys(), "committed_at")
    try:
        conn.execute(
            f"""
            INSERT INTO runtime_control_authenticated_observations
              ({", ".join(columns)})
            VALUES ({", ".join("?" for _ in columns)})
            """,
            (*values.values(), committed_at),
        )
    except sqlite3.IntegrityError:
        _entry_rejected()
    return _CommittedObservation(
        result=result,
        dispatch_authorization=authenticated.dispatch_authorization,
        request_semantic_digest=authenticated.request_semantic_digest,
        observation_ref=authenticated.observation_ref,
        result_digest=authenticated.result_digest,
    )


def _require_entry_source_truth(
    conn: sqlite3.Connection,
    *,
    result: VerifySessionResultV1,
    dispatch_authorization: DispatchAuthorizationV1,
    result_digest: str,
    request_semantic_digest: str,
) -> dict[str, object]:
    identity = result.identity
    run = _run_row(conn, identity.run_id)
    operation, expectation = _operation_and_expectation(
        conn,
        runtime_run_id=identity.run_id,
        operation_id=identity.operation_id,
        attempt_no=identity.attempt_no,
        dispatch_authorization_ordinal=(
            dispatch_authorization.dispatch_authorization_ordinal
        ),
    )
    ordinal = dispatch_authorization.dispatch_authorization_ordinal
    dispatch = conn.execute(
        """
        SELECT * FROM runtime_control_source_dispatch_outbox
        WHERE runtime_run_id = ? AND operation_id = ?
          AND dispatch_authorization_ordinal = ?
        """,
        (identity.run_id, identity.operation_id, ordinal),
    ).fetchone()
    active_lease = _active_lease_row(conn, identity.run_id)
    try:
        source_ids = json.loads(run["source_ids_json"]) if run else None
    except (TypeError, json.JSONDecodeError):
        source_ids = None
    if (
        run is None
        or source_ids != ["liepin"]
        or run["approved_requirement_revision_id"]
        != identity.accepted_requirement_revision_id
        or operation is None
        or expectation is None
        or dispatch is None
        or operation["source_id"] != "liepin"
        or operation["operation_kind"] != "verify_session"
        or operation["canonical_request_hash"] != identity.request_hash
        or operation["idempotency_key"] != identity.idempotency_key
        or operation["accepted_requirement_revision_id"]
        != identity.accepted_requirement_revision_id
        or expectation["runtime_attempt_fence_ref"]
        != identity.runtime_attempt_fence_ref
        or expectation["profile_binding_generation"]
        != identity.profile_binding_generation
        or expectation["browser_control_scope_id"]
        != identity.browser_control_scope_id
        or dispatch["canonical_request_hash"] != identity.request_hash
        or dispatch["dispatch_authorization_ordinal"] != ordinal
        or dispatch["dispatch_intent_id"]
        != dispatch_authorization.dispatch_intent_id
        or dispatch["dispatch_intent_digest"]
        != dispatch_authorization.dispatch_intent_digest
        or dispatch["source_operation_acceptance_ref"]
        != dispatch_authorization.source_operation_acceptance_ref
        or dispatch["expected_ledger_revision"]
        != identity.expected_source_operation_ledger_revision
        or dispatch["expected_reconciliation_revision"]
        != identity.expected_reconciliation_revision
    ):
        _entry_rejected()
    if active_lease is not None:
        if (
            run["status"] not in {"starting", "running", "pause_requested"}
            or active_lease["attempt_no"] != identity.attempt_no
            or operation["ledger_revision"]
            != identity.expected_source_operation_ledger_revision
            or operation["reconciliation_revision"]
            != identity.expected_reconciliation_revision
            or (
                operation["operation_phase"]
                not in {"accepted", "dispatch_intent"}
                and not (
                    ordinal > 1
                    and operation["operation_phase"] == "reconciled"
                )
            )
            or operation["main_commit_ref"] is not None
        ):
            _entry_rejected()
        return {
            "ledger_revision": operation["ledger_revision"],
            "reconciliation_revision": operation["reconciliation_revision"],
            "reconciliation_id": None,
            "reconciliation_digest": None,
            "dispatch_intent_id": dispatch["dispatch_intent_id"],
            "dispatch_intent_digest": dispatch["dispatch_intent_digest"],
            "source_operation_acceptance_ref": (
                dispatch["source_operation_acceptance_ref"]
            ),
            "authority_mode": "active_owner",
            "owner_lease_id": active_lease["lease_id"],
        }
    reconciliation = conn.execute(
        """
        SELECT * FROM runtime_control_source_reconciliations
        WHERE runtime_run_id = ? AND operation_id = ?
        ORDER BY committed_reconciliation_revision DESC LIMIT 1
        """,
        (identity.run_id, identity.operation_id),
    ).fetchone()
    if (
        run["status"] != "resume_requested"
        or reconciliation is None
        or operation["operation_phase"] != "reconciled"
        or operation["source_operation_disposition"]
        != "user_action_required"
        or operation["retry_posture"] != "no_retry"
        or reconciliation["decision_kind"] != "conclusive_observation"
        or reconciliation["history_outcome"] != "matched"
        or reconciliation["history_conclusion"] != "observed_result"
        or reconciliation["source_operation_disposition"]
        != "user_action_required"
        or reconciliation["conclusive_observation_ref"] != result_digest
        or not _history_reconciliation_binding_is_valid(reconciliation)
        or reconciliation["accepted_requirement_revision_id"]
        != identity.accepted_requirement_revision_id
        or reconciliation["runtime_attempt_no"] != identity.attempt_no
        or reconciliation["canonical_request_hash"] != identity.request_hash
        or reconciliation["expected_ledger_revision"]
        != identity.expected_source_operation_ledger_revision
        or reconciliation["expected_reconciliation_revision"]
        != identity.expected_reconciliation_revision
        or reconciliation["committed_ledger_revision"]
        != operation["ledger_revision"]
        or reconciliation["committed_reconciliation_revision"]
        != operation["reconciliation_revision"]
    ):
        _entry_rejected()
    return {
        "ledger_revision": operation["ledger_revision"],
        "reconciliation_revision": operation["reconciliation_revision"],
        "reconciliation_id": reconciliation["reconciliation_id"],
        "reconciliation_digest": _row_digest(reconciliation),
        "dispatch_intent_id": dispatch["dispatch_intent_id"],
        "dispatch_intent_digest": dispatch["dispatch_intent_digest"],
        "source_operation_acceptance_ref": (
            dispatch["source_operation_acceptance_ref"]
        ),
        "authority_mode": "no_owner",
        "owner_lease_id": None,
    }


def _require_satisfaction_source_truth(
    conn: sqlite3.Connection,
    *,
    action_row: sqlite3.Row,
    result: VerifySessionResultV1,
    dispatch_authorization: DispatchAuthorizationV1,
    result_digest: str,
    request_hash: str,
    request_semantic_digest: str,
    runtime_attempt_fence_ref: str,
) -> dict[str, object]:
    entry = observation_row_by_ref(
        conn,
        action_row["entry_observation_ref"],
    )
    identity = result.identity
    ordinal = dispatch_authorization.dispatch_authorization_ordinal
    operation, expectation = _operation_and_expectation(
        conn,
        runtime_run_id=identity.run_id,
        operation_id=identity.operation_id,
        attempt_no=identity.attempt_no,
        dispatch_authorization_ordinal=ordinal,
    )
    dispatch = conn.execute(
        """
        SELECT * FROM runtime_control_source_dispatch_outbox
        WHERE runtime_run_id = ? AND operation_id = ?
          AND dispatch_authorization_ordinal = ?
        """,
        (identity.run_id, identity.operation_id, ordinal),
    ).fetchone()
    reconciliation = conn.execute(
        """
        SELECT * FROM runtime_control_source_reconciliations
        WHERE runtime_run_id = ? AND operation_id = ?
        ORDER BY committed_reconciliation_revision DESC LIMIT 1
        """,
        (identity.run_id, identity.operation_id),
    ).fetchone()
    if (
        entry is None
        or operation is None
        or expectation is None
        or dispatch is None
        or reconciliation is None
        or identity.operation_id == action_row["operation_id"]
        or request_semantic_digest
        != action_row["entry_request_semantic_digest"]
        or identity.request_hash != request_hash
        or identity.runtime_attempt_fence_ref != runtime_attempt_fence_ref
        or identity.accepted_requirement_revision_id
        != action_row["accepted_requirement_revision_id"]
        or identity.attempt_no != action_row["runtime_attempt_no"]
        or identity.profile_binding_generation
        != action_row["profile_binding_generation"]
        or identity.browser_control_scope_id
        != action_row["browser_control_scope_id"]
        or identity.browser_control_scope_id
        != action_row["affected_scope_ref"]
        or result.actual_profile_binding_ref
        != entry["actual_profile_binding_ref"]
        or result.actual_profile_binding_generation
        < entry["actual_profile_binding_generation"]
        or operation["canonical_request_hash"] != identity.request_hash
        or operation["idempotency_key"] != identity.idempotency_key
        or operation["source_id"] != "liepin"
        or operation["operation_kind"] != "verify_session"
        or operation["accepted_requirement_revision_id"]
        != identity.accepted_requirement_revision_id
        or operation["operation_phase"] != "reconciled"
        or operation["source_operation_disposition"] != "completed"
        or operation["retry_posture"] != "no_retry"
        or expectation["runtime_attempt_no"] != identity.attempt_no
        or expectation["runtime_attempt_fence_ref"]
        != identity.runtime_attempt_fence_ref
        or expectation["profile_binding_generation"]
        != identity.profile_binding_generation
        or expectation["browser_control_scope_id"]
        != identity.browser_control_scope_id
        or dispatch["dispatch_authorization_ordinal"] != ordinal
        or dispatch["dispatch_intent_id"]
        != dispatch_authorization.dispatch_intent_id
        or dispatch["dispatch_intent_digest"]
        != dispatch_authorization.dispatch_intent_digest
        or dispatch["source_operation_acceptance_ref"]
        != dispatch_authorization.source_operation_acceptance_ref
        or dispatch["canonical_request_hash"] != identity.request_hash
        or dispatch["expected_ledger_revision"]
        != identity.expected_source_operation_ledger_revision
        or dispatch["expected_reconciliation_revision"]
        != identity.expected_reconciliation_revision
        or reconciliation["decision_kind"] != "conclusive_observation"
        or reconciliation["history_outcome"] != "matched"
        or reconciliation["history_conclusion"] != "observed_result"
        or reconciliation["source_operation_disposition"] != "completed"
        or reconciliation["retry_posture"] != "no_retry"
        or reconciliation["conclusive_observation_ref"] != result_digest
        or not _history_reconciliation_binding_is_valid(reconciliation)
        or reconciliation["accepted_requirement_revision_id"]
        != identity.accepted_requirement_revision_id
        or reconciliation["runtime_attempt_no"] != identity.attempt_no
        or reconciliation["canonical_request_hash"] != identity.request_hash
        or reconciliation["expected_ledger_revision"]
        != identity.expected_source_operation_ledger_revision
        or reconciliation["expected_reconciliation_revision"]
        != identity.expected_reconciliation_revision
        or reconciliation["committed_ledger_revision"]
        != operation["ledger_revision"]
        or reconciliation["committed_reconciliation_revision"]
        != operation["reconciliation_revision"]
        or _active_lease_row(conn, identity.run_id) is not None
    ):
        raise RuntimeControlError(
            "runtime_needs_attention_satisfaction_rejected"
        )
    return {
        "ledger_revision": operation["ledger_revision"],
        "reconciliation_revision": operation["reconciliation_revision"],
        "reconciliation_id": reconciliation["reconciliation_id"],
        "reconciliation_digest": _row_digest(reconciliation),
    }


def _operation_and_expectation(
    conn: sqlite3.Connection,
    *,
    runtime_run_id: str,
    operation_id: str,
    attempt_no: int,
    dispatch_authorization_ordinal: int,
) -> tuple[sqlite3.Row | None, sqlite3.Row | None]:
    operation = conn.execute(
        """
        SELECT * FROM runtime_control_source_operations
        WHERE runtime_run_id = ? AND operation_id = ?
        """,
        (runtime_run_id, operation_id),
    ).fetchone()
    expectation = conn.execute(
        """
        SELECT * FROM runtime_control_source_operation_admission_expectations
        WHERE runtime_run_id = ? AND operation_id = ? AND runtime_attempt_no = ?
          AND dispatch_authorization_ordinal = ?
        """,
        (
            runtime_run_id,
            operation_id,
            attempt_no,
            dispatch_authorization_ordinal,
        ),
    ).fetchone()
    return operation, expectation


def _run_row(
    conn: sqlite3.Connection,
    runtime_run_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM runtime_control_runs WHERE runtime_run_id = ?",
        (runtime_run_id,),
    ).fetchone()


def _action_row(
    conn: sqlite3.Connection,
    action_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM runtime_control_user_actions WHERE action_id = ?",
        (action_id,),
    ).fetchone()


def _active_lease_row(
    conn: sqlite3.Connection,
    runtime_run_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM runtime_control_executor_leases
        WHERE runtime_run_id = ? AND status = 'active'
        ORDER BY attempt_no DESC LIMIT 1
        """,
        (runtime_run_id,),
    ).fetchone()


def _reconciliation_row(
    conn: sqlite3.Connection,
    reconciliation_id: str | None,
) -> sqlite3.Row | None:
    if reconciliation_id is None:
        return None
    return conn.execute(
        """
        SELECT * FROM runtime_control_source_reconciliations
        WHERE reconciliation_id = ?
        """,
        (reconciliation_id,),
    ).fetchone()


def _row_digest(row: sqlite3.Row) -> str:
    return sha256(_canonical_json(dict(row))).hexdigest()


def _history_reconciliation_binding_is_valid(row: sqlite3.Row) -> bool:
    history_digest = row["history_result_digest"]
    return (
        isinstance(history_digest, str)
        and len(history_digest) == 64
        and all(character in "0123456789abcdef" for character in history_digest)
        and row["history_result_ref"] == f"sha256:{history_digest}"
        and row["reconciliation_id"] == f"source-history-{history_digest}"
    )


def _action_digest(action: UserActionV1) -> str:
    return sha256(
        _canonical_json(action.model_dump(mode="json"))
    ).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _entry_rejected() -> Never:
    raise RuntimeControlError("runtime_needs_attention_admission_rejected")


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _required_string(value: object) -> str:
    if not isinstance(value, str) or not value:
        _entry_rejected()
    return value


def _required_int(value: object) -> int:
    if type(value) is not int:
        _entry_rejected()
    return value


def needs_attention_evidence_acceptance_matches(
    conn: sqlite3.Connection,
    run_row: sqlite3.Row,
    operation_id: str,
    source_id: str,
    operation_kind: str,
    accepted_requirement_revision_id: str,
    runtime_attempt_no: int,
    profile_binding_generation: int,
    browser_control_scope_id: str | None,
    dispatch_authorization_ordinal: int,
    expected_ledger_revision: int,
    expected_reconciliation_revision: int,
) -> bool:
    action = conn.execute(
        """
        SELECT * FROM runtime_control_user_actions
        WHERE action_id = ? AND runtime_run_id = ? AND status = 'pending'
        """,
        (run_row["current_action_id"], run_row["runtime_run_id"]),
    ).fetchone()
    return (
        action is not None
        and not run_has_active_executor_lease(conn, run_row["runtime_run_id"])
        and operation_id != action["operation_id"]
        and source_id == "liepin"
        and operation_kind == "verify_session"
        and accepted_requirement_revision_id
        == action["accepted_requirement_revision_id"]
        and runtime_attempt_no == action["runtime_attempt_no"]
        and profile_binding_generation == action["profile_binding_generation"]
        and browser_control_scope_id == action["browser_control_scope_id"]
        and dispatch_authorization_ordinal == 1
        and expected_ledger_revision == 1
        and expected_reconciliation_revision == 0
    )


def needs_attention_evidence_reconciliation_matches(
    conn: sqlite3.Connection,
    *,
    run_row: sqlite3.Row,
    decision: SourceOperationReconciliationDecision,
) -> bool:
    if run_row["status"] != "needs_attention":
        return False
    action = conn.execute(
        """
        SELECT * FROM runtime_control_user_actions
        WHERE action_id = ? AND runtime_run_id = ? AND status = 'pending'
        """,
        (run_row["current_action_id"], run_row["runtime_run_id"]),
    ).fetchone()
    expectation = conn.execute(
        """
        SELECT expectation.*
        FROM runtime_control_source_operation_admission_expectations
             AS expectation
        JOIN runtime_control_source_dispatch_outbox AS dispatch
          ON dispatch.runtime_run_id = expectation.runtime_run_id
         AND dispatch.operation_id = expectation.operation_id
         AND dispatch.dispatch_authorization_ordinal
             = expectation.dispatch_authorization_ordinal
        WHERE expectation.runtime_run_id = ?
          AND expectation.operation_id = ?
          AND dispatch.expected_ledger_revision = ?
          AND dispatch.expected_reconciliation_revision = ?
        """,
        (
            decision.runtime_run_id,
            decision.operation_id,
            decision.expected_ledger_revision,
            decision.expected_reconciliation_revision,
        ),
    ).fetchone()
    return (
        action is not None
        and expectation is not None
        and not run_has_active_executor_lease(conn, run_row["runtime_run_id"])
        and decision.operation_id != action["operation_id"]
        and decision.source_id == "liepin"
        and decision.operation_kind == "verify_session"
        and decision.accepted_requirement_revision_id
        == action["accepted_requirement_revision_id"]
        and decision.runtime_attempt_no == action["runtime_attempt_no"]
        and expectation["profile_binding_generation"]
        == action["profile_binding_generation"]
        and expectation["browser_control_scope_id"]
        == action["browser_control_scope_id"]
        and decision.decision_kind == "conclusive_observation"
        and decision.history_outcome == "matched"
        and decision.history_conclusion == "observed_result"
        and decision.conclusive_observation_ref is not None
        and decision.source_operation_disposition == "completed"
        and decision.retry_posture == "no_retry"
    )


def run_has_active_executor_lease(
    conn: sqlite3.Connection,
    runtime_run_id: str,
) -> bool:
    return (
        conn.execute(
            """
            SELECT 1
            FROM runtime_control_executor_leases
            WHERE runtime_run_id = ? AND status = 'active'
            LIMIT 1
            """,
            (runtime_run_id,),
        ).fetchone()
        is not None
    )


__all__ = [
    "ActionSatisfactionAdmission",
    "ActionSatisfactionData",
    "NeedsAttentionAdmission",
    "NeedsAttentionAdmissionData",
    "admit_action_satisfaction",
    "admit_needs_attention",
    "canonical_action_from_row",
    "entry_admission_data",
    "needs_attention_evidence_acceptance_matches",
    "needs_attention_evidence_reconciliation_matches",
    "observation_row_by_ref",
    "reconciliation_digest",
    "require_committed_entry_admission",
    "require_committed_satisfaction_admission",
    "run_has_active_executor_lease",
    "satisfaction_admission_data",
    "satisfaction_binding_digest",
]
