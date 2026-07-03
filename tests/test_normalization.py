from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from seektalent.models import (
    ResumeCandidate,
    StructuredResumeEvidence,
    StructuredResumeTimelineItem,
    StructuredScoringRole,
)
from seektalent.normalization import normalize_resume
from seektalent.runtime.normalized_artifacts import normalized_resume_artifact_payload


def _candidate_with_raw(resume_id: str, raw: dict[str, object]) -> ResumeCandidate:
    return ResumeCandidate(
        resume_id=resume_id,
        source_resume_id=resume_id,
        snapshot_sha256=f"sha-{resume_id}",
        dedup_key=resume_id,
        search_text="senior ai infra engineer",
        raw=raw,
    )


def test_normalized_resume_preserves_cts_provider_from_raw() -> None:
    normalized = normalize_resume(
        _candidate_with_raw(
            "cts-1",
            {
                "provider": "cts",
                "source": "cts",
                "candidate_name": "Alice Chen",
                "current_title": "AI Infra Engineer",
                "currentCompany": "Example AI",
                "workExperienceList": [{"company": "Example AI", "title": "AI Infra Engineer", "summary": "Built agent runtime."}],
                "skills": ["Python", "Agents"],
            },
        )
    )

    assert normalized.source_provider == "cts"
    scoring = normalized.structured_evidence.to_scoring_evidence()
    assert scoring.current_role.title == "AI Infra Engineer"
    assert scoring.current_role.company == "Example AI"
    assert scoring.work_experience[0].summary == "Built agent runtime."
    assert scoring.skills == ["Python", "Agents"]


def test_normalized_resume_preserves_liepin_provider_from_raw() -> None:
    normalized = normalize_resume(
        _candidate_with_raw(
            "liepin-1",
            {
                "provider": "liepin",
                "source": "liepin",
                "safe_card_summary": {"display_title": "AI Agent Engineer"},
            },
        )
    )

    assert normalized.source_provider == "liepin"


def test_liepin_safe_card_summary_feeds_normalized_resume() -> None:
    candidate = ResumeCandidate(
        resume_id="liepin-card-1",
        dedup_key="dedup-liepin-card-1",
        search_text="数据开发 数据仓库 数据治理 Python Java 大规模数据处理",
        raw={
            "provider": "liepin",
            "safe_card_summary": {
                "display_title": "高级数据开发工程师",
                "current_or_recent_company": "业务线科技公司",
                "current_or_recent_title": "数据开发工程师",
                "work_years": 8,
                "city": "上海",
                "expected_city": "杭州",
                "education_level": "硕士",
                "school_names": ["华东理工大学"],
                "major_names": ["计算机科学"],
                "skill_tags": ["Python", "Java", "Hive", "数据仓库", "数据治理", "大规模数据处理"],
                "experience_preview": [
                    {
                        "company": "业务线科技公司",
                        "title": "数据开发工程师",
                        "date_range": "2019.01-至今",
                        "duration": "5年",
                    }
                ],
            },
        },
    )

    normalized = normalize_resume(candidate)

    assert normalized.current_title == "数据开发工程师"
    assert normalized.current_company == "业务线科技公司"
    assert normalized.years_of_experience == 8
    assert "上海" in normalized.locations
    assert "硕士" in normalized.education_summary
    assert "Python" in normalized.skills
    assert normalized.recent_experiences[0].company == "业务线科技公司"
    assert normalized.recent_experiences[0].title == "数据开发工程师"
    assert normalized.recent_experiences[0].duration == "2019.01-至今"
    assert "大规模数据处理" in normalized.raw_text_excerpt
    serialized = json.dumps(normalized.model_dump(mode="json"), ensure_ascii=False)
    assert "normalized_card_text" not in serialized
    assert "visible_text" not in serialized
    assert normalized.completeness_score >= 60


