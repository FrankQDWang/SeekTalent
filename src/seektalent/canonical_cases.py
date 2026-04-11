from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.models.test import TestModel

from seektalent.api import run_match
from seektalent.bootstrap_assets import default_bootstrap_assets
from seektalent.candidate_text import build_candidate_search_text
from seektalent.clients.cts_client import CTSFetchResult
from seektalent.config import AppSettings
from seektalent.models import (
    BusinessPolicyPack,
    DomainKnowledgePack,
    RetrievedCandidate_t,
    SearchRunBundle,
    stable_deduplicate,
)
from seektalent.resources import runtime_case_dir, runtime_eval_matrix_file
from seektalent_rerank.models import RerankResponse, RerankResult


ACTIVE_PACK_IDS = (
    "llm_agent_rag_engineering",
    "search_ranking_retrieval_engineering",
    "finance_risk_control_ai",
)


class CanonicalCaseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    scenario: str
    business_context: str
    expected_route: str
    expected_stop_reason: str
    expected_knowledge_pack_ids: list[str] = Field(default_factory=list)
    expected_fallback_reason: str | None = None
    must_hold: list[str] = Field(default_factory=list)
    must_not_hold: list[str] = Field(default_factory=list)


@dataclass
class _FakeCTSClient:
    results: list[CTSFetchResult]
    seen_plans: list[object] = field(default_factory=list)

    async def search(self, plan, *, trace_id: str = "") -> CTSFetchResult:
        del trace_id
        self.seen_plans.append(plan)
        if not self.results:
            raise AssertionError("unexpected_cts_call")
        return self.results.pop(0)


@dataclass
class _FakeRerankRequest:
    pack_scores: dict[str, float]
    candidate_scores: list[dict[str, float]]
    seen_requests: list[object] = field(default_factory=list)

    async def __call__(self, request):
        self.seen_requests.append(request)
        document_ids = [document.id for document in request.documents]
        if document_ids and document_ids[0] in ACTIVE_PACK_IDS:
            scores = self.pack_scores
        else:
            if not self.candidate_scores:
                raise AssertionError("unexpected_candidate_rerank_call")
            scores = self.candidate_scores.pop(0)
        return RerankResponse(
            model="test-reranker",
            results=[
                RerankResult(id=item_id, index=index, score=scores[item_id], rank=index + 1)
                for index, item_id in enumerate(document_ids)
            ],
        )


@dataclass
class _SequentialTestModel:
    outputs: list[object]
    model_name: str = "test"

    @property
    def custom_output_args(self) -> object:
        if not self.outputs:
            raise AssertionError("unexpected_model_call")
        return self.outputs.pop(0)


def canonical_case_specs() -> tuple[CanonicalCaseSpec, ...]:
    return (
        CanonicalCaseSpec(
            case_id="case-bootstrap-explicit-pack",
            scenario="显式 pack bootstrap",
            business_context="业务已明确指定领域，系统直接注入该领域知识包，不再猜领域。",
            expected_route="explicit_pack",
            expected_stop_reason="controller_stop",
            expected_knowledge_pack_ids=["llm_agent_rag_engineering"],
            must_hold=[
                "selected_knowledge_pack_ids contains llm_agent_rag_engineering",
                "pack_bridge remains legal in bootstrap",
            ],
            must_not_hold=["routing_mode = generic_fallback"],
        ),
        CanonicalCaseSpec(
            case_id="case-bootstrap-inferred-single-pack",
            scenario="单领域 top1 路由",
            business_context="岗位文本足够明确，reranker 应稳定命中单一领域知识包。",
            expected_route="inferred_single_pack",
            expected_stop_reason="controller_stop",
            expected_knowledge_pack_ids=["llm_agent_rag_engineering"],
            must_hold=["selected_knowledge_pack_ids contains llm_agent_rag_engineering"],
            must_not_hold=["routing_mode = generic_fallback"],
        ),
        CanonicalCaseSpec(
            case_id="case-bootstrap-close-high-score-multi-pack",
            scenario="接近高分触发 multi-pack",
            business_context="两个领域都很强且分数接近时，同时注入 top2 packs 做 bootstrap。",
            expected_route="inferred_multi_pack",
            expected_stop_reason="controller_stop",
            expected_knowledge_pack_ids=[
                "llm_agent_rag_engineering",
                "search_ranking_retrieval_engineering",
            ],
            must_hold=[
                "selected_knowledge_pack_ids contains llm_agent_rag_engineering",
                "selected_knowledge_pack_ids contains search_ranking_retrieval_engineering",
            ],
            must_not_hold=["routing_mode = generic_fallback"],
        ),
        CanonicalCaseSpec(
            case_id="case-bootstrap-out-of-domain-generic",
            scenario="低分 out-of-domain generic",
            business_context="JD 缺少领域锚点时，不强行路由任何领域知识包。",
            expected_route="generic_fallback",
            expected_stop_reason="controller_stop",
            expected_fallback_reason="top1_confidence_below_floor",
            must_hold=[
                "fallback_reason is top1_confidence_below_floor",
                "selected_knowledge_pack_ids is empty",
            ],
            must_not_hold=["routing_mode = inferred_single_pack"],
        ),
        CanonicalCaseSpec(
            case_id="case-crossover-legal",
            scenario="合法 crossover",
            business_context="进入 balance 期且已有合法 donor 时，控制器可发起 crossover 搜索。",
            expected_route="inferred_single_pack",
            expected_stop_reason="controller_stop",
            expected_knowledge_pack_ids=["llm_agent_rag_engineering"],
            must_hold=["round 2 uses crossover_compose with donor_frontier_node_id"],
            must_not_hold=["missing donor candidate list"],
        ),
        CanonicalCaseSpec(
            case_id="case-crossover-illegal-reject",
            scenario="非法 crossover 拒绝",
            business_context="控制器给出非法 donor 时，structured validator 立即拒绝并要求重试。",
            expected_route="inferred_single_pack",
            expected_stop_reason="controller_stop",
            expected_knowledge_pack_ids=["llm_agent_rag_engineering"],
            must_hold=["controller validator retry count equals 1"],
            must_not_hold=["illegal crossover reaches execution_plan"],
        ),
        CanonicalCaseSpec(
            case_id="case-stop-controller-direct-accepted",
            scenario="controller stop 直接接受",
            business_context="进入 balance 期后，controller stop 第一次提出即可直接终止 run。",
            expected_route="inferred_single_pack",
            expected_stop_reason="controller_stop",
            expected_knowledge_pack_ids=["llm_agent_rag_engineering"],
            must_hold=["round 2 stop_reason is controller_stop"],
            must_not_hold=["search_cts round exists"],
        ),
        CanonicalCaseSpec(
            case_id="case-stop-controller-direct-rejected",
            scenario="controller stop 先拒绝后接受",
            business_context="在 explore 期 stop 会被拒绝，直到 balance 期才被 runtime 接受。",
            expected_route="inferred_single_pack",
            expected_stop_reason="controller_stop",
            expected_knowledge_pack_ids=["llm_agent_rag_engineering"],
            must_hold=["round 0 stop_reason is null", "round 1 stop_reason is null", "round 2 stop_reason is controller_stop"],
            must_not_hold=["round count equals 1"],
        ),
        CanonicalCaseSpec(
            case_id="case-stop-exhausted-low-gain-and-finalize",
            scenario="低增益 exhausted finalize",
            business_context="进入 harvest 后，空结果且 novelty/usefulness/reward 都偏低时，系统应以 exhausted_low_gain 收口。",
            expected_route="inferred_single_pack",
            expected_stop_reason="exhausted_low_gain",
            expected_knowledge_pack_ids=["llm_agent_rag_engineering"],
            must_hold=["round 3 stop_reason is exhausted_low_gain"],
            must_not_hold=["stop_reason = controller_stop"],
        ),
    )


