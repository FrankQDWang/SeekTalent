from __future__ import annotations

import base64
import copy
import inspect
import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

import seektalent.release_signing as release_signing
from seektalent.release_manifest import (
    ReleaseManifestV1,
    canonical_release_manifest_bytes,
    parse_release_manifest,
    release_manifest_digest,
)
from seektalent.release_signing import (
    ReleaseManifestSignatureV1,
    ReleaseManifestTrustKeyV1,
    ReleaseManifestTrustPolicyV1,
    ReleaseSigningError,
    ReleaseSigningReason,
    parse_release_manifest_signature,
    verify_release_manifest_signature,
)
from tests.test_release_manifest import TARGETS, _manifest_payload, _raw as manifest_raw


RFC8032_PRIVATE_SEED = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
RFC8032_PUBLIC_KEY = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
RFC8032_EMPTY_SIGNATURE = bytes.fromhex(
    "e5564300c360ac729086e2cc806e828a"
    "84877f1eb8e5d974d873e06522490155"
    "5fb8821590a33bacc61e39701cf9b46b"
    "d25bf5f0595bbe24655141438e7a100b"
)
SIGNATURE_GOLDENS = {
    ("windows", "x86_64"): "knhg4RtXvhOgN45ENbFL8m6q47oo2kulLBPa8s4Jl1BR9/7HwRk7EVBtl+1jorv4aSyrsaR8fxXfp5LSSSj+AA==",
    ("macos", "x86_64"): "CSwovWHaLPMxKHfAvEdkky1gO3Ryv24O2H6xjHa3gbo94CKCgUM0mtH8wQTEsZkZ1EY8fnPLaOTq6+WuSlciAA==",
    ("macos", "arm64"): "fGrHcaXKoFpyymVqIkyspBJ2Sgsd2FvWPpFxVx1IpfgKbtsQh7GrNAnGCKKDtrxkKYYF/viGqVK9xQLJZMM+Bg==",
}
VALID_FROM = datetime(2026, 7, 18, 0, 0, tzinfo=UTC)
VALID_UNTIL = datetime(2027, 7, 18, 0, 0, tzinfo=UTC)
VERIFICATION_TIME = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)


def _manifest(index: int = 0, *, sort_keys: bool = False, ensure_ascii: bool = False) -> ReleaseManifestV1:
    payload = _manifest_payload(TARGETS[index])
    return parse_release_manifest(manifest_raw(payload, sort_keys=sort_keys, ensure_ascii=ensure_ascii))


