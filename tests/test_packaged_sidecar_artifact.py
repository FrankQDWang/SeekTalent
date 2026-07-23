from __future__ import annotations

import json
import os
import platform
import sqlite3
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest

import seektalent.installed_release as installed_release
import seektalent.installed_slot as installed_slot
import seektalent.owned_sidecar_process as owned_process
import seektalent.sidecar_readiness as readiness
from seektalent.installed_filesystem import InstalledReleaseError, InstalledReleaseReason
from seektalent.installed_slot import (
    ActiveSlotPointerV1,
    InstalledSidecarLaunchLease,
    acquire_installed_sidecar_launch_lease,
)
from seektalent.owned_sidecar_process import OwnedSidecarProcess, spawn_owned_sidecar
from seektalent.sidecar_readiness import spawn_ready_sidecar
from seektalent.source_port.history_contract import SourceHistoryMatched, SourceHistoryNotFound
from seektalent.source_port.sidecar_transport import exchange_source_history, exchange_verify_session
from seektalent.source_port.verify_session_contract import VerifySessionRequestV1
from seektalent.release_manifest import parse_release_manifest, release_manifest_digest
from tools.build_packaged_sidecar import (
    TEST_ONLY_SIGNING_SEED,
    TEST_ONLY_VERIFICATION_TIME,
    build_packaged_sidecar,
    build_test_only_manifest_id,
    test_only_trust_policy as _test_only_trust_policy,
)
from tests.support.source_history_sqlite_harness import SourceHistorySQLiteHarness
from tests.test_source_history_sqlite_harness import _accepted, _query


pytestmark = pytest.mark.skipif(
    platform.system() not in {"Darwin", "Windows"},
    reason="the packaged sidecar artifact has native Windows/macOS targets only",
)


def _install_active_artifact(tmp_path: Path) -> Path:
    built_slot = build_packaged_sidecar(tmp_path / "built-slot", TEST_ONLY_SIGNING_SEED)
    root = tmp_path / "installation"
    slot_root = root / "slots" / "A"
    slot_root.parent.mkdir(parents=True)
    built_slot.rename(slot_root)

    manifest = parse_release_manifest(
        (slot_root / installed_release.INSTALLED_MANIFEST_RELATIVE_PATH).read_bytes()
    )
    control = root / "control"
    control.mkdir()
    control.joinpath("installation-id").write_text("packaged-artifact-test", encoding="ascii")
    control.joinpath("active-slot.lock").write_bytes(b"0")
    control.joinpath("slot-A.lock").write_bytes(b"0")
    control.joinpath("slot-B.lock").write_bytes(b"0")
    pointer = ActiveSlotPointerV1.model_construct(
        schema_version="seektalent.active-slot/v1",
        installation_id="packaged-artifact-test",
        physical_slot="A",
        pointer_generation=1,
        product_build_id=manifest.product_build_id,
        release_manifest_sha256=release_manifest_digest(manifest),
        committed_at="2026-07-21T12:00:00Z",
    )
    control.joinpath("active-slot.json").write_bytes(installed_slot.canonical_active_slot_pointer_bytes(pointer))
    return root


def _acquire(root: Path):
    return acquire_installed_sidecar_launch_lease(root, _test_only_trust_policy(), TEST_ONLY_VERIFICATION_TIME)


def _sidecar_files(slot_root: Path) -> tuple[tuple[Path, ...], Path]:
    manifest = parse_release_manifest(
        (slot_root / installed_release.INSTALLED_MANIFEST_RELATIVE_PATH).read_bytes()
    )
    sidecar = next(item for item in manifest.components if item.component_id == "liepin_execution_sidecar")
    root = slot_root / manifest.payload_root / sidecar.root_path
    return (
        tuple(root / item.path for item in sidecar.files),
        root / sidecar.entrypoints[0],
    )


def _spawn_test_history_sidecar(
    lease: InstalledSidecarLaunchLease,
    database: Path,
) -> OwnedSidecarProcess:
    return _spawn_test_source_port_sidecar(
        lease,
        "--test-only-source-history-database",
        str(database),
    )


