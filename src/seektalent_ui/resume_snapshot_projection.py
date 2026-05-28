from __future__ import annotations

import json
from pathlib import Path

from seektalent.artifacts import safe_artifact_path
from seektalent.config import AppSettings
from seektalent.corpus.store import CorpusStore
from seektalent_ui.models import (
    WorkbenchGraphCandidateResumeSnapshotResponse,
    WorkbenchResumeSnapshotSourceCompleteness,
    WorkbenchOriginalResumeFieldResponse,
    WorkbenchOriginalResumeItemResponse,
    WorkbenchOriginalResumeResponse,
    WorkbenchOriginalResumeSectionResponse,
    WorkbenchResumeSnapshotEducationResponse,
    WorkbenchResumeSnapshotProfileResponse,
    WorkbenchResumeSnapshotProjectResponse,
    WorkbenchResumeSnapshotSourceEvidenceResponse,
    WorkbenchResumeSnapshotWorkExperienceResponse,
)
from seektalent_ui.models import WorkbenchGraphCandidateSummaryResponse
from seektalent_ui.workbench_candidate_graph import resolve_graph_candidate
from seektalent_ui.workbench_store import DEFAULT_TENANT_ID, WorkbenchStore, WorkbenchUser


def build_resume_snapshot_response(
    *,
    settings: AppSettings,
    graph_secret: str,
    store: WorkbenchStore,
    user: WorkbenchUser,
    session_id: str,
    graph_candidate_id: str,
) -> WorkbenchGraphCandidateResumeSnapshotResponse | None:
    candidate = resolve_graph_candidate(
        settings=settings,
        graph_secret=graph_secret,
        store=store,
        user=user,
        session_id=session_id,
        graph_candidate_id=graph_candidate_id,
    )
    if candidate is None:
        return None
    if not candidate.snapshot_sha256:
        return WorkbenchGraphCandidateResumeSnapshotResponse(
            graphCandidateId=graph_candidate_id,
            status="snapshot_not_found",
            reason="snapshot_not_found",
            sourceCompleteness="unavailable",
        )
    corpus = CorpusStore(settings.corpus_path)
    docs = corpus.get_resume_documents_by_snapshot_sha256(
        tenant_id=DEFAULT_TENANT_ID,
        workspace_id=user.workspace_id,
        snapshot_sha256_values=[candidate.snapshot_sha256],
    )
    doc = docs.get(candidate.snapshot_sha256)
    if doc is None:
        return WorkbenchGraphCandidateResumeSnapshotResponse(
            graphCandidateId=graph_candidate_id,
            status="snapshot_not_found",
            reason="snapshot_not_found",
            sourceCompleteness="unavailable",
        )
    if not _snapshot_materialization_allowed(doc):
        return WorkbenchGraphCandidateResumeSnapshotResponse(
            graphCandidateId=graph_candidate_id,
            status="snapshot_forbidden",
            reason="snapshot_forbidden",
            sourceCompleteness="unavailable",
        )
    return _project_doc(graph_candidate_id=graph_candidate_id, corpus=corpus, doc=doc, fallback=candidate.summary)


