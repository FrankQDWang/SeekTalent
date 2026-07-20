from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest


PROBE = Path("tools/native_probes/launch_binding_probe.py")
COMMON_PROBE = Path("tools/native_probes/launch_binding_common.py")
MACOS_PROBE = Path("tools/native_probes/launch_binding_macos.py")
WINDOWS_PROBE = Path("tools/native_probes/launch_binding_windows.py")
MACOS_HELPER = Path("tools/native_probes/macos_dynamic_code_identity.c")
WORKFLOW = Path(".github/workflows/native-launch-binding-probe.yml")
EVIDENCE_PATH_ENV = "SEEKTALENT_NATIVE_LAUNCH_BINDING_EVIDENCE"
requires_native_host = pytest.mark.skipif(
    os.name != "nt" and sys.platform != "darwin",
    reason="native launch binding evidence is defined only for Windows and macOS",
)


@requires_native_host
def test_native_launch_binding_probe_records_host_semantics() -> None:
    evidence_path = os.environ.get(EVIDENCE_PATH_ENV)
    if evidence_path is None:
        completed = subprocess.run(
            [sys.executable, str(PROBE), "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=240,
        )
        assert completed.returncode == 0, completed.stderr
        result = json.loads(completed.stdout)
    else:
        result = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
    assert result["schema_version"] == "seektalent.native_launch_binding_probe.v1"
    assert result["architecture"] == platform.machine().lower()
    host = result["host"]
    assert host["os_release"]
    assert host["os_build"]
    assert host["python_implementation"]
    assert host["python_version"]
    assert len(host["python_build"]) == 2
    popen_toctou = result["evidence"]["popen_toctou"]
    assert popen_toctou["path_replacement_changed_started_image"] is True
    assert popen_toctou["admitted_sha256"] != popen_toctou["launched_sha256"]
    assert popen_toctou["before"] != popen_toctou["after"]

    if sys.platform == "darwin":
        assert result["platform"] == "macos"
        limits = result["evidence"]["fd_and_flock_limits"]
        assert limits["flock_blocks_cooperating_locker"] is True
        assert limits["flock_does_not_block_noncooperative_write"] is True
        assert limits["open_file_and_flock_do_not_block_replace"] is True
        assert limits["open_directory_and_flock_do_not_block_rename"] is True
        assert isinstance(limits["os_execve_supports_fd"], bool)
        assert limits["cpython_has_fexecve"] is False
        assert limits["cpython_has_posix_spawn"] is True
        lifecycle = result["evidence"]["cooperative_slot_lifecycle"]
        assert lifecycle["activation_to_new_slot_succeeded"] is True
        assert lifecycle["rollback_selects_retained_slot"] is True
        assert lifecycle["retire_while_leased"] == {"lease_acquired": False, "deleted": False}
        assert lifecycle["retire_after_release"] == {"lease_acquired": True, "deleted": True}
        race_no_go = result["evidence"]["path_start_replace_race"]
        assert race_no_go["automated_1000_raw_path_race"] == "no-go"
        assert race_no_go["required_ci_probe"] is False
        assert race_no_go["pre_spawn_guarantee_for_per_user_writable_slot"] is False
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
        filesystem = result["evidence"]["supported_local_filesystem"]
        assert filesystem["filesystem_name"] == "NTFS"
        assert filesystem["supported_local_filesystem"] is True
        reparse = result["evidence"]["reparse_component"]
        assert reparse["opened_with_open_reparse_point"] is True
        assert reparse["handle_attribute_tag_checked"] is True
        assert reparse["rejected_before_lease_or_spawn"] is True
        assert reparse["spawn_attempted"] is False
        lease = result["evidence"]["createfile_file_lease"]
        assert lease["all_chain_handles_live_through_identity_gate"] is True
        assert lease["lease_handle_count"] == 5
        assert len(lease["path_components"]) == 4
        assert lease["write_while_leased"]["succeeded"] is False
        assert lease["rename_while_leased"]["succeeded"] is False
        assert lease["replace_while_leased"]["succeeded"] is False
        assert lease["delete_while_leased"]["succeeded"] is False
        assert all(outcome["succeeded"] is False for outcome in lease["component_rename_while_leased"].values())
        assert all(outcome["succeeded"] is False for outcome in lease["component_delete_while_leased"].values())
        started = lease["create_process_under_full_lease"]
        assert started["created_suspended"] is True
        assert started["identity_match"] is True
        assert started["terminated_while_suspended"] is True
        assert started["admitted_final_path"] == started["observed_final_path"]
        assert started["raw_process_image_path"]
        assert started["raw_path_is_corroborating_name_evidence"] is True
        assert started["cleanup"]["terminate_succeeded"] is True
        assert started["cleanup"]["reaped"] is True
        mismatch = lease["identity_mismatch_cleanup"]
        assert mismatch["identity_match"] is False
        assert mismatch["failed_closed"] is True
        assert mismatch["cleanup"]["reaped"] is True
        race = lease["start_replace_race"]
        assert race["iterations"] == 1_000
        assert race["identity_verified_child_count"] == 1_000
        assert race["unauthorized_child_count"] == 0
        assert race["unexplained_result_count"] == 0
        assert race["replace_error_histogram"] == {"32": 1_000}
        assert all(lease["controls_after_release"].values())
        components = result["evidence"]["createfile_component_leases"]
        assert all(outcome["succeeded"] is False for outcome in components.values())
        assert result["evidence"]["preexisting_writer_limit"] == {
            "preexisting_writer_causes_share_mode_conflict": True,
            "error": 32,
        }


def test_native_probe_stays_outside_production_composition() -> None:
    for probe_source in (PROBE, COMMON_PROBE, MACOS_PROBE, WINDOWS_PROBE):
        source = probe_source.read_text(encoding="utf-8")
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
    assert EVIDENCE_PATH_ENV in source
