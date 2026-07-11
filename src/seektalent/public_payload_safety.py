from __future__ import annotations

import re


_PUBLIC_SOURCE_ALIAS = "internal_referrals"
_UNSAFE_ASSIGNMENT = re.compile(r"(?:secret|api(?:[_-]|\s*)key|token|cookie|password)\s*[:=]", re.IGNORECASE)
_UNSAFE_DIAGNOSTIC_TOKEN = re.compile(r"(?<![A-Za-z0-9])(?:opencli|cdp)(?![A-Za-z0-9])", re.IGNORECASE)
_UNSAFE_BEARER_TOKEN = re.compile(r"(?<![A-Za-z0-9])bearer(?![A-Za-z0-9])", re.IGNORECASE)
_UNSAFE_AUTHORIZATION = re.compile(r"authorization\s*[:=]", re.IGNORECASE)


def public_text(value: object, *, max_length: int) -> str | None:
    if not isinstance(value, str) or max_length <= 0:
        return None
    text = value.strip()
    if not text or _looks_unsafe(text):
        return None
    return text[:max_length]


def public_source_identifier(value: object, *, max_length: int = 80) -> str | None:
    text = public_text(value, max_length=max_length)
    if isinstance(value, str) and len(value.strip()) > max_length:
        return None
    if text == _PUBLIC_SOURCE_ALIAS:
        return text
    if text is None and isinstance(value, str):
        alias = value.strip()
        if alias == _PUBLIC_SOURCE_ALIAS and 0 < len(alias) <= max_length:
            return alias
    if text is None or any(not (character.isascii() and (character.isalnum() or character in "_-")) for character in text):
        return None
    return text


def _looks_unsafe(text: str) -> bool:
    upper = text.upper()
    lower = text.lower()
    if "SHOULD_NOT_RENDER" in upper or upper.startswith("INTERNAL_"):
        return True
    if "http://" in lower or "https://" in lower:
        return True
    return bool(
        _UNSAFE_ASSIGNMENT.search(text)
        or _UNSAFE_DIAGNOSTIC_TOKEN.search(text)
        or _UNSAFE_BEARER_TOKEN.search(text)
        or _UNSAFE_AUTHORIZATION.search(text)
    )
