from __future__ import annotations

from pathlib import Path

from seektalent.api import MatchRunResult
from seektalent.evaluation import EvaluationResult, EvaluationStageResult
from seektalent.models import FinalCandidate, FinalResult, RuntimeConstraint, StopGuidance
from seektalent.runtime.constraints import RuntimeConstraintsContractV1
from seektalent.runtime.orchestrator import WorkflowRuntime
from seektalent.runtime.production_contract import ProductionMatchResultV1, SourceSelectionV1
from seektalent.runtime.stop_reasons import PUBLIC_STOP_REASON_ALLOWLIST, normalize_stop_reason
from tests.settings_factory import make_settings


def _constraint_debug_result(
    tmp_path: Path,
    *,
    runtime_constraints: tuple[RuntimeConstraint, ...] = (),
    stop_reason: str = "controller_stop",
    terminal_stop_guidance: StopGuidance | None = None,
) -> MatchRunResult:
    trace_log = tmp_path / "trace.log"
    trace_log.write_text("debug", encoding="utf-8")
    return MatchRunResult(
        final_result=FinalResult(
            run_id="run-constraints",
            run_dir=str(tmp_path),
            candidates=[
                FinalCandidate(
                    resume_id="candidate-1",
                    rank=1,
                    final_score=91,
                    fit_bucket="fit",
                    source_provider="cts",
                    evidence_level="card",
                    detail_open_status="not_supported",
                    match_summary="Strong match.",
                    strengths=["Python"],
                    weaknesses=[],
                    matched_must_haves=["Python"],
                    matched_preferences=[],
                    risk_flags=[],
                    why_selected="Best current fit.",
                    source_round=1,
                )
            ],
            summary="done",
            stop_reason=stop_reason,
            rounds_executed=2,
        ),
        final_markdown="# Final",
        run_id="run-constraints",
        run_dir=tmp_path,
        trace_log_path=trace_log,
        evaluation_result=EvaluationResult(
            run_id="run-constraints",
            judge_model="judge",
            jd_sha256="jd",
            final=EvaluationStageResult(stage="final", ndcg_at_10=0.0, precision_at_10=0.0, total_score=0.0),
            round_01=EvaluationStageResult(stage="round_01", ndcg_at_10=0.0, precision_at_10=0.0, total_score=0.0),
        ),
        terminal_stop_guidance=terminal_stop_guidance,
        runtime_constraints=runtime_constraints,
    )


def test_runtime_constraints_contract_exports_hard_limits_from_settings() -> None:
    settings = make_settings(
        max_rounds=7,
        min_rounds=2,
        search_max_pages_per_round=4,
        search_max_attempts_per_round=5,
        search_no_progress_limit=3,
        scoring_timeout_seconds=45.0,
        prf_probe_phrase_proposal_timeout_seconds=2.5,
    )

    contract = RuntimeConstraintsContractV1.from_settings(settings)

    assert contract.policy_version == "runtime-constraints.v1"
    assert contract.max_rounds == 7
    assert contract.min_rounds == 2
    assert contract.search_max_pages_per_round == 4
    assert contract.search_max_attempts_per_round == 5
    assert contract.search_no_progress_limit == 3
    assert contract.scoring_timeout_seconds == 45.0
    assert contract.prf_probe_phrase_proposal_timeout_seconds == 2.5
    assert contract.near_budget_threshold_ratio == 0.8
    assert "controller_stop" in contract.stop_reason_allowlist
    assert "max_rounds_reached" in contract.stop_reason_allowlist
    assert "target_satisfied" in contract.stop_reason_allowlist
    assert "max_attempts_reached" in contract.stop_reason_allowlist
    assert contract.stop_reason_allowlist == PUBLIC_STOP_REASON_ALLOWLIST


def test_runtime_and_production_stop_reason_normalization_share_policy() -> None:
    assert WorkflowRuntime._normalize_stop_reason(object(), proposed="target_satisfied") == normalize_stop_reason(
        "target_satisfied"
    )
    assert WorkflowRuntime._normalize_stop_reason(object(), proposed="/tmp/raw provider failure") == normalize_stop_reason(
        "/tmp/raw provider failure"
    )


