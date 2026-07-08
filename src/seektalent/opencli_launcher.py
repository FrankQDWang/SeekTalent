from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path


OPENCLI_PACKAGE = "@jackwener/opencli"
OPENCLI_VERSION = "1.8.6"
VERIFICATION_STAMP_SCHEMA_VERSION = "seektalent.opencli_runtime_verification.v1"
VERIFICATION_STAMP_FILENAME = ".seektalent-opencli-verified.json"
RUNTIME_ROOT = Path.home() / ".seektalent" / "opencli-runtime"
PROVIDER_SECRET_ENV_VARS = frozenset(
    {
        "SEEKTALENT_TEXT_LLM_API_KEY",
        "SEEKTALENT_DOMI_JWT",
        "SEEKTALENT_DOMI_LLM_BASE_URL",
        "SEEKTALENT_DOMI_LLM_CHANNEL",
    }
)
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
    opencli_version: str = OPENCLI_VERSION,
    env: Mapping[str, str] | None = None,
) -> OpenCliRuntime:
    runtime_root = (root or RUNTIME_ROOT).expanduser()
    external_node = _configured_node_from_env(env)
    if external_node is None:
        raise BootstrapError(
            "domi_node_missing: SEEKTALENT_OPENCLI_NODE, SEEKTALENT_DOMI_NODE, or DOMI_NODE is required"
        )
    runtime_root.mkdir(parents=True, exist_ok=True)
    with _runtime_lock(runtime_root):
        node = _require_domi_node_file(external_node)
        install_dir = _opencli_install_dir(runtime_root, opencli_version)
        node_verified = False
        if not _managed_opencli_is_complete(install_dir, opencli_version=opencli_version):
            _verify_domi_node(node)
            node_verified = True
        opencli_main = _ensure_managed_opencli(runtime_root, node=node, opencli_version=opencli_version)
        package_json = _opencli_package_json_path(install_dir)
        stamp_path = _verification_stamp_path(install_dir)
        if not _verification_stamp_matches(
            stamp_path,
            node=node,
            opencli_main=opencli_main,
            package_json=package_json,
            opencli_version=opencli_version,
        ):
            if not node_verified:
                _verify_domi_node(node)
            _probe_opencli_cli(node=node, opencli_main=opencli_main, opencli_version=opencli_version)
            _write_verification_stamp(
                stamp_path,
                node=node,
                opencli_main=opencli_main,
                package_json=package_json,
                opencli_version=opencli_version,
            )
    return OpenCliRuntime(node=node, opencli_main=opencli_main)


def _configured_node_from_env(env: Mapping[str, str] | None = None) -> Path | None:
    source_env = os.environ if env is None else env
    for key in (EXPLICIT_OPENCLI_NODE_ENV, *DOMI_NODE_ENV_VARS):
        raw = source_env.get(key)
        if raw and raw.strip():
            return _resolve_node_env_path(raw)
    return None


def _resolve_node_env_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    if path.is_dir():
        return path / ("node.exe" if sys.platform == "win32" else "node")
    return path


def _require_domi_node_file(node: Path) -> Path:
    if not node.is_file():
        raise BootstrapError(f"domi_node_missing: Node runtime is not an executable file: {node}")
    if sys.platform != "win32" and not os.access(node, os.X_OK):
        raise BootstrapError(f"domi_node_missing: Node runtime is not executable: {node}")
    return node


def _verify_domi_node(node: Path) -> None:
    try:
        _probe_node_version(node)
    except BootstrapError as exc:
        message = str(exc)
        if message.startswith("domi_node_missing:"):
            raise
        raise BootstrapError(f"domi_node_missing: {message}") from exc


