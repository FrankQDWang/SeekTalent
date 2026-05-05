from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import json
from math import ceil
from pathlib import Path
from time import perf_counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from seektalent.candidate_feedback.llm_prf import (
    LLMPRFExtractor,
    LLMPRFInput,
    build_conservative_prf_family_id,
    build_llm_prf_input,
    feedback_expressions_from_llm_grounding,
    ground_llm_prf_candidates,
)
from seektalent.candidate_feedback.policy import (
    MAX_NEGATIVE_SUPPORT_RATE,
    MIN_PRF_SEED_COUNT,
    PRF_POLICY_VERSION,
    PRFGateInput,
    PRFPolicyDecision,
    build_prf_policy_decision,
)
from seektalent.config import AppSettings, load_process_env
from seektalent.models import ScoredCandidate, unique_strings
from seektalent.prompting import PromptRegistry

LanguageBucket = Literal["english", "chinese", "mixed"]
LiveExpectedBehavior = Literal[
    "should_activate",
    "should_fallback",
    "should_reject_existing",
    "should_reject_single_seed",
    "should_handle_cjk_ascii",
]
LiveValidationStatus = Literal["passed", "fallback", "provider_failed", "schema_failed", "blocked"]

_BLOCKER_REJECT_REASONS = {
    "company_entity",
    "company_entity_rejected",
    "generic_or_filter_like",
    "derived_summary_only_grounding",
    "insufficient_seed_support",
    "blocked_term_accepted",
}


class LLMPRFBakeoffCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    language_bucket: LanguageBucket
    role_title: str
    must_have_capabilities: list[str] = Field(default_factory=list)
    seed_texts: list[str] = Field(default_factory=list)
    expected_query_material: list[str] = Field(default_factory=list)
    blocked_terms: list[str] = Field(default_factory=list)


class LLMPRFBakeoffResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    language_bucket: LanguageBucket
    accepted_expression: str | None = None
    accepted_grounded: bool = False
    accepted_reject_reasons: list[str] = Field(default_factory=list)
    fallback_reason: str | None = None
    structured_output_failed: bool = False
    latency_ms: int | None = None


class LLMPRFLiveValidationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    expected_behavior: LiveExpectedBehavior
    input: LLMPRFInput
    blocked_terms: list[str] = Field(default_factory=list)
    notes: str = ""


class LLMPRFLiveValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    expected_behavior: LiveExpectedBehavior
    status: LiveValidationStatus
    provider_failure: bool = False
    fallback_reason: str | None = None
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    candidate_surfaces: list[str] = Field(default_factory=list)
    accepted_expression: str | None = None
    accepted_positive_seed_support_count: int | None = None
    accepted_negative_support_count: int | None = None
    reject_reasons: list[str] = Field(default_factory=list)
    latency_ms: int | None = None


def load_bakeoff_cases(path: Path) -> list[LLMPRFBakeoffCase]:
    cases: list[LLMPRFBakeoffCase] = []
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            cases.append(LLMPRFBakeoffCase.model_validate_json(line))
        except ValidationError as exc:
            raise ValueError(f"invalid bakeoff case at {path}:{line_no}") from exc
    return cases


def load_live_validation_cases(path: Path) -> list[LLMPRFLiveValidationCase]:
    cases: list[LLMPRFLiveValidationCase] = []
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            cases.append(LLMPRFLiveValidationCase.model_validate_json(line))
        except ValidationError as exc:
            raise ValueError(f"invalid live validation case at {path}:{line_no}") from exc
    return cases


def score_llm_prf_bakeoff_results(results: list[LLMPRFBakeoffResult]) -> dict[str, object]:
    accepted = [item for item in results if item.accepted_expression is not None]
    blockers = [
        item
        for item in accepted
        if not item.accepted_grounded or any(reason in _BLOCKER_REJECT_REASONS for reason in item.accepted_reject_reasons)
    ]
    latencies = [item.latency_ms for item in results if item.latency_ms is not None]
    return {
        "case_count": len(results),
        "accepted_count": len(accepted),
        "blocker_count": len(blockers),
        "non_extractive_accepted_count": sum(1 for item in accepted if not item.accepted_grounded),
        "structured_output_failure_rate": _rate(
            sum(1 for item in results if item.structured_output_failed),
            len(results),
        ),
        "generic_fallback_rate": _rate(
            sum(1 for item in results if item.fallback_reason),
            len(results),
        ),
        "latency_ms_p50": _percentile(latencies, 0.50),
        "latency_ms_p95": _percentile(latencies, 0.95),
        "language_bucket_counts": dict(Counter(item.language_bucket for item in results)),
    }


