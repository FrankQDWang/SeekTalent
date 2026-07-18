from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal, LiteralString, Self, TypeVar

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pydantic_core import PydanticCustomError

from seektalent.release_manifest import (
    ProductBuildId,
    ReleaseManifestV1,
    Sha256,
    canonical_release_manifest_bytes,
    release_manifest_digest,
)
from seektalent.strict_json import StrictJsonError, strict_json_object_loads


IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/+\-=]{0,127}\Z")


class ReleaseSigningReason(StrEnum):
    RAW_INPUT_REQUIRED = "raw_input_required"
    INVALID_UTF8 = "invalid_utf8"
    INVALID_JSON = "invalid_json"
    DUPLICATE_KEY = "duplicate_key"
    ILLEGAL_NUMBER = "illegal_number"
    INVALID_UNICODE = "invalid_unicode"
    ROOT_NOT_OBJECT = "root_not_object"
    UNKNOWN_FIELD = "unknown_field"
    SCHEMA_VALIDATION = "schema_validation"
    INVALID_BASE64 = "invalid_base64"
    UNSUPPORTED_ALGORITHM = "unsupported_algorithm"
    MANIFEST_IDENTITY_MISMATCH = "manifest_identity_mismatch"
    MANIFEST_DIGEST_MISMATCH = "manifest_digest_mismatch"
    TRUST_POLICY_MISMATCH = "trust_policy_mismatch"
    SIGNER_ROLE_MISMATCH = "signer_role_mismatch"
    UNKNOWN_KEY = "unknown_key"
    REVOKED_KEY = "revoked_key"
    TIME_UNTRUSTED = "time_untrusted"
    KEY_NOT_YET_VALID = "key_not_yet_valid"
    KEY_EXPIRED = "key_expired"
    INVALID_PUBLIC_KEY = "invalid_public_key"
    INVALID_TRUST_POLICY = "invalid_trust_policy"
    INVALID_SIGNATURE = "invalid_signature"


class ReleaseSigningError(ValueError):
    def __init__(self, reason: ReleaseSigningReason, location: tuple[str | int, ...] = ()) -> None:
        self.reason = reason
        self.location = location
        super().__init__(reason.value)


def _schema_error(reason: ReleaseSigningReason, message: LiteralString) -> PydanticCustomError:
    return PydanticCustomError(reason.value, message)


def _validate_identifier(value: str, *, reason: ReleaseSigningReason) -> None:
    if IDENTIFIER_RE.fullmatch(value) is None:
        raise ReleaseSigningError(reason)


class ReleaseManifestSignatureV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["seektalent.release-manifest-signature/v1"]
    manifest_id: Annotated[str, Field(min_length=1, max_length=96)]
    product_build_id: ProductBuildId
    release_manifest_sha256: Sha256
    signer_role: Annotated[str, Field(min_length=1, max_length=128)]
    signer_key_id: Annotated[str, Field(min_length=1, max_length=128)]
    algorithm: Annotated[str, Field(min_length=1, max_length=32)]
    trust_policy_id: Annotated[str, Field(min_length=1, max_length=128)]
    signature: Annotated[str, Field(max_length=256)]

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
        if not isinstance(obj, cls):
            raise ReleaseSigningError(ReleaseSigningReason.RAW_INPUT_REQUIRED)
        return BaseModel.model_validate.__func__(
            cls,
            obj,
            strict=strict,
            extra=extra,
            from_attributes=from_attributes,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )

    @classmethod
    def model_validate_json(
        cls,
        json_data: str | bytes | bytearray,
        *,
        strict: bool | None = None,
        extra: Literal["allow", "ignore", "forbid"] | None = None,
        context: object | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        if not isinstance(json_data, bytes):
            raise ReleaseSigningError(ReleaseSigningReason.RAW_INPUT_REQUIRED)
        return _parse_signature_bytes(
            cls,
            json_data,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )

    @field_validator("manifest_id", "signer_key_id", "trust_policy_id")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        if IDENTIFIER_RE.fullmatch(value) is None:
            raise _schema_error(ReleaseSigningReason.SCHEMA_VALIDATION, "identifier has an invalid format")
        return value

    @field_validator("signer_role")
    @classmethod
    def validate_signer_role(cls, value: str) -> str:
        if value != "release_manifest_signer":
            raise _schema_error(ReleaseSigningReason.SIGNER_ROLE_MISMATCH, "unsupported signer role")
        return value

    @field_validator("algorithm")
    @classmethod
    def validate_algorithm(cls, value: str) -> str:
        if value != "ed25519":
            raise _schema_error(ReleaseSigningReason.UNSUPPORTED_ALGORITHM, "unsupported signature algorithm")
        return value

    @field_validator("signature")
    @classmethod
    def validate_signature(cls, value: str) -> str:
        try:
            decoded = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError):
            raise _schema_error(ReleaseSigningReason.INVALID_BASE64, "signature must be canonical base64") from None
        if len(decoded) != 64 or base64.b64encode(decoded).decode("ascii") != value:
            raise _schema_error(ReleaseSigningReason.INVALID_BASE64, "signature must encode exactly 64 bytes")
        return value

    def signature_bytes(self) -> bytes:
        return base64.b64decode(self.signature, validate=True)