def test_liepin_normalizer_uses_card_experience_preview_not_normalized_card_text() -> None:
    candidate = ResumeCandidate(
        resume_id="liepin-card-preview-1",
        dedup_key="dedup-liepin-card-preview-1",
        search_text="structured search text",
        raw={
            "provider": "liepin",
            "safe_card_summary": {
                "current_or_recent_company": "结构化科技",
                "current_or_recent_title": "AI平台工程师",
                "skill_tags": ["Python", "RAG"],
                "normalized_card_text": "SENTINEL legacy card text",
                "experience_preview": [
                    {
                        "company": "结构化科技",
                        "title": "AI平台工程师",
                        "date_range": "2021.04-至今",
                        "duration": "3年",
                    }
                ],
            },
        },
    )

    normalized = normalize_resume(candidate)
    serialized = json.dumps(normalized.model_dump(mode="json"), ensure_ascii=False)

    assert normalized.current_title == "AI平台工程师"
    assert normalized.recent_experiences[0].title == "AI平台工程师"
    assert normalized.recent_experiences[0].company == "结构化科技"
    assert "SENTINEL legacy card text" not in serialized


def test_liepin_detail_candidate_reuses_shared_structured_resume_normalization() -> None:
    candidate = ResumeCandidate(
        resume_id="liepin-detail-1",
        dedup_key="dedup-liepin-detail-1",
        search_text="数据开发专家 数据仓库 数据治理 Python Hive Spark",
        raw={
            "provider": "liepin",
            "score_evidence_source": "detail_enriched",
            "candidate_name": "张三",
            "currentTitle": "数据开发专家",
            "currentCompany": "Example Data",
            "workExperienceList": [
                {
                    "company": "Example Data",
                    "title": "数据开发专家",
                    "duration": "2020.01-至今",
                    "summary": "建设大规模数据平台、数据治理和 ETL 链路。",
                }
            ],
            "educationList": [{"school": "北京大学", "degree": "本科", "speciality": "计算机"}],
            "skills": ["Python", "Hive", "Spark"],
            "locations": ["北京"],
        },
    )

    normalized = normalize_resume(candidate)

    assert normalized.candidate_name == "张三"
    assert normalized.current_title == "数据开发专家"
    assert normalized.current_company == "Example Data"
    assert normalized.education_summary == "北京大学 计算机 本科"
    assert "Python" in normalized.skills
    assert "北京" in normalized.locations
    assert "大规模数据平台" in normalized.raw_text_excerpt
    assert normalized.score_evidence_source == "detail_enriched"
    assert normalized.completeness_score >= 80


def test_liepin_detail_without_full_text_still_produces_legacy_excerpt() -> None:
    candidate = ResumeCandidate(
        resume_id="liepin-detail-structured-1",
        dedup_key="dedup-liepin-detail-structured-1",
        search_text="用户体验设计专家 平安好医 用户研究 交互设计",
        raw={
            "provider": "liepin",
            "score_evidence_source": "detail_enriched",
            "candidate_name": "潘**",
            "currentTitle": "资深体验设计工程师",
            "currentCompany": "平安集团",
            "workExperienceList": [
                {
                    "company": "平安好医",
                    "title": "用户体验设计专家",
                    "duration": "2019.06-至今",
                    "summary": "提供B端及C端体验设计方案。",
                }
            ],
            "skills": ["用户研究", "交互设计"],
        },
    )

    normalized = normalize_resume(candidate)

    assert normalized.raw_text_excerpt
    assert "平安好医" in normalized.raw_text_excerpt
    assert "fullText" not in normalized.raw_text_excerpt


def test_normalization_dispatches_liepin_to_liepin_normalizer(monkeypatch) -> None:
    from seektalent.resume_normalizers import registry

    called: list[str] = []

    def fake_liepin(candidate: ResumeCandidate):
        called.append(candidate.resume_id)
        return registry._legacy_normalize_resume(candidate).model_copy(update={"source_provider": "liepin"})

    monkeypatch.setitem(registry.NORMALIZERS, "liepin", fake_liepin)

    normalized = normalize_resume(
        ResumeCandidate(
            resume_id="liepin-dispatch-1",
            dedup_key="liepin-dispatch-1",
            search_text="用户体验设计",
            raw={"provider": "liepin", "currentTitle": "AI Agent Engineer"},
        )
    )

    assert called == ["liepin-dispatch-1"]
    assert normalized.source_provider == "liepin"