def _project_doc(
    *,
    graph_candidate_id: str,
    corpus: CorpusStore,
    doc: dict[str, object],
    fallback: WorkbenchGraphCandidateSummaryResponse,
) -> WorkbenchGraphCandidateResumeSnapshotResponse:
    sections = _json_object(doc.get("normalized_sections_json"))
    profile = _json_object(sections.get("profile"))
    locations = _json_list(doc.get("locations_json"))
    normalized_text = _safe_text(doc.get("normalized_text"), 1200) or ""
    display_name = _safe_text(profile.get("name"), 160) or _safe_text(fallback.displayName, 160) or ""
    headline = _safe_text(doc.get("current_title"), 160) or _safe_text(fallback.title, 160) or ""
    company = _safe_text(doc.get("current_company"), 160) or _safe_text(fallback.company, 160) or ""
    location = (_safe_text(locations[0], 160) if locations else None) or _safe_text(fallback.location, 160) or ""
    summary = _safe_text(profile.get("summary"), 500) or _safe_text(fallback.summary, 500) or normalized_text
    original_resume = _project_original_resume(corpus=corpus, doc=doc)
    provider_name = _provider_name(doc)
    return WorkbenchGraphCandidateResumeSnapshotResponse(
        graphCandidateId=graph_candidate_id,
        status="ready",
        sourceCompleteness=_source_completeness(provider_name, original_resume),
        originalResume=original_resume,
        profile=WorkbenchResumeSnapshotProfileResponse(
            displayName=display_name,
            headline=headline,
            company=company,
            location=location,
            summary=summary,
        ),
        workExperience=[
            WorkbenchResumeSnapshotWorkExperienceResponse(
                company=_safe_text(item.get("company"), 160) or "",
                title=_safe_text(item.get("title"), 160) or "",
                duration=_safe_text(item.get("duration"), 80),
                summary=_safe_text(item.get("summary"), 500),
            )
            for item in _dict_items(doc.get("experience_json"))[:20]
        ],
        education=[
            WorkbenchResumeSnapshotEducationResponse(
                school=_safe_text(item.get("school"), 160) or "",
                degree=_safe_text(item.get("degree"), 120),
                major=_safe_text(item.get("major"), 120),
            )
            for item in _dict_items(doc.get("education_json"))[:20]
        ],
        projects=[
            WorkbenchResumeSnapshotProjectResponse(
                name=_safe_text(item.get("name"), 160) or "",
                summary=_safe_text(item.get("summary"), 500),
            )
            for item in _dict_items(sections.get("projects"))[:20]
        ],
        skills=[skill for skill in (_safe_text(value, 80) for value in _json_list(doc.get("skills_json"))) if skill][:50],
        sourceEvidence=[
            WorkbenchResumeSnapshotSourceEvidenceResponse(label="summary", text=summary)
        ]
        if summary
        else [],
    )


_BASIC_FIELD_KEYS = (
    "candidateName",
    "candidate_name",
    "name",
    "age",
    "gender",
    "nowLocation",
    "location",
    "activeStatus",
    "jobState",
    "workYear",
)
_EXPECTATION_FIELD_KEYS = (
    "expectedJobCategory",
    "expectedIndustry",
    "expectedLocation",
    "expectedSalary",
)
_PROJECT_TEXT_FIELD_KEYS = ("projectNameAll", "workSummariesAll")
_LIEPIN_BASIC_FIELD_KEYS = (
    "candidate_name",
    "candidateName",
    "name",
    "currentTitle",
    "current_title",
    "title",
    "currentCompany",
    "current_company",
    "company",
    "location",
)
_LIEPIN_TEXT_FIELD_KEYS = ("summary", "fullText", "rawText", "page_text")
_STRUCTURED_SECTION_KEYS = set(
    _BASIC_FIELD_KEYS
    + _EXPECTATION_FIELD_KEYS
    + _PROJECT_TEXT_FIELD_KEYS
    + _LIEPIN_BASIC_FIELD_KEYS
    + _LIEPIN_TEXT_FIELD_KEYS
    + ("workExperienceList", "educationList")
)
_FIELD_LABELS = {
    "candidateName": "姓名",
    "candidate_name": "姓名",
    "name": "姓名",
    "age": "年龄",
    "gender": "性别",
    "nowLocation": "当前地点",
    "location": "地点",
    "activeStatus": "活跃状态",
    "jobState": "求职状态",
    "workYear": "工作年限",
    "expectedJobCategory": "期望岗位",
    "expectedIndustry": "期望行业",
    "expectedLocation": "期望地点",
    "expectedSalary": "期望薪资",
    "company": "公司",
    "title": "职位",
    "currentTitle": "当前职位",
    "current_title": "当前职位",
    "currentCompany": "当前公司",
    "current_company": "当前公司",
    "startTime": "开始时间",
    "endTime": "结束时间",
    "summary": "经历描述",
    "fullText": "简历全文",
    "rawText": "原始文本",
    "page_text": "页面文本",
    "school": "学校",
    "degree": "学历",
    "major": "专业",
    "projectNameAll": "项目名称",
    "workSummariesAll": "经历文本",
}
_SENSITIVE_KEY_TOKENS = (
    "authorization",
    "authheader",
    "bearer",
    "cookie",
    "debug",
    "stack",
    "storage",
    "token",
    "websocket",
    "wsendpoint",
)
_SENSITIVE_KEY_EXACT = {
    "artifact",
    "artifactpath",
    "artifactref",
    "artifactrefid",
    "artifactroot",
    "rawpayloadartifactrefid",
    "run_dir",
}
_PROVIDER_METADATA_KEY_EXACT = {
    "createtime",
    "createdtime",
    "createdat",
    "updatetime",
    "updatedtime",
    "updatedat",
}
_PROVIDER_METADATA_KEY_TOKENS = (
    "categoryid",
    "categoryids",
    "companyid",
    "companyids",
    "groupcompanyid",
    "groupcompanyids",
    "industryid",
    "industryids",
    "locationid",
    "locationids",
    "providerid",
    "providerrank",
    "hash",
    "resumeid",
    "schoolid",
    "schoolids",
    "sourceid",
    "uuid",
)


