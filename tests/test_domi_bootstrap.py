from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from seektalent import domi_bootstrap


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
    site_packages = tmp_path / "home" / ".seektalent" / "python-prefix" / "0.7.24" / "Lib" / "site-packages"
    legacy_ps1 = _touch(home / ".seektalent" / "seektalent.ps1")
    legacy_cmd = _touch(home / ".seektalent" / "seektalent.cmd")

    result = domi_bootstrap.bootstrap_domi_workbench(
        home=home,
        platform="win32",
        domi_python=domi_python,
        domi_node=domi_node,
        python_paths=(site_packages,),
        package_version="0.7.24",
    )

    ps1 = result.bin_dir / "seektalent.ps1"
    cmd = result.bin_dir / "seektalent.cmd"
    assert ps1.exists()
    assert cmd.exists()
    assert not legacy_ps1.exists()
    assert not legacy_cmd.exists()
    assert result.command_name == "seektalent"
    assert result.package_version == "0.7.24"

    ps1_text = ps1.read_text(encoding="utf-8")
    assert ps1_text.startswith('$ErrorActionPreference = "Stop"')
    assert "`$DomiPython" not in ps1_text
    assert str(domi_python) in ps1_text
    assert str(domi_node) in ps1_text
    assert str(site_packages) in ps1_text
    assert "SEEKTALENT_DOMI_NODE" in ps1_text
    assert "-m seektalent.domi_workbench" in ps1_text
    assert "-m seektalent @args" in ps1_text

    cmd_text = cmd.read_text(encoding="utf-8")
    assert "seektalent.ps1" in cmd_text


def test_bootstrap_writes_posix_shim_with_domi_python_node_and_pythonpath(tmp_path: Path) -> None:
    home = tmp_path / "home"
    domi_python = _touch_executable(tmp_path / "Domi.app" / "python" / "runtime" / "bin" / "python")
    domi_node = _touch_executable(tmp_path / "Domi.app" / "node" / "runtime" / "bin" / "node")
    site_packages = home / ".seektalent" / "python-prefix" / "0.7.24" / "site-packages"

    result = domi_bootstrap.bootstrap_domi_workbench(
        home=home,
        platform="darwin",
        domi_python=domi_python,
        domi_node=domi_node,
        python_paths=(site_packages,),
        package_version="0.7.24",
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
    assert "-m seektalent \"$@\"" in text


def test_resolve_domi_node_uses_windows_default_appdata_path(tmp_path: Path) -> None:
    appdata = tmp_path / "AppData" / "Roaming"
    expected = _touch(appdata / "Domi" / "runtime" / "node" / "node.exe")

    assert domi_bootstrap.resolve_domi_node(env={"APPDATA": str(appdata)}, platform="win32", home=tmp_path) == expected


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
                "0.7.24",
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
    assert "seektalent==$Version" in windows_script
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
    assert "seektalent==${version}" in mac_script
    assert "--no-deps" not in mac_script
    assert "--ignore-installed" in mac_script
    assert "set -euo pipefail" not in mac_script
    assert "-m seektalent.domi_bootstrap" in mac_script
    assert "--python-path" in mac_script
    assert "export PATH=" in mac_script


def test_posix_install_script_preserves_sourced_shell_state(tmp_path: Path) -> None:
    home = tmp_path / "home"
    domi_python = tmp_path / "Domi.app" / "python" / "runtime" / "bin" / "python"
    domi_node = tmp_path / "Domi.app" / "node" / "runtime" / "bin" / "node"
    domi_python.parent.mkdir(parents=True)
    domi_node.parent.mkdir(parents=True)
    home.mkdir()
    domi_python.write_text(
        """#!/usr/bin/env bash
if [[ "${1:-} ${2:-}" == "-m pip" ]]; then
  exit 0
fi
if [[ "${1:-} ${2:-}" == "-m seektalent.domi_bootstrap" ]]; then
  echo '{}'
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
export PYTHONPATH="before-pythonpath"
before_flags="$-"
before_pipefail="$(set -o | awk '$1 == "pipefail" {{ print $2 }}')"
before_pythonpath="$PYTHONPATH"
source {_bash_quote(script)} 0.7.24 >/dev/null
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
case ":$PATH:" in
  *":$HOME/.seektalent/bin:"*) exit 0 ;;
  *) echo "PATH missing seektalent bin: $PATH" >&2; exit 44 ;;
esac
"""

    result = subprocess.run(["bash", "-c", bash_code], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr


def test_posix_install_script_accepts_seektalent_domi_node_alias(tmp_path: Path) -> None:
    home = tmp_path / "home"
    domi_python = tmp_path / "Domi.app" / "python" / "runtime" / "bin" / "python"
    domi_node = tmp_path / "custom-domi" / "node" / "bin" / "node"
    domi_python.parent.mkdir(parents=True)
    domi_node.parent.mkdir(parents=True)
    home.mkdir()
    node_capture = tmp_path / "node-capture.txt"
    domi_python.write_text(
        f"""#!/usr/bin/env bash
if [[ "${{1:-}} ${{2:-}}" == "-m pip" ]]; then
  exit 0
fi
if [[ "${{1:-}} ${{2:-}}" == "-m seektalent.domi_bootstrap" ]]; then
  while [[ $# -gt 0 ]]; do
    if [[ "$1" == "--domi-node" ]]; then
      printf "%s" "$2" > {_bash_quote(node_capture)}
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
source {_bash_quote(script)} 0.7.24 >/dev/null
"""

    result = subprocess.run(["bash", "-c", bash_code], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr
    assert node_capture.read_text(encoding="utf-8") == str(domi_node)


def _bash_quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "'\"'\"'") + "'"