def classify_live_validation_blockers(result: LLMPRFLiveValidationResult) -> tuple[list[str], list[str]]:
    blockers = list(result.blockers)
    warnings = list(result.warnings)
    if result.provider_failure:
        return unique_strings(blockers), unique_strings(warnings)
    if result.status == "schema_failed":
        blockers.append("schema_validation_failed")
    if result.expected_behavior == "should_activate" and result.accepted_expression is None:
        blockers.append("expected_activation_fell_back")
    if result.expected_behavior == "should_handle_cjk_ascii" and result.accepted_expression is None:
        blockers.append("cjk_ascii_grounding_failed")
    if result.accepted_positive_seed_support_count is not None and result.accepted_positive_seed_support_count < 2:
        blockers.append("accepted_expression_insufficient_seed_support")
    if result.expected_behavior in {"should_fallback", "should_reject_existing", "should_reject_single_seed"}:
        if result.accepted_expression is not None:
            blockers.append("unexpected_accepted_expression")
    return unique_strings(blockers), unique_strings(warnings)


def score_live_validation_results(results: list[LLMPRFLiveValidationResult]) -> dict[str, object]:
    blocker_count = 0
    warning_count = 0
    provider_failure_count = 0
    schema_failure_count = 0
    fallback_count = 0
    accepted_count = 0
    latencies = [item.latency_ms for item in results if item.latency_ms is not None]
    for result in results:
        blockers, warnings = classify_live_validation_blockers(result)
        blocker_count += len(blockers)
        warning_count += len(warnings)
        provider_failure_count += int(result.provider_failure)
        schema_failure_count += int(result.status == "schema_failed")
        fallback_count += int(result.accepted_expression is None and not result.provider_failure)
        accepted_count += int(result.accepted_expression is not None)
    return {
        "case_count": len(results),
        "passed_count": sum(1 for item in results if item.status == "passed"),
        "blocker_count": blocker_count,
        "warning_count": warning_count,
        "provider_failure_count": provider_failure_count,
        "schema_failure_count": schema_failure_count,
        "fallback_count": fallback_count,
        "accepted_count": accepted_count,
        "max_latency_ms": max(latencies) if latencies else None,
        "p95_latency_ms": _percentile(latencies, 0.95),
    }


def run_live_bakeoff(
    *,
    settings: AppSettings,
    cases: list[LLMPRFBakeoffCase],
    output_dir: Path,
) -> list[LLMPRFBakeoffResult]:
    return asyncio.run(_run_live_bakeoff(settings=settings, cases=cases, output_dir=output_dir))


def run_live_validation(
    *,
    settings: AppSettings,
    cases: list[LLMPRFLiveValidationCase],
    output_dir: Path,
) -> list[LLMPRFLiveValidationResult]:
    live_settings = settings.model_copy(
        update={
            "prf_probe_phrase_proposal_timeout_seconds": settings.prf_probe_phrase_proposal_live_harness_timeout_seconds
        }
    )
    return asyncio.run(_run_live_validation(settings=live_settings, cases=cases, output_dir=output_dir))


async def _run_live_bakeoff(
    *,
    settings: AppSettings,
    cases: list[LLMPRFBakeoffCase],
    output_dir: Path,
) -> list[LLMPRFBakeoffResult]:
    prompt = PromptRegistry(settings.prompt_dir).load("prf_probe_phrase_proposal")
    extractor = LLMPRFExtractor(settings, prompt)
    results: list[LLMPRFBakeoffResult] = []
    for case in cases:
        results.append(await _run_live_case(settings=settings, extractor=extractor, case=case, output_dir=output_dir))
    return results


