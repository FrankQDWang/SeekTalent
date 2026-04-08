from __future__ import annotations

import hashlib

from seektalent.models import HardConstraints, RequirementExtractionDraft, RequirementPreferences
from seektalent.requirements import build_input_truth, normalize_requirement_draft


def test_build_input_truth_uses_v03_field_names_and_hashes() -> None:
    truth = build_input_truth(
        job_description="Python agent engineer",
        hiring_notes="Shanghai preferred",
    )

    assert truth.model_dump() == {
        "job_description": "Python agent engineer",
        "hiring_notes": "Shanghai preferred",
        "job_description_sha256": hashlib.sha256("Python agent engineer".encode("utf-8")).hexdigest(),
        "hiring_notes_sha256": hashlib.sha256("Shanghai preferred".encode("utf-8")).hexdigest(),
    }


def test_normalize_requirement_draft_flattens_hard_constraints() -> None:
    truth = build_input_truth(
        job_description="招聘 Senior Python Agent Engineer\nBuild retrieval ranking workflows.",
        hiring_notes="上海优先。避免纯前端背景。",
    )
    draft = RequirementExtractionDraft(
        role_summary_candidate="Own retrieval ranking and CTS bridge work.",
        must_have_capability_candidates=["python", " retrieval ", "Python"],
        preferred_capability_candidates=["ranking", "cts"],
        exclusion_signal_candidates=["纯前端", " 纯前端 "],
        preference_candidates=RequirementPreferences(
            preferred_domains=["招聘科技", "招聘科技"],
            preferred_backgrounds=["搜推", "LLM"],
        ),
        hard_constraint_candidates=HardConstraints(
            locations=[" 上海 ", "上海"],
            min_years=8,
            max_years=3,
            company_names=["阿里巴巴", "阿里巴巴"],
            school_names=["复旦大学"],
            degree_requirement="本科",
            school_type_requirement=["211", "211"],
            gender_requirement="男性优先",
            min_age=35,
            max_age=28,
        ),
        scoring_rationale_candidate="Prioritize Python, retrieval, and CTS fit.",
    )

    sheet = normalize_requirement_draft(draft, input_truth=truth)

    assert sheet.role_title == "Senior Python Agent Engineer"
    assert sheet.must_have_capabilities == ["python", "retrieval"]
    assert sheet.exclusion_signals == ["纯前端"]
    assert sheet.preferences.preferred_domains == ["招聘科技"]
    assert sheet.preferences.preferred_backgrounds == ["搜推", "LLM"]
    assert sheet.hard_constraints.locations == ["上海"]
    assert sheet.hard_constraints.min_years == 3
    assert sheet.hard_constraints.max_years == 8
    assert sheet.hard_constraints.company_names == ["阿里巴巴"]
    assert sheet.hard_constraints.degree_requirement == "本科及以上"
    assert sheet.hard_constraints.school_type_requirement == ["211"]
    assert sheet.hard_constraints.gender_requirement == "男"
    assert sheet.hard_constraints.min_age == 28
    assert sheet.hard_constraints.max_age == 35