def _signature_payload(
    manifest: ReleaseManifestV1,
    signature: bytes,
    **overrides: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "seektalent.release-manifest-signature/v1",
        "manifest_id": manifest.manifest_id,
        "product_build_id": manifest.product_build_id,
        "release_manifest_sha256": release_manifest_digest(manifest),
        "signer_role": "release_manifest_signer",
        "signer_key_id": "rfc8032-test-key-1",
        "algorithm": "ed25519",
        "trust_policy_id": "release-trust-policy-v1",
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    payload.update(overrides)
    return payload


def _signature_raw(payload: object, *, sort_keys: bool = False, ensure_ascii: bool = False) -> bytes:
    return json.dumps(payload, sort_keys=sort_keys, ensure_ascii=ensure_ascii, separators=(",", ":")).encode()


def _signed(manifest: ReleaseManifestV1) -> tuple[ReleaseManifestSignatureV1, dict[str, object]]:
    private_key = Ed25519PrivateKey.from_private_bytes(RFC8032_PRIVATE_SEED)
    signature = private_key.sign(canonical_release_manifest_bytes(manifest))
    payload = _signature_payload(manifest, signature)
    return parse_release_manifest_signature(_signature_raw(payload)), payload


def _trust_key(
    *,
    key_id: str = "rfc8032-test-key-1",
    public_key: bytes = RFC8032_PUBLIC_KEY,
    not_before: datetime = VALID_FROM,
    not_after: datetime = VALID_UNTIL,
) -> ReleaseManifestTrustKeyV1:
    return ReleaseManifestTrustKeyV1(
        key_id=key_id,
        public_key=public_key,
        not_before=not_before,
        not_after=not_after,
    )


def _policy(
    *,
    policy_id: str = "release-trust-policy-v1",
    revision: int = 7,
    allowed_signer_role: str = "release_manifest_signer",
    allowed_algorithm: str = "ed25519",
    keys: tuple[ReleaseManifestTrustKeyV1, ...] | None = None,
    revoked_key_ids: frozenset[str] = frozenset(),
) -> ReleaseManifestTrustPolicyV1:
    return ReleaseManifestTrustPolicyV1(
        policy_id=policy_id,
        revision=revision,
        allowed_signer_role=allowed_signer_role,
        allowed_algorithm=allowed_algorithm,
        keys=keys or (_trust_key(),),
        revoked_key_ids=revoked_key_ids,
    )


def _assert_parse_reason(raw: bytes, reason: ReleaseSigningReason) -> None:
    with pytest.raises(ReleaseSigningError) as raised:
        parse_release_manifest_signature(raw)
    assert raised.value.reason == reason


def _assert_verify_reason(
    signature: ReleaseManifestSignatureV1,
    manifest: ReleaseManifestV1,
    policy: ReleaseManifestTrustPolicyV1,
    reason: ReleaseSigningReason,
    *,
    verification_time: datetime | None = VERIFICATION_TIME,
) -> None:
    with pytest.raises(ReleaseSigningError) as raised:
        verify_release_manifest_signature(signature, manifest, policy, verification_time)
    assert raised.value.reason == reason


def test_rfc8032_known_answer_vector() -> None:
    Ed25519PublicKey.from_public_bytes(RFC8032_PUBLIC_KEY).verify(RFC8032_EMPTY_SIGNATURE, b"")
    assert (
        Ed25519PrivateKey.from_private_bytes(RFC8032_PRIVATE_SEED).public_key().public_bytes_raw()
        == RFC8032_PUBLIC_KEY
    )


@pytest.mark.parametrize(("index", "target"), enumerate(TARGETS))
def test_three_platform_manifest_signatures_match_exact_canonical_byte_goldens(
    index: int,
    target: dict[str, str],
) -> None:
    manifest = _manifest(index)
    signature, _ = _signed(manifest)

    assert signature.signature == SIGNATURE_GOLDENS[(target["os"], target["arch"])]
    assert verify_release_manifest_signature(signature, manifest, _policy(), VERIFICATION_TIME) == (
        release_signing.VerifiedReleaseManifestSignatureV1(
            release_manifest_sha256=release_manifest_digest(manifest),
            signer_key_id="rfc8032-test-key-1",
            trust_policy_id="release-trust-policy-v1",
            trust_policy_revision=7,
        )
    )


def test_raw_manifest_order_and_unicode_escape_do_not_change_verification() -> None:
    direct = _manifest(1, ensure_ascii=False)
    escaped_and_sorted = _manifest(1, sort_keys=True, ensure_ascii=True)
    signature, _ = _signed(direct)

    assert canonical_release_manifest_bytes(direct) == canonical_release_manifest_bytes(escaped_and_sorted)
    assert verify_release_manifest_signature(signature, direct, _policy(), VERIFICATION_TIME) == (
        verify_release_manifest_signature(signature, escaped_and_sorted, _policy(), VERIFICATION_TIME)
    )


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"manifest_id": "manifest-other"}, ReleaseSigningReason.MANIFEST_IDENTITY_MISMATCH),
        ({"product_build_id": "st1-" + "f" * 32}, ReleaseSigningReason.MANIFEST_IDENTITY_MISMATCH),
        ({"release_manifest_sha256": "f" * 64}, ReleaseSigningReason.MANIFEST_DIGEST_MISMATCH),
        ({"trust_policy_id": "other-policy"}, ReleaseSigningReason.TRUST_POLICY_MISMATCH),
        ({"signer_key_id": "unknown-key"}, ReleaseSigningReason.UNKNOWN_KEY),
    ],
)
def test_signature_metadata_is_bound_to_manifest_and_caller_policy(
    overrides: dict[str, object],
    reason: ReleaseSigningReason,
) -> None:
    manifest = _manifest()
    _, payload = _signed(manifest)
    payload.update(overrides)
    signature = parse_release_manifest_signature(_signature_raw(payload))
    _assert_verify_reason(signature, manifest, _policy(), reason)