def _project_original_resume(*, corpus: CorpusStore, doc: dict[str, object]) -> WorkbenchOriginalResumeResponse | None:
    provider_name = _provider_name(doc)
    if provider_name not in {"cts", "liepin"}:
        return None
    artifact_ref_id = str(doc.get("raw_payload_artifact_ref_id") or "").strip()
    payload = _read_raw_payload(corpus, artifact_ref_id or None)
    if not isinstance(payload, dict):
        return None
    sections = _liepin_original_sections(payload) if provider_name == "liepin" else _cts_original_sections(payload)
    visible_sections = [section for section in sections if section is not None and section.items]
    if not visible_sections:
        return None
    return WorkbenchOriginalResumeResponse(sourceKind=provider_name, sections=visible_sections)


def _provider_name(doc: dict[str, object]) -> str:
    return str(doc.get("provider_name") or "").strip().casefold()


def _source_completeness(
    provider_name: str,
    original_resume: WorkbenchOriginalResumeResponse | None,
) -> WorkbenchResumeSnapshotSourceCompleteness:
    if original_resume is None:
        return "normalized_fallback"
    if provider_name == "liepin":
        return "liepin_raw_payload"
    return "cts_raw_payload"


def _cts_original_sections(payload: dict[str, object]) -> list[WorkbenchOriginalResumeSectionResponse | None]:
    return [
        _field_section("基本信息", payload, _BASIC_FIELD_KEYS),
        _field_section("期望信息", payload, _EXPECTATION_FIELD_KEYS),
        _list_section("工作经历", payload.get("workExperienceList")),
        _list_section("教育经历", payload.get("educationList")),
        _field_section("项目/经历文本", payload, _PROJECT_TEXT_FIELD_KEYS),
        _other_section(payload),
    ]


def _liepin_original_sections(payload: dict[str, object]) -> list[WorkbenchOriginalResumeSectionResponse | None]:
    return [
        _field_section("基本信息", payload, _LIEPIN_BASIC_FIELD_KEYS),
        _list_section("工作经历", payload.get("workExperienceList")),
        _list_section("教育经历", payload.get("educationList")),
        _field_section("简历文本", payload, _LIEPIN_TEXT_FIELD_KEYS),
        _other_section(payload),
    ]


def _read_raw_payload(corpus: CorpusStore, artifact_ref_id: str | None) -> dict[str, object] | None:
    if not artifact_ref_id:
        return None
    artifact_ref = corpus.get_artifact_ref(artifact_ref_id)
    if artifact_ref is None:
        return None
    relative_path = str(artifact_ref.get("relative_path") or "").strip()
    root = str(artifact_ref.get("artifact_root") or "").strip()
    if not relative_path or not root:
        return None
    try:
        path = safe_artifact_path(Path(root), relative_path)
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _field_section(
    title: str,
    payload: dict[str, object],
    keys: tuple[str, ...],
) -> WorkbenchOriginalResumeSectionResponse | None:
    fields = [_field_response(key, payload.get(key)) for key in keys if key in payload]
    visible_fields = [field for field in fields if field is not None]
    if not visible_fields:
        return None
    return WorkbenchOriginalResumeSectionResponse(
        title=title,
        items=[WorkbenchOriginalResumeItemResponse(title=title, fields=visible_fields)],
    )


def _list_section(title: str, value: object) -> WorkbenchOriginalResumeSectionResponse | None:
    if not isinstance(value, list) or not value:
        return None
    items: list[WorkbenchOriginalResumeItemResponse] = []
    for index, item in enumerate(value, start=1):
        if isinstance(item, dict):
            fields = [_field_response(str(key), field_value) for key, field_value in item.items()]
            visible_fields = [field for field in fields if field is not None]
            if visible_fields:
                items.append(WorkbenchOriginalResumeItemResponse(title=_item_title(title, visible_fields, index), fields=visible_fields))
            continue
        field = _field_response(f"{title}.{index}", item)
        if field is not None:
            items.append(WorkbenchOriginalResumeItemResponse(title=f"{title} {index}", fields=[field]))
    return WorkbenchOriginalResumeSectionResponse(title=title, items=items) if items else None


