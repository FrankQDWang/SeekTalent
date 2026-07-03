from __future__ import annotations

import pytest
from pydantic import ValidationError

from seektalent.providers.liepin import mapper as liepin_mapper
from seektalent.providers.liepin.detail_payload_text import (
    PROHIBITED_LIEPIN_WHOLE_PAGE_TEXT_KEYS,
    STRUCTURED_LIEPIN_DETAIL_TEXT_MAX_CHARS,
)
from seektalent.providers.liepin.mapper import map_liepin_worker_card, map_liepin_worker_detail
from seektalent.providers.liepin.worker_contracts import (
    LiepinSafeCardSummary,
    LiepinWorkerCandidateCard,
    LiepinWorkerCandidateDetail,
)


ALLOWED_RAW_KEYS = {
    "provider",
    "provider_subject_id",
    "provider_listing_id",
    "synthetic_candidate_fingerprint",
    "identity_confidence",
    "extraction_source",
    "extractor_version",
    "pii_classification",
    "retention_policy",
    "access_scope",
    "redaction_state",
    "raw_payload_artifact_ref",
    "score_evidence_source",
}

WHOLE_PAGE_TEXT_ALIASES = tuple(sorted(PROHIBITED_LIEPIN_WHOLE_PAGE_TEXT_KEYS))

ALLOWED_DETAIL_RAW_KEYS = ALLOWED_RAW_KEYS | {
    "candidate_name",
    "activeStatus",
    "jobStatus",
    "gender",
    "age",
    "city",
    "education",
    "workYears",
    "currentTitle",
    "currentCompany",
    "jobIntention",
    "workExperienceList",
    "projectExperienceList",
    "educationList",
    "skills",
    "sourceUrl",
}

FORBIDDEN_RAW_KEYS = {
    "raw_payload",
    "payload",
    "resume_text",
    "resume_free_text",
    "phone",
    "email",
    "cookies",
    "storageState",
    "authorization",
    "auth_headers",
    "detail_body",
}


def _worker_card() -> LiepinWorkerCandidateCard:
    return LiepinWorkerCandidateCard(
        payload={
            "candidateId": "candidate-1",
            "listingId": "listing-1",
            "name": "Candidate One",
            "headline": "Python backend engineer",
            "privateCardNote": "Private card note with 13800000000 and one@example.com",
            "phone": "13800000000",
            "email": "one@example.com",
            "cookies": "session=secret",
            "storageState": {"cookies": [{"name": "session", "value": "secret"}]},
            "authorization": "Bearer secret",
        },
        normalized_text="Python backend engineer card summary",
        provider_subject_id="candidate-1",
        provider_listing_id="listing-1",
        synthetic_candidate_fingerprint="fp-card-1",
        identity_confidence="provider_subject_id",
        extraction_source="network",
        extractor_version="liepin-worker-v1",
        pii_classification="direct_contact_possible",
        retention_policy="provider_snapshot_30d",
        access_scope="local_run_only",
        redaction_state="raw_provider_payload",
    )


def _worker_card_wire_payload() -> dict[str, object]:
    return _worker_card().model_dump(mode="json", by_alias=True, exclude_none=True)


def _worker_detail() -> LiepinWorkerCandidateDetail:
    return LiepinWorkerCandidateDetail(
        payload={
            "candidateId": "candidate-1",
            "listingId": "listing-1",
            "candidate_name": "吴**",
            "activeStatus": "近30天内活跃",
            "jobStatus": "在职，看看新机会",
            "gender": "男",
            "age": 32,
            "city": "上海",
            "education": "本科",
            "workYears": 10,
            "currentTitle": "资深体验设计工程师",
            "currentCompany": "平安集团",
            "jobIntention": {"expectedSalary": "20-24k*14薪"},
            "workExperienceList": [
                {
                    "company": "平安好医",
                    "title": "用户体验设计专家",
                    "summary": "structured work summary stays",
                }
            ],
            "projectExperienceList": [{"name": "助力C端业务增长", "summary": "structured project summary stays"}],
            "educationList": [{"school": "华东师范大学", "degree": "硕士"}],
            "skills": ["用户研究", "交互设计"],
            "sourceUrl": "https://h.liepin.com/resume/showresumedetail/?res_id_encode=abc",
            "phone": "13800000000",
            "email": "one@example.com",
            "auth_headers": {"authorization": "Bearer secret"},
        },
        normalized_text="Python backend engineer detail summary",
        provider_subject_id="candidate-1",
        provider_listing_id="listing-1",
        synthetic_candidate_fingerprint="fp-detail-1",
        identity_confidence="provider_subject_id",
        extraction_source="dom_fallback",
        extractor_version="liepin-worker-v1",
        pii_classification="direct_contact_present",
        retention_policy="provider_snapshot_7d",
        access_scope="local_run_only",
        redaction_state="raw_provider_payload",
    )


