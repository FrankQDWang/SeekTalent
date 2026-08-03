from __future__ import annotations

import pytest

from seektalent.candidate_observation_merge import (
    merge_resume_candidate_observations,
    merge_runtime_source_evidence,
)
from seektalent.models import ResumeCandidate, RuntimeSourceEvidence
from seektalent.resume_normalizers.registry import normalize_resume
from seektalent.source_references import SourceReference


SOURCE_REFERENCE = SourceReference(
    source_kind="liepin",
    display_label="猎聘",
    url="https://h.liepin.com/resume/showresumedetail/?res_id_encode=verified",
)


def _candidate(*, detail: bool) -> ResumeCandidate:
    raw: dict[str, object] = {
        "provider": "liepin",
        "candidateName": "王明",
        "currentTitle": "数据平台工程师" if detail else "",
        "currentCompany": "星河科技" if detail else "",
        "workYears": 8 if detail else None,
        "score_evidence_source": "detail" if detail else "card",
    }
    if detail:
        raw.update(
            {
                "city": "上海",
                "skills": ["Python", "Spark"],
                "workExperienceList": [
                    {
                        "company": "星河科技",
                        "title": "数据平台工程师",
                        "duration": "2021-至今",
                        "summary": "建设实时数据平台",
                    }
                ],
                "educationList": [
                    {
                        "school": "南京大学",
                        "major": "计算机科学",
                        "degree": "硕士",
                    }
                ],
            }
        )
    return ResumeCandidate(
        resume_id="resume-1",
        source_resume_id="provider-1",
        snapshot_sha256="detail-snapshot" if detail else "card-snapshot",
        dedup_key="person-1",
        expected_location="上海" if detail else None,
        work_experience_summaries=["建设实时数据平台"] if detail else [],
        source_references=(SOURCE_REFERENCE,) if detail else (),
        search_text="王明 数据平台工程师" if detail else "王明",
        raw=raw,
    )


def _evidence(*, detail: bool, provider_hash: str = "provider-hash-1") -> RuntimeSourceEvidence:
    return RuntimeSourceEvidence(
        evidence_id="evidence-1",
        source="liepin",
        provider="liepin",
        evidence_level="detail" if detail else "card",
        candidate_resume_id="resume-1",
        provider_candidate_key_hash=provider_hash,
        query_fingerprint="detail-query" if detail else "card-query",
        provider_snapshot_ref="artifact://detail" if detail else None,
        safe_summary_ref="artifact://summary",
        collected_at="2026-08-03T10:00:00Z" if detail else "2026-08-03T11:00:00Z",
        safe_reason_codes=("source_detail_candidate",) if detail else ("source_card_candidate",),
        source_references=(SOURCE_REFERENCE,) if detail else (),
    )


@pytest.mark.parametrize("rich_first", [True, False])
def test_candidate_observation_merge_is_order_independent_and_monotonic(rich_first: bool) -> None:
    rich = _candidate(detail=True)
    sparse = _candidate(detail=False)
    rich_evidence = (_evidence(detail=True),)
    sparse_evidence = (_evidence(detail=False),)
    left, right = (rich, sparse) if rich_first else (sparse, rich)
    left_evidence, right_evidence = (
        (rich_evidence, sparse_evidence) if rich_first else (sparse_evidence, rich_evidence)
    )

    merged, normalized = merge_resume_candidate_observations(
        left,
        right,
        left_evidence=left_evidence,
        right_evidence=right_evidence,
    )

    assert merged.raw["workExperienceList"] == rich.raw["workExperienceList"]
    assert merged.expected_location == "上海"
    assert merged.source_references == (SOURCE_REFERENCE,)
    assert normalized == normalize_resume(merged)
    assert normalized.completeness_score == normalize_resume(rich).completeness_score
    assert normalized.score_evidence_source == "detail"


@pytest.mark.parametrize("detail_first", [True, False])
def test_source_evidence_upsert_keeps_highest_level_and_verified_references(detail_first: bool) -> None:
    detail = _evidence(detail=True)
    card = _evidence(detail=False)
    left, right = (detail, card) if detail_first else (card, detail)

    merged = merge_runtime_source_evidence(left, right)

    assert merged.evidence_level == "detail"
    assert merged.provider_snapshot_ref == "artifact://detail"
    assert merged.source_references == (SOURCE_REFERENCE,)
    assert set(merged.safe_reason_codes) == {"source_card_candidate", "source_detail_candidate"}


def test_candidate_observation_merge_rejects_provider_hash_conflict() -> None:
    with pytest.raises(ValueError, match="candidate_observation_provider_mismatch"):
        merge_resume_candidate_observations(
            _candidate(detail=True),
            _candidate(detail=False),
            left_evidence=(_evidence(detail=True, provider_hash="provider-a"),),
            right_evidence=(_evidence(detail=False, provider_hash="provider-b"),),
        )


def test_candidate_observation_merge_preserves_earliest_query_attribution() -> None:
    rich = _candidate(detail=True).model_copy(
        update={
            "source_round": 2,
            "first_round_no": 2,
            "first_batch_no": 3,
            "first_query_instance_id": "query-later",
            "first_query_fingerprint": "fingerprint-later",
        }
    )
    sparse = _candidate(detail=False).model_copy(
        update={
            "source_round": 1,
            "first_round_no": 1,
            "first_batch_no": 1,
            "first_query_instance_id": "query-first",
            "first_query_fingerprint": "fingerprint-first",
        }
    )

    merged, _normalized = merge_resume_candidate_observations(
        rich,
        sparse,
        left_evidence=(_evidence(detail=True),),
        right_evidence=(_evidence(detail=False),),
    )

    assert merged.raw["workExperienceList"] == rich.raw["workExperienceList"]
    assert merged.source_round == 1
    assert merged.first_round_no == 1
    assert merged.first_batch_no == 1
    assert merged.first_query_instance_id == "query-first"
    assert merged.first_query_fingerprint == "fingerprint-first"


def test_source_evidence_upsert_rejects_conflicting_identity() -> None:
    with pytest.raises(ValueError, match="runtime_source_evidence_identity_mismatch"):
        merge_runtime_source_evidence(
            _evidence(detail=True, provider_hash="provider-a"),
            _evidence(detail=False, provider_hash="provider-b"),
        )
