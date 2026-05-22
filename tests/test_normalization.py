from __future__ import annotations

from seektalent.models import ResumeCandidate
from seektalent.normalization import normalize_resume


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