def test_normalization_dispatches_cts_to_cts_normalizer(monkeypatch) -> None:
    from seektalent.resume_normalizers import registry

    called: list[str] = []

    def fake_cts(candidate: ResumeCandidate):
        called.append(candidate.resume_id)
        return registry._legacy_normalize_resume(candidate).model_copy(update={"source_provider": "cts"})

    monkeypatch.setitem(registry.NORMALIZERS, "cts", fake_cts)

    normalized = normalize_resume(
        ResumeCandidate(
            resume_id="cts-dispatch-1",
            dedup_key="cts-dispatch-1",
            search_text="AI Infra Engineer",
            raw={"provider": "cts", "current_title": "AI Infra Engineer"},
        )
    )

    assert called == ["cts-dispatch-1"]
    assert normalized.source_provider == "cts"


def test_liepin_normalization_uses_structured_evidence_without_whole_page_text() -> None:
    candidate = ResumeCandidate(
        resume_id="liepin-detail-structured-1",
        dedup_key="liepin-detail-structured-1",
        search_text="用户体验设计 用户研究 交互设计",
        raw={
            "provider": "liepin",
            "candidate_name": "吴**",
            "activeStatus": "近30天内活跃",
            "jobStatus": "在职，看看新机会",
            "age": 32,
            "gender": "男",
            "city": "上海",
            "education": "本科",
            "workYears": 10,
            "currentTitle": "资深体验设计工程师",
            "currentCompany": "平安集团",
            "jobIntention": {"expectedSalary": "20-24k*14薪", "expectedCity": "上海"},
            "workExperienceList": [
                {"company": "平安好医", "title": "用户体验设计专家", "summary": "负责 B 端和 C 端体验设计。"}
            ],
            "projectExperienceList": [{"name": "增长项目", "summary": "通过用户研究优化转化。"}],
            "educationList": [{"school": "华东师范大学", "degree": "硕士", "major": "设计学"}],
            "skills": ["用户研究", "交互设计"],
        },
    )

    normalized = normalize_resume(candidate)

    assert normalized.source_provider == "liepin"
    assert normalized.structured_evidence.current_role["title"] == "资深体验设计工程师"
    assert normalized.structured_evidence.work_experience[0].company == "平安好医"
    assert normalized.structured_evidence.project_experience[0].name == "增长项目"
    assert normalized.structured_evidence.education_experience[0].school == "华东师范大学"
    assert normalized.raw_text_excerpt
    assert "fullText" not in normalized.raw_text_excerpt
    assert normalized.completeness_score == 100


def test_liepin_normalized_artifact_excludes_legacy_raw_text_excerpt() -> None:
    normalized = normalize_resume(
        ResumeCandidate(
            resume_id="liepin-detail-artifact-1",
            dedup_key="liepin-detail-artifact-1",
            search_text="用户体验设计 用户研究 交互设计",
            raw={
                "provider": "liepin",
                "currentTitle": "资深体验设计工程师",
                "currentCompany": "平安集团",
                "workExperienceList": [
                    {"company": "平安好医", "title": "用户体验设计专家", "summary": "负责 B 端和 C 端体验设计。"}
                ],
                "projectExperienceList": [{"name": "增长项目", "summary": "通过用户研究优化转化。"}],
                "skills": ["用户研究", "交互设计"],
            },
        )
    )

    payload = normalized_resume_artifact_payload(normalized)

    assert normalized.raw_text_excerpt
    assert "raw_text_excerpt" not in payload
    assert payload["structured_evidence"]