SignatureModel = TypeVar("SignatureModel", bound=ReleaseManifestSignatureV1)


def parse_release_manifest_signature(raw: bytes) -> ReleaseManifestSignatureV1:
    if not isinstance(raw, bytes):
        raise ReleaseSigningError(ReleaseSigningReason.RAW_INPUT_REQUIRED)
    return _parse_signature_bytes(ReleaseManifestSignatureV1, raw)


def _parse_signature_bytes(
    model_cls: type[SignatureModel],
    raw: bytes,
    *,
    context: object | None = None,
    by_alias: bool | None = None,
    by_name: bool | None = None,
) -> SignatureModel:
    try:
        strict_json_object_loads(raw)
    except StrictJsonError as exc:
        raise ReleaseSigningError(ReleaseSigningReason(exc.reason.value), exc.location) from None
    try:
        return BaseModel.model_validate_json.__func__(
            model_cls,
            raw,
            strict=True,
            extra="forbid",
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )
    except ValidationError as exc:
        first = exc.errors(include_url=False, include_context=False)[0]
        error_type = str(first["type"])
        try:
            reason = ReleaseSigningReason(error_type)
        except ValueError:
            reason = (
                ReleaseSigningReason.UNKNOWN_FIELD
                if error_type == "extra_forbidden"
                else ReleaseSigningReason.SCHEMA_VALIDATION
            )
        raise ReleaseSigningError(reason, tuple(first["loc"])) from None


def _is_utc(value: object) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() == timedelta(0)


@dataclass(frozen=True)
class ReleaseManifestTrustKeyV1:
    key_id: str
    public_key: bytes
    not_before: datetime
    not_after: datetime

    def __post_init__(self) -> None:
        _validate_identifier(self.key_id, reason=ReleaseSigningReason.INVALID_TRUST_POLICY)
        if type(self.public_key) is not bytes or len(self.public_key) != 32:
            raise ReleaseSigningError(ReleaseSigningReason.INVALID_PUBLIC_KEY)
        if not _is_utc(self.not_before) or not _is_utc(self.not_after) or self.not_before >= self.not_after:
            raise ReleaseSigningError(ReleaseSigningReason.INVALID_TRUST_POLICY)


@dataclass(frozen=True)
class ReleaseManifestTrustPolicyV1:
    policy_id: str
    revision: int
    allowed_signer_role: str
    allowed_algorithm: str
    keys: tuple[ReleaseManifestTrustKeyV1, ...]
    revoked_key_ids: frozenset[str]

    def __post_init__(self) -> None:
        _validate_identifier(self.policy_id, reason=ReleaseSigningReason.INVALID_TRUST_POLICY)
        if type(self.revision) is not int or self.revision < 1:
            raise ReleaseSigningError(ReleaseSigningReason.INVALID_TRUST_POLICY)
        _validate_identifier(self.allowed_signer_role, reason=ReleaseSigningReason.INVALID_TRUST_POLICY)
        _validate_identifier(self.allowed_algorithm, reason=ReleaseSigningReason.INVALID_TRUST_POLICY)
        if type(self.keys) is not tuple or not self.keys or any(
            not isinstance(key, ReleaseManifestTrustKeyV1) for key in self.keys
        ):
            raise ReleaseSigningError(ReleaseSigningReason.INVALID_TRUST_POLICY)
        key_ids = tuple(key.key_id for key in self.keys)
        if len(key_ids) != len(set(key_ids)):
            raise ReleaseSigningError(ReleaseSigningReason.INVALID_TRUST_POLICY)
        if type(self.revoked_key_ids) is not frozenset or any(
            not isinstance(key_id, str) or IDENTIFIER_RE.fullmatch(key_id) is None
            for key_id in self.revoked_key_ids
        ):
            raise ReleaseSigningError(ReleaseSigningReason.INVALID_TRUST_POLICY)


