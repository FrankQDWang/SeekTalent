from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from seektalent.browser_bridge_manifest import (
    BrowserBridgeBundle,
    BrowserBridgeExtensionFile,
    BrowserBridgeManifestError,
    BrowserBridgeRequirement,
    load_browser_bridge_bundle,
    load_runtime_package_identity,
)
from seektalent.browser_bridge_runtime_receipt import (
    bind_runtime_package_receipt,
    verify_installed_runtime_package,
)
from seektalent.strict_json import StrictJsonError, strict_json_object_loads


_MAX_RUNTIME_FILES = 20_000
_MAX_RUNTIME_UNPACKED_BYTES = 512 * 1024 * 1024
_NPM_INSTALL_TIMEOUT_SECONDS = 300


@dataclass(frozen=True, slots=True)
class BrowserBridgeInstallResult:
    runtime_dir: Path
    runtime_main: Path
    extension_dir: Path
    manifest_path: Path
    bridge_build_id: str
    extension_version: str


def install_browser_bridge_bundle(
    *,
    bundle_dir: Path,
    install_root: Path,
    node: Path,
    prepared_runtime_dir: Path | None = None,
    additional_targets: tuple[tuple[Path | None, Path], ...] = (),
) -> BrowserBridgeInstallResult:
    """Verify, stage, and atomically switch one WTSCLI runtime/extension pair."""
    bundle = load_browser_bridge_bundle(bundle_dir)
    resolved_install_root = _resolve_install_root(install_root.expanduser())
    resolved_node = node.expanduser().resolve(strict=False)
    _require_node(resolved_node)
    targets = _install_targets(resolved_install_root, bundle.requirement)
    requested_targets = (
        targets.runtime_dir,
        targets.extension_dir,
        targets.manifest_path,
        *(target for _source, target in additional_targets),
    )
    _validate_target_paths(resolved_install_root, requested_targets)
    created_staging_parents = _ensure_parent_directory(
        resolved_install_root.parent,
    )
    try:
        with tempfile.TemporaryDirectory(
            prefix=f".{resolved_install_root.name}.wtscli-install-stage-",
            dir=resolved_install_root.parent,
        ) as temporary:
            stage_root = Path(temporary)
            runtime_stage = stage_root / ".stage-runtime"
            extension_stage = stage_root / ".stage-extension"
            manifest_stage = stage_root / ".stage-bridge-manifest.json"
            runtime_package_stage = stage_root / ".stage-runtime-package.tgz"
            exact_runtime_stage = stage_root / ".stage-exact-runtime"
            candidate_home = stage_root / ".candidate-home"
            candidate_home.mkdir()
            shutil.copy2(bundle.runtime_package, runtime_package_stage)
            if (
                runtime_package_stage.stat().st_size != bundle.requirement.cli.size
                or _file_sha256(runtime_package_stage) != bundle.requirement.cli.sha256
            ):
                raise BrowserBridgeManifestError("integrity_failed")
            exact_package_dir = (
                exact_runtime_stage
                / "node_modules"
                / bundle.requirement.runtime_identity.package.name
            )
            _extract_runtime_package(runtime_package_stage, exact_package_dir)
            if prepared_runtime_dir is not None:
                _copy_prepared_runtime(
                    source=prepared_runtime_dir,
                    target=runtime_stage,
                )
            elif _runtime_dependencies(exact_package_dir):
                _install_runtime_with_npm(
                    runtime_package=runtime_package_stage,
                    runtime_dir=runtime_stage,
                    node=resolved_node,
                    requirement=bundle.requirement,
                    state_home=candidate_home,
                )
            else:
                os.replace(exact_runtime_stage, runtime_stage)
            _remove_runtime_bin_links(runtime_stage)
            bind_runtime_package_receipt(
                runtime_dir=runtime_stage,
                runtime_package=runtime_package_stage,
                requirement=bundle.requirement,
            )
            shutil.copytree(bundle.extension_dir, extension_stage)
            shutil.copy2(bundle.manifest_path, manifest_stage)
            runtime_main = _verify_staged_pair(
                bundle=bundle,
                runtime_dir=runtime_stage,
                extension_dir=extension_stage,
                manifest_path=manifest_stage,
                node=resolved_node,
                state_home=candidate_home,
            )
            relative_main = runtime_main.relative_to(runtime_stage)
            staged_additional = _stage_additional_targets(
                stage_root=stage_root,
                additional_targets=additional_targets,
            )
            staged = (
                (runtime_stage, targets.runtime_dir),
                (extension_stage, targets.extension_dir),
                (manifest_stage, targets.manifest_path),
                *staged_additional,
            )
            created_target_parents = _prepare_target_parents(
                resolved_install_root,
                tuple(target for _source, target in staged),
            )
            try:
                _activate_pair(
                    staged=staged,
                    install_root=resolved_install_root,
                )
            except (OSError, BrowserBridgeManifestError, RuntimeError):
                _remove_empty_directories(created_target_parents)
                raise
    finally:
        _remove_empty_directories(created_staging_parents)

    return BrowserBridgeInstallResult(
        runtime_dir=targets.runtime_dir,
        runtime_main=targets.runtime_dir / relative_main,
        extension_dir=targets.extension_dir,
        manifest_path=targets.manifest_path,
        bridge_build_id=bundle.bridge_build_id,
        extension_version=bundle.extension_version,
    )


