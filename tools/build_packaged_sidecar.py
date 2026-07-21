"""Build the test-only native packaged sidecar artifact used by release CI."""

from __future__ import annotations

import argparse
import base64
import json
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import rfc8785
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from seektalent.release_manifest import canonical_release_manifest_bytes, parse_release_manifest, release_manifest_digest
from seektalent.release_signing import ReleaseManifestTrustKeyV1, ReleaseManifestTrustPolicyV1


TEST_ONLY_SIGNING_SEED = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
TEST_ONLY_SIGNER_KEY_ID = "test-only-packaged-sidecar-signer-v1"
TEST_ONLY_TRUST_POLICY_ID = "test-only-packaged-sidecar-policy-v1"
TEST_ONLY_VERIFICATION_TIME = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
_REPOSITORY = "https://github.com/FrankQDWang/SeekTalent"
_COMPONENT_SPECS = (
    ("browser_bridge", "bridge", "bridge", (), False, ("bridge.json",)),
    (
        "installer_updater_support",
        "installer_support",
        "installer-support",
        (
            "browser_bridge",
            "licenses_sbom",
            "liepin_execution_sidecar",
            "main_application",
            "node_runtime",
            "python_runtime",
            "sqlite_runtime",
            "workbench_assets",
            "wtscli_runtime",
        ),
        False,
        ("installer.bin", "uninstaller.bin", "updater.bin"),
    ),
    ("licenses_sbom", "metadata", "licenses", (), True, ("licenses.json", "sbom.json")),
    (
        "liepin_execution_sidecar",
        "sidecar",
        "bin/sidecar",
        ("browser_bridge", "node_runtime", "python_runtime", "sqlite_runtime", "wtscli_runtime"),
        False,
        (),
    ),
    (
        "main_application",
        "application",
        "bin/main",
        ("liepin_execution_sidecar", "python_runtime", "sqlite_runtime", "workbench_assets"),
        False,
        ("main-placeholder",),
    ),
    ("node_runtime", "runtime", "runtimes/node", (), False, ("node-placeholder",)),
    ("python_runtime", "runtime", "runtimes/python", (), False, ("python-placeholder",)),
    ("sqlite_runtime", "runtime", "runtimes/sqlite", (), False, ("sqlite-placeholder",)),
    ("workbench_assets", "assets", "workbench", (), True, ("index.html",)),
    ("wtscli_runtime", "runtime", "runtimes/wtscli", ("browser_bridge", "node_runtime"), False, ("wtscli-placeholder",)),
)


def test_only_trust_policy() -> ReleaseManifestTrustPolicyV1:
    """Return the explicitly non-production trust policy for native CI artifacts."""
    public_key = Ed25519PrivateKey.from_private_bytes(TEST_ONLY_SIGNING_SEED).public_key().public_bytes_raw()
    return ReleaseManifestTrustPolicyV1(
        policy_id=TEST_ONLY_TRUST_POLICY_ID,
        revision=1,
        allowed_signer_role="release_manifest_signer",
        allowed_algorithm="ed25519",
        keys=(
            ReleaseManifestTrustKeyV1(
                key_id=TEST_ONLY_SIGNER_KEY_ID,
                public_key=public_key,
                not_before=datetime(2026, 7, 1, tzinfo=UTC),
                not_after=datetime(2027, 7, 1, tzinfo=UTC),
            ),
        ),
        revoked_key_ids=frozenset(),
    )