def test_card_mapping_keeps_raw_payload_out_of_resume_candidate_raw() -> None:
    mapped = map_liepin_worker_card(_worker_card(), raw_payload_artifact_ref="worker://cards/candidate-1.json")

    assert set(mapped.candidate.raw) == ALLOWED_RAW_KEYS
    assert not (set(mapped.candidate.raw) & FORBIDDEN_RAW_KEYS)
    assert "13800000000" not in str(mapped.candidate.raw)
    assert "one@example.com" not in str(mapped.candidate.raw)
    assert "Private card note" not in str(mapped.candidate.raw)
    assert mapped.candidate.raw["raw_payload_artifact_ref"] == "worker://cards/candidate-1.json"


def test_worker_card_accepts_allowlisted_safe_card_summary() -> None:
    card = _worker_card().model_copy(
        update={
            "safe_card_summary": LiepinSafeCardSummary(
                current_or_recent_company="Acme",
                current_or_recent_title="Backend Engineer",
                skill_tags=("Python", "FastAPI"),
                masked_name=True,
            )
        }
    )

    mapped = map_liepin_worker_card(card, raw_payload_artifact_ref="worker://cards/candidate-1.json")

    assert mapped.candidate.raw["safe_card_summary"] == {
        "display_title": None,
        "current_or_recent_company": "Acme",
        "current_or_recent_title": "Backend Engineer",
        "work_years": None,
        "age": None,
        "gender": None,
        "city": None,
        "expected_city": None,
        "education_level": None,
        "school_names": [],
        "major_names": [],
        "skill_tags": ["Python", "FastAPI"],
        "job_intention": None,
        "active_status": None,
        "badges": [],
        "experience_preview": [],
        "education_preview": [],
        "masked_name": True,
    }


def test_worker_card_accepts_structured_card_evidence_preview_fields() -> None:
    payload = _worker_card_wire_payload()
    payload["safeCardSummary"] = {
        "current_or_recent_company": "北京思图场景数据科技服务有限公司",
        "current_or_recent_title": "AI算法工程师",
        "skill_tags": ["Python", "MySQL"],
        "experience_preview": [
            {
                "company": "北京思图场景数据科技服务有限公司",
                "title": "AI算法工程师",
                "date_range": "2021.04-至今",
                "duration": "6年3个月",
                "is_current": True,
            }
        ],
        "education_preview": [
            {
                "school": "齐齐哈尔大学",
                "major": "计算机科学与技术",
                "degree": "本科",
                "recruitment_type": "统招",
                "date_range": "2017.08-2021.07",
            }
        ],
        "masked_name": True,
    }
    card = LiepinWorkerCandidateCard.model_validate(payload)

    mapped = map_liepin_worker_card(card, raw_payload_artifact_ref="worker://cards/candidate-1.json")

    summary = mapped.candidate.raw["safe_card_summary"]
    assert summary["experience_preview"] == [
        {
            "company": "北京思图场景数据科技服务有限公司",
            "title": "AI算法工程师",
            "date_range": "2021.04-至今",
            "duration": "6年3个月",
            "is_current": True,
        }
    ]
    assert summary["education_preview"] == [
        {
            "school": "齐齐哈尔大学",
            "major": "计算机科学与技术",
            "degree": "本科",
            "recruitment_type": "统招",
            "date_range": "2017.08-2021.07",
        }
    ]


