from __future__ import annotations

import copy
import ctypes
import gc
import io
import json
import os
import pickle
import queue
import socket
import subprocess
import threading
import time
import weakref
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Callable, cast

import pytest
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

import seektalent.installed_release as installed_release
import seektalent.installed_slot as installed_slot
import seektalent.sidecar_child_session as child_session_module
import seektalent.sidecar_handshake_protocol as handshake
import seektalent.sidecar_readiness as readiness
from seektalent.installed_slot import (
    ActiveSlotPointerV1,
    InstalledSidecarLaunchLease,
    acquire_installed_sidecar_launch_lease,
    canonical_active_slot_pointer_bytes,
)
from seektalent.owned_sidecar_process import (
    OwnedSidecarProcess,
    SidecarSpawnCleanupError,
    maintain_abandoned_sidecar_spawns,
)
from seektalent.source_port.authenticated_history_frames import (
    HistoryFrameError,
    HistoryFrameReason,
    PostHandshakeHistorySession,
)
from seektalent.source_port.history_contract import SourceHistoryNotFound, SourceHistoryQueryV1
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
    *,
    after_sidecar_ready: Callable[[_FakeChild], None] | None = None,
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
            result = readiness.serve_sidecar_handshake(reader, writer, identity, timeout=1)
            if after_sidecar_ready is not None:
                after_sidecar_ready(child)
            results.append(result)
        except (OSError, ValueError, readiness.SidecarReadinessError) as exc:
            errors.append(exc)
        finally:
            if not results:
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
        except EOFError:
            return
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

    assert session.pid == process.pid
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
        _ = fake.pid

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
            lambda reader, writer: (
                _read_frame(reader),
                getattr(writer, "write")((8).to_bytes(4, "big") + b"{"),
                getattr(writer, "flush")(),
                time.sleep(0.2),
            ),
            readiness.SidecarReadinessReason.TRUNCATED_FRAME,
            id="slow-partial-frame-timeout",
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


def test_child_exit_immediately_after_sidecar_ready_never_mints_ready_authority(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = lease_factory()
    identity = _identity(lease.admission)
    child_holder: dict[str, _FakeChild] = {}

    def sidecar_ready_then_exit(reader: object, writer: object) -> None:
        main_hello_raw = _read_frame(reader)
        main_hello = json.loads(main_hello_raw)
        secret = handshake._decode_secret(main_hello["session_secret"])
        sidecar_hello = handshake._sidecar_hello_payload(
            identity,
            main_hello["session_id"],
            main_hello["nonce"],
            secret,
            (main_hello_raw,),
        )
        sidecar_hello_raw = handshake._canonical_payload(sidecar_hello)
        _write_frame(writer, sidecar_hello_raw)
        main_ready_raw = _read_frame(reader)
        sidecar_ready = handshake._ready_payload(
            "sidecar_ready",
            main_hello["session_id"],
            secret,
            b"sidecar_ready",
            (main_hello_raw, sidecar_hello_raw, main_ready_raw),
        )
        child_holder["child"].returncode = 70
        _write_frame(writer, handshake._canonical_payload(sidecar_ready))

    process, child, thread = _connected_scripted_process(lease, sidecar_ready_then_exit)
    child_holder["child"] = child
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)

    with pytest.raises(readiness.SidecarReadinessError) as raised:
        readiness.spawn_ready_sidecar(lease, timeout=1)

    assert raised.value.reason is readiness.SidecarReadinessReason.CHILD_EXIT
    assert child.kill_calls == 0
    thread.join(timeout=1)
    assert not thread.is_alive()
    lease_factory().close()


def test_sidecar_ready_stdout_eof_while_child_remains_alive_never_mints_ready_authority(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = lease_factory()
    identity = _identity(lease.admission)
    release_child = threading.Event()

    def sidecar_ready_then_half_close_stdout(reader: object, writer: object) -> None:
        main_hello_raw = _read_frame(reader)
        main_hello = json.loads(main_hello_raw)
        secret = handshake._decode_secret(main_hello["session_secret"])
        sidecar_hello = handshake._sidecar_hello_payload(
            identity,
            main_hello["session_id"],
            main_hello["nonce"],
            secret,
            (main_hello_raw,),
        )
        sidecar_hello_raw = handshake._canonical_payload(sidecar_hello)
        _write_frame(writer, sidecar_hello_raw)
        main_ready_raw = _read_frame(reader)
        sidecar_ready = handshake._ready_payload(
            "sidecar_ready",
            main_hello["session_id"],
            secret,
            b"sidecar_ready",
            (main_hello_raw, sidecar_hello_raw, main_ready_raw),
        )
        _write_frame(writer, handshake._canonical_payload(sidecar_ready))
        peer_socket = getattr(writer, "_sock")
        peer_socket.shutdown(socket.SHUT_WR)
        release_child.wait(timeout=1)

    process, child, thread = _connected_scripted_process(lease, sidecar_ready_then_half_close_stdout)
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)

    with pytest.raises(readiness.SidecarReadinessError) as raised:
        readiness.spawn_ready_sidecar(lease, timeout=1)

    assert raised.value.reason is readiness.SidecarReadinessReason.EOF
    assert child.returncode is not None
    release_child.set()
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
        (b'{"message_type":1.5}', readiness.SidecarReadinessReason.ILLEGAL_NUMBER),
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


