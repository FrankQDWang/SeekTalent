from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from seektalent.bootstrap_assets import BootstrapAssets, default_bootstrap_assets
from seektalent.bootstrap_llm import (
    request_bootstrap_keyword_draft,
    request_requirement_extraction_draft,
)
from seektalent.bootstrap_ops import (
    freeze_scoring_policy,
    generate_bootstrap_output,
    initialize_frontier_state,
    route_domain_knowledge_pack,
)
from seektalent.models import (
    BootstrapOutput,
    BootstrapRoutingResult,
    FrontierState_t,
    LLMCallAudit,
    RequirementSheet,
    ScoringPolicy,
    SearchInputTruth,
)
from seektalent.requirements import build_input_truth, normalize_requirement_draft
from seektalent.search_ops import AsyncRerankRequest
from seektalent.runtime_budget import derive_round0_seed_max_query_terms


@dataclass(frozen=True)
class BootstrapArtifacts:
    input_truth: SearchInputTruth
    requirement_extraction_audit: LLMCallAudit
    requirement_sheet: RequirementSheet
    routing_result: BootstrapRoutingResult
    scoring_policy: ScoringPolicy
    bootstrap_keyword_generation_audit: LLMCallAudit
    bootstrap_output: BootstrapOutput
    frontier_state: FrontierState_t


async def bootstrap_round0_async(
    *,
    job_description: str,
    hiring_notes: str = "",
    assets: BootstrapAssets | None = None,
    rerank_request: AsyncRerankRequest | None = None,
    requirement_extraction_model: Any | None = None,
    bootstrap_keyword_generation_model: Any | None = None,
    env_file: str | Path | None = ".env",
) -> BootstrapArtifacts:
    active_assets = assets or default_bootstrap_assets()
    if rerank_request is None and not active_assets.business_policy_pack.knowledge_pack_id_override:
        raise ValueError(
            "bootstrap_round0_async requires rerank_request when knowledge_pack_id_override is empty"
        )
    input_truth = build_input_truth(job_description=job_description, hiring_notes=hiring_notes)
    requirement_draft, requirement_extraction_audit = await request_requirement_extraction_draft(
        input_truth,
        model=requirement_extraction_model,
        env_file=env_file,
    )
    requirement_sheet = normalize_requirement_draft(requirement_draft, input_truth=input_truth)
    routing_result = await route_domain_knowledge_pack(
        requirement_sheet,
        active_assets.business_policy_pack,
        active_assets.knowledge_packs,
        active_assets.reranker_calibration,
        rerank_request=rerank_request,
    )
    selected_knowledge_packs = [
        pack
        for pack in active_assets.knowledge_packs
        if pack.knowledge_pack_id in set(routing_result.selected_knowledge_pack_ids)
    ]
    scoring_policy = freeze_scoring_policy(
        requirement_sheet,
        active_assets.business_policy_pack,
        active_assets.reranker_calibration,
    )
    max_seed_terms = derive_round0_seed_max_query_terms(
        active_assets.runtime_term_budget_policy
    )
    keyword_draft, bootstrap_keyword_generation_audit = await request_bootstrap_keyword_draft(
        requirement_sheet,
        routing_result,
        selected_knowledge_packs,
        max_seed_terms=max_seed_terms,
        model=bootstrap_keyword_generation_model,
        env_file=env_file,
    )
    bootstrap_output = generate_bootstrap_output(
        requirement_sheet,
        routing_result,
        selected_knowledge_packs,
        keyword_draft,
        max_seed_terms,
    )
    frontier_state = initialize_frontier_state(
        bootstrap_output,
        active_assets.runtime_search_budget,
        active_assets.operator_catalog,
    )
    return BootstrapArtifacts(
        input_truth=input_truth,
        requirement_extraction_audit=requirement_extraction_audit,
        requirement_sheet=requirement_sheet,
        routing_result=routing_result,
        scoring_policy=scoring_policy,
        bootstrap_keyword_generation_audit=bootstrap_keyword_generation_audit,
        bootstrap_output=bootstrap_output,
        frontier_state=frontier_state,
    )


def bootstrap_round0(
    *,
    job_description: str,
    hiring_notes: str = "",
    assets: BootstrapAssets | None = None,
    rerank_request: AsyncRerankRequest | None = None,
    requirement_extraction_model: Any | None = None,
    bootstrap_keyword_generation_model: Any | None = None,
    env_file: str | Path | None = ".env",
) -> BootstrapArtifacts:
    return asyncio.run(
        bootstrap_round0_async(
            job_description=job_description,
            hiring_notes=hiring_notes,
            assets=assets,
            rerank_request=rerank_request,
            requirement_extraction_model=requirement_extraction_model,
            bootstrap_keyword_generation_model=bootstrap_keyword_generation_model,
            env_file=env_file,
        )
    )


__all__ = ["BootstrapArtifacts", "bootstrap_round0", "bootstrap_round0_async"]
