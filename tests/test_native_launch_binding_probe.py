from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest


PROBE = Path("tools/native_probes/launch_binding_probe.py")
MACOS_HELPER = Path("tools/native_probes/macos_dynamic_code_identity.c")
WORKFLOW = Path(".github/workflows/native-launch-binding-probe.yml")
requires_native_host = pytest.mark.skipif(
    os.name != "nt" and sys.platform != "darwin",
    reason="native launch binding evidence is defined only for Windows and macOS",
)


@requires_native_host
def test_native_launch_binding_probe_records_host_semantics() -> None:
    completed = subprocess.run(
        [sys.executable, str(PROBE), "--json"],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["schema_version"] == "seektalent.native_launch_binding_probe.v1"
    assert result["architecture"] == platform.machine().lower()
    popen_toctou = result["evidence"]["popen_toctou"]
    assert popen_toctou["path_replacement_changed_started_image"] is True
    assert popen_toctou["admitted_sha256"] != popen_toctou["launched_sha256"]
    assert popen_toctou["before"] != popen_toctou["after"]

    if sys.platform == "darwin":
        assert result["platform"] == "macos"
        limits = result["evidence"]["fd_and_flock_limits"]
        assert limits == {
            "flock_blocks_cooperating_locker": True,
            "flock_does_not_block_noncooperative_write": True,
            "open_file_and_flock_do_not_block_replace": True,
            "open_directory_and_flock_do_not_block_rename": True,
            "cpython_has_fexecve": False,
            "cpython_has_posix_spawn": True,
        }
        identity = result["evidence"]["security_framework"]
        assert identity["pid_dynamic_identity_is_available"] is True
        assert identity["apple_requirement_fails_closed_before_resume"] is True
        assert identity["apple_signed_child"]["apple_requirement_status"] == 0
        suspended = identity["suspended_local_child"]
        assert suspended["apple_requirement_status"] != 0
        assert suspended["marker_absent_before_resume"] is True
        assert suspended["marker_absent_after_reap"] is True
        assert suspended["child_killed_without_resume"] is True
    else:
        assert result["platform"] == "windows"
        lease = result["evidence"]["createfile_file_lease"]
        assert lease["write_while_leased"]["succeeded"] is False
        assert lease["replace_while_leased"]["succeeded"] is False
        assert lease["delete_while_leased"]["succeeded"] is False
        assert lease["create_process_under_file_lease"]["created_suspended"] is True
        assert lease["create_process_under_file_lease"]["child_exit_code"] == 0
        assert lease["replace_after_release"]["succeeded"] is True
        components = result["evidence"]["createfile_component_leases"]
        assert all(outcome["succeeded"] is False for outcome in components.values())
        assert result["evidence"]["preexisting_writer_limit"] == {
            "preexisting_writer_can_mutate_after_lease": True
        }


def test_native_probe_stays_outside_production_composition() -> None:
    source = PROBE.read_text(encoding="utf-8")
    assert "import seektalent" not in source
    assert "from seektalent" not in source
    assert "subprocess.Popen" not in MACOS_HELPER.read_text(encoding="utf-8")


def test_native_workflow_has_the_three_fixed_native_hosts() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "windows-2025" in source
    assert "macos-15" in source
    assert "macos-15-intel" in source
    assert '"tools/native_probes/launch_binding_probe.py", "--json"' in source
    assert "tests/test_native_launch_binding_probe.py" in source
