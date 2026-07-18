from __future__ import annotations

import copy
import json
from hashlib import sha256

import pytest
import rfc8785

from seektalent.release_manifest import (
    ReleaseManifestError,
    ReleaseManifestReason,
    ReleaseManifestV1,
    canonical_release_manifest_bytes,
    declared_component_tree_digest,
    declared_payload_tree_digest,
    expected_product_build_id,
    parse_release_manifest,
    product_build_identity_bytes,
    release_manifest_digest,
    same_manifest_identity_conflict,
)
from seektalent.storage.json import canonical_json


SOURCE_SHA = "1" * 40
SOURCE_TREE_SHA = "2" * 64
RECIPE_SHA = "3" * 64
LOCK_SHA = "4" * 64
WEB_LOCK_SHA = "5" * 64
FILE_SHA = "6" * 64

TARGETS = (
    {"os": "windows", "arch": "x86_64", "min_os_build": "10.0.22000", "max_os_build": "10.0.26100"},
    {"os": "macos", "arch": "x86_64", "min_os_build": "13.0", "max_os_build": "15.9"},
    {"os": "macos", "arch": "arm64", "min_os_build": "13.0", "max_os_build": "15.9"},
)

GOLDEN_MANIFEST_DIGESTS = {
    ("windows", "x86_64"): "ef98599f4c45a121b6f5b7d21b6988921835eceb6580ac517a2d7a0b438e60d0",
    ("macos", "x86_64"): "74e608fa892285fd30ab0b425ab483d73260398a6ed6788df724e0b9b4a73c07",
    ("macos", "arm64"): "86cd5693582adc92c6b25b5057bc11e9ff8fd50d337c1835166d3ce1e40227e0",
}


def _tree_digest(files: list[dict[str, object]]) -> str:
    content = "".join(f"{file['sha256']}  {file['path']}\n" for file in files)
    return sha256(content.encode()).hexdigest()


def _file(path: str, *, executable: bool = False, digest: str = FILE_SHA, size: int = 10) -> dict[str, object]:
    return {
        "path": path,
        "size_bytes": size,
        "sha256": digest,
        "mode_class": "regular_executable" if executable else "regular_readonly",
        "executable": executable,
    }


def _component(
    component_id: str,
    kind: str,
    root_path: str,
    dependencies: list[str],
    target: dict[str, str],
    *,
    files: list[dict[str, object]] | None = None,
    platform_independent: bool = False,
) -> dict[str, object]:
    component_files = files or [_file("component.bin", executable=True)]
    executable_paths = [str(file["path"]) for file in component_files if file["executable"]]
    return {
        "component_id": component_id,
        "component_kind": kind,
        "version": "1.0.0",
        "build_id": f"{component_id}-build-1",
        "source_ref": {
            "repository": "https://github.com/FrankQDWang/SeekTalent-é",
            "revision": SOURCE_SHA,
            "artifact_ref": None,
        },
        "root_path": root_path,
        "entrypoints": sorted(executable_paths[:1]),
        "files": component_files,
        "tree_sha256": _tree_digest(component_files),
        "size_bytes": sum(int(file["size_bytes"]) for file in component_files),
        "platform": "platform_independent" if platform_independent else target,
        "dependencies": sorted(dependencies),
        "protocols": [],
        "code_signature_ref": f"signature/{component_id}" if executable_paths else None,
        "build_provenance_ref": f"provenance/{component_id}",
    }