def _spawn_test_source_port_sidecar(
    lease: InstalledSidecarLaunchLease,
    *arguments: str,
) -> OwnedSidecarProcess:
    lease_state = lease._take_for_spawn()
    resolution = lease_state.admission.resolution
    executable = resolution.executable_path
    process = subprocess.Popen(
        [str(executable), *arguments],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        text=False,
        bufsize=0,
        close_fds=True,
        cwd=str(owned_process._installed_release_working_directory(resolution)),
        env=owned_process._bounded_environment(),
        start_new_session=os.name == "posix",
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    return OwnedSidecarProcess(
        _process=process,
        protocol_writer=process.stdin,
        protocol_reader=process.stdout,
        stderr_reader=process.stderr,
        _process_group_id=process.pid if os.name == "posix" else None,
        _lease_state=lease_state,
    )


def _verify_request() -> VerifySessionRequestV1:
    return VerifySessionRequestV1.create(
        run_id="packaged-verify-run-1",
        operation_id="packaged-verify-operation-1",
        attempt_no=1,
        idempotency_key="packaged-verify-key-1",
        correlation_id="packaged-verify-correlation-1",
        accepted_requirement_revision_id="packaged-requirement-1",
        runtime_attempt_fence_token="packaged-verify-fence-" + "x" * 64,
        profile_binding_generation=1,
        browser_control_scope_id="packaged-browser-scope-1",
        deadline_value=60_000,
        expected_source_operation_ledger_revision=1,
        expected_reconciliation_revision=0,
        delivery_mode="initial",
        dispatch_intent_id="packaged-dispatch-intent-1",
        dispatch_intent_revision=1,
        source_operation_acceptance_ref="packaged-source-acceptance-1",
        profile_binding_ref="packaged-profile-binding-1",
        provider_account_ref="packaged-provider-account-1",
        required_capabilities=("bridge", "extension"),
        user_interaction_policy="observe_only",
        verify_search_surface=True,
        component_receipt_refs=("packaged-main-receipt-1",),
    )


def _assert_no_child_start(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[object] = []

    def fail_if_spawned(*args: object, **kwargs: object) -> object:
        started.append((args, kwargs))
        raise AssertionError("tampered package reached child creation")

    monkeypatch.setattr(owned_process, "spawn_owned_sidecar", fail_if_spawned)
    with pytest.raises(Exception):
        lease = _acquire(root)
        owned_process.spawn_owned_sidecar(lease)
    assert started == []


def test_packaged_artifact_completes_readiness_from_verified_active_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _install_active_artifact(tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "ambient-source-must-not-be-used"))

    lease = _acquire(root)
    admission = lease.admission
    assert admission.manifest_id.startswith("test-only-packaged-sidecar-st1-")
    assert admission.trust_policy_id == "test-only-packaged-sidecar-policy-v1"
    assert admission.source_port_protocol.protocol_id == "seektalent-source-port"
    manifest = parse_release_manifest(
        (root / "slots" / "A" / installed_release.INSTALLED_MANIFEST_RELATIVE_PATH).read_bytes()
    )
    main = next(item for item in manifest.components if item.component_id == "main_application")
    sidecar = next(item for item in manifest.components if item.component_id == "liepin_execution_sidecar")
    assert (admission.product_build_id, admission.main_application_build_id, admission.sidecar_build_id) == (
        manifest.product_build_id,
        main.build_id,
        sidecar.build_id,
    )
    assert admission.source_port_protocol == manifest.compatibility.main_sidecar.source_port_protocol
    assert admission.executable_path.is_relative_to(root / "slots" / "A")
    assert admission.manifest_id == build_test_only_manifest_id(manifest.product_build_id)
    assert [(item.name, item.sha256, item.platform_scope) for item in manifest.dependency_inputs] == [
        ("uv.lock", _digest_file(Path(__file__).resolve().parents[1] / "uv.lock"), "platform_independent"),
    ]
    assert manifest.build_recipe.digest == _digest_file(
        Path(__file__).resolve().parents[1] / "tools" / "build_packaged_sidecar.py"
    )

    session = spawn_ready_sidecar(lease, timeout=5)
    assert session.pid > 0
    assert session.session_id
    assert session.new_history_session().closed is False
    assert session.close(5) != 0

    retry = _acquire(root)
    retry.close()


def test_packaged_artifact_returns_authenticated_read_only_sqlite_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = SourceHistorySQLiteHarness.create(tmp_path / "history.sqlite3")
    harness.register_generation(1)
    harness.record_accepted(_accepted(), generation=1)
    before_bytes = harness.path.read_bytes()
    before_mtime = harness.path.stat().st_mtime_ns
    observer = sqlite3.connect(f"{harness.path.as_uri()}?mode=ro", uri=True)
    before_data_version = observer.execute("PRAGMA data_version").fetchone()
    root = _install_active_artifact(tmp_path)
    monkeypatch.setattr(
        readiness,
        "spawn_owned_sidecar",
        lambda lease: _spawn_test_history_sidecar(lease, harness.path),
    )

    session = spawn_ready_sidecar(_acquire(root), timeout=30)
    try:
        admitted = exchange_source_history(session, _query(), timeout=30)
        second = exchange_source_history(
            session,
            _query(operation_id="operation-2", idempotency_key="key-operation-2"),
            timeout=30,
        )
        assert isinstance(admitted.payload, SourceHistoryMatched)
        assert admitted.payload.facts[0].conclusion == "accepted_no_dispatch"
        assert admitted.session_id == session.session_id
        assert isinstance(second.payload, SourceHistoryNotFound)
        assert second.session_id == session.session_id
    finally:
        session.close(30)

    assert harness.path.read_bytes() == before_bytes
    assert harness.path.stat().st_mtime_ns == before_mtime
    assert observer.execute("PRAGMA data_version").fetchone() == before_data_version
    observer.close()
    retry = _acquire(root)
    retry.close()


def test_packaged_artifact_runs_history_verify_history_over_one_authenticated_pipe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = SourceHistorySQLiteHarness.create(tmp_path / "history.sqlite3")
    harness.register_generation(1)
    harness.record_accepted(_accepted(), generation=1)
    journal_path = tmp_path / "verify-session-journal.sqlite3"
    root = _install_active_artifact(tmp_path)
    monkeypatch.setattr(
        readiness,
        "spawn_owned_sidecar",
        lambda lease: _spawn_test_source_port_sidecar(
            lease,
            "--test-only-source-history-database",
            str(harness.path),
            "--test-only-verify-session-journal",
            str(journal_path),
        ),
    )

    session = spawn_ready_sidecar(_acquire(root), timeout=30)
    try:
        before = exchange_source_history(session, _query(), timeout=30)
        verify_exchange = exchange_verify_session(session, _verify_request(), timeout=30)
        after = exchange_source_history(
            session,
            _query(operation_id="packaged-history-after-verify", idempotency_key="packaged-history-after-verify-key"),
            timeout=30,
        )
        assert isinstance(before.payload, SourceHistoryMatched)
        assert verify_exchange.accepted_ack is not None
        assert verify_exchange.accepted_ack.payload.accepted_fact == "dispatch_authorized"
        assert verify_exchange.terminal.payload.session_readiness == "ready"
        assert isinstance(after.payload, SourceHistoryNotFound)
    finally:
        session.close(30)

    with sqlite3.connect(journal_path) as connection:
        phases = connection.execute("SELECT phase FROM source_history_heads").fetchall()
    assert phases == [("observed_result",)]
    retry = _acquire(root)
    retry.close()


def test_packaged_artifact_exits_after_early_parent_eof(tmp_path: Path) -> None:
    root = _install_active_artifact(tmp_path)

    process = spawn_owned_sidecar(_acquire(root))
    try:
        process.close_stdin()
        assert process.wait(20) == 70
    finally:
        if process.poll() is None:
            process.kill(5)
        process.close_readers()
    retry = _acquire(root)
    retry.close()


def test_packaged_artifact_lifecycle_terminate_and_kill_release_the_active_slot(tmp_path: Path) -> None:
    root = _install_active_artifact(tmp_path)

    terminated = spawn_owned_sidecar(_acquire(root))
    assert terminated.terminate(5) != 0
    terminated.close_stdin()
    terminated.close_readers()
    retry_after_terminate = _acquire(root)
    retry_after_terminate.close()

    killed = spawn_owned_sidecar(_acquire(root))
    assert killed.kill(5) != 0
    killed.close_stdin()
    killed.close_readers()
    retry_after_kill = _acquire(root)
    retry_after_kill.close()


def test_missing_declared_sidecar_support_file_fails_before_child_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _install_active_artifact(tmp_path)
    slot_root = root / "slots" / "A"
    payload_files, executable = _sidecar_files(slot_root)
    missing = next(path for path in payload_files if path != executable)
    missing.unlink()
    child_creation: list[object] = []

    def should_not_start_child(*args: object, **kwargs: object) -> object:
        child_creation.append((args, kwargs))
        raise AssertionError("missing support file reached child creation")

    monkeypatch.setattr(owned_process, "spawn_owned_sidecar", should_not_start_child)

    with pytest.raises(InstalledReleaseError) as raised:
        lease = _acquire(root)
        try:
            owned_process.spawn_owned_sidecar(lease)
        finally:
            lease.close()

    assert raised.value.reason == InstalledReleaseReason.NOT_REGULAR_FILE
    assert raised.value.path == missing
    assert child_creation == []


@pytest.mark.parametrize(
    "tamper",
    ["entrypoint", "payload", "manifest", "signature", "pointer", "build", "hash", "platform"],
)
def test_packaged_artifact_tampering_fails_before_owned_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    root = _install_active_artifact(tmp_path)
    slot_root = root / "slots" / "A"
    manifest_path = slot_root / installed_release.INSTALLED_MANIFEST_RELATIVE_PATH

    if tamper == "entrypoint":
        _, executable = _sidecar_files(slot_root)
        executable.write_bytes(executable.read_bytes() + b"tampered")
    elif tamper == "payload":
        payload_files, executable = _sidecar_files(slot_root)
        payload = next(path for path in payload_files if path != executable)
        payload.write_bytes(payload.read_bytes() + b"tampered")
    elif tamper == "manifest":
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["channel"] = "candidate"
        manifest_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    elif tamper == "signature":
        signature_path = slot_root / installed_release.INSTALLED_SIGNATURE_RELATIVE_PATH
        payload = json.loads(signature_path.read_text(encoding="utf-8"))
        signature = payload["signature"]
        payload["signature"] = ("A" if signature[0] != "A" else "B") + signature[1:]
        signature_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    elif tamper == "pointer":
        pointer_path = root / installed_slot.ACTIVE_SLOT_POINTER_RELATIVE_PATH
        pointer_path.write_bytes(pointer_path.read_bytes() + b" ")
    elif tamper == "build":
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        sidecar = next(item for item in payload["components"] if item["component_id"] == "liepin_execution_sidecar")
        sidecar["build_id"] = "test-only-replaced-sidecar-build"
        manifest_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    elif tamper == "hash":
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        sidecar = next(item for item in payload["components"] if item["component_id"] == "liepin_execution_sidecar")
        sidecar["files"][0]["sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    else:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["target"]["arch"] = "x86_64" if platform.machine().lower() == "arm64" else "arm64"
        manifest_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

    _assert_no_child_start(root, monkeypatch)


def test_packaged_artifact_is_not_a_python_source_or_network_launcher(tmp_path: Path) -> None:
    root = _install_active_artifact(tmp_path)
    slot_root = root / "slots" / "A"
    files, executable = _sidecar_files(slot_root)

    assert executable.is_file()
    assert len(files) > 1
    assert all(path.is_relative_to(slot_root / "release") for path in files)
    assert not any(path.suffix == ".py" for path in files)
    cryptography_files = [path for path in files if "cryptography" in path.parts]
    assert not cryptography_files
    assert not os.environ.get("SEEKTALENT_PACKAGED_SIDECAR_NETWORK")

    environment = {
        "PATH": os.environ.get("SYSTEMROOT", "") if os.name == "nt" else "/usr/bin:/bin",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": str(tmp_path / "ambient-source-must-not-be-used"),
    }
    if os.name == "nt":
        environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    direct = subprocess.run(
        [str(executable)],
        cwd=executable.parent,
        input=b"",
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )
    assert direct.returncode == 70, direct.stderr.decode("utf-8", errors="replace")


def _digest_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()