def test_cts_normalized_artifact_keeps_cts_raw_text_excerpt() -> None:
    normalized = normalize_resume(
        ResumeCandidate(
            resume_id="cts-artifact-1",
            dedup_key="cts-artifact-1",
            search_text="Python backend engineer",
            raw={
                "provider": "cts",
                "currentTitle": "Python Engineer",
                "workExperienceList": [
                    {"company": "Example Co", "title": "Python Engineer", "summary": "Built retrieval workflows."}
                ],
            },
        )
    )

    payload = normalized_resume_artifact_payload(normalized)

    assert payload["raw_text_excerpt"]


def test_liepin_normalization_rejects_whole_page_text_keys() -> None:
    candidate = ResumeCandidate(
        resume_id="liepin-bad-text-1",
        dedup_key="liepin-bad-text-1",
        search_text="用户体验设计",
        raw={"provider": "liepin", "fullText": "whole page text"},
    )

    with pytest.raises(ValueError, match="whole-page text"):
        normalize_resume(candidate)


@pytest.mark.parametrize("key", ["summary", "profile"])
def test_liepin_normalization_rejects_generic_whole_page_text_keys(key: str) -> None:
    candidate = ResumeCandidate(
        resume_id=f"liepin-bad-{key}-1",
        dedup_key=f"liepin-bad-{key}-1",
        search_text="用户体验设计",
        raw={"provider": "liepin", key: "whole page text"},
    )

    with pytest.raises(ValueError, match="whole-page text"):
        normalize_resume(candidate)


def test_liepin_normalization_accepts_camel_case_safe_card_summary() -> None:
    candidate = ResumeCandidate(
        resume_id="liepin-camel-safe-card-1",
        dedup_key="liepin-camel-safe-card-1",
        search_text="数据开发 数据治理 Python",
        raw={
            "provider": "liepin",
            "safeCardSummary": {
                "display_title": "高级数据开发工程师",
                "current_or_recent_company": "业务线科技公司",
                "current_or_recent_title": "数据开发工程师",
                "work_years": 8,
                "city": "上海",
                "skill_tags": ["Python", "Hive"],
                "experience_preview": [
                    {
                        "company": "业务线科技公司",
                        "title": "数据开发工程师",
                        "date_range": "2020.01-至今",
                        "duration": "4年",
                    }
                ],
            },
        },
    )

    normalized = normalize_resume(candidate)

    assert normalized.current_title == "数据开发工程师"
    assert normalized.current_company == "业务线科技公司"
    assert normalized.years_of_experience == 8
    assert "上海" in normalized.locations
    assert "Python" in normalized.skills
    assert normalized.recent_experiences[0].company == "业务线科技公司"
    assert normalized.recent_experiences[0].title == "数据开发工程师"


def test_unknown_source_with_liepin_text_alias_rejects_instead_of_cts_fallback() -> None:
    candidate = ResumeCandidate(
        resume_id="unknown-liepin-bad-text-1",
        dedup_key="unknown-liepin-bad-text-1",
        search_text="用户体验设计",
        raw={"fullText": "old Liepin whole page text"},
    )

    with pytest.raises(ValueError, match="Unsupported or unmigrated Liepin-shaped resume payload"):
        normalize_resume(candidate)


@pytest.mark.parametrize("raw", [{"summary": "ordinary summary"}, {"profile": "ordinary profile"}, {"summary": "s", "profile": "p"}])
def test_unknown_source_with_generic_summary_or_profile_uses_cts_fallback(raw: dict[str, object]) -> None:
    candidate = ResumeCandidate(
        resume_id="unknown-generic-summary-profile-1",
        dedup_key="unknown-generic-summary-profile-1",
        search_text="Python backend engineer",
        raw=raw,
    )

    normalized = normalize_resume(candidate)

    assert normalized.source_provider is None