def _manifest_payload(target: dict[str, str]) -> dict[str, object]:
    component_specs = [
        ("browser_bridge", "bridge", "bridge", []),
        (
            "installer_updater_support",
            "installer_support",
            "installer-support",
            [
                "browser_bridge",
                "licenses_sbom",
                "liepin_execution_sidecar",
                "main_application",
                "node_runtime",
                "python_runtime",
                "sqlite_runtime",
                "workbench_assets",
                "wtscli_runtime",
            ],
        ),
        ("licenses_sbom", "metadata", "licenses", []),
        (
            "liepin_execution_sidecar",
            "sidecar",
            "bin/sidecar",
            ["browser_bridge", "node_runtime", "python_runtime", "sqlite_runtime", "wtscli_runtime"],
        ),
        (
            "main_application",
            "application",
            "bin/main",
            ["liepin_execution_sidecar", "python_runtime", "sqlite_runtime", "workbench_assets"],
        ),
        ("node_runtime", "runtime", "runtimes/node", []),
        ("python_runtime", "runtime", "runtimes/python", []),
        ("sqlite_runtime", "runtime", "runtimes/sqlite", []),
        ("workbench_assets", "assets", "workbench", []),
        ("wtscli_runtime", "runtime", "runtimes/wtscli", ["browser_bridge", "node_runtime"]),
    ]
    components: list[dict[str, object]] = []
    for component_id, kind, root, dependencies in component_specs:
        files = None
        independent = component_id in {"licenses_sbom", "workbench_assets"}
        if component_id == "licenses_sbom":
            files = [_file("licenses.json", digest="7" * 64), _file("sbom.json", digest="8" * 64)]
        elif component_id == "installer_updater_support":
            files = [
                _file("installer.bin", executable=True),
                _file("uninstaller.bin", executable=True),
                _file("updater.bin", executable=True),
            ]
        elif component_id == "workbench_assets":
            files = [_file("index.html", digest="9" * 64)]
        components.append(
            _component(
                component_id,
                kind,
                root,
                dependencies,
                target,
                files=files,
                platform_independent=independent,
            )
        )

    payload: dict[str, object] = {
        "schema_version": "seektalent.release-manifest/v1",
        "manifest_id": f"manifest-{target['os']}-{target['arch']}",
        "release_series_id": "release-series-1",
        "product_name": "SeekTalent",
        "product_version": "0.7.49",
        "product_build_id": "st1-" + "0" * 32,
        "source_revision": SOURCE_SHA,
        "source_tree_digest": SOURCE_TREE_SHA,
        "build_recipe": {
            "recipe_id": "release-recipe",
            "revision": "recipe-revision-1",
            "digest": RECIPE_SHA,
            "runner_image_ref": "runner/image@sha256:abc",
            "toolchain_refs": ["node/24.16.0", "python/3.12.11"],
        },
        "dependency_inputs": [
            {"name": "python-lock", "sha256": LOCK_SHA, "platform_scope": target},
            {"name": "web-lock", "sha256": WEB_LOCK_SHA, "platform_scope": "platform_independent"},
        ],
        "target": target,
        "channel": "candidate",
        "created_at": "2026-07-18T12:00:00Z",
        "payload_root": "release",
        "payload_tree_sha256": "0" * 64,
        "components": components,
        "external_dependencies": {
            "chrome_stable": {
                "channel": "stable",
                "tested_min_version": "130.0.0",
                "tested_max_version": "140.0.0",
                "allowed_os_policy_postures": ["default", "enterprise-managed"],
            },
            "chrome_profile": {
                "mode": "existing_profile_compatibility",
                "required_binding_fields": ["account-ref", "extension-id", "profile-ref"],
                "residual_risk_policy_ref": "policy/existing-profile-risk/v1",
            },
            "production_extension": {
                "distribution": "chrome_web_store",
                "extension_id": "a" * 32,
                "store_item_ref": "cws/item/production",
                "protocol_major": 1,
                "protocol_min_minor": 0,
                "protocol_max_minor": 2,
                "required_capabilities": ["cards", "details", "search"],
                "min_version": "1.0.0",
                "max_version": "1.2.0",
                "min_build": 100,
                "max_build": 120,
                "compatibility_matrix_ref": "matrix/extension/v1",
            },
            "domi_host": {
                "posture": "optional",
                "tested_min_version": "1.0.0",
                "tested_max_version": "2.0.0",
                "tested_min_build": 100,
                "tested_max_build": 200,
                "launch_contract_ref": "contract/domi-launch/v1",
            },
            "network_postures": [
                {"posture_id": "direct", "mode": "direct"},
                {"posture_id": "proxy-corporate", "mode": "validated_proxy"},
            ],
            "provider": {"source": "liepin", "real_canary_policy_ref": "policy/liepin-canary/v1"},
        },
        "compatibility": {
            "main_sidecar": {
                "product_build_id": "st1-" + "0" * 32,
                "source_port_protocol": {
                    "protocol_id": "seektalent-source-port",
                    "major": 1,
                    "min_minor": 0,
                    "max_minor": 2,
                    "capabilities": ["authenticated-framing", "rollback-journal"],
                },
                "required_operation_contract_ids": [
                    "cards",
                    "cleanup",
                    "continuation",
                    "details",
                    "search",
                    "verify_session",
                ],
            },
            "sidecar_wtscli": {
                "wtscli_build_id": "wtscli-build-1",
                "wtscli_tree_sha256": "a" * 64,
                "bridge_build_id": "bridge-build-1",
                "bridge_tree_sha256": "b" * 64,
                "bridge_protocol": {
                    "protocol_id": "browser-bridge",
                    "major": 1,
                    "min_minor": 0,
                    "max_minor": 1,
                    "capabilities": ["cards", "details"],
                },
            },
            "evidence_schemas": {
                "diagnostic_event_schema_ref": "seektalent.diagnostic-event/v1",
                "failure_envelope_schema_ref": "seektalent.failure-envelope/v1",
                "receipt_schema_ref": "seektalent.receipt/v1",
                "operation_evidence_schema_ref": "seektalent.operation-evidence/v1",
            },
            "runtime_control_schema": _schema_range("runtime-control"),
            "databases": [_schema_range("main"), _schema_range("runtime-control")],
            "sidecar_journal": {
                "schema_range": _schema_range("sidecar-journal"),
                "sqlite_component_id": "sqlite_runtime",
                "journal_mode": "DELETE",
                "synchronous": "FULL",
            },
            "result_spool": {
                "schema_range": _schema_range("result-spool"),
                "retention_policy_ref": "retention/result-spool/v1",
            },
            "previous_product_builds": [],
            "binary_rollback": "requires_activation_backup_restore",
            "chrome_window_ref": "window/chrome/v1",
            "extension_window_ref": "window/extension/v1",
            "domi_window_ref": "window/domi/v1",
        },
        "storage_contract": {
            "install_root": "INSTALL_ROOT",
            "data_root": "DATA_ROOT",
            "profile_mode": "existing_profile_compatibility",
            "pointer_schema_ref": "schema/active-slot/v1",
            "minimum_atomic_filesystem_capability": "atomic-replace-fsync",
            "database_names": ["main", "runtime-control"],
            "backup_group_schema_ref": "schema/backup-group/v1",
            "minimum_free_space_formula": "payload-times-two-plus-backup",
            "sidecar_journal_path": "sidecar/command-journal.sqlite3",
            "result_spool_path": "sidecar/result-spool",
            "rollback_mode": "DELETE",
            "synchronous": "FULL",
            "retention_policy_ref": "retention/storage/v1",
            "profile_binding_schema_ref": "schema/profile-binding/v1",
            "profile_binding_generation_policy_ref": "policy/profile-generation/v1",
            "authority_rotation_policy_id": "authority-rotation-v1",
            "uninstall_default": "preserve_user_data_and_profile",
            "purge_requires_explicit_confirmation": True,
            "purge_optional_final_backup": True,
        },
        "installer_contract": {
            "installer": _installer_tool("installer.bin"),
            "updater": _installer_tool("updater.bin"),
            "uninstaller": _installer_tool("uninstaller.bin"),
            "supported_actions": [
                "activate",
                "clean_install",
                "drain",
                "preflight",
                "repair",
                "rollback",
                "stage",
                "uninstall",
                "upgrade",
            ],
            "supported_source_versions": ["0.7.48"],
            "minimum_installer_version": "1.0.0",
            "signature_requirement_ref": "policy/installer-signature/v1",
            "notarization_requirement_ref": None if target["os"] == "windows" else "policy/notarization/v1",
            "installed_manifest_path": "release/release-manifest.json",
            "pointer_schema_ref": "schema/active-slot/v1",
            "activation_journal_schema_ref": "schema/activation-journal/v1",
            "required_preflight_ids": ["disk-space", "filesystem-atomicity", "platform-identity"],
            "typed_reject_registry_ref": "registry/installer-reject/v1",
            "privilege_posture": "per_user_non_admin",
        },
        "evidence_policy": {
            "schema_refs": ["seektalent.operation-evidence/v1", "seektalent.receipt/v1"],
            "matrix_revision": "release-evidence-matrix-v1",
            "required_evidence_classes": ["build", "component", "sbom", "secret-scan"],
        },
        "build_evidence_refs": ["evidence/build/1", "evidence/sbom/1"],
        "signing_policy": {
            "required_signer_ids": ["release-authority"],
            "algorithms": ["ed25519"],
            "platform_verification_kinds": ["detached-manifest"],
        },
        "sbom_ref": _file("licenses/sbom.json", digest="8" * 64),
        "license_inventory_ref": _file("licenses/licenses.json", digest="7" * 64),
    }
    _recalculate(payload)
    return payload


