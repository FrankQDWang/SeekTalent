from __future__ import annotations

from hashlib import sha256
from typing import Any

from seektalent.storage.json import canonical_json, sha256_json

JD_SCHEMA_VERSION = "jd-doc-v1"
RESUME_DOC_SCHEMA_VERSION = "resume-doc-v1"
SEARCHABLE_TEXT_VERSION = "searchable-text-v1"
NORMALIZATION_VERSION = "resume-normalization-v1"
PII_CLASSIFICATION_VERSION = "pii-v1"
DEFAULT_RETENTION_POLICY = "retain_local"

_PROMPT_LIKE_MARKERS = (
    "ignore previous instructions",
    "忽略之前",
    "system prompt",
    "developer message",
    "把我评为",
    "rank me first",
)


def detect_prompt_like_text(text: str) -> bool:
    folded = text.casefold()
    return any(marker in folded for marker in _PROMPT_LIKE_MARKERS)


def _sha_text(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def build_jd_document_row(
    *,
    tenant_id: str,
    workspace_id: str,
    job_title: str,
    jd_text: str,
    notes_text: str,
    source_kind: str,
    source_ref: str | None,
) -> dict[str, Any]:
    task_sha256 = sha256_json(
        {
            "task_schema_version": JD_SCHEMA_VERSION,
            "job_title": job_title,
            "jd_text": jd_text,
            "notes_text": notes_text,
        }
    )
    jd_doc_id = sha256_json(
        {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "task_sha256": task_sha256,
        }
    )
    return {
        "jd_doc_id": jd_doc_id,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "job_title": job_title,
        "jd_text": jd_text,
        "notes_text": notes_text,
        "jd_sha256": _sha_text(jd_text),
        "notes_sha256": _sha_text(notes_text),
        "task_sha256": task_sha256,
        "language": None,
        "domain_tags_json": [],
        "seniority": None,
        "source_kind": source_kind,
        "source_ref": source_ref,
        "memory_eligible": False,
        "allowed_uses_json": ["search"],
        "search_index_eligible": True,
        "benchmark_eligible": False,
        "training_eligible": False,
        "external_export_eligible": False,
        "internal_materialization_eligible": True,
        "llm_ingestion_eligible": False,
        "consent_basis": None,
        "source_terms_ref": None,
        "pii_classification_version": PII_CLASSIFICATION_VERSION,
        "redaction_status": "unredacted",
        "sensitivity_json": {
            "contains_pii": False,
            "contains_external_text": True,
        },
        "content_trust_level": "untrusted_external",
        "contains_prompt_like_text": detect_prompt_like_text(f"{jd_text}\n{notes_text}"),
        "llm_sanitization_version": None,
        "llm_ingestion_policy": "quote_as_data_only",
        "retention_policy": DEFAULT_RETENTION_POLICY,
        "schema_version": JD_SCHEMA_VERSION,
    }


def build_resume_subject_row(
    *,
    tenant_id: str,
    workspace_id: str,
    provider_name: str,
    provider_candidate_id: str | None,
    source_resume_id: str | None,
    dedup_key: str | None,
    snapshot_sha256: str,
) -> dict[str, Any]:
    if provider_candidate_id:
        binding_reason = "provider_candidate_id"
        subject_key = provider_candidate_id
    elif source_resume_id:
        binding_reason = "source_resume_id"
        subject_key = source_resume_id
    elif dedup_key:
        binding_reason = "dedup_key"
        subject_key = dedup_key
    else:
        binding_reason = "snapshot_sha256"
        subject_key = snapshot_sha256

    subject_id = sha256_json(
        {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "provider_name": provider_name,
            "subject_key": subject_key,
        }
    )
    return {
        "subject_id": subject_id,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "provider_name": provider_name,
        "provider_candidate_id": provider_candidate_id,
        "source_resume_id": source_resume_id,
        "dedup_key": dedup_key,
        "subject_confidence": "snapshot_only" if binding_reason == "snapshot_sha256" else "provider_scoped",
        "subject_binding_reason": binding_reason,
    }


def build_resume_document_row(
    *,
    tenant_id: str,
    workspace_id: str,
    raw_payload: dict[str, Any],
    provider_name: str,
    provider_candidate_id: str | None,
    source_resume_id: str | None,
    dedup_key: str | None,
    resume_doc_id: str,
    subject_id: str,
    snapshot_sha256: str,
    raw_payload_artifact_ref_id: str,
    raw_payload_sha256: str,
    raw_payload_size_bytes: int,
    normalized_text: str,
    first_seen_run_id: str | None,
    first_seen_query_instance_id: str | None,
    first_seen_stage_id: str | None,
    first_seen_artifact_ref_id: str | None,
    provider_privacy_metadata: dict[str, Any] | None = None,
    retention_policy: str | None = None,
) -> dict[str, Any]:
    searchable_text = normalized_text.strip()
    has_searchable_text = bool(searchable_text)
    canonical_raw_payload = canonical_json(raw_payload)
    experience = _experience_items(raw_payload)
    education = _education_items(raw_payload)
    locations = _locations(raw_payload)
    current_title = _current_title(raw_payload, experience)
    current_company = _current_company(experience)
    normalized_sections = _normalized_sections(raw_payload, normalized_text=searchable_text)
    sensitivity_json: dict[str, Any] = {
        "contains_pii": True,
        "contains_external_text": True,
    }
    if provider_privacy_metadata:
        sensitivity_json["liepin_snapshot"] = provider_privacy_metadata
    return {
        "resume_doc_id": resume_doc_id,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "subject_id": subject_id,
        "snapshot_sha256": snapshot_sha256,
        "source_resume_id": source_resume_id,
        "provider_name": provider_name,
        "provider_candidate_id": provider_candidate_id,
        "dedup_key": dedup_key,
        "raw_payload_artifact_ref_id": raw_payload_artifact_ref_id,
        "raw_payload_sha256": raw_payload_sha256,
        "raw_payload_size_bytes": raw_payload_size_bytes,
        "raw_payload_json": None,
        "raw_payload_inline_reason": None,
        "normalized_text": searchable_text or None,
        "normalized_sections_json": normalized_sections,
        "skills_json": _skills(raw_payload),
        "experience_json": experience,
        "education_json": education,
        "locations_json": locations,
        "current_title": current_title,
        "current_company": current_company,
        "searchable_text_version": SEARCHABLE_TEXT_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "normalization_status": "ok" if has_searchable_text else "failed",
        "normalization_failure_kind": None if has_searchable_text else "empty_searchable_text",
        "normalization_warnings_json": [],
        "payload_completeness": "search_result_summary",
        "has_searchable_text": has_searchable_text,
        "source_kind": "provider_return",
        "first_seen_run_id": first_seen_run_id,
        "first_seen_query_instance_id": first_seen_query_instance_id,
        "first_seen_stage_id": first_seen_stage_id,
        "first_seen_artifact_ref_id": first_seen_artifact_ref_id,
        "memory_eligible": False,
        "allowed_uses_json": ["search"],
        "search_index_eligible": has_searchable_text,
        "benchmark_eligible": False,
        "training_eligible": False,
        "external_export_eligible": False,
        "internal_materialization_eligible": True,
        "llm_ingestion_eligible": False,
        "consent_basis": None,
        "source_terms_ref": None,
        "pii_classification_version": PII_CLASSIFICATION_VERSION,
        "redaction_status": "unredacted",
        "sensitivity_json": sensitivity_json,
        "content_trust_level": "untrusted_external",
        "contains_prompt_like_text": detect_prompt_like_text(searchable_text)
        or detect_prompt_like_text(canonical_raw_payload),
        "llm_sanitization_version": None,
        "llm_ingestion_policy": "quote_as_data_only",
        "retention_policy": retention_policy or DEFAULT_RETENTION_POLICY,
        "schema_version": RESUME_DOC_SCHEMA_VERSION,
    }


def _normalized_sections(raw_payload: dict[str, Any], *, normalized_text: str) -> dict[str, Any]:
    summary = _first_text(raw_payload.get("summary"), raw_payload.get("resumeSummary")) or _clip(normalized_text, 800)
    profile = {
        "name": _first_text(
            raw_payload.get("candidate_name"),
            raw_payload.get("candidateName"),
            raw_payload.get("name"),
        )
        or "",
        "summary": summary,
    }
    projects = [
        {"name": name, "summary": summary}
        for name, summary in zip(_text_list(raw_payload.get("projectNameAll")), _text_list(raw_payload.get("workSummariesAll")), strict=False)
    ]
    return {"profile": profile, "projects": projects}


def _experience_items(raw_payload: dict[str, Any]) -> list[dict[str, str | None]]:
    items = raw_payload.get("workExperienceList")
    if not isinstance(items, list):
        return []
    experiences: list[dict[str, str | None]] = []
    for item in items[:20]:
        if not isinstance(item, dict):
            continue
        start = _first_text(item.get("startTime"))
        end = _first_text(item.get("endTime"))
        experiences.append(
            {
                "company": _first_text(item.get("company")) or "",
                "title": _first_text(item.get("title")) or "",
                "duration": _join_nonempty([start, end], " - "),
                "summary": _first_text(item.get("summary")),
            }
        )
    return experiences


def _education_items(raw_payload: dict[str, Any]) -> list[dict[str, str | None]]:
    items = raw_payload.get("educationList")
    if not isinstance(items, list):
        return []
    education: list[dict[str, str | None]] = []
    for item in items[:20]:
        if not isinstance(item, dict):
            continue
        education.append(
            {
                "school": _first_text(item.get("school")) or "",
                "degree": _first_text(item.get("degree"), item.get("education")),
                "major": _first_text(item.get("major"), item.get("speciality")),
            }
        )
    return education


def _locations(raw_payload: dict[str, Any]) -> list[str]:
    return _unique_texts([raw_payload.get("nowLocation"), raw_payload.get("expectedLocation")])


def _skills(raw_payload: dict[str, Any]) -> list[str]:
    return _unique_texts([raw_payload.get("skills"), raw_payload.get("skillTags"), raw_payload.get("skillTagList")])


def _current_title(raw_payload: dict[str, Any], experience: list[dict[str, str | None]]) -> str | None:
    return _first_text(
        raw_payload.get("current_title"),
        raw_payload.get("currentTitle"),
        raw_payload.get("expectedJobCategory"),
        experience[0].get("title") if experience else None,
    )


def _current_company(experience: list[dict[str, str | None]]) -> str | None:
    return _first_text(experience[0].get("company") if experience else None)


def _first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str):
            text = " ".join(value.split()).strip()
            if text:
                return text
    return None


