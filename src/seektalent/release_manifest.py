from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Annotated, Literal, LiteralString, Self

import rfc8785
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from pydantic_core import PydanticCustomError


MAX_SAFE_INTEGER = (1 << 53) - 1
REQUIRED_COMPONENT_IDS = frozenset(
    {
        "main_application",
        "liepin_execution_sidecar",
        "python_runtime",
        "sqlite_runtime",
        "node_runtime",
        "wtscli_runtime",
        "browser_bridge",
        "workbench_assets",
        "installer_updater_support",
        "licenses_sbom",
    }
)
REQUIRED_OPERATION_CONTRACT_IDS = (
    "cards",
    "cleanup",
    "continuation",
    "details",
    "search",
    "verify_session",
)
COMPONENT_KINDS = {
    "main_application": "application",
    "liepin_execution_sidecar": "sidecar",
    "python_runtime": "runtime",
    "sqlite_runtime": "runtime",
    "node_runtime": "runtime",
    "wtscli_runtime": "runtime",
    "browser_bridge": "bridge",
    "workbench_assets": "assets",
    "installer_updater_support": "installer_support",
    "licenses_sbom": "metadata",
}

SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
GIT_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
BUILD_ID_RE = re.compile(r"st1-[0-9a-f]{32}\Z")
OPAQUE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/+\-=]{0,127}\Z")
VERSION_RE = re.compile(r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){1,3}(?:[-+][0-9A-Za-z.-]+)?\Z")
OS_BUILD_RE = re.compile(r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){1,3}\Z")
UTC_RFC3339_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")


class ReleaseManifestReason(StrEnum):
    RAW_INPUT_REQUIRED = "raw_input_required"
    INVALID_UTF8 = "invalid_utf8"
    INVALID_JSON = "invalid_json"
    DUPLICATE_KEY = "duplicate_key"
    ILLEGAL_NUMBER = "illegal_number"
    INVALID_UNICODE = "invalid_unicode"
    ROOT_NOT_OBJECT = "root_not_object"
    UNKNOWN_FIELD = "unknown_field"
    SCHEMA_VALIDATION = "schema_validation"
    INVALID_VALUE = "invalid_value"
    NON_CANONICAL_ORDER = "non_canonical_order"
    INVALID_PATH = "invalid_path"
    PATH_COLLISION = "path_collision"
    COMPONENT_CLOSURE = "component_closure"
    COMPONENT_CONFLICT = "component_conflict"
    UNKNOWN_DEPENDENCY = "unknown_dependency"
    DEPENDENCY_CYCLE = "dependency_cycle"
    PLATFORM_MISMATCH = "platform_mismatch"
    DIGEST_MISMATCH = "digest_mismatch"
    BUILD_ID_MISMATCH = "build_id_mismatch"


class ReleaseManifestError(ValueError):
    def __init__(self, reason: ReleaseManifestReason, location: tuple[str | int, ...] = ()) -> None:
        self.reason = reason
        self.location = location
        super().__init__(reason.value)


def _error(reason: ReleaseManifestReason, message: LiteralString) -> PydanticCustomError:
    return PydanticCustomError(reason.value, message)


def _validate_unicode(value: str) -> str:
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise _error(ReleaseManifestReason.INVALID_UNICODE, "strings must contain Unicode scalar values")
    return value


def _validate_sha256(value: str) -> str:
    if SHA256_RE.fullmatch(value) is None:
        raise _error(ReleaseManifestReason.INVALID_VALUE, "SHA-256 must be 64 lowercase hexadecimal characters")
    return value


def _validate_git_sha(value: str) -> str:
    if GIT_SHA_RE.fullmatch(value) is None:
        raise _error(ReleaseManifestReason.INVALID_VALUE, "source revision must be a full lowercase Git SHA")
    return value


def _validate_build_id(value: str) -> str:
    if BUILD_ID_RE.fullmatch(value) is None:
        raise _error(ReleaseManifestReason.INVALID_VALUE, "product build ID has an invalid format")
    return value


def _validate_opaque(value: str) -> str:
    if OPAQUE_RE.fullmatch(value) is None:
        raise _error(ReleaseManifestReason.INVALID_VALUE, "reference must use the bounded opaque token format")
    return value


def _validate_version(value: str) -> str:
    if VERSION_RE.fullmatch(value) is None:
        raise _error(ReleaseManifestReason.INVALID_VALUE, "version must use normalized numeric components")
    return value


def _version_key(value: str) -> tuple[tuple[int, ...], int, str]:
    core, separator, suffix = value.partition("-")
    core, plus, build = core.partition("+")
    suffix = suffix or build
    numbers = tuple(int(part) for part in core.split("."))
    return numbers + (0,) * (4 - len(numbers)), 0 if separator else 1, suffix if separator or plus else ""


