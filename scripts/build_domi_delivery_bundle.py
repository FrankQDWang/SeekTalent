from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from seektalent.browser_bridge_install import install_browser_bridge_bundle
from seektalent.browser_bridge_manifest import load_browser_bridge_bundle
from seektalent.browser_bridge_manifest import (
    WTSCLI_EXTENSION_ID,
    WTSCLI_FORK_COMMIT,
)
from seektalent.domi_host_runtime import (
    HostRuntimeIdentity,
    delivery_runtime_contract,
    probe_runtime_with_python,
    validate_delivery_runtime_contract,
)
from seektalent.version import __version__


ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_PLATFORMS = frozenset(
    {
        "windows-x64",
        "macos-arm64",
        "macos-x86_64",
    }
)
ACCEPTANCE_FIXTURE = (
    ROOT / "acceptance" / "fixtures" / "liepin-ai-agent-engineer-v1.json"
)


def build_delivery_bundle(
    *,
    output_dir: Path,
    browser_bridge_bundle: Path,
    seektalent_wheel: Path,
    wheelhouse_dir: Path,
    domi_python: Path | None,
    node: Path,
    platform_name: str,
    source_revision: str | None = None,
    host_runtime_identity: HostRuntimeIdentity | None = None,
) -> Path:
    """Build one downloadable SeekTalent package with its exact WTSCLI pair."""
    if platform_name not in SUPPORTED_PLATFORMS:
        raise ValueError(f"unsupported delivery platform: {platform_name}")
    admitted = load_browser_bridge_bundle(browser_bridge_bundle)
    exact_source_revision = source_revision or _source_revision()
    if (
        len(exact_source_revision) != 40
        or any(character not in "0123456789abcdef" for character in exact_source_revision)
    ):
        raise ValueError("exact lowercase source revision is required")
    if (
        seektalent_wheel.is_symlink()
        or not seektalent_wheel.is_file()
        or not seektalent_wheel.name.startswith("seektalent-")
        or seektalent_wheel.suffix != ".whl"
    ):
        raise ValueError("one SeekTalent wheel is required")
    if not wheelhouse_dir.is_dir() or not any(wheelhouse_dir.glob("*.whl")):
        raise ValueError("a non-empty offline Python wheelhouse is required")
    identity = host_runtime_identity
    if identity is None:
        if domi_python is None:
            raise ValueError("the Domi Python executable is required")
        identity = probe_runtime_with_python(domi_python, node)
    contract = delivery_runtime_contract(identity)
    reason = validate_delivery_runtime_contract(identity, contract)
    if reason is not None or identity.platform != platform_name:
        raise ValueError(reason or "domi_platform_contract_mismatch")

    output_dir.mkdir(parents=True, exist_ok=True)
    package_name = f"seektalent-offline-{__version__}-{platform_name}-py313"
    archive = output_dir / f"{package_name}.zip"
    archive.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="seektalent-domi-delivery-") as temporary:
        package_root = Path(temporary) / package_name
        package_root.mkdir()
        shutil.copytree(
            admitted.root,
            package_root / "wtscli-browser-bridge",
        )
        shutil.copy2(
            ROOT / "scripts" / "install_staging_browser_bridge.py",
            package_root / "install_staging_browser_bridge.py",
        )
        installer_name = (
            "install-seektalent-domi.ps1"
            if platform_name == "windows-x64"
            else "install-seektalent-domi.sh"
        )
        startup_script_name = (
            "start-seektalent-domi.ps1"
            if platform_name == "windows-x64"
            else "start-seektalent-domi.sh"
        )
        shutil.copy2(
            ROOT / "scripts" / installer_name,
            package_root / installer_name,
        )
        shutil.copy2(
            ROOT / "scripts" / startup_script_name,
            package_root / startup_script_name,
        )
        if platform_name != "windows-x64":
            shutil.copy2(
                ROOT / "scripts" / "rollback-seektalent-domi.sh",
                package_root / "rollback-seektalent-domi.sh",
            )
        shutil.copy2(seektalent_wheel, package_root / seektalent_wheel.name)
        shutil.copytree(wheelhouse_dir, package_root / "python-wheelhouse")
        fixture_relative = Path("acceptance") / ACCEPTANCE_FIXTURE.name
        fixture_target = package_root / fixture_relative
        fixture_target.parent.mkdir()
        shutil.copy2(ACCEPTANCE_FIXTURE, fixture_target)
        shutil.copy2(
            ROOT / "src" / "seektalent" / "domi_host_runtime.py",
            package_root / "verify_domi_host_runtime.py",
        )
        with tempfile.TemporaryDirectory(
            prefix="seektalent-wtscli-prepared-",
        ) as prepared_temporary:
            installed = install_browser_bridge_bundle(
                bundle_dir=admitted.root,
                install_root=Path(prepared_temporary) / ".seektalent",
                node=node,
            )
            _write_runtime_zip(
                installed.runtime_dir,
                package_root / "wtscli-runtime.zip",
            )
        manifest: dict[str, object] = {
            "schema_version": 2,
            "platform": platform_name,
            "os_family": identity.os_family,
            "architecture": identity.architecture,
            "product_version": __version__,
            "source_revision": exact_source_revision,
            "product_build_id": (
                f"seektalent-{__version__}+{exact_source_revision}"
            ),
            "bridge_build_id": admitted.bridge_build_id,
            "wtscli_version": admitted.requirement.cli.version,
            "wtscli_fork_commit": WTSCLI_FORK_COMMIT,
            "extension_version": admitted.extension_version,
            "extension_id_sha256": hashlib.sha256(
                WTSCLI_EXTENSION_ID.encode()
            ).hexdigest(),
            "extension_directory": "~/.seektalent/chrome-extension/wtscli",
            "browser_bridge_bundle": "wtscli-browser-bridge",
            "browser_bridge_manifest_sha256": _sha256(
                package_root / "wtscli-browser-bridge" / "bridge-manifest.json"
            ),
            "prepared_runtime": "wtscli-runtime.zip",
            "prepared_runtime_sha256": _sha256(
                package_root / "wtscli-runtime.zip"
            ),
            "seektalent_wheel": seektalent_wheel.name,
            "seektalent_wheel_sha256": _sha256(
                package_root / seektalent_wheel.name
            ),
            "host_runtime_contract": contract,
            "acceptance_fixture": {
                "path": fixture_relative.as_posix(),
                "schema_version": "seektalent.acceptance-fixture.v1",
                "sha256": _sha256(fixture_target),
            },
            "startup_script": startup_script_name,
            "startup_contract": {
                "jwt_env": "SEEKTALENT_DOMI_JWT",
                "python_env": "DOMI_PYTHON",
                "node_env": "DOMI_NODE",
            },
        }
        manifest["files"] = _file_manifest(package_root)
        (package_root / "delivery-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _write_sha256s(package_root)
        _write_archive(package_root, archive)
    (output_dir / f"{archive.name}.sha256").write_bytes(
        f"{_sha256(archive)}  {archive.name}\n".encode("ascii")
    )
    return archive


def _file_manifest(package_root: Path) -> list[dict[str, object]]:
    files: list[dict[str, object]] = []
    for path in sorted(package_root.rglob("*")):
        if path.is_symlink():
            raise ValueError("delivery package must not contain symlinks")
        if path.is_file():
            files.append(
                {
                    "path": path.relative_to(package_root).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
    return files


def _write_sha256s(package_root: Path) -> None:
    lines = [
        f"{entry['sha256']}  {entry['path']}"
        for entry in _file_manifest(package_root)
        if entry["path"] != "SHA256SUMS"
    ]
    (package_root / "SHA256SUMS").write_bytes(
        ("\n".join(lines) + "\n").encode("utf-8")
    )


def _write_runtime_zip(runtime_dir: Path, archive: Path) -> None:
    with zipfile.ZipFile(
        archive,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as output:
        for path in sorted(runtime_dir.rglob("*")):
            if path.is_symlink():
                raise ValueError("prepared WTSCLI runtime must not contain symlinks")
            if not path.is_file():
                continue
            info = zipfile.ZipInfo(
                path.relative_to(runtime_dir).as_posix(),
            )
            info.external_attr = (path.stat().st_mode & 0o777) << 16
            output.writestr(info, path.read_bytes())


def _write_archive(package_root: Path, archive: Path) -> None:
    with zipfile.ZipFile(
        archive,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as output:
        for path in sorted(package_root.rglob("*")):
            if path.is_symlink():
                raise ValueError("delivery package must not contain symlinks")
            if not path.is_file():
                continue
            relative = Path(package_root.name) / path.relative_to(package_root)
            info = zipfile.ZipInfo(relative.as_posix())
            mode = path.stat().st_mode & 0o777
            if path.name.endswith(".sh"):
                mode |= stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            info.external_attr = mode << 16
            output.writestr(info, path.read_bytes())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_revision() -> str:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if dirty:
        raise RuntimeError("source_checkout_not_clean")
    return revision


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a platform SeekTalent package with the exact WTSCLI pair.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--wtscli-bundle-dir", type=Path, required=True)
    parser.add_argument("--seektalent-wheel", type=Path, required=True)
    parser.add_argument("--wheelhouse-dir", type=Path, required=True)
    parser.add_argument("--domi-python", type=Path, required=True)
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--source-revision")
    parser.add_argument(
        "--platform",
        dest="platform_name",
        choices=sorted(SUPPORTED_PLATFORMS),
        required=True,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    source_revision = _source_revision()
    if (
        args.source_revision is not None
        and args.source_revision != source_revision
    ):
        raise ValueError("source_revision_does_not_match_checkout")
    archive = build_delivery_bundle(
        output_dir=args.output_dir,
        browser_bridge_bundle=args.wtscli_bundle_dir,
        seektalent_wheel=args.seektalent_wheel,
        wheelhouse_dir=args.wheelhouse_dir,
        domi_python=args.domi_python,
        node=args.node,
        platform_name=args.platform_name,
        source_revision=source_revision,
    )
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