def build_all_canonical_artifacts(*, repo_root: Path) -> None:
    _clear_generated_outputs(repo_root)
    rows: list[dict[str, object]] = []
    specs = canonical_case_specs()
    for spec in specs:
        bundle = build_case_bundle(spec, repo_root=repo_root)
        canonical_bundle = _canonical_bundle(bundle, case_id=spec.case_id)
        _write_case_artifacts(spec, canonical_bundle)
        _write_trace_docs(spec, canonical_bundle, repo_root=repo_root)
        rows.append(build_case_eval(spec, canonical_bundle))
        tmp_runs_dir = runtime_case_dir(spec.case_id) / "_tmp_runs"
        if tmp_runs_dir.exists():
            shutil.rmtree(tmp_runs_dir)
    _write_eval_matrix(rows)
    _write_trace_index(specs, repo_root=repo_root)


def build_case_bundle(
    spec: CanonicalCaseSpec,
    *,
    repo_root: Path,
    assets_override: object | None = None,
    runs_dir_override: Path | None = None,
) -> SearchRunBundle:
    match spec.case_id:
        case "case-bootstrap-explicit-pack":
            return _build_explicit_pack_bundle(
                repo_root=repo_root,
                assets_override=assets_override,
                runs_dir_override=runs_dir_override,
            )
        case "case-bootstrap-inferred-single-pack":
            return _build_inferred_single_pack_bundle(
                repo_root=repo_root,
                assets_override=assets_override,
                runs_dir_override=runs_dir_override,
            )
        case "case-bootstrap-close-high-score-multi-pack":
            return _build_close_high_score_multi_pack_bundle(
                repo_root=repo_root,
                assets_override=assets_override,
                runs_dir_override=runs_dir_override,
            )
        case "case-bootstrap-out-of-domain-generic":
            return _build_out_of_domain_generic_bundle(
                repo_root=repo_root,
                assets_override=assets_override,
                runs_dir_override=runs_dir_override,
            )
        case "case-crossover-legal":
            return _build_legal_crossover_bundle(
                repo_root=repo_root,
                assets_override=assets_override,
                runs_dir_override=runs_dir_override,
            )
        case "case-crossover-illegal-reject":
            return _build_illegal_crossover_bundle(
                repo_root=repo_root,
                assets_override=assets_override,
                runs_dir_override=runs_dir_override,
            )
        case "case-stop-controller-direct-accepted":
            return _build_direct_stop_accepted_bundle(
                repo_root=repo_root,
                assets_override=assets_override,
                runs_dir_override=runs_dir_override,
            )
        case "case-stop-controller-direct-rejected":
            return _build_direct_stop_rejected_bundle(
                repo_root=repo_root,
                assets_override=assets_override,
                runs_dir_override=runs_dir_override,
            )
        case "case-stop-exhausted-low-gain-and-finalize":
            return _build_exhausted_low_gain_bundle(
                repo_root=repo_root,
                assets_override=assets_override,
                runs_dir_override=runs_dir_override,
            )
    raise ValueError(f"unknown_case_id: {spec.case_id}")


def build_case_eval(spec: CanonicalCaseSpec, bundle: SearchRunBundle) -> dict[str, object]:
    routing = bundle.bootstrap.routing_result
    selected_pack_ids = routing.selected_knowledge_pack_ids
    packs = _selected_packs(bundle)
    include_keyword_adoption = _include_keyword_adoption(bundle, packs)
    exclude_keyword_leak = _exclude_keyword_leak(bundle, packs)
    return {
        "case_id": spec.case_id,
        "scenario": spec.scenario,
        "experiment_id": "E5",
        "expected_route": spec.expected_route,
        "observed_route": routing.routing_mode,
        "expected_stop_reason": spec.expected_stop_reason,
        "observed_stop_reason": bundle.final_result.stop_reason,
        "expected_knowledge_pack_ids": spec.expected_knowledge_pack_ids,
        "observed_knowledge_pack_ids": selected_pack_ids,
        "expected_fallback_reason": spec.expected_fallback_reason,
        "observed_fallback_reason": routing.fallback_reason,
        "must_hold": spec.must_hold,
        "must_not_hold": spec.must_not_hold,
        "metrics": [
            {"name": "route_match", "value": routing.routing_mode == spec.expected_route},
            {"name": "selected_packs_match", "value": selected_pack_ids == spec.expected_knowledge_pack_ids},
            {
                "name": "generic_fallback_correctness",
                "value": (
                    routing.routing_mode == "generic_fallback"
                    and not selected_pack_ids
                    and routing.fallback_reason == spec.expected_fallback_reason
                )
                if spec.expected_route == "generic_fallback"
                else routing.routing_mode != "generic_fallback",
            },
            {"name": "include_keyword_adoption", "value": include_keyword_adoption},
            {"name": "exclude_keyword_leak", "value": exclude_keyword_leak},
            {"name": "stop_reason_match", "value": bundle.final_result.stop_reason == spec.expected_stop_reason},
            {"name": "round_count", "value": len(bundle.rounds)},
        ],
    }


