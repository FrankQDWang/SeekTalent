"""Packaged sidecar bootstrap with no product operation authority."""

from __future__ import annotations

import sys
from importlib import import_module

from seektalent.sidecar_child_session import serve_sidecar_handshake
from seektalent.sidecar_handshake_protocol import SidecarHandshakeIdentity


def main() -> int:
    try:
        identity_module = import_module("sidecar_embedded_identity")
        identity_payload = getattr(identity_module, "SIDECAR_HANDSHAKE_IDENTITY")
        if not isinstance(identity_payload, dict):
            return 70
        identity = SidecarHandshakeIdentity(**identity_payload)
        session = serve_sidecar_handshake(sys.stdin.buffer, sys.stdout.buffer, identity)
        session.wait_for_parent_eof()
    except (RuntimeError, TypeError, ValueError):
        return 70
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
