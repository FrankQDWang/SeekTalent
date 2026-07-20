"""Darwin-only native evidence for the immutable-slot decision."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from launch_binding_common import ProbeFailure, red_popen_toctou


def _child_lock_attempt(path: Path) -> int:
    script = """
import errno
import fcntl
import os
import sys
fd = os.open(sys.argv[1], os.O_RDWR)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError as exc:
    if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK}:
        raise SystemExit(0)
    raise
raise SystemExit(1)
"""
    return subprocess.run([sys.executable, "-c", script, str(path)], check=False, timeout=10).returncode


def _noncooperative_write(path: Path) -> int:
    script = """
import sys
with open(sys.argv[1], "r+b", buffering=0) as output:
    output.write(b"changed")
"""
    return subprocess.run([sys.executable, "-c", script, str(path)], check=False, timeout=10).returncode


def _fd_and_flock_limits(root: Path) -> dict[str, object]:
    import fcntl

    payload = root / "payload"
    staged = root / "staged"
    payload.write_bytes(b"original")
    staged.write_bytes(b"replacement")
    descriptor = os.open(payload, os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        cooperative_lock_blocked = _child_lock_attempt(payload) == 0
        noncooperative_write_succeeded = _noncooperative_write(payload) == 0
        os.replace(staged, payload)
        replace_succeeded = payload.read_bytes() == b"replacement"
    finally:
        os.close(descriptor)

    slot = root / "slot"
    slot.mkdir()
    slot_descriptor = os.open(slot, os.O_RDONLY)
    try:
        fcntl.flock(slot_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        moved_slot = root / "slot-moved"
        slot.rename(moved_slot)
        directory_rename_succeeded = moved_slot.is_dir()
    finally:
        os.close(slot_descriptor)

    if not cooperative_lock_blocked or not noncooperative_write_succeeded or not replace_succeeded:
        raise ProbeFailure("Darwin flock did not show its expected advisory-only behavior")
    if not directory_rename_succeeded:
        raise ProbeFailure("Darwin open directory descriptor unexpectedly prevented rename")
    return {
        "flock_blocks_cooperating_locker": cooperative_lock_blocked,
        "flock_does_not_block_noncooperative_write": noncooperative_write_succeeded,
        "open_file_and_flock_do_not_block_replace": replace_succeeded,
        "open_directory_and_flock_do_not_block_rename": directory_rename_succeeded,
        "os_execve_supports_fd": os.execve in os.supports_fd,
        "cpython_has_fexecve": hasattr(os, "fexecve"),
        "cpython_has_posix_spawn": hasattr(os, "posix_spawn"),
    }


def _activate(activation: Path, release: Path) -> None:
    staged = activation.with_name(f"{activation.name}.staged")
    staged.write_text(release.name, encoding="utf-8")
    os.replace(staged, activation)


def _cooperating_retire(slot: Path) -> dict[str, bool]:
    script = """
import errno
import fcntl
import json
import os
import shutil
import sys

slot = sys.argv[1]
descriptor = os.open(os.path.join(slot, ".lease"), os.O_RDONLY)
try:
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK}:
            print(json.dumps({"lease_acquired": False, "deleted": False}))
            raise SystemExit(0)
        raise
    shutil.rmtree(slot)
    print(json.dumps({"lease_acquired": True, "deleted": True}))
finally:
    os.close(descriptor)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(slot)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise ProbeFailure(f"cooperating Darwin retire helper failed: {completed.stderr.strip()}")
    value = json.loads(completed.stdout)
    if not isinstance(value, dict) or set(value) != {"lease_acquired", "deleted"}:
        raise ProbeFailure("cooperating Darwin retire helper returned invalid evidence")
    if not all(isinstance(item, bool) for item in value.values()):
        raise ProbeFailure("cooperating Darwin retire helper returned non-boolean evidence")
    return value


