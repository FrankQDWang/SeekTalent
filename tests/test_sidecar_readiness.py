from __future__ import annotations

import copy
import io
import pickle
import socket
import subprocess
import threading
import time
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Callable

import pytest
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

import seektalent.installed_release as installed_release
import seektalent.sidecar_readiness as readiness
from seektalent.installed_slot import (
    ActiveSlotPointerV1,
    InstalledSidecarLaunchLease,
    acquire_installed_sidecar_launch_lease,
    canonical_active_slot_pointer_bytes,
)
from seektalent.owned_sidecar_process import OwnedSidecarProcess, SidecarSpawnCleanupError
from seektalent.source_port.authenticated_history_frames import HistoryFrameError, HistoryFrameReason
from seektalent.source_port.history_contract import SourceHistoryQueryV1
from seektalent.release_manifest import parse_release_manifest
from tests.test_installed_release import _install_slot
from tests.test_release_signing import VERIFICATION_TIME, _policy, _signed


class _FakeChild:
    pid = 4444

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.kill_calls = 0

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9


@pytest.fixture
def lease_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[], InstalledSidecarLaunchLease]:
    source_slot, _, _ = _install_slot(tmp_path, monkeypatch, executable_bytes=b"readiness-probe\n")
    installation_root = tmp_path / "installation"
    slot_root = installation_root / "slots" / "A"
    slot_root.parent.mkdir(parents=True)
    source_slot.rename(slot_root)
    manifest_path = slot_root / "release" / "release-manifest.json"
    manifest = parse_release_manifest(manifest_path.read_bytes())
    _, signature_payload = _signed(manifest)
    manifest_path.parent.joinpath("signatures").mkdir()
    manifest_path.parent.joinpath("signatures", "release-manifest.sig").write_text(
        __import__("json").dumps(signature_payload, separators=(",", ":")), encoding="utf-8"
    )
    control = installation_root / "control"
    control.mkdir()
    control.joinpath("installation-id").write_bytes(b"readiness-test-installation")
    control.joinpath("active-slot.lock").write_bytes(b"0")
    control.joinpath("slot-A.lock").write_bytes(b"0")
    control.joinpath("slot-B.lock").write_bytes(b"0")
    pointer = ActiveSlotPointerV1.model_construct(
        schema_version="seektalent.active-slot/v1",
        installation_id="readiness-test-installation",
        physical_slot="A",
        pointer_generation=1,
        product_build_id=manifest.product_build_id,
        release_manifest_sha256=installed_release.release_manifest_digest(manifest),
        committed_at="2026-07-21T12:00:00Z",
    )
    control.joinpath("active-slot.json").write_bytes(canonical_active_slot_pointer_bytes(pointer))
    return lambda: acquire_installed_sidecar_launch_lease(installation_root, _policy(), VERIFICATION_TIME)


def _identity(admission: installed_release.AuthenticatedInstalledSidecarLaunch) -> readiness.SidecarHandshakeIdentity:
    protocol = admission.source_port_protocol
    return readiness.SidecarHandshakeIdentity(
        product_build_id=admission.product_build_id,
        sidecar_build_id=admission.sidecar_build_id,
        protocol_id=protocol.protocol_id,
        protocol_major=protocol.major,
        protocol_min_minor=protocol.min_minor,
        protocol_max_minor=protocol.max_minor,
        protocol_capabilities=protocol.capabilities,
        expected_main_application_build_id=admission.main_application_build_id,
    )


def _connected_process(
    lease: InstalledSidecarLaunchLease,
    identity: readiness.SidecarHandshakeIdentity,
) -> tuple[
    OwnedSidecarProcess,
    _FakeChild,
    threading.Thread,
    list[BaseException],
    list[readiness.SidecarHandshakeResult],
]:
    main_socket, sidecar_socket = socket.socketpair()
    child = _FakeChild()
    protocol_writer = main_socket.makefile("wb", buffering=0)
    protocol_reader = main_socket.makefile("rb", buffering=0)
    main_socket.close()
    process = OwnedSidecarProcess(
        _process=child,
        protocol_writer=protocol_writer,
        protocol_reader=protocol_reader,
        stderr_reader=io.BytesIO(),
        _process_group_id=None,
        _lease_state=lease._take_for_spawn(),
    )
    errors: list[BaseException] = []
    results: list[readiness.SidecarHandshakeResult] = []

    def serve() -> None:
        reader = sidecar_socket.makefile("rb", buffering=0)
        writer = sidecar_socket.makefile("wb", buffering=0)
        sidecar_socket.close()
        try:
            results.append(readiness.serve_sidecar_handshake(reader, writer, identity, timeout=1))
        except (OSError, ValueError, readiness.SidecarReadinessError) as exc:
            errors.append(exc)
        finally:
            reader.close()
            writer.close()
            sidecar_socket.close()

    thread = threading.Thread(target=serve)
    thread.start()
    return process, child, thread, errors, results


