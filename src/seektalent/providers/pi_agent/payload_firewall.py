from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol


class SafePayloadViolation(ValueError):
    pass


class ArtifactRefRegistry(Protocol):
    def contains_public_artifact_ref(self, ref: str) -> bool: ...

    def resolve_material(self, ref: str) -> bytes: ...


FORBIDDEN_TEXT_PATTERNS = (
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"(?:\+?86[-\s]?)?1[3-9]\d{9}\b"),
    re.compile(r"\b(?:wechat|weixin|wx)[-_:\s]?[A-Za-z0-9_]{4,}\b", re.IGNORECASE),
    re.compile(r"\bbearer\s+[A-Za-z0-9._-]+", re.IGNORECASE),
    re.compile(r"\bcookie\b|\bsession=", re.IGNORECASE),
    re.compile(r"\blocalStorage\b|\bsessionStorage\b", re.IGNORECASE),
    re.compile(r"<[^>]+>"),
)

ALLOWED_ARTIFACT_PREFIXES = (
    "artifact://protected/",
    "artifact://public-summary/",
)


class LocalPiArtifactRegistry:
    def __init__(self, artifacts_root: Path) -> None:
        self._root = artifacts_root

    @property
    def artifact_root_for_pi(self) -> Path:
        return self._root / "pi-agent"

    def contains_public_artifact_ref(self, ref: str) -> bool:
        try:
            return self._path_for(ref).is_file()
        except SafePayloadViolation:
            return False

    def resolve_material(self, ref: str) -> bytes:
        path = self._path_for(ref)
        if not path.is_file():
            raise SafePayloadViolation("artifact ref is not registered")
        return path.read_bytes()

    def _path_for(self, ref: str) -> Path:
        validate_public_artifact_ref(ref)
        scope, _, relative = ref.removeprefix("artifact://").partition("/")
        if scope not in {"protected", "public-summary"} or not relative:
            raise SafePayloadViolation("unsupported artifact ref scope")
        path = (self.artifact_root_for_pi / scope / relative).resolve()
        root = (self.artifact_root_for_pi / scope).resolve()
        if root not in path.parents and path != root:
            raise SafePayloadViolation("artifact ref escapes artifact root")
        return path


class SafePayloadFirewall:
    def assert_safe_text(self, text: str | None) -> None:
        if text is None:
            return
        for pattern in FORBIDDEN_TEXT_PATTERNS:
            if pattern.search(text):
                raise SafePayloadViolation("forbidden external executor text")
        if len(text) > 2000:
            raise SafePayloadViolation("external executor text exceeds public safety limit")

    def assert_safe_mapping(self, payload: object) -> None:
        if isinstance(payload, str):
            if payload.startswith(ALLOWED_ARTIFACT_PREFIXES):
                validate_public_artifact_ref(payload)
            else:
                self.assert_safe_text(payload)
            return
        if isinstance(payload, dict):
            for value in payload.values():
                self.assert_safe_mapping(value)
            return
        if isinstance(payload, list | tuple):
            for value in payload:
                self.assert_safe_mapping(value)


def validate_public_artifact_ref(
    ref: str | None,
    *,
    registry: ArtifactRefRegistry | None = None,
) -> str | None:
    if ref is None:
        return None
    if not ref.startswith(ALLOWED_ARTIFACT_PREFIXES):
        raise SafePayloadViolation("unsupported artifact ref scheme")
    if ".." in ref.split("/"):
        raise SafePayloadViolation("artifact ref contains parent path")
    if not re.fullmatch(r"artifact://[A-Za-z0-9._/-]+", ref):
        raise SafePayloadViolation("artifact ref contains invalid characters")
    if registry is not None and not registry.contains_public_artifact_ref(ref):
        raise SafePayloadViolation("artifact ref is not registered")
    return ref