def test_worker_card_preserves_pi_safe_hash_and_artifact_refs() -> None:
    card = _worker_card().model_copy(
        update={
            "payload": {
                "providerCandidateKeyHash": "hmac-provider-key-1",
                "safeSummaryRef": "artifact://public-summary/pi-card/run-1/1",
                "protectedSnapshotRef": "artifact://protected/pi-card/run-1/1",
                "actionTraceRef": "artifact://protected/pi-trace/run-1",
            },
            "provider_subject_id": None,
            "synthetic_candidate_fingerprint": "fingerprint-from-provider-hash",
            "identity_confidence": "synthetic_fingerprint",
        }
    )

    mapped = map_liepin_worker_card(card)

    assert mapped.candidate.raw["provider_candidate_key_hash"] == "hmac-provider-key-1"
    assert mapped.candidate.raw["safe_summary_ref"] == "artifact://public-summary/pi-card/run-1/1"
    assert mapped.candidate.raw["provider_snapshot_ref"] == "artifact://protected/pi-card/run-1/1"
    assert mapped.candidate.raw["action_trace_ref"] == "artifact://protected/pi-trace/run-1"


def test_worker_card_rejects_unknown_safe_card_summary_fields() -> None:
    payload = _worker_card().model_dump(mode="json")
    payload["safeCardSummary"] = {
        "current_or_recent_title": "Backend Engineer",
        "cookie": "session=secret",
    }

    with pytest.raises(ValidationError):
        LiepinWorkerCandidateCard.model_validate(payload)


def test_worker_card_rejects_card_text_tail_fields() -> None:
    payload = _worker_card_wire_payload()
    payload["safeCardSummary"] = {
        "current_or_recent_title": "Backend Engineer",
        "visible_text": "raw visible card text",
    }

    with pytest.raises(ValidationError) as visible_text_error:
        LiepinWorkerCandidateCard.model_validate(payload)
    assert ("safeCardSummary", "visible_text") in (error["loc"] for error in visible_text_error.value.errors())

    payload = _worker_card_wire_payload()
    payload["safeCardSummary"] = {
        "current_or_recent_title": "Backend Engineer",
        "normalized_card_text": "legacy card text",
    }

    with pytest.raises(ValidationError) as normalized_text_error:
        LiepinWorkerCandidateCard.model_validate(payload)
    assert ("safeCardSummary", "normalized_card_text") in (
        error["loc"] for error in normalized_text_error.value.errors()
    )


def test_safe_card_summary_does_not_copy_raw_payload_contact_material() -> None:
    card = _worker_card().model_copy(
        update={
            "safe_card_summary": LiepinSafeCardSummary(
                current_or_recent_title="Backend Engineer",
                experience_preview=({"company": "Acme", "title": "Backend Engineer"},),
            )
        }
    )

    mapped = map_liepin_worker_card(card, raw_payload_artifact_ref="worker://cards/candidate-1.json")

    assert "13800000000" not in str(mapped.candidate.raw["safe_card_summary"])
    assert "one@example.com" not in str(mapped.candidate.raw["safe_card_summary"])
    assert "session=secret" not in str(mapped.candidate.raw["safe_card_summary"])


def test_detail_mapping_keeps_raw_payload_and_detail_body_out_of_resume_candidate_raw() -> None:
    mapped = map_liepin_worker_detail(_worker_detail(), raw_payload_artifact_ref="worker://details/candidate-1.json")

    assert set(mapped.candidate.raw) == ALLOWED_DETAIL_RAW_KEYS
    assert mapped.candidate.raw["candidate_name"] == "吴**"
    assert mapped.candidate.raw["activeStatus"] == "近30天内活跃"
    assert mapped.candidate.raw["jobIntention"] == {"expectedSalary": "20-24k*14薪"}
    assert mapped.candidate.raw["projectExperienceList"] == [
        {"name": "助力C端业务增长", "summary": "structured project summary stays"}
    ]
    assert mapped.candidate.raw["sourceUrl"].startswith("https://h.liepin.com/resume/showresumedetail/")
    assert not (set(mapped.candidate.raw) & FORBIDDEN_RAW_KEYS)
    assert not (set(mapped.candidate.raw) & set(WHOLE_PAGE_TEXT_ALIASES))
    assert "Liepin private detail body" not in str(mapped.candidate.raw)
    assert "Detailed private resume text" not in str(mapped.candidate.raw)
    assert "one@example.com" not in str(mapped.candidate.raw)
    assert mapped.candidate.raw["raw_payload_artifact_ref"] == "worker://details/candidate-1.json"


