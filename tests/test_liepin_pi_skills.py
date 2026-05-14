import pytest

from seektalent.providers.liepin.pi_skills import (
    DIRECT_REQUEST_FORBIDDEN_ACTIONS,
    get_liepin_pi_skill,
    is_liepin_skill_url_allowed,
)
from seektalent.providers.pi_agent.contracts import (
    PiAgentActionType,
    PiAgentCompletionReason,
    PiAgentFailureCode,
    PiAgentTaskType,
)


def test_search_skill_has_route_redaction_failure_pacing_and_evidence() -> None:
    skill = get_liepin_pi_skill(PiAgentTaskType.LIEPIN_SEARCH_CARDS)

    assert skill.skill_id == "liepin.search_cards.v1"
    assert skill.task_type == PiAgentTaskType.LIEPIN_SEARCH_CARDS
    assert skill.allowed_url_hosts == ("www.liepin.com", "h.liepin.com")
    assert "/zhaopin/" in skill.pre_action_allowed_route_patterns
    assert "/zhaopin/" in skill.post_action_expected_route_patterns
    assert skill.redaction_policy_id == "liepin-card-redaction-v1"
    assert PiAgentFailureCode.RISK_CONTROL in skill.failure_codes
    assert PiAgentCompletionReason.PAGE_EXHAUSTED in skill.completion_reasons
    assert skill.pacing_policy_id == "liepin-search-pacing-v1"
    assert skill.evidence_requirement == "redacted_text_snapshot"


def test_detail_skill_requires_runtime_grant_and_redacted_evidence() -> None:
    skill = get_liepin_pi_skill(PiAgentTaskType.LIEPIN_OPEN_DETAIL_AFTER_APPROVAL)

    assert skill.requires_detail_approval is True
    assert skill.requires_runtime_grant is True
    assert skill.evidence_requirement == "redacted_text_snapshot"
    assert PiAgentFailureCode.DETAIL_OPEN_GRANT_MISSING in skill.failure_codes
    assert skill.allowed_actions == (PiAgentActionType.LIEPIN_OPEN_DETAIL_AFTER_APPROVAL,)
    assert skill.pre_action_allowed_route_patterns == ("/zhaopin/", "/lptjob/")
    assert skill.post_action_expected_route_patterns == ("/resume/showresumedetail/", "/candidate/detail/")


def test_all_skills_forbid_direct_authenticated_request_replay() -> None:
    for task_type in PiAgentTaskType:
        skill = get_liepin_pi_skill(task_type)
        for forbidden in DIRECT_REQUEST_FORBIDDEN_ACTIONS:
            assert forbidden in skill.forbidden_actions

    assert "list_network_requests" in DIRECT_REQUEST_FORBIDDEN_ACTIONS
    assert "get_network_request" in DIRECT_REQUEST_FORBIDDEN_ACTIONS
    assert "evaluate_script" in DIRECT_REQUEST_FORBIDDEN_ACTIONS


def test_every_task_type_has_skill_recipe() -> None:
    for task_type in PiAgentTaskType:
        assert get_liepin_pi_skill(task_type).task_type == task_type


def test_skill_recipes_use_contract_enums_not_free_strings() -> None:
    for task_type in PiAgentTaskType:
        skill = get_liepin_pi_skill(task_type)
        assert isinstance(skill.task_type, PiAgentTaskType)
        assert all(isinstance(action, PiAgentActionType) for action in skill.allowed_actions)
        assert all(isinstance(code, PiAgentFailureCode) for code in skill.failure_codes)
        assert all(isinstance(reason, PiAgentCompletionReason) for reason in skill.completion_reasons)


def test_unknown_skill_raises_key_error() -> None:
    with pytest.raises(KeyError):
        get_liepin_pi_skill("liepin.unknown")


def test_skill_url_matcher_rejects_non_liepin_host_and_api_routes() -> None:
    skill = get_liepin_pi_skill(PiAgentTaskType.LIEPIN_SEARCH_CARDS)

    assert is_liepin_skill_url_allowed(skill, "https://www.liepin.com/zhaopin/")
    assert not is_liepin_skill_url_allowed(skill, "https://evil.com/zhaopin/")
    assert not is_liepin_skill_url_allowed(skill, "https://www.liepin.com/api/search")
    assert not is_liepin_skill_url_allowed(skill, "https://www.liepin.com/zhaopin/api/search")
    assert not is_liepin_skill_url_allowed(skill, "https://www.liepin.com/lptjob/ajax/page")


def test_root_route_pattern_only_matches_root_path() -> None:
    skill = get_liepin_pi_skill(PiAgentTaskType.LIEPIN_DETECT_LOGIN_OR_RISK_STATE)

    assert is_liepin_skill_url_allowed(skill, "https://passport.liepin.com/")
    assert not is_liepin_skill_url_allowed(skill, "https://passport.liepin.com/sensitive/unexpected")


def test_open_detail_skill_uses_pre_and_post_route_phases() -> None:
    skill = get_liepin_pi_skill(PiAgentTaskType.LIEPIN_OPEN_DETAIL_AFTER_APPROVAL)

    assert is_liepin_skill_url_allowed(skill, "https://www.liepin.com/zhaopin/", phase="pre")
    assert not is_liepin_skill_url_allowed(skill, "https://www.liepin.com/zhaopin/", phase="post")
    assert is_liepin_skill_url_allowed(skill, "https://www.liepin.com/resume/showresumedetail/123", phase="post")


def test_skill_url_matcher_fails_closed_for_unknown_phase() -> None:
    skill = get_liepin_pi_skill(PiAgentTaskType.LIEPIN_OPEN_DETAIL_AFTER_APPROVAL)

    assert not is_liepin_skill_url_allowed(
        skill,
        "https://www.liepin.com/resume/showresumedetail/123",
        phase="unexpected",
    )
