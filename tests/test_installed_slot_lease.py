from __future__ import annotations

import copy
import io
import os
import pickle
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Literal, cast

import pytest

import seektalent.installed_release as installed_release
import seektalent.owned_sidecar_process as owned_process
from seektalent.installed_release import (
    ActiveSlotPointerError,
    ActiveSlotPointerReason,
    ActiveSlotPointerV1,
    InstalledSidecarLaunchLease,
    InstalledSlotError,
    InstalledSlotReason,
    acquire_installed_sidecar_launch_lease,
    canonical_active_slot_pointer_bytes,
    parse_active_slot_pointer,
)
from seektalent.owned_sidecar_process import spawn_owned_sidecar
from seektalent.release_manifest import parse_release_manifest
from tests.test_installed_release import _install_slot
from tests.test_release_signing import VERIFICATION_TIME, _policy, _signed


class _FakePopen:
    def __init__(self, *, missing_pipe: bool = False) -> None:
        self.args = ["fake-sidecar"]
        self.pid = 12345
        self.returncode: int | None = None
        self.stdin = None if missing_pipe else io.BytesIO()
        self.stdout = io.BytesIO()
        self.stderr = io.BytesIO()

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.returncode = 0
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = 0


class _TimeoutOncePopen(_FakePopen):
    def __init__(self) -> None:
        super().__init__()
        self.wait_calls = 0

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        if self.wait_calls == 1:
            raise subprocess.TimeoutExpired(self.args, timeout)
        return super().wait(timeout)


def _pointer_for(
    manifest_path: Path,
    *,
    physical_slot: str = "A",
    generation: int = 1,
    installation_id: str = "test-installation-1",
) -> ActiveSlotPointerV1:
    manifest = parse_release_manifest(manifest_path.read_bytes())
    return ActiveSlotPointerV1.model_validate_json(
        canonical_active_slot_pointer_bytes(
            ActiveSlotPointerV1.model_construct(
                schema_version="seektalent.active-slot/v1",
                installation_id=installation_id,
                physical_slot=physical_slot,
                pointer_generation=generation,
                product_build_id=manifest.product_build_id,
                release_manifest_sha256=installed_release.release_manifest_digest(manifest),
                committed_at="2026-07-20T12:00:00Z",
            )
        )
    )


def _write_pointer(root: Path, pointer: ActiveSlotPointerV1) -> None:
    (root / installed_release.ACTIVE_SLOT_POINTER_RELATIVE_PATH).write_bytes(
        canonical_active_slot_pointer_bytes(pointer)
    )


def _install_active_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    source_slot, _, _ = _install_slot(tmp_path, monkeypatch)
    root = tmp_path / "installation"
    slot_root = root / "slots" / "A"
    slot_root.parent.mkdir(parents=True)
    source_slot.rename(slot_root)
    manifest_path = slot_root / installed_release.INSTALLED_MANIFEST_RELATIVE_PATH
    manifest = parse_release_manifest(manifest_path.read_bytes())
    _, signature_payload = _signed(manifest)
    signature_path = slot_root / installed_release.INSTALLED_SIGNATURE_RELATIVE_PATH
    signature_path.parent.mkdir()
    signature_path.write_text(__import__("json").dumps(signature_payload, separators=(",", ":")), encoding="utf-8")

    control = root / "control"
    control.mkdir()
    (control / "installation-id").write_bytes(b"test-installation-1")
    (control / "active-slot.lock").write_bytes(b"0")
    (control / "slot-A.lock").write_bytes(b"0")
    (control / "slot-B.lock").write_bytes(b"0")
    _write_pointer(root, _pointer_for(manifest_path))
    return root, slot_root


@pytest.fixture
def installed_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root, _ = _install_active_slot(tmp_path, monkeypatch)
    return root


def _acquire(root: Path) -> InstalledSidecarLaunchLease:
    return acquire_installed_sidecar_launch_lease(root, _policy(), VERIFICATION_TIME)