@dataclass(frozen=True, slots=True)
class _InstallTargets:
    runtime_dir: Path
    extension_dir: Path
    manifest_path: Path


def _resolve_install_root(install_root: Path) -> Path:
    resolved = install_root.absolute()
    try:
        if os.path.lexists(resolved) and (
            resolved.is_symlink() or not resolved.is_dir()
        ):
            raise BrowserBridgeManifestError("integrity_failed")
    except OSError as exc:
        raise BrowserBridgeManifestError("integrity_failed") from exc
    return resolved


def _reject_relative_symlink_components(root: Path, path: Path) -> None:
    try:
        relative = path.absolute().relative_to(root)
    except ValueError as exc:
        raise BrowserBridgeManifestError("integrity_failed") from exc
    current = root
    for part in relative.parts:
        current /= part
        if os.path.lexists(current) and current.is_symlink():
            raise BrowserBridgeManifestError("integrity_failed")


def _validate_target_paths(
    install_root: Path,
    targets: tuple[Path, ...],
) -> None:
    seen: set[Path] = set()
    for raw_target in targets:
        target = raw_target.expanduser().absolute()
        if target in seen:
            raise BrowserBridgeManifestError("integrity_failed")
        seen.add(target)
        try:
            relative = target.relative_to(install_root)
        except ValueError as exc:
            raise BrowserBridgeManifestError("integrity_failed") from exc
        if not relative.parts:
            raise BrowserBridgeManifestError("integrity_failed")
        current = install_root
        for part in relative.parts:
            current /= part
            if os.path.lexists(current) and current.is_symlink():
                raise BrowserBridgeManifestError("integrity_failed")


def _prepare_target_parents(
    install_root: Path,
    targets: tuple[Path, ...],
) -> tuple[Path, ...]:
    created: list[Path] = []
    try:
        if not install_root.exists():
            created.extend(_ensure_parent_directory(install_root))
        for target in targets:
            try:
                relative_parent = target.parent.relative_to(install_root)
            except ValueError as exc:
                raise BrowserBridgeManifestError("integrity_failed") from exc
            current = install_root
            for part in relative_parent.parts:
                current /= part
                try:
                    if os.path.lexists(current):
                        if current.is_symlink() or not current.is_dir():
                            raise BrowserBridgeManifestError("integrity_failed")
                    else:
                        current.mkdir()
                        created.append(current)
                except OSError as exc:
                    raise BrowserBridgeManifestError("integrity_failed") from exc
            if os.path.lexists(target) and target.is_symlink():
                raise BrowserBridgeManifestError("integrity_failed")
    except (OSError, BrowserBridgeManifestError):
        _remove_empty_directories(tuple(created))
        raise
    return tuple(created)


def _ensure_parent_directory(path: Path) -> tuple[Path, ...]:
    missing: list[Path] = []
    current = path
    while not current.exists():
        if os.path.lexists(current) and current.is_symlink():
            raise BrowserBridgeManifestError("integrity_failed")
        missing.append(current)
        if current.parent == current:
            raise BrowserBridgeManifestError("integrity_failed")
        current = current.parent
    if current.is_symlink() or not current.is_dir():
        raise BrowserBridgeManifestError("integrity_failed")
    created: list[Path] = []
    try:
        for directory in reversed(missing):
            directory.mkdir()
            created.append(directory)
    except OSError as exc:
        _remove_empty_directories(tuple(created))
        raise BrowserBridgeManifestError("integrity_failed") from exc
    return tuple(created)


def _remove_empty_directories(paths: tuple[Path, ...]) -> None:
    for path in reversed(paths):
        try:
            path.rmdir()
        except OSError:
            break


