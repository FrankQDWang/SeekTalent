import asyncio
from pathlib import Path

import pytest

from seektalent.models import RequirementExtractionDraft, RequirementSheet
from seektalent.prompting import LoadedPrompt
from seektalent.requirements import build_input_truth, build_scoring_policy, normalize_requirement_draft
from seektalent.requirements.extractor import RequirementExtractor, requirement_cache_key
from seektalent.runtime.exact_llm_cache import put_cached_json
from seektalent.tracing import ProviderUsageSnapshot
from tests.settings_factory import make_settings


def _valid_requirement_draft() -> RequirementExtractionDraft:
    return RequirementExtractionDraft(
        role_title="Senior Python Engineer",
        title_anchor_terms=["Python"],
        title_anchor_rationale="Python is the stable searchable anchor from the title.",
        jd_query_terms=["Retrieval Systems"],
        role_summary="Build retrieval and ranking capabilities.",
        must_have_capabilities=["Python"],
        scoring_rationale="Prioritize Python and retrieval depth.",
    )


def _fake_usage_result(output: RequirementExtractionDraft):
    class FakeUsage:
        input_tokens = 12
        output_tokens = 4
        total_tokens = 16
        cache_read_tokens = 8
        cache_write_tokens = 2
        details = {"reasoning_tokens": 6}

    class FakeResult:
        def __init__(self, output: RequirementExtractionDraft) -> None:
            self.output = output

        def usage(self) -> FakeUsage:
            return FakeUsage()

    return FakeResult(output)


def _provider_usage(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    reasoning_tokens: int = 0,
) -> ProviderUsageSnapshot:
    return ProviderUsageSnapshot(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        details={"reasoning_tokens": reasoning_tokens} if reasoning_tokens else {},
    )


def test_normalize_requirement_draft_covers_standard_slots() -> None:
    requirement_sheet = normalize_requirement_draft(
        RequirementExtractionDraft(
            role_title="高级 Python 工程师",
            title_anchor_terms=["Python"],
            title_anchor_rationale="Python is the stable searchable anchor from the title.",
            jd_query_terms=["检索", "后端"],
            role_summary="负责 Python 后端和检索链路建设。",
            must_have_capabilities=["Python", "检索", "后端"],
            preferred_capabilities=["招聘领域", "trace"],
            exclusion_signals=["不考虑纯前端背景"],
            locations=["上海"],
            school_names=["复旦大学", "上海交通大学"],
            degree_requirement="本科及以上",
            school_type_requirement=["985", "211", "双一流"],
            experience_requirement="3-5年",
            gender_requirement="不限",
            age_requirement="35岁以下",
            company_names=["阿里巴巴", "蚂蚁集团"],
            preferred_companies=["字节跳动", "腾讯"],
            preferred_domains=["招聘"],
            preferred_backgrounds=["做过人才搜索"],
            preferred_query_terms=["resume matching", "trace"],
            scoring_rationale="优先评估 Python、检索和招聘相关性。",
        ),
        job_title="高级 Python 工程师",
    )

    assert requirement_sheet.role_title == "高级 Python 工程师"
    assert requirement_sheet.title_anchor_terms == ["Python"]
    assert requirement_sheet.initial_query_term_pool[0].term == "Python"
    assert requirement_sheet.initial_query_term_pool[1].term == "检索"
    assert requirement_sheet.hard_constraints.locations == ["上海"]
    assert requirement_sheet.hard_constraints.degree_requirement is not None
    assert requirement_sheet.hard_constraints.degree_requirement.canonical_degree == "本科及以上"
    assert requirement_sheet.hard_constraints.school_type_requirement is not None
    assert requirement_sheet.hard_constraints.school_type_requirement.canonical_types == ["985", "211", "双一流"]
    assert requirement_sheet.hard_constraints.school_names == ["复旦大学", "上海交通大学"]
    assert requirement_sheet.hard_constraints.experience_requirement is not None
    assert requirement_sheet.hard_constraints.experience_requirement.min_years == 3
    assert requirement_sheet.hard_constraints.experience_requirement.max_years == 5
    assert requirement_sheet.hard_constraints.age_requirement is not None
    assert requirement_sheet.hard_constraints.age_requirement.max_age == 35
    assert requirement_sheet.hard_constraints.gender_requirement is not None
    assert requirement_sheet.hard_constraints.gender_requirement.canonical_gender == "不限"
    assert requirement_sheet.hard_constraints.company_names == ["阿里巴巴", "蚂蚁集团"]
    assert requirement_sheet.preferences.preferred_companies == ["字节跳动", "腾讯"]
    assert requirement_sheet.preferences.preferred_query_terms == ["resume matching", "trace"]
    assert "不考虑纯前端背景" in requirement_sheet.exclusion_signals