def build_packaged_sidecar(slot_root: Path, test_signing_seed: bytes) -> Path:
    """Build one native PyInstaller-onedir slot and its test-only release metadata."""
    if test_signing_seed != TEST_ONLY_SIGNING_SEED:
        raise ValueError("only the declared test-only signing key is accepted")
    if slot_root.exists():
        raise FileExistsError(slot_root)
    target = _native_target()
    source_revision = _source_revision()
    sidecar_root = slot_root / "release" / "bin" / "sidecar"
    with tempfile.TemporaryDirectory(prefix="seektalent-sidecar-build-") as temporary:
        temporary_root = Path(temporary)
        _run_pyinstaller(temporary_root)
        artifact_dir = temporary_root / "dist" / "seektalent-sidecar-bootstrap"
        shutil.copytree(artifact_dir, sidecar_root)

    executable_name = "seektalent-sidecar-bootstrap.exe" if target["os"] == "windows" else "seektalent-sidecar-bootstrap"
    executable_path = sidecar_root / executable_name
    if not executable_path.is_file():
        raise RuntimeError(f"PyInstaller did not produce {executable_name}")
    _write_test_only_component_placeholders(slot_root)
    payload = _manifest_payload(slot_root, target, source_revision, executable_name)
    manifest = parse_release_manifest(_json_bytes(payload))
    manifest_path = slot_root / "release" / "release-manifest.json"
    manifest_path.write_bytes(canonical_release_manifest_bytes(manifest))
    _write_test_only_signature(slot_root, manifest, test_signing_seed)
    return slot_root


def _run_pyinstaller(temporary_root: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--onedir",
            "--clean",
            "--noconfirm",
            "--name",
            "seektalent-sidecar-bootstrap",
            "--distpath",
            str(temporary_root / "dist"),
            "--workpath",
            str(temporary_root / "work"),
            "--specpath",
            str(temporary_root / "spec"),
            str(Path(__file__).resolve().parents[1] / "src" / "seektalent" / "sidecar_bootstrap.py"),
        ],
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"PyInstaller onedir build failed with exit status {completed.returncode}")


def _native_target() -> dict[str, str]:
    system = platform.system()
    machine = platform.machine().lower()
    arch = {"amd64": "x86_64", "x86_64": "x86_64", "aarch64": "arm64", "arm64": "arm64"}.get(machine)
    if system == "Windows" and arch == "x86_64":
        return {"os": "windows", "arch": arch, "min_os_build": "10.0.22000", "max_os_build": "10.0.26100"}
    if system == "Darwin" and arch in {"x86_64", "arm64"}:
        return {"os": "macos", "arch": arch, "min_os_build": "13.0", "max_os_build": "15.9"}
    raise RuntimeError(f"unsupported native packaged-sidecar builder host: {system}/{machine}")


def _source_revision() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    )
    return completed.stdout.strip()


