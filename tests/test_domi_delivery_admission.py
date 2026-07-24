from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from seektalent import browser_bridge_install, domi_bootstrap
from tests.browser_bridge_bundle_fixtures import (
    WTSCLI_BUILD_ID,
    WTSCLI_EXTENSION_ID,
    write_browser_bridge_bundle,
)


ROOT = Path(__file__).resolve().parents[1]
POSIX_INSTALLER = ROOT / "scripts" / "install-seektalent-domi.sh"
POWERSHELL_INSTALLER = ROOT / "scripts" / "install-seektalent-domi.ps1"
OFFLINE_INSTALLER = ROOT / "scripts" / "offline" / "install-offline-macos-intel.sh"


def test_posix_delivery_requires_explicit_bundle_before_target_mutation(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    sentinel = home / ".seektalent" / "python-prefix" / "0.7.49" / "sentinel"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("previous-install", encoding="utf-8")
    before = _snapshot(home)
    command = f"""
export HOME={_shell_quote(home)}
export DOMI_PYTHON={_shell_quote(sys.executable)}
export DOMI_NODE={_shell_quote(sys.executable)}
unset SEEKTALENT_WTSCLI_BUNDLE_DIR
source {_shell_quote(POSIX_INSTALLER)} 0.7.49
"""

    completed = subprocess.run(
        ("bash", "-c", command),
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "reason_code=wtscli_bundle_missing" in completed.stderr
    assert _snapshot(home) == before


@pytest.mark.parametrize("mutation", ["legacy_identity", "tampered_runtime"])
def test_bootstrap_rejects_invalid_bundle_before_target_mutation(
    tmp_path: Path,
    mutation: str,
) -> None:
    home = tmp_path / "home"
    sentinel = home / ".seektalent" / "python-prefix" / "0.7.49" / "sentinel"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("previous-install", encoding="utf-8")
    bundle = tmp_path / "bundle"
    manifest = write_browser_bridge_bundle(bundle)
    if mutation == "legacy_identity":
        manifest["implementation"] = "seektalent-opencli"
        (bundle / "bridge-manifest.json").write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )
    else:
        (bundle / "runtime" / "wtscli-0.1.0.tgz").write_bytes(b"tampered")
    before = _snapshot(home)

    with pytest.raises(
        domi_bootstrap.DomiBootstrapError,
        match="exact SeekTalent WTSCLI browser bridge bundle",
    ):
        domi_bootstrap.bootstrap_domi_workbench(
            home=home,
            platform="darwin",
            domi_python=Path(sys.executable),
            domi_node=Path(sys.executable),
            browser_bridge_bundle_dir=bundle,
        )

    assert _snapshot(home) == before


def test_bootstrap_failed_activation_rolls_back_python_shims_and_wts_pair(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    bundle = tmp_path / "bundle"
    write_browser_bridge_bundle(bundle)
    prefix_target = home / ".seektalent" / "python-prefix" / "0.7.49"
    first_candidate = tmp_path / "candidate-one"
    second_candidate = tmp_path / "candidate-two"
    (first_candidate / "site-packages").mkdir(parents=True)
    (second_candidate / "site-packages").mkdir(parents=True)
    (first_candidate / "site-packages" / "candidate.txt").write_text(
        "first",
        encoding="utf-8",
    )
    (second_candidate / "site-packages" / "candidate.txt").write_text(
        "second",
        encoding="utf-8",
    )

    domi_bootstrap.bootstrap_domi_workbench(
        home=home,
        platform="darwin",
        domi_python=Path(sys.executable),
        domi_node=Path(sys.executable),
        python_paths=(prefix_target / "site-packages",),
        package_version="0.7.49",
        browser_bridge_bundle_dir=bundle,
        python_prefix_candidate=first_candidate,
        python_prefix_target=prefix_target,
    )
    before = _snapshot(home)
    real_replace = browser_bridge_install.os.replace
    failed = False
    bin_target = home / ".seektalent" / "bin"

    def fail_bin_activation(source: os.PathLike[str], target: os.PathLike[str]) -> None:
        nonlocal failed
        source_path = Path(source)
        target_path = Path(target)
        if (
            not failed
            and target_path == bin_target
            and ".previous-" not in source_path.name
        ):
            failed = True
            raise OSError("injected late activation failure")
        real_replace(source, target)

    monkeypatch.setattr(browser_bridge_install.os, "replace", fail_bin_activation)

    with pytest.raises(domi_bootstrap.DomiBootstrapError):
        domi_bootstrap.bootstrap_domi_workbench(
            home=home,
            platform="darwin",
            domi_python=Path(sys.executable),
            domi_node=Path(sys.executable),
            python_paths=(prefix_target / "site-packages",),
            package_version="0.7.49",
            browser_bridge_bundle_dir=bundle,
            python_prefix_candidate=second_candidate,
            python_prefix_target=prefix_target,
        )

    assert failed is True
    assert _snapshot(home) == before


@pytest.mark.parametrize("mutation", ["legacy_identity", "tampered_runtime"])
def test_posix_delivery_rejects_invalid_bundle_before_pip_or_target_mutation(
    tmp_path: Path,
    mutation: str,
) -> None:
    home = tmp_path / "home"
    sentinel = home / ".seektalent" / "python-prefix" / "0.7.49" / "sentinel"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("previous-install", encoding="utf-8")
    bundle = _invalid_bundle(tmp_path / "invalid-bundle", mutation=mutation)
    log = tmp_path / "python-invocations.log"
    python_wrapper = tmp_path / "domi-python"
    python_wrapper.write_text(
        f"""#!/usr/bin/env bash
printf '%s\n' "$*" >> {_shell_quote(log)}
if [[ "${{1:-}}" == *"install_staging_browser_bridge.py" ]]; then
  exec {_shell_quote(sys.executable)} "$@"
fi
echo "unexpected pre-admission process: $*" >&2
exit 97
""",
        encoding="utf-8",
    )
    python_wrapper.chmod(0o755)
    before = _snapshot(home)
    command = f"""
export HOME={_shell_quote(home)}
export DOMI_PYTHON={_shell_quote(python_wrapper)}
export DOMI_NODE={_shell_quote(sys.executable)}
source {_shell_quote(POSIX_INSTALLER)} 0.7.49 {_shell_quote(bundle)}
"""

    completed = subprocess.run(
        ("bash", "-c", command),
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "-m pip" not in log.read_text(encoding="utf-8")
    assert _snapshot(home) == before


@pytest.mark.skipif(os.name != "nt", reason="PowerShell delivery executes on Windows CI")
@pytest.mark.parametrize("mutation", ["legacy_identity", "tampered_runtime"])
def test_powershell_delivery_rejects_invalid_bundle_before_pip_or_target_mutation(
    tmp_path: Path,
    mutation: str,
) -> None:
    home = tmp_path / "home"
    sentinel = home / ".seektalent" / "python-prefix" / "0.7.49" / "sentinel"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("previous-install", encoding="utf-8")
    bundle = _invalid_bundle(tmp_path / "invalid-bundle", mutation=mutation)
    pip_log = tmp_path / "pip.log"
    before = _snapshot(home)
    env = {
        **os.environ,
        "USERPROFILE": str(home),
        "APPDATA": str(tmp_path / "appdata"),
        "PIP_NO_INDEX": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_LOG": str(pip_log),
    }

    completed = subprocess.run(
        (
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(POWERSHELL_INSTALLER),
            "-Version",
            "0.7.49",
            "-DomiPython",
            sys.executable,
            "-DomiNode",
            sys.executable,
            "-WtscliBundleDir",
            str(bundle),
        ),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert not pip_log.exists()
    assert _snapshot(home) == before


@pytest.mark.parametrize("mutation", ["legacy_identity", "tampered_runtime"])
def test_offline_delivery_rejects_invalid_bundle_before_pip_or_target_mutation(
    tmp_path: Path,
    mutation: str,
) -> None:
    bundle_root = tmp_path / "offline"
    bundle_root.mkdir()
    installer = bundle_root / "install-offline.sh"
    shutil.copy2(OFFLINE_INSTALLER, installer)
    installer.chmod(0o755)
    _write_offline_resources(bundle_root, mutation=mutation)
    home = tmp_path / "home"
    sentinel = home / ".seektalent" / "python-prefix" / "0.7.49" / "sentinel"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("previous-install", encoding="utf-8")
    log = tmp_path / "offline-python-invocations.log"
    python_wrapper = tmp_path / "offline-domi-python"
    python_wrapper.write_text(
        f"""#!/usr/bin/env bash
printf '%s\n' "$*" >> {_shell_quote(log)}
if [[ "${{1:-}}" == "-c" && "${{2:-}}" == *"platform.machine"* ]]; then
  echo x86_64
  exit 0
fi
if [[ "${{1:-}}" == "-c" && "${{2:-}}" == *"sys.version_info.major"* ]]; then
  echo 3.13
  exit 0
fi
if [[ "${{1:-}}" == *"/pip.pyz" ]]; then
  exit 0
fi
if [[ "${{1:-}} ${{2:-}}" == "-m seektalent.domi_bootstrap" ]]; then
  export PYTHONPATH={_shell_quote(ROOT / "src")}${{PYTHONPATH:+:$PYTHONPATH}}
fi
exec {_shell_quote(sys.executable)} "$@"
""",
        encoding="utf-8",
    )
    python_wrapper.chmod(0o755)
    before = _snapshot(home)
    command = f"""
export HOME={_shell_quote(home)}
export DOMI_PYTHON={_shell_quote(python_wrapper)}
export DOMI_NODE={_shell_quote(sys.executable)}
export PYTHONPATH={_shell_quote(ROOT / "src")}
source {_shell_quote(installer)}
"""

    completed = subprocess.run(
        ("bash", "-c", command),
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "/pip.pyz install" not in log.read_text(encoding="utf-8")
    assert _snapshot(home) == before


def test_offline_checksums_are_verified_before_wheel_admission_code_runs(
    tmp_path: Path,
) -> None:
    bundle_root = tmp_path / "offline"
    bundle_root.mkdir()
    installer = bundle_root / "install-offline.sh"
    shutil.copy2(OFFLINE_INSTALLER, installer)
    installer.chmod(0o755)
    _write_offline_resources(bundle_root)
    app_wheel = bundle_root / "python-wheelhouse" / "seektalent-0.7.49-py3-none-any.whl"
    app_wheel.write_bytes(app_wheel.read_bytes() + b"tampered")
    log = tmp_path / "python-invocations.log"
    python_wrapper = tmp_path / "offline-domi-python"
    python_wrapper.write_text(
        f"""#!/usr/bin/env bash
printf '%s\n' "$*" >> {_shell_quote(log)}
exit 99
""",
        encoding="utf-8",
    )
    python_wrapper.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    before = _snapshot(home)
    command = f"""
export HOME={_shell_quote(home)}
export DOMI_PYTHON={_shell_quote(python_wrapper)}
export DOMI_NODE={_shell_quote(sys.executable)}
source {_shell_quote(installer)}
"""

    completed = subprocess.run(
        ("bash", "-c", command),
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "reason_code=offline_bundle_checksum_mismatch" in completed.stderr
    assert not log.exists()
    assert _snapshot(home) == before


def test_offline_candidate_version_failure_precedes_activation(
    tmp_path: Path,
) -> None:
    bundle_root = tmp_path / "offline"
    bundle_root.mkdir()
    installer = bundle_root / "install-offline.sh"
    shutil.copy2(OFFLINE_INSTALLER, installer)
    installer.chmod(0o755)
    _write_offline_resources(bundle_root, mutation="valid")
    home = tmp_path / "home"
    sentinel = home / ".seektalent" / "python-prefix" / "0.7.49" / "sentinel"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("previous-install", encoding="utf-8")
    log = tmp_path / "offline-python-invocations.log"
    python_wrapper = tmp_path / "offline-domi-python"
    python_wrapper.write_text(
        f"""#!/usr/bin/env bash
printf '%s\n' "$*" >> {_shell_quote(log)}
if [[ "${{1:-}}" == "-c" && "${{2:-}}" == *"platform.machine"* ]]; then
  echo x86_64
  exit 0
fi
if [[ "${{1:-}}" == "-c" && "${{2:-}}" == *"sys.version_info.major"* ]]; then
  echo 3.13
  exit 0
fi
if [[ "${{1:-}}" == *"/pip.pyz" ]]; then
  exit 0
fi
if [[ "${{1:-}} ${{2:-}}" == "-m seektalent" ]]; then
  echo wrong-version
  exit 0
fi
if [[ "${{1:-}} ${{2:-}}" == "-m seektalent.domi_bootstrap" ]]; then
  exit 0
fi
exec {_shell_quote(sys.executable)} "$@"
""",
        encoding="utf-8",
    )
    python_wrapper.chmod(0o755)
    before = _snapshot(home)
    command = f"""
export HOME={_shell_quote(home)}
export DOMI_PYTHON={_shell_quote(python_wrapper)}
export DOMI_NODE={_shell_quote(sys.executable)}
source {_shell_quote(installer)}
"""

    completed = subprocess.run(
        ("bash", "-c", command),
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    invocations = log.read_text(encoding="utf-8")
    assert "-m seektalent --version" in invocations
    assert "-m seektalent.domi_bootstrap" not in invocations
    assert _snapshot(home) == before


def _invalid_bundle(root: Path, *, mutation: str) -> Path:
    manifest = write_browser_bridge_bundle(root)
    if mutation == "valid":
        return root
    if mutation == "legacy_identity":
        manifest["implementation"] = "seektalent-opencli"
        (root / "bridge-manifest.json").write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )
    else:
        assert mutation == "tampered_runtime"
        (root / "runtime" / "wtscli-0.1.0.tgz").write_bytes(b"tampered")
    return root


def _write_offline_resources(
    bundle_root: Path,
    *,
    mutation: str = "legacy_identity",
) -> None:
    bridge = _invalid_bundle(
        bundle_root / "wtscli-browser-bridge",
        mutation=mutation,
    )
    wheelhouse = bundle_root / "python-wheelhouse"
    tools = bundle_root / "tools"
    runtime_dir = bundle_root / "wtscli-runtime"
    wheelhouse.mkdir()
    tools.mkdir()
    runtime_dir.mkdir()
    app_wheel = wheelhouse / "seektalent-0.7.49-py3-none-any.whl"
    _write_admission_wheel(app_wheel)
    (tools / "pip.pyz").write_bytes(b"test pip zipapp")
    runtime_archive = runtime_dir / "wtscli-0.1.0-runtime.zip"
    with zipfile.ZipFile(runtime_archive, "w") as archive:
        archive.writestr("node_modules/wtscli/package.json", "{}\n")
    manifest = {
        "schema_version": 1,
        "platform": "macos-x86_64",
        "python_version": "3.13",
        "seektalent_version": "0.7.49",
        "wtscli_version": "0.1.0",
        "extension_version": "0.1.0",
        "browser_bridge_bundle": bridge.name,
        "browser_bridge_runtime": f"wtscli-runtime/{runtime_archive.name}",
        "browser_bridge_runtime_sha256": _sha256(runtime_archive),
        "browser_bridge_build_id": WTSCLI_BUILD_ID,
        "browser_bridge_fork_commit": "709622fc3fb3463f15551467fdf0d28571dfd049",
        "browser_bridge_extension_id": WTSCLI_EXTENSION_ID,
    }
    (bundle_root / "bundle-manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    checksum_path = bundle_root / "SHA256SUMS"
    files = sorted(path for path in bundle_root.rglob("*") if path.is_file())
    checksum_path.write_text(
        "".join(
            f"{_sha256(path)}  {path.relative_to(bundle_root).as_posix()}\n"
            for path in files
        ),
        encoding="utf-8",
    )


def _write_admission_wheel(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for filename in (
            "__init__.py",
            "version.py",
            "strict_json.py",
            "browser_bridge_manifest.py",
        ):
            archive.write(
                ROOT / "src" / "seektalent" / filename,
                f"seektalent/{filename}",
            )


def _snapshot(root: Path) -> tuple[tuple[str, str, bytes], ...]:
    if not root.exists():
        return ()
    entries: list[tuple[str, str, bytes]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            entries.append((relative, "symlink", os.readlink(path).encode()))
        elif path.is_dir():
            entries.append((relative, "dir", b""))
        else:
            entries.append((relative, "file", path.read_bytes()))
    return tuple(entries)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _shell_quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "'\"'\"'") + "'"