def test_pointer_parser_requires_canonical_bytes_and_reuses_strict_json_boundary() -> None:
    raw = (
        b'{"committed_at":"2026-07-20T12:00:00Z","installation_id":"test-installation-1",'
        b'"physical_slot":"A","pointer_generation":1,'
        b'"product_build_id":"st1-0123456789abcdef0123456789abcdef",'
        b'"release_manifest_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        b'"schema_version":"seektalent.active-slot/v1"}'
    )
    pointer = parse_active_slot_pointer(raw)

    assert canonical_active_slot_pointer_bytes(pointer) == raw
    for malformed, reason in [
        (b'{"schema_version":"a","schema_version":"b"}', ActiveSlotPointerReason.DUPLICATE_KEY),
        (b'{"unknown":true}', ActiveSlotPointerReason.UNKNOWN_FIELD),
        (b'{"pointer_generation":1.0}', ActiveSlotPointerReason.ILLEGAL_NUMBER),
        (b"[]", ActiveSlotPointerReason.ROOT_NOT_OBJECT),
        (b" " + raw, ActiveSlotPointerReason.NON_CANONICAL),
    ]:
        with pytest.raises(ActiveSlotPointerError) as raised:
            parse_active_slot_pointer(malformed)
        assert raised.value.reason == reason

    for non_bytes in (raw.decode("utf-8"), bytearray(raw), {"physical_slot": "A"}):
        with pytest.raises(ActiveSlotPointerError) as raised:
            parse_active_slot_pointer(non_bytes)  # type: ignore[arg-type]
        assert raised.value.reason == ActiveSlotPointerReason.RAW_INPUT_REQUIRED

    with pytest.raises(ActiveSlotPointerError) as raised:
        ActiveSlotPointerV1.model_validate({"physical_slot": "A"})
    assert raised.value.reason == ActiveSlotPointerReason.RAW_INPUT_REQUIRED
    with pytest.raises(ActiveSlotPointerError) as raised:
        ActiveSlotPointerV1.model_validate_json(raw[:-1] + b',"unknown":true}', extra="allow")
    assert raised.value.reason == ActiveSlotPointerReason.UNKNOWN_FIELD


def test_windows_snapshot_comparison_ignores_only_unstable_ctime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "active-slot.json"
    expected = installed_release._PathSnapshot(path, 1, 2, 0o100666, 1, 100, 10, 11)
    actual = installed_release._PathSnapshot(path, 1, 2, 0o100666, 1, 100, 10, 12)
    monkeypatch.setattr(installed_release.os, "name", "nt")

    installed_release._require_same_snapshot(expected, actual)

    monkeypatch.setattr(installed_release.os, "name", "posix")
    with pytest.raises(installed_release.InstalledReleaseError) as raised:
        installed_release._require_same_snapshot(expected, actual)
    assert raised.value.reason == installed_release.InstalledReleaseReason.PATH_CHANGED