async def _run_live_validation(
    *,
    settings: AppSettings,
    cases: list[LLMPRFLiveValidationCase],
    output_dir: Path,
) -> list[LLMPRFLiveValidationResult]:
    prompt = PromptRegistry(settings.prompt_dir).load("prf_probe_phrase_proposal")
    extractor = LLMPRFExtractor(settings, prompt)
    results: list[LLMPRFLiveValidationResult] = []
    for case in cases:
        results.append(await _run_live_validation_case(settings=settings, extractor=extractor, case=case, output_dir=output_dir))
    return results


async def _run_live_case(
    *,
    settings: AppSettings,
    extractor: LLMPRFExtractor,
    case: LLMPRFBakeoffCase,
    output_dir: Path,
) -> LLMPRFBakeoffResult:
    case_dir = output_dir / case.case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    seed_resumes = _seed_resumes_from_case(case)
    payload = build_llm_prf_input(
        seed_resumes=seed_resumes,
        negative_resumes=[],
        round_no=2,
        role_title=case.role_title,
        must_have_capabilities=case.must_have_capabilities,
    )
    if payload is None:
        result = LLMPRFBakeoffResult(
            case_id=case.case_id,
            language_bucket=case.language_bucket,
            fallback_reason="insufficient_prf_seed_support",
        )
        _write_json(case_dir / "result.json", result.model_dump(mode="json"))
        return result

    _write_json(case_dir / "input.json", payload.model_dump(mode="json"))
    started = perf_counter()
    try:
        extraction = await asyncio.wait_for(extractor.propose(payload), timeout=settings.prf_probe_phrase_proposal_timeout_seconds)
    except Exception as exc:  # noqa: BLE001
        latency_ms = _elapsed_ms(started)
        result = LLMPRFBakeoffResult(
            case_id=case.case_id,
            language_bucket=case.language_bucket,
            fallback_reason=_failure_reason(exc),
            structured_output_failed=_is_structured_output_failure(exc),
            latency_ms=latency_ms,
        )
        _write_json(case_dir / "proposal_error.json", {"error_type": type(exc).__name__, "message": str(exc)})
        _write_json(case_dir / "result.json", result.model_dump(mode="json"))
        return result

    latency_ms = _elapsed_ms(started)
    grounding = ground_llm_prf_candidates(payload, extraction)
    expressions = feedback_expressions_from_llm_grounding(
        payload,
        grounding,
        known_company_entities=set(),
        tried_term_family_ids=set(),
    )
    decision = build_prf_policy_decision(
        PRFGateInput(
            round_no=payload.round_no,
            seed_resume_ids=payload.seed_resume_ids,
            seed_count=len(payload.seed_resume_ids),
            negative_resume_ids=payload.negative_resume_ids,
            candidate_expressions=expressions,
            candidate_expression_count=len(expressions),
            tried_term_family_ids=[],
            tried_query_fingerprints=[],
            min_seed_count=MIN_PRF_SEED_COUNT,
            max_negative_support_rate=MAX_NEGATIVE_SUPPORT_RATE,
            policy_version=PRF_POLICY_VERSION,
        )
    )
    result = _result_from_decision(case=case, decision=decision, latency_ms=latency_ms)
    _write_json(case_dir / "proposal.json", extraction.model_dump(mode="json"))
    _write_json(case_dir / "grounding.json", grounding.model_dump(mode="json"))
    _write_json(case_dir / "policy.json", decision.model_dump(mode="json"))
    _write_json(case_dir / "result.json", result.model_dump(mode="json"))
    return result