def _write_test_only_component_placeholders(slot_root: Path) -> None:
    for component_id, _, root_path, _, _, names in _COMPONENT_SPECS:
        if component_id == "liepin_execution_sidecar":
            continue
        root = slot_root / "release" / root_path
        root.mkdir(parents=True, exist_ok=True)
        for name in names:
            path = root / name
            path.write_text(f"test-only packaged sidecar component: {component_id}\n", encoding="utf-8")
            if name.endswith((".bin", "-placeholder")):
                path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _manifest_payload(
    slot_root: Path,
    target: dict[str, str],
    source_revision: str,
    executable_name: str,
) -> dict[str, object]:
    components = [
        _component_payload(slot_root, target, source_revision, component_id, kind, root, dependencies, independent, names, executable_name)
        for component_id, kind, root, dependencies, independent, names in _COMPONENT_SPECS
    ]
    components.sort(key=lambda component: str(component["component_id"]))
    payload: dict[str, object] = {
        "schema_version": "seektalent.release-manifest/v1",
        "manifest_id": f"test-only-packaged-sidecar-{target['os']}-{target['arch']}",
        "release_series_id": "test-only-packaged-sidecar-series-v1",
        "product_name": "SeekTalent",
        "product_version": "0.7.49",
        "product_build_id": "st1-00000000000000000000000000000000",
        "source_revision": source_revision,
        "source_tree_digest": _digest_file(Path(__file__).resolve().parents[1] / "src" / "seektalent" / "sidecar_bootstrap.py"),
        "build_recipe": {
            "recipe_id": "test-only-pyinstaller-onedir-v1",
            "revision": "pyinstaller-6.21.0",
            "digest": _digest_file(Path(__file__).resolve()),
            "runner_image_ref": "test-only-native-github-runner",
            "toolchain_refs": ["pyinstaller-6.21.0", "python-3.12"],
        },
        "dependency_inputs": [
            {"name": "pyinstaller", "sha256": _digest_file(Path(__file__).resolve()), "platform_scope": target},
        ],
        "target": target,
        "channel": "internal",
        "created_at": "2026-07-21T00:00:00Z",
        "payload_root": "release",
        "payload_tree_sha256": "0" * 64,
        "components": components,
        "external_dependencies": {
            "chrome_stable": {"channel": "stable", "tested_min_version": "130.0.0", "tested_max_version": "140.0.0", "allowed_os_policy_postures": ["default"]},
            "chrome_profile": {"mode": "existing_profile_compatibility", "required_binding_fields": ["account-ref"], "residual_risk_policy_ref": "test-only-profile-risk"},
            "production_extension": {"distribution": "chrome_web_store", "extension_id": "a" * 32, "store_item_ref": "test-only-extension", "protocol_major": 1, "protocol_min_minor": 0, "protocol_max_minor": 0, "required_capabilities": ["test-only"], "min_version": "1.0.0", "max_version": "1.0.0", "min_build": 1, "max_build": 1, "compatibility_matrix_ref": "test-only-extension-matrix"},
            "domi_host": {"posture": "optional", "tested_min_version": "1.0.0", "tested_max_version": "1.0.0", "tested_min_build": 1, "tested_max_build": 1, "launch_contract_ref": "test-only-domi-launch"},
            "network_postures": [{"posture_id": "direct", "mode": "direct"}],
            "provider": {"source": "liepin", "real_canary_policy_ref": "test-only-no-provider-canary"},
        },
        "compatibility": _compatibility_payload(),
        "storage_contract": {
            "install_root": "INSTALL_ROOT", "data_root": "DATA_ROOT", "profile_mode": "existing_profile_compatibility", "pointer_schema_ref": "test-only-active-slot", "minimum_atomic_filesystem_capability": "test-only-atomic-replace", "database_names": ["main"], "backup_group_schema_ref": "test-only-backup", "minimum_free_space_formula": "test-only-space", "sidecar_journal_path": "sidecar/command-journal.sqlite3", "result_spool_path": "sidecar/result-spool", "rollback_mode": "DELETE", "synchronous": "FULL", "retention_policy_ref": "test-only-retention", "profile_binding_schema_ref": "test-only-profile-binding", "profile_binding_generation_policy_ref": "test-only-profile-generation", "authority_rotation_policy_id": "test-only-authority-rotation", "uninstall_default": "preserve_user_data_and_profile", "purge_requires_explicit_confirmation": True, "purge_optional_final_backup": False,
        },
        "installer_contract": _installer_contract(components),
        "evidence_policy": {"schema_refs": ["test-only-evidence"], "matrix_revision": "test-only-native-matrix-v1", "required_evidence_classes": ["test-only-build"]},
        "build_evidence_refs": ["test-only-native-build"],
        "signing_policy": {"required_signer_ids": [TEST_ONLY_SIGNER_KEY_ID], "algorithms": ["ed25519"], "platform_verification_kinds": ["test-only-detached-signature"]},
        "sbom_ref": _file_payload(slot_root / "release" / "licenses" / "sbom.json", "licenses/sbom.json", executable=False),
        "license_inventory_ref": _file_payload(slot_root / "release" / "licenses" / "licenses.json", "licenses/licenses.json", executable=False),
    }
    payload["payload_tree_sha256"] = _payload_tree_digest(components)
    product_build_id = _expected_product_build_id(payload)
    payload["product_build_id"] = product_build_id
    compatibility = _mapping(payload["compatibility"])
    main_sidecar = _mapping(compatibility["main_sidecar"])
    main_sidecar["product_build_id"] = product_build_id
    compatibility["main_sidecar"] = main_sidecar
    payload["compatibility"] = compatibility
    return payload


