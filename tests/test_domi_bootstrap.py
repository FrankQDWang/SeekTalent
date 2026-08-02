from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from seektalent import domi_bootstrap
from seektalent.browser_bridge_manifest import (
    WTSCLI_BUILD_ID,
    WTSCLI_EXTENSION_ID,
    WTSCLI_FORK_COMMIT,
    WTSCLI_VERSION,
)
from seektalent.domi_host_runtime import HostRuntimeIdentity
from tests.browser_bridge_bundle_fixtures import write_browser_bridge_bundle


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


def _touch_executable(path: Path) -> Path:
    path = _touch(path)
    path.chmod(0o755)
    return path


def test_bootstrap_writes_windows_shims_with_domi_python_node_and_pythonpath(tmp_path: Path) -> None:
    home = tmp_path / "home"
    domi_python = _touch(tmp_path / "Domi" / "runtime" / "python" / "bin" / "python.exe")
    domi_node = _touch(tmp_path / "Domi" / "runtime" / "node" / "node.exe")
    site_packages = tmp_path / "home" / ".seektalent" / "python-prefix" / "0.7.25" / "Lib" / "site-packages"
    legacy_bin_ps1 = _touch(home / ".seektalent" / "bin" / "seektalent.ps1")
    legacy_ps1 = _touch(home / ".seektalent" / "seektalent.ps1")
    legacy_cmd = _touch(home / ".seektalent" / "seektalent.cmd")
    root_runner = home / ".seektalent" / "seektalent-runner.ps1"
    legacy_bin_ps1.write_text("old 0.7.21 bin shim", encoding="utf-8")
    legacy_ps1.write_text("old 0.7.21 root shim", encoding="utf-8")
    legacy_cmd.write_text("old root cmd", encoding="utf-8")

    result = domi_bootstrap.bootstrap_domi_workbench(
        home=home,
        platform="win32",
        domi_python=domi_python,
        domi_node=domi_node,
        python_paths=(site_packages,),
        package_version="0.7.25",
    )

    stale_ps1 = result.bin_dir / "seektalent.ps1"
    runner = result.bin_dir / "seektalent-runner.ps1"
    cmd = result.bin_dir / "seektalent.cmd"
    assert not stale_ps1.exists()
    assert runner.exists()
    assert cmd.exists()
    assert not legacy_ps1.exists()
    assert legacy_cmd.exists()
    assert result.command_name == "seektalent"
    assert result.package_version == "0.7.25"

    runner_text = runner.read_text(encoding="utf-8")
    assert runner_text.startswith('$ErrorActionPreference = "Stop"')
    assert "`$DomiPython" not in runner_text
    assert str(domi_python) in runner_text
    assert str(domi_node) in runner_text
    assert str(site_packages) in runner_text
    assert "SEEKTALENT_DOMI_NODE" in runner_text
    assert "-m seektalent.domi_workbench" in runner_text
    assert "-m seektalent_ui.maintenance" in runner_text
    assert "-m seektalent @args" in runner_text

    cmd_text = cmd.read_text(encoding="utf-8")
    assert "seektalent.ps1" not in cmd_text
    assert "seektalent-runner.ps1" in cmd_text
    assert "-ExecutionPolicy Bypass" in cmd_text

    assert legacy_cmd.read_text(encoding="utf-8") == cmd_text
    assert root_runner.read_text(encoding="utf-8") == runner_text


def test_bootstrap_writes_posix_shim_with_domi_python_node_and_pythonpath(tmp_path: Path) -> None:
    home = tmp_path / "home"
    domi_python = _touch_executable(tmp_path / "Domi.app" / "python" / "runtime" / "bin" / "python")
    domi_node = _touch_executable(tmp_path / "Domi.app" / "node" / "runtime" / "bin" / "node")
    site_packages = home / ".seektalent" / "python-prefix" / "0.7.25" / "site-packages"

    result = domi_bootstrap.bootstrap_domi_workbench(
        home=home,
        platform="darwin",
        domi_python=domi_python,
        domi_node=domi_node,
        python_paths=(site_packages,),
        package_version="0.7.25",
    )

    shim = result.bin_dir / "seektalent"
    assert shim.exists()
    assert os.access(shim, os.X_OK)

    text = shim.read_text(encoding="utf-8")
    assert str(domi_python) in text
    assert str(domi_node) in text
    assert str(site_packages) in text
    assert "SEEKTALENT_DOMI_NODE" in text
    assert "-m seektalent.domi_workbench" in text
    assert "-m seektalent_ui.maintenance" in text
    assert "-m seektalent \"$@\"" in text


