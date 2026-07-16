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
from typing import Any, cast


PIP_ZIPAPP_URL = "https://bootstrap.pypa.io/pip/pip.pyz"
VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]*$")
BROWSER_BRIDGE_SCHEMA_VERSION = "seektalent.browser_bridge_bundle.v1"
BROWSER_BRIDGE_IMPLEMENTATION = "seektalent-opencli"
REQUIRED_BROWSER_BRIDGE_CAPABILITIES = frozenset(
    {
        "browser.operation-deadline.v1",
        "browser.operations.v1",
        "control-fence.v1",
        "tab.close-verified.v1",
        "tab.create-in-existing-window.v1",
        "tab.find.v1",
        "tab.idle-deadline.v1",
    }
)


class BrowserBridgeBundle:
    def __init__(
        self,
        *,
        root: Path,
        manifest_path: Path,
        manifest: dict[str, Any],
        runtime_package: Path,
        extension_dir: Path,
    ) -> None:
        self.root = root
        self.manifest_path = manifest_path
        self.manifest = manifest
        self.runtime_package = runtime_package
        self.extension_dir = extension_dir

    @property
    def extension_version(self) -> str:
        return str(self.manifest["extension"]["version"])

    @property
    def bridge_build_id(self) -> str:
        return str(self.manifest["bridgeBuildId"])

    @property
    def fork_commit(self) -> str:
        return str(self.manifest["forkCommit"])


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


def _bundle_path(root: Path, value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"browser bridge {label} is missing")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError(f"browser bridge {label} must stay inside its bundle")
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise RuntimeError(f"browser bridge {label} must stay inside its bundle")
    return path


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"browser bridge {label} must be an object")
    return cast(dict[str, Any], value)


def _string(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"browser bridge {label} is missing")
    return value


def _extension_tree(extension_dir: Path) -> tuple[str, list[dict[str, object]]]:
    files: list[dict[str, object]] = []
    for path in sorted(extension_dir.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"browser bridge extension contains a symlink: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(extension_dir).as_posix()
        files.append({"path": relative, "size": path.stat().st_size, "sha256": sha256(path)})
    tree_text = "".join(f"{item['sha256']}  {item['path']}\n" for item in files)
    tree_sha256 = hashlib.sha256(tree_text.encode()).hexdigest()
    return tree_sha256, files


def load_browser_bridge_bundle(root: Path, *, opencli_version: str) -> BrowserBridgeBundle:
    root = root.resolve()
    manifest_path = root / "bridge-manifest.json"
    try:
        manifest = _mapping(json.loads(manifest_path.read_text(encoding="utf-8")), label="manifest")
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"browser bridge manifest could not be read: {manifest_path}") from exc

    if manifest.get("schemaVersion") != BROWSER_BRIDGE_SCHEMA_VERSION:
        raise RuntimeError("browser bridge manifest has the wrong schema version")
    if manifest.get("implementation") != BROWSER_BRIDGE_IMPLEMENTATION:
        raise RuntimeError("browser bridge manifest is not the SeekTalent WTSCLI fork")

    fork_commit = _string(manifest.get("forkCommit"), label="fork commit")
    if not re.fullmatch(r"[0-9a-f]{40}", fork_commit):
        raise RuntimeError("browser bridge fork commit must be a full Git SHA")
    expected_build_id = f"seektalent-opencli-{opencli_version}+{fork_commit[:12]}"
    if manifest.get("bridgeBuildId") != expected_build_id:
        raise RuntimeError("browser bridge build ID does not match its fork commit")

    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, list) or not all(isinstance(item, str) for item in capabilities):
        raise RuntimeError("browser bridge capabilities must be a string list")
    missing_capabilities = sorted(REQUIRED_BROWSER_BRIDGE_CAPABILITIES - set(capabilities))
    if missing_capabilities:
        raise RuntimeError(
            "browser bridge is missing required capabilities: " + ", ".join(missing_capabilities)
        )

    cli = _mapping(manifest.get("cli"), label="CLI metadata")
    if cli.get("version") != opencli_version:
        raise RuntimeError(f"browser bridge CLI must be WTSCLI {opencli_version}")
    runtime_package = _bundle_path(root, cli.get("asset"), label="CLI asset")
    if not runtime_package.is_file():
        raise RuntimeError(f"browser bridge CLI asset was not found: {runtime_package}")
    if (
        runtime_package.stat().st_size != cli.get("size")
        or sha256(runtime_package) != cli.get("sha256")
    ):
        raise RuntimeError("browser bridge CLI asset failed manifest verification")

    extension = _mapping(manifest.get("extension"), label="extension metadata")
    extension_version = _string(extension.get("version"), label="extension version")
    validate_version(extension_version, label="browser bridge extension version")
    extension_dir = _bundle_path(root, extension.get("directory"), label="extension directory")
    if not extension_dir.is_dir():
        raise RuntimeError(f"browser bridge extension directory was not found: {extension_dir}")
    manifest_file = extension_dir / "manifest.json"
    if not manifest_file.is_file() or sha256(manifest_file) != extension.get("manifestSha256"):
        raise RuntimeError("browser bridge extension manifest failed verification")
    try:
        installed_extension_version = json.loads(manifest_file.read_text(encoding="utf-8"))["version"]
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        raise RuntimeError("browser bridge extension manifest is invalid") from exc
    if installed_extension_version != extension_version:
        raise RuntimeError("browser bridge extension version does not match its manifest")

    tree_sha256, files = _extension_tree(extension_dir)
    if tree_sha256 != extension.get("treeSha256") or files != extension.get("files"):
        raise RuntimeError("browser bridge extension tree failed manifest verification")

    return BrowserBridgeBundle(
        root=root,
        manifest_path=manifest_path,
        manifest=manifest,
        runtime_package=runtime_package,
        extension_dir=extension_dir,
    )


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

