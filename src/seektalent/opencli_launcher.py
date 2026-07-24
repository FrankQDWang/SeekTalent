from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path, PurePosixPath

from seektalent.browser_bridge_manifest import (
    WTSCLI_PACKAGE,
    WTSCLI_VERSION,
    BrowserBridgeManifestError,
    BrowserBridgeRequirement,
    load_browser_bridge_requirement,
    load_runtime_package_identity,
)
from seektalent.strict_json import StrictJsonError, strict_json_object_loads


# These internal names remain stable for callers; their authority is the WTS
# manifest, not the historical OpenCLI package or state layout.
OPENCLI_PACKAGE = WTSCLI_PACKAGE
OPENCLI_VERSION = WTSCLI_VERSION
VERIFICATION_STAMP_SCHEMA_VERSION = "seektalent.wtscli_runtime_verification.v1"
VERIFICATION_STAMP_FILENAME = ".seektalent-wtscli-verified.json"
RUNTIME_ROOT = Path.home() / ".seektalent" / "wtscli-runtime"
PROVIDER_SECRET_ENV_VARS = frozenset(
    {
        "SEEKTALENT_TEXT_LLM_API_KEY",
        "SEEKTALENT_DOMI_JWT",
        "SEEKTALENT_DOMI_LLM_BASE_URL",
        "SEEKTALENT_DOMI_LLM_CHANNEL",
    }
)
EXPLICIT_OPENCLI_NODE_ENV = "SEEKTALENT_WTSCLI_NODE"
DOMI_NODE_ENV_VARS = ("SEEKTALENT_DOMI_NODE", "DOMI_NODE")


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        runtime = ensure_opencli_runtime()
    except BootstrapError as exc:
        print(f"SeekTalent WTSCLI bootstrap failed: {exc}", file=sys.stderr)
        return 127
    env = opencli_subprocess_env(
        node_bin_dir=runtime.node_bin_dir,
        requirement=runtime_requirement(runtime),
    )
    completed = subprocess.run(
        (str(runtime.node), str(runtime.opencli_main), *args),
        env=env,
        check=False,
    )
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
        requirement: BrowserBridgeRequirement | None = None,
    ) -> None:
        self.node = node
        self.opencli_main = opencli_main
        self.bridge_manifest = bridge_manifest
        self.requirement = requirement

    @property
    def node_bin_dir(self) -> Path:
        return self.node.parent


def ensure_opencli_runtime(
    *,
    root: Path | None = None,
    opencli_version: str | None = None,
    env: Mapping[str, str] | None = None,
) -> OpenCliRuntime:
    runtime_root = (root or RUNTIME_ROOT).expanduser().absolute()
    external_node = _configured_node_from_env(env)
    if external_node is None:
        raise BootstrapError(
            "domi_node_missing: SEEKTALENT_WTSCLI_NODE, SEEKTALENT_DOMI_NODE, or DOMI_NODE is required"
        )
    runtime_anchor = (
        runtime_root.parent.parent
        if runtime_root.parent.name == ".seektalent"
        else runtime_root.parent
    )
    _reject_runtime_symlink_components(runtime_anchor, runtime_root)
    if not runtime_root.is_dir():
        raise BootstrapError(
            f"opencli_offline_runtime_missing: Reinstall SeekTalent to restore {runtime_root}"
        )
    runtime_root = runtime_root.resolve(strict=True)
    with _runtime_lock(runtime_root):
        bridge_manifest = _bridge_manifest_path(runtime_root)
        _reject_runtime_symlink_components(runtime_root.parent, bridge_manifest)
        requirement = _load_bridge_requirement(bridge_manifest)
        if opencli_version is not None and opencli_version != requirement.cli.version:
            raise BootstrapError(
                "opencli_bridge_build_mismatch: Caller-selected WTSCLI versions are not supported"
            )
        node = _require_domi_node_file(external_node)
        install_dir = _opencli_install_dir(runtime_root, requirement.cli.version)
        package_dir = _opencli_package_dir(install_dir, requirement)
        _reject_runtime_symlink_components(runtime_root, package_dir)
        package_json = package_dir / "package.json"
        bridge_identity = package_dir / "bridge-identity.json"
        opencli_main = _require_installed_opencli(
            package_dir,
            requirement=requirement,
        )
        _verify_runtime_bridge_identity(bridge_identity, requirement)
        stamp_path = _verification_stamp_path(install_dir)
        if not _verification_stamp_matches(
            stamp_path,
            node=node,
            opencli_main=opencli_main,
            package_json=package_json,
            bridge_identity=bridge_identity,
            bridge_manifest=bridge_manifest,
            requirement=requirement,
        ):
            _verify_domi_node(node)
            _probe_opencli_cli(
                node=node,
                opencli_main=opencli_main,
                requirement=requirement,
            )
            _write_verification_stamp(
                stamp_path,
                node=node,
                opencli_main=opencli_main,
                package_json=package_json,
                bridge_identity=bridge_identity,
                bridge_manifest=bridge_manifest,
                requirement=requirement,
            )
    return OpenCliRuntime(
        node=node,
        opencli_main=opencli_main,
        bridge_manifest=bridge_manifest,
        requirement=requirement,
    )


