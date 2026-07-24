from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
from pathlib import Path

from seektalent.browser_bridge_install import install_browser_bridge_bundle
from seektalent.browser_bridge_manifest import (
    BrowserBridgeBundle,
    BrowserBridgeManifestError,
    load_browser_bridge_bundle as _load_browser_bridge_bundle,
)


PIP_ZIPAPP_URL = "https://bootstrap.pypa.io/pip/pip.pyz"
VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]*$")
OFFLINE_BROWSER_BRIDGE_INSTALLER_FILES = (
    "__init__.py",
    "browser_bridge_install.py",
    "browser_bridge_manifest.py",
    "strict_json.py",
    "version.py",
)


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_browser_bridge_bundle(root: Path, *, opencli_version: str) -> BrowserBridgeBundle:
    try:
        bundle = _load_browser_bridge_bundle(root)
    except BrowserBridgeManifestError as exc:
        raise RuntimeError(f"browser bridge bundle admission failed: {exc.code}") from exc
    if bundle.requirement.cli.version != opencli_version:
        raise RuntimeError(f"browser bridge CLI must be WTSCLI {opencli_version}")
    return bundle


def project_version(repo_root: Path) -> str:
    with (repo_root / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def validate_version(value: str, *, label: str) -> str:
    if not VERSION_PATTERN.fullmatch(value):
        raise ValueError(f"{label} contains unsupported characters: {value!r}")
    return value


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "SeekTalent-offline-builder/1"})
    with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def validate_wheelhouse(wheelhouse: Path) -> list[Path]:
    wheels = sorted(wheelhouse.glob("*.whl"))
    if not wheels:
        raise RuntimeError("wheelhouse is empty")

    native_wheels: list[Path] = []
    for wheel in wheels:
        filename = wheel.name.lower()
        if "arm64" in filename:
            raise RuntimeError(f"arm64 wheel found in Intel bundle: {wheel.name}")
        if "-none-any.whl" in filename:
            continue
        if "macosx" not in filename:
            raise RuntimeError(f"non-macOS native wheel found in Intel bundle: {wheel.name}")
        if "x86_64" not in filename and "universal2" not in filename:
            raise RuntimeError(f"native wheel is not Intel-compatible: {wheel.name}")
        native_wheels.append(wheel)

    if not native_wheels:
        raise RuntimeError("wheelhouse did not contain any native Intel or universal2 wheels")
    return native_wheels


def zip_directory(source: Path, destination: Path, *, include_root: bool) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    zip_env = {**os.environ, "COPYFILE_DISABLE": "1"}
    if include_root:
        run(["zip", "-qry", str(destination), source.name], cwd=source.parent, env=zip_env)
    else:
        run(["zip", "-qry", str(destination), "."], cwd=source, env=zip_env)


def write_bundle_checksums(bundle_root: Path) -> None:
    checksum_path = bundle_root / "SHA256SUMS"
    files = sorted(
        path for path in bundle_root.rglob("*") if path.is_file() and path != checksum_path
    )
    lines = [f"{sha256(path)}  {path.relative_to(bundle_root).as_posix()}" for path in files]
    checksum_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def copy_browser_bridge_installer(repo_root: Path, destination: Path) -> None:
    source_package = repo_root / "src" / "seektalent"
    destination_package = destination / "seektalent"
    destination_package.mkdir(parents=True)
    for filename in OFFLINE_BROWSER_BRIDGE_INSTALLER_FILES:
        shutil.copy2(source_package / filename, destination_package / filename)


def write_readme(bundle_root: Path, *, version: str, opencli_version: str, extension_version: str) -> None:
    (bundle_root / "README.md").write_text(
        f"""# SeekTalent {version} macOS Intel 离线安装包

目标平台：macOS Intel x86_64、Domi Python 3.13、Domi Node。

本包包含 SeekTalent {version}、全部 macOS Intel Python 依赖、离线 pip、WTSCLI {opencli_version} 完整 runtime，以及 WTSCLI Browser Bridge {extension_version} Chrome 扩展。安装过程不会访问 PyPI、npm、GitHub 或 Chrome Web Store。

前提：Domi 已安装，Chrome 已安装并登录猎聘。

在 Terminal 中执行：

```bash
source ./install-offline.sh
```

然后打开 `chrome://extensions`，开启“开发者模式”，点击“加载已解压的扩展程序”，选择：

```text
~/.seektalent/chrome-extension/wtscli
```

设置 JWT 并启动：

```bash
export SEEKTALENT_DOMI_JWT='<新的 DOMI_JWT>'
seektalent workbench
```

安装脚本只写入 `~/.seektalent`，不会修改 Domi 或 Chrome 用户配置。实际运行 Workbench 时仍需联网访问 Domi LLM 服务和猎聘。
""",
        encoding="utf-8",
    )