def test_posix_shim_routes_maintenance_to_installed_support_cli(tmp_path: Path) -> None:
    home = tmp_path / "home"
    invocation = tmp_path / "python-invocation.txt"
    domi_python = tmp_path / "Domi.app" / "python" / "runtime" / "bin" / "python"
    domi_python.parent.mkdir(parents=True)
    domi_python.write_text(
        f"""#!/bin/sh
printf '%s\\n' "$@" > {_bash_quote(invocation)}
""",
        encoding="utf-8",
    )
    domi_python.chmod(0o755)
    domi_node = _touch_executable(tmp_path / "Domi.app" / "node" / "runtime" / "bin" / "node")

    result = domi_bootstrap.bootstrap_domi_workbench(
        home=home,
        platform="darwin",
        domi_python=domi_python,
        domi_node=domi_node,
        package_version="0.8.0rc1",
    )

    completed = subprocess.run(
        [
            str(result.bin_dir / "seektalent"),
            "maintenance",
            "support-bundle",
            "--runtime-mode",
            "prod",
        ],
        check=False,
    )

    assert completed.returncode == 0
    assert invocation.read_text(encoding="utf-8").splitlines() == [
        "-m",
        "seektalent_ui.maintenance",
        "support-bundle",
        "--runtime-mode",
        "prod",
    ]


