from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

from seektalent_runtime_control.artifact_policy import RuntimeArtifactPolicy, normalize_artifact_output_mode


class RuntimeArtifactLifecycleRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_id: str
    artifact_uri: str | None
    retention_policy: str
    debug_artifacts_available: bool
    delete_eligible: bool
    safety_class: str
    max_bytes: int
    support_bundle_only: bool

    @classmethod
    def from_output_mode(
        cls,
        *,
        artifact_id: str,
        output_mode: object,
    ) -> RuntimeArtifactLifecycleRef:
        return cls.from_policy(
            artifact_id=artifact_id,
            policy=RuntimeArtifactPolicy(normalize_artifact_output_mode(output_mode)),
        )

    @classmethod
    def from_policy(
        cls,
        *,
        artifact_id: str,
        policy: RuntimeArtifactPolicy,
    ) -> RuntimeArtifactLifecycleRef:
        metadata = policy.retention_metadata
        return cls(
            artifact_id=artifact_id,
            artifact_uri=f"artifact://run/{artifact_id}" if policy.writes_local_debug_artifacts else None,
            retention_policy=str(metadata["retention_ttl_class"]),
            debug_artifacts_available=policy.writes_local_debug_artifacts,
            delete_eligible=bool(metadata["delete_eligible"]),
            safety_class=str(metadata["safety_class"]),
            max_bytes=_metadata_int(metadata, "max_bytes"),
            support_bundle_only=bool(metadata["support_bundle_only"]),
        )


def _metadata_int(metadata: Mapping[str, object], key: str) -> int:
    value = metadata[key]
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise TypeError(f"artifact lifecycle metadata {key} must be int, str, or None")