def _validate_relative_path(value: str) -> str:
    if not value or value != unicodedata.normalize("NFC", value):
        raise _error(ReleaseManifestReason.INVALID_PATH, "path must be non-empty NFC")
    if "\\" in value or ":" in value or "\x00" in value:
        raise _error(ReleaseManifestReason.INVALID_PATH, "path contains an ambiguous separator or platform syntax")
    if value.startswith("/") or value.endswith("/") or "//" in value:
        raise _error(ReleaseManifestReason.INVALID_PATH, "path must be normalized and relative")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts) or PurePosixPath(value).is_absolute():
        raise _error(ReleaseManifestReason.INVALID_PATH, "path must not escape its declared root")
    if any(any(ord(character) < 0x20 for character in part) for part in parts):
        raise _error(ReleaseManifestReason.INVALID_PATH, "path contains a control character")
    return value


Sha256 = Annotated[str, AfterValidator(_validate_sha256)]
GitSha = Annotated[str, AfterValidator(_validate_git_sha)]
ProductBuildId = Annotated[str, AfterValidator(_validate_build_id)]
OpaqueToken = Annotated[str, Field(min_length=1, max_length=128), AfterValidator(_validate_opaque)]
NormalizedVersion = Annotated[str, Field(min_length=1, max_length=64), AfterValidator(_validate_version)]
RelativePath = Annotated[str, Field(min_length=1, max_length=512), AfterValidator(_validate_relative_path)]
SafeSize = Annotated[int, Field(ge=0, le=MAX_SAFE_INTEGER)]
PositiveSafeSize = Annotated[int, Field(gt=0, le=MAX_SAFE_INTEGER)]


def _require_sorted(values: tuple[str, ...], *, unique: bool = True) -> tuple[str, ...]:
    if values != tuple(sorted(values)) or (unique and len(values) != len(set(values))):
        raise _error(ReleaseManifestReason.NON_CANONICAL_ORDER, "list must already be sorted and unique")
    return values


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @field_validator("*", mode="after")
    @classmethod
    def validate_json_scalars(cls, value: object) -> object:
        def visit(item: object) -> None:
            if isinstance(item, str):
                _validate_unicode(item)
            elif isinstance(item, int) and not isinstance(item, bool):
                if item < -MAX_SAFE_INTEGER or item > MAX_SAFE_INTEGER:
                    raise _error(ReleaseManifestReason.ILLEGAL_NUMBER, "integer is outside the I-JSON safe range")
            elif isinstance(item, tuple):
                for child in item:
                    visit(child)

        visit(value)
        return value


class TargetV1(StrictModel):
    os: Literal["windows", "macos"]
    arch: Literal["x86_64", "arm64"]
    min_os_build: Annotated[str, Field(min_length=1, max_length=32)]
    max_os_build: Annotated[str, Field(min_length=1, max_length=32)]

    @model_validator(mode="after")
    def validate_target(self) -> Self:
        if (self.os, self.arch) not in {
            ("windows", "x86_64"),
            ("macos", "x86_64"),
            ("macos", "arm64"),
        }:
            raise _error(ReleaseManifestReason.PLATFORM_MISMATCH, "target tuple is not supported by v1")
        if OS_BUILD_RE.fullmatch(self.min_os_build) is None or OS_BUILD_RE.fullmatch(self.max_os_build) is None:
            raise _error(ReleaseManifestReason.INVALID_VALUE, "OS builds must be normalized numeric versions")
        minimum = tuple(int(part) for part in self.min_os_build.split("."))
        maximum = tuple(int(part) for part in self.max_os_build.split("."))
        minimum += (0,) * (4 - len(minimum))
        maximum += (0,) * (4 - len(maximum))
        if minimum > maximum:
            raise _error(ReleaseManifestReason.INVALID_VALUE, "OS build range is empty")
        if self.os == "windows" and minimum < (10, 0, 22000):
            raise _error(ReleaseManifestReason.PLATFORM_MISMATCH, "Windows v1 requires Windows 11 or newer")
        return self


class BuildRecipeV1(StrictModel):
    recipe_id: OpaqueToken
    revision: OpaqueToken
    digest: Sha256
    runner_image_ref: OpaqueToken
    toolchain_refs: Annotated[tuple[OpaqueToken, ...], Field(min_length=1, max_length=32)]

    @field_validator("toolchain_refs")
    @classmethod
    def validate_toolchain_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _require_sorted(value)


class DependencyInputV1(StrictModel):
    name: OpaqueToken
    sha256: Sha256
    platform_scope: TargetV1 | Literal["platform_independent"]