def _stage_additional_targets(
    *,
    stage_root: Path,
    additional_targets: tuple[tuple[Path | None, Path], ...],
) -> tuple[tuple[Path | None, Path], ...]:
    staged: list[tuple[Path | None, Path]] = []
    for index, (raw_source, raw_target) in enumerate(additional_targets):
        target = raw_target.expanduser().absolute()
        if raw_source is None:
            staged.append((None, target))
            continue
        source = raw_source.expanduser().absolute()
        staged_source = stage_root / f".stage-additional-{index}"
        try:
            if source.is_symlink():
                raise BrowserBridgeManifestError("integrity_failed")
            if source.is_dir():
                if any(candidate.is_symlink() for candidate in source.rglob("*")):
                    raise BrowserBridgeManifestError("integrity_failed")
                shutil.copytree(source, staged_source)
            elif source.is_file():
                shutil.copy2(source, staged_source)
            else:
                raise BrowserBridgeManifestError("integrity_failed")
        except OSError as exc:
            raise BrowserBridgeManifestError("integrity_failed") from exc
        staged.append((staged_source, target))
    return tuple(staged)


def _install_targets(
    install_root: Path,
    requirement: BrowserBridgeRequirement,
) -> _InstallTargets:
    package = requirement.runtime_identity.package.name
    version = requirement.cli.version
    return _InstallTargets(
        runtime_dir=install_root / f"{package}-runtime" / package / version,
        extension_dir=install_root / "chrome-extension" / package,
        manifest_path=install_root / "browser-bridge" / "bridge-manifest.json",
    )


def _extract_runtime_package(runtime_package: Path, package_dir: Path) -> None:
    package_dir.mkdir(parents=True)
    seen: set[str] = set()
    total_size = 0
    file_count = 0
    try:
        archive = tarfile.open(runtime_package, mode="r:gz")
    except (OSError, tarfile.TarError) as exc:
        raise BrowserBridgeManifestError("integrity_failed") from exc
    with archive:
        for member in archive:
            relative = _runtime_member_path(member.name)
            if relative is None:
                if member.isdir() and member.name.rstrip("/") == "package":
                    continue
                raise BrowserBridgeManifestError("integrity_failed")
            collision_key = relative.as_posix().casefold()
            if collision_key in seen:
                raise BrowserBridgeManifestError("integrity_failed")
            seen.add(collision_key)
            target = package_dir.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile() or member.size < 0:
                raise BrowserBridgeManifestError("integrity_failed")
            file_count += 1
            total_size += member.size
            if file_count > _MAX_RUNTIME_FILES or total_size > _MAX_RUNTIME_UNPACKED_BYTES:
                raise BrowserBridgeManifestError("integrity_failed")
            source = archive.extractfile(member)
            if source is None:
                raise BrowserBridgeManifestError("integrity_failed")
            target.parent.mkdir(parents=True, exist_ok=True)
            with source, target.open("xb") as destination:
                shutil.copyfileobj(source, destination)
            target.chmod(member.mode & 0o777)


def _runtime_member_path(value: str) -> PurePosixPath | None:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or path.parts[0] != "package":
        return None
    relative = PurePosixPath(*path.parts[1:])
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        return None
    return relative


def _verify_staged_pair(
    *,
    bundle: BrowserBridgeBundle,
    runtime_dir: Path,
    extension_dir: Path,
    manifest_path: Path,
    node: Path,
    state_home: Path,
) -> Path:
    requirement = bundle.requirement
    package_dir = runtime_dir / "node_modules" / requirement.runtime_identity.package.name
    package_json_path = package_dir / "package.json"
    identity_path = package_dir / "bridge-identity.json"
    try:
        package_json = strict_json_object_loads(package_json_path.read_bytes())
    except (OSError, StrictJsonError) as exc:
        raise BrowserBridgeManifestError("integrity_failed") from exc
    bin_mapping = package_json.get("bin")
    if (
        package_json.get("name") != requirement.cli.package
        or package_json.get("version") != requirement.cli.version
        or type(bin_mapping) is not dict
        or set(bin_mapping) != {requirement.cli.entrypoint}
    ):
        raise BrowserBridgeManifestError("integrity_failed")
    entrypoint = next(iter(bin_mapping.values()))
    if type(entrypoint) is not str:
        raise BrowserBridgeManifestError("integrity_failed")
    main = _package_path(package_dir, entrypoint)
    if main.is_symlink() or not main.is_file():
        raise BrowserBridgeManifestError("integrity_failed")
    verify_installed_runtime_package(runtime_dir, requirement=requirement)
    _require_runtime_dependencies(runtime_dir, package_dir)

    runtime_identity = load_runtime_package_identity(identity_path)
    if (
        runtime_identity.implementation != requirement.implementation
        or runtime_identity.bridge_build_id != requirement.bridge_build_id
        or runtime_identity.runtime_identity != requirement.runtime_identity
        or runtime_identity.protocol_version != requirement.protocol_version
        or runtime_identity.capabilities != requirement.capabilities
    ):
        raise BrowserBridgeManifestError("integrity_failed")
    if any(candidate.is_symlink() or candidate.suffix == ".node" for candidate in runtime_dir.rglob("*")):
        raise BrowserBridgeManifestError("integrity_failed")

    installed_requirement = load_browser_bridge_bundle(bundle.root).requirement
    if installed_requirement != requirement or manifest_path.read_bytes() != bundle.manifest_path.read_bytes():
        raise BrowserBridgeManifestError("integrity_failed")
    if _declared_extension_files(extension_dir) != requirement.extension.files:
        raise BrowserBridgeManifestError("integrity_failed")
    _probe_wtscli(
        node=node,
        main=main,
        requirement=requirement,
        state_home=state_home,
    )
    return main