def test_normalize_requirement_draft_preserves_explicit_unlimited_constraints() -> None:
    requirement_sheet = normalize_requirement_draft(
        RequirementExtractionDraft(
            role_title="Python 工程师",
            title_anchor_terms=["Python"],
            title_anchor_rationale="Python is the stable searchable anchor from the title.",
            jd_query_terms=["服务开发"],
            role_summary="负责 Python 服务开发。",
            must_have_capabilities=["Python"],
            locations=["上海"],
            degree_requirement="不限",
            experience_requirement="经验不限",
            age_requirement="年龄不限",
            gender_requirement="男女不限",
            scoring_rationale="先看 Python 相关性。",
        ),
        job_title="Python 工程师",
    )

    assert requirement_sheet.hard_constraints.degree_requirement is not None
    assert requirement_sheet.hard_constraints.degree_requirement.canonical_degree == "不限"
    assert requirement_sheet.hard_constraints.experience_requirement is not None
    assert requirement_sheet.hard_constraints.experience_requirement.min_years is None
    assert requirement_sheet.hard_constraints.experience_requirement.max_years is None
    assert requirement_sheet.hard_constraints.age_requirement is not None
    assert requirement_sheet.hard_constraints.age_requirement.min_age is None
    assert requirement_sheet.hard_constraints.age_requirement.max_age is None
    assert requirement_sheet.hard_constraints.gender_requirement is not None
    assert requirement_sheet.hard_constraints.gender_requirement.canonical_gender == "不限"


def test_build_scoring_policy_returns_frozen_copy() -> None:
    requirement_sheet = normalize_requirement_draft(
        RequirementExtractionDraft(
            role_title="Python 工程师",
            title_anchor_terms=["Python"],
            title_anchor_rationale="Python is the stable searchable anchor from the title.",
            jd_query_terms=["服务开发"],
            role_summary="负责 Python 服务开发。",
            must_have_capabilities=["Python"],
            locations=["上海"],
            preferred_domains=["招聘"],
            scoring_rationale="先看 Python 相关性。",
        ),
        job_title="Python 工程师",
    )
    scoring_policy = build_scoring_policy(requirement_sheet)

    requirement_sheet.must_have_capabilities.append("new capability")
    requirement_sheet.hard_constraints.locations.append("北京市")
    requirement_sheet.preferences.preferred_domains.append("new domain")

    assert "new capability" not in scoring_policy.must_have_capabilities
    assert scoring_policy.hard_constraints.locations == ["上海"]
    assert "new domain" not in scoring_policy.preferences.preferred_domains


def test_normalize_requirement_draft_handles_common_alias_phrasings() -> None:
    requirement_sheet = normalize_requirement_draft(
        RequirementExtractionDraft(
            role_title="算法工程师",
            title_anchor_terms=["算法"],
            title_anchor_rationale="算法 is the stable searchable anchor from the title.",
            jd_query_terms=["Python"],
            role_summary="负责算法系统建设。",
            must_have_capabilities=["Python"],
            degree_requirement="全日制本科",
            school_type_requirement=["211院校", "QS前100"],
            experience_requirement="三到五年",
            gender_requirement="男性优先",
            age_requirement="三十五岁以下",
            scoring_rationale="先看算法和 Python 相关性。",
        ),
        job_title="算法工程师",
    )

    assert requirement_sheet.hard_constraints.degree_requirement is not None
    assert requirement_sheet.hard_constraints.degree_requirement.canonical_degree == "本科"
    assert requirement_sheet.hard_constraints.school_type_requirement is not None
    assert requirement_sheet.hard_constraints.school_type_requirement.canonical_types == ["211", "THE100"]
    assert requirement_sheet.hard_constraints.experience_requirement is not None
    assert requirement_sheet.hard_constraints.experience_requirement.min_years == 3
    assert requirement_sheet.hard_constraints.experience_requirement.max_years == 5
    assert requirement_sheet.hard_constraints.gender_requirement is not None
    assert requirement_sheet.hard_constraints.gender_requirement.canonical_gender == "男"
    assert requirement_sheet.hard_constraints.age_requirement is not None
    assert requirement_sheet.hard_constraints.age_requirement.max_age == 35