class SourceRefV1(StrictModel):
    repository: Annotated[str, Field(min_length=1, max_length=256)] | None
    revision: GitSha | None
    artifact_ref: OpaqueToken | None

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        tracked_source = self.repository is not None or self.revision is not None
        if tracked_source and (self.repository is None or self.revision is None or self.artifact_ref is not None):
            raise _error(ReleaseManifestReason.INVALID_VALUE, "source must be repo+revision or an artifact ref")
        if not tracked_source and self.artifact_ref is None:
            raise _error(ReleaseManifestReason.INVALID_VALUE, "source reference is incomplete")
        return self


class FileRefV1(StrictModel):
    path: RelativePath
    size_bytes: SafeSize
    sha256: Sha256
    mode_class: Literal["regular_readonly", "regular_executable"]
    executable: bool

    @model_validator(mode="after")
    def validate_mode(self) -> Self:
        if self.executable != (self.mode_class == "regular_executable"):
            raise _error(ReleaseManifestReason.INVALID_VALUE, "mode class and executable flag disagree")
        return self


class ProtocolFactV1(StrictModel):
    protocol_id: OpaqueToken
    major: Annotated[int, Field(ge=1, le=65535)]
    min_minor: Annotated[int, Field(ge=0, le=65535)]
    max_minor: Annotated[int, Field(ge=0, le=65535)]
    capabilities: Annotated[tuple[OpaqueToken, ...], Field(max_length=64)] = ()

    @model_validator(mode="after")
    def validate_protocol(self) -> Self:
        if self.min_minor > self.max_minor:
            raise _error(ReleaseManifestReason.INVALID_VALUE, "protocol range is empty")
        _require_sorted(self.capabilities)
        return self


class ComponentV1(StrictModel):
    component_id: Literal[
        "main_application",
        "liepin_execution_sidecar",
        "python_runtime",
        "sqlite_runtime",
        "node_runtime",
        "wtscli_runtime",
        "browser_bridge",
        "workbench_assets",
        "installer_updater_support",
        "licenses_sbom",
    ]
    component_kind: Literal[
        "application",
        "sidecar",
        "runtime",
        "browser_engine",
        "bridge",
        "assets",
        "installer_support",
        "metadata",
    ]
    version: NormalizedVersion
    build_id: OpaqueToken
    source_ref: SourceRefV1
    root_path: RelativePath
    entrypoints: Annotated[tuple[RelativePath, ...], Field(max_length=8)] = ()
    files: Annotated[tuple[FileRefV1, ...], Field(min_length=1, max_length=4096)]
    tree_sha256: Sha256
    size_bytes: SafeSize
    platform: TargetV1 | Literal["platform_independent"]
    dependencies: Annotated[tuple[str, ...], Field(max_length=32)] = ()
    protocols: Annotated[tuple[ProtocolFactV1, ...], Field(max_length=32)] = ()
    code_signature_ref: OpaqueToken | None
    build_provenance_ref: OpaqueToken

    @model_validator(mode="after")
    def validate_component(self) -> Self:
        if COMPONENT_KINDS[self.component_id] != self.component_kind:
            raise _error(ReleaseManifestReason.COMPONENT_CONFLICT, "component ID has the wrong kind")
        _require_sorted(self.entrypoints)
        _require_sorted(self.dependencies)
        file_paths = tuple(file.path for file in self.files)
        if file_paths != tuple(sorted(file_paths)) or len(file_paths) != len(set(file_paths)):
            raise _error(ReleaseManifestReason.NON_CANONICAL_ORDER, "component files must be sorted and unique")
        protocol_ids = tuple(protocol.protocol_id for protocol in self.protocols)
        if protocol_ids != tuple(sorted(protocol_ids)) or len(protocol_ids) != len(set(protocol_ids)):
            raise _error(ReleaseManifestReason.NON_CANONICAL_ORDER, "protocol facts must be sorted and unique")
        files_by_path = {file.path: file for file in self.files}
        if any(path not in files_by_path or not files_by_path[path].executable for path in self.entrypoints):
            raise _error(ReleaseManifestReason.INVALID_VALUE, "entrypoints must name declared executable files")
        if any(file.executable for file in self.files) and self.code_signature_ref is None:
            raise _error(ReleaseManifestReason.INVALID_VALUE, "executable component requires a signature ref")
        if sum(file.size_bytes for file in self.files) != self.size_bytes:
            raise _error(ReleaseManifestReason.DIGEST_MISMATCH, "component size does not match declared files")
        if declared_component_tree_digest(self) != self.tree_sha256:
            raise _error(ReleaseManifestReason.DIGEST_MISMATCH, "component tree digest does not match declared files")
        return self