def _schema_range(name: str) -> dict[str, object]:
    return {
        "logical_name": name,
        "reader_min": 1,
        "reader_max": 2,
        "writer_target": 2,
        "migration_plan_id": f"migration/{name}/v2",
    }


def _installer_tool(path: str) -> dict[str, object]:
    return {
        "component_id": "installer_updater_support",
        "build_id": "installer_updater_support-build-1",
        "version": "1.0.0",
        "file_ref": _file(path, executable=True),
    }


def _recalculate(payload: dict[str, object]) -> None:
    components = payload["components"]
    assert isinstance(components, list)
    entries: list[tuple[str, str]] = []
    for component in components:
        assert isinstance(component, dict)
        files = component["files"]
        assert isinstance(files, list)
        component["tree_sha256"] = _tree_digest(files)
        component["size_bytes"] = sum(int(file["size_bytes"]) for file in files)
        entries.extend((f"{component['root_path']}/{file['path']}", str(file["sha256"])) for file in files)
    tree = "".join(f"{digest}  {path}\n" for path, digest in sorted(entries))
    payload["payload_tree_sha256"] = sha256(tree.encode()).hexdigest()
    build_recipe = payload["build_recipe"]
    dependency_inputs = payload["dependency_inputs"]
    target = payload["target"]
    assert isinstance(build_recipe, dict)
    assert isinstance(dependency_inputs, list)
    assert isinstance(target, dict)
    identity = {
        "build_recipe_digest": build_recipe["digest"],
        "component_build_identities": [
            {"build_id": component["build_id"], "component_id": component["component_id"]}
            for component in components
        ],
        "dependency_input_digests": [item["sha256"] for item in dependency_inputs],
        "product_version": payload["product_version"],
        "source_revision": payload["source_revision"],
        "target": {"arch": target["arch"], "os": target["os"]},
    }
    product_build_id = f"st1-{sha256(rfc8785.dumps(identity)).hexdigest()[:32]}"
    payload["product_build_id"] = product_build_id
    compatibility = payload["compatibility"]
    assert isinstance(compatibility, dict)
    main_sidecar = compatibility["main_sidecar"]
    assert isinstance(main_sidecar, dict)
    main_sidecar["product_build_id"] = product_build_id