def test_old_liepin_fixture_with_source_url_without_provider_must_be_migrated() -> None:
    candidate = ResumeCandidate(
        resume_id="old-liepin-fixture-1",
        dedup_key="old-liepin-fixture-1",
        search_text="用户体验设计",
        raw={
            "sourceUrl": "https://h.liepin.com/resume/showresumedetail/?res_id_encode=abc",
            "currentTitle": "资深体验设计工程师",
            "workExperienceList": [{"company": "平安好医"}],
        },
    )

    with pytest.raises(ValueError, match="Unsupported or unmigrated Liepin-shaped resume payload"):
        normalize_resume(candidate)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("sourceUrl", "https://h.liepin.com/resume/showresumedetail/?res_id_encode=abc"),
        ("safeCardSummary", {"display_title": "资深体验设计工程师"}),
        ("safe_card_summary", {"display_title": "资深体验设计工程师"}),
    ],
)
def test_unknown_source_with_liepin_structured_aliases_rejects_instead_of_cts_fallback(
    key: str, value: object
) -> None:
    candidate = ResumeCandidate(
        resume_id=f"unknown-liepin-{key}",
        dedup_key=f"unknown-liepin-{key}",
        search_text="用户体验设计",
        raw={key: value},
    )

    with pytest.raises(ValueError, match="Unsupported or unmigrated Liepin-shaped resume payload"):
        normalize_resume(candidate)


@pytest.mark.parametrize(
    "raw",
    [
        {"candidateName": "Candidate A"},
        {"currentTitle": "Backend Engineer"},
        {"currentCompany": "Example Co"},
        {"activeStatus": "active"},
        {"workYears": 8},
    ],
)
def test_unknown_source_with_single_common_cts_fields_uses_cts_fallback(raw: dict[str, object]) -> None:
    candidate = ResumeCandidate(
        resume_id="unknown-common-cts-field-1",
        dedup_key="unknown-common-cts-field-1",
        search_text="Python backend engineer",
        raw=raw,
    )

    normalized = normalize_resume(candidate)

    assert normalized.source_provider is None


def test_unknown_source_with_non_liepin_source_url_uses_cts_fallback() -> None:
    candidate = ResumeCandidate(
        resume_id="unknown-generic-source-url-1",
        dedup_key="unknown-generic-source-url-1",
        search_text="Python backend engineer",
        raw={"sourceUrl": "https://notliepin.com/resume/123"},
    )

    normalized = normalize_resume(candidate)

    assert normalized.source_provider is None


def test_unregistered_non_liepin_source_uses_cts_fallback() -> None:
    candidate = ResumeCandidate(
        resume_id="fixture-source-1",
        dedup_key="fixture-source-1",
        search_text="Python data platform engineer",
        raw={"source": "fixture_source", "currentTitle": "Data Platform Engineer"},
    )

    normalized = normalize_resume(candidate)

    assert normalized.source_provider == "fixture_source"
    assert normalized.current_title == "Data Platform Engineer"


def test_unknown_source_with_generic_cts_timeline_lists_uses_cts_fallback() -> None:
    candidate = ResumeCandidate(
        resume_id="generic-cts-timeline-1",
        dedup_key="generic-cts-timeline-1",
        search_text="Python backend engineer",
        raw={
            "workExperienceList": [
                {"company": "Example Co", "title": "Python Engineer", "summary": "Built retrieval workflows."}
            ],
            "educationList": [{"school": "Fudan University", "degree": "Bachelor", "speciality": "CS"}],
            "skills": ["Python", "Retrieval"],
        },
    )

    normalized = normalize_resume(candidate)

    assert normalized.source_provider is None
    assert normalized.recent_experiences[0].company == "Example Co"
    assert normalized.education_summary == "Fudan University CS Bachelor"


def test_unknown_source_with_generic_current_title_and_work_experience_uses_cts_fallback() -> None:
    candidate = ResumeCandidate(
        resume_id="generic-cts-current-title-timeline-1",
        dedup_key="generic-cts-current-title-timeline-1",
        search_text="Python backend engineer",
        raw={
            "currentTitle": "Python Engineer",
            "workExperienceList": [
                {"company": "Example Co", "title": "Python Engineer", "summary": "Built retrieval workflows."}
            ],
        },
    )

    normalized = normalize_resume(candidate)

    assert normalized.source_provider is None
    assert normalized.current_title == "Python Engineer"
    assert normalized.recent_experiences[0].company == "Example Co"