@pytest.mark.parametrize("tail_kind", ["partial_header", "partial_body", "history", "duplicate_ready"])
def test_sidecar_rejects_every_main_ready_pipeline_tail_before_sidecar_ready(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    tail_kind: str,
) -> None:
    lease = lease_factory()
    identity = _identity(lease.admission)
    main_socket, sidecar_socket = socket.socketpair()
    main_reader = main_socket.makefile("rb", buffering=0)
    main_writer = main_socket.makefile("wb", buffering=0)
    main_socket.close()
    errors: list[BaseException] = []
    results: list[readiness.SidecarHandshakeResult] = []

    def serve() -> None:
        reader = sidecar_socket.makefile("rb", buffering=0)
        writer = sidecar_socket.makefile("wb", buffering=0)
        sidecar_socket.close()
        try:
            results.append(readiness.serve_sidecar_handshake(reader, writer, identity, timeout=1))
        except readiness.SidecarReadinessError as error:
            errors.append(error)

    thread = threading.Thread(target=serve)
    thread.start()
    main_hello, secret = handshake._new_main_hello(
        lease.admission.product_build_id,
        lease.admission.main_application_build_id,
    )
    main_hello_raw = handshake._canonical_payload(main_hello)
    _write_frame(main_writer, main_hello_raw)
    sidecar_hello_raw = _read_frame(main_reader)
    main_ready = handshake._ready_payload(
        "main_ready",
        main_hello["session_id"],
        secret,
        b"main_ready",
        (main_hello_raw, sidecar_hello_raw),
    )
    main_ready_raw = handshake._canonical_payload(main_ready)
    expected_sidecar_ready_raw = handshake._canonical_payload(
        handshake._ready_payload(
            "sidecar_ready",
            main_hello["session_id"],
            secret,
            b"sidecar_ready",
            (main_hello_raw, sidecar_hello_raw, main_ready_raw),
        )
    )
    main_to_sidecar, sidecar_to_main = handshake._derive_direction_keys(
        secret,
        main_hello["session_id"],
        (main_hello_raw, sidecar_hello_raw, main_ready_raw, expected_sidecar_ready_raw),
    )
    history = PostHandshakeHistorySession.for_main(
        session_id=main_hello["session_id"],
        protocol_minor=identity.protocol_max_minor,
        main_to_sidecar_key=main_to_sidecar,
        sidecar_to_main_key=sidecar_to_main,
    )
    history_frame = history.encode_query(
        message_id="early-query",
        correlation_id=None,
        payload=_history_query(),
    )
    tail = {
        "partial_header": history_frame[:2],
        "partial_body": history_frame[:7],
        "history": history_frame,
        "duplicate_ready": len(main_ready_raw).to_bytes(4, "big") + main_ready_raw,
    }[tail_kind]
    main_writer.write(len(main_ready_raw).to_bytes(4, "big") + main_ready_raw + tail)
    main_writer.flush()
    main_writer.close()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert results == []
    assert len(errors) == 1
    assert errors[0].reason is readiness.SidecarReadinessReason.EXTRA_FRAME
    assert main_reader.read() == b""
    main_reader.close()
    lease.close()


def test_pre_ready_boundary_prioritizes_buffered_bytes_over_an_eof_sentinel() -> None:
    transport = handshake._ProtocolTransport(io.BytesIO(), io.BytesIO())
    try:
        transport._buffer.extend(b"early-history-byte")
        transport._set_eof()
        transport._items.put_nowait(None)
        with pytest.raises(readiness.SidecarReadinessError) as raised:
            transport.require_clean_pre_ready_boundary(time.monotonic() + 1)
        assert raised.value.reason is readiness.SidecarReadinessReason.EXTRA_FRAME
    finally:
        transport.close()


def test_pre_ready_boundary_keeps_a_clean_eof_distinct_from_extra_bytes() -> None:
    transport = handshake._ProtocolTransport(io.BytesIO(), io.BytesIO())
    try:
        transport._set_eof()
        transport._items.put_nowait(None)
        with pytest.raises(readiness.SidecarReadinessError) as raised:
            transport.require_clean_pre_ready_boundary(time.monotonic() + 1)
        assert raised.value.reason is readiness.SidecarReadinessReason.EOF
    finally:
        transport.close()


def test_main_handshake_failure_retains_a_transport_that_cannot_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Process:
        def __init__(self) -> None:
            self.errors: list[readiness.SidecarReadinessError] = []

        def _cleanup_after_handshake_failure(
            self,
            error: readiness.SidecarReadinessError,
        ) -> None:
            self.errors.append(error)

    class _Transport:
        def close(self) -> bool:
            return False

    process = _Process()
    transport = _Transport()
    retained: list[object] = []
    monkeypatch.setattr(readiness, "_retain_unclosed_transport", retained.append)
    error = readiness.SidecarReadinessError(readiness.SidecarReadinessReason.BAD_PROOF)

    readiness._cleanup_failed_readiness(
        cast(OwnedSidecarProcess, process),
        cast(handshake._ProtocolTransport, transport),
        error,
    )

    assert process.errors == [error]
    assert retained == [transport]


