"""Packaged sidecar bootstrap with no product operation authority."""

from __future__ import annotations

import sys
from importlib import import_module

from seektalent.sidecar_readiness import (
    SidecarHandshakeIdentity,
    SidecarReadinessError,
    serve_sidecar_handshake,
)


def main() -> int:
    try:
        identity_module = import_module("sidecar_embedded_identity")
        identity_payload = getattr(identity_module, "SIDECAR_HANDSHAKE_IDENTITY")
        if not isinstance(identity_payload, dict):
            return 70
        identity = SidecarHandshakeIdentity(**identity_payload)
        serve_sidecar_handshake(sys.stdin.buffer, sys.stdout.buffer, identity)
    except (SidecarReadinessError, TypeError, ValueError):
        return 70
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