def _component_payload(
    slot_root: Path,
    target: dict[str, str],
    source_revision: str,
    component_id: str,
    component_kind: str,
    root_path: str,
    dependencies: tuple[str, ...],
    platform_independent: bool,
    names: tuple[str, ...],
    executable_name: str,
) -> dict[str, object]:
    root = slot_root / "release" / root_path
    if component_id == "liepin_execution_sidecar":
        files = _artifact_file_payloads(root, executable_name)
    else:
        files = [_file_payload(root / name, name, executable=name.endswith((".bin", "-placeholder"))) for name in names]
    files.sort(key=lambda item: str(item["path"]))
    build_id = f"test-only-{component_id}-{_tree_digest(files)[:16]}"
    entrypoints = [executable_name] if component_id == "liepin_execution_sidecar" else [str(item["path"]) for item in files if item["executable"]]
    return {
        "component_id": component_id,
        "component_kind": component_kind,
        "version": "1.0.0",
        "build_id": build_id,
        "source_ref": {"repository": _REPOSITORY, "revision": source_revision, "artifact_ref": None},
        "root_path": root_path,
        "entrypoints": entrypoints,
        "files": files,
        "tree_sha256": _tree_digest(files),
        "size_bytes": sum(_integer(item["size_bytes"]) for item in files),
        "platform": "platform_independent" if platform_independent else target,
        "dependencies": list(dependencies),
        "protocols": [],
        "code_signature_ref": "test-only-detached-manifest-signature" if entrypoints else None,
        "build_provenance_ref": f"test-only-{component_id}-build",
    }


def _artifact_file_payloads(root: Path, executable_name: str) -> list[dict[str, object]]:
    files: list[dict[str, object]] = []
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            if path.is_dir():
                continue
            raise RuntimeError(f"PyInstaller onedir payload is not a regular file: {path}")
        files.append(_file_payload(path, path.relative_to(root).as_posix(), executable=path.name == executable_name))
    if not any(item["path"] == executable_name and item["executable"] for item in files):
        raise RuntimeError("PyInstaller onedir payload has no declared executable entrypoint")
    return files


def _file_payload(path: Path, relative_path: str, *, executable: bool) -> dict[str, object]:
    return {"path": relative_path, "size_bytes": path.stat().st_size, "sha256": _digest_file(path), "mode_class": "regular_executable" if executable else "regular_readonly", "executable": executable}


def _compatibility_payload() -> dict[str, object]:
    protocol = {"protocol_id": "seektalent-source-port", "major": 1, "min_minor": 0, "max_minor": 0, "capabilities": []}
    schema = {"logical_name": "main", "reader_min": 1, "reader_max": 1, "writer_target": 1, "migration_plan_id": "test-only-main-schema"}
    return {
        "main_sidecar": {"product_build_id": "st1-00000000000000000000000000000000", "source_port_protocol": protocol, "required_operation_contract_ids": ["cards", "cleanup", "continuation", "details", "search", "verify_session"]},
        "sidecar_wtscli": {"wtscli_build_id": "test-only-wtscli", "wtscli_tree_sha256": "0" * 64, "bridge_build_id": "test-only-bridge", "bridge_tree_sha256": "0" * 64, "bridge_protocol": protocol},
        "evidence_schemas": {"diagnostic_event_schema_ref": "test-only-diagnostic", "failure_envelope_schema_ref": "test-only-failure", "receipt_schema_ref": "test-only-receipt", "operation_evidence_schema_ref": "test-only-operation-evidence"},
        "runtime_control_schema": schema,
        "databases": [schema],
        "sidecar_journal": {"schema_range": schema, "sqlite_component_id": "sqlite_runtime", "journal_mode": "DELETE", "synchronous": "FULL"},
        "result_spool": {"schema_range": schema, "retention_policy_ref": "test-only-spool-retention"},
        "previous_product_builds": [],
        "binary_rollback": "manual_recovery_only",
        "chrome_window_ref": "test-only-chrome-window",
        "extension_window_ref": "test-only-extension-window",
        "domi_window_ref": "test-only-domi-window",
    }


