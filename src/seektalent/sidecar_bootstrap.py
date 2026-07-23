"""Packaged sidecar bootstrap with only explicit native-test fake operation authority."""

from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

from seektalent.sidecar_child_session import serve_sidecar_handshake
from seektalent.sidecar_handshake_protocol import SidecarHandshakeIdentity
from seektalent.source_port.command_journal import create_command_journal
from seektalent.source_port.history_sqlite_reader import SourceHistorySQLiteReader
from seektalent.source_port.sidecar_transport import (
    serve_test_source_history_database,
    serve_test_source_port,
    wait_for_parent_eof,
)
from seektalent.source_port.verify_session_contract import VerifySessionRequestV1, VerifySessionResultV1
from seektalent.source_port.verify_session_journal_effect import create_verify_session_journal_effect_composition


def main() -> int:
    try:
        identity_module = import_module("sidecar_embedded_identity")
        identity_payload = getattr(identity_module, "SIDECAR_HANDSHAKE_IDENTITY")
        if not isinstance(identity_payload, dict):
            return 70
        identity = SidecarHandshakeIdentity(**identity_payload)
        history_database, journal_database = _test_source_port_paths(identity, tuple(sys.argv[1:]))
        session = serve_sidecar_handshake(sys.stdin.buffer, sys.stdout.buffer, identity)
        try:
            if history_database is not None and journal_database is not None:
                journal = create_command_journal(journal_database)
                composition = create_verify_session_journal_effect_composition(
                    command_journal_session=journal.start(),
                    frame_session=session.source_port_session(),
                    effect=_deterministic_fake_verify_effect,
                )
                try:
                    serve_test_source_port(session, SourceHistorySQLiteReader(history_database), composition)
                finally:
                    composition.close()
                    journal.close()
            elif history_database is not None:
                serve_test_source_history_database(session, history_database)
            else:
                wait_for_parent_eof(session)
        finally:
            session.close()
    except (RuntimeError, TypeError, ValueError):
        return 70
    return 0


def _test_source_port_paths(
    identity: SidecarHandshakeIdentity,
    arguments: tuple[str, ...],
) -> tuple[Path | None, Path | None]:
    if not arguments:
        return None, None
    if not identity.sidecar_build_id.startswith("test-only-liepin_execution_sidecar-source-"):
        raise ValueError("history database argument is test-only")
    if len(arguments) == 2 and arguments[0] == "--test-only-source-history-database":
        history_path = Path(arguments[1])
        if not history_path.is_absolute():
            raise ValueError("test history database path must be absolute")
        return history_path, None
    if (
        len(arguments) != 4
        or arguments[0] != "--test-only-source-history-database"
        or arguments[2] != "--test-only-verify-session-journal"
    ):
        raise ValueError("invalid test-only sidecar arguments")
    history_path = Path(arguments[1])
    journal_path = Path(arguments[3])
    if not history_path.is_absolute() or not journal_path.is_absolute():
        raise ValueError("test source port paths must be absolute")
    return history_path, journal_path


def _deterministic_fake_verify_effect(
    request: VerifySessionRequestV1,
    deadline_at: float,
) -> VerifySessionResultV1:
    """Return deterministic test facts without WTSCLI, Node, browser, or network access."""
    del deadline_at
    return VerifySessionResultV1.model_validate(
        {
            "contract_version": "seektalent.source.verify-session.result/v1",
            "identity": request.identity,
            "process_readiness": "ready",
            "bridge_readiness": "ready",
            "extension_readiness": "ready",
            "profile_lock_readiness": "ready",
            "account_readiness": "ready",
            "search_surface_readiness": "ready",
            "risk_state": "clear",
            "session_readiness": "ready",
            "actual_profile_binding_ref": request.profile_binding_ref,
            "actual_provider_account_ref": request.provider_account_ref,
            "actual_profile_binding_generation": request.identity.profile_binding_generation,
            "safe_reason_code": None,
            "user_action": None,
            "component_receipt_refs": request.component_receipt_refs,
        },
        strict=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