def _build_explicit_pack_bundle(
    *,
    repo_root: Path,
    assets_override=None,
    runs_dir_override: Path | None = None,
) -> SearchRunBundle:
    return _run_case(
        repo_root=repo_root,
        case_id="case-bootstrap-explicit-pack",
        assets=_runtime_assets(
            knowledge_pack_id_override="llm_agent_rag_engineering",
            base_assets=assets_override,
        ),
        requirement_payload=_llm_requirement_payload(),
        keyword_payload=_llm_keyword_payload(),
        pack_scores=_llm_pack_scores(),
        controller_outputs=_phase_gated_stop_outputs(),
        final_summary="Explicit pack bootstrap stopped cleanly.",
        runs_dir_override=runs_dir_override,
    )


def _build_inferred_single_pack_bundle(
    *,
    repo_root: Path,
    assets_override=None,
    runs_dir_override: Path | None = None,
) -> SearchRunBundle:
    return _run_case(
        repo_root=repo_root,
        case_id="case-bootstrap-inferred-single-pack",
        assets=_runtime_assets(base_assets=assets_override),
        requirement_payload=_llm_requirement_payload(),
        keyword_payload=_llm_keyword_payload(),
        pack_scores=_llm_pack_scores(),
        controller_outputs=_phase_gated_stop_outputs(),
        final_summary="Single-pack inferred bootstrap stopped cleanly.",
        runs_dir_override=runs_dir_override,
    )


def _build_close_high_score_multi_pack_bundle(
    *,
    repo_root: Path,
    assets_override=None,
    runs_dir_override: Path | None = None,
) -> SearchRunBundle:
    return _run_case(
        repo_root=repo_root,
        case_id="case-bootstrap-close-high-score-multi-pack",
        assets=_runtime_assets(base_assets=assets_override),
        requirement_payload=_hybrid_requirement_payload(),
        keyword_payload=_hybrid_keyword_payload(),
        pack_scores={
            "llm_agent_rag_engineering": 0.7,
            "search_ranking_retrieval_engineering": 0.65,
            "finance_risk_control_ai": 0.1,
        },
        controller_outputs=_phase_gated_stop_outputs(),
        final_summary="Close high scores triggered a multi-pack bootstrap.",
        runs_dir_override=runs_dir_override,
    )


def _build_out_of_domain_generic_bundle(
    *,
    repo_root: Path,
    assets_override=None,
    runs_dir_override: Path | None = None,
) -> SearchRunBundle:
    return _run_case(
        repo_root=repo_root,
        case_id="case-bootstrap-out-of-domain-generic",
        assets=_runtime_assets(base_assets=assets_override),
        requirement_payload=_ops_requirement_payload(),
        keyword_payload=_ops_keyword_payload(),
        pack_scores={
            "llm_agent_rag_engineering": 0.2,
            "search_ranking_retrieval_engineering": 0.1,
            "finance_risk_control_ai": 0.0,
        },
        controller_outputs=_phase_gated_stop_outputs(),
        final_summary="Out-of-domain route fell back to generic bootstrap.",
        runs_dir_override=runs_dir_override,
    )


def _build_legal_crossover_bundle(
    *,
    repo_root: Path,
    assets_override=None,
    runs_dir_override: Path | None = None,
) -> SearchRunBundle:
    assets = _runtime_assets(base_assets=assets_override)
    crossover_payload = _legal_crossover_round_two_payload(assets)
    return _run_case(
        repo_root=repo_root,
        case_id="case-crossover-legal",
        assets=assets,
        requirement_payload=_crossover_requirement_payload(),
        keyword_payload=_crossover_keyword_payload(),
        pack_scores=_llm_pack_scores(),
        controller_outputs=[
            _search_payload("core_precision", query_terms=["Python backend", "retrieval"]),
            _search_payload("must_have_alias", query_terms=["retrieval", "workflow"]),
            crossover_payload,
            _stop_payload(),
        ],
        candidate_scores=[
            {"candidate-crossover-1": 2.0},
            {"candidate-crossover-2": 1.7},
            {"candidate-crossover-3": 1.9},
        ],
        cts_results=[
            CTSFetchResult(
                request_payload={},
                candidates=[_candidate("candidate-crossover-1", search_text="python backend rag ranking")],
                raw_candidate_count=1,
                latency_ms=5,
            ),
            CTSFetchResult(
                request_payload={},
                candidates=[_candidate("candidate-crossover-2", search_text="python backend ranking workflow")],
                raw_candidate_count=1,
                latency_ms=5,
            ),
            CTSFetchResult(
                request_payload={},
                candidates=[_candidate("candidate-crossover-3", search_text="python backend workflow agent ranking")],
                raw_candidate_count=1,
                latency_ms=5,
            ),
        ],
        branch_outputs=[
            _branch_payload(novelty=0.8, usefulness=0.7, repair_operator_hint="core_precision"),
            _branch_payload(novelty=0.7, usefulness=0.6, repair_operator_hint="core_precision"),
            _branch_payload(novelty=0.7, usefulness=0.6, repair_operator_hint="crossover_compose"),
        ],
        final_summary="Legal crossover produced an expanded shortlist.",
        runs_dir_override=runs_dir_override,
    )