def test_cts_normalizer_ignores_liepin_safe_card_summary() -> None:
    candidate = ResumeCandidate(
        resume_id="cts-safe-card-ignored-1",
        dedup_key="cts-safe-card-ignored-1",
        search_text="Python backend engineer",
        raw={
            "provider": "cts",
            "skillTags": ["should-not-fill-skill"],
            "safe_card_summary": {
                "display_title": "should-not-fill-title",
                "current_or_recent_company": "should-not-fill-company",
                "work_years": 8,
                "city": "上海",
                "education_level": "硕士",
                "skill_tags": ["should-not-fill-skill"],
                "recent_experience_text": "should-not-fill-experience",
                "normalized_card_text": "should-not-fill-excerpt",
            },
        },
    )

    normalized = normalize_resume(candidate)
    serialized = json.dumps(normalized.model_dump(mode="json"), ensure_ascii=False)

    assert normalized.source_provider == "cts"
    assert normalized.current_title == ""
    assert normalized.current_company == ""
    assert normalized.years_of_experience is None
    assert normalized.locations == []
    assert normalized.education_summary == ""
    assert normalized.skills == []
    assert normalized.recent_experiences == []
    assert "should-not-fill" not in serialized
    assert not any("Liepin safe card" in note or "safe card summary" in note for note in normalized.normalization_notes)


def test_structured_resume_evidence_derives_scoring_evidence_without_protected_fields() -> None:
    evidence = StructuredResumeEvidence(
        identity={"candidateName": "吴**", "age": 32, "gender": "男"},
        current_role={"title": "资深体验设计工程师", "company": "平安集团", "workYears": 10},
        job_intention={"expectedRole": "体验设计", "expectedCity": "上海", "expectedSalary": "20-24k"},
        work_experience=[
            StructuredResumeTimelineItem(
                company="平安好医",
                title="用户体验设计专家",
                duration="2019.06-至今",
                summary="负责 B 端和 C 端体验设计。",
            )
        ],
        project_experience=[StructuredResumeTimelineItem(name="增长项目", summary="通过用户研究优化转化。")],
        education_experience=[StructuredResumeTimelineItem(school="华东师范大学", degree="硕士", major="设计学")],
        skills=["用户研究", "交互设计"],
        source_metadata={"sourceUrl": "https://h.liepin.com/resume/showresumedetail/abc"},
    )

    scoring = evidence.to_scoring_evidence().model_dump(mode="json")
    serialized = json.dumps(scoring, ensure_ascii=False)

    assert "平安好医" in serialized
    assert "增长项目" in serialized
    assert "用户研究" in serialized
    assert "吴**" not in serialized
    assert '"name":' not in serialized
    assert "age" not in serialized
    assert "gender" not in serialized
    assert "sourceUrl" not in serialized
    assert "华东师范大学" not in serialized
    assert "硕士" not in serialized
    assert "设计学" not in serialized


def test_structured_resume_evidence_preserves_zero_work_years() -> None:
    evidence = StructuredResumeEvidence(current_role={"title": "应届体验设计师", "workYears": 0})

    scoring = evidence.to_scoring_evidence()

    assert scoring.current_role.work_years == 0