def _package_path(package_dir: Path, value: str) -> Path:
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or not candidate.parts or any(part in {"", ".", ".."} for part in candidate.parts):
        raise BrowserBridgeManifestError("integrity_failed")
    resolved = package_dir.joinpath(*candidate.parts).resolve(strict=False)
    if not resolved.is_relative_to(package_dir.resolve(strict=True)):
        raise BrowserBridgeManifestError("integrity_failed")
    return resolved


def _declared_extension_files(
    extension_dir: Path,
) -> tuple[BrowserBridgeExtensionFile, ...]:
    files: list[BrowserBridgeExtensionFile] = []
    for candidate in sorted(extension_dir.rglob("*")):
        if candidate.is_symlink():
            raise BrowserBridgeManifestError("integrity_failed")
        if candidate.is_file():
            files.append(
                BrowserBridgeExtensionFile(
                    path=candidate.relative_to(extension_dir).as_posix(),
                    size=candidate.stat().st_size,
                    sha256=_file_sha256(candidate),
                )
            )
    return tuple(files)


def _runtime_dependencies(package_dir: Path) -> tuple[str, ...]:
    try:
        package_json = strict_json_object_loads((package_dir / "package.json").read_bytes())
    except (OSError, StrictJsonError) as exc:
        raise BrowserBridgeManifestError("integrity_failed") from exc
    raw_dependencies = package_json.get("dependencies", {})
    if type(raw_dependencies) is not dict:
        raise BrowserBridgeManifestError("integrity_failed")
    dependencies: list[str] = []
    for name, version in raw_dependencies.items():
        if type(name) is not str or not name or type(version) is not str or not version:
            raise BrowserBridgeManifestError("integrity_failed")
        dependencies.append(name)
    return tuple(sorted(dependencies))


def _require_runtime_dependencies(runtime_dir: Path, package_dir: Path) -> None:
    for dependency in _runtime_dependencies(package_dir):
        parts = dependency.split("/")
        if (
            any(part in {"", ".", ".."} for part in parts)
            or (dependency.startswith("@") and len(parts) != 2)
            or (not dependency.startswith("@") and len(parts) != 1)
        ):
            raise BrowserBridgeManifestError("integrity_failed")
        candidates = (
            package_dir.joinpath("node_modules", *parts, "package.json"),
            runtime_dir.joinpath("node_modules", *parts, "package.json"),
        )
        if not any(
            candidate.is_file() and not candidate.is_symlink()
            for candidate in candidates
        ):
            raise BrowserBridgeManifestError("integrity_failed")


def _copy_prepared_runtime(*, source: Path, target: Path) -> None:
    source = source.expanduser().absolute()
    if source.is_symlink() or not source.is_dir():
        raise BrowserBridgeManifestError("integrity_failed")
    try:
        shutil.copytree(source, target, symlinks=True)
    except OSError as exc:
        raise BrowserBridgeManifestError("integrity_failed") from exc