def test_normalize_requirement_draft_keeps_only_allowed_preferred_locations() -> None:
    requirement_sheet = normalize_requirement_draft(
        RequirementExtractionDraft(
            role_title="销售经理",
            title_anchor_terms=["销售"],
            title_anchor_rationale="销售 is the stable searchable anchor from the title.",
            jd_query_terms=["销售拓展"],
            role_summary="负责多城市销售拓展。",
            must_have_capabilities=["销售"],
            locations=["上海", "北京", "深圳"],
            preferred_locations=["北京", "上海", "北京", "杭州"],
            scoring_rationale="先看城市匹配和销售经验。",
        ),
        job_title="销售经理",
    )

    assert requirement_sheet.hard_constraints.locations == ["上海", "北京", "深圳"]
    assert requirement_sheet.preferences.preferred_locations == ["北京", "上海"]


def test_normalize_requirement_draft_admits_ascii_notes_and_demotes_abstract_notes() -> None:
    requirement_sheet = normalize_requirement_draft(
        RequirementExtractionDraft(
            role_title="平台工程师",
            title_anchor_terms=["平台"],
            title_anchor_rationale="平台 is the stable searchable anchor from the title.",
            jd_query_terms=["服务开发", "可观测性", "自动化"],
            notes_query_terms=["Python", "Java", "交付经验"],
            role_summary="负责平台系统建设。",
            must_have_capabilities=["平台"],
            scoring_rationale="先看平台和工程交付能力。",
        ),
        job_title="平台工程师",
    )

    non_anchor_terms = requirement_sheet.initial_query_term_pool[1:]
    assert [item.term for item in non_anchor_terms] == [
        "服务开发",
        "可观测性",
        "自动化",
        "Python",
        "Java",
        "交付经验",
    ]
    assert [item.source for item in non_anchor_terms] == ["jd", "jd", "jd", "notes", "notes", "notes"]
    assert [item.active for item in non_anchor_terms] == [True, True, True, True, True, False]
    assert [item.queryability for item in non_anchor_terms] == [
        "admitted",
        "admitted",
        "admitted",
        "admitted",
        "admitted",
        "score_only",
    ]


def test_normalize_requirement_draft_clears_preferred_locations_for_single_city() -> None:
    requirement_sheet = normalize_requirement_draft(
        RequirementExtractionDraft(
            role_title="销售经理",
            title_anchor_terms=["销售"],
            title_anchor_rationale="销售 is the stable searchable anchor from the title.",
            jd_query_terms=["华东区域"],
            role_summary="负责华东区域销售。",
            must_have_capabilities=["销售"],
            locations=["上海"],
            preferred_locations=["上海"],
            scoring_rationale="先看城市匹配和销售经验。",
        ),
        job_title="销售经理",
    )

    assert requirement_sheet.hard_constraints.locations == ["上海"]
    assert requirement_sheet.preferences.preferred_locations == []


