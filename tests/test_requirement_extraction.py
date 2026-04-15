from seektalent.models import RequirementExtractionDraft
from seektalent.requirements import build_scoring_policy, normalize_requirement_draft


def test_normalize_requirement_draft_covers_standard_slots() -> None:
    requirement_sheet = normalize_requirement_draft(
        RequirementExtractionDraft(
            role_title="高级 Python 工程师",
            title_anchor_term="Python",
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
    assert requirement_sheet.title_anchor_term == "Python"
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
            title_anchor_term="Python",
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
            title_anchor_term="Python",
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
            title_anchor_term="算法",
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
            title_anchor_term="销售",
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


def test_normalize_requirement_draft_clears_preferred_locations_for_single_city() -> None:
    requirement_sheet = normalize_requirement_draft(
        RequirementExtractionDraft(
            role_title="销售经理",
            title_anchor_term="销售",
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
