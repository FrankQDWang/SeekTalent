from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from seektalent.providers.pi_agent.contracts import (
    PiAgentActionType,
    PiAgentCompletionReason,
    PiAgentFailureCode,
    PiAgentTaskType,
)


EvidenceRequirement = Literal[
    "redacted_text_snapshot",
    "redacted_visual_snapshot",
    "action_trace_only",
]

RoutePhase = Literal["pre", "post"]

DIRECT_REQUEST_FORBIDDEN_ACTIONS = (
    "page.request",
    "browserContext.request",
    "APIRequestContext",
    "list_network_requests",
    "get_network_request",
    "evaluate_script",
    "provider_signature_generation",
    "stealth_plugin",
    "proxy_rotation",
    "header_or_cookie_injection",
)

_FORBIDDEN_ROUTE_SEGMENTS = {"api", "ajax"}


@dataclass(frozen=True)
class LiepinPiSkill:
    skill_id: str
    task_type: PiAgentTaskType
    allowed_url_hosts: tuple[str, ...]
    pre_action_allowed_route_patterns: tuple[str, ...]
    post_action_expected_route_patterns: tuple[str, ...]
    allowed_actions: tuple[PiAgentActionType, ...]
    forbidden_actions: tuple[str, ...]
    output_schema_version: str
    redaction_policy_id: str
    failure_codes: tuple[PiAgentFailureCode, ...]
    completion_reasons: tuple[PiAgentCompletionReason, ...]
    pacing_policy_id: str
    evidence_requirement: EvidenceRequirement
    max_attempts: int
    risk_circuit_breaker: str
    requires_detail_approval: bool = False
    requires_runtime_grant: bool = False


_LIEPIN_SEARCH_HOSTS = ("www.liepin.com", "h.liepin.com")
_LIEPIN_DETAIL_HOSTS = ("www.liepin.com", "h.liepin.com")
_LIEPIN_LOGIN_HOSTS = ("www.liepin.com", "passport.liepin.com", "h.liepin.com")

_COMMON_FAILURE_CODES: tuple[PiAgentFailureCode, ...] = (
    PiAgentFailureCode.LOGIN_EXPIRED,
    PiAgentFailureCode.VERIFICATION_REQUIRED,
    PiAgentFailureCode.RISK_CONTROL,
    PiAgentFailureCode.SELECTOR_DRIFT,
    PiAgentFailureCode.PAGE_TIMEOUT,
    PiAgentFailureCode.PROVIDER_CONNECTION_LOCKED,
)

_DETAIL_FAILURE_CODES: tuple[PiAgentFailureCode, ...] = (
    *_COMMON_FAILURE_CODES,
    PiAgentFailureCode.DETAIL_OPEN_GRANT_MISSING,
    PiAgentFailureCode.DETAIL_BUDGET_RESERVATION_FAILED,
    PiAgentFailureCode.DETAIL_OPEN_GRANT_EXPIRED,
    PiAgentFailureCode.DETAIL_OPEN_GRANT_CANDIDATE_MISMATCH,
    PiAgentFailureCode.DETAIL_OPEN_GRANT_SOURCE_RUN_MISMATCH,
    PiAgentFailureCode.DETAIL_OPEN_DUPLICATE,
)

_SEARCH_COMPLETION_REASONS: tuple[PiAgentCompletionReason, ...] = (
    PiAgentCompletionReason.PAGE_EXHAUSTED,
    PiAgentCompletionReason.ENOUGH_STRONG_CARDS,
    PiAgentCompletionReason.DETAIL_BUDGET_EXHAUSTED,
    PiAgentCompletionReason.USER_STOPPED,
)

_DETAIL_COMPLETION_REASONS: tuple[PiAgentCompletionReason, ...] = (
    PiAgentCompletionReason.COMPLETED,
    PiAgentCompletionReason.DETAIL_BUDGET_WAITING_FOR_HUMAN,
    PiAgentCompletionReason.USER_STOPPED,
)

