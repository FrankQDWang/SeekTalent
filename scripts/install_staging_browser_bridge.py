from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import cast

if __package__:
    from scripts.build_offline_macos_intel import load_browser_bridge_bundle
else:
    from build_offline_macos_intel import load_browser_bridge_bundle


WTSCLI_VERSION = "0.1.0"


def install_browser_bridge(*, bundle_dir: Path, staging_home: Path, node: Path) -> dict[str, str]:
    bundle = load_browser_bridge_bundle(bundle_dir, opencli_version=WTSCLI_VERSION)
    install_root = staging_home / ".seektalent"
    runtime_target = install_root / "opencli-runtime" / "opencli" / WTSCLI_VERSION
    extension_target = install_root / "chrome-extension" / "opencli"
    manifest_target = install_root / "browser-bridge" / "bridge-manifest.json"
    install_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="seektalent-staging-bridge-", dir=install_root) as temporary:
        stage_root = Path(temporary)
        runtime_stage = stage_root / "runtime"
        extension_stage = stage_root / "extension"
        subprocess.run(
            (
                "npm",
                "install",
                "--prefix",
                str(runtime_stage),
                "--omit=dev",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
                str(bundle.runtime_package),
            ),
            check=True,
        )
        shutil.copytree(bundle.extension_dir, extension_stage)
        _verify_staged_pair(
            runtime_dir=runtime_stage,
            extension_dir=extension_stage,
            manifest=bundle.manifest,
            node=node,
        )
        _replace_directory(runtime_stage, runtime_target)
        _replace_directory(extension_stage, extension_target)
        manifest_target.parent.mkdir(parents=True, exist_ok=True)
        manifest_stage = stage_root / "bridge-manifest.json"
        shutil.copy2(bundle.manifest_path, manifest_stage)
        os.replace(manifest_stage, manifest_target)

    return {
        "runtime": str(runtime_target),
        "extension": str(extension_target),
        "manifest": str(manifest_target),
        "bridgeBuildId": bundle.bridge_build_id,
        "extensionVersion": bundle.extension_version,
    }


def _verify_staged_pair(
    *,
    runtime_dir: Path,
    extension_dir: Path,
    manifest: dict[str, object],
    node: Path,
) -> None:
    package_dir = runtime_dir / "node_modules" / "@jackwener" / "opencli"
    package_json = json.loads((package_dir / "package.json").read_text(encoding="utf-8"))
    identity = json.loads((package_dir / "bridge-identity.json").read_text(encoding="utf-8"))
    main = package_dir / "dist" / "src" / "main.js"
    if package_json.get("version") != WTSCLI_VERSION or not main.is_file():
        raise RuntimeError("staged WTSCLI runtime is incomplete")
    for key in ("implementation", "bridgeBuildId", "protocolVersion", "capabilities"):
        if identity.get(key) != manifest.get(key):
            raise RuntimeError(f"staged WTSCLI identity mismatch: {key}")
    if any(runtime_dir.rglob("*.node")):
        raise RuntimeError("staged WTSCLI unexpectedly contains a native Node module")
    extension_metadata = manifest["extension"]
    if not isinstance(extension_metadata, dict):
        raise RuntimeError("browser bridge extension metadata is invalid")
    typed_extension_metadata = cast(dict[str, object], extension_metadata)
    extension_manifest = json.loads((extension_dir / "manifest.json").read_text(encoding="utf-8"))
    if extension_manifest.get("version") != typed_extension_metadata.get("version"):
        raise RuntimeError("staged browser extension version mismatch")
    completed = subprocess.run(
        (str(node), str(main), "--version"),
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if completed.returncode != 0 or completed.stdout.strip() != WTSCLI_VERSION:
        raise RuntimeError("staged WTSCLI failed its version probe")


def _replace_directory(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = target.with_name(f"{target.name}.previous")
    if backup.exists():
        shutil.rmtree(backup)
    if target.exists():
        os.replace(target, backup)
    try:
        os.replace(source, target)
    except BaseException:
        if backup.exists() and not target.exists():
            os.replace(backup, target)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--staging-home", type=Path, required=True)
    parser.add_argument("--node", type=Path, required=True)
    args = parser.parse_args()
    result = install_browser_bridge(
        bundle_dir=args.bundle_dir.resolve(),
        staging_home=args.staging_home.resolve(),
        node=args.node.resolve(),
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