def test_structured_resume_evidence_scrubs_protected_values_from_summaries_only() -> None:
    evidence = StructuredResumeEvidence(
        identity={"candidateName": "吴**", "age": 32, "gender": "男"},
        work_experience=[
            StructuredResumeTimelineItem(
                company="平安好医",
                title="用户体验设计专家",
                summary=(
                    "吴**在平安好医负责 B 端和 C 端体验设计，"
                    "教育经历为华东师范大学硕士设计学。"
                    "详情见 https://h.liepin.com/resume/showresumedetail/abc。"
                ),
            )
        ],
        project_experience=[
            StructuredResumeTimelineItem(
                name="增长项目",
                summary="增长项目中吴**通过用户研究优化转化，参考华东师范大学经历。",
            )
        ],
        education_experience=[StructuredResumeTimelineItem(school="华东师范大学", degree="硕士", major="设计学")],
        source_metadata={"sourceUrl": "https://h.liepin.com/resume/showresumedetail/abc"},
    )

    scoring = evidence.to_scoring_evidence().model_dump(mode="json")
    serialized = json.dumps(scoring, ensure_ascii=False)

    assert "平安好医" in serialized
    assert "增长项目" in serialized
    assert "负责 B 端和 C 端体验设计" in serialized
    assert "通过用户研究优化转化" in serialized
    assert "吴**" not in serialized
    assert "https://h.liepin.com/resume/showresumedetail/abc" not in serialized
    assert "华东师范大学" not in serialized
    assert "硕士" not in serialized
    assert "设计学" not in serialized


def test_structured_resume_evidence_scrubs_age_and_gender_values_from_summaries() -> None:
    evidence = StructuredResumeEvidence(
        identity={"age": 32, "gender": "男"},
        work_experience=[
            StructuredResumeTimelineItem(
                company="平安好医",
                summary="候选人32岁，男，负责 B 端体验设计。",
            )
        ],
        project_experience=[
            StructuredResumeTimelineItem(
                name="增长项目",
                summary="项目记录32和男，通过用户研究优化转化。",
            )
        ],
    )

    scoring = evidence.to_scoring_evidence().model_dump(mode="json")
    serialized = json.dumps(scoring, ensure_ascii=False)

    assert "负责 B 端体验设计" in serialized
    assert "通过用户研究优化转化" in serialized
    assert "32" not in serialized
    assert "男" not in serialized


def test_structured_resume_evidence_keeps_age_and_gender_substrings_in_allowed_words() -> None:
    evidence = StructuredResumeEvidence(
        identity={"age": 23, "gender": "男"},
        work_experience=[
            StructuredResumeTimelineItem(
                company="平安好医",
                summary="2023年负责男装业务增长，转化率提升23%。",
            )
        ],
        project_experience=[
            StructuredResumeTimelineItem(
                name="增长项目",
                summary="男装业务在2023年通过用户研究优化转化23%。",
            )
        ],
    )

    scoring = evidence.to_scoring_evidence().model_dump(mode="json")
    serialized = json.dumps(scoring, ensure_ascii=False)

    assert "2023年" in serialized
    assert "23%" in serialized
    assert "男装业务" in serialized


def test_structured_resume_evidence_scrubs_gender_after_chinese_linking_particle() -> None:
    evidence = StructuredResumeEvidence(
        identity={"gender": "男"},
        work_experience=[
            StructuredResumeTimelineItem(
                company="平安好医",
                summary="候选人为男，负责体验设计。",
            )
        ],
        project_experience=[
            StructuredResumeTimelineItem(
                name="增长项目",
                summary="男装业务通过用户研究优化转化。",
            )
        ],
    )

    scoring = evidence.to_scoring_evidence().model_dump(mode="json")
    serialized = json.dumps(scoring, ensure_ascii=False)

    assert "负责体验设计" in serialized
    assert "男装业务" in serialized
    assert "候选人为男" not in serialized


def test_structured_resume_evidence_scrubs_gender_after_gender_label() -> None:
    evidence = StructuredResumeEvidence(
        identity={"gender": "男"},
        work_experience=[
            StructuredResumeTimelineItem(
                company="平安好医",
                summary="性别男，负责体验设计。",
            )
        ],
        project_experience=[StructuredResumeTimelineItem(name="增长项目", summary="男装业务增长。")],
    )

    scoring = evidence.to_scoring_evidence().model_dump(mode="json")
    serialized = json.dumps(scoring, ensure_ascii=False)

    assert "负责体验设计" in serialized
    assert "男装业务" in serialized
    assert "性别男" not in serialized