def test_child_handshake_failure_retains_a_transport_that_cannot_close(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Transport:
        def read_handshake(self, *_: object) -> bytes:
            raise readiness.SidecarReadinessError(readiness.SidecarReadinessReason.BAD_PROOF)

        def close(self) -> bool:
            return False

    transport = _Transport()
    retained: list[object] = []
    monkeypatch.setattr(child_session_module, "_ProtocolTransport", lambda *_: transport)
    monkeypatch.setattr(child_session_module, "_retain_unclosed_transport", retained.append)
    lease = lease_factory()

    with pytest.raises(readiness.SidecarReadinessError) as raised:
        readiness.serve_sidecar_handshake(io.BytesIO(), io.BytesIO(), _identity(lease.admission), timeout=1)

    assert raised.value.reason is readiness.SidecarReadinessReason.BAD_PROOF
    assert retained == [transport]
    lease.close()


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        pytest.param(
            lambda payload: payload.__setitem__("nonce", "wrong-nonce"),
            readiness.SidecarReadinessReason.NONCE_MISMATCH,
            id="nonce-mismatch",
        ),
        pytest.param(
            lambda payload: payload.__setitem__("session_id", "wrong-session"),
            readiness.SidecarReadinessReason.SESSION_MISMATCH,
            id="session-mismatch",
        ),
        pytest.param(
            lambda payload: payload.__setitem__("proof", "0" * 64),
            readiness.SidecarReadinessReason.BAD_PROOF,
            id="bad-proof",
        ),
        pytest.param(
            lambda payload: payload.__setitem__("unexpected", "field"),
            readiness.SidecarReadinessReason.UNKNOWN_FIELD,
            id="unknown-field",
        ),
    ],
)
def test_identity_proof_and_nonce_failures_reap_without_secret_disclosure(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
    mutation: Callable[[dict[str, object]], None],
    reason: readiness.SidecarReadinessReason,
) -> None:
    lease = lease_factory()
    identity = _identity(lease.admission)

    def send_mutated_sidecar_hello(reader: object, writer: object) -> None:
        main_hello_raw = _read_frame(reader)
        main_hello = json.loads(main_hello_raw)
        secret = handshake._decode_secret(main_hello["session_secret"])
        payload = handshake._sidecar_hello_payload(
            identity,
            main_hello["session_id"],
            main_hello["nonce"],
            secret,
            (main_hello_raw,),
        )
        mutation(payload)
        _write_frame(writer, handshake._canonical_payload(payload))

    process, child, thread = _connected_scripted_process(lease, send_mutated_sidecar_hello)
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)

    with pytest.raises(readiness.SidecarReadinessError) as raised:
        readiness.spawn_ready_sidecar(lease, timeout=1)

    assert raised.value.reason is reason
    assert "session_secret" not in str(raised.value)
    assert child.kill_calls == 1
    thread.join(timeout=1)
    assert not thread.is_alive()
    lease_factory().close()


@pytest.mark.parametrize(
    ("writer_factory", "reason"),
    [
        pytest.param(
            lambda: _BlockingWriter(),
            readiness.SidecarReadinessReason.WRITE_TIMEOUT,
            id="write-timeout",
        ),
        pytest.param(
            lambda: _BrokenWriter(),
            readiness.SidecarReadinessReason.PIPE_IO_FAILURE,
            id="pipe-io-failure",
        ),
    ],
)
def test_write_failures_are_bounded_and_reap_the_child(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
    writer_factory: Callable[[], object],
    reason: readiness.SidecarReadinessReason,
) -> None:
    lease = lease_factory()
    process, child, thread = _connected_scripted_process(lease, lambda reader, _: _read_frame(reader))
    process.protocol_writer = writer_factory()  # type: ignore[assignment]
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)

    with pytest.raises(readiness.SidecarReadinessError) as raised:
        readiness.spawn_ready_sidecar(lease, timeout=0.05)

    assert raised.value.reason is reason
    assert child.kill_calls == 1
    thread.join(timeout=1)
    assert not thread.is_alive()
    lease_factory().close()


class _BlockingWriter:
    def __init__(self) -> None:
        self.closed = False
        self._released = threading.Event()

    def write(self, _: bytes) -> int:
        self._released.wait(timeout=1)
        return 0

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True
        self._released.set()


class _BrokenWriter:
    closed = False

    def write(self, _: bytes) -> int:
        raise OSError("injected pipe failure")

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("field", ["product_build_id", "main_application_build_id"])
def test_sidecar_requires_the_exact_admitted_main_identity(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    field: str,
) -> None:
    lease = lease_factory()
    payload, _ = readiness._new_main_hello(lease.admission)
    payload[field] = "forged-main-build"
    body = handshake._canonical_payload(payload)

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
    main_to_sidecar, sidecar_to_main = handshake._derive_direction_keys(secret, session_id, transcript)
    transcript_digest = sha256(b"".join(handshake._length_prefixed(frame) for frame in transcript)).digest()

    def expected(direction: bytes) -> bytes:
        info = (
            handshake._HANDSHAKE_KEY_DOMAIN
            + handshake._length_prefixed(session_id.encode("ascii"))
            + handshake._length_prefixed(direction)
        )
        return HKDF(algorithm=SHA256(), length=32, salt=transcript_digest, info=info).derive(secret)

    assert main_to_sidecar == expected(handshake._MAIN_TO_SIDECAR)
    assert sidecar_to_main == expected(handshake._SIDECAR_TO_MAIN)


def test_handshake_returns_the_child_transport_without_waiting_for_parent_eof(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = lease_factory()
    process, _, thread, errors, results = _connected_process(lease, _identity(lease.admission))
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)

    session = readiness.spawn_ready_sidecar(lease, timeout=1)
    try:
        thread.join(timeout=0.1)
        assert not thread.is_alive()
        assert errors == []
        assert results
    finally:
        session.close(1)
        thread.join(timeout=1)


def test_ready_session_cannot_reset_its_history_replay_state(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = lease_factory()
    process, _, thread, _, _ = _connected_process(lease, _identity(lease.admission))
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)
    session = readiness.spawn_ready_sidecar(lease, timeout=1)
    try:
        assert session.new_history_session() is session.new_history_session()
    finally:
        session.close(1)
        thread.join(timeout=1)