def _build_illegal_crossover_bundle(
    *,
    repo_root: Path,
    assets_override=None,
    runs_dir_override: Path | None = None,
) -> SearchRunBundle:
    return _run_case(
        repo_root=repo_root,
        case_id="case-crossover-illegal-reject",
        assets=_runtime_assets(base_assets=assets_override),
        requirement_payload=_crossover_requirement_payload(),
        keyword_payload=_crossover_keyword_payload(),
        pack_scores=_llm_pack_scores(),
        controller_outputs=[
            _search_payload("core_precision", query_terms=["Python backend", "retrieval"]),
            _search_payload("must_have_alias", query_terms=["retrieval", "workflow"]),
            [
                _crossover_payload("missing-donor"),
                _stop_payload(),
            ],
        ],
        candidate_scores=[
            {"candidate-illegal-1": 2.0},
            {"candidate-illegal-2": 1.8},
        ],
        cts_results=[
            CTSFetchResult(
                request_payload={},
                candidates=[_candidate("candidate-illegal-1", search_text="python backend rag ranking")],
                raw_candidate_count=1,
                latency_ms=5,
            ),
            CTSFetchResult(
                request_payload={},
                candidates=[_candidate("candidate-illegal-2", search_text="python backend workflow ranking")],
                raw_candidate_count=1,
                latency_ms=5,
            ),
        ],
        branch_outputs=[
            _branch_payload(novelty=0.8, usefulness=0.7, repair_operator_hint="core_precision"),
            _branch_payload(novelty=0.7, usefulness=0.6, repair_operator_hint="core_precision"),
        ],
        final_summary="Illegal crossover was rejected and the run stopped on retry.",
        runs_dir_override=runs_dir_override,
    )


def _build_direct_stop_accepted_bundle(
    *,
    repo_root: Path,
    assets_override=None,
    runs_dir_override: Path | None = None,
) -> SearchRunBundle:
    return _run_case(
        repo_root=repo_root,
        case_id="case-stop-controller-direct-accepted",
        assets=_runtime_assets(base_assets=assets_override),
        requirement_payload=_llm_requirement_payload(),
        keyword_payload=_llm_keyword_payload(),
        pack_scores=_llm_pack_scores(),
        controller_outputs=_phase_gated_stop_outputs(),
        final_summary="Controller stop was accepted in the first balance-phase stop round.",
        runs_dir_override=runs_dir_override,
    )


def _build_direct_stop_rejected_bundle(
    *,
    repo_root: Path,
    assets_override=None,
    runs_dir_override: Path | None = None,
) -> SearchRunBundle:
    return _run_case(
        repo_root=repo_root,
        case_id="case-stop-controller-direct-rejected",
        assets=_runtime_assets(base_assets=assets_override),
        requirement_payload=_llm_requirement_payload(),
        keyword_payload=_llm_keyword_payload(),
        pack_scores=_llm_pack_scores(),
        controller_outputs=_phase_gated_stop_outputs(),
        final_summary="Controller stop was rejected in explore and accepted in balance.",
        runs_dir_override=runs_dir_override,
    )


def _build_exhausted_low_gain_bundle(
    *,
    repo_root: Path,
    assets_override=None,
    runs_dir_override: Path | None = None,
) -> SearchRunBundle:
    keyword_payload = _llm_keyword_payload()
    keyword_payload["candidate_seeds"] = [
        {
            "intent_type": "core_precision",
            "keywords": ["python backend"],
            "source_knowledge_pack_ids": [],
            "reasoning": "deterministic core seed",
        },
        {
            "intent_type": "relaxed_floor",
            "keywords": ["python backend"],
            "source_knowledge_pack_ids": [],
            "reasoning": "deterministic relaxed seed",
        },
        {
            "intent_type": "must_have_alias",
            "keywords": ["python backend"],
            "source_knowledge_pack_ids": [],
            "reasoning": "deterministic alias seed",
        },
        {
            "intent_type": "pack_bridge",
            "keywords": ["python backend"],
            "source_knowledge_pack_ids": ["llm_agent_rag_engineering"],
            "reasoning": "deterministic pack seed",
        },
        {
            "intent_type": "vocabulary_bridge",
            "keywords": ["python backend"],
            "source_knowledge_pack_ids": [],
            "reasoning": "deterministic generic seed",
        },
    ]
    return _run_case(
        repo_root=repo_root,
        case_id="case-stop-exhausted-low-gain-and-finalize",
        assets=_runtime_assets(base_assets=assets_override),
        requirement_payload=_llm_requirement_payload(),
        keyword_payload=keyword_payload,
        pack_scores=_llm_pack_scores(),
        controller_outputs=[
            _search_payload("core_precision", query_terms=["python backend"]),
            _search_payload("core_precision", query_terms=["python backend"]),
            _search_payload("core_precision", query_terms=["python backend"]),
            _search_payload("core_precision", query_terms=["python backend"]),
        ],
        cts_results=[
            CTSFetchResult(request_payload={}, candidates=[], raw_candidate_count=0, latency_ms=5),
            CTSFetchResult(request_payload={}, candidates=[], raw_candidate_count=0, latency_ms=5),
            CTSFetchResult(request_payload={}, candidates=[], raw_candidate_count=0, latency_ms=5),
            CTSFetchResult(request_payload={}, candidates=[], raw_candidate_count=0, latency_ms=5),
        ],
        branch_outputs=[
            _branch_payload(novelty=0.1, usefulness=0.1, repair_operator_hint="core_precision"),
            _branch_payload(novelty=0.1, usefulness=0.1, repair_operator_hint="core_precision"),
            _branch_payload(novelty=0.1, usefulness=0.1, repair_operator_hint="core_precision"),
            _branch_payload(novelty=0.1, usefulness=0.1, repair_operator_hint="core_precision"),
        ],
        final_summary="Low-gain branch was exhausted and finalized.",
        runs_dir_override=runs_dir_override,
    )


def _run_case(
    *,
    repo_root: Path,
    case_id: str,
    assets: Any,
    requirement_payload: dict[str, object],
    keyword_payload: dict[str, object],
    pack_scores: dict[str, float],
    controller_outputs: list[object],
    final_summary: str,
    candidate_scores: list[dict[str, float]] | None = None,
    cts_results: list[CTSFetchResult] | None = None,
    branch_outputs: list[dict[str, object]] | None = None,
    runs_dir_override: Path | None = None,
) -> SearchRunBundle:
    del repo_root
    return run_match(
        job_description=str(requirement_payload["role_title_candidate"]),
        hiring_notes="canonical case",
        settings=AppSettings(
            _env_file=None,
            mock_cts=True,
            runs_dir=str(runs_dir_override or (runtime_case_dir(case_id) / "_tmp_runs")),
        ),
        env_file=None,
        assets=assets,
        cts_client=_FakeCTSClient(list(cts_results or [])),
        rerank_request=_FakeRerankRequest(
            pack_scores=dict(pack_scores),
            candidate_scores=list(candidate_scores or []),
        ),
        requirement_extraction_model=TestModel(custom_output_args=requirement_payload),
        bootstrap_keyword_generation_model=TestModel(custom_output_args=keyword_payload),
        search_controller_decision_model=_SequentialTestModel(outputs=list(controller_outputs)),
        branch_outcome_evaluation_model=(
            None if branch_outputs is None else _SequentialTestModel(outputs=list(branch_outputs))
        ),
        search_run_finalization_model=TestModel(custom_output_args={"run_summary": final_summary}),
    )


