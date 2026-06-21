from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass


class PromptSafetyError(ValueError):
    """Base prompt safety error."""


class UnsafePromptSnapshotError(PromptSafetyError):
    """Raised when a rendered production prompt contains private debug material."""


@dataclass(frozen=True)
class PromptSafetyFinding:
    kind: str
    path: str
    match: str


_UNSAFE_SNAPSHOT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("raw provider payload", re.compile(r"\braw[_ -]?provider[_ -]?payload\b", re.IGNORECASE)),
    ("raw payload", re.compile(r"\braw[_ -]?payload\b", re.IGNORECASE)),
    ("normalized debug store", re.compile(r"\bnormalized[_ -]?store\b", re.IGNORECASE)),
    ("debug store", re.compile(r"\bdebug[_ -]?store\b", re.IGNORECASE)),
    ("prompt body", re.compile(r"\bprompt[_ -]?body\b", re.IGNORECASE)),
    ("OpenAI-style secret", re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")),
    ("bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{6,}", re.IGNORECASE)),
    ("API key assignment", re.compile(r"\b(?:api[_-]?key|token|cookie|authorization)\s*[:=]", re.IGNORECASE)),
    ("local user path", re.compile(r"/Users/[^ \n\t`\"']+")),
    ("tmp path", re.compile(r"/tmp/[^ \n\t`\"']*")),
    ("private tmp path", re.compile(r"/private/(?:tmp|var)/[^ \n\t`\"']*")),
    ("var folders path", re.compile(r"/var/folders/[^ \n\t`\"']*")),
    ("file URI", re.compile(r"\bfile://\S+")),
    ("browser debug endpoint", re.compile(r"\b(?:ws://|wss://)[^ \n\t`\"']*(?:devtools|cdp)[^ \n\t`\"']*", re.IGNORECASE)),
)

_UNSAFE_KEY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("raw provider payload key", re.compile(r"^(?:raw_provider_payload|rawPayload|raw_payload|providerPayload)$")),
    ("raw html key", re.compile(r"^(?:raw_html|raw_resume)$")),
    ("debug store key", re.compile(r"^(?:debug_store|normalized_store)$")),
    ("prompt body key", re.compile(r"^(?:prompt_body|user_prompt_text)$")),
    ("source prompt key", re.compile(r"^source_prompt$")),
    ("secret header key", re.compile(r"^(?:authorization|cookie|set-cookie)$", re.IGNORECASE)),
)


def prompt_template_version(name: str, *, major: int = 1) -> str:
    normalized = "_".join(name.strip().split()).lower()
    if not normalized:
        raise ValueError("prompt template name must not be empty")
    return f"seektalent.prompt.{normalized}.v{major}"


def render_template_version_block(name: str, *, major: int = 1) -> str:
    return f"TEMPLATE VERSION\n{prompt_template_version(name, major=major)}"


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _boundary_for(label: str, text: str) -> str:
    digest = hashlib.sha256(f"{label}\0{text}".encode("utf-8")).hexdigest()[:16].upper()
    return f"SEEKTALENT_UNTRUSTED_{label}_{digest}"


def _normalize_label(label: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_]+", "_", label.strip().upper()).strip("_")
    if not normalized:
        raise ValueError("untrusted data label must not be empty")
    return normalized


def _neutralize_delimiter_breakouts(text: str) -> str:
    return text.replace("</", "<\\/")


def render_untrusted_text_block(label: str, value: object | None) -> str:
    normalized_label = _normalize_label(label)
    text = "" if value is None else str(value)
    boundary = _boundary_for(normalized_label, text)
    if boundary in text:
        raise UnsafePromptSnapshotError(f"untrusted payload for {normalized_label} contains its prompt boundary")
    safe_text = _neutralize_delimiter_breakouts(text)
    return "\n".join(
        [
            f'UNTRUSTED DATA "{normalized_label}"',
            f"BEGIN_{boundary}",
            safe_text,
            f"END_{boundary}",
            f'END UNTRUSTED DATA "{normalized_label}"',
        ]
    )


def render_untrusted_json_block(label: str, payload: object) -> str:
    return render_untrusted_text_block(label, _canonical_json(payload))


def _find_in_text(text: str, *, path: str) -> list[PromptSafetyFinding]:
    findings: list[PromptSafetyFinding] = []
    for name, pattern in _UNSAFE_SNAPSHOT_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            findings.append(PromptSafetyFinding(kind=name, path=path, match=match.group(0)))
    return findings


def find_unsafe_prompt_material(value: object, *, surface: str = "prompt") -> tuple[PromptSafetyFinding, ...]:
    findings: list[PromptSafetyFinding] = []

    def visit(current: object, path: str) -> None:
        if isinstance(current, dict):
            for key, nested in current.items():
                key_text = str(key)
                for name, pattern in _UNSAFE_KEY_PATTERNS:
                    if pattern.search(key_text):
                        findings.append(PromptSafetyFinding(kind=name, path=f"{path}.{key_text}", match=key_text))
                findings.extend(_find_in_text(key_text, path=f"{path}.{key_text}"))
                visit(nested, f"{path}.{key_text}")
            return
        if isinstance(current, (list, tuple)):
            for index, nested in enumerate(current):
                visit(nested, f"{path}[{index}]")
            return
        if isinstance(current, str):
            findings.extend(_find_in_text(current, path=path))

    visit(value, surface)
    return tuple(findings)


def assert_prompt_snapshot_safe(snapshot: object, *, surface: str = "prompt") -> None:
    findings = find_unsafe_prompt_material(snapshot, surface=surface)
    if findings:
        summary = ", ".join(f"{finding.kind} at {finding.path}" for finding in findings[:5])
        raise UnsafePromptSnapshotError(f"prompt snapshot contains unsafe material: {summary}")


def validate_allowed_actions(actions: Iterable[str], *, allowed: set[str] | frozenset[str]) -> None:
    unsupported = sorted({action for action in actions if action not in allowed})
    if unsupported:
        raise ValueError(f"unsupported prompt action: {', '.join(unsupported)}")