def test_requirement_cache_hit_skips_provider_and_normalizes_current_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(llm_cache_dir=str(tmp_path / "cache"))
    prompt = LoadedPrompt(name="requirements", path=Path("requirements.md"), content="requirements prompt", sha256="p1")
    extractor = RequirementExtractor(settings, prompt)
    input_truth = build_input_truth(
        job_title="Senior Python Engineer",
        jd="Build retrieval systems in Python.",
        notes="",
    )
    cached_draft = _valid_requirement_draft()
    cache_key = requirement_cache_key(settings, prompt=prompt, input_truth=input_truth)
    put_cached_json(
        settings,
        namespace="requirements",
        key=cache_key,
        payload=cached_draft.model_dump(mode="json"),
    )

    provider_calls = 0

    async def fake_extract_live(*, input_truth, prompt_cache_key=None):  # noqa: ANN001
        nonlocal provider_calls
        provider_calls += 1
        return cached_draft

    monkeypatch.setattr(extractor, "_extract_live", fake_extract_live)

    draft, sheet = asyncio.run(extractor.extract_with_draft(input_truth=input_truth))

    assert provider_calls == 0
    assert draft == cached_draft
    assert sheet.role_title == "Senior Python Engineer"
    assert extractor.last_cache_hit is True


def test_normalize_requirement_draft_keeps_one_or_two_title_anchors() -> None:
    one_anchor_sheet = normalize_requirement_draft(
        RequirementExtractionDraft(
            role_title="Senior Backend Engineer",
            title_anchor_terms=["Backend Engineer"],
            title_anchor_rationale="Backend Engineer is the stable searchable title anchor.",
            jd_query_terms=["Python"],
            role_summary="Build backend systems.",
            must_have_capabilities=["Python"],
            scoring_rationale="Prioritize backend depth and Python.",
        ),
        job_title="Senior Backend Engineer",
    )
    two_anchor_sheet = normalize_requirement_draft(
        RequirementExtractionDraft(
            role_title="Senior Backend Engineer",
            title_anchor_terms=["Backend Engineer", "Platform Engineer"],
            title_anchor_rationale="Backend Engineer is primary and Platform Engineer is a close alternate title.",
            jd_query_terms=["Python"],
            role_summary="Build backend systems.",
            must_have_capabilities=["Python"],
            scoring_rationale="Prioritize backend depth and Python.",
        ),
        job_title="Senior Backend Engineer",
    )

    assert one_anchor_sheet.title_anchor_terms == ["Backend Engineer"]
    assert two_anchor_sheet.title_anchor_terms == ["Backend Engineer", "Platform Engineer"]


def test_normalize_requirement_draft_does_not_require_second_title_anchor() -> None:
    requirement_sheet = normalize_requirement_draft(
        RequirementExtractionDraft(
            role_title="Senior Backend Engineer",
            title_anchor_terms=["Backend Engineer"],
            title_anchor_rationale="Backend Engineer is the stable searchable title anchor.",
            jd_query_terms=["Python", "Backend Engineer"],
            role_summary="Build backend systems.",
            must_have_capabilities=["Python"],
            scoring_rationale="Prioritize backend depth and Python.",
        ),
        job_title="Senior Backend Engineer",
    )

    assert requirement_sheet.title_anchor_terms == ["Backend Engineer"]
    assert [item.term for item in requirement_sheet.initial_query_term_pool] == ["Backend Engineer", "Python"]


def test_normalize_requirement_draft_rejects_more_than_two_title_anchors() -> None:
    with pytest.raises(ValueError, match="title_anchor_terms"):
        normalize_requirement_draft(
            RequirementExtractionDraft(
                role_title="Senior Backend Engineer",
                title_anchor_terms=["Backend Engineer", "Platform Engineer", "Software Engineer"],
                title_anchor_rationale="The role could map to several nearby titles.",
                jd_query_terms=["Python"],
                role_summary="Build backend systems.",
                must_have_capabilities=["Python"],
                scoring_rationale="Prioritize backend depth and Python.",
            ),
            job_title="Senior Backend Engineer",
        )