def _raw(payload: object, *, sort_keys: bool = False, ensure_ascii: bool = False) -> bytes:
    return json.dumps(payload, ensure_ascii=ensure_ascii, sort_keys=sort_keys, separators=(",", ":")).encode()


def _parse(target: dict[str, str] | None = None) -> ReleaseManifestV1:
    return parse_release_manifest(_raw(_manifest_payload(target or TARGETS[0])))


def _assert_reason(payload: dict[str, object], reason: ReleaseManifestReason) -> None:
    with pytest.raises(ReleaseManifestError) as raised:
        parse_release_manifest(_raw(payload))
    assert raised.value.reason == reason


@pytest.mark.parametrize("target", TARGETS)
def test_valid_targets_have_deterministic_identity_and_canonical_golden(target: dict[str, str]) -> None:
    payload = _manifest_payload(target)
    manifest = parse_release_manifest(_raw(payload))

    assert expected_product_build_id(manifest) == manifest.product_build_id
    assert product_build_identity_bytes(manifest) == product_build_identity_bytes(_parse(target))
    assert release_manifest_digest(manifest) == GOLDEN_MANIFEST_DIGESTS[(target["os"], target["arch"])]
    assert canonical_release_manifest_bytes(manifest) == rfc8785.dumps(manifest.model_dump(mode="json"))
    assert declared_payload_tree_digest(manifest) == manifest.payload_tree_sha256
    assert all(declared_component_tree_digest(component) == component.tree_sha256 for component in manifest.components)


