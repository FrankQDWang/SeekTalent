from __future__ import annotations

from hashlib import sha256

from seektalent.providers.pi_agent.contracts import PiArtifactRef, ProtectedArtifactClass


def make_artifact_ref(content: bytes, artifact_class: ProtectedArtifactClass, policy_id: str) -> PiArtifactRef:
    content_hash = sha256(content).hexdigest()
    return PiArtifactRef(
        artifact_class=artifact_class,
        artifact_ref=f"{artifact_class.value}:{content_hash}",
        content_sha256=content_hash,
        redaction_policy_id=policy_id
        if artifact_class in {ProtectedArtifactClass.SAFE_SUMMARY, ProtectedArtifactClass.REDACTED_EVIDENCE}
        else None,
        protection_policy_id=policy_id if artifact_class == ProtectedArtifactClass.PROTECTED_PROVIDER_SNAPSHOT else None,
    )


def assert_ui_safe_artifact(ref: PiArtifactRef) -> None:
    if ref.artifact_class != ProtectedArtifactClass.SAFE_SUMMARY:
        raise PermissionError("ordinary UI may only access safe_summary_artifact refs")