def _other_section(payload: dict[str, object]) -> WorkbenchOriginalResumeSectionResponse | None:
    fields = [
        _field_response(key, value)
        for key, value in payload.items()
        if key not in _STRUCTURED_SECTION_KEYS
    ]
    visible_fields = [field for field in fields if field is not None]
    if not visible_fields:
        return None
    return WorkbenchOriginalResumeSectionResponse(
        title="其他信息",
        items=[WorkbenchOriginalResumeItemResponse(title="其他信息", fields=visible_fields)],
    )


def _field_response(key: str, value: object) -> WorkbenchOriginalResumeFieldResponse | None:
    if _is_sensitive_resume_key(key):
        return None
    text = _resume_value_text(value)
    if not text:
        return None
    return WorkbenchOriginalResumeFieldResponse(key=key, label=_FIELD_LABELS.get(key, key), value=text)


def _resume_value_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
    elif isinstance(value, bool):
        text = "是" if value else "否"
    elif isinstance(value, int | float):
        text = str(value)
    elif isinstance(value, list):
        parts = [part for item in value if (part := _resume_value_text(item))]
        text = "、".join(parts)
    elif isinstance(value, dict):
        parts = []
        for raw_key, raw_item in value.items():
            key = str(raw_key)
            if _is_sensitive_resume_key(key):
                continue
            item_text = _resume_value_text(raw_item)
            if item_text:
                parts.append(f"{_FIELD_LABELS.get(key, key)}：{item_text}")
        text = "；".join(parts)
    else:
        text = str(value).strip()
    return _safe_text(text, 2000)


def _is_sensitive_resume_key(key: str) -> bool:
    compact = "".join(character for character in key.casefold() if character.isalnum())
    if compact in _SENSITIVE_KEY_EXACT:
        return True
    if any(token in compact for token in _SENSITIVE_KEY_TOKENS):
        return True
    return _is_provider_metadata_key(compact)


def _is_provider_metadata_key(compact_key: str) -> bool:
    if compact_key in _PROVIDER_METADATA_KEY_EXACT:
        return True
    if compact_key.endswith("id") or compact_key.endswith("ids"):
        return True
    return any(token in compact_key for token in _PROVIDER_METADATA_KEY_TOKENS)


def _item_title(title: str, fields: list[WorkbenchOriginalResumeFieldResponse], index: int) -> str:
    by_key = {field.key: field.value for field in fields}
    if title == "工作经历":
        return " · ".join(value for value in [by_key.get("title"), by_key.get("company")] if value) or f"工作经历 {index}"
    if title == "教育经历":
        return " · ".join(value for value in [by_key.get("school"), by_key.get("degree"), by_key.get("major")] if value) or f"教育经历 {index}"
    return f"{title} {index}"


def _dict_items(value: object) -> list[dict[str, object]]:
    return [_json_object(item) for item in _json_list(value) if isinstance(item, dict)]


def _json_object(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return {str(key): item for key, item in parsed.items()} if isinstance(parsed, dict) else {}
    return {}


def _json_list(value: object) -> list[object]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return list(parsed) if isinstance(parsed, list) else []
    return []


def _snapshot_materialization_allowed(doc: dict[str, object]) -> bool:
    if not bool(doc.get("internal_materialization_eligible")):
        return False
    redaction_status = str(doc.get("redaction_status") or "").strip().casefold()
    if redaction_status in {"blocked", "forbidden", "failed"}:
        return False
    allowed_uses = {str(value).strip().casefold() for value in _json_list(doc.get("allowed_uses_json"))}
    return bool(allowed_uses.intersection({"search", "recruiting", "internal_materialization", "workspace_recruiting_record"}))


def _safe_text(value: object, max_length: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    forbidden = (
        "cookie",
        "authorization",
        "bearer ",
        "set-cookie",
        "authheader",
        "storagestate",
        "localstorage",
        "sessionstorage",
        "cdp",
        "websocket",
        "websocketdebuggerurl",
        "wsendpoint",
        "run_dir",
        "artifact",
        "provider_account_hash",
        "provider-secret",
        "source-secret",
        "token=",
        "ticket=",
    )
    lowered = text.lower()
    if any(token in lowered for token in forbidden):
        return None
    if "://" in lowered and ("http://" in lowered or "https://" in lowered or "ws://" in lowered or "wss://" in lowered):
        return None
    return text[:max_length]
