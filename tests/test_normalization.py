from __future__ import annotations

import json

from seektalent.models import ResumeCandidate, StructuredResumeEvidence, StructuredResumeTimelineItem
from seektalent.normalization import normalize_resume


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
            },
        )
    )

    assert normalized.source_provider == "cts"


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
                "skill_tags": ["Python", "Java", "Hive"],
                "recent_experience_text": "负责数据仓库、数据治理和大规模数据处理平台建设。",
                "normalized_card_text": "数据开发 数据仓库 数据治理 Python Java 大规模数据处理",
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
    assert normalized.recent_experiences[0].summary == "负责数据仓库、数据治理和大规模数据处理平台建设。"
    assert "大规模数据处理" in normalized.raw_text_excerpt
    assert normalized.completeness_score >= 60


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