def _cooperative_slot_lifecycle(root: Path) -> dict[str, object]:
    import fcntl

    installed_root = root / "installed-root"
    slots = installed_root / "slots"
    old_slot = slots / "release-001"
    new_slot = slots / "release-002"
    (old_slot / "bin").mkdir(parents=True)
    (new_slot / "bin").mkdir(parents=True)
    (old_slot / ".lease").write_text("lease", encoding="utf-8")
    (new_slot / ".lease").write_text("lease", encoding="utf-8")
    activation = installed_root / "active-slot"
    _activate(activation, old_slot)

    descriptor = os.open(old_slot / ".lease", os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _activate(activation, new_slot)
        activation_to_new_slot = activation.read_text(encoding="utf-8") == new_slot.name
        retained_while_leased = _cooperating_retire(old_slot)
        _activate(activation, old_slot)
        rollback_selects_retained_slot = activation.read_text(encoding="utf-8") == old_slot.name
    finally:
        os.close(descriptor)
    retired_after_release = _cooperating_retire(old_slot)

    if not activation_to_new_slot or retained_while_leased != {"lease_acquired": False, "deleted": False}:
        raise ProbeFailure("cooperating activation did not retain the leased Darwin slot")
    if not rollback_selects_retained_slot or retired_after_release != {"lease_acquired": True, "deleted": True}:
        raise ProbeFailure("cooperating Darwin lifecycle did not delete only after release")
    return {
        "activation_to_new_slot_succeeded": activation_to_new_slot,
        "rollback_selects_retained_slot": rollback_selects_retained_slot,
        "retire_while_leased": retained_while_leased,
        "retire_after_release": retired_after_release,
    }


def _write_exit_script(path: Path, exit_code: int) -> None:
    path.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8")
    path.chmod(0o700)


def _noncooperative_path_race_no_go() -> dict[str, object]:
    """Record why a raw macOS path-start racer is intentionally not CI automation."""
    return {
        "automated_1000_raw_path_race": "no-go",
        "observation": "one raw concurrent path-start/replace attempt left a dyld-start child unreapable after SIGKILL",
        "required_ci_probe": False,
        "pre_spawn_guarantee_for_per_user_writable_slot": False,
        "safe_required_evidence": [
            "deterministic_path_replacement_toctou",
            "cooperative_slot_lifecycle",
            "suspended_security_framework_pid_gate",
        ],
    }


def _dynamic_code_identity(root: Path) -> dict[str, object]:
    helper_source = Path(__file__).with_name("macos_dynamic_code_identity.c")
    helper = root / "macos_dynamic_code_identity"
    compiled = subprocess.run(
        [
            "/usr/bin/xcrun",
            "clang",
            "-Werror",
            "-framework",
            "Security",
            "-framework",
            "CoreFoundation",
            str(helper_source),
            "-o",
            str(helper),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if compiled.returncode != 0:
        raise ProbeFailure(f"could not compile Security.framework probe: {compiled.stderr.strip()}")

    system_child = subprocess.Popen(["/bin/sleep", "10"])
    try:
        trusted = _run_identity_helper(helper, system_child.pid)
    finally:
        _stop_child(system_child)

    marker = root / "untrusted-child-ran"
    suspended = _run_suspended_identity_helper(helper, marker)
    if trusted["guest_status"] != 0 or trusted["dynamic_validity_status"] != 0:
        raise ProbeFailure("Security.framework could not dynamically validate a live Apple-signed process")
    if trusted["apple_requirement_status"] != 0:
        raise ProbeFailure("Security.framework rejected /bin/sleep for the Apple anchor requirement")
    if (
        suspended["guest_status"] != 0
        or suspended["apple_requirement_status"] == 0
        or not suspended["marker_absent_before_resume"]
        or not suspended["marker_absent_after_reap"]
        or not suspended["child_killed_without_resume"]
    ):
        raise ProbeFailure("suspended Security.framework gate did not kill the unauthorized local child")
    return {
        "apple_signed_child": trusted,
        "suspended_local_child": suspended,
        "pid_dynamic_identity_is_available": True,
        "apple_requirement_fails_closed_before_resume": True,
    }


def _run_identity_helper(helper: Path, pid: int) -> dict[str, int]:
    completed = subprocess.run([str(helper), str(pid)], check=False, capture_output=True, text=True, timeout=10)
    if completed.returncode != 0:
        raise ProbeFailure(f"Security.framework helper failed: {completed.stderr.strip()}")
    value = json.loads(completed.stdout)
    if not isinstance(value, dict) or not all(isinstance(item, int) for item in value.values()):
        raise ProbeFailure("Security.framework helper returned an invalid result")
    return value


def _run_suspended_identity_helper(helper: Path, marker: Path) -> dict[str, int | bool]:
    completed = subprocess.run(
        [str(helper), "--spawn-suspended-and-reject", str(marker)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise ProbeFailure(f"suspended Security.framework helper failed: {completed.stderr.strip()}")
    value = json.loads(completed.stdout)
    expected = {
        "marker_absent_before_resume",
        "marker_absent_after_reap",
        "child_killed_without_resume",
        "guest_status",
        "dynamic_validity_status",
        "apple_requirement_status",
    }
    if set(value) != expected or not all(isinstance(item, (bool, int)) for item in value.values()):
        raise ProbeFailure("suspended Security.framework helper returned an invalid result")
    return value


def _stop_child(child: subprocess.Popen[bytes] | subprocess.Popen[str]) -> None:
    if child.poll() is None:
        child.terminate()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=5)


def probe(root: Path) -> dict[str, object]:
    trusted = root / "trusted-sidecar"
    replacement = root / "replacement-sidecar"
    _write_exit_script(trusted, 0)
    _write_exit_script(replacement, 1)
    return {
        "popen_toctou": red_popen_toctou(
            root,
            trusted,
            replacement,
            record_open_descriptor_identity=True,
        ),
        "fd_and_flock_limits": _fd_and_flock_limits(root),
        "cooperative_slot_lifecycle": _cooperative_slot_lifecycle(root),
        "path_start_replace_race": _noncooperative_path_race_no_go(),
        "security_framework": _dynamic_code_identity(root),
    }