async def _run_live_validation_case(
    *,
    settings: AppSettings,
    extractor: LLMPRFExtractor,
    case: LLMPRFLiveValidationCase,
    output_dir: Path,
) -> LLMPRFLiveValidationResult:
    case_dir = output_dir / case.case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    payload = case.input
    _write_json(case_dir / "input.json", payload.model_dump(mode="json"))
    started = perf_counter()
    try:
        extraction = await asyncio.wait_for(extractor.propose(payload), timeout=settings.prf_probe_phrase_proposal_timeout_seconds)
    except Exception as exc:  # noqa: BLE001
        latency_ms = _elapsed_ms(started)
        structured_output_failure = _is_structured_output_failure(exc)
        result = LLMPRFLiveValidationResult(
            case_id=case.case_id,
            expected_behavior=case.expected_behavior,
            status="schema_failed" if structured_output_failure else "provider_failed",
            provider_failure=not structured_output_failure,
            fallback_reason=_failure_reason(exc),
            latency_ms=latency_ms,
        )
        blockers, warnings = classify_live_validation_blockers(result)
        result = result.model_copy(update={"blockers": blockers, "warnings": warnings})
        _write_json(case_dir / "proposal_error.json", {"error_type": type(exc).__name__, "message": str(exc)})
        _write_json(case_dir / "result.json", result.model_dump(mode="json"))
        return result

    latency_ms = _elapsed_ms(started)
    grounding = ground_llm_prf_candidates(payload, extraction)
    expressions = feedback_expressions_from_llm_grounding(
        payload,
        grounding,
        known_company_entities=set(),
        tried_term_family_ids=set(payload.tried_term_family_ids),
    )
    decision = build_prf_policy_decision(
        PRFGateInput(
            round_no=payload.round_no,
            seed_resume_ids=payload.seed_resume_ids,
            seed_count=len(payload.seed_resume_ids),
            negative_resume_ids=payload.negative_resume_ids,
            candidate_expressions=expressions,
            candidate_expression_count=len(expressions),
            tried_term_family_ids=payload.tried_term_family_ids,
            tried_query_fingerprints=[],
            min_seed_count=MIN_PRF_SEED_COUNT,
            max_negative_support_rate=MAX_NEGATIVE_SUPPORT_RATE,
            policy_version=PRF_POLICY_VERSION,
        )
    )
    result = _live_validation_result_from_decision(
        case=case,
        extraction=extraction,
        decision=decision,
        latency_ms=latency_ms,
    )
    blockers, warnings = classify_live_validation_blockers(result)
    if blockers:
        status: LiveValidationStatus = "blocked"
    elif result.accepted_expression is None:
        status = "fallback"
    else:
        status = "passed"
    result = result.model_copy(update={"status": status, "blockers": blockers, "warnings": warnings})
    _write_json(case_dir / "proposal.json", extraction.model_dump(mode="json"))
    _write_json(case_dir / "grounding.json", grounding.model_dump(mode="json"))
    _write_json(case_dir / "policy.json", decision.model_dump(mode="json"))
    _write_json(case_dir / "result.json", result.model_dump(mode="json"))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a live LLM PRF phrase proposal bakeoff.")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--case-format", choices=["bakeoff", "llm-prf-input"], default="bakeoff")
    args = parser.parse_args(argv)
    if not args.live:
        raise SystemExit("--live is required so real DeepSeek calls are never accidental")

    load_process_env(args.env_file)
    settings = AppSettings(_env_file=args.env_file)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.case_format == "llm-prf-input":
        live_cases = load_live_validation_cases(args.cases)
        live_results = run_live_validation(settings=settings, cases=live_cases, output_dir=args.output_dir)
        summary = score_live_validation_results(live_results)
        _write_jsonl(
            args.output_dir / "llm_prf_live_validation_results.jsonl",
            [item.model_dump(mode="json") for item in live_results],
        )
        _write_json(args.output_dir / "llm_prf_live_validation_summary.json", summary)
        return 0 if summary["blocker_count"] == 0 and summary["provider_failure_count"] == 0 else 1

    cases = load_bakeoff_cases(args.cases)
    results = run_live_bakeoff(settings=settings, cases=cases, output_dir=args.output_dir)
    metrics = score_llm_prf_bakeoff_results(results)
    _write_jsonl(args.output_dir / "llm_prf_bakeoff_results.jsonl", [item.model_dump(mode="json") for item in results])
    _write_json(args.output_dir / "llm_prf_bakeoff_metrics.json", metrics)
    return 0


