from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

from scripts.build_domi_delivery_bundle import build_delivery_bundle
from tests.browser_bridge_bundle_fixtures import (
    WTSCLI_BUILD_ID,
    write_browser_bridge_bundle,
)


def test_build_delivery_bundle_contains_exact_pair_runtime_and_platform_installer(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "wtscli-browser-bridge"
    write_browser_bridge_bundle(bundle)
    wheel = tmp_path / "seektalent-0.7.49-py3-none-any.whl"
    wheel.write_bytes(b"wheel")

    archive = build_delivery_bundle(
        output_dir=tmp_path / "dist",
        browser_bridge_bundle=bundle,
        seektalent_wheel=wheel,
        node=Path(sys.executable),
        platform_name="windows-x64",
    )

    with zipfile.ZipFile(archive) as delivery:
        names = set(delivery.namelist())
        root = archive.stem
        assert f"{root}/install-seektalent-domi.ps1" in names
        assert f"{root}/install_staging_browser_bridge.py" in names
        assert f"{root}/wtscli-browser-bridge/bridge-manifest.json" in names
        assert f"{root}/wtscli-browser-bridge/extension/manifest.json" in names
        assert f"{root}/wtscli-runtime.zip" in names
        assert f"{root}/{wheel.name}" in names
        manifest = json.loads(delivery.read(f"{root}/delivery-manifest.json"))
        assert manifest["bridge_build_id"] == WTSCLI_BUILD_ID
        assert manifest["platform"] == "windows-x64"
        assert manifest["extension_directory"] == "~/.seektalent/chrome-extension/wtscli"


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