def _connected_scripted_process(
    lease: InstalledSidecarLaunchLease,
    script: Callable[[object, object], None],
) -> tuple[OwnedSidecarProcess, _FakeChild, threading.Thread]:
    main_socket, sidecar_socket = socket.socketpair()
    child = _FakeChild()
    protocol_writer = main_socket.makefile("wb", buffering=0)
    protocol_reader = main_socket.makefile("rb", buffering=0)
    main_socket.close()
    process = OwnedSidecarProcess(
        _process=child,
        protocol_writer=protocol_writer,
        protocol_reader=protocol_reader,
        stderr_reader=io.BytesIO(),
        _process_group_id=None,
        _lease_state=lease._take_for_spawn(),
    )

    def serve() -> None:
        reader = sidecar_socket.makefile("rb", buffering=0)
        writer = sidecar_socket.makefile("wb", buffering=0)
        sidecar_socket.close()
        try:
            script(reader, writer)
        finally:
            reader.close()
            writer.close()

    thread = threading.Thread(target=serve)
    thread.start()
    return process, child, thread


def _read_frame(stream: object) -> bytes:
    read = getattr(stream, "read")
    header = read(4)
    while len(header) < 4:
        more = read(4 - len(header))
        if not more:
            raise EOFError
        header += more
    length = int.from_bytes(header, "big")
    body = b""
    while len(body) < length:
        more = read(length - len(body))
        if not more:
            raise EOFError
        body += more
    return body


def _write_frame(stream: object, body: bytes) -> None:
    write = getattr(stream, "write")
    flush = getattr(stream, "flush")
    write(len(body).to_bytes(4, "big") + body)
    flush()


def test_ready_session_requires_the_complete_four_step_transcript(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = lease_factory()
    process, child, thread, errors, _ = _connected_process(lease, _identity(lease.admission))
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)

    session = readiness.spawn_ready_sidecar(lease, timeout=1)

    assert session.process is process
    assert session.session_id
    assert child.kill_calls == 0
    assert errors == []
    session.close(1)
    thread.join(timeout=1)
    assert not thread.is_alive()
    lease_factory().close()


def test_identity_mismatch_fails_closed_and_releases_the_slot_lease(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = lease_factory()
    wrong_identity = replace(_identity(lease.admission), sidecar_build_id="caller-forged-sidecar")
    process, child, thread, _, _ = _connected_process(lease, wrong_identity)
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)

    with pytest.raises(readiness.SidecarReadinessError) as raised:
        readiness.spawn_ready_sidecar(lease, timeout=1)

    assert raised.value.reason is readiness.SidecarReadinessReason.IDENTITY_MISMATCH
    assert child.kill_calls == 1
    thread.join(timeout=1)
    assert not thread.is_alive()
    lease_factory().close()


def test_ready_session_cannot_be_fabricated_copied_or_serialized(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = lease_factory()
    process, _, thread, errors, _ = _connected_process(lease, _identity(lease.admission))
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)
    session = readiness.spawn_ready_sidecar(lease, timeout=1)

    with pytest.raises(TypeError):
        readiness.ReadySidecarSession()
    with pytest.raises(TypeError):
        readiness.ReadySidecarSession(**{"process": process})  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        copy.copy(session)
    with pytest.raises(TypeError):
        copy.deepcopy(session)
    with pytest.raises(TypeError):
        replace(session)
    with pytest.raises(TypeError):
        pickle.dumps(session)
    fake = object.__new__(readiness.ReadySidecarSession)
    with pytest.raises(TypeError):
        _ = fake.process

    session.close(1)
    thread.join(timeout=1)
    assert errors == []