def _result_from_decision(
    *,
    case: LLMPRFBakeoffCase,
    decision: PRFPolicyDecision,
    latency_ms: int,
) -> LLMPRFBakeoffResult:
    accepted_expression = decision.accepted_expression
    if accepted_expression is None:
        return LLMPRFBakeoffResult(
            case_id=case.case_id,
            language_bucket=case.language_bucket,
            fallback_reason="no_safe_llm_prf_expression",
            latency_ms=latency_ms,
        )
    reject_reasons = list(accepted_expression.reject_reasons)
    if _matches_any(accepted_expression.canonical_expression, case.blocked_terms):
        reject_reasons = unique_strings([*reject_reasons, "blocked_term_accepted"])
    return LLMPRFBakeoffResult(
        case_id=case.case_id,
        language_bucket=case.language_bucket,
        accepted_expression=accepted_expression.canonical_expression,
        accepted_grounded=_accepted_expression_was_grounded(accepted_expression.term_family_id, decision),
        accepted_reject_reasons=reject_reasons,
        latency_ms=latency_ms,
    )


def _accepted_expression_was_grounded(term_family_id: str, decision: PRFPolicyDecision) -> bool:
    return any(
        expression.term_family_id == term_family_id and expression.positive_seed_support_count >= MIN_PRF_SEED_COUNT
        for expression in decision.candidate_expressions
    )


def _live_validation_result_from_decision(
    *,
    case: LLMPRFLiveValidationCase,
    extraction,
    decision: PRFPolicyDecision,
    latency_ms: int,
) -> LLMPRFLiveValidationResult:
    accepted_expression = decision.accepted_expression
    candidate_surfaces = unique_strings([candidate.surface for candidate in extraction.candidates])
    if accepted_expression is None:
        return LLMPRFLiveValidationResult(
            case_id=case.case_id,
            expected_behavior=case.expected_behavior,
            status="fallback",
            fallback_reason=decision.reject_reasons[0] if decision.reject_reasons else "no_safe_llm_prf_expression",
            candidate_surfaces=candidate_surfaces,
            reject_reasons=decision.reject_reasons,
            latency_ms=latency_ms,
        )
    blockers = []
    if _matches_any(accepted_expression.canonical_expression, case.blocked_terms):
        blockers.append("blocked_term_accepted")
    return LLMPRFLiveValidationResult(
        case_id=case.case_id,
        expected_behavior=case.expected_behavior,
        status="passed",
        blockers=blockers,
        candidate_surfaces=candidate_surfaces,
        accepted_expression=accepted_expression.canonical_expression,
        accepted_positive_seed_support_count=accepted_expression.positive_seed_support_count,
        accepted_negative_support_count=accepted_expression.negative_support_count,
        reject_reasons=list(accepted_expression.reject_reasons),
        latency_ms=latency_ms,
    )


def _seed_resumes_from_case(case: LLMPRFBakeoffCase) -> list[ScoredCandidate]:
    return [
        ScoredCandidate(
            resume_id=f"{case.case_id}-seed-{index}",
            fit_bucket="fit",
            overall_score=90,
            must_have_match_score=85,
            preferred_match_score=70,
            risk_score=10,
            risk_flags=[],
            reasoning_summary=text,
            evidence=[text],
            confidence="high",
            matched_must_haves=case.must_have_capabilities,
            missing_must_haves=[],
            matched_preferences=[],
            negative_signals=[],
            strengths=[],
            weaknesses=[],
            source_round=1,
        )
        for index, text in enumerate(case.seed_texts, start=1)
    ]


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    sorted_values = sorted(values)
    index = max(0, ceil(percentile * len(sorted_values)) - 1)
    return sorted_values[min(index, len(sorted_values) - 1)]


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)


def _failure_reason(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return "llm_prf_timeout"
    if _is_structured_output_failure(exc):
        return "structured_output_failure"
    return "llm_prf_provider_failure"


def _is_structured_output_failure(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    return (
        isinstance(exc, (json.JSONDecodeError, ValidationError))
        or "validation" in name
        or "schema" in name
        or "unexpectedmodelbehavior" in name
        or "structured" in name
    )


def _matches_any(value: str, candidates: list[str]) -> bool:
    value_family_id = build_conservative_prf_family_id(value)
    return any(value_family_id == build_conservative_prf_family_id(candidate) for candidate in candidates)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[object]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
