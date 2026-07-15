from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path

from seektalent.browser_bridge_manifest import (
    BrowserBridgeManifestError,
    BrowserBridgeRequirement,
    load_browser_bridge_requirement,
)


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
    env = opencli_subprocess_env(node_bin_dir=runtime.node_bin_dir)
    completed = subprocess.run((str(runtime.node), str(runtime.opencli_main), *args), env=env, check=False)
    return completed.returncode


class BootstrapError(RuntimeError):
    pass


class OpenCliRuntime:
    def __init__(
        self,
        *,
        node: Path,
        opencli_main: Path,
        bridge_manifest: Path | None = None,
    ) -> None:
        self.node = node
        self.opencli_main = opencli_main
        self.bridge_manifest = bridge_manifest

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
    if not runtime_root.is_dir():
        raise BootstrapError(
            f"opencli_offline_runtime_missing: Reinstall SeekTalent to restore {runtime_root}"
        )
    with _runtime_lock(runtime_root):
        node = _require_domi_node_file(external_node)
        install_dir = _opencli_install_dir(runtime_root, opencli_version)
        opencli_main = _require_installed_opencli(install_dir, opencli_version=opencli_version)
        package_json = _opencli_package_json_path(install_dir)
        bridge_identity = _opencli_bridge_identity_path(install_dir)
        bridge_manifest = _bridge_manifest_path(runtime_root)
        requirement = _load_bridge_requirement(bridge_manifest)
        _verify_runtime_bridge_identity(bridge_identity, requirement)
        stamp_path = _verification_stamp_path(install_dir)
        if not _verification_stamp_matches(
            stamp_path,
            node=node,
            opencli_main=opencli_main,
            package_json=package_json,
            bridge_identity=bridge_identity,
            bridge_manifest=bridge_manifest,
            opencli_version=opencli_version,
        ):
            _verify_domi_node(node)
            _probe_opencli_cli(node=node, opencli_main=opencli_main, opencli_version=opencli_version)
            _write_verification_stamp(
                stamp_path,
                node=node,
                opencli_main=opencli_main,
                package_json=package_json,
                bridge_identity=bridge_identity,
                bridge_manifest=bridge_manifest,
                opencli_version=opencli_version,
            )
    return OpenCliRuntime(
        node=node,
        opencli_main=opencli_main,
        bridge_manifest=bridge_manifest,
    )


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


def _require_installed_opencli(install_dir: Path, *, opencli_version: str) -> Path:
    main = _opencli_main_path(install_dir)
    package_json = _opencli_package_json_path(install_dir)
    if not main.exists() or _package_version(package_json) != opencli_version:
        raise BootstrapError(
            f"opencli_offline_runtime_missing: Reinstall SeekTalent to restore OpenCLI {opencli_version}"
        )
    if not _opencli_bridge_identity_path(install_dir).is_file():
        raise BootstrapError(
            "opencli_bridge_integrity_failed: Installed OpenCLI has no SeekTalent bridge identity"
        )
    return main


def _opencli_install_dir(runtime_root: Path, opencli_version: str) -> Path:
    return runtime_root / "opencli" / opencli_version


def _opencli_package_dir(install_dir: Path) -> Path:
    return install_dir / "node_modules" / "@jackwener" / "opencli"


def _opencli_main_path(install_dir: Path) -> Path:
    return _opencli_package_dir(install_dir) / "dist" / "src" / "main.js"


def _opencli_package_json_path(install_dir: Path) -> Path:
    return _opencli_package_dir(install_dir) / "package.json"


def _opencli_bridge_identity_path(install_dir: Path) -> Path:
    return _opencli_package_dir(install_dir) / "bridge-identity.json"


def _bridge_manifest_path(runtime_root: Path) -> Path:
    return runtime_root.parent / "browser-bridge" / "bridge-manifest.json"


def _load_bridge_requirement(path: Path) -> BrowserBridgeRequirement:
    try:
        return load_browser_bridge_requirement(path)
    except BrowserBridgeManifestError as exc:
        raise BootstrapError(
            f"opencli_bridge_{exc.code}: Reinstall the SeekTalent browser bridge"
        ) from exc


def _verify_runtime_bridge_identity(
    path: Path,
    requirement: BrowserBridgeRequirement,
) -> None:
    try:
        identity = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BootstrapError("opencli_bridge_integrity_failed: Invalid runtime bridge identity") from exc
    expected_protocol = {
        "major": requirement.protocol_major,
        "minor": requirement.protocol_minor,
    }
    if (
        not isinstance(identity, dict)
        or identity.get("implementation") != requirement.implementation
        or identity.get("bridgeBuildId") != requirement.bridge_build_id
        or identity.get("protocolVersion") != expected_protocol
        or identity.get("capabilities") != sorted(requirement.capabilities)
    ):
        raise BootstrapError(
            "opencli_bridge_build_mismatch: Installed runtime and extension manifest are not a pair"
        )


def _probe_opencli_cli(*, node: Path, opencli_main: Path, opencli_version: str) -> None:
    try:
        completed = subprocess.run(
            (str(node), str(opencli_main), "--help"),
            env=opencli_subprocess_env(node_bin_dir=node.parent),
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
    bridge_identity: Path,
    bridge_manifest: Path,
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
        bridge_identity=bridge_identity,
        bridge_manifest=bridge_manifest,
        opencli_version=opencli_version,
    )


def _write_verification_stamp(
    stamp_path: Path,
    *,
    node: Path,
    opencli_main: Path,
    package_json: Path,
    bridge_identity: Path,
    bridge_manifest: Path,
    opencli_version: str,
) -> None:
    payload = _verification_payload(
        node=node,
        opencli_main=opencli_main,
        package_json=package_json,
        bridge_identity=bridge_identity,
        bridge_manifest=bridge_manifest,
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
                bridge_identity=bridge_identity,
                bridge_manifest=bridge_manifest,
                opencli_version=opencli_version,
            ):
                return
            raise
    finally:
        with suppress(FileNotFoundError):
            temporary_path.unlink()


def _verification_payload(
    *,
    node: Path,
    opencli_main: Path,
    package_json: Path,
    bridge_identity: Path,
    bridge_manifest: Path,
    opencli_version: str,
) -> dict[str, object]:
    return {
        "schema_version": VERIFICATION_STAMP_SCHEMA_VERSION,
        "opencli_package": OPENCLI_PACKAGE,
        "opencli_version": opencli_version,
        "node": _file_fingerprint(node),
        "opencli_main": _file_fingerprint(opencli_main),
        "package_json": _file_fingerprint(package_json),
        "bridge_identity": _file_fingerprint(bridge_identity),
        "bridge_manifest": _file_fingerprint(bridge_manifest),
    }


def _file_fingerprint(path: Path) -> dict[str, object]:
    stat = path.stat()
    return {
        "path": str(path),
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
    }


def opencli_subprocess_env(*, node_bin_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in PROVIDER_SECRET_ENV_VARS:
        env.pop(key, None)
    env["PATH"] = os.pathsep.join((str(node_bin_dir), env.get("PATH", "")))
    return env


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