@pytest.mark.parametrize(
    ("script", "reason"),
    [
        pytest.param(
            lambda reader, writer: (
                _read_frame(reader),
                _write_frame(writer, b'{"message_type":"main_ready","handshake_version":1}'),
            ),
            readiness.SidecarReadinessReason.UNEXPECTED_MESSAGE,
            id="out-of-order-message",
        ),
        pytest.param(
            lambda reader, writer: (_read_frame(reader), _write_frame(writer, b"{")),
            readiness.SidecarReadinessReason.INVALID_JSON,
            id="invalid-json",
        ),
        pytest.param(
            lambda reader, writer: (
                _read_frame(reader),
                getattr(writer, "write")((readiness.MAX_HANDSHAKE_FRAME_BYTES + 1).to_bytes(4, "big")),
            ),
            readiness.SidecarReadinessReason.FRAME_TOO_LARGE,
            id="oversize-frame",
        ),
        pytest.param(
            lambda reader, writer: (
                _read_frame(reader),
                getattr(writer, "write")((8).to_bytes(4, "big") + b"{"),
                getattr(writer, "flush")(),
            ),
            readiness.SidecarReadinessReason.TRUNCATED_FRAME,
            id="partial-frame-eof",
        ),
        pytest.param(
            lambda reader, writer: (_read_frame(reader), time.sleep(0.2)),
            readiness.SidecarReadinessReason.READ_TIMEOUT,
            id="silent-child-timeout",
        ),
    ],
)
def test_invalid_readiness_frames_fail_closed_with_a_typed_reason(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
    script: Callable[[object, object], None],
    reason: readiness.SidecarReadinessReason,
) -> None:
    lease = lease_factory()
    process, child, thread = _connected_scripted_process(lease, script)
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)

    with pytest.raises(readiness.SidecarReadinessError) as raised:
        readiness.spawn_ready_sidecar(lease, timeout=0.05)

    assert raised.value.reason is reason
    assert child.kill_calls == 1
    thread.join(timeout=1)
    assert not thread.is_alive()
    lease_factory().close()


def test_child_exit_before_sidecar_hello_is_typed_and_releases_its_lease(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child_holder: dict[str, _FakeChild] = {}

    def exit_after_main_hello(reader: object, _: object) -> None:
        _read_frame(reader)
        child_holder["child"].returncode = 70

    lease = lease_factory()
    process, child, thread = _connected_scripted_process(lease, exit_after_main_hello)
    child_holder["child"] = child
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)

    with pytest.raises(readiness.SidecarReadinessError) as raised:
        readiness.spawn_ready_sidecar(lease, timeout=1)

    assert raised.value.reason is readiness.SidecarReadinessReason.CHILD_EXIT
    assert child.kill_calls == 0
    thread.join(timeout=1)
    assert not thread.is_alive()
    lease_factory().close()


def test_unreaped_handshake_failure_retains_the_existing_cleanup_capability(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = lease_factory()
    process, child, thread = _connected_scripted_process(
        lease,
        lambda reader, writer: (_read_frame(reader), _write_frame(writer, b"{")),
    )
    attempts = 0

    def kill_without_confirmed_exit() -> None:
        child.kill_calls += 1

    def wait_for_retry(timeout: float | None = None) -> int:
        nonlocal attempts
        del timeout
        attempts += 1
        if attempts == 1:
            raise subprocess.TimeoutExpired("sidecar", 1)
        child.returncode = 0
        return 0

    monkeypatch.setattr(child, "kill", kill_without_confirmed_exit)
    monkeypatch.setattr(child, "wait", wait_for_retry)
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)

    with pytest.raises(readiness.SidecarReadinessError) as raised:
        readiness.spawn_ready_sidecar(lease, timeout=1)

    assert raised.value.reason is readiness.SidecarReadinessReason.INVALID_JSON
    cleanup_error = raised.value.cleanup_error
    assert isinstance(cleanup_error, SidecarSpawnCleanupError)
    assert cleanup_error.direct_child_reaped is False
    assert cleanup_error.reap() is True
    assert cleanup_error.direct_child_reaped is True
    assert cleanup_error.lease_released is True
    thread.join(timeout=1)
    assert not thread.is_alive()
    lease_factory().close()


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b'{"message_type":"main_hello","message_type":"main_hello"}', readiness.SidecarReadinessReason.DUPLICATE_KEY),
        (b"\xff", readiness.SidecarReadinessReason.INVALID_UTF8),
        (b"[]", readiness.SidecarReadinessReason.ROOT_NOT_OBJECT),
        (b'{"message_type":"main_hello","handshake_version":2}', readiness.SidecarReadinessReason.PROTOCOL_MISMATCH),
    ],
)
def test_sidecar_rejects_ambiguous_or_wrong_protocol_main_hello(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    body: bytes,
    reason: readiness.SidecarReadinessReason,
) -> None:
    lease = lease_factory()
    frame = len(body).to_bytes(4, "big") + body

    with pytest.raises(readiness.SidecarReadinessError) as raised:
        readiness.serve_sidecar_handshake(io.BytesIO(frame), io.BytesIO(), _identity(lease.admission), timeout=1)

    assert raised.value.reason is reason
    lease.close()