def test_requirement_models_accept_legacy_title_anchor_term_during_transition() -> None:
    draft = RequirementExtractionDraft(
        role_title="Senior Python Engineer",
        title_anchor_terms=["Python"],
        title_anchor_rationale="Python is the stable searchable anchor from the title.",
        jd_query_terms=["Retrieval Systems"],
        role_summary="Build retrieval and ranking capabilities.",
        must_have_capabilities=["Python"],
        scoring_rationale="Prioritize Python and retrieval depth.",
    )
    sheet = normalize_requirement_draft(draft, job_title="Senior Python Engineer")

    assert draft.title_anchor_terms == ["Python"]
    assert draft.title_anchor_term == "Python"
    assert sheet.title_anchor_terms == ["Python"]
    assert sheet.title_anchor_term == "Python"
    assert sheet.title_anchor_rationale


def test_requirement_sheet_enforces_title_anchor_invariants_and_hides_legacy_accessor() -> None:
    with pytest.raises(Exception, match="title_anchor_terms"):
        RequirementSheet(
            role_title="Senior Python Engineer",
            title_anchor_terms=[],
            title_anchor_rationale="Python is the primary anchor.",
            role_summary="Build retrieval and ranking capabilities.",
            scoring_rationale="Prioritize Python and retrieval depth.",
        )
    with pytest.raises(Exception, match="title_anchor_rationale"):
        RequirementSheet(
            role_title="Senior Python Engineer",
            title_anchor_terms=["Python"],
            title_anchor_rationale="",
            role_summary="Build retrieval and ranking capabilities.",
            scoring_rationale="Prioritize Python and retrieval depth.",
        )

    sheet = RequirementSheet(
        role_title="Senior Python Engineer",
        title_anchor_terms=["Python"],
        title_anchor_rationale="Python is the primary anchor.",
        role_summary="Build retrieval and ranking capabilities.",
        scoring_rationale="Prioritize Python and retrieval depth.",
    )

    assert sheet.title_anchor_term == "Python"
    assert "title_anchor_term" not in sheet.model_dump(mode="json")


def test_requirements_extractor_records_provider_usage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = make_settings(llm_cache_dir=str(tmp_path / "cache"))
    prompt = LoadedPrompt(name="requirements", path=Path("requirements.md"), content="requirements prompt", sha256="p6")
    extractor = RequirementExtractor(settings, prompt)
    input_truth = build_input_truth(
        job_title="Senior Python Engineer",
        jd="Build retrieval systems in Python.",
        notes="",
    )
    draft = _valid_requirement_draft()

    class FakeAgent:
        async def run(self, prompt: str):  # noqa: ANN001
            del prompt
            return _fake_usage_result(draft)

    monkeypatch.setattr(extractor, "_get_agent", lambda prompt_cache_key=None: FakeAgent())  # noqa: ARG005

    result = asyncio.run(extractor._extract_live(input_truth=input_truth))

    assert result == draft
    assert extractor.last_provider_usage is not None
    assert extractor.last_provider_usage.model_dump(mode="json") == {
        "input_tokens": 12,
        "output_tokens": 4,
        "total_tokens": 16,
        "cache_read_tokens": 8,
        "cache_write_tokens": 2,
        "details": {"reasoning_tokens": 6},
    }


def test_requirement_cache_key_changes_when_requirements_thinking_changes() -> None:
    prompt = LoadedPrompt(name="requirements", path=Path("requirements.md"), content="requirements prompt", sha256="p3")
    input_truth = build_input_truth(
        job_title="Senior Python Engineer",
        jd="Build retrieval systems in Python.",
        notes="",
    )
    thinking_on_settings = make_settings(requirements_enable_thinking=True)
    thinking_off_settings = make_settings(requirements_enable_thinking=False)

    thinking_on_key = requirement_cache_key(thinking_on_settings, prompt=prompt, input_truth=input_truth)
    thinking_off_key = requirement_cache_key(thinking_off_settings, prompt=prompt, input_truth=input_truth)

    assert thinking_on_key != thinking_off_key


def test_requirement_cache_key_changes_when_reasoning_effort_changes() -> None:
    prompt = LoadedPrompt(name="requirements", path=Path("requirements.md"), content="requirements prompt", sha256="p5")
    input_truth = build_input_truth(
        job_title="Senior Python Engineer",
        jd="Build retrieval systems in Python.",
        notes="",
    )
    low_settings = make_settings(reasoning_effort="low")
    high_settings = make_settings(reasoning_effort="high")

    low_key = requirement_cache_key(low_settings, prompt=prompt, input_truth=input_truth)
    high_key = requirement_cache_key(high_settings, prompt=prompt, input_truth=input_truth)

    assert low_key != high_key


