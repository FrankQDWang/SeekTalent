from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from seektalent.browser_bridge_install import install_browser_bridge_bundle
from seektalent.browser_bridge_manifest import (
    WTSCLI_BUILD_ID,
    WTSCLI_EXTENSION_ID,
    WTSCLI_FORK_COMMIT,
    WTSCLI_VERSION,
)
from seektalent.domi_bootstrap import INSTALL_RECEIPT_SCHEMA
from seektalent.version import __version__
from tests.browser_bridge_bundle_fixtures import write_browser_bridge_bundle


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "start-seektalent-domi.sh"


def _exact_install(tmp_path: Path, capture: Path) -> tuple[Path, Path, Path]:
    node_text = shutil.which("node")
    if node_text is None:
        pytest.skip("Node runtime is unavailable")
    home = tmp_path / "home"
    root = home / ".seektalent"
    bundle = tmp_path / "wtscli-browser-bridge"
    write_browser_bridge_bundle(
        bundle,
        runtime_main="console.log(process.argv.includes('--version') ? '0.1.0' : 'help')\n",
    )
    wheel = tmp_path / f"seektalent-{__version__}-py3-none-any.whl"
    wheel.write_bytes(b"exact-test-wheel")
    manifest = tmp_path / "delivery-manifest.json"
    source_revision = "a" * 40
    manifest_payload = {
        "schema_version": 1,
        "product_version": __version__,
        "source_revision": source_revision,
        "product_build_id": f"seektalent-{__version__}+{source_revision}",
        "bridge_build_id": WTSCLI_BUILD_ID,
        "wtscli_version": WTSCLI_VERSION,
        "wtscli_fork_commit": WTSCLI_FORK_COMMIT,
        "extension_version": WTSCLI_VERSION,
        "extension_id_sha256": hashlib.sha256(WTSCLI_EXTENSION_ID.encode()).hexdigest(),
        "seektalent_wheel": wheel.name,
        "seektalent_wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
    }
    manifest.write_text(json.dumps(manifest_payload) + "\n", encoding="utf-8")
    receipt = {
        "schemaVersion": INSTALL_RECEIPT_SCHEMA,
        "productVersion": __version__,
        "sourceRevision": source_revision,
        "productBuildId": manifest_payload["product_build_id"],
        "wheelSha256": manifest_payload["seektalent_wheel_sha256"],
        "wheelFilename": wheel.name,
        "deliveryManifestSha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "deliveryManifestFilename": manifest.name,
        "bridgeBuildId": WTSCLI_BUILD_ID,
        "wtscliVersion": WTSCLI_VERSION,
        "wtscliForkCommit": WTSCLI_FORK_COMMIT,
        "extensionVersion": WTSCLI_VERSION,
        "extensionIdSha256": manifest_payload["extension_id_sha256"],
    }
    (tmp_path / "install-receipt.json").write_text(
        json.dumps(receipt) + "\n",
        encoding="utf-8",
    )
    install_browser_bridge_bundle(
        bundle_dir=bundle,
        install_root=root,
        node=Path(node_text),
        additional_targets=(
            (tmp_path / "install-receipt.json", root / "install-receipt.json"),
            (manifest, root / manifest.name),
            (wheel, root / wheel.name),
        ),
    )
    site_packages = root / "python-prefix" / __version__ / "site-packages"
    site_packages.mkdir(parents=True)
    shutil.copytree(ROOT / "src" / "seektalent", site_packages / "seektalent")
    bin_path = root / "bin" / "seektalent"
    bin_path.parent.mkdir(parents=True)
    bin_path.write_text(
        f"#!/bin/sh\nprintf '%s\\n' \"$SEEKTALENT_DOMI_JWT\" \"$SEEKTALENT_DOMI_PYTHON\" \"$SEEKTALENT_DOMI_NODE\" \"$1\" \"$2\" > {capture}\n",
        encoding="utf-8",
    )
    bin_path.chmod(0o755)
    return home, Path(sys.executable), Path(node_text)


def test_start_script_executes_only_the_validated_installed_shim(
    tmp_path: Path,
) -> None:
    capture = tmp_path / "capture.txt"
    home, domi_python, domi_node = _exact_install(tmp_path, capture)
    decoy = tmp_path / "decoy"
    decoy.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    decoy.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "SEEKTALENT_INSTALL_HOME": str(home),
            "SEEKTALENT_DOMI_JWT": "test-jwt-not-a-real-credential",
            "DOMI_PYTHON": str(domi_python),
            "DOMI_NODE": str(domi_node),
            "SEEKTALENT_BIN": str(decoy),
        }
    )
    result = subprocess.run(
        ["sh", str(SCRIPT), "--host", "127.0.0.1"],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert capture.read_text(encoding="utf-8").splitlines() == [
        "test-jwt-not-a-real-credential",
        str(domi_python),
        str(domi_node),
        "workbench",
        "--host",
    ]
    assert "test-jwt-not-a-real-credential" not in result.stdout
    assert "test-jwt-not-a-real-credential" not in result.stderr


def test_start_script_requires_jwt_before_exact_validation(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.pop("SEEKTALENT_DOMI_JWT", None)
    env["SEEKTALENT_INSTALL_HOME"] = str(tmp_path)
    result = subprocess.run(
        ["sh", str(SCRIPT)],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "seektalent_domi_jwt_missing" in result.stderr


def _run_installed_release_validator(home: Path, python: Path) -> subprocess.CompletedProcess[str]:
    site_packages = home / ".seektalent" / "python-prefix" / __version__ / "site-packages"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(site_packages)
    env["PYTHONNOUSERSITE"] = "1"
    return subprocess.run(
        [
            str(python),
            "-m",
            "seektalent.installed_domi_release",
            "--home",
            str(home),
        ],
        cwd="/",
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_installed_release_validator_rejects_tampered_wheel(
    tmp_path: Path,
) -> None:
    home, domi_python, _domi_node = _exact_install(tmp_path, tmp_path / "capture.txt")
    wheel = next((home / ".seektalent").glob("*.whl"))
    wheel.write_bytes(wheel.read_bytes() + b"tampered")

    result = _run_installed_release_validator(home, domi_python)

    assert result.returncode != 0
    assert "reason_code=seektalent_wheel_hash_mismatch" in result.stderr


def test_installed_release_validator_rejects_tampered_delivery_manifest(
    tmp_path: Path,
) -> None:
    home, domi_python, _domi_node = _exact_install(tmp_path, tmp_path / "capture.txt")
    manifest = home / ".seektalent" / "delivery-manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["source_revision"] = "b" * 40
    manifest.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    result = _run_installed_release_validator(home, domi_python)

    assert result.returncode != 0
    assert "reason_code=delivery_manifest_hash_mismatch" in result.stderr


def test_installed_release_validator_rejects_tampered_receipt_identity(
    tmp_path: Path,
) -> None:
    home, domi_python, _domi_node = _exact_install(tmp_path, tmp_path / "capture.txt")
    receipt = home / ".seektalent" / "install-receipt.json"
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["productBuildId"] = "seektalent-tampered"
    receipt.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    result = _run_installed_release_validator(home, domi_python)

    assert result.returncode != 0
    assert "reason_code=delivery_manifest_identity_mismatch" in result.stderr