def _probe_node_version(node: Path) -> None:
    try:
        completed = subprocess.run(
            (str(node), "--version"),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BootstrapError(f"Node runtime failed version probe: {node}") from exc
    output = (completed.stdout or completed.stderr or "").strip()
    if completed.returncode != 0:
        detail = f": {output[:200]}" if output else ""
        raise BootstrapError(f"Node runtime failed version probe: {node}{detail}")
    if not output.startswith("v"):
        raise BootstrapError(f"Node runtime returned an unexpected version: {node}")


def _ensure_managed_opencli(runtime_root: Path, *, node: Path, opencli_version: str) -> Path:
    install_dir = _opencli_install_dir(runtime_root, opencli_version)
    main = _opencli_main_path(install_dir)
    package_json = _opencli_package_json_path(install_dir)
    if _managed_opencli_is_complete(install_dir, opencli_version=opencli_version):
        return main
    npm_command = _npm_command_for_node(node)
    install_dir.mkdir(parents=True, exist_ok=True)
    env = _opencli_subprocess_env(node_bin_dir=node.parent)
    completed = subprocess.run(
        (
            *(str(part) for part in npm_command),
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
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise BootstrapError(f"OpenCLI {opencli_version} install failed: {detail[:500]}")
    if not main.exists() or _package_version(package_json) != opencli_version:
        raise BootstrapError(f"OpenCLI {opencli_version} install is incomplete")
    return main


def _opencli_install_dir(runtime_root: Path, opencli_version: str) -> Path:
    return runtime_root / "opencli" / opencli_version


def _opencli_package_dir(install_dir: Path) -> Path:
    return install_dir / "node_modules" / "@jackwener" / "opencli"


def _opencli_main_path(install_dir: Path) -> Path:
    return _opencli_package_dir(install_dir) / "dist" / "src" / "main.js"


def _opencli_package_json_path(install_dir: Path) -> Path:
    return _opencli_package_dir(install_dir) / "package.json"


def _managed_opencli_is_complete(install_dir: Path, *, opencli_version: str) -> bool:
    return _opencli_main_path(install_dir).exists() and _package_version(
        _opencli_package_json_path(install_dir)
    ) == opencli_version


def _probe_opencli_cli(*, node: Path, opencli_main: Path, opencli_version: str) -> None:
    try:
        completed = subprocess.run(
            (str(node), str(opencli_main), "--help"),
            env=_opencli_subprocess_env(node_bin_dir=node.parent),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BootstrapError(f"OpenCLI {opencli_version} usability probe failed") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        suffix = f": {detail[:500]}" if detail else ""
        raise BootstrapError(f"OpenCLI {opencli_version} usability probe failed{suffix}")


def _verification_stamp_path(install_dir: Path) -> Path:
    return install_dir / VERIFICATION_STAMP_FILENAME


def _verification_stamp_matches(
    stamp_path: Path,
    *,
    node: Path,
    opencli_main: Path,
    package_json: Path,
    opencli_version: str,
) -> bool:
    try:
        data = json.loads(stamp_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return data == _verification_payload(
        node=node,
        opencli_main=opencli_main,
        package_json=package_json,
        opencli_version=opencli_version,
    )


def _write_verification_stamp(
    stamp_path: Path,
    *,
    node: Path,
    opencli_main: Path,
    package_json: Path,
    opencli_version: str,
) -> None:
    payload = _verification_payload(
        node=node,
        opencli_main=opencli_main,
        package_json=package_json,
        opencli_version=opencli_version,
    )
    stamp_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=stamp_path.parent,
        prefix=f"{stamp_path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary_path = Path(temporary_file.name)
    try:
        with temporary_file:
            json.dump(payload, temporary_file, sort_keys=True)
        try:
            temporary_path.replace(stamp_path)
        except OSError:
            if _verification_stamp_matches(
                stamp_path,
                node=node,
                opencli_main=opencli_main,
                package_json=package_json,
                opencli_version=opencli_version,
            ):
                return
            raise
    finally:
        with suppress(FileNotFoundError):
            temporary_path.unlink()


def _verification_payload(*, node: Path, opencli_main: Path, package_json: Path, opencli_version: str) -> dict[str, object]:
    return {
        "schema_version": VERIFICATION_STAMP_SCHEMA_VERSION,
        "opencli_package": OPENCLI_PACKAGE,
        "opencli_version": opencli_version,
        "node": _file_fingerprint(node),
        "opencli_main": _file_fingerprint(opencli_main),
        "package_json": _file_fingerprint(package_json),
    }


def _file_fingerprint(path: Path) -> dict[str, object]:
    stat = path.stat()
    return {
        "path": str(path),
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
    }


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


def _npm_command_for_node(node: Path) -> tuple[Path, ...]:
    try:
        return (_npm_for_node(node),)
    except BootstrapError:
        npm_cli = node.parent / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"
        if npm_cli.exists():
            return (node, npm_cli)
        raise


def _package_version(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    version = data.get("version")
    return version if isinstance(version, str) else None


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