@pytest.mark.parametrize("field", ["product_build_id", "main_application_build_id"])
def test_sidecar_requires_the_exact_admitted_main_identity(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    field: str,
) -> None:
    lease = lease_factory()
    payload, _ = readiness._new_main_hello(lease.admission)
    payload[field] = "forged-main-build"
    body = readiness._canonical_payload(payload)

    with pytest.raises(readiness.SidecarReadinessError) as raised:
        readiness.serve_sidecar_handshake(
            io.BytesIO(len(body).to_bytes(4, "big") + body),
            io.BytesIO(),
            _identity(lease.admission),
            timeout=1,
        )

    assert raised.value.reason is readiness.SidecarReadinessReason.IDENTITY_MISMATCH
    lease.close()


def test_derived_direction_keys_interoperate_only_in_their_assigned_direction(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = lease_factory()
    process, _, thread, errors, results = _connected_process(lease, _identity(lease.admission))
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)
    session = readiness.spawn_ready_sidecar(lease, timeout=1)
    main_history = session.new_history_session()

    process.close_stdin()
    assert process.wait(1) == 0
    process.close_readers()
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert errors == []
    side_history = results[0].new_history_session()
    query = SourceHistoryQueryV1(
        contract_version="seektalent.source-port.query.request/v1",
        run_id="run-1",
        operation_id="operation-1",
        source="liepin",
        operation_kind="search",
        idempotency_key="key-1",
        request_hash="a" * 64,
        attempt_no=1,
        authorization_selector={"kind": "exact", "ordinal": 1},
        accepted_generation_hint=2,
        searched_first_generation=1,
        searched_last_generation=3,
        expected_source_operation_ledger_revision=4,
        expected_reconciliation_revision=0,
    )
    frame = main_history.encode_query(message_id="message-1", correlation_id=None, payload=query)

    assert side_history.feed(frame)[0].payload == query
    with pytest.raises(HistoryFrameError) as raised:
        main_history.feed(frame)
    assert raised.value.reason_code == HistoryFrameReason.BAD_AUTH_TAG.value


def test_directional_keys_use_hkdf_sha256() -> None:
    secret = b"s" * 32
    session_id = "a" * 32
    transcript = (b"main-hello", b"sidecar-hello", b"main-ready", b"sidecar-ready")
    main_to_sidecar, sidecar_to_main = readiness._derive_direction_keys(secret, session_id, transcript)
    transcript_digest = sha256(b"".join(readiness._length_prefixed(frame) for frame in transcript)).digest()

    def expected(direction: bytes) -> bytes:
        info = (
            readiness._HANDSHAKE_KEY_DOMAIN
            + readiness._length_prefixed(session_id.encode("ascii"))
            + readiness._length_prefixed(direction)
        )
        return HKDF(algorithm=SHA256(), length=32, salt=transcript_digest, info=info).derive(secret)

    assert main_to_sidecar == expected(readiness._MAIN_TO_SIDECAR)
    assert sidecar_to_main == expected(readiness._SIDECAR_TO_MAIN)


def test_main_ready_session_factory_has_no_production_caller() -> None:
    callers = []
    for path in (Path(__file__).resolve().parents[1] / "src").rglob("*.py"):
        if path.name == "sidecar_readiness.py":
            continue
        source = path.read_text(encoding="utf-8")
        if "spawn_ready_sidecar(" in source:
            callers.append(path.relative_to(Path(__file__).resolve().parents[1]).as_posix())

    assert callers == []
