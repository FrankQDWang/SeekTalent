from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "build_offline_macos_intel.py"
CONSTRAINTS_PATH = (
    Path(__file__).parents[1]
    / "scripts"
    / "offline"
    / "constraints-0.7.46-macos-intel.txt"
)
SPEC = importlib.util.spec_from_file_location("build_offline_macos_intel", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _browser_bridge_bundle(directory: Path) -> Path:
    runtime_dir = directory / "runtime"
    extension_dir = directory / "extension"
    runtime_dir.mkdir(parents=True)
    (extension_dir / "dist").mkdir(parents=True)
    runtime_package = runtime_dir / "jackwener-opencli-1.8.6.tgz"
    runtime_package.write_bytes(b"fork runtime")
    (extension_dir / "dist" / "background.js").write_text("bridge", encoding="utf-8")
    (extension_dir / "manifest.json").write_text(
        json.dumps({"version": "1.0.22.1"}, separators=(",", ":")),
        encoding="utf-8",
    )
    tree_sha256, files = MODULE._extension_tree(extension_dir)
    fork_commit = "a" * 40
    manifest = {
        "schemaVersion": MODULE.BROWSER_BRIDGE_SCHEMA_VERSION,
        "implementation": MODULE.BROWSER_BRIDGE_IMPLEMENTATION,
        "forkCommit": fork_commit,
        "bridgeBuildId": f"seektalent-opencli-1.8.6+{fork_commit[:12]}",
        "protocolVersion": {"major": 1, "minor": 0},
        "capabilities": sorted(MODULE.REQUIRED_BROWSER_BRIDGE_CAPABILITIES),
        "cli": {
            "version": "1.8.6",
            "asset": "runtime/jackwener-opencli-1.8.6.tgz",
            "size": runtime_package.stat().st_size,
            "sha256": hashlib.sha256(runtime_package.read_bytes()).hexdigest(),
        },
        "extension": {
            "version": "1.0.22.1",
            "directory": "extension",
            "treeSha256": tree_sha256,
            "manifestSha256": hashlib.sha256(
                (extension_dir / "manifest.json").read_bytes()
            ).hexdigest(),
            "files": files,
        },
    }
    (directory / "bridge-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    return directory


def _wheel(directory: Path, name: str) -> None:
    (directory / name).write_bytes(b"wheel")


def test_constraints_pin_the_current_release_and_accepted_native_dependencies() -> None:
    constraints = CONSTRAINTS_PATH.read_text(encoding="utf-8").splitlines()

    assert "seektalent==0.7.46" in constraints
    assert "cryptography==48.0.0" in constraints
    assert "pydantic-core==2.46.4" in constraints
    assert "tiktoken==0.13.0" in constraints
    assert all("==" in line for line in constraints if line and not line.startswith("#"))


def test_load_browser_bridge_bundle_accepts_verified_seek_talent_fork(tmp_path: Path) -> None:
    bundle_dir = _browser_bridge_bundle(tmp_path / "bridge")

    bundle = MODULE.load_browser_bridge_bundle(bundle_dir, opencli_version="1.8.6")

    assert bundle.bridge_build_id == "seektalent-opencli-1.8.6+aaaaaaaaaaaa"
    assert bundle.runtime_package.name == "jackwener-opencli-1.8.6.tgz"
    assert bundle.extension_version == "1.0.22.1"


def test_load_browser_bridge_bundle_rejects_tampered_runtime(tmp_path: Path) -> None:
    bundle_dir = _browser_bridge_bundle(tmp_path / "bridge")
    (bundle_dir / "runtime" / "jackwener-opencli-1.8.6.tgz").write_bytes(b"tampered")

    with pytest.raises(RuntimeError, match="CLI asset failed manifest verification"):
        MODULE.load_browser_bridge_bundle(bundle_dir, opencli_version="1.8.6")


def test_load_browser_bridge_bundle_rejects_tampered_extension_tree(tmp_path: Path) -> None:
    bundle_dir = _browser_bridge_bundle(tmp_path / "bridge")
    (bundle_dir / "extension" / "unexpected.js").write_text("tampered", encoding="utf-8")

    with pytest.raises(RuntimeError, match="extension tree failed manifest verification"):
        MODULE.load_browser_bridge_bundle(bundle_dir, opencli_version="1.8.6")


def test_load_browser_bridge_bundle_requires_production_capabilities(tmp_path: Path) -> None:
    bundle_dir = _browser_bridge_bundle(tmp_path / "bridge")
    manifest_path = bundle_dir / "bridge-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["capabilities"].remove("tab.idle-deadline.v1")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RuntimeError, match="tab.idle-deadline.v1"):
        MODULE.load_browser_bridge_bundle(bundle_dir, opencli_version="1.8.6")


def test_offline_release_uses_pinned_fork_bundle_not_upstream_assets() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    workflow = (SCRIPT_PATH.parents[1] / ".github/workflows/build-macos-intel-offline.yml").read_text(
        encoding="utf-8"
    )

    assert "--opencli-bundle-dir" in source
    assert "github.com/jackwener/OpenCLI/releases" not in source
    assert "@jackwener/opencli@{opencli_version}" not in source
    assert "repository: FrankQDWang/OpenCLI" in workflow
    assert "OPENCLI_FORK_COMMIT" in workflow


def test_validate_wheelhouse_accepts_pure_intel_and_universal2_wheels(tmp_path: Path) -> None:
    _wheel(tmp_path, "seektalent-0.7.46-py3-none-any.whl")
    _wheel(tmp_path, "pydantic_core-2.46.4-cp313-cp313-macosx_10_13_x86_64.whl")
    _wheel(tmp_path, "cryptography-49.0.0-cp311-abi3-macosx_10_9_universal2.whl")

    native = MODULE.validate_wheelhouse(tmp_path)

    assert [wheel.name for wheel in native] == [
        "cryptography-49.0.0-cp311-abi3-macosx_10_9_universal2.whl",
        "pydantic_core-2.46.4-cp313-cp313-macosx_10_13_x86_64.whl",
    ]


@pytest.mark.parametrize(
    "wheel_name",
    [
        "pydantic_core-2.46.4-cp313-cp313-macosx_11_0_arm64.whl",
        "pydantic_core-2.46.4-cp313-cp313-manylinux_2_17_x86_64.whl",
    ],
)
def test_validate_wheelhouse_rejects_wrong_platform_wheels(tmp_path: Path, wheel_name: str) -> None:
    _wheel(tmp_path, "seektalent-0.7.46-py3-none-any.whl")
    _wheel(tmp_path, wheel_name)

    with pytest.raises(RuntimeError):
        MODULE.validate_wheelhouse(tmp_path)