class ChromeStableDependencyV1(StrictModel):
    channel: Literal["stable"]
    tested_min_version: NormalizedVersion
    tested_max_version: NormalizedVersion
    allowed_os_policy_postures: Annotated[tuple[OpaqueToken, ...], Field(min_length=1, max_length=16)]

    @field_validator("allowed_os_policy_postures")
    @classmethod
    def validate_postures(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _require_sorted(value)

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if _version_key(self.tested_min_version) > _version_key(self.tested_max_version):
            raise _error(ReleaseManifestReason.INVALID_VALUE, "Chrome version window is empty")
        return self


class ChromeProfileDependencyV1(StrictModel):
    mode: Literal["existing_profile_compatibility"]
    required_binding_fields: Annotated[tuple[OpaqueToken, ...], Field(min_length=1, max_length=16)]
    residual_risk_policy_ref: OpaqueToken

    @field_validator("required_binding_fields")
    @classmethod
    def validate_fields(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _require_sorted(value)


class ProductionExtensionDependencyV1(StrictModel):
    distribution: Literal["chrome_web_store", "enterprise_managed"]
    extension_id: Annotated[str, Field(pattern=r"[a-p]{32}")]
    store_item_ref: OpaqueToken
    protocol_major: Annotated[int, Field(ge=1, le=65535)]
    protocol_min_minor: Annotated[int, Field(ge=0, le=65535)]
    protocol_max_minor: Annotated[int, Field(ge=0, le=65535)]
    required_capabilities: Annotated[tuple[OpaqueToken, ...], Field(min_length=1, max_length=32)]
    min_version: NormalizedVersion
    max_version: NormalizedVersion
    min_build: SafeSize
    max_build: SafeSize
    compatibility_matrix_ref: OpaqueToken

    @model_validator(mode="after")
    def validate_extension(self) -> Self:
        if self.protocol_min_minor > self.protocol_max_minor:
            raise _error(ReleaseManifestReason.INVALID_VALUE, "extension protocol range is empty")
        if _version_key(self.min_version) > _version_key(self.max_version) or self.min_build > self.max_build:
            raise _error(ReleaseManifestReason.INVALID_VALUE, "extension compatibility window is empty")
        _require_sorted(self.required_capabilities)
        return self


class DomiHostDependencyV1(StrictModel):
    posture: Literal["required", "optional"]
    tested_min_version: NormalizedVersion
    tested_max_version: NormalizedVersion
    tested_min_build: SafeSize
    tested_max_build: SafeSize
    launch_contract_ref: OpaqueToken

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if (
            _version_key(self.tested_min_version) > _version_key(self.tested_max_version)
            or self.tested_min_build > self.tested_max_build
        ):
            raise _error(ReleaseManifestReason.INVALID_VALUE, "Domi compatibility window is empty")
        return self


class NetworkPostureV1(StrictModel):
    posture_id: OpaqueToken
    mode: Literal["direct", "validated_proxy", "validated_custom_ca"]


class ProviderDependencyV1(StrictModel):
    source: Literal["liepin"]
    real_canary_policy_ref: OpaqueToken


class ExternalDependenciesV1(StrictModel):
    chrome_stable: ChromeStableDependencyV1
    chrome_profile: ChromeProfileDependencyV1
    production_extension: ProductionExtensionDependencyV1
    domi_host: DomiHostDependencyV1
    network_postures: Annotated[tuple[NetworkPostureV1, ...], Field(min_length=1, max_length=16)]
    provider: ProviderDependencyV1

    @field_validator("network_postures")
    @classmethod
    def validate_network_postures(cls, value: tuple[NetworkPostureV1, ...]) -> tuple[NetworkPostureV1, ...]:
        ids = tuple(item.posture_id for item in value)
        _require_sorted(ids)
        if not any(item.mode == "direct" for item in value):
            raise _error(ReleaseManifestReason.INVALID_VALUE, "direct network posture must be declared")
        return value


class MainSidecarCompatibilityV1(StrictModel):
    product_build_id: ProductBuildId
    source_port_protocol: ProtocolFactV1
    required_operation_contract_ids: Annotated[tuple[str, ...], Field(min_length=6, max_length=6)]

    @field_validator("required_operation_contract_ids")
    @classmethod
    def validate_operation_contracts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != REQUIRED_OPERATION_CONTRACT_IDS:
            raise _error(ReleaseManifestReason.INVALID_VALUE, "all six source operation contracts are required")
        return value


class SidecarWtscliCompatibilityV1(StrictModel):
    wtscli_build_id: OpaqueToken
    wtscli_tree_sha256: Sha256
    bridge_build_id: OpaqueToken
    bridge_tree_sha256: Sha256
    bridge_protocol: ProtocolFactV1


class EvidenceSchemaCompatibilityV1(StrictModel):
    diagnostic_event_schema_ref: OpaqueToken
    failure_envelope_schema_ref: OpaqueToken
    receipt_schema_ref: OpaqueToken
    operation_evidence_schema_ref: OpaqueToken


class SchemaRangeV1(StrictModel):
    logical_name: OpaqueToken
    reader_min: Annotated[int, Field(ge=1, le=MAX_SAFE_INTEGER)]
    reader_max: Annotated[int, Field(ge=1, le=MAX_SAFE_INTEGER)]
    writer_target: Annotated[int, Field(ge=1, le=MAX_SAFE_INTEGER)]
    migration_plan_id: OpaqueToken

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.reader_min > self.reader_max or not self.reader_min <= self.writer_target <= self.reader_max:
            raise _error(ReleaseManifestReason.INVALID_VALUE, "schema compatibility range is empty")
        return self


class SidecarJournalCompatibilityV1(StrictModel):
    schema_range: SchemaRangeV1
    sqlite_component_id: Literal["sqlite_runtime"]
    journal_mode: Literal["DELETE"]
    synchronous: Literal["FULL"]


class ResultSpoolCompatibilityV1(StrictModel):
    schema_range: SchemaRangeV1
    retention_policy_ref: OpaqueToken


class CompatibilityV1(StrictModel):
    main_sidecar: MainSidecarCompatibilityV1
    sidecar_wtscli: SidecarWtscliCompatibilityV1
    evidence_schemas: EvidenceSchemaCompatibilityV1
    runtime_control_schema: SchemaRangeV1
    databases: Annotated[tuple[SchemaRangeV1, ...], Field(min_length=1, max_length=32)]
    sidecar_journal: SidecarJournalCompatibilityV1
    result_spool: ResultSpoolCompatibilityV1
    previous_product_builds: Annotated[tuple[ProductBuildId, ...], Field(max_length=32)] = ()
    binary_rollback: Literal[
        "reads_current_schema_without_restore",
        "requires_activation_backup_restore",
        "manual_recovery_only",
    ]
    chrome_window_ref: OpaqueToken
    extension_window_ref: OpaqueToken
    domi_window_ref: OpaqueToken

    @model_validator(mode="after")
    def validate_compatibility(self) -> Self:
        database_names = tuple(database.logical_name for database in self.databases)
        _require_sorted(database_names)
        _require_sorted(self.previous_product_builds)
        return self


class StorageContractV1(StrictModel):
    install_root: Literal["INSTALL_ROOT"]
    data_root: Literal["DATA_ROOT"]
    profile_mode: Literal["existing_profile_compatibility"]
    pointer_schema_ref: OpaqueToken
    minimum_atomic_filesystem_capability: OpaqueToken
    database_names: Annotated[tuple[OpaqueToken, ...], Field(min_length=1, max_length=32)]
    backup_group_schema_ref: OpaqueToken
    minimum_free_space_formula: OpaqueToken
    sidecar_journal_path: RelativePath
    result_spool_path: RelativePath
    rollback_mode: Literal["DELETE"]
    synchronous: Literal["FULL"]
    retention_policy_ref: OpaqueToken
    profile_binding_schema_ref: OpaqueToken
    profile_binding_generation_policy_ref: OpaqueToken
    authority_rotation_policy_id: OpaqueToken
    uninstall_default: Literal["preserve_user_data_and_profile"]
    purge_requires_explicit_confirmation: Literal[True]
    purge_optional_final_backup: bool

    @field_validator("database_names")
    @classmethod
    def validate_database_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _require_sorted(value)


class InstallerToolV1(StrictModel):
    component_id: Literal["installer_updater_support"]
    build_id: OpaqueToken
    version: NormalizedVersion
    file_ref: FileRefV1


class InstallerContractV1(StrictModel):
    installer: InstallerToolV1
    updater: InstallerToolV1
    uninstaller: InstallerToolV1
    supported_actions: Annotated[tuple[str, ...], Field(min_length=1, max_length=9)]
    supported_source_versions: Annotated[tuple[NormalizedVersion, ...], Field(max_length=32)] = ()
    minimum_installer_version: NormalizedVersion
    signature_requirement_ref: OpaqueToken
    notarization_requirement_ref: OpaqueToken | None
    installed_manifest_path: RelativePath
    pointer_schema_ref: OpaqueToken
    activation_journal_schema_ref: OpaqueToken
    required_preflight_ids: Annotated[tuple[OpaqueToken, ...], Field(min_length=1, max_length=32)]
    typed_reject_registry_ref: OpaqueToken
    privilege_posture: Literal["per_user_non_admin"]

    @model_validator(mode="after")
    def validate_installer(self) -> Self:
        allowed = {
            "activate",
            "clean_install",
            "drain",
            "preflight",
            "repair",
            "rollback",
            "stage",
            "uninstall",
            "upgrade",
        }
        _require_sorted(self.supported_actions)
        if not set(self.supported_actions) <= allowed or not {"activate", "rollback", "uninstall"} <= set(
            self.supported_actions
        ):
            raise _error(ReleaseManifestReason.INVALID_VALUE, "installer action set is incomplete or unknown")
        _require_sorted(self.supported_source_versions)
        _require_sorted(self.required_preflight_ids)
        return self


class EvidencePolicyV1(StrictModel):
    schema_refs: Annotated[tuple[OpaqueToken, ...], Field(min_length=1, max_length=32)]
    matrix_revision: OpaqueToken
    required_evidence_classes: Annotated[tuple[OpaqueToken, ...], Field(min_length=1, max_length=32)]

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        _require_sorted(self.schema_refs)
        _require_sorted(self.required_evidence_classes)
        return self


class SigningPolicyV1(StrictModel):
    required_signer_ids: Annotated[tuple[OpaqueToken, ...], Field(min_length=1, max_length=16)]
    algorithms: Annotated[tuple[OpaqueToken, ...], Field(min_length=1, max_length=16)]
    platform_verification_kinds: Annotated[tuple[OpaqueToken, ...], Field(min_length=1, max_length=16)]

    @model_validator(mode="after")
    def validate_signing_policy(self) -> Self:
        _require_sorted(self.required_signer_ids)
        _require_sorted(self.algorithms)
        _require_sorted(self.platform_verification_kinds)
        return self


class ReleaseManifestV1(StrictModel):
    schema_version: Literal["seektalent.release-manifest/v1"]
    manifest_id: Annotated[str, Field(min_length=1, max_length=96), AfterValidator(_validate_opaque)]
    release_series_id: Annotated[str, Field(min_length=1, max_length=96), AfterValidator(_validate_opaque)]
    product_name: Literal["SeekTalent"]
    product_version: NormalizedVersion
    product_build_id: ProductBuildId
    source_revision: GitSha
    source_tree_digest: Sha256
    build_recipe: BuildRecipeV1
    dependency_inputs: Annotated[tuple[DependencyInputV1, ...], Field(min_length=1, max_length=32)]
    target: TargetV1
    channel: Literal["internal", "candidate", "production"]
    created_at: Annotated[str, Field(min_length=20, max_length=20)]
    payload_root: Literal["release"]
    payload_tree_sha256: Sha256
    components: Annotated[tuple[ComponentV1, ...], Field(min_length=1, max_length=32)]
    external_dependencies: ExternalDependenciesV1
    compatibility: CompatibilityV1
    storage_contract: StorageContractV1
    installer_contract: InstallerContractV1
    evidence_policy: EvidencePolicyV1
    build_evidence_refs: Annotated[tuple[OpaqueToken, ...], Field(min_length=1, max_length=32)]
    signing_policy: SigningPolicyV1
    sbom_ref: FileRefV1
    license_inventory_ref: FileRefV1

    @classmethod
    def model_validate(
        cls,
        obj: object,
        *,
        strict: bool | None = None,
        extra: Literal["allow", "ignore", "forbid"] | None = None,
        from_attributes: bool | None = None,
        context: object | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        if isinstance(obj, Mapping):
            raise ReleaseManifestError(ReleaseManifestReason.RAW_INPUT_REQUIRED)
        return super().model_validate(
            obj,
            strict=strict,
            extra=extra,
            from_attributes=from_attributes,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: str) -> str:
        if UTC_RFC3339_RE.fullmatch(value) is None:
            raise _error(ReleaseManifestReason.INVALID_VALUE, "created_at must be second-precision UTC RFC3339")
        try:
            parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        except ValueError as exc:
            raise _error(ReleaseManifestReason.INVALID_VALUE, "created_at is not a real UTC timestamp") from exc
        if parsed.utcoffset() != UTC.utcoffset(parsed):
            raise _error(ReleaseManifestReason.INVALID_VALUE, "created_at must be UTC")
        return value

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        _validate_manifest_ordering(self)
        _validate_component_graph(self)
        _validate_declared_paths(self)
        _validate_component_platforms(self)
        _validate_top_level_file_refs(self)
        _validate_installer_refs(self)
        database_names = tuple(database.logical_name for database in self.compatibility.databases)
        if database_names != self.storage_contract.database_names:
            raise _error(ReleaseManifestReason.INVALID_VALUE, "storage and compatibility database sets differ")
        if self.compatibility.main_sidecar.product_build_id != self.product_build_id:
            raise _error(ReleaseManifestReason.BUILD_ID_MISMATCH, "compatibility build ID does not match manifest")
        if expected_product_build_id(self) != self.product_build_id:
            raise _error(ReleaseManifestReason.BUILD_ID_MISMATCH, "product build ID does not match identity inputs")
        if declared_payload_tree_digest(self) != self.payload_tree_sha256:
            raise _error(ReleaseManifestReason.DIGEST_MISMATCH, "payload tree digest does not match declared files")
        return self


def declared_component_tree_digest(component: ComponentV1) -> str:
    content = "".join(f"{file.sha256}  {file.path}\n" for file in component.files)
    return sha256(content.encode("utf-8")).hexdigest()


def declared_payload_tree_digest(manifest: ReleaseManifestV1) -> str:
    entries = sorted(
        (f"{component.root_path}/{file.path}", file.sha256)
        for component in manifest.components
        for file in component.files
    )
    content = "".join(f"{digest}  {path}\n" for path, digest in entries)
    return sha256(content.encode("utf-8")).hexdigest()


def product_build_identity_bytes(manifest: ReleaseManifestV1) -> bytes:
    identity = {
        "build_recipe_digest": manifest.build_recipe.digest,
        "component_build_identities": [
            {"build_id": component.build_id, "component_id": component.component_id}
            for component in manifest.components
        ],
        "dependency_input_digests": [item.sha256 for item in manifest.dependency_inputs],
        "product_version": manifest.product_version,
        "source_revision": manifest.source_revision,
        "target": {"arch": manifest.target.arch, "os": manifest.target.os},
    }
    return rfc8785.dumps(identity)


def expected_product_build_id(manifest: ReleaseManifestV1) -> str:
    return f"st1-{sha256(product_build_identity_bytes(manifest)).hexdigest()[:32]}"


def canonical_release_manifest_bytes(manifest: ReleaseManifestV1) -> bytes:
    if not isinstance(manifest, ReleaseManifestV1):
        raise ReleaseManifestError(ReleaseManifestReason.SCHEMA_VALIDATION)
    return rfc8785.dumps(manifest.model_dump(mode="json"))


def release_manifest_digest(manifest: ReleaseManifestV1) -> str:
    return sha256(canonical_release_manifest_bytes(manifest)).hexdigest()


def same_manifest_identity_conflict(existing: ReleaseManifestV1, candidate: ReleaseManifestV1) -> bool:
    return existing.manifest_id == candidate.manifest_id and release_manifest_digest(existing) != release_manifest_digest(candidate)


def parse_release_manifest(raw: bytes) -> ReleaseManifestV1:
    if not isinstance(raw, bytes):
        raise ReleaseManifestError(ReleaseManifestReason.RAW_INPUT_REQUIRED)
    _strict_json_loads(raw)
    try:
        return ReleaseManifestV1.model_validate_json(raw, strict=True)
    except ValidationError as exc:
        first = exc.errors(include_url=False, include_context=False)[0]
        error_type = str(first["type"])
        try:
            reason = ReleaseManifestReason(error_type)
        except ValueError:
            reason = (
                ReleaseManifestReason.UNKNOWN_FIELD
                if error_type == "extra_forbidden"
                else ReleaseManifestReason.SCHEMA_VALIDATION
            )
        raise ReleaseManifestError(reason, tuple(first["loc"])) from None


def _strict_json_loads(raw: bytes) -> object:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ReleaseManifestError(ReleaseManifestReason.INVALID_UTF8) from None
    if text.startswith("\ufeff"):
        raise ReleaseManifestError(ReleaseManifestReason.INVALID_UTF8)

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ReleaseManifestError(ReleaseManifestReason.DUPLICATE_KEY, (key,))
            result[key] = value
        return result

    def reject_float(_: str) -> float:
        raise ReleaseManifestError(ReleaseManifestReason.ILLEGAL_NUMBER)

    def reject_constant(_: str) -> float:
        raise ReleaseManifestError(ReleaseManifestReason.ILLEGAL_NUMBER)

    def parse_integer(value: str) -> int:
        if value == "-0":
            raise ReleaseManifestError(ReleaseManifestReason.ILLEGAL_NUMBER)
        parsed = int(value)
        if parsed < -MAX_SAFE_INTEGER or parsed > MAX_SAFE_INTEGER:
            raise ReleaseManifestError(ReleaseManifestReason.ILLEGAL_NUMBER)
        return parsed

    try:
        payload = json.loads(
            text,
            object_pairs_hook=reject_duplicates,
            parse_float=reject_float,
            parse_int=parse_integer,
            parse_constant=reject_constant,
        )
    except ReleaseManifestError:
        raise
    except (json.JSONDecodeError, RecursionError):
        raise ReleaseManifestError(ReleaseManifestReason.INVALID_JSON) from None
    if not isinstance(payload, dict):
        raise ReleaseManifestError(ReleaseManifestReason.ROOT_NOT_OBJECT)
    _validate_raw_json_value(payload)
    return payload


def _validate_raw_json_value(value: object) -> None:
    if isinstance(value, str):
        try:
            _validate_unicode(value)
        except PydanticCustomError:
            raise ReleaseManifestError(ReleaseManifestReason.INVALID_UNICODE) from None
    elif isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ReleaseManifestError(ReleaseManifestReason.INVALID_JSON)
            _validate_raw_json_value(key)
            _validate_raw_json_value(child)
    elif isinstance(value, list):
        for child in value:
            _validate_raw_json_value(child)


def _validate_manifest_ordering(manifest: ReleaseManifestV1) -> None:
    dependency_names = tuple(item.name for item in manifest.dependency_inputs)
    _require_sorted(dependency_names)
    component_ids = tuple(component.component_id for component in manifest.components)
    if component_ids != tuple(sorted(component_ids)):
        raise _error(ReleaseManifestReason.NON_CANONICAL_ORDER, "components must be sorted by component ID")
    if len(component_ids) != len(set(component_ids)):
        raise _error(ReleaseManifestReason.COMPONENT_CONFLICT, "component IDs must be unique")
    _require_sorted(manifest.build_evidence_refs)


def _validate_component_graph(manifest: ReleaseManifestV1) -> None:
    components: dict[str, ComponentV1] = {
        component.component_id: component for component in manifest.components
    }
    if set(components) != REQUIRED_COMPONENT_IDS:
        raise _error(ReleaseManifestReason.COMPONENT_CLOSURE, "manifest must contain the exact required component set")
    for component in manifest.components:
        if component.component_id in component.dependencies:
            raise _error(ReleaseManifestReason.DEPENDENCY_CYCLE, "component cannot depend on itself")
        if set(component.dependencies) - set(components):
            raise _error(ReleaseManifestReason.UNKNOWN_DEPENDENCY, "component has an unknown dependency")
    required_edges = {
        "main_application": {
            "liepin_execution_sidecar",
            "python_runtime",
            "sqlite_runtime",
            "workbench_assets",
        },
        "liepin_execution_sidecar": {
            "browser_bridge",
            "node_runtime",
            "python_runtime",
            "sqlite_runtime",
            "wtscli_runtime",
        },
        "wtscli_runtime": {"browser_bridge", "node_runtime"},
        "installer_updater_support": REQUIRED_COMPONENT_IDS - {"installer_updater_support"},
    }
    for component_id, dependencies in required_edges.items():
        if not dependencies <= set(components[component_id].dependencies):
            raise _error(ReleaseManifestReason.COMPONENT_CLOSURE, "required component dependency is missing")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(component_id: str) -> None:
        if component_id in visiting:
            raise _error(ReleaseManifestReason.DEPENDENCY_CYCLE, "component dependency graph contains a cycle")
        if component_id in visited:
            return
        visiting.add(component_id)
        for dependency in components[component_id].dependencies:
            visit(dependency)
        visiting.remove(component_id)
        visited.add(component_id)

    for component_id in components:
        visit(component_id)


def _collision_key(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


def _validate_declared_paths(manifest: ReleaseManifestV1) -> None:
    seen: dict[str, str] = {}
    root_keys: dict[str, str] = {}
    for component in manifest.components:
        root_key = _collision_key(component.root_path)
        if root_key in root_keys:
            raise _error(ReleaseManifestReason.PATH_COLLISION, "component roots collide")
        root_keys[root_key] = component.root_path
        for file in component.files:
            full_path = f"{component.root_path}/{file.path}"
            if full_path == "release-manifest.json" or full_path.startswith(("signatures/", "attestations/")):
                raise _error(ReleaseManifestReason.INVALID_PATH, "component file occupies a reserved metadata path")
            key = _collision_key(full_path)
            if key in seen:
                raise _error(ReleaseManifestReason.PATH_COLLISION, "declared file paths collide")
            seen[key] = full_path


def _validate_component_platforms(manifest: ReleaseManifestV1) -> None:
    for component in manifest.components:
        if component.platform != "platform_independent" and component.platform != manifest.target:
            raise _error(ReleaseManifestReason.PLATFORM_MISMATCH, "component target differs from manifest target")


def _validate_top_level_file_refs(manifest: ReleaseManifestV1) -> None:
    declared = {
        f"{component.root_path}/{file.path}": file
        for component in manifest.components
        for file in component.files
    }
    for file_ref in (manifest.sbom_ref, manifest.license_inventory_ref):
        matched = declared.get(file_ref.path)
        if matched is None or (
            matched.size_bytes,
            matched.sha256,
            matched.mode_class,
            matched.executable,
        ) != (
            file_ref.size_bytes,
            file_ref.sha256,
            file_ref.mode_class,
            file_ref.executable,
        ):
            raise _error(ReleaseManifestReason.COMPONENT_CLOSURE, "top-level file ref is not in component closure")


def _validate_installer_refs(manifest: ReleaseManifestV1) -> None:
    component = next(
        component for component in manifest.components if component.component_id == "installer_updater_support"
    )
    files = {file.path: file for file in component.files}
    for tool in (
        manifest.installer_contract.installer,
        manifest.installer_contract.updater,
        manifest.installer_contract.uninstaller,
    ):
        if tool.build_id != component.build_id or files.get(tool.file_ref.path) != tool.file_ref:
            raise _error(ReleaseManifestReason.COMPONENT_CLOSURE, "installer tool ref is outside its component closure")
