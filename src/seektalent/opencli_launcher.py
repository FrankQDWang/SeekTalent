from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path


OPENCLI_PACKAGE = "@jackwener/opencli"
OPENCLI_VERSION = "1.8.6"
NODE_VERSION = "v24.16.0"
RUNTIME_ROOT = Path.home() / ".seektalent" / "opencli-runtime"
NODE_DIST_BASE_URL = "https://nodejs.org/dist"
PROVIDER_SECRET_ENV_VARS = frozenset(
    {
        "SEEKTALENT_TEXT_LLM_API_KEY",
        "SEEKTALENT_DOMI_JWT",
        "SEEKTALENT_DOMI_LLM_BASE_URL",
        "SEEKTALENT_DOMI_LLM_CHANNEL",
    }
)
OPENCLI_NODE_POLICY_ENV = "SEEKTALENT_OPENCLI_NODE_POLICY"
EXPLICIT_OPENCLI_NODE_ENV = "SEEKTALENT_OPENCLI_NODE"
DOMI_NODE_ENV_VARS = ("SEEKTALENT_DOMI_NODE", "DOMI_NODE")


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        runtime = ensure_opencli_runtime()
    except BootstrapError as exc:
        print(f"SeekTalent OpenCLI bootstrap failed: {exc}", file=sys.stderr)
        return 127
    env = _opencli_subprocess_env(node_bin_dir=runtime.node_bin_dir)
    completed = subprocess.run((str(runtime.node), str(runtime.opencli_main), *args), env=env, check=False)
    return completed.returncode


class BootstrapError(RuntimeError):
    pass


class OpenCliRuntime:
    def __init__(self, *, node: Path, opencli_main: Path) -> None:
        self.node = node
        self.opencli_main = opencli_main

    @property
    def node_bin_dir(self) -> Path:
        return self.node.parent


def ensure_opencli_runtime(
    *,
    root: Path | None = None,
    node_version: str = NODE_VERSION,
    opencli_version: str = OPENCLI_VERSION,
) -> OpenCliRuntime:
    runtime_root = (root or RUNTIME_ROOT).expanduser()
    node_policy = os.environ.get(OPENCLI_NODE_POLICY_ENV, "").strip().lower()
    external_node = _configured_node_from_env()
    runtime_root.mkdir(parents=True, exist_ok=True)
    with _runtime_lock(runtime_root):
        if node_policy == "domi":
            if external_node is None:
                raise BootstrapError(
                    "domi_node_missing: SEEKTALENT_DOMI_NODE or DOMI_NODE is required "
                    "when SEEKTALENT_OPENCLI_NODE_POLICY=domi"
                )
            node = _ensure_external_node(external_node)
        elif external_node is not None:
            node = _ensure_external_node(external_node)
        else:
            node = _ensure_managed_node(runtime_root, node_version=node_version)
        opencli_main = _ensure_managed_opencli(runtime_root, node=node, opencli_version=opencli_version)
    return OpenCliRuntime(node=node, opencli_main=opencli_main)


def _configured_node_from_env() -> Path | None:
    for key in (EXPLICIT_OPENCLI_NODE_ENV, *DOMI_NODE_ENV_VARS):
        raw = os.environ.get(key)
        if raw and raw.strip():
            return _resolve_node_env_path(raw)
    return None


def _resolve_node_env_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    if path.is_dir():
        return path / ("node.exe" if sys.platform == "win32" else "node")
    return path


def _ensure_external_node(node: Path) -> Path:
    if not node.exists():
        raise BootstrapError(f"domi_node_missing: Node runtime is not executable: {node}")
    _npm_for_node(node)
    return node


def _ensure_managed_node(runtime_root: Path, *, node_version: str) -> Path:
    spec = _node_platform_spec()
    install_dir = runtime_root / "node" / f"{node_version}-{spec.platform_name}"
    node = install_dir / ("node.exe" if sys.platform == "win32" else "bin/node")
    npm = install_dir / ("npm.cmd" if sys.platform == "win32" else "bin/npm")
    if node.exists() and npm.exists():
        return node
    archive_name = f"node-{node_version}-{spec.platform_name}{spec.archive_suffix}"
    archive_url = f"{NODE_DIST_BASE_URL}/{node_version}/{archive_name}"
    checksum_url = f"{NODE_DIST_BASE_URL}/{node_version}/SHASUMS256.txt"
    with tempfile.TemporaryDirectory(prefix="seektalent-node-") as temp_dir_raw:
        temp_dir = Path(temp_dir_raw)
        archive = temp_dir / archive_name
        checksums = _download_text(checksum_url)
        expected_sha = _checksum_for_archive(checksums, archive_name)
        _download_file(archive_url, archive)
        _verify_sha256(archive, expected_sha)
        extracted = temp_dir / "extract"
        extracted.mkdir()
        if spec.archive_suffix == ".zip":
            with zipfile.ZipFile(archive) as zip_file:
                zip_file.extractall(extracted)
        else:
            with tarfile.open(archive, "r:gz") as tar:
                tar.extractall(extracted, filter="data")
        source_dir = extracted / f"node-{node_version}-{spec.platform_name}"
        if not source_dir.exists():
            raise BootstrapError(f"Node archive did not contain {source_dir.name}")
        if install_dir.exists():
            shutil.rmtree(install_dir)
        install_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_dir), install_dir)
    if not node.exists() or not npm.exists():
        raise BootstrapError("managed Node install is incomplete")
    return node