def test_requirement_repair_fixes_empty_non_anchor_jd_terms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(llm_cache_dir=str(tmp_path / "cache"))
    prompt = LoadedPrompt(name="requirements", path=Path("requirements.md"), content="requirements prompt", sha256="p2")
    extractor = RequirementExtractor(
        settings,
        prompt,
        repair_prompt=LoadedPrompt(
            name="repair_requirements",
            path=Path("repair_requirements.md"),
            content="repair requirements prompt",
            sha256="repair-p2",
        ),
    )
    input_truth = build_input_truth(
        job_title="Senior Python Engineer",
        jd="Build retrieval systems in Python.",
        notes="",
    )
    bad_draft = RequirementExtractionDraft(
        role_title="Senior Python Engineer",
        title_anchor_terms=["Python"],
        title_anchor_rationale="Python is the stable searchable anchor from the title.",
        jd_query_terms=["Python"],
        role_summary="Build retrieval systems in Python.",
        must_have_capabilities=["Python"],
        scoring_rationale="Prioritize Python.",
    )
    fixed_draft = RequirementExtractionDraft(
        role_title="Senior Python Engineer",
        title_anchor_terms=["Python"],
        title_anchor_rationale="Python is the stable searchable anchor from the title.",
        jd_query_terms=["Retrieval Systems"],
        role_summary="Build retrieval systems in Python.",
        must_have_capabilities=["Python"],
        scoring_rationale="Prioritize Python.",
    )
    seen_prompt_names: dict[str, str] = {}

    async def fake_extract_live(*, input_truth, prompt_cache_key=None):  # noqa: ANN001
        return bad_draft

    async def fake_repair(settings, prompt, repair_prompt, input_truth, draft, reason):  # noqa: ANN001
        del settings, input_truth, draft, reason
        seen_prompt_names["source"] = prompt.name
        seen_prompt_names["repair"] = repair_prompt.name
        return fixed_draft, None

    monkeypatch.setattr(extractor, "_extract_live", fake_extract_live)
    monkeypatch.setattr("seektalent.requirements.extractor.repair_requirement_draft", fake_repair)

    draft, sheet = asyncio.run(extractor.extract_with_draft(input_truth=input_truth))

    assert draft == fixed_draft
    assert len(sheet.initial_query_term_pool) >= 2
    assert extractor.last_repair_attempt_count == 1
    assert extractor.last_repair_succeeded is True
    assert extractor.last_repair_reason is not None
    assert "jd_query_terms" in extractor.last_repair_reason
    assert "non-anchor" in extractor.last_repair_reason
    assert seen_prompt_names == {"source": "requirements", "repair": "repair_requirements"}


