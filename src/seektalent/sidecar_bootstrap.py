"""Packaged sidecar bootstrap with an explicit native-test history reader."""

from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

from seektalent.sidecar_child_session import serve_sidecar_handshake
from seektalent.sidecar_handshake_protocol import SidecarHandshakeIdentity
from seektalent.source_port.sidecar_transport import (
    serve_test_source_history_database,
    wait_for_parent_eof,
)


def main() -> int:
    try:
        identity_module = import_module("sidecar_embedded_identity")
        identity_payload = getattr(identity_module, "SIDECAR_HANDSHAKE_IDENTITY")
        if not isinstance(identity_payload, dict):
            return 70
        identity = SidecarHandshakeIdentity(**identity_payload)
        history_database = _test_source_history_path(
            identity,
            tuple(sys.argv[1:]),
        )
        session = serve_sidecar_handshake(sys.stdin.buffer, sys.stdout.buffer, identity)
        try:
            if history_database is not None:
                serve_test_source_history_database(session, history_database)
            else:
                wait_for_parent_eof(session)
        finally:
            session.close()
    except (RuntimeError, TypeError, ValueError):
        return 70
    return 0


def _test_source_history_path(
    identity: SidecarHandshakeIdentity,
    arguments: tuple[str, ...],
) -> Path | None:
    if not arguments:
        return None
    if not identity.sidecar_build_id.startswith("test-only-liepin_execution_sidecar-source-"):
        raise ValueError("history database argument is test-only")
    if len(arguments) == 2 and arguments[0] == "--test-only-source-history-database":
        history_path = Path(arguments[1])
        if not history_path.is_absolute():
            raise ValueError("test history database path must be absolute")
        return history_path
    raise ValueError("invalid test-only sidecar arguments")


if __name__ == "__main__":
    raise SystemExit(main())