def test_ready_transport_keeps_one_real_pipe_for_bidirectional_authenticated_history_frames(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = lease_factory()
    process, _, thread, errors, results = _connected_process(lease, _identity(lease.admission))
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)
    session = readiness.spawn_ready_sidecar(lease, timeout=1)
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert errors == []
    child_session = results[0]
    query = _history_query()
    try:
        main_history = session.new_history_session()
        query_frame = main_history.encode_query(
            message_id="query-message-1",
            correlation_id="correlation-1",
            payload=query,
        )
        session.send_history_frame(query_frame, timeout=1)
        assert child_session.receive_history(timeout=1)[0].payload == query

        result = SourceHistoryNotFound(
            **query.model_dump(exclude={"contract_version"}),
            contract_version="seektalent.source-port.query.result/v1",
            outcome="not_found",
            oldest_retained_generation=1,
            newest_known_generation=3,
            history_complete=True,
            history_truncated=False,
        )
        result_frame = child_session.new_history_session().encode_result(
            message_id="result-message-1",
            reply_to="query-message-1",
            payload=result,
        )
        child_session.send_history_frame(result_frame[:3], timeout=1)
        assert session.receive_history(timeout=1) == ()
        child_session.send_history_frame(result_frame[3:], timeout=1)
        assert session.receive_history(timeout=1)[0].payload == result
    finally:
        child_session.close()
        session.close(1)


def test_replayed_history_frame_is_rejected_through_every_public_transport_factory(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = lease_factory()
    process, _, thread, errors, results = _connected_process(lease, _identity(lease.admission))
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)
    session = readiness.spawn_ready_sidecar(lease, timeout=1)
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert errors == []
    child_session = results[0]
    try:
        query = _history_query()
        frame = session.new_history_session().encode_query(
            message_id="query-message-1",
            correlation_id=None,
            payload=query,
        )
        session.send_history_frame(frame, timeout=1)
        assert child_session.receive_history(timeout=1)[0].payload == query
        assert child_session.new_history_session() is child_session.new_history_session()
        session.send_history_frame(frame, timeout=1)
        with pytest.raises(HistoryFrameError) as raised:
            child_session.receive_history(timeout=1)
        assert raised.value.reason_code == HistoryFrameReason.SEQUENCE_MISMATCH.value
    finally:
        child_session.close()
        session.close(1)


def test_main_history_eof_preserves_partial_frame_reason_and_clean_eof_closes_parser(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = lease_factory()
    process, _, thread, errors, results = _connected_process(lease, _identity(lease.admission))
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)
    session = readiness.spawn_ready_sidecar(lease, timeout=1)
    thread.join(timeout=1)
    assert errors == []
    child_result = results.pop()
    query = _history_query()
    query_frame = session.new_history_session().encode_query(
        message_id="query-eof",
        correlation_id=None,
        payload=query,
    )
    session.send_history_frame(query_frame, timeout=1)
    assert child_result.receive_history(timeout=1)[0].payload == query
    result_frame = child_result.new_history_session().encode_result(
        message_id="result-eof",
        reply_to="query-eof",
        payload=SourceHistoryNotFound(
            **_history_query().model_dump(exclude={"contract_version"}),
            contract_version="seektalent.source-port.query.result/v1",
            outcome="not_found",
            oldest_retained_generation=1,
            newest_known_generation=3,
            history_complete=True,
            history_truncated=False,
        ),
    )
    child_result.send_history_frame(result_frame[:3], timeout=1)
    assert session.receive_history(timeout=1) == ()
    child_result.close()
    with pytest.raises(HistoryFrameError) as partial:
        session.receive_history(timeout=1)
    assert partial.value.reason_code == HistoryFrameReason.TRUNCATED_FRAME.value
    session.close(1)

    lease = lease_factory()
    process, _, thread, errors, results = _connected_process(lease, _identity(lease.admission))
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)
    clean_session = readiness.spawn_ready_sidecar(lease, timeout=1)
    thread.join(timeout=1)
    assert errors == []
    results.pop().close()
    with pytest.raises(readiness.SidecarReadinessError) as clean:
        clean_session.receive_history(timeout=1)
    assert clean.value.reason is readiness.SidecarReadinessReason.EOF
    assert clean_session.new_history_session().closed is True
    clean_session.close(1)


def test_child_history_eof_preserves_partial_frame_reason_and_clean_eof_closes_parser(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = lease_factory()
    process, _, thread, errors, results = _connected_process(lease, _identity(lease.admission))
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)
    session = readiness.spawn_ready_sidecar(lease, timeout=1)
    thread.join(timeout=1)
    assert errors == []
    child_result = results.pop()
    frame = session.new_history_session().encode_query(
        message_id="child-query-eof",
        correlation_id=None,
        payload=_history_query(),
    )
    session.send_history_frame(frame[:3], timeout=1)
    assert child_result.receive_history(timeout=1) == ()
    process.close_stdin()
    process.close_readers()
    with pytest.raises(HistoryFrameError) as partial:
        child_result.receive_history(timeout=1)
    assert partial.value.reason_code == HistoryFrameReason.TRUNCATED_FRAME.value
    child_result.close()
    session.close(1)

    lease = lease_factory()
    process, _, thread, errors, results = _connected_process(lease, _identity(lease.admission))
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)
    clean_session = readiness.spawn_ready_sidecar(lease, timeout=1)
    thread.join(timeout=1)
    assert errors == []
    clean_child_result = results.pop()
    process.close_stdin()
    process.close_readers()
    with pytest.raises(readiness.SidecarReadinessError) as clean:
        clean_child_result.receive_history(timeout=1)
    assert clean.value.reason is readiness.SidecarReadinessReason.EOF
    assert clean_child_result.new_history_session().closed is True
    clean_child_result.close()
    clean_session.close(1)


