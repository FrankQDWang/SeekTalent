from __future__ import annotations

import base64
import errno
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import seektalent.installed_release as installed_release
import seektalent.owned_sidecar_process as owned_process
from seektalent.installed_release import (
    InstalledReleaseError,
    InstalledReleaseReason,
    admit_installed_sidecar_launch,
)
from seektalent.owned_sidecar_process import spawn_owned_sidecar
from seektalent.release_manifest import parse_release_manifest, release_manifest_digest
from seektalent.release_signing import (
    ReleaseManifestTrustPolicyV1,
    ReleaseSigningError,
    ReleaseSigningReason,
)
from tests.test_installed_release import _install_slot
from tests.test_release_manifest import TARGETS, _raw
from tests.test_release_signing import (
    VALID_FROM,
    VALID_UNTIL,
    VERIFICATION_TIME,
    _policy,
    _signature_raw,
    _signed,
    _trust_key,
)


def _install_signed_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    target: dict[str, str] | None = None,
) -> tuple[Path, Path, Path, dict[str, object]]:
    slot_root, executable, payload = _install_slot(tmp_path, monkeypatch, target=target)
    manifest_path = slot_root / "release" / "release-manifest.json"
    manifest = parse_release_manifest(manifest_path.read_bytes())
    _, signature_payload = _signed(manifest)
    signature_path = slot_root / installed_release.INSTALLED_SIGNATURE_RELATIVE_PATH
    signature_path.parent.mkdir()
    signature_path.write_bytes(_signature_raw(signature_payload))
    return slot_root, signature_path, executable, payload


def _admit_and_spawn(
    slot_root: Path,
    *,
    policy: ReleaseManifestTrustPolicyV1 | None = None,
    verification_time: datetime | None = VERIFICATION_TIME,
) -> None:
    admission = admit_installed_sidecar_launch(
        slot_root,
        policy if policy is not None else _policy(),
        cast(datetime, verification_time),
    )
    spawn_owned_sidecar(admission)


@pytest.mark.parametrize("target", TARGETS)
def test_signed_admission_derives_all_identity_from_one_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: dict[str, str],
) -> None:
    slot_root, _, executable, _ = _install_signed_slot(tmp_path, monkeypatch, target=target)
    manifest = parse_release_manifest((slot_root / "release" / "release-manifest.json").read_bytes())
    main = next(item for item in manifest.components if item.component_id == "main_application")
    sidecar = next(item for item in manifest.components if item.component_id == "liepin_execution_sidecar")

    admission = admit_installed_sidecar_launch(slot_root, _policy(), VERIFICATION_TIME)

    assert admission.manifest_id == manifest.manifest_id == admission.resolution.manifest_id
    assert admission.manifest_sha256 == release_manifest_digest(manifest)
    assert admission.manifest_sha256 == admission.resolution.manifest_sha256
    assert admission.product_build_id == manifest.product_build_id == admission.resolution.product_build_id
    assert (admission.main_application_build_id, admission.main_application_tree_sha256) == (
        main.build_id,
        main.tree_sha256,
    )
    assert (admission.sidecar_build_id, admission.sidecar_tree_sha256) == (
        sidecar.build_id,
        sidecar.tree_sha256,
    )
    assert admission.sidecar_executable_sha256 == admission.resolution.executable_sha256
    assert admission.source_port_protocol == manifest.compatibility.main_sidecar.source_port_protocol
    assert admission.executable_path == executable
    assert admission.signer_key_id == "rfc8032-test-key-1"
    assert (admission.trust_policy_id, admission.trust_policy_revision) == (
        "release-trust-policy-v1",
        7,
    )