@dataclass(frozen=True)
class VerifiedReleaseManifestSignatureV1:
    release_manifest_sha256: str
    signer_key_id: str
    trust_policy_id: str
    trust_policy_revision: int


def verify_release_manifest_signature(
    signature: ReleaseManifestSignatureV1,
    manifest: ReleaseManifestV1,
    trust_policy: ReleaseManifestTrustPolicyV1,
    verification_time: datetime | None = None,
) -> VerifiedReleaseManifestSignatureV1:
    if not isinstance(signature, ReleaseManifestSignatureV1) or not isinstance(manifest, ReleaseManifestV1):
        raise ReleaseSigningError(ReleaseSigningReason.RAW_INPUT_REQUIRED)
    if not isinstance(trust_policy, ReleaseManifestTrustPolicyV1):
        raise ReleaseSigningError(ReleaseSigningReason.INVALID_TRUST_POLICY)

    if signature.manifest_id != manifest.manifest_id or signature.product_build_id != manifest.product_build_id:
        raise ReleaseSigningError(ReleaseSigningReason.MANIFEST_IDENTITY_MISMATCH)
    manifest_digest = release_manifest_digest(manifest)
    if signature.release_manifest_sha256 != manifest_digest:
        raise ReleaseSigningError(ReleaseSigningReason.MANIFEST_DIGEST_MISMATCH)
    if signature.trust_policy_id != trust_policy.policy_id:
        raise ReleaseSigningError(ReleaseSigningReason.TRUST_POLICY_MISMATCH)
    if signature.signer_role != trust_policy.allowed_signer_role:
        raise ReleaseSigningError(ReleaseSigningReason.SIGNER_ROLE_MISMATCH)
    if signature.algorithm != trust_policy.allowed_algorithm:
        raise ReleaseSigningError(ReleaseSigningReason.UNSUPPORTED_ALGORITHM)
    if signature.signer_key_id in trust_policy.revoked_key_ids:
        raise ReleaseSigningError(ReleaseSigningReason.REVOKED_KEY)

    key = next((item for item in trust_policy.keys if item.key_id == signature.signer_key_id), None)
    if key is None:
        raise ReleaseSigningError(ReleaseSigningReason.UNKNOWN_KEY)
    if not _is_utc(verification_time):
        raise ReleaseSigningError(ReleaseSigningReason.TIME_UNTRUSTED)
    assert verification_time is not None
    if verification_time < key.not_before:
        raise ReleaseSigningError(ReleaseSigningReason.KEY_NOT_YET_VALID)
    if verification_time > key.not_after:
        raise ReleaseSigningError(ReleaseSigningReason.KEY_EXPIRED)

    try:
        public_key = Ed25519PublicKey.from_public_bytes(key.public_key)
    except ValueError:
        raise ReleaseSigningError(ReleaseSigningReason.INVALID_PUBLIC_KEY) from None
    try:
        public_key.verify(signature.signature_bytes(), canonical_release_manifest_bytes(manifest))
    except InvalidSignature:
        raise ReleaseSigningError(ReleaseSigningReason.INVALID_SIGNATURE) from None

    return VerifiedReleaseManifestSignatureV1(
        release_manifest_sha256=manifest_digest,
        signer_key_id=key.key_id,
        trust_policy_id=trust_policy.policy_id,
        trust_policy_revision=trust_policy.revision,
    )
