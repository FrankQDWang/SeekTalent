"""Validate the exact SeekTalent release installed by the Domi host."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from seektalent.browser_bridge_manifest import (
    WTSCLI_BUILD_ID,
    WTSCLI_EXTENSION_ID,
    WTSCLI_FORK_COMMIT,
    WTSCLI_VERSION,
    load_browser_bridge_requirement,
)
from seektalent.providers.liepin.browser_environment import (
    check_installed_browser_bridge_bundle,
)
from seektalent.version import __version__


INSTALL_RECEIPT_SCHEMA = "seektalent.install-receipt.v2"


@dataclass(frozen=True, slots=True)
class InstalledReleaseValidation:
    ok: bool
    reason_code: str


def validate_installed_release(home: Path) -> InstalledReleaseValidation:
    root = home.expanduser().absolute() / ".seektalent"
    receipt_path = root / "install-receipt.json"
    receipt = _read_object(receipt_path)
    if receipt is None:
        return InstalledReleaseValidation(False, "seektalent_receipt_invalid")
    if receipt.get("schemaVersion") == "seektalent.install-receipt.v1":
        return InstalledReleaseValidation(False, "install_receipt_upgrade_required")
    if receipt.get("schemaVersion") != INSTALL_RECEIPT_SCHEMA:
        return InstalledReleaseValidation(False, "seektalent_receipt_invalid")

    product_version = receipt.get("productVersion")
    source_revision = receipt.get("sourceRevision")
    product_build_id = receipt.get("productBuildId")
    wheel_filename = receipt.get("wheelFilename")
    manifest_filename = receipt.get("deliveryManifestFilename")
    if (
        product_version != __version__
        or not _is_sha(source_revision, length=40)
        or product_build_id != f"seektalent-{product_version}+{source_revision}"
        or not isinstance(wheel_filename, str)
        or not _safe_filename(wheel_filename, suffix=".whl")
        or not isinstance(manifest_filename, str)
        or not _safe_filename(manifest_filename, suffix=".json")
    ):
        return InstalledReleaseValidation(False, "delivery_manifest_identity_mismatch")

    prefix = root / "python-prefix" / str(product_version)
    site_packages = _site_packages(prefix)
    if not site_packages.is_dir():
        return InstalledReleaseValidation(False, "seektalent_release_prefix_missing")
    if not _path_within(Path(__file__).resolve(), site_packages.resolve()):
        return InstalledReleaseValidation(False, "seektalent_release_source_path")

    wheel_path = root / wheel_filename
    if not wheel_path.is_file():
        return InstalledReleaseValidation(False, "seektalent_wheel_missing")
    wheel_sha = receipt.get("wheelSha256")
    if not _is_sha(wheel_sha) or _sha256(wheel_path) != wheel_sha:
        return InstalledReleaseValidation(False, "seektalent_wheel_hash_mismatch")

    manifest_path = root / manifest_filename
    manifest_bytes = _read_bytes(manifest_path)
    if manifest_bytes is None:
        return InstalledReleaseValidation(False, "delivery_manifest_missing")
    manifest_sha = receipt.get("deliveryManifestSha256")
    if not _is_sha(manifest_sha) or hashlib.sha256(manifest_bytes).hexdigest() != manifest_sha:
        return InstalledReleaseValidation(False, "delivery_manifest_hash_mismatch")
    manifest = _read_object(manifest_path)
    if manifest is None or not _manifest_matches_receipt(manifest, receipt, wheel_filename, wheel_sha):
        return InstalledReleaseValidation(False, "delivery_manifest_identity_mismatch")

    fixture_relative = receipt.get("acceptanceFixtureInstalledPath")
    fixture_schema = receipt.get("acceptanceFixtureSchemaVersion")
    fixture_sha = receipt.get("acceptanceFixtureSha256")
    if (
        not isinstance(fixture_relative, str)
        or not _safe_relative_path(fixture_relative)
        or fixture_schema != "seektalent.acceptance-fixture.v1"
        or not _is_sha(fixture_sha)
    ):
        return InstalledReleaseValidation(False, "acceptance_fixture_identity_mismatch")
    fixture_path = root / fixture_relative
    fixture = _read_object(fixture_path)
    if (
        fixture is None
        or fixture.get("schemaVersion") != fixture_schema
        or _sha256(fixture_path) != fixture_sha
    ):
        return InstalledReleaseValidation(False, "acceptance_fixture_identity_mismatch")

    bridge_manifest = root / "browser-bridge" / "bridge-manifest.json"
    try:
        requirement = load_browser_bridge_requirement(bridge_manifest)
    except (OSError, ValueError, RuntimeError):
        return InstalledReleaseValidation(False, "wtscli_bundle_corrupt")
    if (
        requirement.bridge_build_id != WTSCLI_BUILD_ID
        or requirement.fork_commit != WTSCLI_FORK_COMMIT
        or requirement.cli.version != WTSCLI_VERSION
        or requirement.extension.version != WTSCLI_VERSION
        or requirement.extension.id != WTSCLI_EXTENSION_ID
        or receipt.get("bridgeBuildId") != requirement.bridge_build_id
        or receipt.get("wtscliVersion") != requirement.cli.version
        or receipt.get("wtscliForkCommit") != requirement.fork_commit
        or receipt.get("extensionVersion") != requirement.extension.version
        or receipt.get("extensionIdSha256") != hashlib.sha256(WTSCLI_EXTENSION_ID.encode()).hexdigest()
    ):
        return InstalledReleaseValidation(False, "wtscli_identity_mismatch")

    bundle_status = check_installed_browser_bridge_bundle(install_root=root)
    if not bundle_status.ok:
        return InstalledReleaseValidation(False, bundle_status.reason_code)
    return InstalledReleaseValidation(True, "exact_installed_release_ready")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate an exact SeekTalent Domi install.")
    parser.add_argument("--home", type=Path, required=True)
    args = parser.parse_args(argv)
    result = validate_installed_release(args.home)
    if not result.ok:
        print(f"reason_code={result.reason_code}", file=sys.stderr)
        return 1
    print(f"reason_code={result.reason_code}")
    return 0


def _manifest_matches_receipt(
    manifest: dict[str, object],
    receipt: dict[str, object],
    wheel_filename: str,
    wheel_sha: object,
) -> bool:
    fixture = manifest.get("acceptance_fixture")
    if not isinstance(fixture, dict):
        return False
    fixture = cast(dict[str, object], fixture)
    return all(
        (
            manifest.get("schema_version") == 2,
            manifest.get("product_version") == receipt.get("productVersion"),
            manifest.get("source_revision") == receipt.get("sourceRevision"),
            manifest.get("product_build_id") == receipt.get("productBuildId"),
            manifest.get("bridge_build_id") == receipt.get("bridgeBuildId"),
            manifest.get("wtscli_version") == receipt.get("wtscliVersion"),
            manifest.get("wtscli_fork_commit") == receipt.get("wtscliForkCommit"),
            manifest.get("extension_version") == receipt.get("extensionVersion"),
            manifest.get("extension_id_sha256") == receipt.get("extensionIdSha256"),
            manifest.get("seektalent_wheel") == wheel_filename,
            manifest.get("seektalent_wheel_sha256") == wheel_sha,
            isinstance(manifest.get("host_runtime_contract"), dict),
            isinstance(manifest.get("files"), list),
            fixture.get("schema_version")
            == receipt.get("acceptanceFixtureSchemaVersion"),
            fixture.get("sha256")
            == receipt.get("acceptanceFixtureSha256"),
        )
    )


def _site_packages(prefix: Path) -> Path:
    windows = prefix / "Lib" / "site-packages"
    return windows if windows.is_dir() else prefix / "site-packages"


def _read_object(path: Path) -> dict[str, object] | None:
    raw = _read_bytes(path)
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        return None
    return {key: item for key, item in value.items()}


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha(value: object, *, length: int = 64) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _safe_filename(value: str, *, suffix: str) -> bool:
    path = Path(value)
    return (
        path.name == value
        and path.suffix == suffix
        and value not in {"", ".", ".."}
    )


def _safe_relative_path(value: str) -> bool:
    path = Path(value)
    return (
        value not in {"", ".", ".."}
        and not path.is_absolute()
        and ".." not in path.parts
        and path.as_posix() == value
    )


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