def build_bundle(args: argparse.Namespace) -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    version = validate_version(args.version or project_version(repo_root), label="SeekTalent version")
    opencli_version = validate_version(args.wtscli_version, label="WTSCLI version")
    browser_bridge = load_browser_bridge_bundle(
        args.wtscli_bundle_dir,
        opencli_version=opencli_version,
    )
    extension_version = browser_bridge.extension_version

    if platform.system() != "Darwin" or platform.machine() != "x86_64":
        raise RuntimeError("this bundle must be built natively on macOS Intel x86_64")
    if sys.version_info[:2] != (3, 13):
        raise RuntimeError(f"Python 3.13 is required, found {platform.python_version()}")

    constraints = (
        repo_root / "scripts" / "offline" / f"constraints-{version}-macos-intel.txt"
    )
    if not constraints.is_file():
        raise RuntimeError(f"offline dependency constraints were not found: {constraints}")

    output_dir = args.output_dir.resolve()
    bundle_name = f"seektalent-offline-{version}-macos-x86_64-py313"
    bundle_root = output_dir / bundle_name
    bundle_zip = output_dir / f"{bundle_name}.zip"
    checksum_file = output_dir / f"{bundle_name}.zip.sha256"

    shutil.rmtree(bundle_root, ignore_errors=True)
    bundle_zip.unlink(missing_ok=True)
    checksum_file.unlink(missing_ok=True)
    (bundle_root / "python-wheelhouse").mkdir(parents=True)
    (bundle_root / "tools").mkdir()
    copy_browser_bridge_installer(
        repo_root,
        bundle_root / "tools" / "browser-bridge-installer",
    )
    browser_bridge_target = bundle_root / "wtscli-browser-bridge"
    shutil.copytree(browser_bridge.root, browser_bridge_target)

    pip_zipapp = bundle_root / "tools" / "pip.pyz"
    download(PIP_ZIPAPP_URL, pip_zipapp)
    shutil.copy2(constraints, bundle_root / "tools" / "python-constraints.txt")
    run(
        [
            sys.executable,
            str(pip_zipapp),
            "download",
            "--disable-pip-version-check",
            "--only-binary=:all:",
            "--constraint",
            str(constraints),
            "--dest",
            str(bundle_root / "python-wheelhouse"),
            f"seektalent=={version}",
        ]
    )
    validate_wheelhouse(bundle_root / "python-wheelhouse")

    node = shutil.which("node")
    if node is None:
        raise RuntimeError("Node is required to prepare the offline WTSCLI runtime")
    runtime_archive = (
        bundle_root
        / "wtscli-runtime"
        / f"wtscli-{opencli_version}-runtime.zip"
    )
    with tempfile.TemporaryDirectory(prefix="seektalent-wtscli-runtime-") as temporary:
        prepared = install_browser_bridge_bundle(
            bundle_dir=browser_bridge_target,
            install_root=Path(temporary) / ".seektalent",
            node=Path(node),
        )
        zip_directory(prepared.runtime_dir, runtime_archive, include_root=False)

    installer = bundle_root / "install-offline.sh"
    shutil.copy2(repo_root / "scripts" / "offline" / "install-offline-macos-intel.sh", installer)
    installer.chmod(0o755)
    manifest = {
        "schema_version": 1,
        "platform": "macos-x86_64",
        "python_version": "3.13",
        "seektalent_version": version,
        "wtscli_version": opencli_version,
        "extension_version": extension_version,
        "browser_bridge_bundle": "wtscli-browser-bridge",
        "browser_bridge_runtime": runtime_archive.relative_to(bundle_root).as_posix(),
        "browser_bridge_runtime_sha256": sha256(runtime_archive),
        "browser_bridge_build_id": browser_bridge.bridge_build_id,
        "browser_bridge_fork_commit": browser_bridge.fork_commit,
        "browser_bridge_extension_id": browser_bridge.requirement.extension.id,
    }
    (bundle_root / "bundle-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_readme(
        bundle_root,
        version=version,
        opencli_version=opencli_version,
        extension_version=extension_version,
    )
    write_bundle_checksums(bundle_root)
    zip_directory(bundle_root, bundle_zip, include_root=True)
    run(["unzip", "-tq", str(bundle_zip)])
    checksum_file.write_text(f"{sha256(bundle_zip)}  {bundle_zip.name}\n", encoding="utf-8")
    return bundle_zip


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the SeekTalent macOS Intel offline bundle.")
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    parser.add_argument("--version")
    parser.add_argument(
        "--wtscli-version",
        "--opencli-version",
        dest="wtscli_version",
        default="0.1.0",
    )
    parser.add_argument(
        "--wtscli-bundle-dir",
        "--opencli-bundle-dir",
        dest="wtscli_bundle_dir",
        type=Path,
        required=True,
        help="Verified output from the SeekTalent WTSCLI fork's build:seektalent-bundle command.",
    )
    return parser.parse_args()


def main() -> int:
    bundle_zip = build_bundle(parse_args())
    print(bundle_zip)
    print(bundle_zip.with_suffix(bundle_zip.suffix + ".sha256"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