def _history_query() -> SourceHistoryQueryV1:
    return SourceHistoryQueryV1(
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


def test_collecting_a_ready_session_reaps_its_exact_child_and_releases_the_lease(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = lease_factory()
    process, child, thread, _, _ = _connected_process(lease, _identity(lease.admission))
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)
    session = readiness.spawn_ready_sidecar(lease, timeout=1)
    reference = weakref.ref(session)

    del session
    gc.collect()

    assert reference() is None
    assert child.kill_calls == 1
    thread.join(timeout=1)
    assert not thread.is_alive()
    lease_factory().close()


def test_collecting_a_ready_session_transfers_unreaped_cleanup_to_maintenance(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = lease_factory()
    process, child, thread, _, results = _connected_process(lease, _identity(lease.admission))
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)
    attempts = 0

    def kill_without_exit() -> None:
        child.kill_calls += 1

    def wait_for_maintenance(timeout: float | None = None) -> int:
        nonlocal attempts
        del timeout
        attempts += 1
        if attempts == 1:
            raise subprocess.TimeoutExpired("sidecar", 1)
        child.returncode = 0
        return 0

    monkeypatch.setattr(child, "kill", kill_without_exit)
    monkeypatch.setattr(child, "wait", wait_for_maintenance)
    session = readiness.spawn_ready_sidecar(lease, timeout=1)
    reference = weakref.ref(session)

    del session
    gc.collect()

    assert reference() is None
    maintenance = maintain_abandoned_sidecar_spawns()
    assert maintenance.reaped >= 1
    assert child.kill_calls >= 2
    results[0].close()
    thread.join(timeout=1)
    lease_factory().close()


def test_child_result_close_cancels_its_reader_while_the_peer_stays_open(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = lease_factory()
    process, _, thread, errors, results = _connected_process(lease, _identity(lease.admission))
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)
    session = readiness.spawn_ready_sidecar(lease, timeout=1)
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert errors == []
    child_result = results.pop()
    transport = child_session_module._result_state(child_result).transport

    child_result.close()

    assert transport.reader_stopped is True
    session.close(1)


def test_collecting_child_result_cancels_its_reader_while_the_peer_stays_open(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = lease_factory()
    process, _, thread, errors, results = _connected_process(lease, _identity(lease.admission))
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)
    session = readiness.spawn_ready_sidecar(lease, timeout=1)
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert errors == []
    child_result = results.pop()
    transport = child_session_module._result_state(child_result).transport
    reference = weakref.ref(child_result)

    del child_result
    gc.collect()

    assert reference() is None
    assert transport.reader_stopped is True
    session.close(1)


def test_collecting_child_result_retains_an_unclosed_transport_for_maintenance(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = lease_factory()
    process, _, thread, errors, results = _connected_process(lease, _identity(lease.admission))
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)
    session = readiness.spawn_ready_sidecar(lease, timeout=1)
    thread.join(timeout=1)
    assert errors == []
    child_result = results.pop()
    transport = child_session_module._result_state(child_result).transport
    original_close = transport.close
    attempts = 0

    def fail_once() -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return False
        return original_close()

    monkeypatch.setattr(transport, "close", fail_once)
    reference = weakref.ref(child_result)
    del child_result
    gc.collect()

    assert reference() is None
    assert transport in handshake._RETAINED_UNCLOSED_TRANSPORTS
    handshake._maintain_retained_unclosed_transports()
    assert transport not in handshake._RETAINED_UNCLOSED_TRANSPORTS
    session.close(1)


def test_child_result_close_unblocks_a_concurrent_parent_eof_wait(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = lease_factory()
    process, _, thread, errors, results = _connected_process(lease, _identity(lease.admission))
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)
    session = readiness.spawn_ready_sidecar(lease, timeout=1)
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert errors == []
    child_result = results.pop()
    transport = child_session_module._result_state(child_result).transport
    original_read = transport.read_history_chunk
    entered_wait = threading.Event()
    wait_finished = threading.Event()

    def wait_for_eof() -> None:
        child_result.wait_for_parent_eof()
        wait_finished.set()

    def signal_then_read(*args: object, **kwargs: object) -> bytes:
        entered_wait.set()
        return original_read(*args, **kwargs)

    monkeypatch.setattr(transport, "read_history_chunk", signal_then_read)
    waiter = threading.Thread(target=wait_for_eof)
    waiter.start()
    assert entered_wait.wait(timeout=1)
    child_result.close()
    waiter.join(timeout=1)

    assert wait_finished.is_set()
    assert not waiter.is_alive()
    session.close(1)


def test_windows_reader_cancel_uses_thread_terminate_and_never_claims_failed_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Function:
        def __init__(self, result: int) -> None:
            self.result = result
            self.calls: list[tuple[object, ...]] = []

        def __call__(self, *args: object) -> int:
            self.calls.append(args)
            return self.result

    class _Kernel32:
        def __init__(self, cancel_result: int) -> None:
            self.OpenThread = _Function(123)
            self.CancelSynchronousIo = _Function(cancel_result)
            self.CloseHandle = _Function(1)

    kernel32 = _Kernel32(cancel_result=1)
    monkeypatch.setattr(handshake.os, "name", "nt")
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32, raising=False)

    assert handshake._cancel_windows_synchronous_read(456) is True
    assert kernel32.OpenThread.calls == [(handshake._THREAD_TERMINATE, False, 456)]
    assert handshake._THREAD_TERMINATE == 0x0001
    assert kernel32.CancelSynchronousIo.calls
    assert kernel32.CloseHandle.calls

    failing_kernel32 = _Kernel32(cancel_result=0)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: failing_kernel32, raising=False)

    assert handshake._cancel_windows_synchronous_read(456) is False


