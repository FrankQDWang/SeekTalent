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
import seektalent.installed_slot as installed_slot
import seektalent.owned_sidecar_process as owned_process
from seektalent.installed_slot import (
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


class _CloseFailsOnce(io.BytesIO):
    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message
        self._failed = False

    def close(self) -> None:
        super().close()
        if not self._failed:
            self._failed = True
            raise OSError(self._message)


class _RetryableCloseStream:
    def __init__(self) -> None:
        self.closed = False
        self.close_calls = 0
        self.fail = True

    def close(self) -> None:
        self.close_calls += 1
        if self.fail:
            raise OSError("injected persistent stream close failure")
        self.closed = True


class _FakePopen:
    def __init__(
        self,
        *,
        missing_pipe: bool = False,
        close_failure_stream: str | None = None,
        kill_failure: OSError | None = None,
        wait_failure: OSError | None = None,
        wait_failure_reaps: bool = True,
        kill_sets_returncode: bool = True,
    ) -> None:
        self.args = ["fake-sidecar"]
        self.pid = 12345
        self.returncode: int | None = None
        self.stdin = None if missing_pipe else io.BytesIO()
        self.stdout = io.BytesIO()
        self.stderr = io.BytesIO()
        if close_failure_stream is not None:
            setattr(self, close_failure_stream, _CloseFailsOnce(f"{close_failure_stream} close failed"))
        self.kill_failure = kill_failure
        self.wait_failure = wait_failure
        self.wait_failure_reaps = wait_failure_reaps
        self.kill_sets_returncode = kill_sets_returncode
        self.kill_calls = 0
        self.wait_calls = 0

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.wait_calls += 1
        if self.wait_failure is not None:
            if self.wait_failure_reaps:
                self.returncode = 0
            raise self.wait_failure
        self.returncode = 0
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.kill_calls += 1
        if self.kill_failure is not None:
            raise self.kill_failure
        if self.kill_sets_returncode:
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
    (root / installed_slot.ACTIVE_SLOT_POINTER_RELATIVE_PATH).write_bytes(
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
    pointer_path = installed_root / installed_slot.ACTIVE_SLOT_POINTER_RELATIVE_PATH
    pointer = parse_active_slot_pointer(pointer_path.read_bytes())
    _write_pointer(
        installed_root,
        pointer.model_copy(update={"installation_id": "other-installation"}),
    )
    with pytest.raises(InstalledSlotError) as raised:
        _acquire(installed_root)
    assert raised.value.reason == InstalledSlotReason.SLOT_IDENTITY_MISMATCH

    _write_pointer(installed_root, pointer)
    original = installed_slot._acquire_slot_lock

    def acquire_then_switch(root: Path, physical_slot: str) -> installed_slot._NativeSlotLock:
        lock = original(root, cast(Literal["A", "B"], physical_slot))
        _write_pointer(installed_root, pointer.model_copy(update={"pointer_generation": 2}))
        return lock

    monkeypatch.setattr(installed_slot, "_acquire_slot_lock", acquire_then_switch)
    with pytest.raises(InstalledSlotError) as raised:
        _acquire(installed_root)
    assert raised.value.reason == InstalledSlotReason.ACTIVE_SLOT_POINTER_CHANGED
    assert calls == []


def test_installation_id_change_after_slot_lease_fails_closed_and_releases_lock(
    installed_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pointer_path = installed_root / installed_slot.ACTIVE_SLOT_POINTER_RELATIVE_PATH
    installation_id_path = installed_root / installed_slot.INSTALLATION_ID_RELATIVE_PATH
    original_pointer = parse_active_slot_pointer(pointer_path.read_bytes())
    original_installation_id = installation_id_path.read_bytes()
    original_acquire = installed_slot._acquire_slot_lock
    original_admit = installed_slot.admit_installed_sidecar_launch
    admissions: list[object] = []
    popen_calls: list[object] = []
    monkeypatch.setattr(
        installed_slot,
        "admit_installed_sidecar_launch",
        lambda *args: (admissions.append(args), original_admit(*args))[1],
    )
    monkeypatch.setattr(
        owned_process.subprocess,
        "Popen",
        lambda *args, **kwargs: popen_calls.append((args, kwargs)),
    )

    def acquire_then_change_installation(
        root: Path,
        physical_slot: str,
    ) -> installed_slot._NativeSlotLock:
        lock = original_acquire(root, cast(Literal["A", "B"], physical_slot))
        installation_id_path.write_bytes(b"other-installation")
        return lock

    monkeypatch.setattr(installed_slot, "_acquire_slot_lock", acquire_then_change_installation)
    with pytest.raises(InstalledSlotError) as raised:
        _acquire(installed_root)
    assert raised.value.reason == InstalledSlotReason.SLOT_IDENTITY_MISMATCH
    assert admissions == []
    assert popen_calls == []

    installation_id_path.write_bytes(original_installation_id)
    _write_pointer(installed_root, original_pointer)
    monkeypatch.setattr(installed_slot, "_acquire_slot_lock", original_acquire)
    released_lock = installed_slot._acquire_slot_lock(installed_root, "A")
    released_lock.close()


@pytest.mark.parametrize(
    "update",
    [
        {"pointer_generation": 2},
        {"physical_slot": "B"},
        {"product_build_id": "st1-" + "f" * 32},
        {"release_manifest_sha256": "f" * 64},
    ],
)
def test_pointer_identity_fact_change_after_slot_lease_fails_before_admission(
    installed_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    update: dict[str, object],
) -> None:
    pointer_path = installed_root / installed_slot.ACTIVE_SLOT_POINTER_RELATIVE_PATH
    original_pointer = parse_active_slot_pointer(pointer_path.read_bytes())
    original_acquire = installed_slot._acquire_slot_lock
    admissions: list[object] = []
    monkeypatch.setattr(
        installed_slot,
        "admit_installed_sidecar_launch",
        lambda *args: admissions.append(args),
    )

    def acquire_then_change_pointer(
        root: Path,
        physical_slot: str,
    ) -> installed_slot._NativeSlotLock:
        lock = original_acquire(root, cast(Literal["A", "B"], physical_slot))
        _write_pointer(installed_root, original_pointer.model_copy(update=update))
        return lock

    monkeypatch.setattr(installed_slot, "_acquire_slot_lock", acquire_then_change_pointer)
    with pytest.raises(InstalledSlotError) as raised:
        _acquire(installed_root)
    assert raised.value.reason == InstalledSlotReason.ACTIVE_SLOT_POINTER_CHANGED
    assert admissions == []

    monkeypatch.setattr(installed_slot, "_acquire_slot_lock", original_acquire)
    released_lock = installed_slot._acquire_slot_lock(installed_root, "A")
    released_lock.close()


def test_admitted_release_must_match_pointer_identity_before_popen(
    installed_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(owned_process.subprocess, "Popen", lambda *args, **kwargs: calls.append((args, kwargs)))
    pointer_path = installed_root / installed_slot.ACTIVE_SLOT_POINTER_RELATIVE_PATH
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
    pointer_path = installed_root / installed_slot.ACTIVE_SLOT_POINTER_RELATIVE_PATH
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


@pytest.mark.parametrize(
    ("expected", "replacement"),
    [
        (b'"pointer_generation":1', b'"pointer_generation":0'),
        (b'"physical_slot":"A"', b'"physical_slot":"C"'),
        (
            b'"release_manifest_sha256":"',
            b'"release_manifest_sha256":"not-a-digest-',
        ),
    ],
)
def test_invalid_pointer_identity_facts_fail_before_admission(
    installed_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    expected: bytes,
    replacement: bytes,
) -> None:
    pointer_path = installed_root / installed_slot.ACTIVE_SLOT_POINTER_RELATIVE_PATH
    original = pointer_path.read_bytes()
    pointer_path.write_bytes(original.replace(expected, replacement, 1))
    admissions: list[object] = []
    monkeypatch.setattr(installed_slot, "admit_installed_sidecar_launch", lambda *args: admissions.append(args))

    with pytest.raises(InstalledSlotError) as raised:
        _acquire(installed_root)

    assert raised.value.reason == InstalledSlotReason.ACTIVE_SLOT_POINTER_INVALID
    assert admissions == []


def test_pointer_product_build_mismatch_fails_after_admission_before_popen(
    installed_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pointer_path = installed_root / installed_slot.ACTIVE_SLOT_POINTER_RELATIVE_PATH
    pointer = parse_active_slot_pointer(pointer_path.read_bytes())
    _write_pointer(
        installed_root,
        pointer.model_copy(update={"product_build_id": "st1-" + "f" * 32}),
    )
    calls: list[object] = []
    monkeypatch.setattr(owned_process.subprocess, "Popen", lambda *args, **kwargs: calls.append((args, kwargs)))

    with pytest.raises(InstalledSlotError) as raised:
        _acquire(installed_root)

    assert raised.value.reason == InstalledSlotReason.SLOT_IDENTITY_MISMATCH
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
    slot_a = installed_root / installed_slot.SLOT_ROOT_RELATIVE_PATHS["A"]
    slot_b = installed_root / installed_slot.SLOT_ROOT_RELATIVE_PATHS["B"]
    shutil.copytree(slot_a, slot_b)
    pointer_path = installed_root / installed_slot.ACTIVE_SLOT_POINTER_RELATIVE_PATH
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
        installed_slot._acquire_slot_lock(installed_root, "A")
    assert raised.value.reason == InstalledSlotReason.SLOT_LEASE_CONFLICT

    lease_b.close()
    lease_a.close()
    released_lock = installed_slot._acquire_slot_lock(installed_root, "A")
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


def test_pipe_close_failure_reaps_child_before_releasing_the_consumed_lease(
    installed_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePopen(missing_pipe=True, close_failure_stream="stdout")
    monkeypatch.setattr(
        owned_process.subprocess,
        "Popen",
        lambda *args, **kwargs: fake,
    )
    monkeypatch.setattr(owned_process, "_signal_process_group", lambda *_: fake.kill())

    with pytest.raises(RuntimeError, match="three stdio pipes") as raised:
        spawn_owned_sidecar(_acquire(installed_root))
    assert any("stdout close failed" in note for note in raised.value.__notes__)
    assert fake.kill_calls == 1
    assert fake.wait_calls == 1
    assert fake.returncode == 0
    assert fake.stdout.closed
    assert fake.stderr.closed

    next_lease = _acquire(installed_root)
    next_lease.close()


def test_reaped_spawn_retains_retryable_lease_cleanup_after_slot_release_failure(
    installed_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reaped failed child is not sufficient while its native slot lock remains held."""
    fake = _FakePopen(missing_pipe=True)
    lease = _acquire(installed_root)
    monkeypatch.setattr(owned_process.subprocess, "Popen", lambda *args, **kwargs: fake)
    monkeypatch.setattr(owned_process, "_signal_process_group", lambda *_: fake.kill())
    original_unlock = installed_slot._unlock_native_slot_lock
    attempts = 0

    def fail_once_then_unlock(descriptor: int, platform: str) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("injected unlock failure")
        original_unlock(descriptor, platform)

    monkeypatch.setattr(installed_slot, "_unlock_native_slot_lock", fail_once_then_unlock)

    with pytest.raises(owned_process.SidecarSpawnCleanupError) as raised:
        spawn_owned_sidecar(lease)

    cleanup_error = raised.value
    assert cleanup_error.direct_child_reaped is True
    assert cleanup_error.lease_released is False
    with pytest.raises(InstalledSlotError) as conflict:
        _acquire(installed_root)
    assert conflict.value.reason is InstalledSlotReason.SLOT_LEASE_CONFLICT

    assert cleanup_error.reap() is True
    assert cleanup_error.lease_released is True
    next_lease = _acquire(installed_root)
    next_lease.close()


@pytest.mark.parametrize("failure", ["kill", "wait"])
def test_failed_spawn_cleanup_error_is_diagnostic_after_reap(
    installed_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    fake = _FakePopen(
        missing_pipe=True,
        kill_failure=OSError("kill failed") if failure == "kill" else None,
        wait_failure=OSError("wait failed") if failure == "wait" else None,
    )
    monkeypatch.setattr(owned_process.subprocess, "Popen", lambda *args, **kwargs: fake)
    monkeypatch.setattr(owned_process, "_signal_process_group", lambda *_: fake.kill())

    with pytest.raises(RuntimeError, match="three stdio pipes") as raised:
        spawn_owned_sidecar(_acquire(installed_root))

    assert fake.kill_calls == 1
    assert fake.wait_calls == 1
    assert fake.returncode == 0
    assert any(f"{failure} failed" in note for note in raised.value.__notes__)
    next_lease = _acquire(installed_root)
    next_lease.close()


def test_confirmed_reap_keeps_its_fact_when_slot_release_fails(
    installed_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePopen(
        missing_pipe=True,
        wait_failure=OSError("wait failed"),
        wait_failure_reaps=False,
        kill_sets_returncode=False,
    )
    monkeypatch.setattr(owned_process.subprocess, "Popen", lambda *args, **kwargs: fake)
    monkeypatch.setattr(owned_process, "_signal_process_group", lambda *_: fake.kill())
    with pytest.raises(owned_process.SidecarSpawnCleanupError) as raised:
        spawn_owned_sidecar(_acquire(installed_root))
    cleanup_error = raised.value

    fake.wait_failure = None
    fake.kill_sets_returncode = True
    original_unlock = installed_slot._unlock_native_slot_lock
    monkeypatch.setattr(
        installed_slot,
        "_unlock_native_slot_lock",
        lambda *args: (_ for _ in ()).throw(OSError("unlock failed")),
    )
    with pytest.raises(InstalledSlotError) as release_failure:
        cleanup_error.reap()
    assert release_failure.value.reason == InstalledSlotReason.SLOT_RELEASE_FAILED
    assert cleanup_error.direct_child_reaped is True
    assert cleanup_error.lease_released is False

    monkeypatch.setattr(installed_slot, "_unlock_native_slot_lock", original_unlock)
    assert cleanup_error.reap() is True
    assert cleanup_error.lease_released is True
    next_lease = _acquire(installed_root)
    next_lease.close()


def test_unreaped_failed_spawn_retains_explicit_lease_owner_until_retry_succeeds(
    installed_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePopen(
        missing_pipe=True,
        wait_failure=OSError("wait failed"),
        wait_failure_reaps=False,
        kill_sets_returncode=False,
    )
    monkeypatch.setattr(owned_process.subprocess, "Popen", lambda *args, **kwargs: fake)
    monkeypatch.setattr(owned_process, "_signal_process_group", lambda *_: fake.kill())

    with pytest.raises(owned_process.SidecarSpawnCleanupError) as raised:
        spawn_owned_sidecar(_acquire(installed_root))

    cleanup_error = raised.value
    assert cleanup_error.direct_child_reaped is False
    assert fake.kill_calls == 1
    assert fake.wait_calls == 1
    with pytest.raises(InstalledSlotError) as lease_conflict:
        _acquire(installed_root)
    assert lease_conflict.value.reason == InstalledSlotReason.SLOT_LEASE_CONFLICT

    assert cleanup_error.reap() is False
    assert cleanup_error.direct_child_reaped is False
    assert fake.wait_calls == 2
    with pytest.raises(InstalledSlotError) as retry_conflict:
        _acquire(installed_root)
    assert retry_conflict.value.reason == InstalledSlotReason.SLOT_LEASE_CONFLICT

    fake.wait_failure = None
    fake.kill_sets_returncode = True
    assert cleanup_error.reap() is True
    assert cleanup_error.direct_child_reaped is True
    next_lease = _acquire(installed_root)
    next_lease.close()


def test_unreaped_failed_spawn_retains_unclosed_pipe_until_a_later_retry_closes_it(
    installed_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePopen(
        missing_pipe=True,
        wait_failure=OSError("first wait failed"),
        wait_failure_reaps=False,
        kill_sets_returncode=False,
    )
    stuck = _RetryableCloseStream()
    fake.stdout = cast(io.BytesIO, stuck)
    monkeypatch.setattr(owned_process.subprocess, "Popen", lambda *args, **kwargs: fake)
    monkeypatch.setattr(owned_process, "_signal_process_group", lambda *_: fake.kill())

    with pytest.raises(owned_process.SidecarSpawnCleanupError) as raised:
        spawn_owned_sidecar(_acquire(installed_root))

    cleanup_error = raised.value
    assert cleanup_error.direct_child_reaped is False
    assert stuck.close_calls == 1

    fake.wait_failure = None
    fake.kill_sets_returncode = True
    assert cleanup_error.reap() is False
    assert cleanup_error.direct_child_reaped is True
    assert cleanup_error.lease_released is True
    assert stuck.closed is False
    assert stuck.close_calls == 2

    stuck.fail = False
    assert cleanup_error.reap() is True
    assert stuck.closed is True
    assert stuck.close_calls == 3
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
    original_unlock = installed_slot._unlock_native_slot_lock
    monkeypatch.setattr(
        installed_slot,
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

    monkeypatch.setattr(installed_slot, "_unlock_native_slot_lock", original_unlock)
    lease.close()
    next_lease = _acquire(installed_root)
    next_lease.close()


def test_native_slot_lock_retries_fd_close_without_reunlocking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor, peer = os.pipe()
    lock = installed_slot._NativeSlotLock(tmp_path / "slot.lock", descriptor, "posix")
    unlock_calls = 0
    close_calls = 0
    original_close = installed_slot.os.close

    def unlock(value: int, platform: str) -> None:
        nonlocal unlock_calls
        assert (value, platform) == (descriptor, "posix")
        unlock_calls += 1

    def fail_first_close(value: int) -> None:
        nonlocal close_calls
        if value == descriptor:
            close_calls += 1
            if close_calls == 1:
                raise OSError("injected fd close failure")
        original_close(value)

    monkeypatch.setattr(installed_slot, "_unlock_native_slot_lock", unlock)
    monkeypatch.setattr(installed_slot.os, "close", fail_first_close)
    with pytest.raises(InstalledSlotError):
        lock.close()
    assert lock.lock_held is False
    assert lock.descriptor == descriptor
    lock.close()
    assert unlock_calls == 1
    assert lock.descriptor is None
    original_close(peer)


@pytest.mark.skipif(
    os.name != "nt" and sys.platform != "darwin",
    reason="native cooperative lock evidence is only claimed on Windows and macOS",
)
def test_native_cross_process_slot_lock_conflicts(installed_root: Path) -> None:
    lock_path = installed_root / installed_slot.SLOT_LOCK_RELATIVE_PATHS["A"]
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