def test_canonical_digest_is_independent_of_raw_key_order_and_unicode_escape() -> None:
    payload = _manifest_payload(TARGETS[1])
    direct = parse_release_manifest(_raw(payload, ensure_ascii=False))
    escaped_and_sorted = parse_release_manifest(_raw(payload, sort_keys=True, ensure_ascii=True))

    assert release_manifest_digest(direct) == release_manifest_digest(escaped_and_sorted)
    assert "é".encode() in canonical_release_manifest_bytes(direct)


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (b'{"schema_version":"a","schema_version":"b"}', ReleaseManifestReason.DUPLICATE_KEY),
        (b'{"value":NaN}', ReleaseManifestReason.ILLEGAL_NUMBER),
        (b'{"value":Infinity}', ReleaseManifestReason.ILLEGAL_NUMBER),
        (b'{"value":1.0}', ReleaseManifestReason.ILLEGAL_NUMBER),
        (b'{"value":-0}', ReleaseManifestReason.ILLEGAL_NUMBER),
        (b'{"value":9007199254740992}', ReleaseManifestReason.ILLEGAL_NUMBER),
        (b'[]', ReleaseManifestReason.ROOT_NOT_OBJECT),
        (b'\xff', ReleaseManifestReason.INVALID_UTF8),
        (b'{"value":"\\ud800"}', ReleaseManifestReason.INVALID_UNICODE),
    ],
)
def test_strict_raw_loader_rejects_ambiguous_json(raw: bytes, reason: ReleaseManifestReason) -> None:
    with pytest.raises(ReleaseManifestError) as raised:
        parse_release_manifest(raw)
    assert raised.value.reason == reason


def test_unknown_field_has_stable_reason_code() -> None:
    payload = _manifest_payload(TARGETS[0])
    payload["unknown"] = True
    _assert_reason(payload, ReleaseManifestReason.UNKNOWN_FIELD)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("schema_version", "seektalent.release-manifest/v2", ReleaseManifestReason.SCHEMA_VALIDATION),
        ("product_name", "OtherProduct", ReleaseManifestReason.SCHEMA_VALIDATION),
        ("channel", "release", ReleaseManifestReason.SCHEMA_VALIDATION),
        ("source_revision", "A" * 40, ReleaseManifestReason.INVALID_VALUE),
        ("source_tree_digest", "A" * 64, ReleaseManifestReason.INVALID_VALUE),
        ("product_build_id", "st1-" + "A" * 32, ReleaseManifestReason.INVALID_VALUE),
    ],
)
def test_invalid_top_level_contract_values(
    field: str,
    value: object,
    reason: ReleaseManifestReason,
) -> None:
    payload = _manifest_payload(TARGETS[0])
    payload[field] = value
    _assert_reason(payload, reason)