def test_bootstrap_installs_prepared_runtime_only_with_its_exact_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    domi_python = _touch_executable(tmp_path / "Domi.app" / "python" / "bin" / "python")
    domi_node = _touch_executable(tmp_path / "Domi.app" / "node" / "bin" / "node")
    bundle = tmp_path / "bundle"
    prepared_runtime = tmp_path / "prepared-runtime"
    write_browser_bridge_bundle(bundle)
    prepared_runtime.mkdir()
    delivery_manifest = tmp_path / "delivery-manifest.json"
    fixture = tmp_path / "acceptance" / "fixture.json"
    fixture.parent.mkdir()
    fixture.write_text(
        json.dumps({"schemaVersion": "seektalent.acceptance-fixture.v1"}),
        encoding="utf-8",
    )
    (tmp_path / "verify_domi_host_runtime.py").write_text("# verifier\n", encoding="utf-8")
    delivery_manifest.write_text(
        json.dumps(
                {
                    "schema_version": 2,
                    "platform": "macos-arm64",
                    "os_family": "macos",
                    "architecture": "arm64",
                    "product_version": "0.8.0rc1",
                "source_revision": "a" * 40,
                "product_build_id": (
                    "seektalent-0.8.0rc1+" + "a" * 40
                ),
                "seektalent_wheel": "seektalent-0.8.0rc1-py3-none-any.whl",
                "seektalent_wheel_sha256": "b" * 64,
                "bridge_build_id": WTSCLI_BUILD_ID,
                "wtscli_version": WTSCLI_VERSION,
                "wtscli_fork_commit": WTSCLI_FORK_COMMIT,
                "extension_version": WTSCLI_VERSION,
                "extension_id_sha256": hashlib.sha256(
                    WTSCLI_EXTENSION_ID.encode()
                ).hexdigest(),
                "host_runtime_contract": {
                    "platform": "macos-arm64",
                    "os_family": "macos",
                    "architecture": "arm64",
                    "python_implementation": "cpython",
                    "python_major_minor": "3.13",
                    "python_cache_tag": "cpython-313",
                    "python_soabi": "cpython-313-darwin",
                    "node_version": "22.14.0",
                },
                "acceptance_fixture": {
                    "path": "acceptance/fixture.json",
                    "schema_version": "seektalent.acceptance-fixture.v1",
                    "sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
                },
            }
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}
    captured_receipt: dict[str, object] = {}

    def fake_install(**kwargs: object) -> None:
        captured.update(kwargs)
        for source, target in kwargs["additional_targets"]:
            if target == home / domi_bootstrap.INSTALL_RECEIPT_RELATIVE_PATH:
                captured_receipt.update(
                    json.loads(source.read_text(encoding="utf-8"))
                )

    monkeypatch.setattr(domi_bootstrap, "install_browser_bridge_bundle", fake_install)
    monkeypatch.setattr(
        domi_bootstrap,
        "probe_runtime_with_python",
        lambda *_args: HostRuntimeIdentity(
            platform="macos-arm64",
            os_family="macos",
            architecture="arm64",
            python_executable=str(domi_python.resolve()),
            python_version="3.13.7",
            python_implementation="cpython",
            python_cache_tag="cpython-313",
            python_soabi="cpython-313-darwin",
            python_executable_sha256="c" * 64,
            node_executable=str(domi_node.resolve()),
            node_version="22.14.0",
            node_executable_sha256="d" * 64,
        ),
    )

    domi_bootstrap.bootstrap_domi_workbench(
        home=home,
        platform="darwin",
        domi_python=domi_python,
        domi_node=domi_node,
        browser_bridge_bundle_dir=bundle,
        browser_bridge_prepared_runtime_dir=prepared_runtime,
        delivery_manifest_path=delivery_manifest,
        package_version="0.8.0rc1",
    )

    assert captured["bundle_dir"] == bundle
    assert captured["install_root"] == home / ".seektalent"
    assert captured["node"] == domi_node
    assert captured["prepared_runtime_dir"] == prepared_runtime
    additional_targets = captured["additional_targets"]
    assert isinstance(additional_targets, tuple)
    assert [target for _source, target in additional_targets] == [
        home / ".seektalent" / "bin",
        home / domi_bootstrap.INSTALL_RECEIPT_RELATIVE_PATH,
        home / ".seektalent" / "delivery-manifest.json",
        home / ".seektalent" / "acceptance" / "fixture.json",
        home / ".seektalent" / "verify_domi_host_runtime.py",
    ]
    assert captured_receipt["sourceRevision"] == "a" * 40
    assert captured_receipt["productVersion"] == "0.8.0rc1"
    assert captured_receipt["bridgeBuildId"] == WTSCLI_BUILD_ID
    assert captured_receipt["schemaVersion"] == "seektalent.install-receipt.v2"
    assert captured_receipt["pythonSoabi"] == "cpython-313-darwin"

    with pytest.raises(
        domi_bootstrap.DomiBootstrapError,
        match="prepared WTSCLI runtime requires",
    ):
        domi_bootstrap.bootstrap_domi_workbench(
            home=home,
            platform="darwin",
            domi_python=domi_python,
            domi_node=domi_node,
            browser_bridge_prepared_runtime_dir=prepared_runtime,
        )


def test_resolve_domi_node_requires_an_explicit_host_path() -> None:
    assert domi_bootstrap.resolve_domi_node(env={"APPDATA": "/ignored"}, platform="win32") == Path("node.exe")


def test_resolve_domi_node_accepts_env_directory_alias(tmp_path: Path) -> None:
    bin_dir = tmp_path / "domi" / "node"
    expected = _touch(bin_dir / "node")

    assert domi_bootstrap.resolve_domi_node(env={"DOMI_NODE": str(bin_dir)}, platform="darwin", home=tmp_path) == expected


def test_bootstrap_rejects_non_executable_posix_domi_python(tmp_path: Path) -> None:
    domi_python = _touch(tmp_path / "Domi.app" / "python" / "runtime" / "bin" / "python")
    domi_node = _touch_executable(tmp_path / "Domi.app" / "node" / "runtime" / "bin" / "node")

    try:
        domi_bootstrap.bootstrap_domi_workbench(
            home=tmp_path / "home",
            platform="darwin",
            domi_python=domi_python,
            domi_node=domi_node,
        )
    except domi_bootstrap.DomiBootstrapError as exc:
        assert exc.reason_code == "domi_python_missing"
    else:
        raise AssertionError("non-executable Domi Python should fail on POSIX")


def test_domi_bootstrap_main_writes_json_result(tmp_path: Path, capsys) -> None:
    domi_python = _touch_executable(tmp_path / "Domi.app" / "python" / "runtime" / "bin" / "python")
    domi_node = _touch_executable(tmp_path / "Domi.app" / "node" / "runtime" / "bin" / "node")
    site_packages = tmp_path / "prefix" / "site-packages"
    bin_dir = tmp_path / "bin"

    assert (
        domi_bootstrap.main(
            [
                "--domi-python",
                str(domi_python),
                "--domi-node",
                str(domi_node),
                "--python-path",
                str(site_packages),
                "--bin-dir",
                str(bin_dir),
                "--package-version",
                "0.7.25",
                "--print-json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["commandName"] == "seektalent"
    assert payload["domiPython"] == str(domi_python)
    assert payload["domiNode"] == str(domi_node)
    assert (bin_dir / "seektalent").exists()


def test_install_scripts_delegate_to_package_bootstrap() -> None:
    windows_script = Path("scripts/install-seektalent-domi.ps1").read_text(encoding="utf-8")
    mac_script = Path("scripts/install-seektalent-domi.sh").read_text(encoding="utf-8")

    assert "pip install" in windows_script
    assert "--no-index --find-links $Wheelhouse" in windows_script
    assert "seektalent==$Version" not in windows_script
    assert "validate-delivery" in windows_script
    assert "SEEKTALENT_INSTALL_HOME" in windows_script
    assert "--home $InstallHome" in windows_script
    assert "SEEKTALENT_INSTALL_HOME" in mac_script
    assert '--home "${install_home}"' in mac_script
    assert "--no-deps" not in windows_script
    assert "--ignore-installed" in windows_script
    assert "function Install-SeekTalentDomi" in windows_script
    assert "-m seektalent.domi_bootstrap" in windows_script
    assert "--python-path" in windows_script
    assert "$env:Path" in windows_script
    assert "SetEnvironmentVariable" not in windows_script
    assert "$PreviousPythonPath = $env:PYTHONPATH" in windows_script
    assert "finally" in windows_script
    assert "Remove-Item Env:PYTHONPATH" in windows_script
    assert "$env:PYTHONPATH = $PreviousPythonPath" in windows_script

    assert "pip install" in mac_script
    assert '--no-index --find-links "${wheelhouse}"' in mac_script
    assert "seektalent==${version}" not in mac_script
    assert "validate-delivery" in mac_script
    assert "--no-deps" not in mac_script
    assert "--ignore-installed" in mac_script
    assert "set -euo pipefail" not in mac_script
    assert "-m seektalent.domi_bootstrap" in mac_script
    assert "--python-path" in mac_script
    assert "export PATH=" in mac_script
    assert 'mktemp -d "${rollback_root}/${version}.XXXXXX"' in mac_script
    assert "rollback-seektalent-domi.sh" in mac_script


def test_posix_rollback_is_single_use_and_preserves_snapshot(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    current = home / ".seektalent"
    current.mkdir(parents=True)
    (current / "identity").write_text("old")
    rollback = home / ".seektalent-rollbacks" / "0.8.0rc1.test"
    rollback.mkdir(parents=True)
    (rollback / "seektalent").mkdir()
    (rollback / "seektalent" / "identity").write_text("old")
    (rollback / ".available").write_text("")
    (current / "identity").write_text("new")

    script = Path("scripts/rollback-seektalent-domi.sh").resolve()
    first = subprocess.run(
        [str(script), str(rollback)],
        env={
            **os.environ,
            "SEEKTALENT_INSTALL_HOME": str(home),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    second = subprocess.run(
        [str(script), str(rollback)],
        env={
            **os.environ,
            "SEEKTALENT_INSTALL_HOME": str(home),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert first.returncode == 0
    assert (current / "identity").read_text() == "old"
    assert (rollback / ".used").is_file()
    assert second.returncode == 1
    assert "rollback_snapshot_unavailable" in second.stderr


def test_posix_install_script_preserves_sourced_shell_state(tmp_path: Path) -> None:
    home = tmp_path / "home"
    domi_python = tmp_path / "Domi.app" / "python" / "runtime" / "bin" / "python"
    domi_node = tmp_path / "Domi.app" / "node" / "runtime" / "bin" / "node"
    domi_python.parent.mkdir(parents=True)
    domi_node.parent.mkdir(parents=True)
    home.mkdir()
    wtscli_bundle = _write_bundle_marker(tmp_path)
    domi_python.write_text(
        f"""#!/usr/bin/env bash
if [[ "${{1:-}}" == *"install_staging_browser_bridge.py" ]]; then
  exec {_bash_quote(sys.executable)} "$@"
fi
if [[ "${{1:-}} ${{2:-}}" == "-m pip" ]]; then
  exit 0
fi
if [[ "${{1:-}} ${{2:-}}" == "-m zipfile" ]]; then
  exit 0
fi
if [[ "${{1:-}} ${{2:-}}" == "-m seektalent.domi_bootstrap" ]]; then
  echo '{{}}'
  exit 0
fi
echo "unexpected fake Domi Python invocation: $*" >&2
exit 2
""",
        encoding="utf-8",
    )
    domi_node.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    domi_python.chmod(0o755)
    domi_node.chmod(0o755)
    script = Path("scripts/install-seektalent-domi.sh").resolve()
    bash_code = f"""
set +e +u +o pipefail
export HOME={_bash_quote(home)}
export DOMI_PYTHON={_bash_quote(domi_python)}
export DOMI_NODE={_bash_quote(domi_node)}
export SEEKTALENT_WTSCLI_PREPARED_RUNTIME={_bash_quote(_write_runtime_marker(tmp_path))}
export PYTHONPATH="before-pythonpath"
before_flags="$-"
before_pipefail="$(set -o | awk '$1 == "pipefail" {{ print $2 }}')"
before_pythonpath="$PYTHONPATH"
source {_bash_quote(script)} 0.7.25 {_bash_quote(wtscli_bundle)} >/dev/null
install_status=$?
after_flags="$-"
after_pipefail="$(set -o | awk '$1 == "pipefail" {{ print $2 }}')"
if [[ "$after_flags" != "$before_flags" ]]; then
  echo "flags changed: before=$before_flags after=$after_flags" >&2
  exit 41
fi
if [[ "$after_pipefail" != "$before_pipefail" ]]; then
  echo "pipefail changed: before=$before_pipefail after=$after_pipefail" >&2
  exit 42
fi
if [[ "$PYTHONPATH" != "$before_pythonpath" ]]; then
  echo "PYTHONPATH changed: before=$before_pythonpath after=$PYTHONPATH" >&2
  exit 43
fi
if [[ "$install_status" -ne 1 ]]; then
  echo "missing exact delivery should fail before install" >&2
  exit 44
fi
if [[ -e "$HOME/.seektalent" ]]; then
  echo "target changed before exact delivery validation" >&2
  exit 45
fi
exit 0
"""

    result = subprocess.run(["bash", "-c", bash_code], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr
    assert "delivery_manifest_missing" in result.stderr


def test_posix_install_script_uses_the_explicit_host_python_path(tmp_path: Path) -> None:
    home = tmp_path / "home"
    domi_python = home / "Library" / "Application Support" / "Domi" / "runtime" / "python" / "bin" / "python"
    domi_node = tmp_path / "Domi.app" / "node" / "runtime" / "bin" / "node"
    domi_python.parent.mkdir(parents=True)
    domi_node.parent.mkdir(parents=True)
    python_capture = tmp_path / "python-capture.txt"
    wtscli_bundle = _write_bundle_marker(tmp_path)
    domi_python.write_text(
        f"""#!/usr/bin/env bash
printf "%s" "$0" > {_bash_quote(python_capture)}
if [[ "${{1:-}}" == *"install_staging_browser_bridge.py" ]]; then
  exec {_bash_quote(sys.executable)} "$@"
fi
if [[ "${{1:-}} ${{2:-}}" == "-m pip" ]]; then
  exit 0
fi
if [[ "${{1:-}} ${{2:-}}" == "-m zipfile" ]]; then
  exit 0
fi
if [[ "${{1:-}} ${{2:-}}" == "-m seektalent.domi_bootstrap" ]]; then
  echo '{{}}'
  exit 0
fi
echo "unexpected fake Domi Python invocation: $*" >&2
exit 2
""",
        encoding="utf-8",
    )
    domi_node.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    domi_python.chmod(0o755)
    domi_node.chmod(0o755)
    script = tmp_path / "install-seektalent-domi.sh"
    script.write_text(Path("scripts/install-seektalent-domi.sh").read_text(encoding="utf-8"), encoding="utf-8")
    bash_code = f"""
set +e +u +o pipefail
export HOME={_bash_quote(home)}
export DOMI_PYTHON={_bash_quote(domi_python)}
export DOMI_NODE={_bash_quote(domi_node)}
export DOMI_NODE={_bash_quote(domi_node)}
export SEEKTALENT_WTSCLI_PREPARED_RUNTIME={_bash_quote(_write_runtime_marker(tmp_path))}
export SEEKTALENT_BROWSER_BRIDGE_HELPER={_bash_quote(Path("scripts/install_staging_browser_bridge.py").resolve())}
source {_bash_quote(script)} 0.7.25 {_bash_quote(wtscli_bundle)} >/dev/null
"""

    result = subprocess.run(["bash", "-c", bash_code], capture_output=True, text=True, check=False)

    assert result.returncode == 1
    assert "delivery_manifest_missing" in result.stderr
    assert python_capture.read_text(encoding="utf-8") == str(domi_python)


def test_posix_install_script_accepts_seektalent_domi_node_alias(tmp_path: Path) -> None:
    home = tmp_path / "home"
    domi_python = tmp_path / "Domi.app" / "python" / "runtime" / "bin" / "python"
    domi_node = tmp_path / "custom-domi" / "node" / "bin" / "node"
    domi_python.parent.mkdir(parents=True)
    domi_node.parent.mkdir(parents=True)
    home.mkdir()
    wtscli_bundle = _write_bundle_marker(tmp_path)
    domi_python.write_text(
        f"""#!/usr/bin/env bash
if [[ "${{1:-}}" == *"install_staging_browser_bridge.py" ]]; then
  exec {_bash_quote(sys.executable)} "$@"
fi
if [[ "${{1:-}} ${{2:-}}" == "-m pip" ]]; then
  exit 0
fi
if [[ "${{1:-}} ${{2:-}}" == "-m zipfile" ]]; then
  exit 0
fi
if [[ "${{1:-}} ${{2:-}}" == "-m seektalent.domi_bootstrap" ]]; then
  while [[ $# -gt 0 ]]; do
    if [[ "$1" == "--domi-node" ]]; then
          echo '{{}}'
      exit 0
    fi
    shift
  done
fi
echo "unexpected fake Domi Python invocation: $*" >&2
exit 2
""",
        encoding="utf-8",
    )
    domi_node.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    domi_python.chmod(0o755)
    domi_node.chmod(0o755)
    script = Path("scripts/install-seektalent-domi.sh").resolve()
    bash_code = f"""
set +e +u +o pipefail
export HOME={_bash_quote(home)}
export DOMI_PYTHON={_bash_quote(domi_python)}
unset DOMI_NODE
export SEEKTALENT_DOMI_NODE={_bash_quote(domi_node)}
export SEEKTALENT_WTSCLI_PREPARED_RUNTIME={_bash_quote(_write_runtime_marker(tmp_path))}
source {_bash_quote(script)} 0.7.25 {_bash_quote(wtscli_bundle)} >/dev/null
"""

    result = subprocess.run(["bash", "-c", bash_code], capture_output=True, text=True, check=False)

    assert result.returncode == 1
    assert "delivery_manifest_missing" in result.stderr
    script_text = Path("scripts/install-seektalent-domi.sh").read_text(encoding="utf-8")
    assert 'DOMI_NODE:-${SEEKTALENT_DOMI_NODE:-}' in script_text


def _write_bundle_marker(root: Path) -> Path:
    bundle = root / "wtscli-bundle"
    write_browser_bridge_bundle(bundle)
    return bundle


def _write_runtime_marker(root: Path) -> Path:
    runtime = root / "wtscli-runtime.zip"
    runtime.write_bytes(b"prepared runtime fixture")
    return runtime


def _bash_quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "'\"'\"'") + "'"