@pytest.mark.skipif(os.name != "posix", reason="POSIX wakeup descriptor rollback")
def test_protocol_transport_start_failure_rolls_back_wakeup_fds_and_reader_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader_descriptor, peer_descriptor = os.pipe()
    reader = os.fdopen(reader_descriptor, "rb", buffering=0)
    baseline = len(os.listdir("/dev/fd"))

    class _FailingThread:
        def __init__(self, **_: object) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("injected reader start failure")

    monkeypatch.setattr(handshake.threading, "Thread", _FailingThread)
    try:
        with pytest.raises(RuntimeError, match="reader start failure"):
            handshake._ProtocolTransport(reader, io.BytesIO())
        assert os.get_blocking(reader_descriptor) is True
        assert len(os.listdir("/dev/fd")) == baseline
    finally:
        reader.close()
        os.close(peer_descriptor)


@pytest.mark.skipif(os.name != "posix", reason="POSIX wakeup descriptor rollback")
def test_protocol_transport_wakeup_setup_failure_restores_reader_mode_and_fds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader_descriptor, peer_descriptor = os.pipe()
    reader = os.fdopen(reader_descriptor, "rb", buffering=0)
    baseline = len(os.listdir("/dev/fd"))
    original_set_blocking = os.set_blocking
    calls = 0

    def fail_while_configuring_wakeup(descriptor: int, blocking: bool) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected wakeup nonblocking failure")
        original_set_blocking(descriptor, blocking)

    monkeypatch.setattr(handshake.os, "set_blocking", fail_while_configuring_wakeup)
    try:
        with pytest.raises(OSError, match="wakeup nonblocking failure"):
            handshake._ProtocolTransport(reader, io.BytesIO())
        assert os.get_blocking(reader_descriptor) is True
        assert len(os.listdir("/dev/fd")) == baseline
    finally:
        reader.close()
        os.close(peer_descriptor)


@pytest.mark.skipif(os.name != "posix", reason="POSIX wakeup descriptor rollback")
def test_protocol_transport_rollback_retries_a_failed_wakeup_descriptor_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader_descriptor, peer_descriptor = os.pipe()
    reader = os.fdopen(reader_descriptor, "rb", buffering=0)
    baseline = len(os.listdir("/dev/fd"))
    original_close = os.close
    failed = False

    class _FailingThread:
        def __init__(self, **_: object) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("injected reader start failure")

    def fail_one_wakeup_close(descriptor: int) -> None:
        nonlocal failed
        if descriptor != reader_descriptor and not failed:
            failed = True
            raise OSError("injected wake descriptor close failure")
        original_close(descriptor)

    monkeypatch.setattr(handshake.threading, "Thread", _FailingThread)
    monkeypatch.setattr(handshake.os, "close", fail_one_wakeup_close)
    try:
        with pytest.raises(RuntimeError, match="reader start failure"):
            handshake._ProtocolTransport(reader, io.BytesIO())
        assert failed is True
        assert handshake._RETAINED_CONSTRUCTOR_WAKE_DESCRIPTORS == set()
        assert len(os.listdir("/dev/fd")) == baseline
    finally:
        reader.close()
        original_close(peer_descriptor)


def test_protocol_transport_close_keeps_authority_until_a_writer_close_retries() -> None:
    class _Writer(io.BytesIO):
        def __init__(self) -> None:
            super().__init__()
            self.close_attempts = 0

        def close(self) -> None:
            self.close_attempts += 1
            if self.close_attempts == 1:
                raise OSError("injected writer close failure")
            super().close()

    writer = _Writer()
    transport = handshake._ProtocolTransport(io.BytesIO(), writer)

    assert transport.close() is False
    assert transport.reader_stopped is True
    assert writer.closed is False
    assert transport.close() is True
    assert writer.closed is True


def test_protocol_transport_close_attempts_writer_when_reader_close_fails() -> None:
    class _Reader(io.BytesIO):
        def __init__(self) -> None:
            super().__init__()
            self.close_attempts = 0

        def close(self) -> None:
            self.close_attempts += 1
            if self.close_attempts <= 2:
                raise OSError("injected reader close failure")
            super().close()

    reader = _Reader()
    writer = io.BytesIO()
    transport = handshake._ProtocolTransport(reader, writer)

    assert transport.close() is False
    assert reader.closed is False
    assert writer.closed is True
    assert transport.close() is True
    assert reader.closed is True


@pytest.mark.skipif(os.name != "posix", reason="POSIX wakeup descriptor disposition")
def test_protocol_transport_close_retains_a_failed_wakeup_descriptor_for_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader_descriptor, peer_descriptor = os.pipe()
    reader = os.fdopen(reader_descriptor, "rb", buffering=0)
    transport = handshake._ProtocolTransport(reader, io.BytesIO())
    wake_reader = transport._wake_reader_descriptor
    assert wake_reader is not None
    original_close = os.close
    failed = False

    def fail_once(value: int) -> None:
        nonlocal failed
        if value == wake_reader and not failed:
            failed = True
            raise OSError("injected wake descriptor close failure")
        original_close(value)

    monkeypatch.setattr(handshake.os, "close", fail_once)
    try:
        assert transport.close() is False
        assert transport.reader_stopped is True
        assert transport._wake_reader_descriptor == wake_reader
        assert transport.close() is True
        assert transport._wake_reader_descriptor is None
    finally:
        original_close(peer_descriptor)