def test_any_manifest_change_is_rejected_before_or_during_signature_verification() -> None:
    original = _manifest()
    signature, payload = _signed(original)
    changed_payload = _manifest_payload(TARGETS[0])
    changed_payload["created_at"] = "2026-07-18T12:00:01Z"
    changed = parse_release_manifest(manifest_raw(changed_payload))

    _assert_verify_reason(signature, changed, _policy(), ReleaseSigningReason.MANIFEST_DIGEST_MISMATCH)

    payload["release_manifest_sha256"] = release_manifest_digest(changed)
    rebound_metadata = parse_release_manifest_signature(_signature_raw(payload))
    _assert_verify_reason(rebound_metadata, changed, _policy(), ReleaseSigningReason.INVALID_SIGNATURE)


def test_signature_bit_flip_and_wrong_public_key_are_rejected() -> None:
    manifest = _manifest()
    _, payload = _signed(manifest)
    raw_signature = bytearray(base64.b64decode(str(payload["signature"])))
    raw_signature[0] ^= 1
    payload["signature"] = base64.b64encode(raw_signature).decode("ascii")
    bit_flipped = parse_release_manifest_signature(_signature_raw(payload))
    _assert_verify_reason(bit_flipped, manifest, _policy(), ReleaseSigningReason.INVALID_SIGNATURE)

    signature, _ = _signed(manifest)
    wrong_public_key = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    wrong_policy = _policy(keys=(_trust_key(public_key=wrong_public_key),))
    _assert_verify_reason(signature, manifest, wrong_policy, ReleaseSigningReason.INVALID_SIGNATURE)


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (b'{"schema_version":"a","schema_version":"b"}', ReleaseSigningReason.DUPLICATE_KEY),
        (b'{"value":1.0}', ReleaseSigningReason.ILLEGAL_NUMBER),
        (b'{"value":NaN}', ReleaseSigningReason.ILLEGAL_NUMBER),
        (b'{"value":-0}', ReleaseSigningReason.ILLEGAL_NUMBER),
        (b'{"value":"\\ud800"}', ReleaseSigningReason.INVALID_UNICODE),
        (b"[]", ReleaseSigningReason.ROOT_NOT_OBJECT),
        (b"{", ReleaseSigningReason.INVALID_JSON),
        (b"\xff", ReleaseSigningReason.INVALID_UTF8),
    ],
)
def test_signature_parser_reuses_duplicate_aware_strict_json_boundary(
    raw: bytes,
    reason: ReleaseSigningReason,
) -> None:
    _assert_parse_reason(raw, reason)


def test_signature_parser_maps_excessive_json_nesting_to_stable_failure() -> None:
    nested = b'{"unknown":' + b'{"value":' * 1_200 + b"null" + b"}" * 1_200 + b"}"

    _assert_parse_reason(nested, ReleaseSigningReason.INVALID_JSON)


def test_signature_parser_rejects_unknown_fields_and_unsupported_contract_values() -> None:
    manifest = _manifest()
    _, payload = _signed(manifest)
    unknown = {**payload, "unknown": True}
    _assert_parse_reason(_signature_raw(unknown), ReleaseSigningReason.UNKNOWN_FIELD)

    wrong_role = {**payload, "signer_role": "artifact_attestor"}
    _assert_parse_reason(_signature_raw(wrong_role), ReleaseSigningReason.SIGNER_ROLE_MISMATCH)

    unsupported = {**payload, "algorithm": "rsa-pss"}
    _assert_parse_reason(_signature_raw(unsupported), ReleaseSigningReason.UNSUPPORTED_ALGORITHM)