@pytest.mark.parametrize(
    ("case", "error_type", "reason"),
    [
        ("missing", InstalledReleaseError, InstalledReleaseReason.NOT_REGULAR_FILE),
        ("oversized", InstalledReleaseError, InstalledReleaseReason.FILE_SIZE_LIMIT_EXCEEDED),
        ("malformed", ReleaseSigningError, ReleaseSigningReason.INVALID_JSON),
        ("duplicate", ReleaseSigningError, ReleaseSigningReason.DUPLICATE_KEY),
        ("symlink", InstalledReleaseError, InstalledReleaseReason.SYMLINK),
        ("hardlink", InstalledReleaseError, InstalledReleaseReason.HARDLINK),
        ("permission", InstalledReleaseError, InstalledReleaseReason.FILE_ACCESS_DENIED),
    ],
)
def test_signature_file_failure_is_typed_and_never_reaches_popen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    error_type: type[InstalledReleaseError] | type[ReleaseSigningError],
    reason: InstalledReleaseReason | ReleaseSigningReason,
) -> None:
    slot_root, signature_path, _, _ = _install_signed_slot(tmp_path, monkeypatch)
    original_signature = signature_path.read_bytes()
    if case == "missing":
        signature_path.unlink()
    elif case == "oversized":
        with signature_path.open("r+b") as stream:
            stream.truncate(installed_release.MAX_INSTALLED_MANIFEST_BYTES + 1)
    elif case == "malformed":
        signature_path.write_bytes(b"{")
    elif case == "duplicate":
        signature_path.write_bytes(
            b'{"schema_version":"seektalent.release-manifest-signature/v1",'
            + original_signature[1:]
        )
    elif case == "symlink":
        real_signature = signature_path.with_name("real-release-manifest.sig")
        signature_path.rename(real_signature)
        signature_path.symlink_to(real_signature)
    elif case == "hardlink":
        os.link(signature_path, signature_path.with_name("signature-alias.sig"))
    else:
        original_open = installed_release.os.open

        def permission_denied(
            path: os.PathLike[str] | str,
            flags: int,
            *args: object,
            **kwargs: object,
        ) -> int:
            if Path(path) == signature_path:
                raise PermissionError(errno.EACCES, "permission denied", str(path))
            return original_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(installed_release.os, "open", permission_denied)

    popen_calls: list[object] = []
    monkeypatch.setattr(owned_process.subprocess, "Popen", lambda *args, **kwargs: popen_calls.append((args, kwargs)))

    with pytest.raises(error_type) as raised:
        _admit_and_spawn(slot_root)

    assert raised.value.reason == reason
    assert popen_calls == []


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("tampered_manifest", ReleaseSigningReason.MANIFEST_DIGEST_MISMATCH),
        ("tampered_signature", ReleaseSigningReason.INVALID_SIGNATURE),
        ("wrong_key", ReleaseSigningReason.INVALID_SIGNATURE),
        ("unknown_key", ReleaseSigningReason.UNKNOWN_KEY),
        ("revoked_key", ReleaseSigningReason.REVOKED_KEY),
        ("expired_key", ReleaseSigningReason.KEY_EXPIRED),
        ("not_yet_valid_key", ReleaseSigningReason.KEY_NOT_YET_VALID),
        ("untrusted_time", ReleaseSigningReason.TIME_UNTRUSTED),
        ("non_utc_time", ReleaseSigningReason.TIME_UNTRUSTED),
    ],
)
def test_signature_trust_or_time_failure_never_reaches_popen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    reason: ReleaseSigningReason,
) -> None:
    slot_root, signature_path, _, payload = _install_signed_slot(tmp_path, monkeypatch)
    policy = _policy()
    verification_time: datetime | None = VERIFICATION_TIME
    if case == "tampered_manifest":
        payload["channel"] = "production"
        (slot_root / "release" / "release-manifest.json").write_bytes(_raw(payload))
    elif case == "tampered_signature":
        signature_payload = json.loads(signature_path.read_text(encoding="utf-8"))
        signature_payload["signature"] = base64.b64encode(b"\x00" * 64).decode("ascii")
        signature_path.write_bytes(_signature_raw(signature_payload))
    elif case == "wrong_key":
        public_key = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
        policy = _policy(keys=(_trust_key(public_key=public_key),))
    elif case == "unknown_key":
        policy = _policy(keys=(_trust_key(key_id="other-release-key"),))
    elif case == "revoked_key":
        policy = _policy(revoked_key_ids=frozenset({"rfc8032-test-key-1"}))
    elif case == "expired_key":
        verification_time = VALID_UNTIL + timedelta(seconds=1)
    elif case == "not_yet_valid_key":
        verification_time = VALID_FROM - timedelta(seconds=1)
    elif case == "untrusted_time":
        verification_time = None
    else:
        verification_time = VERIFICATION_TIME.replace(tzinfo=None)

    popen_calls: list[object] = []
    monkeypatch.setattr(owned_process.subprocess, "Popen", lambda *args, **kwargs: popen_calls.append((args, kwargs)))

    with pytest.raises(ReleaseSigningError) as raised:
        _admit_and_spawn(slot_root, policy=policy, verification_time=verification_time)

    assert raised.value.reason == reason
    assert popen_calls == []


@pytest.mark.parametrize("target", ["manifest", "signature", "sidecar"])
@pytest.mark.parametrize("mutation", ["append", "replace"])
def test_installed_identity_changed_during_admission_never_reaches_popen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    mutation: str,
) -> None:
    slot_root, signature_path, executable, _ = _install_signed_slot(tmp_path, monkeypatch)
    target_path = {
        "manifest": slot_root / "release" / "release-manifest.json",
        "signature": signature_path,
        "sidecar": executable,
    }[target]
    target_inode = target_path.stat().st_ino
    target_bytes = target_path.read_bytes()
    target_mode = target_path.stat().st_mode & 0o777
    original_read = installed_release.os.read
    changed = False

    def mutating_read(descriptor: int, size: int) -> bytes:
        nonlocal changed
        chunk = original_read(descriptor, size)
        if not changed and chunk and os.fstat(descriptor).st_ino == target_inode:
            changed = True
            if mutation == "replace":
                replacement = target_path.with_name(f"replacement-{target_path.name}")
                replacement.write_bytes(target_bytes)
                replacement.chmod(target_mode)
                os.replace(replacement, target_path)
            else:
                target_path.chmod(0o700)
                with target_path.open("ab") as stream:
                    stream.write(b"x")
        return chunk

    monkeypatch.setattr(installed_release.os, "read", mutating_read)
    popen_calls: list[object] = []
    monkeypatch.setattr(owned_process.subprocess, "Popen", lambda *args, **kwargs: popen_calls.append((args, kwargs)))

    with pytest.raises(InstalledReleaseError) as raised:
        _admit_and_spawn(slot_root)

    assert raised.value.reason == InstalledReleaseReason.PATH_CHANGED
    assert changed
    assert popen_calls == []
