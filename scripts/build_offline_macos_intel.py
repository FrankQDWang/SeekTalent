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


PIP_ZIPAPP_URL = "https://bootstrap.pypa.io/pip/pip.pyz"
VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]*$")


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


def write_readme(bundle_root: Path, *, version: str, opencli_version: str, extension_version: str) -> None:
    (bundle_root / "README.md").write_text(
        f"""# SeekTalent {version} macOS Intel 离线安装包

目标平台：macOS Intel x86_64、Domi Python 3.13、Domi Node。

本包包含 SeekTalent {version}、全部 macOS Intel Python 依赖、离线 pip、OpenCLI {opencli_version} 完整 runtime，以及 OpenCLI Browser Bridge {extension_version} Chrome 扩展。安装过程不会访问 PyPI、npm、GitHub 或 Chrome Web Store。

前提：Domi 已安装，Chrome 已安装并登录猎聘。

在 Terminal 中执行：

```bash
source ./install-offline.sh
```

然后打开 `chrome://extensions`，开启“开发者模式”，点击“加载已解压的扩展程序”，选择：

```text
~/.seektalent/chrome-extension/opencli
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
    opencli_version = validate_version(args.opencli_version, label="OpenCLI version")
    extension_version = validate_version(args.extension_version, label="extension version")
    extension_sha256 = args.extension_sha256.lower()
    if not re.fullmatch(r"[0-9a-f]{64}", extension_sha256):
        raise ValueError("extension SHA256 must contain exactly 64 lowercase hexadecimal characters")

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
    (bundle_root / "opencli").mkdir()
    (bundle_root / "chrome-extension").mkdir()

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

    with tempfile.TemporaryDirectory(prefix="seektalent-macos-intel-") as temporary:
        runtime_root = Path(temporary) / "opencli-runtime"
        run(
            [
                "npm",
                "install",
                "--prefix",
                str(runtime_root),
                "--omit=dev",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
                f"@jackwener/opencli@{opencli_version}",
            ]
        )
        opencli_package = runtime_root / "node_modules" / "@jackwener" / "opencli" / "package.json"
        opencli_main = runtime_root / "node_modules" / "@jackwener" / "opencli" / "dist" / "src" / "main.js"
        if not opencli_main.is_file() or json.loads(opencli_package.read_text(encoding="utf-8"))["version"] != opencli_version:
            raise RuntimeError("OpenCLI runtime is incomplete or has the wrong version")
        native_node_modules = sorted(runtime_root.rglob("*.node"))
        if native_node_modules:
            names = ", ".join(path.name for path in native_node_modules)
            raise RuntimeError(f"OpenCLI runtime unexpectedly contains native Node modules: {names}")
        run(["node", str(opencli_main), "--version"])
        zip_directory(
            runtime_root,
            bundle_root / "opencli" / f"opencli-{opencli_version}-runtime.zip",
            include_root=False,
        )

    extension_archive = bundle_root / "chrome-extension" / f"opencli-extension-v{extension_version}.zip"
    extension_url = (
        f"https://github.com/jackwener/OpenCLI/releases/download/v{opencli_version}/"
        f"opencli-extension-v{extension_version}.zip"
    )
    download(extension_url, extension_archive)
    actual_extension_sha256 = sha256(extension_archive)
    if actual_extension_sha256 != extension_sha256:
        raise RuntimeError(
            f"Browser Bridge SHA256 mismatch: expected {extension_sha256}, found {actual_extension_sha256}"
        )

    installer = bundle_root / "install-offline.sh"
    shutil.copy2(repo_root / "scripts" / "offline" / "install-offline-macos-intel.sh", installer)
    installer.chmod(0o755)
    manifest = {
        "schema_version": 1,
        "platform": "macos-x86_64",
        "python_version": "3.13",
        "seektalent_version": version,
        "opencli_version": opencli_version,
        "extension_version": extension_version,
        "extension_sha256": extension_sha256,
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
    parser.add_argument("--opencli-version", default="1.8.6")
    parser.add_argument("--extension-version", default="1.0.22")
    parser.add_argument(
        "--extension-sha256",
        default="9d2e3d053948beab5d97124aa79b1532d2122e33e461eca56cac113afd33207a",
    )
    return parser.parse_args()


def main() -> int:
    bundle_zip = build_bundle(parse_args())
    print(bundle_zip)
    print(bundle_zip.with_suffix(bundle_zip.suffix + ".sha256"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