@pytest.mark.parametrize(
    "encoded",
    [
        "not-base64!",
        base64.b64encode(b"x" * 63).decode("ascii"),
        base64.b64encode(b"x" * 65).decode("ascii"),
        base64.b64encode(b"x" * 64).decode("ascii").rstrip("="),
        base64.b64encode(b"x" * 64).decode("ascii") + "=",
    ],
)
def test_signature_parser_rejects_invalid_base64_padding_and_length(encoded: str) -> None:
    manifest = _manifest()
    _, payload = _signed(manifest)
    payload["signature"] = encoded
    _assert_parse_reason(_signature_raw(payload), ReleaseSigningReason.INVALID_BASE64)


@pytest.mark.parametrize("raw", ["{}", bytearray(b"{}"), {}])
def test_signature_parser_rejects_non_bytes_and_dict_bypasses(raw: object) -> None:
    with pytest.raises(ReleaseSigningError) as raised:
        parse_release_manifest_signature(raw)  # type: ignore[arg-type]
    assert raised.value.reason == ReleaseSigningReason.RAW_INPUT_REQUIRED

    with pytest.raises(ReleaseSigningError) as model_raised:
        ReleaseManifestSignatureV1.model_validate(raw)
    assert model_raised.value.reason == ReleaseSigningReason.RAW_INPUT_REQUIRED


def test_direct_model_json_validation_cannot_be_weakened() -> None:
    manifest = _manifest()
    _, payload = _signed(manifest)
    raw = _signature_raw(payload)
    duplicate = b'{"schema_version":"invalid",' + raw[1:]

    with pytest.raises(ReleaseSigningError) as duplicate_raised:
        ReleaseManifestSignatureV1.model_validate_json(duplicate, strict=False, extra="allow")
    assert duplicate_raised.value.reason == ReleaseSigningReason.DUPLICATE_KEY

    unknown = _signature_raw({**payload, "unknown": True})
    with pytest.raises(ReleaseSigningError) as unknown_raised:
        ReleaseManifestSignatureV1.model_validate_json(unknown, strict=False, extra="allow")
    assert unknown_raised.value.reason == ReleaseSigningReason.UNKNOWN_FIELD


@pytest.mark.parametrize("length", [31, 33])
def test_trust_key_requires_exact_32_byte_public_key(length: int) -> None:
    with pytest.raises(ReleaseSigningError) as raised:
        _trust_key(public_key=b"x" * length)
    assert raised.value.reason == ReleaseSigningReason.INVALID_PUBLIC_KEY


def test_policy_authority_is_explicit_and_cannot_be_loaded_or_relaxed_by_artifact_ids() -> None:
    manifest = _manifest()
    signature, _ = _signed(manifest)

    _assert_verify_reason(
        signature,
        manifest,
        _policy(policy_id="local-policy-v2"),
        ReleaseSigningReason.TRUST_POLICY_MISMATCH,
    )
    _assert_verify_reason(
        signature,
        manifest,
        _policy(allowed_signer_role="artifact_attestor"),
        ReleaseSigningReason.SIGNER_ROLE_MISMATCH,
    )
    _assert_verify_reason(
        signature,
        manifest,
        _policy(allowed_algorithm="rsa-pss"),
        ReleaseSigningReason.UNSUPPORTED_ALGORITHM,
    )

    with pytest.raises(ReleaseSigningError) as revision_raised:
        _policy(revision=0)
    assert revision_raised.value.reason == ReleaseSigningReason.INVALID_TRUST_POLICY


def test_unknown_and_revoked_keys_have_distinct_fail_closed_reasons() -> None:
    manifest = _manifest()
    signature, _ = _signed(manifest)
    unknown_policy = _policy(keys=(_trust_key(key_id="other-key"),))
    _assert_verify_reason(signature, manifest, unknown_policy, ReleaseSigningReason.UNKNOWN_KEY)

    revoked_policy = _policy(revoked_key_ids=frozenset({"rfc8032-test-key-1"}))
    _assert_verify_reason(signature, manifest, revoked_policy, ReleaseSigningReason.REVOKED_KEY)


@pytest.mark.parametrize("verification_time", [VALID_FROM, VALID_UNTIL])
def test_key_validity_window_boundaries_are_inclusive(verification_time: datetime) -> None:
    manifest = _manifest()
    signature, _ = _signed(manifest)
    verify_release_manifest_signature(signature, manifest, _policy(), verification_time)