def _install_runtime_with_npm(
    *,
    runtime_package: Path,
    runtime_dir: Path,
    node: Path,
    requirement: BrowserBridgeRequirement,
    state_home: Path,
) -> None:
    npm_cli = _npm_cli_for_node(node)
    env = _wtscli_env(requirement, state_home=state_home)
    env["npm_config_cache"] = str(runtime_dir.parent / ".npm-cache")
    try:
        completed = subprocess.run(
            (
                str(node),
                str(npm_cli),
                "install",
                "--prefix",
                str(runtime_dir),
                "--omit=dev",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
                "--no-package-lock",
                str(runtime_package),
            ),
            env=env,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_NPM_INSTALL_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BrowserBridgeManifestError("integrity_failed") from exc
    if completed.returncode != 0:
        raise BrowserBridgeManifestError("integrity_failed")


def _npm_cli_for_node(node: Path) -> Path:
    candidates = (
        node.parent / "node_modules" / "npm" / "bin" / "npm-cli.js",
        node.parent.parent / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js",
        node.parent.parent / "node_modules" / "npm" / "bin" / "npm-cli.js",
    )
    for candidate in candidates:
        if candidate.is_file() and not candidate.is_symlink():
            return candidate.resolve(strict=True)
    raise BrowserBridgeManifestError("integrity_failed")


def _remove_runtime_bin_links(runtime_dir: Path) -> None:
    bin_dir = runtime_dir / "node_modules" / ".bin"
    if bin_dir.exists() or bin_dir.is_symlink():
        _remove_path(bin_dir)


def _probe_wtscli(
    *,
    node: Path,
    main: Path,
    requirement: BrowserBridgeRequirement,
    state_home: Path,
) -> None:
    env = _wtscli_env(requirement, state_home=state_home)
    try:
        version = subprocess.run(
            (str(node), str(main), "--version"),
            env=env,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        help_result = subprocess.run(
            (str(node), str(main), "--help"),
            env=env,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BrowserBridgeManifestError("integrity_failed") from exc
    if (
        version.returncode != 0
        or version.stdout.strip() != requirement.cli.version
        or help_result.returncode != 0
    ):
        raise BrowserBridgeManifestError("integrity_failed")


def _wtscli_env(
    requirement: BrowserBridgeRequirement,
    *,
    state_home: Path,
) -> dict[str, str]:
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("OPENCLI_")
        and not name.startswith(requirement.runtime_identity.state.env_prefix)
        and not name.upper().startswith("NPM_CONFIG_")
        and name.upper() not in {"NODE_PATH", "NODE_OPTIONS"}
    }
    state = requirement.runtime_identity.state
    state_root = state.resolve_root(home=state_home)
    env["HOME"] = str(state_home)
    env["USERPROFILE"] = str(state_home)
    env[state.config_dir_env] = str(state_root)
    env[state.cache_dir_env] = str(state_root / "cache")
    return env


def _activate_pair(
    *,
    staged: tuple[tuple[Path | None, Path], ...],
    install_root: Path,
) -> None:
    token = uuid.uuid4().hex
    backups: list[tuple[Path, Path]] = []
    activated: list[tuple[Path, Path]] = []
    try:
        for source, target in staged:
            _reject_relative_symlink_components(install_root, target.parent)
            if not target.parent.resolve(strict=True).is_relative_to(install_root):
                raise BrowserBridgeManifestError("integrity_failed")
            if os.path.lexists(target) and target.is_symlink():
                raise BrowserBridgeManifestError("integrity_failed")
            backup = target.with_name(f".{target.name}.previous-{token}")
            if os.path.lexists(target):
                os.replace(target, backup)
                backups.append((backup, target))
            if source is not None:
                os.replace(source, target)
                activated.append((target, source))
    except (OSError, BrowserBridgeManifestError):
        rollback_errors: list[OSError] = []
        for target, source in reversed(activated):
            try:
                if os.path.lexists(target):
                    source.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(target, source)
            except OSError as exc:
                rollback_errors.append(exc)
                _remove_path(target)
        for backup, target in reversed(backups):
            try:
                if os.path.lexists(backup):
                    os.replace(backup, target)
            except OSError as exc:
                rollback_errors.append(exc)
        if rollback_errors:
            details = ",".join(type(error).__name__ for error in rollback_errors)
            raise RuntimeError(f"WTSCLI install rollback failed: {details}") from rollback_errors[0]
        raise
    for backup, _target in backups:
        _remove_path(backup)


def _remove_path(path: Path) -> None:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        return


def _require_node(node: Path) -> None:
    if not node.is_file() or (os.name != "nt" and not os.access(node, os.X_OK)):
        raise BrowserBridgeManifestError("integrity_failed")


def _file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "BrowserBridgeInstallResult",
    "install_browser_bridge_bundle",
]
