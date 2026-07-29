"""Supervised child process owning the real Liepin cards and details browser effects."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from hashlib import sha256
import time
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from seektalent.config import AppSettings
from seektalent.providers.liepin.client import build_liepin_opencli_site_adapter
from seektalent.sidecar_child_session import serve_sidecar_handshake
from seektalent.sidecar_handshake_protocol import (
    SidecarReadinessError,
    SidecarReadinessReason,
)
from seektalent.source_port.authenticated_liepin_cards_frames import (
    LiepinCardsAcceptedAckV1,
    LiepinCardsReconcileRequiredV1,
    LiepinCardsResultV1,
    ReceivedLiepinCardsSubmit,
)
from seektalent.source_port.authenticated_liepin_details_frames import (
    LiepinDetailsAcceptedAckV1,
    LiepinDetailsReconcileRequiredV1,
    LiepinDetailsResultV1,
    ReceivedLiepinDetailsSubmit,
)
from seektalent.source_port.authenticated_history_frames import (
    ReceivedHistoryQuery,
)
from seektalent.source_port.command_journal import (
    AcceptedCommand,
    CommandJournalTransitionDisposition,
    create_command_journal,
    open_command_journal,
)
from seektalent.source_port.liepin_cards_artifacts import (
    write_liepin_cards_artifact,
)
from seektalent.source_port.liepin_cards_contract import (
    LiepinCardsArtifactV1,
    LiepinCardsObservationV1,
)
from seektalent.source_port.liepin_details_artifacts import (
    write_liepin_details_artifact,
)
from seektalent.source_port.liepin_details_contract import (
    LiepinDetailsArtifactV1,
    LiepinDetailsObservationV1,
)
from seektalent.source_port.liepin_cards_sidecar_identity import (
    liepin_cards_sidecar_identity,
)
from seektalent.source_port.history_sqlite_reader import SourceHistorySQLiteReader
from seektalent.source_port.wire_primitives import canonical_json_bytes
from seektalent.strict_json import strict_json_object_loads


def main(argv: list[str] | None = None) -> int:
    return _serve(argv)


def _serve(
    argv: list[str] | None = None,
    *,
    site_factory: Callable[[], object] | None = None,
    fault_hook: Callable[[str], None] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    journal = None
    journal_session = None
    session = None
    try:
        session = serve_sidecar_handshake(
            __import__("sys").stdin.buffer,
            __import__("sys").stdout.buffer,
            liepin_cards_sidecar_identity(),
        )
        if args.history_only:
            _serve_history(session, args.journal)
            return 0
        journal = (
            open_command_journal(args.journal)
            if args.journal.exists()
            else create_command_journal(args.journal)
        )
        journal_session = journal.start()
        frame_session = session.liepin_source_session()
        cards_artifacts = args.artifacts
        details_artifacts = args.artifacts.parent / "liepin-details-results"
        locator_root = args.artifacts.parent / "liepin-details-locators"
        site = None
        while True:
            try:
                messages = session.receive_liepin_source_messages(
                    deadline=time.monotonic() + 86_400
                )
            except SidecarReadinessError as exc:
                if exc.reason is SidecarReadinessReason.EOF:
                    break
                raise
            if len(messages) != 1:
                raise RuntimeError("liepin_source_sidecar_unexpected_message")
            received = messages[0]
            if isinstance(received, ReceivedLiepinCardsSubmit):
                site = _handle_cards_submit(
                    session=session,
                    frame_session=frame_session,
                    journal_session=journal_session,
                    received=received,
                    cards_artifacts=cards_artifacts,
                    site=site,
                    site_factory=site_factory,
                    fault_hook=fault_hook,
                )
            elif isinstance(received, ReceivedLiepinDetailsSubmit):
                site = _handle_details_submit(
                    session=session,
                    frame_session=frame_session,
                    journal_session=journal_session,
                    received=received,
                    details_artifacts=details_artifacts,
                    locator_root=locator_root,
                    site=site,
                    site_factory=site_factory,
                    fault_hook=fault_hook,
                )
            else:
                raise RuntimeError("liepin_source_sidecar_unexpected_message")
    except (OSError, RuntimeError, TypeError, ValueError, ValidationError):
        return 70
    finally:
        if session is not None:
            session.close()
        if journal_session is not None:
            journal_session.close()
        if journal is not None:
            journal.close()
    return 0


def _handle_cards_submit(
    *,
    session,
    frame_session,
    journal_session,
    received: ReceivedLiepinCardsSubmit,
    cards_artifacts: Path,
    site,
    site_factory: Callable[[], object] | None,
    fault_hook: Callable[[str], None] | None,
):
    submit = received.payload
    _inject_fault(fault_hook, "before_accept")
    authorization = submit.delivery.authorization
    accepted = journal_session.record_accepted(
        AcceptedCommand(
            run_id=submit.identity.run_id,
            operation_id=submit.identity.operation_id,
            source="liepin",
            operation_kind="cards",
            idempotency_key=submit.identity.idempotency_key,
            request_hash=submit.identity.request_hash,
            attempt_no=submit.identity.attempt_no,
            accepted_requirement_revision_id=(
                submit.identity.accepted_requirement_revision_id
            ),
            runtime_attempt_fence_ref=submit.identity.runtime_attempt_fence_ref,
            authorized_dispatch_intent_id=authorization.dispatch_intent_id,
            authorized_dispatch_intent_revision=(
                authorization.dispatch_intent_revision
            ),
            authorized_dispatch_intent_digest=authorization.dispatch_intent_digest,
            profile_binding_generation=submit.identity.profile_binding_generation,
            browser_control_scope_id=submit.identity.browser_control_scope_id,
            dispatch_authorization_ordinal=(
                authorization.dispatch_authorization_ordinal
            ),
            safe_retry_commit_ref=authorization.safe_retry_commit_ref,
            expected_source_operation_ledger_revision=(
                authorization.expected_source_operation_ledger_revision
            ),
            expected_reconciliation_revision=(
                authorization.expected_reconciliation_revision
            ),
        ),
        allow_existing_phase_replay=True,
        allow_transport_replay=True,
    )
    _inject_fault(fault_hook, "after_accept")
    dispatch_ref = (
        f"source-dispatch://{submit.identity.operation_id}/"
        f"{authorization.dispatch_authorization_ordinal}"
    )
    ack_kind = (
        "new_logical_operation"
        if authorization.dispatch_authorization_ordinal == 1
        else "new_dispatch_authorization"
    )
    ack = LiepinCardsAcceptedAckV1(
        contract_version="seektalent.source.liepin-cards.ack/v1",
        identity=submit.identity,
        sidecar_generation=accepted.startup_generation,
        accepted_journal_revision=accepted.accepted_journal_revision,
        ack_kind=ack_kind,
        dispatch_intent_ref=dispatch_ref,
    )
    _send(
        session,
        frame_session.encode_cards_accepted_ack(
            message_id=_message_id("ack", submit.identity.operation_id),
            reply_to=received.message_id,
            correlation_id=received.correlation_id,
            payload=ack,
        ),
    )
    if accepted.head_phase in {"observed_result", "observed_failure"}:
        observation = _cards_observation_from_receipt(accepted.terminal_reply_bytes)
        _send_cards_result(
            session,
            frame_session,
            received,
            submit.identity,
            observation,
        )
        return site
    if (
        accepted.head_phase == "dispatch_intent"
        or accepted.disposition is CommandJournalTransitionDisposition.EXACT_REPLAY
    ):
        _send_cards_reconcile(
            session,
            frame_session,
            received,
            submit.identity,
            (
                "dispatch_not_observed"
                if accepted.head_phase == "dispatch_intent"
                else "accepted_no_dispatch"
            ),
        )
        return site
    dispatch = journal_session.record_dispatch_intent(
        run_id=submit.identity.run_id,
        operation_id=submit.identity.operation_id,
        dispatch_authorization_ordinal=authorization.dispatch_authorization_ordinal,
        expected_head_journal_revision=accepted.revision,
        durable_dispatch_intent_ref=dispatch_ref,
    )
    _inject_fault(fault_hook, "after_dispatch_intent")
    site = _ensure_site(site, site_factory)
    artifact = _execute_cards(site, submit)
    _inject_fault(fault_hook, "after_effect")
    artifact_ref, artifact_hash = write_liepin_cards_artifact(
        cards_artifacts,
        artifact,
    )
    observation = LiepinCardsObservationV1(
        contract_version="seektalent.source.liepin-cards.observation/v1",
        operation_id=submit.identity.operation_id,
        canonical_request_hash=submit.identity.request_hash,
        disposition=_disposition(artifact.status),
        artifact_ref=artifact_ref,
        artifact_hash=artifact_hash,
        cards_seen=artifact.cards_seen,
        card_count=len(artifact.cards),
        safe_reason_code=artifact.safe_reason_code,
        producer_generation=journal_session.generation,
    )
    _record_terminal_observation(
        journal_session,
        submit=submit,
        authorization=authorization,
        dispatch=dispatch,
        observation=observation,
        fault_hook=fault_hook,
    )
    _send_cards_result(
        session,
        frame_session,
        received,
        submit.identity,
        observation,
    )
    return site


def _handle_details_submit(
    *,
    session,
    frame_session,
    journal_session,
    received: ReceivedLiepinDetailsSubmit,
    details_artifacts: Path,
    locator_root: Path,
    site,
    site_factory: Callable[[], object] | None,
    fault_hook: Callable[[str], None] | None,
):
    submit = received.payload
    _inject_fault(fault_hook, "before_accept")
    authorization = submit.delivery.authorization
    accepted = journal_session.record_accepted(
        AcceptedCommand(
            run_id=submit.identity.run_id,
            operation_id=submit.identity.operation_id,
            source="liepin",
            operation_kind="details",
            idempotency_key=submit.identity.idempotency_key,
            request_hash=submit.identity.request_hash,
            attempt_no=submit.identity.attempt_no,
            accepted_requirement_revision_id=(
                submit.identity.accepted_requirement_revision_id
            ),
            runtime_attempt_fence_ref=submit.identity.runtime_attempt_fence_ref,
            authorized_dispatch_intent_id=authorization.dispatch_intent_id,
            authorized_dispatch_intent_revision=(
                authorization.dispatch_intent_revision
            ),
            authorized_dispatch_intent_digest=authorization.dispatch_intent_digest,
            profile_binding_generation=submit.identity.profile_binding_generation,
            browser_control_scope_id=submit.identity.browser_control_scope_id,
            dispatch_authorization_ordinal=(
                authorization.dispatch_authorization_ordinal
            ),
            safe_retry_commit_ref=authorization.safe_retry_commit_ref,
            expected_source_operation_ledger_revision=(
                authorization.expected_source_operation_ledger_revision
            ),
            expected_reconciliation_revision=(
                authorization.expected_reconciliation_revision
            ),
        ),
        allow_existing_phase_replay=True,
        allow_transport_replay=True,
    )
    _inject_fault(fault_hook, "after_accept")
    dispatch_ref = (
        f"source-dispatch://{submit.identity.operation_id}/"
        f"{authorization.dispatch_authorization_ordinal}"
    )
    ack_kind = (
        "new_logical_operation"
        if authorization.dispatch_authorization_ordinal == 1
        else "new_dispatch_authorization"
    )
    ack = LiepinDetailsAcceptedAckV1(
        contract_version="seektalent.source.liepin-details.ack/v1",
        identity=submit.identity,
        sidecar_generation=accepted.startup_generation,
        accepted_journal_revision=accepted.accepted_journal_revision,
        ack_kind=ack_kind,
        dispatch_intent_ref=dispatch_ref,
    )
    _send(
        session,
        frame_session.encode_details_accepted_ack(
            message_id=_message_id("ack", submit.identity.operation_id),
            reply_to=received.message_id,
            correlation_id=received.correlation_id,
            payload=ack,
        ),
    )
    if accepted.head_phase in {"observed_result", "observed_failure"}:
        observation = _details_observation_from_receipt(
            accepted.terminal_reply_bytes
        )
        _send_details_result(
            session,
            frame_session,
            received,
            submit.identity,
            observation,
        )
        return site
    if (
        accepted.head_phase == "dispatch_intent"
        or accepted.disposition is CommandJournalTransitionDisposition.EXACT_REPLAY
    ):
        _send_details_reconcile(
            session,
            frame_session,
            received,
            submit.identity,
            (
                "dispatch_not_observed"
                if accepted.head_phase == "dispatch_intent"
                else "accepted_no_dispatch"
            ),
        )
        return site
    dispatch = journal_session.record_dispatch_intent(
        run_id=submit.identity.run_id,
        operation_id=submit.identity.operation_id,
        dispatch_authorization_ordinal=authorization.dispatch_authorization_ordinal,
        expected_head_journal_revision=accepted.revision,
        durable_dispatch_intent_ref=dispatch_ref,
    )
    _inject_fault(fault_hook, "after_dispatch_intent")
    site = _ensure_site(site, site_factory)
    artifact = _execute_details(site, submit, locator_root=locator_root)
    _inject_fault(fault_hook, "after_effect")
    artifact_ref, artifact_hash = write_liepin_details_artifact(
        details_artifacts,
        artifact,
    )
    observation = LiepinDetailsObservationV1(
        contract_version="seektalent.source.liepin-details.observation/v1",
        operation_id=submit.identity.operation_id,
        canonical_request_hash=submit.identity.request_hash,
        disposition=_disposition(artifact.status),
        artifact_ref=artifact_ref,
        artifact_hash=artifact_hash,
        open_mode=artifact.open_mode,
        provider_candidate_key_hash=artifact.provider_candidate_key_hash,
        rank=artifact.rank,
        action_attempted=artifact.action_attempted,
        safe_reason_code=artifact.safe_reason_code,
        producer_generation=journal_session.generation,
    )
    _record_terminal_observation(
        journal_session,
        submit=submit,
        authorization=authorization,
        dispatch=dispatch,
        observation=observation,
        fault_hook=fault_hook,
    )
    _send_details_result(
        session,
        frame_session,
        received,
        submit.identity,
        observation,
    )
    return site


def _ensure_site(site, site_factory: Callable[[], object] | None):
    if site is not None:
        return site
    return (
        site_factory()
        if site_factory is not None
        else build_liepin_opencli_site_adapter(
            AppSettings(_env_file=None),
            cards_operation_executor=None,
        )
    )


def _record_terminal_observation(
    journal_session,
    *,
    submit,
    authorization,
    dispatch,
    observation,
    fault_hook: Callable[[str], None] | None,
) -> None:
    terminal_bytes = canonical_json_bytes(observation.model_dump(mode="json"))
    terminal_digest = _terminal_observation_digest(terminal_bytes)
    if observation.disposition in {"completed", "partial"}:
        journal_session.record_observed_result(
            run_id=submit.identity.run_id,
            operation_id=submit.identity.operation_id,
            dispatch_authorization_ordinal=(
                authorization.dispatch_authorization_ordinal
            ),
            expected_head_journal_revision=dispatch.revision,
            result_ref=terminal_digest,
            result_hash=terminal_digest,
            terminal_reply_bytes=terminal_bytes,
        )
    else:
        journal_session.record_observed_failure(
            run_id=submit.identity.run_id,
            operation_id=submit.identity.operation_id,
            dispatch_authorization_ordinal=(
                authorization.dispatch_authorization_ordinal
            ),
            expected_head_journal_revision=dispatch.revision,
            failure_ref=terminal_digest,
            failure_hash=terminal_digest,
            terminal_reply_bytes=terminal_bytes,
        )
    _inject_fault(fault_hook, "after_terminal")


def _inject_fault(
    fault_hook: Callable[[str], None] | None,
    point: str,
) -> None:
    if fault_hook is not None:
        fault_hook(point)


def _execute_cards(site, submit) -> LiepinCardsArtifactV1:
    request = submit.request
    envelope, structured = site._execute_liepin_cards_sidecar_effect(
        source_run_id=request.source_lane_run_id,
        query=request.keyword_query,
        max_pages=request.max_pages,
        max_cards=request.max_cards,
        native_filters=request.native_filters,
    )
    status = envelope.get("status")
    safe_reason = envelope.get("safe_reason_code") or envelope.get("stop_reason")
    cards_seen = envelope.get("cards_seen")
    cards: tuple[dict[str, object], ...] = ()
    result_status = "failed"
    if status in {"succeeded", "partial"}:
        raw_cards = (
            structured.observation.get("cards", ())
            if structured is not None
            else ()
        )
        if (
            structured is not None
            and structured.ok
            and isinstance(raw_cards, list)
        ):
            cards = tuple(item for item in raw_cards if isinstance(item, dict))
            result_status = "succeeded" if status == "succeeded" else "partial"
            if result_status == "succeeded":
                safe_reason = None
        else:
            result_status = "partial" if int(cards_seen or 0) > 0 else "failed"
            safe_reason = (
                (
                    structured.safe_reason_code
                    if structured is not None
                    else None
                )
                or "liepin_opencli_cards_observation_unavailable"
            )
    return LiepinCardsArtifactV1.model_validate(
        {
            "contract_version": "seektalent.source.liepin-cards.artifact/v1",
            "operation_id": submit.identity.operation_id,
            "canonical_request_hash": submit.identity.request_hash,
            "status": result_status,
            "cards": cards,
            "cards_seen": max(0, int(cards_seen or len(cards))),
            "safe_reason_code": (
                str(safe_reason)[:160] if safe_reason else None
            ),
        },
        strict=True,
    )


def _execute_details(
    site,
    submit,
    *,
    locator_root: Path,
) -> LiepinDetailsArtifactV1:
    request = submit.request
    effect = site._execute_liepin_details_sidecar_effect(
        source_run_id=request.source_lane_run_id,
        card_ref=request.card_ref,
        rank=request.rank,
        open_mode=request.open_mode,
        provider_candidate_key_hash=request.provider_candidate_key_hash,
        expected_provider_candidate_key_hash=(
            request.expected_provider_candidate_key_hash
        ),
        locator_root=locator_root,
    )
    status = effect.get("status")
    safe_reason = effect.get("safe_reason_code")
    result_status = "failed"
    if status in {"succeeded", "partial"}:
        result_status = "succeeded" if status == "succeeded" else "partial"
        if result_status == "succeeded":
            safe_reason = None
    resume = effect.get("resume")
    if resume is not None and not isinstance(resume, dict):
        resume = None
    return LiepinDetailsArtifactV1.model_validate(
        {
            "contract_version": "seektalent.source.liepin-details.artifact/v1",
            "operation_id": submit.identity.operation_id,
            "canonical_request_hash": submit.identity.request_hash,
            "status": result_status,
            "open_mode": request.open_mode,
            "provider_candidate_key_hash": effect.get("provider_candidate_key_hash"),
            "rank": request.rank,
            "card_ref": request.card_ref,
            "detail_url": effect.get("detail_url"),
            "resume": resume,
            "action_attempted": max(0, int(effect.get("action_attempted") or 0)),
            "safe_reason_code": (
                str(safe_reason)[:160] if safe_reason else None
            ),
        },
        strict=True,
    )


def _serve_history(session, journal_path: Path) -> None:
    frame_session = session.cards_history_session()
    reader = SourceHistorySQLiteReader(journal_path)
    while True:
        try:
            messages = session.receive_cards_history_messages(
                deadline=time.monotonic() + 86_400
            )
        except SidecarReadinessError as exc:
            if exc.reason is SidecarReadinessReason.EOF:
                return
            raise
        if len(messages) != 1 or not isinstance(
            messages[0],
            ReceivedHistoryQuery,
        ):
            raise RuntimeError("liepin_cards_history_unexpected_message")
        received = messages[0]
        deadline = time.monotonic() + 30
        result = reader.query(received.payload, deadline=deadline)
        session.send_cards_history_frame(
            frame_session.encode_result(
                message_id=_message_id(
                    "history-result",
                    received.payload.operation_id,
                ),
                reply_to=received.message_id,
                payload=result,
            ),
            deadline=deadline,
        )


def _cards_observation_from_receipt(raw: bytes | None) -> LiepinCardsObservationV1:
    if raw is None:
        raise RuntimeError("liepin_cards_terminal_observation_missing")
    return LiepinCardsObservationV1.model_validate(
        strict_json_object_loads(raw),
        strict=True,
    )


def _details_observation_from_receipt(
    raw: bytes | None,
) -> LiepinDetailsObservationV1:
    if raw is None:
        raise RuntimeError("liepin_details_terminal_observation_missing")
    return LiepinDetailsObservationV1.model_validate(
        strict_json_object_loads(raw),
        strict=True,
    )


def _send_cards_result(session, frame_session, received, identity, observation) -> None:
    _send(
        session,
        frame_session.encode_cards_result(
            message_id=_message_id("result", identity.operation_id),
            reply_to=received.message_id,
            correlation_id=received.correlation_id,
            payload=LiepinCardsResultV1(
                contract_version="seektalent.source.liepin-cards.result/v1",
                identity=identity,
                observation=observation,
            ),
        ),
    )


def _send_details_result(session, frame_session, received, identity, observation) -> None:
    _send(
        session,
        frame_session.encode_details_result(
            message_id=_message_id("result", identity.operation_id),
            reply_to=received.message_id,
            correlation_id=received.correlation_id,
            payload=LiepinDetailsResultV1(
                contract_version="seektalent.source.liepin-details.result/v1",
                identity=identity,
                observation=observation,
            ),
        ),
    )


def _send_cards_reconcile(
    session,
    frame_session,
    received,
    identity,
    history_fact,
) -> None:
    _send(
        session,
        frame_session.encode_cards_reconcile_required(
            message_id=_message_id("reconcile", identity.operation_id),
            reply_to=received.message_id,
            correlation_id=received.correlation_id,
            payload=LiepinCardsReconcileRequiredV1(
                contract_version=(
                    "seektalent.source.liepin-cards.reconcile-required/v1"
                ),
                identity=identity,
                history_fact=history_fact,
            ),
        ),
    )


def _send_details_reconcile(
    session,
    frame_session,
    received,
    identity,
    history_fact,
) -> None:
    _send(
        session,
        frame_session.encode_details_reconcile_required(
            message_id=_message_id("reconcile", identity.operation_id),
            reply_to=received.message_id,
            correlation_id=received.correlation_id,
            payload=LiepinDetailsReconcileRequiredV1(
                contract_version=(
                    "seektalent.source.liepin-details.reconcile-required/v1"
                ),
                identity=identity,
                history_fact=history_fact,
            ),
        ),
    )


def _send(session, frame: bytes) -> None:
    session.send_liepin_source_frame(
        frame,
        deadline=time.monotonic() + 30,
    )


def _terminal_observation_digest(terminal_reply_bytes: bytes) -> str:
    return sha256(terminal_reply_bytes).hexdigest()


def _message_id(kind: str, operation_id: str) -> str:
    return f"{kind}-{operation_id}"[:96]


def _disposition(
    status: str,
) -> Literal["completed", "partial", "failed"]:
    if status == "succeeded":
        return "completed"
    if status == "partial":
        return "partial"
    return "failed"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--history-only", action="store_true")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