def _runtime_assets(
    *,
    knowledge_pack_id_override: str | None = None,
    base_assets=None,
):
    base_assets = base_assets or default_bootstrap_assets()
    return replace(
        base_assets,
        business_policy_pack=BusinessPolicyPack.model_validate(
            {
                **base_assets.business_policy_pack.model_dump(mode="python"),
                "knowledge_pack_id_override": knowledge_pack_id_override,
            }
        ),
        stop_guard_thresholds=base_assets.stop_guard_thresholds,
    )


def _legal_crossover_round_two_payload(assets) -> dict[str, object]:
    probe_bundle = run_match(
        job_description=str(_crossover_requirement_payload()["role_title_candidate"]),
        hiring_notes="canonical case",
        settings=AppSettings(
            _env_file=None,
            mock_cts=True,
            runs_dir="/tmp/seektalent-crossover-probe",
        ),
        env_file=None,
        assets=assets,
        cts_client=_FakeCTSClient(
            [
                CTSFetchResult(
                    request_payload={},
                    candidates=[_candidate("candidate-probe-1", search_text="python backend rag ranking")],
                    raw_candidate_count=1,
                    latency_ms=5,
                ),
                CTSFetchResult(
                    request_payload={},
                    candidates=[_candidate("candidate-probe-2", search_text="python backend ranking workflow")],
                    raw_candidate_count=1,
                    latency_ms=5,
                ),
            ]
        ),
        rerank_request=_FakeRerankRequest(
            pack_scores=_llm_pack_scores(),
            candidate_scores=[
                {"candidate-probe-1": 2.0},
                {"candidate-probe-2": 1.7},
            ],
        ),
        requirement_extraction_model=TestModel(custom_output_args=_crossover_requirement_payload()),
        bootstrap_keyword_generation_model=TestModel(custom_output_args=_crossover_keyword_payload()),
        search_controller_decision_model=_SequentialTestModel(
            outputs=[
                _search_payload("core_precision", query_terms=["Python backend", "retrieval"]),
                _search_payload("must_have_alias", query_terms=["retrieval", "workflow"]),
                _stop_payload(),
            ]
        ),
        branch_outcome_evaluation_model=_SequentialTestModel(
            outputs=[
                _branch_payload(novelty=0.8, usefulness=0.7, repair_operator_hint="core_precision"),
                _branch_payload(novelty=0.7, usefulness=0.6, repair_operator_hint="core_precision"),
            ]
        ),
        search_run_finalization_model=TestModel(custom_output_args={"run_summary": "probe"}),
    )
    round_two = probe_bundle.rounds[2]
    donors = round_two.controller_context.donor_candidate_node_summaries
    if not donors:
        raise ValueError("missing_round_two_legal_crossover_donor")
    donor_frontier_node_id = donors[0].frontier_node_id
    active_node = round_two.controller_context.active_frontier_node_summary
    donor_node = probe_bundle.rounds[1].frontier_state_after.frontier_nodes[donor_frontier_node_id]
    donor_terms_used = [
        term
        for term in donor_node.node_query_term_pool
        if term not in set(active_node.node_query_term_pool)
    ]
    return _crossover_payload(
        donor_frontier_node_id,
        shared_anchor_terms=donors[0].shared_anchor_terms,
        donor_terms_used=donor_terms_used,
    )


def _llm_pack_scores() -> dict[str, float]:
    return {
        "llm_agent_rag_engineering": 1.2,
        "search_ranking_retrieval_engineering": 0.2,
        "finance_risk_control_ai": 0.1,
    }


def _llm_requirement_payload() -> dict[str, object]:
    return {
        "role_title_candidate": "Senior Python / LLM Engineer",
        "role_summary_candidate": "Build Python, LLM, and retrieval systems.",
        "must_have_capability_candidates": [
            "Python backend",
            "LLM application",
            "retrieval pipeline",
        ],
        "preferred_capability_candidates": ["workflow orchestration", "tool calling"],
        "exclusion_signal_candidates": ["frontend"],
        "preference_candidates": {
            "preferred_domains": [],
            "preferred_backgrounds": [],
        },
        "hard_constraint_candidates": {
            "locations": ["Shanghai"],
            "min_years": 5,
            "max_years": 10,
        },
        "scoring_rationale_candidate": "Prioritize core must-have fit.",
    }


def _hybrid_requirement_payload() -> dict[str, object]:
    return {
        "role_title_candidate": "Agent Search Engineer",
        "role_summary_candidate": "Build agent and ranking systems together.",
        "must_have_capability_candidates": [
            "Python backend",
            "agent engineer",
            "ranking",
        ],
        "preferred_capability_candidates": ["workflow orchestration"],
        "exclusion_signal_candidates": ["sales"],
        "preference_candidates": {
            "preferred_domains": [],
            "preferred_backgrounds": [],
        },
        "hard_constraint_candidates": {
            "locations": ["Shanghai"],
            "min_years": 4,
            "max_years": 10,
        },
        "scoring_rationale_candidate": "Prefer direct technical overlap.",
    }


def _crossover_requirement_payload() -> dict[str, object]:
    return {
        "role_title_candidate": "Workflow Search Engineer",
        "role_summary_candidate": "Build workflow-aware search systems.",
        "must_have_capability_candidates": [
            "Python backend",
            "LLM",
            "retrieval",
            "workflow",
            "ranking",
            "agent",
        ],
        "preferred_capability_candidates": [],
        "exclusion_signal_candidates": ["frontend"],
        "preference_candidates": {
            "preferred_domains": [],
            "preferred_backgrounds": [],
        },
        "hard_constraint_candidates": {
            "locations": ["Shanghai"],
            "min_years": 5,
            "max_years": 10,
        },
        "scoring_rationale_candidate": "Keep crossover provenance explicit.",
    }