def runtime_requirement(runtime: OpenCliRuntime) -> BrowserBridgeRequirement:
    if runtime.requirement is not None:
        return runtime.requirement
    if runtime.bridge_manifest is None:
        raise BootstrapError("opencli_bridge_integrity_failed: Missing WTSCLI bridge manifest")
    return _load_bridge_requirement(runtime.bridge_manifest)


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


def _require_installed_opencli(
    package_dir: Path,
    *,
    requirement: BrowserBridgeRequirement,
) -> Path:
    package_json_path = package_dir / "package.json"
    _reject_runtime_symlink_components(package_dir, package_json_path)
    try:
        package_json = strict_json_object_loads(package_json_path.read_bytes())
    except (OSError, StrictJsonError) as exc:
        raise BootstrapError(
            f"opencli_offline_runtime_missing: Reinstall SeekTalent to restore WTSCLI {requirement.cli.version}"
        ) from exc
    bin_mapping = package_json.get("bin")
    if (
        package_json.get("name") != requirement.cli.package
        or package_json.get("version") != requirement.cli.version
        or type(bin_mapping) is not dict
        or set(bin_mapping) != {requirement.cli.entrypoint}
    ):
        raise BootstrapError(
            f"opencli_offline_runtime_missing: Reinstall SeekTalent to restore WTSCLI {requirement.cli.version}"
        )
    entrypoint = next(iter(bin_mapping.values()))
    if type(entrypoint) is not str:
        raise BootstrapError("opencli_bridge_integrity_failed: Invalid WTSCLI entrypoint")
    main = _package_path(package_dir, entrypoint)
    if main.is_symlink() or not main.is_file():
        raise BootstrapError(
            f"opencli_offline_runtime_missing: Reinstall SeekTalent to restore WTSCLI {requirement.cli.version}"
        )
    bridge_identity = package_dir / "bridge-identity.json"
    _reject_runtime_symlink_components(package_dir, bridge_identity)
    if not bridge_identity.is_file():
        raise BootstrapError(
            "opencli_bridge_integrity_failed: Installed WTSCLI has no SeekTalent bridge identity"
        )
    return main


def _package_path(package_dir: Path, value: str) -> Path:
    relative = PurePosixPath(value)
    if relative.is_absolute() or not relative.parts or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise BootstrapError("opencli_bridge_integrity_failed: Invalid WTSCLI entrypoint")
    _reject_runtime_symlink_components(package_dir.parent, package_dir)
    resolved_package = package_dir.resolve(strict=True)
    unresolved_candidate = package_dir.joinpath(*relative.parts)
    _reject_runtime_symlink_components(package_dir, unresolved_candidate)
    candidate = unresolved_candidate.resolve(strict=False)
    if not candidate.is_relative_to(resolved_package):
        raise BootstrapError("opencli_bridge_integrity_failed: Invalid WTSCLI entrypoint")
    return candidate


def _opencli_install_dir(runtime_root: Path, opencli_version: str) -> Path:
    return runtime_root / WTSCLI_PACKAGE / opencli_version


def _opencli_package_dir(
    install_dir: Path,
    requirement: BrowserBridgeRequirement | None = None,
) -> Path:
    package = requirement.runtime_identity.package.name if requirement else WTSCLI_PACKAGE
    return install_dir / "node_modules" / package


def _opencli_main_path(
    install_dir: Path,
    requirement: BrowserBridgeRequirement | None = None,
) -> Path:
    return _opencli_package_dir(install_dir, requirement) / "dist" / "src" / "main.js"