@pytest.mark.parametrize(
    "target",
    [
        {"os": "windows", "arch": "arm64", "min_os_build": "10.0.22000", "max_os_build": "10.0.26100"},
        {"os": "windows", "arch": "x86_64", "min_os_build": "10.0.19045", "max_os_build": "10.0.26100"},
        {"os": "macos", "arch": "universal2", "min_os_build": "13.0", "max_os_build": "15.9"},
    ],
)
def test_invalid_target_tuple_is_rejected(target: dict[str, str]) -> None:
    payload = _manifest_payload(TARGETS[0])
    payload["target"] = target
    _assert_reason(payload, ReleaseManifestReason.PLATFORM_MISMATCH if target["arch"] != "universal2" else ReleaseManifestReason.SCHEMA_VALIDATION)


@pytest.mark.parametrize("path", ["/absolute", "../escape", "a/../escape", "a\\b", "a:b", "a//b", "e\u0301.txt"])
def test_illegal_paths_are_rejected(path: str) -> None:
    payload = _manifest_payload(TARGETS[0])
    component = payload["components"][0]
    component["files"][0]["path"] = path
    _assert_reason(payload, ReleaseManifestReason.INVALID_PATH)


@pytest.mark.parametrize("collision_kind", ["exact", "casefold", "unicode_normalization"])
def test_file_path_collisions_are_rejected(collision_kind: str) -> None:
    payload = _manifest_payload(TARGETS[0])
    first = payload["components"][0]
    second = payload["components"][1]
    if collision_kind == "exact":
        second["root_path"] = first["root_path"]
    elif collision_kind == "casefold":
        first["root_path"] = "Bridge"
        second["root_path"] = "bridge"
    else:
        first["root_path"] = "caf\u00e9"
        second["root_path"] = "CAFE\u0301"
    _recalculate(payload)
    expected = ReleaseManifestReason.INVALID_PATH if collision_kind == "unicode_normalization" else ReleaseManifestReason.PATH_COLLISION
    _assert_reason(payload, expected)


def test_missing_and_duplicate_components_are_rejected() -> None:
    missing = _manifest_payload(TARGETS[0])
    missing["components"].pop()
    _recalculate(missing)
    _assert_reason(missing, ReleaseManifestReason.COMPONENT_CLOSURE)

    duplicate = _manifest_payload(TARGETS[0])
    duplicate["components"].insert(1, copy.deepcopy(duplicate["components"][0]))
    _recalculate(duplicate)
    _assert_reason(duplicate, ReleaseManifestReason.COMPONENT_CONFLICT)


def test_unknown_dependency_and_cycle_are_rejected() -> None:
    unknown = _manifest_payload(TARGETS[0])
    unknown["components"][0]["dependencies"] = ["unknown_component"]
    _assert_reason(unknown, ReleaseManifestReason.UNKNOWN_DEPENDENCY)

    cycle = _manifest_payload(TARGETS[0])
    cycle["components"][0]["dependencies"] = ["main_application"]
    _assert_reason(cycle, ReleaseManifestReason.DEPENDENCY_CYCLE)


def test_platform_mismatch_is_rejected() -> None:
    payload = _manifest_payload(TARGETS[0])
    payload["components"][0]["platform"] = TARGETS[1]
    _assert_reason(payload, ReleaseManifestReason.PLATFORM_MISMATCH)


@pytest.mark.parametrize("list_path", ["build_evidence_refs", "dependency_inputs", "components"])
def test_unsorted_contract_lists_are_rejected(list_path: str) -> None:
    payload = _manifest_payload(TARGETS[0])
    values = payload[list_path]
    assert isinstance(values, list)
    values.reverse()
    _assert_reason(payload, ReleaseManifestReason.NON_CANONICAL_ORDER)


