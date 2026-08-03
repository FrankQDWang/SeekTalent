from __future__ import annotations

import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

from scripts import build_domi_delivery_bundle as delivery_module
from scripts.build_domi_delivery_bundle import build_delivery_bundle
from seektalent.domi_host_runtime import HostRuntimeIdentity, validate_delivery_payload
from seektalent import release_source
from tests.browser_bridge_bundle_fixtures import (
    WTSCLI_BUILD_ID,
    write_browser_bridge_bundle,
)


def _windows_identity() -> HostRuntimeIdentity:
    return HostRuntimeIdentity(
        platform="windows-x64",
        os_family="windows",
        architecture="x64",
        python_executable=r"C:\Domi\python.exe",
        python_version="3.13.7",
        python_implementation="cpython",
        python_cache_tag="cpython-313",
        python_soabi="cp313-win_amd64",
        python_executable_sha256="a" * 64,
        node_executable=r"C:\Domi\node.exe",
        node_version="22.14.0",
        node_executable_sha256="b" * 64,
    )


def test_source_revision_rejects_a_dirty_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(command: list[str], **_kwargs: object):
        if command[1:] == ["rev-parse", "HEAD"]:
            return release_source.subprocess.CompletedProcess(
                command,
                0,
                stdout="a" * 40 + "\n",
                stderr="",
            )
        if command[1:] == ["status", "--porcelain"]:
            return release_source.subprocess.CompletedProcess(
                command,
                0,
                stdout=" M src/seektalent/version.py\n",
                stderr="",
            )
        raise AssertionError(command)

    monkeypatch.setattr(release_source.subprocess, "run", run)

    with pytest.raises(RuntimeError, match="source_checkout_not_clean"):
        release_source.source_revision(delivery_module.ROOT)