def test_windows_pre_ready_boundary_cancels_an_idle_blocking_read_without_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _CancellableReader:
        def __init__(self) -> None:
            self.cancelled = threading.Event()
            self.entered_read = threading.Event()
            self.closed = False

        def read(self, _: int) -> bytes:
            self.entered_read.set()
            self.cancelled.wait(timeout=1)
            if self.closed:
                return b""
            self.cancelled.clear()
            raise OSError(995, "operation aborted")

        def close(self) -> None:
            self.closed = True
            self.cancelled.set()

    reader = _CancellableReader()
    with monkeypatch.context() as context:
        context.setattr(handshake.os, "name", "nt")
        transport = handshake._ProtocolTransport(reader, io.BytesIO())
        context.setattr(
            handshake,
            "_cancel_windows_synchronous_read",
            lambda _: reader.cancelled.set() or True,
        )
        assert reader.entered_read.wait(timeout=1)
        transport.require_clean_pre_ready_boundary(time.monotonic() + 1)
        assert transport.close() is True


def test_windows_boundary_abort_after_ack_does_not_poison_the_next_blocking_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _RepeatedCancellableReader:
        def __init__(self) -> None:
            self.cancelled = threading.Event()
            self.second_read = threading.Event()
            self.third_read = threading.Event()
            self.closed = False
            self.read_count = 0

        def read(self, _: int) -> bytes:
            self.read_count += 1
            if self.read_count == 2:
                self.second_read.set()
            if self.read_count == 3:
                self.third_read.set()
            self.cancelled.wait(timeout=1)
            self.cancelled.clear()
            if self.closed:
                return b""
            raise OSError(995, "operation aborted")

        def close(self) -> None:
            self.closed = True
            self.cancelled.set()

    reader = _RepeatedCancellableReader()
    with monkeypatch.context() as context:
        context.setattr(handshake.os, "name", "nt")
        transport = handshake._ProtocolTransport(reader, io.BytesIO())
        context.setattr(
            handshake,
            "_cancel_windows_synchronous_read",
            lambda _: reader.cancelled.set() or True,
        )
        response: queue.Queue[tuple[bool, BaseException | None]] = queue.Queue(maxsize=1)
        transport._windows_boundary_cancel_active.set()
        transport._boundary_requests.put(response)
        reader.cancelled.set()
        assert response.get(timeout=1) == (False, None)
        assert reader.second_read.wait(timeout=1)

        # The request has already been acknowledged, but the observer still
        # owns the cancellation window while it consumes that response.
        reader.cancelled.set()
        assert reader.third_read.wait(timeout=1)
        assert transport.reader_stopped is False

        transport._windows_boundary_cancel_active.clear()
        assert transport.close() is True


def test_windows_pre_ready_boundary_observes_a_real_reader_error_before_its_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailingReader:
        def __init__(self) -> None:
            self.release_error = threading.Event()
            self.entered_read = threading.Event()
            self.closed = False

        def read(self, _: int) -> bytes:
            self.entered_read.set()
            self.release_error.wait(timeout=1)
            if self.closed:
                return b""
            raise OSError("injected reader failure")

        def close(self) -> None:
            self.closed = True
            self.release_error.set()

    reader = _FailingReader()
    with monkeypatch.context() as context:
        context.setattr(handshake.os, "name", "nt")
        transport = handshake._ProtocolTransport(reader, io.BytesIO())
        context.setattr(handshake, "_cancel_windows_synchronous_read", lambda _: False)
        request_posted = threading.Event()
        original_put = transport._boundary_requests.put

        def record_request(
            item: queue.Queue[tuple[bool, BaseException | None]],
            *args: object,
            **kwargs: object,
        ) -> None:
            request_posted.set()
            original_put(item, *args, **kwargs)

        transport._boundary_requests.put = record_request  # type: ignore[method-assign]
        assert reader.entered_read.wait(timeout=1)
        observed: list[readiness.SidecarReadinessError] = []

        def observe() -> None:
            try:
                transport.require_clean_pre_ready_boundary(time.monotonic() + 1)
            except readiness.SidecarReadinessError as error:
                observed.append(error)

        boundary = threading.Thread(target=observe)
        boundary.start()
        assert request_posted.wait(timeout=1)
        reader.release_error.set()
        boundary.join(timeout=1)

        assert not boundary.is_alive()
        assert len(observed) == 1
        assert observed[0].reason is readiness.SidecarReadinessReason.PIPE_IO_FAILURE
        assert transport.close() is True