def _ops_requirement_payload() -> dict[str, object]:
    return {
        "role_title_candidate": "People Operations Manager",
        "role_summary_candidate": "Lead hiring operations and stakeholder management.",
        "must_have_capability_candidates": [
            "stakeholder management",
            "process design",
        ],
        "preferred_capability_candidates": ["hiring operations"],
        "exclusion_signal_candidates": ["sales"],
        "preference_candidates": {
            "preferred_domains": [],
            "preferred_backgrounds": [],
        },
        "hard_constraint_candidates": {
            "locations": ["Shanghai"],
            "min_years": 5,
            "max_years": 12,
        },
        "scoring_rationale_candidate": "Keep the search broad when domain signal is weak.",
    }


def _llm_keyword_payload() -> dict[str, object]:
    return {
        "candidate_seeds": [
            {
                "intent_type": "core_precision",
                "keywords": ["agent engineer", "rag", "python backend"],
                "source_knowledge_pack_ids": [],
                "reasoning": "anchor the route",
            },
            {
                "intent_type": "must_have_alias",
                "keywords": ["llm application", "retrieval pipeline"],
                "source_knowledge_pack_ids": [],
                "reasoning": "cover aliases",
            },
            {
                "intent_type": "relaxed_floor",
                "keywords": ["python backend", "retrieval"],
                "source_knowledge_pack_ids": [],
                "reasoning": "widen recall",
            },
            {
                "intent_type": "pack_bridge",
                "keywords": ["workflow orchestration", "tool calling"],
                "source_knowledge_pack_ids": ["llm_agent_rag_engineering"],
                "reasoning": "use pack hints",
            },
            {
                "intent_type": "vocabulary_bridge",
                "keywords": ["backend engineer", "agent workflow"],
                "source_knowledge_pack_ids": [],
                "reasoning": "extra route",
            },
        ],
        "negative_keywords": ["frontend"],
    }


def _hybrid_keyword_payload() -> dict[str, object]:
    return {
        "candidate_seeds": [
            {
                "intent_type": "core_precision",
                "keywords": ["agent engineer", "ranking", "python backend"],
                "source_knowledge_pack_ids": [],
                "reasoning": "anchor the hybrid route",
            },
            {
                "intent_type": "must_have_alias",
                "keywords": ["retrieval pipeline", "ranking"],
                "source_knowledge_pack_ids": [],
                "reasoning": "cover aliases",
            },
            {
                "intent_type": "relaxed_floor",
                "keywords": ["python backend", "agent"],
                "source_knowledge_pack_ids": [],
                "reasoning": "widen recall",
            },
            {
                "intent_type": "pack_bridge",
                "keywords": ["workflow orchestration", "tool calling"],
                "source_knowledge_pack_ids": ["llm_agent_rag_engineering"],
                "reasoning": "expand through llm pack",
            },
            {
                "intent_type": "pack_bridge",
                "keywords": ["agent ranking", "retrieval workflow"],
                "source_knowledge_pack_ids": [
                    "llm_agent_rag_engineering",
                    "search_ranking_retrieval_engineering",
                ],
                "reasoning": "bridge both packs",
            },
            {
                "intent_type": "vocabulary_bridge",
                "keywords": ["search backend", "reranker"],
                "source_knowledge_pack_ids": [],
                "reasoning": "extra route",
            },
        ],
        "negative_keywords": ["sales"],
    }


def _crossover_keyword_payload() -> dict[str, object]:
    return {
        "candidate_seeds": [
            {
                "intent_type": "core_precision",
                "keywords": ["Python backend", "LLM", "retrieval"],
                "source_knowledge_pack_ids": [],
                "reasoning": "anchor the route",
            },
            {
                "intent_type": "must_have_alias",
                "keywords": ["workflow", "ranking"],
                "source_knowledge_pack_ids": [],
                "reasoning": "cover aliases",
            },
            {
                "intent_type": "relaxed_floor",
                "keywords": ["Python backend", "workflow"],
                "source_knowledge_pack_ids": [],
                "reasoning": "widen recall",
            },
            {
                "intent_type": "pack_bridge",
                "keywords": ["agent", "ranking"],
                "source_knowledge_pack_ids": ["llm_agent_rag_engineering"],
                "reasoning": "use pack hints",
            },
            {
                "intent_type": "vocabulary_bridge",
                "keywords": ["retrieval engineer", "workflow systems"],
                "source_knowledge_pack_ids": [],
                "reasoning": "extra route",
            },
        ],
        "negative_keywords": ["frontend"],
    }


def _ops_keyword_payload() -> dict[str, object]:
    return {
        "candidate_seeds": [
            {
                "intent_type": "core_precision",
                "keywords": ["process design", "operations manager"],
                "source_knowledge_pack_ids": [],
                "reasoning": "anchor the route",
            },
            {
                "intent_type": "must_have_alias",
                "keywords": ["stakeholder management", "operations"],
                "source_knowledge_pack_ids": [],
                "reasoning": "cover aliases",
            },
            {
                "intent_type": "relaxed_floor",
                "keywords": ["operations", "manager"],
                "source_knowledge_pack_ids": [],
                "reasoning": "widen recall",
            },
            {
                "intent_type": "vocabulary_bridge",
                "keywords": ["hiring operations", "workflow"],
                "source_knowledge_pack_ids": [],
                "reasoning": "generic expansion",
            },
            {
                "intent_type": "vocabulary_bridge",
                "keywords": ["process improvement", "team operations"],
                "source_knowledge_pack_ids": [],
                "reasoning": "secondary route",
            },
        ],
        "negative_keywords": ["sales"],
    }


def _stop_payload() -> dict[str, object]:
    return {
        "action": "stop",
        "selected_operator_name": "must_have_alias",
        "operator_args": {},
        "expected_gain_hypothesis": "Stop.",
    }


def _search_payload(operator_name: str, *, query_terms: list[str]) -> dict[str, object]:
    return {
        "action": "search_cts",
        "selected_operator_name": operator_name,
        "operator_args": {"query_terms": query_terms},
        "expected_gain_hypothesis": "Expand coverage.",
    }


def _phase_gated_stop_outputs() -> list[dict[str, object]]:
    return [_stop_payload(), _stop_payload(), _stop_payload()]