def test_production_contract_normalizes_unknown_stop_reason(tmp_path: Path) -> None:
    result = ProductionMatchResultV1.from_debug_result(
        _constraint_debug_result(tmp_path, stop_reason="raw provider failure /tmp/secret"),
        input_digest="digest",
        source_selection=SourceSelectionV1(required=("cts",)),
    )

    assert result.stop_reason.code == "controller_stop"
    assert result.stop_reason.message == "controller_stop"
    assert "raw provider failure" not in result.model_dump_json()
    assert str(tmp_path) not in result.model_dump_json()


def test_production_contract_preserves_allowlisted_stop_reason(tmp_path: Path) -> None:
    result = ProductionMatchResultV1.from_debug_result(
        _constraint_debug_result(tmp_path, stop_reason="target_satisfied"),
        input_digest="digest",
        source_selection=SourceSelectionV1(required=("cts",)),
    )

    assert result.stop_reason.code == "target_satisfied"
    assert result.stop_reason.message == "target_satisfied"


def test_production_contract_exposes_runtime_constraints_contract(tmp_path: Path) -> None:
    settings = make_settings(max_rounds=6, search_max_pages_per_round=3)
    contract = RuntimeConstraintsContractV1.from_settings(settings)

    result = ProductionMatchResultV1.from_debug_result(
        _constraint_debug_result(tmp_path),
        input_digest="digest",
        source_selection=SourceSelectionV1(required=("cts",)),
        runtime_constraints_contract=contract,
    )

    assert result.runtime_constraints == contract
    assert result.runtime_constraints.max_rounds == 6
    assert result.runtime_constraints.search_max_pages_per_round == 3


def test_production_contract_does_not_leak_terminal_stop_guidance_reason(tmp_path: Path) -> None:
    guidance = StopGuidance(
        can_stop=True,
        reason=f"raw provider stopped at {tmp_path}/secret.txt with sk-test-secret",
        top_pool_strength="weak",
        quality_gate_status="low_quality_exhausted",
    )

    result = ProductionMatchResultV1.from_debug_result(
        _constraint_debug_result(tmp_path, terminal_stop_guidance=guidance),
        input_digest="digest",
        source_selection=SourceSelectionV1(required=("cts",)),
    )

    payload = result.model_dump()
    payload_json = result.model_dump_json()
    assert payload["terminal_quality_gate_status"] == "low_quality_exhausted"
    assert "terminal_stop_guidance" not in payload
    assert "sk-test-secret" not in payload_json
    assert str(tmp_path) not in payload_json


def test_production_contract_projects_runtime_constraints_as_candidate_decisions(tmp_path: Path) -> None:
    constraints = (
        RuntimeConstraint(
            field="age_requirement",
            normalized_value=["max=35"],
            source="jd",
            rationale="JD requested age range.",
            blocking=True,
        ),
        RuntimeConstraint(
            field="gender_requirement",
            normalized_value="男",
            source="jd",
            rationale="JD requested gender.",
            blocking=True,
        ),
        RuntimeConstraint(
            field="school_names",
            normalized_value=["复旦大学"],
            source="jd",
            rationale="JD requested schools.",
            blocking=True,
        ),
        RuntimeConstraint(
            field="company_names",
            normalized_value=["OpenAI"],
            source="jd",
            rationale="JD requested target company.",
            blocking=True,
        ),
    )

    result = ProductionMatchResultV1.from_debug_result(
        _constraint_debug_result(tmp_path, runtime_constraints=constraints),
        input_digest="digest",
        source_selection=SourceSelectionV1(required=("cts",)),
    )

    decisions = result.final_candidates[0].constraint_decisions

    assert [(decision.rule_id, decision.decision) for decision in decisions] == [
        ("age_requirement", "not_applicable"),
        ("gender_requirement", "not_applicable"),
        ("school_names", "not_applicable"),
        ("company_names", "unknown"),
    ]
    assert decisions[0].reason_code == "protected_attribute_excluded_from_candidate_scoring"
    assert decisions[0].provenance == "runtime_constraint:jd"
    assert decisions[1].reason_code == "protected_attribute_excluded_from_candidate_scoring"
    assert decisions[2].reason_code == "protected_attribute_excluded_from_candidate_scoring"
    assert decisions[3].reason_code == "runtime_constraint_requires_non_llm_verification"
    assert decisions[3].provenance == "runtime_constraint:jd"