@pytest.mark.parametrize("alias", WHOLE_PAGE_TEXT_ALIASES)
def test_worker_detail_rejects_whole_page_text_payload_aliases(alias: str) -> None:
    payload = _worker_detail().model_dump(mode="json")
    assert isinstance(payload["payload"], dict)
    payload["payload"][alias] = "whole page text must not cross the worker detail boundary"

    with pytest.raises(ValidationError):
        LiepinWorkerCandidateDetail.model_validate(payload)


def test_worker_detail_rejects_nested_whole_page_text_payload_aliases() -> None:
    payload = _worker_detail().model_dump(mode="json")
    assert isinstance(payload["payload"], dict)
    payload["payload"]["extra"] = {"fullText": "nested whole page text must not cross the boundary"}

    with pytest.raises(ValidationError):
        LiepinWorkerCandidateDetail.model_validate(payload)


def test_detail_mapping_sanitizes_provider_snapshot_payload() -> None:
    raw = _worker_detail().model_dump(mode="python")
    detail_payload = raw["payload"]
    assert isinstance(detail_payload, dict)
    for alias in WHOLE_PAGE_TEXT_ALIASES:
        detail_payload[alias] = "whole page text must be dropped"
    detail_payload["workExperienceList"] = [
        {
            "company": "平安好医",
            "title": "用户体验设计专家",
            "summary": "structured work summary stays",
        }
    ]
    detail = LiepinWorkerCandidateDetail.model_construct(**raw)

    mapped = map_liepin_worker_detail(detail, raw_payload_artifact_ref="worker://details/candidate-1.json")

    assert not (set(mapped.candidate.raw) & set(WHOLE_PAGE_TEXT_ALIASES))
    assert not (set(mapped.provider_snapshot.raw_payload) & set(WHOLE_PAGE_TEXT_ALIASES))
    assert mapped.candidate.raw["workExperienceList"][0]["summary"] == "structured work summary stays"
    assert mapped.provider_snapshot.raw_payload["workExperienceList"][0]["summary"] == "structured work summary stays"


def test_detail_mapping_sanitizes_nested_provider_snapshot_payload_aliases() -> None:
    raw = _worker_detail().model_dump(mode="python")
    detail_payload = raw["payload"]
    assert isinstance(detail_payload, dict)
    detail_payload["extra"] = {
        "fullText": "nested fullText must be dropped",
        "rawText": "nested rawText must be dropped",
        "summary": "nested arbitrary summary must be dropped",
        "safe_note": "non-alias nested value stays",
    }
    detail_payload["workExperienceList"] = [
        {
            "company": "平安好医",
            "title": "用户体验设计专家",
            "summary": "structured work summary stays",
            "extra": {"page_text": "nested page text must be dropped"},
        }
    ]
    detail_payload["projectExperienceList"] = [
        {
            "name": "助力C端业务增长",
            "summary": "structured project summary stays",
            "detailBody": "nested detail body must be dropped",
        }
    ]
    detail = LiepinWorkerCandidateDetail.model_construct(**raw)

    mapped = map_liepin_worker_detail(detail, raw_payload_artifact_ref="worker://details/candidate-1.json")

    serialized_payload = str(mapped.provider_snapshot.raw_payload)
    serialized_raw = str(mapped.candidate.raw)
    assert "nested fullText must be dropped" not in serialized_payload
    assert "nested rawText must be dropped" not in serialized_payload
    assert "nested arbitrary summary must be dropped" not in serialized_payload
    assert "nested page text must be dropped" not in serialized_payload
    assert "nested detail body must be dropped" not in serialized_payload
    assert "nested fullText must be dropped" not in serialized_raw
    assert "nested rawText must be dropped" not in serialized_raw
    assert "nested arbitrary summary must be dropped" not in serialized_raw
    assert "nested page text must be dropped" not in serialized_raw
    assert "nested detail body must be dropped" not in serialized_raw
    assert mapped.provider_snapshot.raw_payload["extra"] == {"safe_note": "non-alias nested value stays"}
    assert mapped.provider_snapshot.raw_payload["workExperienceList"][0]["summary"] == "structured work summary stays"
    assert mapped.provider_snapshot.raw_payload["projectExperienceList"][0]["summary"] == "structured project summary stays"
    assert mapped.candidate.raw["workExperienceList"][0]["summary"] == "structured work summary stays"
    assert mapped.candidate.raw["projectExperienceList"][0]["summary"] == "structured project summary stays"