def test_pointer_identity_mismatch_and_pointer_swap_fail_before_popen(
    installed_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(owned_process.subprocess, "Popen", lambda *args, **kwargs: calls.append((args, kwargs)))
    pointer_path = installed_root / installed_release.ACTIVE_SLOT_POINTER_RELATIVE_PATH
    pointer = parse_active_slot_pointer(pointer_path.read_bytes())
    _write_pointer(
        installed_root,
        pointer.model_copy(update={"installation_id": "other-installation"}),
    )
    with pytest.raises(InstalledSlotError) as raised:
        _acquire(installed_root)
    assert raised.value.reason == InstalledSlotReason.SLOT_IDENTITY_MISMATCH

    _write_pointer(installed_root, pointer)
    original = installed_release._acquire_slot_lock

    def acquire_then_switch(root: Path, physical_slot: str) -> installed_release._NativeSlotLock:
        lock = original(root, cast(Literal["A", "B"], physical_slot))
        _write_pointer(installed_root, pointer.model_copy(update={"pointer_generation": 2}))
        return lock

    monkeypatch.setattr(installed_release, "_acquire_slot_lock", acquire_then_switch)
    with pytest.raises(InstalledSlotError) as raised:
        _acquire(installed_root)
    assert raised.value.reason == InstalledSlotReason.ACTIVE_SLOT_POINTER_CHANGED
    assert calls == []


def test_admitted_release_must_match_pointer_identity_before_popen(
    installed_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(owned_process.subprocess, "Popen", lambda *args, **kwargs: calls.append((args, kwargs)))
    pointer_path = installed_root / installed_release.ACTIVE_SLOT_POINTER_RELATIVE_PATH
    pointer = parse_active_slot_pointer(pointer_path.read_bytes())
    _write_pointer(installed_root, pointer.model_copy(update={"product_build_id": "st1-" + "f" * 32}))

    with pytest.raises(InstalledSlotError) as raised:
        _acquire(installed_root)

    assert raised.value.reason == InstalledSlotReason.SLOT_IDENTITY_MISMATCH
    assert calls == []


@pytest.mark.parametrize("mutation", ["duplicate", "unknown", "noncanonical"])
def test_invalid_active_pointer_has_causal_failure_and_never_spawns(
    installed_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    pointer_path = installed_root / installed_release.ACTIVE_SLOT_POINTER_RELATIVE_PATH
    original = pointer_path.read_bytes()
    if mutation == "duplicate":
        pointer_path.write_bytes(b'{"schema_version":"seektalent.active-slot/v1",' + original[1:])
    elif mutation == "unknown":
        pointer_path.write_bytes(original[:-1] + b',"unknown":true}')
    else:
        pointer_path.write_bytes(b" " + original)
    calls: list[object] = []
    monkeypatch.setattr(owned_process.subprocess, "Popen", lambda *args, **kwargs: calls.append((args, kwargs)))

    with pytest.raises(InstalledSlotError) as raised:
        _acquire(installed_root)

    assert raised.value.reason == InstalledSlotReason.ACTIVE_SLOT_POINTER_INVALID
    assert calls == []


def test_lease_hard_cut_rejects_bare_admission_copies_pickle_and_released_lease_before_popen(
    installed_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _acquire(installed_root)
    calls: list[object] = []
    monkeypatch.setattr(owned_process.subprocess, "Popen", lambda *args, **kwargs: calls.append((args, kwargs)))

    with pytest.raises(TypeError):
        spawn_owned_sidecar(lease.admission)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        copy.copy(lease)
    with pytest.raises(TypeError):
        copy.deepcopy(lease)
    with pytest.raises(TypeError):
        replace(lease)
    with pytest.raises(TypeError):
        pickle.dumps(lease)
    fake = object.__new__(InstalledSidecarLaunchLease)
    with pytest.raises(TypeError):
        spawn_owned_sidecar(fake)

    lease.close()
    with pytest.raises(TypeError):
        spawn_owned_sidecar(lease)
    assert calls == []


def test_live_lease_conflicts_until_owned_child_is_reaped(
    installed_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _acquire(installed_root)
    fake = _FakePopen()
    monkeypatch.setattr(owned_process.subprocess, "Popen", lambda *args, **kwargs: fake)
    process = spawn_owned_sidecar(lease)

    with pytest.raises(InstalledSlotError) as raised:
        _acquire(installed_root)
    assert raised.value.reason == InstalledSlotReason.SLOT_LEASE_CONFLICT

    assert process.wait(1) == 0
    next_lease = _acquire(installed_root)
    next_lease.close()


def test_pointer_can_switch_to_b_while_a_remains_leased(
    installed_root: Path,
) -> None:
    slot_a = installed_root / installed_release.SLOT_ROOT_RELATIVE_PATHS["A"]
    slot_b = installed_root / installed_release.SLOT_ROOT_RELATIVE_PATHS["B"]
    shutil.copytree(slot_a, slot_b)
    pointer_path = installed_root / installed_release.ACTIVE_SLOT_POINTER_RELATIVE_PATH
    pointer_a = parse_active_slot_pointer(pointer_path.read_bytes())
    lease_a = _acquire(installed_root)

    _write_pointer(
        installed_root,
        pointer_a.model_copy(update={"physical_slot": "B", "pointer_generation": 2}),
    )
    lease_b = _acquire(installed_root)
    assert lease_a.identity.physical_slot == "A"
    assert lease_b.identity.physical_slot == "B"
    assert lease_a.identity != lease_b.identity

    with pytest.raises(InstalledSlotError) as raised:
        installed_release._acquire_slot_lock(installed_root, "A")
    assert raised.value.reason == InstalledSlotReason.SLOT_LEASE_CONFLICT

    lease_b.close()
    lease_a.close()
    released_lock = installed_release._acquire_slot_lock(installed_root, "A")
    released_lock.close()


@pytest.mark.parametrize("failure", ["popen", "missing_pipe"])
def test_spawn_failure_reaps_or_closes_then_releases_slot_lease(
    installed_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    lease = _acquire(installed_root)
    if failure == "popen":
        monkeypatch.setattr(
            owned_process.subprocess,
            "Popen",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("spawn failed")),
        )
        error = OSError
    else:
        monkeypatch.setattr(
            owned_process.subprocess,
            "Popen",
            lambda *args, **kwargs: _FakePopen(missing_pipe=True),
        )
        error = RuntimeError

    with pytest.raises(error):
        spawn_owned_sidecar(lease)

    next_lease = _acquire(installed_root)
    next_lease.close()


def test_pipe_cleanup_error_still_releases_the_consumed_lease(
    installed_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        owned_process.subprocess,
        "Popen",
        lambda *args, **kwargs: _FakePopen(missing_pipe=True),
    )
    monkeypatch.setattr(
        owned_process,
        "_reap_failed_spawn",
        lambda *args: (_ for _ in ()).throw(OSError("reap failed")),
    )

    with pytest.raises(OSError, match="reap failed"):
        spawn_owned_sidecar(_acquire(installed_root))

    next_lease = _acquire(installed_root)
    next_lease.close()


def test_timeout_retains_lease_until_a_direct_child_is_reaped(
    installed_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _TimeoutOncePopen()
    monkeypatch.setattr(owned_process.subprocess, "Popen", lambda *args, **kwargs: fake)
    process = spawn_owned_sidecar(_acquire(installed_root))

    with pytest.raises(subprocess.TimeoutExpired):
        process.wait(1)
    with pytest.raises(InstalledSlotError) as raised:
        _acquire(installed_root)
    assert raised.value.reason == InstalledSlotReason.SLOT_LEASE_CONFLICT

    assert process.wait(1) == 0
    next_lease = _acquire(installed_root)
    next_lease.close()


def test_release_failure_never_restores_lease_authority(
    installed_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _acquire(installed_root)
    original_unlock = installed_release._unlock_native_slot_lock
    monkeypatch.setattr(
        installed_release,
        "_unlock_native_slot_lock",
        lambda *args: (_ for _ in ()).throw(OSError("unlock failed")),
    )
    with pytest.raises(InstalledSlotError) as raised:
        lease.close()
    assert raised.value.reason == InstalledSlotReason.SLOT_RELEASE_FAILED

    calls: list[object] = []
    monkeypatch.setattr(owned_process.subprocess, "Popen", lambda *args, **kwargs: calls.append((args, kwargs)))
    with pytest.raises(TypeError):
        spawn_owned_sidecar(lease)
    assert calls == []

    monkeypatch.setattr(installed_release, "_unlock_native_slot_lock", original_unlock)
    next_lease = _acquire(installed_root)
    next_lease.close()


@pytest.mark.skipif(
    os.name != "nt" and sys.platform != "darwin",
    reason="native cooperative lock evidence is only claimed on Windows and macOS",
)
def test_native_cross_process_slot_lock_conflicts(installed_root: Path) -> None:
    lock_path = installed_root / installed_release.SLOT_LOCK_RELATIVE_PATHS["A"]
    if os.name == "nt":
        program = (
            "import msvcrt, os, sys; f=os.open(sys.argv[1], os.O_RDWR); "
            "os.lseek(f, 0, os.SEEK_SET); msvcrt.locking(f, msvcrt.LK_NBLCK, 1); "
            "print('ready', flush=True); sys.stdin.read()"
        )
    else:
        program = (
            "import fcntl, os, sys; f=os.open(sys.argv[1], os.O_RDWR); "
            "fcntl.flock(f, fcntl.LOCK_EX); print('ready', flush=True); sys.stdin.read()"
        )
    holder = subprocess.Popen(
        [sys.executable, "-c", program, str(lock_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    assert holder.stdin is not None
    try:
        assert holder.stdout.readline().strip() == "ready"
        with pytest.raises(InstalledSlotError) as raised:
            _acquire(installed_root)
        assert raised.value.reason == InstalledSlotReason.SLOT_LEASE_CONFLICT
    finally:
        holder.stdin.close()
        assert holder.wait(timeout=5) == 0