def _crossover_payload(
    donor_frontier_node_id: str,
    *,
    shared_anchor_terms: list[str] | None = None,
    donor_terms_used: list[str] | None = None,
) -> dict[str, object]:
    return {
        "action": "search_cts",
        "selected_operator_name": "crossover_compose",
        "operator_args": {
            "donor_frontier_node_id": donor_frontier_node_id,
            "shared_anchor_terms": list(shared_anchor_terms or ["ranking"]),
            "donor_terms_used": list(donor_terms_used or ["workflow systems"]),
            "crossover_rationale": "reuse donor workflow-ranking signal from the relaxed floor branch",
        },
        "expected_gain_hypothesis": "Fuse donor coverage.",
    }


def _branch_payload(
    *,
    novelty: float,
    usefulness: float,
    repair_operator_hint: str,
) -> dict[str, object]:
    return {
        "novelty_score": novelty,
        "usefulness_score": usefulness,
        "branch_exhausted": False,
        "repair_operator_hint": repair_operator_hint,
        "evaluation_notes": "Canonical branch evaluation.",
    }


def _candidate(candidate_id: str, *, search_text: str) -> RetrievedCandidate_t:
    return RetrievedCandidate_t(
        candidate_id=candidate_id,
        now_location="Shanghai",
        expected_location="Shanghai",
        years_of_experience_raw=6,
        education_summaries=["复旦大学 计算机 本科"],
        work_experience_summaries=[
            "TestCo | Python Engineer | Built retrieval ranking systems."
        ],
        project_names=["retrieval platform"],
        work_summaries=["python", "ranking"],
        search_text=build_candidate_search_text(
            role_title="Python Engineer",
            locations=["Shanghai"],
            projects=["retrieval platform"],
            work_summaries=[search_text],
            education_summaries=["复旦大学 计算机 本科"],
            work_experience_summaries=[
                "TestCo | Python Engineer | Built retrieval ranking systems."
            ],
        ),
        raw_payload={"title": "Python Engineer", "workExperienceList": []},
    )


def _selected_packs(bundle: SearchRunBundle) -> list[DomainKnowledgePack]:
    selected_pack_ids = set(bundle.bootstrap.routing_result.selected_knowledge_pack_ids)
    if not selected_pack_ids:
        return []
    assets = default_bootstrap_assets()
    return [
        pack
        for pack in assets.knowledge_packs
        if pack.knowledge_pack_id in selected_pack_ids
    ]


def _seed_terms(bundle: SearchRunBundle) -> list[str]:
    return [
        term
        for seed in bundle.bootstrap.bootstrap_output.frontier_seed_specifications
        for term in seed.seed_terms
    ]


def _include_keyword_adoption(bundle: SearchRunBundle, packs: list[DomainKnowledgePack]) -> float:
    include_keywords = stable_deduplicate(
        [keyword for pack in packs for keyword in pack.include_keywords]
    )
    if not include_keywords:
        return 0.0
    seed_terms = _seed_terms(bundle)
    hits = sum(1 for keyword in include_keywords if _any_text_hit(keyword, seed_terms))
    return hits / len(include_keywords)


def _exclude_keyword_leak(bundle: SearchRunBundle, packs: list[DomainKnowledgePack]) -> bool:
    return any(
        _any_text_hit(keyword, _seed_terms(bundle))
        for pack in packs
        for keyword in pack.exclude_keywords
    )


def _any_text_hit(keyword: str, haystack: list[str]) -> bool:
    needle = _normalize_text(keyword)
    return any(needle in _normalize_text(item) or _normalize_text(item) in needle for item in haystack)


def _normalize_text(value: str | None) -> str:
    return " ".join((value or "").split()).strip().casefold()


def _clear_generated_outputs(repo_root: Path) -> None:
    for path in (
        repo_root / "artifacts" / "runtime" / "cases",
        repo_root / "artifacts" / "runtime" / "evals",
        repo_root / "docs" / "v-0.3.3" / "traces" / "agent",
        repo_root / "docs" / "v-0.3.3" / "traces" / "business",
    ):
        if path.exists():
            shutil.rmtree(path)