def test_detail_mapping_derives_normalized_text_from_structured_payload() -> None:
    sentinel = "PAGE_CHROME_SHOULD_NOT_PERSIST"
    detail = _worker_detail().model_copy(update={"normalized_text": f"{sentinel} " * 2000})

    mapped = map_liepin_worker_detail(detail, raw_payload_artifact_ref="worker://details/candidate-1.json")

    for text in (mapped.candidate.search_text, mapped.provider_snapshot.normalized_text):
        assert "平安好医" in text
        assert "用户体验设计专家" in text
        assert sentinel not in text
        assert len(text) <= STRUCTURED_LIEPIN_DETAIL_TEXT_MAX_CHARS


def test_detail_mapping_does_not_fallback_to_worker_normalized_text_when_structured_text_empty() -> None:
    sentinel = "DETAIL_NORMALIZED_TEXT_SHOULD_NOT_PERSIST"
    detail = _worker_detail().model_copy(
        update={
            "payload": {"candidateId": "candidate-1", "listingId": "listing-1"},
            "normalized_text": sentinel,
        }
    )

    mapped = map_liepin_worker_detail(detail, raw_payload_artifact_ref="worker://details/candidate-1.json")

    assert mapped.candidate.search_text == ""
    assert mapped.provider_snapshot.normalized_text == ""
    assert sentinel not in mapped.candidate.search_text
    assert sentinel not in mapped.provider_snapshot.normalized_text


def test_card_mapping_returns_provider_snapshot_with_raw_payload_and_privacy_metadata() -> None:
    card = _worker_card()
    mapped = map_liepin_worker_card(card, raw_payload_artifact_ref="worker://cards/candidate-1.json")

    assert mapped.provider_snapshot.raw_payload == liepin_mapper._sanitize_liepin_provider_payload(card.payload)
    assert mapped.provider_snapshot.pii_classification == "direct_contact_possible"
    assert mapped.provider_snapshot.retention_policy == "provider_snapshot_30d"
    assert mapped.provider_snapshot.access_scope == "local_run_only"
    assert mapped.provider_snapshot.redaction_state == "raw_provider_payload"
    assert mapped.provider_snapshot.score_evidence_source == "card_only"
    assert mapped.candidate.raw["score_evidence_source"] == "card_only"


def test_detail_mapping_returns_provider_snapshot_with_raw_payload_and_privacy_metadata() -> None:
    detail = _worker_detail()
    mapped = map_liepin_worker_detail(detail, raw_payload_artifact_ref="worker://details/candidate-1.json")

    assert mapped.provider_snapshot.raw_payload == liepin_mapper._sanitize_liepin_provider_payload(detail.payload)
    assert mapped.provider_snapshot.pii_classification == "direct_contact_present"
    assert mapped.provider_snapshot.retention_policy == "provider_snapshot_7d"
    assert mapped.provider_snapshot.access_scope == "local_run_only"
    assert mapped.provider_snapshot.redaction_state == "raw_provider_payload"
    assert mapped.provider_snapshot.score_evidence_source == "detail_enriched"
    assert mapped.candidate.raw["score_evidence_source"] == "detail_enriched"


def test_worker_contracts_reject_unknown_privacy_policy_values() -> None:
    payload = _worker_card().model_dump()
    payload["pii_classification"] = "whatever_the_worker_sent"

    with pytest.raises(ValidationError):
        LiepinWorkerCandidateCard.model_validate(payload)


def test_extraction_source_tracks_origin_not_card_or_detail_kind() -> None:
    card = map_liepin_worker_card(_worker_card(), raw_payload_artifact_ref="worker://cards/candidate-1.json")
    detail = map_liepin_worker_detail(_worker_detail(), raw_payload_artifact_ref="worker://details/candidate-1.json")

    assert card.provider_snapshot.extraction_source == "network"
    assert card.provider_snapshot.score_evidence_source == "card_only"
    assert detail.provider_snapshot.extraction_source == "dom_fallback"
    assert detail.provider_snapshot.score_evidence_source == "detail_enriched"