def test_requirement_full_retry_when_repaired_draft_still_fails_normalization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(llm_cache_dir=str(tmp_path / "cache"))
    prompt = LoadedPrompt(name="requirements", path=Path("requirements.md"), content="requirements prompt", sha256="p4")
    extractor = RequirementExtractor(settings, prompt)
    input_truth = build_input_truth(
        job_title="Senior Python Engineer",
        jd="Build retrieval systems in Python.",
        notes="",
    )
    first_live_draft = RequirementExtractionDraft(
        role_title="Senior Python Engineer",
        title_anchor_terms=["Python"],
        title_anchor_rationale="Python is the stable searchable anchor from the title.",
        jd_query_terms=["Python"],
        role_summary="Build retrieval systems in Python.",
        must_have_capabilities=["Python"],
        scoring_rationale="Prioritize Python.",
    )
    repaired_but_still_invalid = RequirementExtractionDraft(
        role_title="Senior Python Engineer",
        title_anchor_terms=["Python"],
        title_anchor_rationale="Python is the stable searchable anchor from the title.",
        jd_query_terms=["Python"],
        role_summary="Build retrieval systems in Python.",
        must_have_capabilities=["Python"],
        scoring_rationale="Prioritize Python.",
    )
    second_live_draft = RequirementExtractionDraft(
        role_title="Senior Python Engineer",
        title_anchor_terms=["Python"],
        title_anchor_rationale="Python is the stable searchable anchor from the title.",
        jd_query_terms=["Retrieval Systems"],
        role_summary="Build retrieval systems in Python.",
        must_have_capabilities=["Python"],
        scoring_rationale="Prioritize Python.",
    )

    provider_calls = 0

    async def fake_extract_live(*, input_truth, prompt_cache_key=None):  # noqa: ANN001
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            return first_live_draft
        return second_live_draft

    async def fake_repair(settings, prompt, repair_prompt, input_truth, draft, reason):  # noqa: ANN001
        del settings, prompt, repair_prompt, input_truth, draft, reason
        return repaired_but_still_invalid, None

    monkeypatch.setattr(extractor, "_extract_live", fake_extract_live)
    monkeypatch.setattr("seektalent.requirements.extractor.repair_requirement_draft", fake_repair)

    draft, sheet = asyncio.run(extractor.extract_with_draft(input_truth=input_truth))

    assert provider_calls == 2
    assert draft == second_live_draft
    assert len(sheet.initial_query_term_pool) >= 2
    assert extractor.last_repair_attempt_count == 1
    assert extractor.last_repair_succeeded is False
    assert extractor.last_full_retry_count == 1


def test_requirement_repair_usage_contributes_to_stage_total(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(llm_cache_dir=str(tmp_path / "cache"))
    prompt = LoadedPrompt(name="requirements", path=Path("requirements.md"), content="requirements prompt", sha256="p7")
    extractor = RequirementExtractor(settings, prompt)
    input_truth = build_input_truth(
        job_title="Senior Python Engineer",
        jd="Build retrieval systems in Python.",
        notes="",
    )
    bad_draft = RequirementExtractionDraft(
        role_title="Senior Python Engineer",
        title_anchor_terms=["Python"],
        title_anchor_rationale="Python is the stable searchable anchor from the title.",
        jd_query_terms=["Python"],
        role_summary="Build retrieval systems in Python.",
        must_have_capabilities=["Python"],
        scoring_rationale="Prioritize Python.",
    )
    fixed_draft = RequirementExtractionDraft(
        role_title="Senior Python Engineer",
        title_anchor_terms=["Python"],
        title_anchor_rationale="Python is the stable searchable anchor from the title.",
        jd_query_terms=["Retrieval Systems"],
        role_summary="Build retrieval systems in Python.",
        must_have_capabilities=["Python"],
        scoring_rationale="Prioritize Python.",
    )
    live_usage = _provider_usage(
        input_tokens=12,
        output_tokens=4,
        cache_read_tokens=8,
        cache_write_tokens=2,
        reasoning_tokens=6,
    )
    repair_usage = _provider_usage(
        input_tokens=5,
        output_tokens=3,
        cache_read_tokens=1,
        cache_write_tokens=4,
        reasoning_tokens=2,
    )

    async def fake_extract_live(*, input_truth, prompt_cache_key=None):  # noqa: ANN001
        del input_truth, prompt_cache_key
        extractor.last_provider_usage = live_usage
        return bad_draft

    async def fake_repair(settings, prompt, repair_prompt, input_truth, draft, reason):  # noqa: ANN001
        del settings, prompt, repair_prompt, input_truth, draft, reason
        return fixed_draft, repair_usage

    monkeypatch.setattr(extractor, "_extract_live", fake_extract_live)
    monkeypatch.setattr("seektalent.requirements.extractor.repair_requirement_draft", fake_repair)

    draft, _sheet = asyncio.run(extractor.extract_with_draft(input_truth=input_truth))

    assert draft == fixed_draft
    assert extractor.last_provider_usage is not None
    assert extractor.last_provider_usage.model_dump(mode="json") == {
        "input_tokens": 17,
        "output_tokens": 7,
        "total_tokens": 24,
        "cache_read_tokens": 9,
        "cache_write_tokens": 6,
        "details": {"reasoning_tokens": 8},
    }