def _opencli_package_json_path(
    install_dir: Path,
    requirement: BrowserBridgeRequirement | None = None,
) -> Path:
    return _opencli_package_dir(install_dir, requirement) / "package.json"


def _opencli_bridge_identity_path(
    install_dir: Path,
    requirement: BrowserBridgeRequirement | None = None,
) -> Path:
    return _opencli_package_dir(install_dir, requirement) / "bridge-identity.json"


def _bridge_manifest_path(runtime_root: Path) -> Path:
    return runtime_root.parent / "browser-bridge" / "bridge-manifest.json"


def _reject_runtime_symlink_components(root: Path, path: Path) -> None:
    try:
        relative = path.absolute().relative_to(root.absolute())
    except ValueError as exc:
        raise BootstrapError(
            "opencli_bridge_integrity_failed: WTSCLI managed path escaped its root"
        ) from exc
    current = root.absolute()
    for part in relative.parts:
        current /= part
        if os.path.lexists(current) and current.is_symlink():
            raise BootstrapError(
                "opencli_bridge_integrity_failed: WTSCLI managed paths must not be symlinked"
            )


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
        identity = load_runtime_package_identity(path)
    except BrowserBridgeManifestError as exc:
        raise BootstrapError(
            "opencli_bridge_integrity_failed: Invalid runtime bridge identity"
        ) from exc
    if (
        identity.implementation != requirement.implementation
        or identity.bridge_build_id != requirement.bridge_build_id
        or identity.runtime_identity != requirement.runtime_identity
        or identity.protocol_version != requirement.protocol_version
        or identity.capabilities != requirement.capabilities
    ):
        raise BootstrapError(
            "opencli_bridge_build_mismatch: Installed runtime and extension manifest are not a pair"
        )


def _probe_opencli_cli(
    *,
    node: Path,
    opencli_main: Path,
    requirement: BrowserBridgeRequirement,
) -> None:
    try:
        completed = subprocess.run(
            (str(node), str(opencli_main), "--help"),
            env=opencli_subprocess_env(
                node_bin_dir=node.parent,
                requirement=requirement,
            ),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BootstrapError(f"WTSCLI {requirement.cli.version} usability probe failed") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        suffix = f": {detail[:500]}" if detail else ""
        raise BootstrapError(
            f"WTSCLI {requirement.cli.version} usability probe failed{suffix}"
        )


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
    requirement: BrowserBridgeRequirement,
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
        requirement=requirement,
    )


def _write_verification_stamp(
    stamp_path: Path,
    *,
    node: Path,
    opencli_main: Path,
    package_json: Path,
    bridge_identity: Path,
    bridge_manifest: Path,
    requirement: BrowserBridgeRequirement,
) -> None:
    payload = _verification_payload(
        node=node,
        opencli_main=opencli_main,
        package_json=package_json,
        bridge_identity=bridge_identity,
        bridge_manifest=bridge_manifest,
        requirement=requirement,
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
                requirement=requirement,
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
    requirement: BrowserBridgeRequirement,
) -> dict[str, object]:
    return {
        "schema_version": VERIFICATION_STAMP_SCHEMA_VERSION,
        "package": requirement.cli.package,
        "entrypoint": requirement.cli.entrypoint,
        "version": requirement.cli.version,
        "bridge_build_id": requirement.bridge_build_id,
        "node": _file_fingerprint(node),
        "main": _file_fingerprint(opencli_main),
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


def opencli_subprocess_env(
    *,
    node_bin_dir: Path,
    requirement: BrowserBridgeRequirement,
) -> dict[str, str]:
    state = requirement.runtime_identity.state
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in PROVIDER_SECRET_ENV_VARS
        and not key.startswith("OPENCLI_")
        and not key.startswith(state.env_prefix)
    }
    state_root = state.resolve_root()
    env[state.config_dir_env] = str(state_root)
    env[state.cache_dir_env] = str(state_root / "cache")
    env["PATH"] = os.pathsep.join((str(node_bin_dir), env.get("PATH", "")))
    return env


@contextmanager
def _runtime_lock(runtime_root: Path) -> Iterator[None]:
    lock_path = runtime_root / ".bootstrap.lock"
    _reject_runtime_symlink_components(runtime_root, lock_path)
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