@pytest.mark.parametrize(
    ("verification_time", "reason"),
    [
        (None, ReleaseSigningReason.TIME_UNTRUSTED),
        (datetime(2026, 7, 18, 12, 0), ReleaseSigningReason.TIME_UNTRUSTED),
        (
            datetime(2026, 7, 18, 20, 0, tzinfo=timezone(timedelta(hours=8))),
            ReleaseSigningReason.TIME_UNTRUSTED,
        ),
        (VALID_FROM - timedelta(microseconds=1), ReleaseSigningReason.KEY_NOT_YET_VALID),
        (VALID_UNTIL + timedelta(microseconds=1), ReleaseSigningReason.KEY_EXPIRED),
    ],
)
def test_verification_time_is_explicit_aware_utc_and_enforces_key_window(
    verification_time: datetime | None,
    reason: ReleaseSigningReason,
) -> None:
    manifest = _manifest()
    signature, _ = _signed(manifest)
    _assert_verify_reason(signature, manifest, _policy(), reason, verification_time=verification_time)


def test_invalid_signature_exception_is_mapped_without_public_exception_text(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = _manifest()
    signature, _ = _signed(manifest)

    class RejectingPublicKey:
        def verify(self, signature_bytes: bytes, data: bytes) -> None:
            raise InvalidSignature("backend-specific message")

    monkeypatch.setattr(
        release_signing.Ed25519PublicKey,
        "from_public_bytes",
        lambda _: RejectingPublicKey(),
    )
    with pytest.raises(ReleaseSigningError) as raised:
        verify_release_manifest_signature(signature, manifest, _policy(), VERIFICATION_TIME)
    assert raised.value.reason == ReleaseSigningReason.INVALID_SIGNATURE
    assert str(raised.value) == "invalid_signature"


def test_verification_is_deterministic_and_product_source_has_no_private_key_api() -> None:
    manifest = _manifest()
    signature, _ = _signed(manifest)
    policy = _policy()

    first = verify_release_manifest_signature(signature, manifest, policy, VERIFICATION_TIME)
    second = verify_release_manifest_signature(signature, manifest, policy, VERIFICATION_TIME)
    assert first == second

    product_source = inspect.getsource(release_signing)
    assert "Ed25519PrivateKey" not in product_source
    assert "datetime.now" not in product_source
    assert "urlopen" not in product_source
    assert "requests" not in product_source


def test_signature_model_is_frozen_and_policy_inputs_are_immutable() -> None:
    manifest = _manifest()
    signature, _ = _signed(manifest)
    policy = _policy()

    with pytest.raises(Exception):
        signature.signature = "changed"  # type: ignore[misc]
    assert isinstance(policy.keys, tuple)
    assert isinstance(policy.revoked_key_ids, frozenset)


def test_verifier_rejects_untyped_signature_manifest_and_policy_inputs() -> None:
    manifest = _manifest()
    signature, _ = _signed(manifest)
    policy = _policy()

    for untyped_signature in ({}, copy.deepcopy(signature.model_dump())):
        with pytest.raises(ReleaseSigningError) as raised:
            verify_release_manifest_signature(untyped_signature, manifest, policy, VERIFICATION_TIME)  # type: ignore[arg-type]
        assert raised.value.reason == ReleaseSigningReason.RAW_INPUT_REQUIRED

    with pytest.raises(ReleaseSigningError) as manifest_raised:
        verify_release_manifest_signature(signature, manifest.model_dump(), policy, VERIFICATION_TIME)  # type: ignore[arg-type]
    assert manifest_raised.value.reason == ReleaseSigningReason.RAW_INPUT_REQUIRED

    with pytest.raises(ReleaseSigningError) as policy_raised:
        verify_release_manifest_signature(signature, manifest, {}, VERIFICATION_TIME)  # type: ignore[arg-type]
    assert policy_raised.value.reason == ReleaseSigningReason.INVALID_TRUST_POLICY