def test_declared_tree_and_build_identity_mismatches_are_rejected() -> None:
    tree_mismatch = _manifest_payload(TARGETS[0])
    tree_mismatch["payload_tree_sha256"] = "f" * 64
    _assert_reason(tree_mismatch, ReleaseManifestReason.DIGEST_MISMATCH)

    build_mismatch = _manifest_payload(TARGETS[0])
    build_mismatch["product_build_id"] = "st1-" + "f" * 32
    build_mismatch["compatibility"]["main_sidecar"]["product_build_id"] = "st1-" + "f" * 32
    _assert_reason(build_mismatch, ReleaseManifestReason.BUILD_ID_MISMATCH)


def test_reserved_metadata_paths_and_installer_refs_are_bound_to_component_closure() -> None:
    reserved = _manifest_payload(TARGETS[0])
    reserved["components"][0]["root_path"] = "signatures"
    _recalculate(reserved)
    _assert_reason(reserved, ReleaseManifestReason.INVALID_PATH)

    installer_mismatch = _manifest_payload(TARGETS[0])
    installer_mismatch["installer_contract"]["updater"]["build_id"] = "other-build"
    _assert_reason(installer_mismatch, ReleaseManifestReason.COMPONENT_CLOSURE)


def test_empty_declared_compatibility_window_is_rejected() -> None:
    payload = _manifest_payload(TARGETS[0])
    payload["external_dependencies"]["production_extension"]["min_build"] = 121
    _assert_reason(payload, ReleaseManifestReason.INVALID_VALUE)


def test_same_manifest_id_conflict_is_pure_and_digest_based() -> None:
    existing = _parse()
    same = _parse()
    changed_payload = _manifest_payload(TARGETS[0])
    changed_payload["created_at"] = "2026-07-18T12:00:01Z"
    changed = parse_release_manifest(_raw(changed_payload))

    assert not same_manifest_identity_conflict(existing, same)
    assert same_manifest_identity_conflict(existing, changed)


def test_public_boundary_rejects_dict_bypass_but_typed_construction_is_explicit() -> None:
    payload = _manifest_payload(TARGETS[0])
    with pytest.raises(ReleaseManifestError) as raised:
        parse_release_manifest(payload)  # type: ignore[arg-type]
    assert raised.value.reason == ReleaseManifestReason.RAW_INPUT_REQUIRED
    with pytest.raises(ReleaseManifestError) as model_validate_raised:
        ReleaseManifestV1.model_validate(payload)
    assert model_validate_raised.value.reason == ReleaseManifestReason.RAW_INPUT_REQUIRED

    parsed = parse_release_manifest(_raw(payload))
    explicitly_typed = ReleaseManifestV1(**parsed.model_dump(mode="python"))
    assert explicitly_typed == parsed
    with pytest.raises(ReleaseManifestError):
        canonical_release_manifest_bytes(payload)  # type: ignore[arg-type]


def test_direct_model_validate_json_uses_duplicate_aware_bytes_boundary() -> None:
    payload = _manifest_payload(TARGETS[0])
    raw = _raw(payload)
    duplicate = b'{"schema_version":"invalid",' + raw[1:]

    with pytest.raises(ReleaseManifestError) as raised:
        ReleaseManifestV1.model_validate_json(duplicate, strict=True)

    assert raised.value.reason == ReleaseManifestReason.DUPLICATE_KEY


@pytest.mark.parametrize("raw", ["{}", bytearray(b"{}")])
def test_direct_model_validate_json_rejects_non_bytes(raw: str | bytearray) -> None:
    with pytest.raises(ReleaseManifestError) as raised:
        ReleaseManifestV1.model_validate_json(raw, strict=True)

    assert raised.value.reason == ReleaseManifestReason.RAW_INPUT_REQUIRED


def test_direct_model_validate_json_matches_parse_release_manifest() -> None:
    raw = _raw(_manifest_payload(TARGETS[2]))

    assert ReleaseManifestV1.model_validate_json(raw, strict=True) == parse_release_manifest(raw)


def test_existing_storage_canonical_json_behavior_is_unchanged() -> None:
    assert canonical_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'
