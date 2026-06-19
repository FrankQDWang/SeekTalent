from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from seektalent_ui.workbench_store_helpers import (
    attr as _attr,
    int_or_none as _int_or_none,
    safe_candidate_text as _safe_candidate_text,
    sha256_text as _sha256_text,
)


@dataclass(frozen=True)
class RuntimeFallbackEvidence:
    evidence_id: str
    source: str
    evidence_level: str
    candidate_resume_id: str
    provider_candidate_key_hash: str


def finalizer_candidate_by_resume_id(artifacts: object) -> dict[str, object]:
    final_result = getattr(artifacts, "final_result", None)
    result: dict[str, object] = {}
    for candidate in list(getattr(final_result, "candidates", []) or []):
        resume_id = _safe_candidate_text(_attr(candidate, "resume_id"), 128)
        if resume_id:
            result[resume_id] = candidate
    return result


def runtime_fallback_final_evidence(
    *,
    identity_id: str,
    canonical_resume_id: str,
    source_kind: str,
    evidence_id: str,
) -> RuntimeFallbackEvidence:
    return RuntimeFallbackEvidence(
        evidence_id=evidence_id,
        source=source_kind,
        evidence_level="final",
        candidate_resume_id=canonical_resume_id,
        provider_candidate_key_hash=_sha256_text(f"{identity_id}:{canonical_resume_id}"),
    )


def snapshot_payload(snapshot: object) -> Mapping[str, object]:
    payload = _attr(snapshot, "raw_payload")
    if not isinstance(payload, Mapping):
        return {}
    return {str(key): value for key, value in payload.items()}


def candidate_education(
    raw_candidate: object,
    normalized: object | None = None,
    *,
    payload: Mapping[str, object] | None = None,
) -> str | None:
    raw_payload = _attr(raw_candidate, "raw")
    sources = (
        _attr(normalized, "education"),
        _attr(normalized, "highest_education"),
        _attr(raw_candidate, "education"),
        _attr(raw_candidate, "highest_education"),
        _attr(raw_payload, "education"),
        _attr(raw_payload, "degree"),
        payload.get("education") if payload is not None else None,
        payload.get("degree") if payload is not None else None,
    )
    for value in sources:
        text = _safe_candidate_text(value, 160)
        if text:
            return text
    return None


def candidate_experience_years(
    raw_candidate: object,
    normalized: object | None = None,
    *,
    payload: Mapping[str, object] | None = None,
) -> int | None:
    raw_payload = _attr(raw_candidate, "raw")
    sources = (
        _attr(normalized, "experience_years"),
        _attr(normalized, "work_years"),
        _attr(raw_candidate, "experience_years"),
        _attr(raw_candidate, "work_years"),
        _attr(raw_payload, "experience_years"),
        _attr(raw_payload, "work_years"),
        payload.get("experienceYears") if payload is not None else None,
        payload.get("experience_years") if payload is not None else None,
        payload.get("workYears") if payload is not None else None,
        payload.get("work_years") if payload is not None else None,
    )
    for value in sources:
        years = safe_experience_years(value)
        if years is not None:
            return years
    return None


def safe_experience_years(value: object) -> int | None:
    years = _int_or_none(value)
    if years is None:
        text = _safe_candidate_text(value, 16)
        years = int(text) if text is not None and text.isdigit() else None
    if years is None or years < 0 or years > 80:
        return None
    return years


def liepin_card_display_fields(
    *,
    candidate: object,
    payload: Mapping[str, object],
    workbench_resume_id: str,
) -> tuple[str, str, str, str, str]:
    display_name = (
        _safe_candidate_text(payload.get("name"), 160)
        or _safe_candidate_text(payload.get("candidateName"), 160)
        or f"Candidate {workbench_resume_id[-8:]}"
    )
    title = (
        _safe_candidate_text(payload.get("title"), 240)
        or _safe_candidate_text(_attr(candidate, "expected_job_category"), 240)
        or "Liepin candidate card"
    )
    company = _safe_candidate_text(payload.get("company"), 240) or ""
    location = (
        _safe_candidate_text(payload.get("location"), 160)
        or _safe_candidate_text(_attr(candidate, "now_location"), 160)
        or ""
    )
    summary = (
        _safe_candidate_text(payload.get("summary"), 1000)
        or _safe_candidate_text(_attr(candidate, "search_text"), 1000)
        or ""
    )
    return display_name, title, company, location, summary


def safe_string_list(value: object, *, max_items: int = 100, max_length: int = 256) -> list[str]:
    if not isinstance(value, Iterable) or isinstance(value, str | bytes | bytearray):
        return []
    items: list[str] = []
    for item in value:
        text = _safe_candidate_text(item, max_length)
        if text is not None:
            items.append(text)
        if len(items) >= max_items:
            break
    return items


def mapping_payload(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    payload: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            continue
        if isinstance(item, str | int | float | bool) or item is None:
            payload[key] = item
        elif isinstance(item, Mapping):
            payload[key] = mapping_payload(item)
        elif isinstance(item, Iterable) and not isinstance(item, str | bytes | bytearray):
            payload[key] = safe_string_list(item)
    return payload


def mapping_items(value: object) -> list[tuple[object, object]]:
    if not isinstance(value, Mapping):
        return []
    return list(value.items())