def test_structured_resume_evidence_scrubs_gender_after_age_expression() -> None:
    evidence = StructuredResumeEvidence(
        identity={"age": 32, "gender": "男"},
        work_experience=[
            StructuredResumeTimelineItem(
                company="平安好医",
                summary="32岁男，负责体验设计。",
            )
        ],
    )

    scoring = evidence.to_scoring_evidence().model_dump(mode="json")
    serialized = json.dumps(scoring, ensure_ascii=False)

    assert "负责体验设计" in serialized
    assert "32岁男" not in serialized
    assert "男" not in serialized


def test_structured_resume_evidence_scrubs_derived_male_marker_after_linking_particle() -> None:
    evidence = StructuredResumeEvidence(
        identity={"gender": "男"},
        work_experience=[
            StructuredResumeTimelineItem(
                company="平安好医",
                summary="候选人为男性，负责体验设计。",
            )
        ],
    )

    scoring = evidence.to_scoring_evidence().model_dump(mode="json")
    serialized = json.dumps(scoring, ensure_ascii=False)

    assert "负责体验设计" in serialized
    assert "男性" not in serialized


def test_structured_resume_evidence_scrubs_derived_male_marker_after_age_expression() -> None:
    evidence = StructuredResumeEvidence(
        identity={"age": 32, "gender": "男"},
        work_experience=[
            StructuredResumeTimelineItem(
                company="平安好医",
                summary="32岁男性，负责体验设计。",
            )
        ],
    )

    scoring = evidence.to_scoring_evidence().model_dump(mode="json")
    serialized = json.dumps(scoring, ensure_ascii=False)

    assert "负责体验设计" in serialized
    assert "32岁男性" not in serialized
    assert "男性" not in serialized


def test_structured_resume_evidence_rejects_boolean_work_years() -> None:
    with pytest.raises(ValidationError):
        StructuredResumeEvidence(current_role={"workYears": False})


def test_structured_resume_evidence_preserves_generic_major_word_in_allowed_summary() -> None:
    evidence = StructuredResumeEvidence(
        education_experience=[StructuredResumeTimelineItem(major="设计")],
        work_experience=[
            StructuredResumeTimelineItem(
                company="平安好医",
                summary="负责 B 端和 C 端体验设计。",
            )
        ],
    )

    scoring = evidence.to_scoring_evidence().model_dump(mode="json")
    serialized = json.dumps(scoring, ensure_ascii=False)

    assert "体验设计" in serialized


def test_structured_scoring_role_rejects_boolean_work_years() -> None:
    with pytest.raises(ValidationError):
        StructuredScoringRole(work_years=False)


def test_structured_resume_evidence_scrubs_short_degree_in_education_context() -> None:
    evidence = StructuredResumeEvidence(
        education_experience=[StructuredResumeTimelineItem(degree="硕士")],
        work_experience=[
            StructuredResumeTimelineItem(
                company="平安好医",
                summary="硕士学历，负责 B 端体验设计。",
            )
        ],
    )

    scoring = evidence.to_scoring_evidence().model_dump(mode="json")
    serialized = json.dumps(scoring, ensure_ascii=False)

    assert "负责 B 端体验设计" in serialized
    assert "硕士" not in serialized


def test_structured_resume_evidence_scrubs_short_major_only_in_education_context() -> None:
    evidence = StructuredResumeEvidence(
        education_experience=[StructuredResumeTimelineItem(major="设计")],
        work_experience=[
            StructuredResumeTimelineItem(
                company="平安好医",
                summary="设计专业背景，负责体验设计。",
            )
        ],
    )

    scoring = evidence.to_scoring_evidence().model_dump(mode="json")
    serialized = json.dumps(scoring, ensure_ascii=False)

    assert "体验设计" in serialized
    assert "设计专业" not in serialized