def _ensure_managed_opencli(runtime_root: Path, *, node: Path, opencli_version: str) -> Path:
    install_dir = runtime_root / "opencli" / opencli_version
    main = install_dir / "node_modules" / "@jackwener" / "opencli" / "dist" / "src" / "main.js"
    package_json = install_dir / "node_modules" / "@jackwener" / "opencli" / "package.json"
    if main.exists() and _package_version(package_json) == opencli_version:
        return main
    npm = _npm_for_node(node)
    install_dir.mkdir(parents=True, exist_ok=True)
    env = _opencli_subprocess_env(node_bin_dir=node.parent)
    completed = subprocess.run(
        (
            str(npm),
            "install",
            "--prefix",
            str(install_dir),
            "--omit=dev",
            "--no-audit",
            "--no-fund",
            f"{OPENCLI_PACKAGE}@{opencli_version}",
        ),
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise BootstrapError(f"OpenCLI {opencli_version} install failed: {detail[:500]}")
    if not main.exists() or _package_version(package_json) != opencli_version:
        raise BootstrapError(f"OpenCLI {opencli_version} install is incomplete")
    return main


def _opencli_subprocess_env(*, node_bin_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in PROVIDER_SECRET_ENV_VARS:
        env.pop(key, None)
    env["PATH"] = os.pathsep.join((str(node_bin_dir), env.get("PATH", "")))
    return env


def _npm_for_node(node: Path) -> Path:
    npm = node.parent / ("npm.cmd" if sys.platform == "win32" else "npm")
    if npm.exists():
        return npm
    raise BootstrapError(f"Node npm is missing beside Node runtime: {node}")


def _package_version(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    version = data.get("version")
    return version if isinstance(version, str) else None


class _NodePlatformSpec:
    def __init__(self, *, platform_name: str, archive_suffix: str) -> None:
        self.platform_name = platform_name
        self.archive_suffix = archive_suffix


def _node_platform_spec() -> _NodePlatformSpec:
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch_by_machine = {
        "x86_64": "x64",
        "amd64": "x64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    arch = arch_by_machine.get(machine)
    if arch is None:
        raise BootstrapError(f"unsupported CPU architecture for managed Node: {machine}")
    if system == "darwin":
        return _NodePlatformSpec(platform_name=f"darwin-{arch}", archive_suffix=".tar.gz")
    if system == "linux":
        return _NodePlatformSpec(platform_name=f"linux-{arch}", archive_suffix=".tar.gz")
    if system == "windows":
        return _NodePlatformSpec(platform_name=f"win-{arch}", archive_suffix=".zip")
    raise BootstrapError(f"unsupported operating system for managed Node: {platform.system()}")


def _download_text(url: str) -> str:
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return response.read().decode("utf-8")
    except OSError as exc:
        raise BootstrapError(f"failed to download {url}") from exc


def _download_file(url: str, target: Path) -> None:
    try:
        with urllib.request.urlopen(url, timeout=300) as response:
            with target.open("wb") as output:
                shutil.copyfileobj(response, output)
    except OSError as exc:
        raise BootstrapError(f"failed to download {url}") from exc


def _checksum_for_archive(checksums: str, archive_name: str) -> str:
    for line in checksums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == archive_name:
            return parts[0]
    raise BootstrapError(f"Node checksum missing for {archive_name}")


def _verify_sha256(path: Path, expected: str) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise BootstrapError(f"checksum mismatch for {path.name}")


@contextmanager
def _runtime_lock(runtime_root: Path) -> Iterator[None]:
    lock_path = runtime_root / ".bootstrap.lock"
    lock_file = lock_path.open("a+")
    try:
        if os.name == "posix":
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if os.name == "posix":
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


if __name__ == "__main__":
    raise SystemExit(main())