_SKILLS: dict[PiAgentTaskType, LiepinPiSkill] = {
    PiAgentTaskType.LIEPIN_SEARCH_CARDS: LiepinPiSkill(
        skill_id="liepin.search_cards.v1",
        task_type=PiAgentTaskType.LIEPIN_SEARCH_CARDS,
        allowed_url_hosts=_LIEPIN_SEARCH_HOSTS,
        pre_action_allowed_route_patterns=("/zhaopin/", "/lptjob/"),
        post_action_expected_route_patterns=("/zhaopin/", "/lptjob/"),
        allowed_actions=(
            PiAgentActionType.LIEPIN_NAVIGATE_TO_SEARCH,
            PiAgentActionType.LIEPIN_SUBMIT_KEYWORD_SEARCH,
            PiAgentActionType.LIEPIN_READ_CARD_PAGE,
            PiAgentActionType.LIEPIN_TURN_PAGE,
        ),
        forbidden_actions=DIRECT_REQUEST_FORBIDDEN_ACTIONS,
        output_schema_version="liepin-card-search-output-v1",
        redaction_policy_id="liepin-card-redaction-v1",
        failure_codes=_COMMON_FAILURE_CODES,
        completion_reasons=_SEARCH_COMPLETION_REASONS,
        pacing_policy_id="liepin-search-pacing-v1",
        evidence_requirement="redacted_text_snapshot",
        max_attempts=1,
        risk_circuit_breaker="liepin-risk-stop-v1",
    ),
    PiAgentTaskType.LIEPIN_READ_CARD_PAGE: LiepinPiSkill(
        skill_id="liepin.read_card_page.v1",
        task_type=PiAgentTaskType.LIEPIN_READ_CARD_PAGE,
        allowed_url_hosts=_LIEPIN_SEARCH_HOSTS,
        pre_action_allowed_route_patterns=("/zhaopin/", "/lptjob/"),
        post_action_expected_route_patterns=("/zhaopin/", "/lptjob/"),
        allowed_actions=(PiAgentActionType.LIEPIN_READ_CARD_PAGE,),
        forbidden_actions=DIRECT_REQUEST_FORBIDDEN_ACTIONS,
        output_schema_version="liepin-card-page-output-v1",
        redaction_policy_id="liepin-card-redaction-v1",
        failure_codes=_COMMON_FAILURE_CODES,
        completion_reasons=_SEARCH_COMPLETION_REASONS,
        pacing_policy_id="liepin-read-pacing-v1",
        evidence_requirement="redacted_text_snapshot",
        max_attempts=1,
        risk_circuit_breaker="liepin-risk-stop-v1",
    ),
    PiAgentTaskType.LIEPIN_CLASSIFY_CARD_SUMMARY: LiepinPiSkill(
        skill_id="liepin.classify_card_summary.v1",
        task_type=PiAgentTaskType.LIEPIN_CLASSIFY_CARD_SUMMARY,
        allowed_url_hosts=_LIEPIN_SEARCH_HOSTS,
        pre_action_allowed_route_patterns=("/zhaopin/", "/lptjob/"),
        post_action_expected_route_patterns=("/zhaopin/", "/lptjob/"),
        allowed_actions=(PiAgentActionType.LIEPIN_CLASSIFY_CARD_SUMMARY,),
        forbidden_actions=DIRECT_REQUEST_FORBIDDEN_ACTIONS,
        output_schema_version="liepin-card-classification-output-v1",
        redaction_policy_id="liepin-card-redaction-v1",
        failure_codes=_COMMON_FAILURE_CODES,
        completion_reasons=_SEARCH_COMPLETION_REASONS,
        pacing_policy_id="liepin-classify-pacing-v1",
        evidence_requirement="action_trace_only",
        max_attempts=1,
        risk_circuit_breaker="liepin-risk-stop-v1",
    ),
    PiAgentTaskType.LIEPIN_REQUEST_DETAIL_OPEN: LiepinPiSkill(
        skill_id="liepin.request_detail_open.v1",
        task_type=PiAgentTaskType.LIEPIN_REQUEST_DETAIL_OPEN,
        allowed_url_hosts=_LIEPIN_SEARCH_HOSTS,
        pre_action_allowed_route_patterns=("/zhaopin/", "/lptjob/"),
        post_action_expected_route_patterns=("/zhaopin/", "/lptjob/"),
        allowed_actions=(PiAgentActionType.LIEPIN_REQUEST_DETAIL_OPEN,),
        forbidden_actions=DIRECT_REQUEST_FORBIDDEN_ACTIONS,
        output_schema_version="liepin-detail-open-request-v1",
        redaction_policy_id="liepin-card-redaction-v1",
        failure_codes=_COMMON_FAILURE_CODES,
        completion_reasons=(PiAgentCompletionReason.DETAIL_BUDGET_WAITING_FOR_HUMAN,),
        pacing_policy_id="liepin-approval-request-pacing-v1",
        evidence_requirement="action_trace_only",
        max_attempts=1,
        risk_circuit_breaker="liepin-risk-stop-v1",
        requires_detail_approval=True,
    ),
    PiAgentTaskType.LIEPIN_OPEN_DETAIL_AFTER_APPROVAL: LiepinPiSkill(
        skill_id="liepin.open_detail_after_approval.v1",
        task_type=PiAgentTaskType.LIEPIN_OPEN_DETAIL_AFTER_APPROVAL,
        allowed_url_hosts=_LIEPIN_DETAIL_HOSTS,
        pre_action_allowed_route_patterns=("/zhaopin/", "/lptjob/"),
        post_action_expected_route_patterns=("/resume/showresumedetail/", "/candidate/detail/"),
        allowed_actions=(PiAgentActionType.LIEPIN_OPEN_DETAIL_AFTER_APPROVAL,),
        forbidden_actions=DIRECT_REQUEST_FORBIDDEN_ACTIONS,
        output_schema_version="liepin-detail-open-output-v1",
        redaction_policy_id="liepin-detail-redaction-v1",
        failure_codes=_DETAIL_FAILURE_CODES,
        completion_reasons=_DETAIL_COMPLETION_REASONS,
        pacing_policy_id="liepin-detail-open-pacing-v1",
        evidence_requirement="redacted_text_snapshot",
        max_attempts=1,
        risk_circuit_breaker="liepin-detail-risk-stop-v1",
        requires_detail_approval=True,
        requires_runtime_grant=True,
    ),
    PiAgentTaskType.LIEPIN_EXTRACT_DETAIL_RESUME: LiepinPiSkill(
        skill_id="liepin.extract_detail_resume.v1",
        task_type=PiAgentTaskType.LIEPIN_EXTRACT_DETAIL_RESUME,
        allowed_url_hosts=_LIEPIN_DETAIL_HOSTS,
        pre_action_allowed_route_patterns=("/resume/showresumedetail/", "/candidate/detail/"),
        post_action_expected_route_patterns=("/resume/showresumedetail/", "/candidate/detail/"),
        allowed_actions=(PiAgentActionType.LIEPIN_EXTRACT_DETAIL_RESUME,),
        forbidden_actions=DIRECT_REQUEST_FORBIDDEN_ACTIONS,
        output_schema_version="liepin-detail-resume-output-v1",
        redaction_policy_id="liepin-detail-redaction-v1",
        failure_codes=(*_COMMON_FAILURE_CODES, PiAgentFailureCode.EXTRACTION_FAILURE),
        completion_reasons=(PiAgentCompletionReason.COMPLETED, PiAgentCompletionReason.USER_STOPPED),
        pacing_policy_id="liepin-detail-extract-pacing-v1",
        evidence_requirement="redacted_text_snapshot",
        max_attempts=1,
        risk_circuit_breaker="liepin-detail-risk-stop-v1",
    ),
    PiAgentTaskType.LIEPIN_DETECT_LOGIN_OR_RISK_STATE: LiepinPiSkill(
        skill_id="liepin.detect_login_or_risk_state.v1",
        task_type=PiAgentTaskType.LIEPIN_DETECT_LOGIN_OR_RISK_STATE,
        allowed_url_hosts=_LIEPIN_LOGIN_HOSTS,
        pre_action_allowed_route_patterns=("/", "/login/", "/zhaopin/", "/lptjob/"),
        post_action_expected_route_patterns=("/", "/login/", "/zhaopin/", "/lptjob/"),
        allowed_actions=(PiAgentActionType.LIEPIN_DETECT_LOGIN_OR_RISK_STATE,),
        forbidden_actions=DIRECT_REQUEST_FORBIDDEN_ACTIONS,
        output_schema_version="liepin-login-risk-state-v1",
        redaction_policy_id="liepin-risk-state-redaction-v1",
        failure_codes=_COMMON_FAILURE_CODES,
        completion_reasons=(PiAgentCompletionReason.COMPLETED, PiAgentCompletionReason.USER_STOPPED),
        pacing_policy_id="liepin-risk-state-pacing-v1",
        evidence_requirement="redacted_text_snapshot",
        max_attempts=1,
        risk_circuit_breaker="liepin-risk-stop-v1",
    ),
}


