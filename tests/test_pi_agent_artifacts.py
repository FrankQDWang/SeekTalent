import pytest

from seektalent.providers.pi_agent.artifacts import assert_ui_safe_artifact, make_artifact_ref
from seektalent.providers.pi_agent.contracts import ProtectedArtifactClass


def test_make_artifact_ref_requires_policy_matching_artifact_class() -> None:
    safe_ref = make_artifact_ref(
        b"safe candidate summary",
        ProtectedArtifactClass.SAFE_SUMMARY,
        "liepin-summary-redaction-v1",
    )
    assert safe_ref.redaction_policy_id == "liepin-summary-redaction-v1"
    assert safe_ref.protection_policy_id is None

    protected_ref = make_artifact_ref(
        b"raw provider snapshot",
        ProtectedArtifactClass.PROTECTED_PROVIDER_SNAPSHOT,
        "liepin-protected-snapshot-v1",
    )
    assert protected_ref.protection_policy_id == "liepin-protected-snapshot-v1"
    assert protected_ref.redaction_policy_id is None


def test_ui_safe_artifact_rejects_protected_snapshot_and_redacted_evidence() -> None:
    protected_ref = make_artifact_ref(
        b"raw provider snapshot",
        ProtectedArtifactClass.PROTECTED_PROVIDER_SNAPSHOT,
        "liepin-protected-snapshot-v1",
    )
    redacted_ref = make_artifact_ref(
        b"redacted audit evidence",
        ProtectedArtifactClass.REDACTED_EVIDENCE,
        "liepin-evidence-redaction-v1",
    )

    with pytest.raises(PermissionError):
        assert_ui_safe_artifact(protected_ref)

    with pytest.raises(PermissionError):
        assert_ui_safe_artifact(redacted_ref)


def test_ui_safe_artifact_accepts_safe_summary_only() -> None:
    safe_ref = make_artifact_ref(
        b"safe candidate summary",
        ProtectedArtifactClass.SAFE_SUMMARY,
        "liepin-summary-redaction-v1",
    )

    assert_ui_safe_artifact(safe_ref)