def _write_case_artifacts(spec: CanonicalCaseSpec, bundle: SearchRunBundle) -> None:
    case_dir = runtime_case_dir(spec.case_id)
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "spec.json").write_text(
        json.dumps(spec.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (case_dir / "bundle.json").write_text(
        json.dumps(bundle.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (case_dir / "judge_packet.json").write_text(
        json.dumps(_judge_packet(spec, bundle), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (case_dir / "eval.json").write_text(
        json.dumps(build_case_eval(spec, bundle), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_trace_docs(spec: CanonicalCaseSpec, bundle: SearchRunBundle, *, repo_root: Path) -> None:
    agent_dir = repo_root / "docs" / "v-0.3.3" / "traces" / "agent"
    business_dir = repo_root / "docs" / "v-0.3.3" / "traces" / "business"
    agent_dir.mkdir(parents=True, exist_ok=True)
    business_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / f"trace-agent-{spec.case_id}.md").write_text(
        _render_agent_trace(spec, bundle),
        encoding="utf-8",
    )
    (business_dir / f"trace-business-{spec.case_id}.md").write_text(
        _render_business_trace(spec, bundle),
        encoding="utf-8",
    )


def _render_agent_trace(spec: CanonicalCaseSpec, bundle: SearchRunBundle) -> str:
    final_candidate_ids = [
        card.candidate_id for card in bundle.final_result.final_candidate_cards
    ]
    round_rows = "\n".join(
        f"| {round_artifact.runtime_round_index} | "
        f"{round_artifact.controller_context.runtime_budget_state.search_phase} | "
        f"{round_artifact.controller_decision.action} | "
        f"{round_artifact.controller_decision.selected_operator_name} | "
        f"{_bool_text(round_artifact.continue_flag)} | "
        f"{round_artifact.stop_reason or 'None'} | "
        f"{_round_outcome(round_artifact)} |"
        for round_artifact in bundle.rounds
    ) or "| 0 | explore | stop | must_have_alias | no | controller_stop | terminated |"
    return (
        f"# Agent Trace: {spec.case_id}\n\n"
        "## Trace Meta\n\n"
        "```yaml\n"
        f"case_id: {spec.case_id}\n"
        f"routing_mode: {bundle.bootstrap.routing_result.routing_mode}\n"
        f"selected_knowledge_pack_ids: {bundle.bootstrap.routing_result.selected_knowledge_pack_ids}\n"
        f"stop_reason: {bundle.final_result.stop_reason}\n"
        f"run_dir: {bundle.run_dir}\n"
        "```\n\n"
        "## Bootstrap\n\n"
        f"- routing_mode: `{bundle.bootstrap.routing_result.routing_mode}`\n"
        f"- selected_knowledge_pack_ids: `{bundle.bootstrap.routing_result.selected_knowledge_pack_ids}`\n"
        f"- fallback_reason: `{bundle.bootstrap.routing_result.fallback_reason}`\n"
        f"- seed_count: `{len(bundle.bootstrap.bootstrap_output.frontier_seed_specifications)}`\n\n"
        "## Runtime Rounds\n\n"
        "| round | phase | action | operator | continue_flag | stop_reason | round_outcome |\n"
        "| --- | --- | --- | --- | --- | --- | --- |\n"
        f"{round_rows}\n\n"
        "## Final Result\n\n"
        f"- final_candidate_ids: `{final_candidate_ids}`\n"
        f"- stop_reason: `{bundle.final_result.stop_reason}`\n"
        f"- Bundle Run Summary: {bundle.final_result.run_summary}\n"
    )


def _render_business_trace(spec: CanonicalCaseSpec, bundle: SearchRunBundle) -> str:
    final_candidate_ids = [
        card.candidate_id for card in bundle.final_result.final_candidate_cards
    ]
    routing = bundle.bootstrap.routing_result
    round_rows = "\n".join(
        f"| {round_artifact.runtime_round_index} | "
        f"{round_artifact.controller_context.runtime_budget_state.search_phase} | "
        f"{round_artifact.controller_decision.action} | "
        f"{_bool_text(round_artifact.continue_flag)} | "
        f"{round_artifact.stop_reason or 'None'} | "
        f"{_round_outcome(round_artifact)} |"
        for round_artifact in bundle.rounds
    ) or "| 0 | explore | stop | no | controller_stop | terminated |"
    return (
        f"# Business Trace: {spec.case_id}\n\n"
        "## 场景背景\n\n"
        f"- 场景：{spec.scenario}\n"
        f"- 业务解释：{spec.business_context}\n\n"
        "## Observed Facts\n\n"
        f"- 路由结果：`{routing.routing_mode}`\n"
        f"- 领域知识包：`{routing.selected_knowledge_pack_ids}`\n"
        f"- fallback_reason：`{routing.fallback_reason}`\n"
        f"- 终止原因：`{bundle.final_result.stop_reason}`\n"
        f"- final_candidate_ids：`{final_candidate_ids}`\n\n"
        "| round | phase | action | continue_flag | stop_reason | round_outcome |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        f"{round_rows}\n\n"
        "## Case Expectations (spec-derived)\n\n"
        f"- expected_route：`{spec.expected_route}`\n"
        f"- expected_stop_reason：`{spec.expected_stop_reason}`\n"
        f"- must_hold：{'; '.join(spec.must_hold)}\n"
        f"- must_not_hold：{'; '.join(spec.must_not_hold)}\n"
    )


def _judge_packet(spec: CanonicalCaseSpec, bundle: SearchRunBundle) -> dict[str, object]:
    routing = bundle.bootstrap.routing_result
    return {
        "case_id": spec.case_id,
        "expected_route": spec.expected_route,
        "observed_route": routing.routing_mode,
        "expected_knowledge_pack_ids": spec.expected_knowledge_pack_ids,
        "observed_knowledge_pack_ids": routing.selected_knowledge_pack_ids,
        "expected_fallback_reason": spec.expected_fallback_reason,
        "observed_fallback_reason": routing.fallback_reason,
        "expected_stop_reason": spec.expected_stop_reason,
        "observed_stop_reason": bundle.final_result.stop_reason,
        "must_hold": spec.must_hold,
        "must_not_hold": spec.must_not_hold,
        "route_match": routing.routing_mode == spec.expected_route,
        "selected_packs_match": routing.selected_knowledge_pack_ids == spec.expected_knowledge_pack_ids,
        "stop_reason_match": bundle.final_result.stop_reason == spec.expected_stop_reason,
    }


def _canonical_bundle(bundle: SearchRunBundle, *, case_id: str) -> SearchRunBundle:
    return bundle.model_copy(
        update={
            "run_id": case_id,
            "run_dir": str(runtime_case_dir(case_id)),
            "created_at_utc": "2026-04-09T00:00:00Z",
            "eval": (
                None
                if bundle.eval is None
                else bundle.eval.model_copy(update={"run_id": case_id})
            ),
        }
    )


def _write_eval_matrix(rows: list[dict[str, object]]) -> None:
    path = runtime_eval_matrix_file("E5")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_trace_index(specs: tuple[CanonicalCaseSpec, ...], *, repo_root: Path) -> None:
    rows = "\n".join(
        f"| `{spec.case_id}` | {spec.scenario} | "
        f"[trace-agent-{spec.case_id}](./traces/agent/trace-agent-{spec.case_id}.md) | "
        f"[trace-business-{spec.case_id}](./traces/business/trace-business-{spec.case_id}.md) |"
        for spec in specs
    )
    content = (
        "# SeekTalent v0.3.3 Trace Index\n\n"
        "> 本页由 phase6 canonical case builder 生成。Agent Trace 由 canonical bundle 渲染；Business Trace 由 canonical bundle 与 case spec 共同渲染。\n\n"
        "## Case Matrix\n\n"
        "| case_id | 场景 | Agent Trace | Business Trace |\n"
        "| --- | --- | --- | --- |\n"
        f"{rows}\n"
    )
    (repo_root / "docs" / "v-0.3.3" / "trace-index.md").write_text(content, encoding="utf-8")


def _round_outcome(round_artifact) -> str:
    if round_artifact.controller_decision.action == "stop":
        if round_artifact.continue_flag:
            return "stop rejected by phase gate"
        return "terminated"
    return "continued" if round_artifact.continue_flag else "terminated"


def _bool_text(value: bool) -> str:
    return "yes" if value else "no"


__all__ = [
    "CanonicalCaseSpec",
    "build_all_canonical_artifacts",
    "build_case_bundle",
    "build_case_eval",
    "canonical_case_specs",
]
