from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from seektalent.candidate_feedback.models import FeedbackCandidateExpression
from seektalent.models import unique_strings

PRF_POLICY_VERSION = "prf-policy-v1"
MIN_PRF_SEED_COUNT = 2
MAX_NEGATIVE_SUPPORT_RATE = 0.4


class PRFGateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_no: int
    seed_resume_ids: list[str] = Field(default_factory=list)
    seed_count: int
    negative_resume_ids: list[str] = Field(default_factory=list)
    candidate_expressions: list[FeedbackCandidateExpression] = Field(default_factory=list)
    candidate_expression_count: int
    tried_term_family_ids: list[str] = Field(default_factory=list)
    tried_query_fingerprints: list[str] = Field(default_factory=list)
    min_seed_count: int = MIN_PRF_SEED_COUNT
    max_negative_support_rate: float = MAX_NEGATIVE_SUPPORT_RATE
    policy_version: str = PRF_POLICY_VERSION


class PRFPolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempted: bool
    gate_passed: bool
    accepted_expression: FeedbackCandidateExpression | None = None
    candidate_expressions: list[FeedbackCandidateExpression] = Field(default_factory=list)
    reject_reasons: list[str] = Field(default_factory=list)
    gate_input: PRFGateInput


def build_prf_policy_decision(policy_input: PRFGateInput) -> PRFPolicyDecision:
    gate_input = _normalize_gate_input(policy_input)
    candidate_expressions = _evaluate_candidate_expressions(
        candidate_expressions=gate_input.candidate_expressions,
        tried_term_family_ids=gate_input.tried_term_family_ids,
        min_seed_count=gate_input.min_seed_count,
        max_negative_support_rate=gate_input.max_negative_support_rate,
    )
    if gate_input.seed_count < gate_input.min_seed_count:
        return PRFPolicyDecision(
            attempted=True,
            gate_passed=False,
            candidate_expressions=candidate_expressions,
            reject_reasons=["insufficient_high_quality_seeds"],
            gate_input=gate_input,
        )

    accepted_expression = next((item for item in candidate_expressions if not item.reject_reasons), None)
    if accepted_expression is None:
        return PRFPolicyDecision(
            attempted=True,
            gate_passed=False,
            candidate_expressions=candidate_expressions,
            reject_reasons=["no_safe_prf_expression"],
            gate_input=gate_input,
        )

    return PRFPolicyDecision(
        attempted=True,
        gate_passed=True,
        accepted_expression=accepted_expression,
        candidate_expressions=candidate_expressions,
        gate_input=gate_input,
    )


def _normalize_gate_input(policy_input: PRFGateInput) -> PRFGateInput:
    return policy_input.model_copy(
        update={
            "seed_resume_ids": unique_strings(policy_input.seed_resume_ids),
            "negative_resume_ids": unique_strings(policy_input.negative_resume_ids),
            "tried_term_family_ids": unique_strings(policy_input.tried_term_family_ids),
            "tried_query_fingerprints": unique_strings(policy_input.tried_query_fingerprints),
        }
    )


def _evaluate_candidate_expressions(
    *,
    candidate_expressions: list[FeedbackCandidateExpression],
    tried_term_family_ids: list[str],
    min_seed_count: int,
    max_negative_support_rate: float,
) -> list[FeedbackCandidateExpression]:
    tried_families = set(tried_term_family_ids)
    evaluated: list[FeedbackCandidateExpression] = []
    for expression in candidate_expressions:
        reject_reasons = _normalize_reject_reasons(expression.reject_reasons)
        if (
            expression.candidate_term_type == "responsibility_phrase"
            and "shadow_only_responsibility_phrase" not in reject_reasons
        ):
            reject_reasons.append("shadow_only_responsibility_phrase")
        if expression.candidate_term_type == "company_entity" and "company_entity_rejected" not in reject_reasons:
            reject_reasons.append("company_entity_rejected")
        if _has_strengths_only_grounding(expression) and "derived_summary_only_grounding" not in reject_reasons:
            reject_reasons.append("derived_summary_only_grounding")
        if expression.term_family_id in tried_families and "existing_or_tried_family" not in reject_reasons:
            reject_reasons.append("existing_or_tried_family")
        if (
            expression.positive_seed_support_count < min_seed_count
            and "insufficient_seed_support" not in reject_reasons
        ):
            reject_reasons.append("insufficient_seed_support")
        if (
            expression.not_fit_support_rate >= max_negative_support_rate
            and "negative_support_too_high" not in reject_reasons
        ):
            reject_reasons.append("negative_support_too_high")
        evaluated.append(expression.model_copy(update={"reject_reasons": unique_strings(reject_reasons)}))
    return evaluated


def _normalize_reject_reasons(reject_reasons: list[str]) -> list[str]:
    normalized: list[str] = []
    for reason in reject_reasons:
        if reason == "company_entity":
            normalized.append("company_entity_rejected")
            continue
        if reason == "tried_term_family":
            normalized.append("existing_or_tried_family")
            continue
        normalized.append(reason)
    return unique_strings(normalized)


def _has_strengths_only_grounding(expression: FeedbackCandidateExpression) -> bool:
    if not expression.field_hits:
        return False
    return set(expression.field_hits) == {"strengths"} and expression.field_hits["strengths"] > 0