def _installer_contract(components: list[dict[str, object]]) -> dict[str, object]:
    component = next(item for item in components if item["component_id"] == "installer_updater_support")
    files = {str(item["path"]): item for item in _mappings(component["files"])}

    def tool(path: str) -> dict[str, object]:
        return {
            "component_id": "installer_updater_support",
            "build_id": component["build_id"],
            "version": "1.0.0",
            "file_ref": files[path],
        }

    return {
        "installer": tool("installer.bin"), "updater": tool("updater.bin"), "uninstaller": tool("uninstaller.bin"),
        "supported_actions": ["activate", "rollback", "uninstall"], "supported_source_versions": [], "minimum_installer_version": "1.0.0",
        "signature_requirement_ref": "test-only-signature-requirement", "notarization_requirement_ref": None,
        "installed_manifest_path": "release/release-manifest.json", "pointer_schema_ref": "test-only-active-slot", "activation_journal_schema_ref": "test-only-activation-journal", "required_preflight_ids": ["test-only-platform"], "typed_reject_registry_ref": "test-only-rejects", "privilege_posture": "per_user_non_admin",
    }


def _write_test_only_signature(slot_root: Path, manifest: object, seed: bytes) -> None:
    from seektalent.release_manifest import ReleaseManifestV1

    if not isinstance(manifest, ReleaseManifestV1):
        raise TypeError("manifest must be parsed before signing")
    signature = Ed25519PrivateKey.from_private_bytes(seed).sign(canonical_release_manifest_bytes(manifest))
    payload = {
        "schema_version": "seektalent.release-manifest-signature/v1",
        "manifest_id": manifest.manifest_id,
        "product_build_id": manifest.product_build_id,
        "release_manifest_sha256": release_manifest_digest(manifest),
        "signer_role": "release_manifest_signer",
        "signer_key_id": TEST_ONLY_SIGNER_KEY_ID,
        "algorithm": "ed25519",
        "trust_policy_id": TEST_ONLY_TRUST_POLICY_ID,
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    path = slot_root / "release" / "signatures" / "release-manifest.sig"
    path.parent.mkdir()
    path.write_bytes(_json_bytes(payload))


def _tree_digest(files: list[dict[str, object]]) -> str:
    return sha256("".join(f"{item['sha256']}  {item['path']}\n" for item in files).encode()).hexdigest()


def _payload_tree_digest(components: list[dict[str, object]]) -> str:
    entries = sorted(
        (f"{component['root_path']}/{item['path']}", str(item["sha256"]))
        for component in components
        for item in _mappings(component["files"])
    )
    return sha256("".join(f"{digest}  {path}\n" for path, digest in entries).encode()).hexdigest()


def _expected_product_build_id(payload: dict[str, object]) -> str:
    components = _mappings(payload["components"])
    recipe = _mapping(payload["build_recipe"])
    dependencies = _mappings(payload["dependency_inputs"])
    target = _mapping(payload["target"])
    identity = {
        "build_recipe_digest": recipe["digest"],
        "component_build_identities": [{"build_id": item["build_id"], "component_id": item["component_id"]} for item in components],
        "dependency_input_digests": [item["sha256"] for item in dependencies],
        "product_version": payload["product_version"],
        "source_revision": payload["source_revision"],
        "target": {"arch": target["arch"], "os": target["os"]},
    }
    return f"st1-{sha256(rfc8785.dumps(identity)).hexdigest()[:32]}"


def _digest_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("expected a JSON object")
    parsed: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError("expected string JSON object keys")
        parsed[key] = item
    return parsed


def _mappings(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise TypeError("expected a JSON object list")
    return [_mapping(item) for item in value]


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("expected an integer")
    return value


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--test-only", action="store_true")
    args = parser.parse_args()
    if not args.test_only:
        parser.error("only the explicitly test-only native CI artifact is supported")
    build_packaged_sidecar(args.output.resolve(), TEST_ONLY_SIGNING_SEED)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