def _text_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := _first_text(item))]


def _unique_texts(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        for text in _text_list(value):
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(text)
    return result


def _join_nonempty(values: list[str | None], separator: str) -> str | None:
    parts = [value for value in values if value]
    return separator.join(parts) if parts else None


def _clip(value: str, limit: int) -> str:
    return value[:limit].strip()


def build_observation_row(
    *,
    tenant_id: str,
    workspace_id: str,
    resume_doc_id: str,
    run_id: str,
    round_no: int | None,
    stage_id: str | None,
    query_instance_id: str | None,
    query_fingerprint: str | None,
    provider_name: str,
    provider_request_id: str | None,
    provider_rank: int | None,
    provider_page_no: int | None,
    provider_fetch_no: int | None,
    attempt_no: int,
    source_artifact_ref_id: str | None,
) -> dict[str, Any]:
    idempotency_key = sha256_json(
        {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "run_id": run_id,
            "stage_id": stage_id,
            "query_instance_id": query_instance_id,
            "provider_name": provider_name,
            "provider_request_id": provider_request_id,
            "provider_page_no": provider_page_no,
            "provider_fetch_no": provider_fetch_no,
            "provider_rank": provider_rank,
            "resume_doc_id": resume_doc_id,
        }
    )
    observation_id = sha256_json(
        {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "idempotency_key": idempotency_key,
        }
    )
    return {
        "observation_id": observation_id,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "resume_doc_id": resume_doc_id,
        "run_id": run_id,
        "round_no": round_no,
        "stage_id": stage_id,
        "query_instance_id": query_instance_id,
        "query_fingerprint": query_fingerprint,
        "provider_name": provider_name,
        "provider_request_id": provider_request_id,
        "provider_rank": provider_rank,
        "provider_page_no": provider_page_no,
        "provider_fetch_no": provider_fetch_no,
        "attempt_no": attempt_no,
        "idempotency_key": idempotency_key,
        "was_scored": False,
        "was_judged": False,
        "was_selected_final": False,
        "source_artifact_ref_id": source_artifact_ref_id,
    }