def get_liepin_pi_skill(name: PiAgentTaskType | str) -> LiepinPiSkill:
    try:
        task_type = PiAgentTaskType(name)
    except ValueError as exc:
        raise KeyError(name) from exc
    return _SKILLS[task_type]


def is_liepin_skill_url_allowed(skill: LiepinPiSkill, url: str, phase: RoutePhase = "pre") -> bool:
    if phase not in {"pre", "post"}:
        return False

    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"}:
        return False
    if parsed.netloc not in skill.allowed_url_hosts:
        return False

    path = parsed.path or "/"
    if _has_forbidden_route_segment(path):
        return False

    patterns = (
        skill.pre_action_allowed_route_patterns
        if phase == "pre"
        else skill.post_action_expected_route_patterns
    )
    return _route_matches(path, patterns)


def _has_forbidden_route_segment(path: str) -> bool:
    return any(segment in _FORBIDDEN_ROUTE_SEGMENTS for segment in path.strip("/").split("/"))


def _route_matches(path: str, patterns: tuple[str, ...]) -> bool:
    normalized_path = path or "/"
    if normalized_path != "/" and not normalized_path.endswith("/"):
        normalized_path = f"{normalized_path}/"

    for pattern in patterns:
        if pattern == "/":
            if path == "/":
                return True
            continue

        normalized_pattern = pattern if pattern.endswith("/") else f"{pattern}/"
        stripped_pattern = normalized_pattern.rstrip("/")
        if path == stripped_pattern or normalized_path.startswith(normalized_pattern):
            return True
    return False
