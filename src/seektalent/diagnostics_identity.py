"""Bounded identity, hash, version, and timestamp types for diagnostics v1."""

from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import Annotated

from pydantic import AfterValidator, Field


MAX_SAFE_INTEGER = (1 << 53) - 1
_RANDOM_ID_RE = re.compile(r"(?!0{32})[0-9a-f]{32}")
_SHA256_REF_RE = re.compile(r"sha256:[0-9a-f]{64}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_TRACE_RE = re.compile(r"(?!0{32})[0-9a-f]{32}")
_SPAN_RE = re.compile(r"(?!0{16})[0-9a-f]{16}")
_UTC_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_VERSION_RE = re.compile(r"[0-9]+(?:\.[0-9]+){0,3}(?:[-+][a-z0-9.]+)?")


def _random_identity(value: str) -> str:
    if _RANDOM_ID_RE.fullmatch(value) is None:
        raise ValueError("diagnostics_invalid_random_identity")
    return value


def _sha256_ref(value: str) -> str:
    if _SHA256_REF_RE.fullmatch(value) is None:
        raise ValueError("diagnostics_invalid_sha256_reference")
    return value


def _sha256(value: str) -> str:
    if _SHA256_RE.fullmatch(value) is None:
        raise ValueError("diagnostics_invalid_sha256")
    return value


def _trace(value: str) -> str:
    if _TRACE_RE.fullmatch(value) is None:
        raise ValueError("diagnostics_invalid_trace_id")
    return value


def _span(value: str) -> str:
    if _SPAN_RE.fullmatch(value) is None:
        raise ValueError("diagnostics_invalid_span_id")
    return value


def _timestamp(value: str) -> str:
    if _UTC_RE.fullmatch(value) is None:
        raise ValueError("diagnostics_invalid_timestamp")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        raise ValueError("diagnostics_invalid_timestamp") from None
    if parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("diagnostics_invalid_timestamp")
    return value


def _version(value: str) -> str:
    if _VERSION_RE.fullmatch(value) is None:
        raise ValueError("diagnostics_invalid_version")
    return value


RandomIdentity = Annotated[
    str, Field(strict=True, min_length=32, max_length=32), AfterValidator(_random_identity)
]
Sha256Ref = Annotated[
    str, Field(strict=True, min_length=71, max_length=71), AfterValidator(_sha256_ref)
]
Sha256 = Annotated[str, Field(strict=True), AfterValidator(_sha256)]
TraceId = Annotated[str, Field(strict=True), AfterValidator(_trace)]
SpanId = Annotated[str, Field(strict=True), AfterValidator(_span)]
UtcTimestamp = Annotated[str, Field(strict=True), AfterValidator(_timestamp)]
VersionString = Annotated[
    str, Field(strict=True, min_length=1, max_length=32), AfterValidator(_version)
]
PositiveSafeInteger = Annotated[int, Field(strict=True, ge=1, le=MAX_SAFE_INTEGER)]
NonNegativeSafeInteger = Annotated[int, Field(strict=True, ge=0, le=MAX_SAFE_INTEGER)]