def test_windows_pre_ready_boundary_waits_for_a_paused_blocking_reader_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BlockingReader:
        def __init__(self) -> None:
            self.allow_read = threading.Event()
            self.closed = False
            self._delivered = False

        def read(self, _: int) -> bytes:
            self.allow_read.wait(timeout=1)
            if not self._delivered:
                self._delivered = True
                return b"early-history"
            return b""

        def close(self) -> None:
            self.closed = True
            self.allow_read.set()

    reader = _BlockingReader()
    with monkeypatch.context() as context:
        context.setattr(handshake.os, "name", "nt")
        transport = handshake._ProtocolTransport(reader, io.BytesIO())
        paused_before_enqueue = threading.Event()
        release_enqueue = threading.Event()
        original_enqueue = transport._enqueue

        def pause_before_enqueue(item: bytes | BaseException | None) -> None:
            if item == b"early-history":
                paused_before_enqueue.set()
                release_enqueue.wait(timeout=1)
            original_enqueue(item)

        transport._enqueue = pause_before_enqueue  # type: ignore[method-assign]
        boundary_requested = threading.Event()
        original_put = transport._boundary_requests.put

        def record_request(
            item: queue.Queue[tuple[bool, BaseException | None]],
            *args: object,
            **kwargs: object,
        ) -> None:
            boundary_requested.set()
            original_put(item, *args, **kwargs)

        transport._boundary_requests.put = record_request  # type: ignore[method-assign]
        reader.allow_read.set()
        assert paused_before_enqueue.wait(timeout=1)
        observed: list[BaseException] = []

        def observe() -> None:
            try:
                transport.require_clean_pre_ready_boundary(time.monotonic() + 1)
            except readiness.SidecarReadinessError as error:
                observed.append(error)

        boundary = threading.Thread(target=observe)
        boundary.start()
        assert boundary_requested.wait(timeout=1)
        assert boundary.is_alive()
        release_enqueue.set()
        boundary.join(timeout=1)

        assert not boundary.is_alive()
        assert len(observed) == 1
        assert isinstance(observed[0], readiness.SidecarReadinessError)
        assert observed[0].reason is readiness.SidecarReadinessReason.EXTRA_FRAME
        assert transport.close() is True


def test_ready_close_retains_reaped_child_cleanup_when_real_slot_unlock_fails(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = lease_factory()
    process, _, thread, errors, _ = _connected_process(lease, _identity(lease.admission))
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)
    session = readiness.spawn_ready_sidecar(lease, timeout=1)
    assert errors == []
    lease_state = readiness._ready_state(session).process._lease_state
    assert lease_state is not None
    descriptor = lease_state.slot_lock.descriptor
    assert descriptor is not None
    original_unlock = installed_slot._unlock_native_slot_lock
    attempts = 0

    def fail_once_then_unlock(value: int, platform: str) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("injected slot unlock failure")
        original_unlock(value, platform)

    monkeypatch.setattr(installed_slot, "_unlock_native_slot_lock", fail_once_then_unlock)

    with pytest.raises(readiness.SidecarReadinessError) as raised:
        session.close(1)

    cleanup_error = raised.value.cleanup_error
    assert isinstance(cleanup_error, SidecarSpawnCleanupError)
    assert cleanup_error.direct_child_reaped is True
    assert cleanup_error.lease_released is False
    assert readiness._ready_state(session).cleanup_error is cleanup_error
    assert lease_state.slot_lock.descriptor == descriptor
    assert session.close(1) == -9
    assert lease_state.released is True
    lease_factory().close()


def test_ready_session_close_retains_authority_after_wait_timeout_and_retries(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = lease_factory()
    process, child, thread, _, results = _connected_process(lease, _identity(lease.admission))
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)
    attempts = 0

    def kill_without_exit() -> None:
        child.kill_calls += 1

    def wait_then_reap(timeout: float | None = None) -> int:
        nonlocal attempts
        del timeout
        attempts += 1
        if attempts <= 2:
            raise subprocess.TimeoutExpired("sidecar", 1)
        child.returncode = 0
        return 0

    monkeypatch.setattr(child, "kill", kill_without_exit)
    monkeypatch.setattr(child, "wait", wait_then_reap)
    session = readiness.spawn_ready_sidecar(lease, timeout=1)

    with pytest.raises(readiness.SidecarReadinessError) as raised:
        session.close(1)

    assert raised.value.reason is readiness.SidecarReadinessReason.CHILD_FAILURE
    assert isinstance(raised.value.cleanup_error, SidecarSpawnCleanupError)
    assert session.pid == process.pid
    assert session.close(1) == 0
    with pytest.raises(TypeError):
        _ = session.pid
    results[0].close()
    thread.join(timeout=1)
    lease_factory().close()


def test_stderr_drain_start_failure_reaps_the_child_and_releases_the_lease(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = lease_factory()
    process, child, thread, _, results = _connected_process(lease, _identity(lease.admission))
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)
    monkeypatch.setattr(readiness._BoundedStderrDrain, "start", lambda _: (_ for _ in ()).throw(RuntimeError()))

    with pytest.raises(readiness.SidecarReadinessError) as raised:
        readiness.spawn_ready_sidecar(lease, timeout=1)

    assert raised.value.reason is readiness.SidecarReadinessReason.PIPE_IO_FAILURE
    assert child.kill_calls == 1
    results.clear()
    thread.join(timeout=1)
    assert not thread.is_alive()
    lease_factory().close()


def test_stderr_flood_does_not_block_the_protocol_handshake(
    lease_factory: Callable[[], InstalledSidecarLaunchLease],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = lease_factory()
    process, _, thread, errors, results = _connected_process(lease, _identity(lease.admission))
    process.stderr_reader = io.BytesIO(b"e" * (2 * 64 * 1024))
    monkeypatch.setattr(readiness, "spawn_owned_sidecar", lambda _: process)

    session = readiness.spawn_ready_sidecar(lease, timeout=1)

    thread.join(timeout=1)
    assert not thread.is_alive()
    assert errors == []
    results[0].close()
    session.close(1)
    lease_factory().close()


def test_main_ready_session_factory_has_no_production_caller() -> None:
    callers = []
    for path in (Path(__file__).resolve().parents[1] / "src").rglob("*.py"):
        if path.name == "sidecar_readiness.py":
            continue
        source = path.read_text(encoding="utf-8")
        if "spawn_ready_sidecar(" in source:
            callers.append(path.relative_to(Path(__file__).resolve().parents[1]).as_posix())

    assert callers == []
