"""Identity shared by the exact-wheel main and its cards sidecar child."""

from __future__ import annotations

import csv
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, distribution
from io import StringIO
from pathlib import Path

from seektalent.sidecar_handshake_protocol import SidecarHandshakeIdentity


def liepin_cards_sidecar_identity() -> SidecarHandshakeIdentity:
    try:
        package = distribution("seektalent")
        package_version = package.version
        record = package.read_text("RECORD")
    except PackageNotFoundError:
        package_version = "source-tree"
        record = None
    package_fingerprint = _package_fingerprint(record)
    build = sha256(
        (
            f"seektalent:{package_version}:{package_fingerprint}:"
            "liepin-cards-sidecar-v1"
        ).encode()
    ).hexdigest()
    product_build_id = (
        f"seektalent-wheel:{package_version}:{package_fingerprint[:16]}"
    )
    return SidecarHandshakeIdentity(
        product_build_id=product_build_id,
        sidecar_build_id=f"sha256:{build}",
        protocol_id="seektalent.source-execution-port",
        protocol_major=1,
        protocol_min_minor=0,
        protocol_max_minor=0,
        protocol_capabilities=(
            "authenticated_framing",
            "liepin_cards_v1",
            "liepin_details_v1",
        ),
        expected_main_application_build_id=f"seektalent-main:{product_build_id}",
    )


def _package_fingerprint(record: str | None) -> str:
    if record is not None:
        package_rows = sorted(
            tuple(row)
            for row in csv.reader(StringIO(record))
            if row and row[0].startswith("seektalent/")
        )
        if package_rows:
            stable_record = "\n".join(
                ",".join(row)
                for row in package_rows
            ).encode()
            return sha256(stable_record).hexdigest()
    return sha256(Path(__file__).read_bytes()).hexdigest()


__all__ = ["liepin_cards_sidecar_identity"]