本包包含 SeekTalent {version}、全部 macOS Intel Python 依赖、离线 pip、WTSCLI {opencli_version} 完整 runtime，以及 WTSCLI Browser Bridge {extension_version} Chrome 扩展。安装过程不会访问 PyPI、npm、GitHub 或 Chrome Web Store。

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
    opencli_version = validate_version(args.opencli_version, label="WTSCLI version")
    browser_bridge = load_browser_bridge_bundle(
        args.opencli_bundle_dir,
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
                str(browser_bridge.runtime_package),
            ]
        )
        opencli_package = runtime_root / "node_modules" / "@jackwener" / "opencli" / "package.json"
        opencli_main = runtime_root / "node_modules" / "@jackwener" / "opencli" / "dist" / "src" / "main.js"
        bridge_identity = runtime_root / "node_modules" / "@jackwener" / "opencli" / "bridge-identity.json"
        if (
            not opencli_main.is_file()
            or not bridge_identity.is_file()
            or json.loads(opencli_package.read_text(encoding="utf-8"))["version"] != opencli_version
            or json.loads(bridge_identity.read_text(encoding="utf-8"))["bridgeBuildId"]
            != browser_bridge.bridge_build_id
        ):
            raise RuntimeError("WTSCLI runtime is incomplete or has the wrong version")
        native_node_modules = sorted(runtime_root.rglob("*.node"))
        if native_node_modules:
            names = ", ".join(path.name for path in native_node_modules)
            raise RuntimeError(f"WTSCLI runtime unexpectedly contains native Node modules: {names}")
        run(["node", str(opencli_main), "--version"])
        zip_directory(
            runtime_root,
            bundle_root / "opencli" / f"opencli-{opencli_version}-runtime.zip",
            include_root=False,
        )

    extension_archive = bundle_root / "chrome-extension" / f"wtscli-extension-v{extension_version}.zip"
    zip_directory(browser_bridge.extension_dir, extension_archive, include_root=False)
    extension_sha256 = sha256(extension_archive)
    bridge_manifest = bundle_root / "opencli" / "bridge-manifest.json"
    shutil.copy2(browser_bridge.manifest_path, bridge_manifest)

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
        "browser_bridge_manifest": "opencli/bridge-manifest.json",
        "browser_bridge_build_id": browser_bridge.bridge_build_id,
        "browser_bridge_fork_commit": browser_bridge.fork_commit,
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
    parser.add_argument("--opencli-version", default="0.1.0")
    parser.add_argument(
        "--opencli-bundle-dir",
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
