from __future__ import annotations

from pathlib import Path

import pytest

from seektalent.providers.pi_agent.payload_firewall import (
    LocalPiArtifactRegistry,
    SafePayloadFirewall,
    SafePayloadViolation,
    validate_public_artifact_ref,
)


class FakeArtifactRefRegistry:
    def __init__(self, refs: set[str]) -> None:
        self._refs = refs

    def contains_public_artifact_ref(self, ref: str) -> bool:
        return ref in self._refs

    def resolve_material(self, ref: str) -> bytes:
        if ref not in self._refs:
            raise SafePayloadViolation("artifact ref is not registered")
        return ref.encode("utf-8")


@pytest.mark.parametrize(
    "text",
    [
        "email me at candidate@example.com",
        "phone 13800138000",
        "wechat wx_candidate",
        "Bearer secret-token",
        "document.cookie",
        "<div>raw html</div>",
        "localStorage.getItem('token')",
    ],
)
def test_firewall_rejects_forbidden_free_text(text: str) -> None:
    with pytest.raises(SafePayloadViolation):
        SafePayloadFirewall().assert_safe_text(text)


@pytest.mark.parametrize(
    "ref",
    [
        "file:///etc/passwd",
        "https://attacker.example/x",
        "artifact://protected/../../secret",
        "/tmp/local-file",
        "artifact://unknown/run-1",
    ],
)
def test_artifact_ref_validator_rejects_unsafe_refs(ref: str) -> None:
    with pytest.raises(SafePayloadViolation):
        validate_public_artifact_ref(ref)


def test_artifact_ref_validator_rejects_missing_registry_record() -> None:
    registry = FakeArtifactRefRegistry(set())

    with pytest.raises(SafePayloadViolation):
        validate_public_artifact_ref("artifact://protected/pi-trace/run-1", registry=registry)


def test_artifact_ref_validator_accepts_registered_schemes() -> None:
    registry = FakeArtifactRefRegistry(
        {
            "artifact://protected/pi-trace/run-1",
            "artifact://public-summary/pi-card/run-1/1",
        }
    )

    assert (
        validate_public_artifact_ref("artifact://protected/pi-trace/run-1", registry=registry)
        == "artifact://protected/pi-trace/run-1"
    )
    assert (
        validate_public_artifact_ref("artifact://public-summary/pi-card/run-1/1", registry=registry)
        == "artifact://public-summary/pi-card/run-1/1"
    )


def test_local_pi_artifact_registry_resolves_materialized_refs(tmp_path: Path) -> None:
    registry = LocalPiArtifactRegistry(tmp_path)
    materialized = tmp_path / "pi-agent" / "protected" / "pi-provider-key" / "run-1" / "1"
    materialized.parent.mkdir(parents=True)
    materialized.write_bytes(b"provider-visible-key")

    assert registry.artifact_root_for_pi == tmp_path / "pi-agent"
    assert registry.resolve_material("artifact://protected/pi-provider-key/run-1/1") == b"provider-visible-key"


def test_local_pi_artifact_registry_rejects_string_only_refs(tmp_path: Path) -> None:
    registry = LocalPiArtifactRegistry(tmp_path)

    with pytest.raises(SafePayloadViolation):
        registry.resolve_material("artifact://protected/pi-provider-key/run-1/missing")