def test_build_delivery_bundle_contains_exact_pair_runtime_and_platform_installer(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "wtscli-browser-bridge"
    write_browser_bridge_bundle(bundle)
    wheel = tmp_path / "seektalent-0.8.1-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    (wheelhouse / "dependency-1.0-py3-none-any.whl").write_bytes(b"dependency")

    archive = build_delivery_bundle(
        output_dir=tmp_path / "dist",
        browser_bridge_bundle=bundle,
        seektalent_wheel=wheel,
        wheelhouse_dir=wheelhouse,
        domi_python=None,
        node=Path(sys.executable),
        platform_name="windows-x64",
        source_revision="a" * 40,
        host_runtime_identity=_windows_identity(),
    )

    with zipfile.ZipFile(archive) as delivery:
        names = set(delivery.namelist())
        root = archive.stem
        assert f"{root}/install-seektalent-domi.ps1" in names
        assert f"{root}/start-seektalent-domi.ps1" in names
        assert f"{root}/install_staging_browser_bridge.py" in names
        assert f"{root}/wtscli-browser-bridge/bridge-manifest.json" in names
        assert f"{root}/wtscli-browser-bridge/extension/manifest.json" in names
        assert f"{root}/wtscli-runtime.zip" in names
        assert f"{root}/{wheel.name}" in names
        assert f"{root}/python-wheelhouse/dependency-1.0-py3-none-any.whl" in names
        assert f"{root}/acceptance/liepin-ai-agent-engineer-v1.json" in names
        assert f"{root}/verify_domi_host_runtime.py" in names
        assert f"{root}/SHA256SUMS" in names
        manifest = json.loads(delivery.read(f"{root}/delivery-manifest.json"))
        assert manifest["schema_version"] == 2
        assert manifest["bridge_build_id"] == WTSCLI_BUILD_ID
        assert manifest["platform"] == "windows-x64"
        assert manifest["extension_directory"] == "~/.seektalent/chrome-extension/wtscli"
        assert manifest["product_version"] == "0.8.1"
        assert manifest["source_revision"] == "a" * 40
        assert manifest["product_build_id"] == (
            "seektalent-0.8.1+" + "a" * 40
        )
        assert manifest["startup_script"] == "start-seektalent-domi.ps1"
        assert manifest["startup_contract"] == {
            "jwt_env": "SEEKTALENT_DOMI_JWT",
            "python_env": "DOMI_PYTHON",
            "node_env": "DOMI_NODE",
        }
        assert manifest["host_runtime_contract"]["python_major_minor"] == "3.13"
        assert manifest["host_runtime_contract"]["node_version"] == "22.14.0"
        assert manifest["acceptance_fixture"]["schema_version"] == (
            "seektalent.acceptance-fixture.v1"
        )
        listed = {entry["path"]: entry for entry in manifest["files"]}
        assert listed[wheel.name]["sha256"] == hashlib.sha256(b"wheel").hexdigest()

    checksum = archive.with_name(f"{archive.name}.sha256")
    assert checksum.read_text(encoding="utf-8").endswith(f"  {archive.name}\n")
    assert b"\r\n" not in checksum.read_bytes()


def test_exact_head_native_delivery_archive_contains_the_bound_pair() -> None:
    raw_archive = os.environ.get("SEEKTALENT_NATIVE_DELIVERY_ARCHIVE")
    expected_platform = os.environ.get("SEEKTALENT_NATIVE_DELIVERY_PLATFORM")
    if not raw_archive or not expected_platform:
        pytest.skip("exact-head native delivery artifact is only available in native CI")

    archive = Path(raw_archive)
    assert archive.is_file()
    with zipfile.ZipFile(archive) as delivery:
        assert delivery.testzip() is None
        names = set(delivery.namelist())
        root = archive.stem
        manifest_path = f"{root}/delivery-manifest.json"
        manifest = json.loads(delivery.read(manifest_path))
        assert manifest["platform"] == expected_platform
        assert manifest["bridge_build_id"] == WTSCLI_BUILD_ID
        assert manifest["extension_directory"] == "~/.seektalent/chrome-extension/wtscli"
        assert f"{root}/wtscli-browser-bridge/extension/manifest.json" in names
        installer = (
            "install-seektalent-domi.ps1"
            if expected_platform == "windows-x64"
            else "install-seektalent-domi.sh"
        )
        startup_script = (
            "start-seektalent-domi.ps1"
            if expected_platform == "windows-x64"
            else "start-seektalent-domi.sh"
        )
        assert f"{root}/{installer}" in names
        assert f"{root}/{startup_script}" in names
        for path_key, hash_key in (
            ("browser_bridge_bundle", "browser_bridge_manifest_sha256"),
            ("prepared_runtime", "prepared_runtime_sha256"),
            ("seektalent_wheel", "seektalent_wheel_sha256"),
        ):
            relative = str(manifest[path_key])
            if path_key == "browser_bridge_bundle":
                relative += "/bridge-manifest.json"
            payload = delivery.read(f"{root}/{relative}")
            assert hashlib.sha256(payload).hexdigest() == manifest[hash_key]


def test_delivery_payload_tampering_is_rejected(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    write_browser_bridge_bundle(bundle)
    wheel = tmp_path / "seektalent-0.8.1-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    dependency = wheelhouse / "dependency-1.0-py3-none-any.whl"
    dependency.write_bytes(b"dependency")
    archive = build_delivery_bundle(
        output_dir=tmp_path / "dist",
        browser_bridge_bundle=bundle,
        seektalent_wheel=wheel,
        wheelhouse_dir=wheelhouse,
        domi_python=None,
        node=Path(sys.executable),
        platform_name="windows-x64",
        source_revision="a" * 40,
        host_runtime_identity=_windows_identity(),
    )
    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(archive) as delivery:
        delivery.extractall(extracted)
    root = extracted / archive.stem
    manifest = json.loads((root / "delivery-manifest.json").read_text(encoding="utf-8"))
    assert b"\r\n" not in (root / "SHA256SUMS").read_bytes()
    assert validate_delivery_payload(root, manifest) is None

    (root / "python-wheelhouse" / dependency.name).write_bytes(b"tampered")
    assert validate_delivery_payload(root, manifest) == "delivery_payload_hash_mismatch"


def test_delivery_uses_one_cross_platform_builder_and_workflow() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = (
        root / ".github" / "workflows" / "native-launch-binding-probe.yml"
    ).read_text(encoding="utf-8")

    assert not (root / ".github" / "workflows" / "build-macos-intel-offline.yml").exists()
    assert not (root / "scripts" / "build_offline_macos_intel.py").exists()
    assert "scripts/build_domi_delivery_bundle.py" in workflow
    assert "build-exact-seektalent-wheel:" in workflow
    assert "name: exact-seektalent-wheel" in workflow
    assert "delivery_platform: windows-x64" in workflow
    assert "delivery_platform: macos-x86_64" in workflow
    assert "${{ runner.temp }}/seektalent-native" in workflow
    assert "NATIVE_BUILD_ROOT: ${{ runner.temp }}" not in workflow
    assert 'Path("dist/wheel")' not in workflow
    assert 'Path("dist/wheelhouse")' not in workflow
    assert '"dist/delivery"' not in workflow
    assert workflow.count("path: wtscli-browser-bridge") == 1


def test_delivery_installers_default_to_the_adjacent_exact_product_bundle() -> None:
    root = Path(__file__).resolve().parents[1]
    posix = (root / "scripts" / "install-seektalent-domi.sh").read_text(encoding="utf-8")
    powershell = (root / "scripts" / "install-seektalent-domi.ps1").read_text(
        encoding="utf-8"
    )

    assert '${script_dir}/wtscli-browser-bridge' in posix
    assert '${script_dir}/wtscli-runtime.zip' in posix
    assert "wtscli-browser-bridge" in powershell
    assert "wtscli-runtime.zip" in powershell
    assert "--browser-bridge-prepared-runtime-dir" in posix
    assert "--browser-bridge-prepared-runtime-dir" in powershell
    assert "npm install" not in posix
    assert "npm install" not in powershell


def test_acceptance_fixture_checkout_is_forced_to_lf() -> None:
    root = Path(__file__).resolve().parents[1]
    attributes = (root / ".gitattributes").read_text(encoding="utf-8").splitlines()
    fixture = root / "acceptance" / "fixtures" / "liepin-ai-agent-engineer-v1.json"

    assert "acceptance/fixtures/*.json text eol=lf" in attributes
    assert b"\r\n" not in fixture.read_bytes()


def test_host_start_scripts_require_domi_runtime_and_use_installed_package() -> None:
    root = Path(__file__).resolve().parents[1]
    posix = (root / "scripts" / "start-seektalent-domi.sh").read_text(encoding="utf-8")
    powershell = (root / "scripts" / "start-seektalent-domi.ps1").read_text(
        encoding="utf-8"
    )

    for script in (posix, powershell):
        assert "SEEKTALENT_DOMI_JWT" in script
        assert "DOMI_PYTHON" in script
        assert "DOMI_NODE" in script
        assert "install-receipt" in script
        assert "wtscli-runtime" in script
        assert "chrome-extension" in script
        assert "workbench" in script
        assert "19826" not in script
        assert "Domi.app" not in script
        assert "daemon restart" not in script
        assert "verify_domi_host_runtime.py" in script
        assert "validate-receipt" in script


def test_installers_print_one_fixed_extension_directory_and_chinese_steps() -> None:
    root = Path(__file__).resolve().parents[1]
    combined = "\n".join(
        (
            (root / "scripts" / "install-seektalent-domi.sh").read_text(
                encoding="utf-8"
            ),
            (root / "scripts" / "install-seektalent-domi.ps1").read_text(
                encoding="utf-8"
            ),
            (
                root / "scripts" / "offline" / "install-offline-macos-intel.sh"
            ).read_text(encoding="utf-8"),
        )
    )

    assert combined.count(".seektalent/chrome-extension/wtscli") >= 2
    assert "打开 chrome://extensions" in combined
    assert "重新加载" in combined
    assert "seektalent browser-check" in combined
