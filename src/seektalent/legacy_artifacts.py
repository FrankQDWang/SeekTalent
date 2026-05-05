from __future__ import annotations

from pydantic import BaseModel, ConfigDict


LEGACY_PRF_EXACT_REPLAY_KEYS = frozenset(
    {
        "prf_model_backend",
        "prf_familying_version",
        "prf_fallback_reason",
        "prf_candidate_span_artifact_ref",
        "prf_expression_family_artifact_ref",
        "prf_policy_decision_artifact_ref",
        "prf_v1_5_mode",
        "shadow_prf_v1_5_artifact_ref",
    }
)

_LEGACY_PRF_REPLAY_PREFIXES = (
    "prf_span_",
    "prf_embedding_",
    "prf_sidecar_",
)


def is_legacy_prf_replay_key(key: str) -> bool:
    return key in LEGACY_PRF_EXACT_REPLAY_KEYS or key.startswith(_LEGACY_PRF_REPLAY_PREFIXES)


class LegacyPRFReplayMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prf_model_backend: str | None = None
    prf_span_model_name: str | None = None
    prf_span_model_revision: str | None = None
    prf_span_schema_version: str | None = None
    prf_embedding_model_name: str | None = None
    prf_embedding_model_revision: str | None = None
    prf_familying_version: str | None = None
    prf_sidecar_endpoint_contract_version: str | None = None
    prf_sidecar_dependency_manifest_hash: str | None = None
    prf_sidecar_image_digest: str | None = None
    prf_span_tokenizer_revision: str | None = None
    prf_embedding_dimension: int | None = None
    prf_embedding_normalized: bool | None = None
    prf_embedding_dtype: str | None = None
    prf_embedding_pooling: str | None = None
    prf_embedding_truncation: bool | None = None
    prf_fallback_reason: str | None = None
    prf_candidate_span_artifact_ref: str | None = None
    prf_expression_family_artifact_ref: str | None = None
    prf_policy_decision_artifact_ref: str | None = None
    prf_v1_5_mode: str | None = None
    shadow_prf_v1_5_artifact_ref: str | None = None


def split_legacy_prf_replay_metadata(
    payload: dict[str, object],
) -> tuple[dict[str, object], LegacyPRFReplayMetadata]:
    active_payload: dict[str, object] = {}
    legacy_payload: dict[str, object] = {}

    existing_legacy_metadata = payload.get("legacy_prf_replay_metadata")
    if isinstance(existing_legacy_metadata, dict):
        legacy_payload.update(existing_legacy_metadata)

    for key, value in payload.items():
        if is_legacy_prf_replay_key(key):
            legacy_payload[key] = value
        else:
            active_payload[key] = value

    legacy_metadata = LegacyPRFReplayMetadata.model_validate(legacy_payload)
    if legacy_payload:
        active_payload["legacy_prf_replay_metadata"] = legacy_payload
    return active_payload, legacy_metadata
