"""Probe and validate the exact runtimes supplied by the Domi host.

This module intentionally depends only on the Python standard library so the
delivery scripts can run it before importing the installed SeekTalent package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform as platform_module
import subprocess
import sys
import sysconfig
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, cast


EXPECTED_PYTHON_IMPLEMENTATION = "cpython"
EXPECTED_PYTHON_MAJOR_MINOR = "3.13"
EXPECTED_PYTHON_CACHE_TAG = "cpython-313"
EXPECTED_NODE_VERSION = "22.14.0"


@dataclass(frozen=True, slots=True)
class HostRuntimeIdentity:
    platform: str
    os_family: str
    architecture: str
    python_executable: str
    python_version: str
    python_implementation: str
    python_cache_tag: str
    python_soabi: str
    python_executable_sha256: str
    node_executable: str
    node_version: str
    node_executable_sha256: str

    def to_receipt_payload(self) -> dict[str, str]:
        return {
            "hostPlatform": self.platform,
            "hostOsFamily": self.os_family,
            "hostArchitecture": self.architecture,
            "pythonExecutable": self.python_executable,
            "pythonVersion": self.python_version,
            "pythonImplementation": self.python_implementation,
            "pythonCacheTag": self.python_cache_tag,
            "pythonSoabi": self.python_soabi,
            "pythonExecutableSha256": self.python_executable_sha256,
            "nodeExecutable": self.node_executable,
            "nodeVersion": self.node_version,
            "nodeExecutableSha256": self.node_executable_sha256,
        }

    def to_public_payload(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_public_payload(cls, payload: Mapping[str, object]) -> HostRuntimeIdentity:
        return cls(
            platform=_required_string(payload, "platform"),
            os_family=_required_string(payload, "os_family"),
            architecture=_required_string(payload, "architecture"),
            python_executable=_required_string(payload, "python_executable"),
            python_version=_required_string(payload, "python_version"),
            python_implementation=_required_string(payload, "python_implementation"),
            python_cache_tag=_required_string(payload, "python_cache_tag"),
            python_soabi=_required_string(payload, "python_soabi"),
            python_executable_sha256=_required_string(
                payload,
                "python_executable_sha256",
            ),
            node_executable=_required_string(payload, "node_executable"),
            node_version=_required_string(payload, "node_version"),
            node_executable_sha256=_required_string(
                payload,
                "node_executable_sha256",
            ),
        )


def probe_current_runtime(node: Path) -> HostRuntimeIdentity:
    python_path = Path(sys.executable).resolve(strict=True)
    node_path = node.expanduser().resolve(strict=True)
    os_family, architecture, platform_name = _host_platform()
    implementation = platform_module.python_implementation().lower()
    cache_tag = sys.implementation.cache_tag or ""
    soabi = sysconfig.get_config_var("SOABI") or ""
    node_version = _node_version(node_path)
    return HostRuntimeIdentity(
        platform=platform_name,
        os_family=os_family,
        architecture=architecture,
        python_executable=str(python_path),
        python_version=platform_module.python_version(),
        python_implementation=implementation,
        python_cache_tag=cache_tag,
        python_soabi=str(soabi),
        python_executable_sha256=_sha256(python_path),
        node_executable=str(node_path),
        node_version=node_version,
        node_executable_sha256=_sha256(node_path),
    )


def probe_runtime_with_python(
    python: Path,
    node: Path,
    *,
    verifier: Path | None = None,
) -> HostRuntimeIdentity:
    python_path = python.expanduser().resolve(strict=True)
    node_path = node.expanduser().resolve(strict=True)
    script = (verifier or Path(__file__)).resolve(strict=True)
    completed = subprocess.run(
        [str(python_path), str(script), "probe", "--node", str(node_path)],
        check=False,
        capture_output=True,
        text=True,
        env=_sanitized_environment(),
    )
    if completed.returncode != 0:
        raise RuntimeError("Domi host runtime probe failed")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Domi host runtime probe returned invalid data") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Domi host runtime probe returned invalid data")
    return HostRuntimeIdentity.from_public_payload(payload)


def delivery_runtime_contract(identity: HostRuntimeIdentity) -> dict[str, str]:
    return {
        "platform": identity.platform,
        "os_family": identity.os_family,
        "architecture": identity.architecture,
        "python_implementation": EXPECTED_PYTHON_IMPLEMENTATION,
        "python_major_minor": EXPECTED_PYTHON_MAJOR_MINOR,
        "python_cache_tag": EXPECTED_PYTHON_CACHE_TAG,
        "python_soabi": identity.python_soabi,
        "node_version": EXPECTED_NODE_VERSION,
    }


def validate_delivery_runtime_contract(
    identity: HostRuntimeIdentity,
    contract: Mapping[str, object],
) -> str | None:
    if any(
        (
            contract.get("platform") != identity.platform,
            contract.get("os_family") != identity.os_family,
            contract.get("architecture") != identity.architecture,
        )
    ):
        return "domi_platform_contract_mismatch"
    if any(
        (
            contract.get("python_implementation") != identity.python_implementation,
            contract.get("python_major_minor") != _major_minor(identity.python_version),
            contract.get("python_cache_tag") != identity.python_cache_tag,
            contract.get("python_soabi") != identity.python_soabi,
            identity.python_implementation != EXPECTED_PYTHON_IMPLEMENTATION,
            _major_minor(identity.python_version) != EXPECTED_PYTHON_MAJOR_MINOR,
            identity.python_cache_tag != EXPECTED_PYTHON_CACHE_TAG,
        )
    ):
        return "domi_python_contract_mismatch"
    if (
        contract.get("node_version") != identity.node_version
        or identity.node_version != EXPECTED_NODE_VERSION
    ):
        return "domi_node_contract_mismatch"
    return None


def validate_installed_runtime_identity(
    identity: HostRuntimeIdentity,
    receipt: Mapping[str, object],
) -> str | None:
    if receipt.get("schemaVersion") == "seektalent.install-receipt.v1":
        return "install_receipt_upgrade_required"
    if receipt.get("schemaVersion") != "seektalent.install-receipt.v2":
        return "seektalent_receipt_invalid"
    expected = identity.to_receipt_payload()
    if any(receipt.get(key) != value for key, value in expected.items()):
        return "domi_host_runtime_changed_reinstall_required"
    return None


def validate_delivery_payload(
    package_root: Path,
    manifest: Mapping[str, object],
) -> str | None:
    if manifest.get("schema_version") != 2:
        return "delivery_manifest_identity_mismatch"
    entries = manifest.get("files")
    fixture = manifest.get("acceptance_fixture")
    if not isinstance(entries, list) or not isinstance(fixture, dict):
        return "delivery_manifest_identity_mismatch"
    expected: dict[str, tuple[int, str]] = {}
    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            return "delivery_manifest_identity_mismatch"
        entry = cast(dict[str, object], raw_entry)
        relative = entry.get("path")
        size = entry.get("size")
        digest = entry.get("sha256")
        if (
            not isinstance(relative, str)
            or not _safe_relative_path(relative)
            or not isinstance(size, int)
            or size < 0
            or not _is_sha256(digest)
            or relative in expected
        ):
            return "delivery_manifest_identity_mismatch"
        expected[relative] = (size, cast(str, digest))
    actual_paths = {
        path.relative_to(package_root).as_posix(): path
        for path in package_root.rglob("*")
        if path.is_file()
        and path.name not in {"delivery-manifest.json", "SHA256SUMS"}
    }
    if set(actual_paths) != set(expected):
        return "delivery_payload_hash_mismatch"
    for relative, path in actual_paths.items():
        if path.is_symlink():
            return "delivery_payload_hash_mismatch"
        size, digest = expected[relative]
        if path.stat().st_size != size or _sha256(path) != digest:
            return "delivery_payload_hash_mismatch"
    fixture = cast(dict[str, object], fixture)
    fixture_path = fixture.get("path")
    fixture_schema = fixture.get("schema_version")
    fixture_sha = fixture.get("sha256")
    if (
        not isinstance(fixture_path, str)
        or fixture_path not in expected
        or fixture_schema != "seektalent.acceptance-fixture.v1"
        or not _is_sha256(fixture_sha)
        or expected[fixture_path][1] != fixture_sha
    ):
        return "acceptance_fixture_identity_mismatch"
    try:
        fixture_payload = _read_object(package_root / fixture_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return "acceptance_fixture_identity_mismatch"
    if fixture_payload.get("schemaVersion") != fixture_schema:
        return "acceptance_fixture_identity_mismatch"
    sums_path = package_root / "SHA256SUMS"
    if not sums_path.is_file():
        return "delivery_payload_hash_mismatch"
    expected_sums = {
        path.relative_to(package_root).as_posix(): _sha256(path)
        for path in package_root.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    }
    try:
        actual_sums = _parse_sha256s(sums_path)
    except (OSError, ValueError):
        return "delivery_payload_hash_mismatch"
    if actual_sums != expected_sums:
        return "delivery_payload_hash_mismatch"
    return None


def _host_platform() -> tuple[str, str, str]:
    system = platform_module.system().lower()
    machine = platform_module.machine().lower()
    if system == "darwin":
        os_family = "macos"
    elif system == "windows":
        os_family = "windows"
    else:
        os_family = system
    architecture = {
        "aarch64": "arm64",
        "arm64": "arm64",
        "amd64": "x64",
        "x86_64": "x64",
    }.get(machine, machine)
    if os_family == "macos" and architecture == "arm64":
        platform_name = "macos-arm64"
    elif os_family == "macos" and architecture == "x64":
        platform_name = "macos-x86_64"
    elif os_family == "windows" and architecture == "x64":
        platform_name = "windows-x64"
    else:
        platform_name = f"{os_family}-{architecture}"
    return os_family, architecture, platform_name


def _major_minor(version: str) -> str:
    return ".".join(version.split(".")[:2])


def _required_string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError("invalid Domi host runtime identity")
    return value


def _node_version(node: Path) -> str:
    completed = subprocess.run(
        [str(node), "--version"],
        check=False,
        capture_output=True,
        text=True,
        env=_sanitized_environment(),
    )
    if completed.returncode != 0:
        raise RuntimeError("Domi Node version probe failed")
    return completed.stdout.strip().removeprefix("v")


def _sanitized_environment() -> dict[str, str]:
    allowed = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TMP", "TEMP", "TMPDIR")
    return {key: os.environ[key] for key in allowed if key in os.environ}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _safe_relative_path(value: str) -> bool:
    path = Path(value)
    return (
        value not in {"", ".", ".."}
        and not path.is_absolute()
        and ".." not in path.parts
        and path.as_posix() == value
    )


def _parse_sha256s(path: Path) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, separator, relative = line.partition("  ")
        if not separator or not _is_sha256(digest) or not _safe_relative_path(relative):
            raise ValueError("invalid SHA256SUMS")
        if relative in parsed:
            raise ValueError("duplicate SHA256SUMS path")
        parsed[relative] = digest
    return parsed


def _read_object(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("expected one JSON object")
    return payload


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Domi host runtime identity.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("probe", "validate-delivery", "validate-receipt"):
        child = subparsers.add_parser(command)
        child.add_argument("--node", type=Path, required=True)
        if command == "validate-delivery":
            child.add_argument("--manifest", type=Path, required=True)
        if command == "validate-receipt":
            child.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        identity = probe_current_runtime(args.node)
        if args.command == "probe":
            print(json.dumps(identity.to_public_payload(), sort_keys=True))
            return 0
        if args.command == "validate-delivery":
            manifest = _read_object(args.manifest)
            contract = manifest.get("host_runtime_contract")
            if not isinstance(contract, dict):
                reason = "delivery_manifest_identity_mismatch"
            else:
                reason = validate_delivery_runtime_contract(
                    identity,
                    cast(dict[str, object], contract),
                )
                if reason is None:
                    reason = validate_delivery_payload(args.manifest.parent, manifest)
        else:
            receipt = _read_object(args.receipt)
            reason = validate_installed_runtime_identity(identity, receipt)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        reason = "domi_host_runtime_probe_failed"
    if reason is not None:
        print(f"reason_code={reason}", file=sys.stderr)
        return 1
    print("reason_code=domi_host_runtime_ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
