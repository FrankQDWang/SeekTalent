from __future__ import annotations

from hashlib import sha1
from typing import Any

from seektalent.models import (
    BootstrapRoutingResult,
    DomainKnowledgePack,
    FrontierNode_t,
    FrontierState_t1,
    LLMCallAudit,
    PromptSurfaceSection,
    PromptSurfaceSnapshot,
    RequirementSheet,
    RuntimeBudgetState,
    SearchControllerContext_t,
    SearchExecutionPlan_t,
    SearchExecutionResult_t,
    SearchInputTruth,
    SearchRoundArtifact,
    SearchScoringResult_t,
    stable_deduplicate,
)
from seektalent.query_terms import query_terms_hit

RETRIES = 0
OUTPUT_RETRIES = 1
STRICT_MODEL_SETTINGS = {
    "allow_text_output": False,
    "allow_image_output": False,
}


def build_llm_call_audit(
    *,
    model: Any | None,
    prompt_surface: PromptSurfaceSnapshot,
    validator_retry_count: int,
) -> LLMCallAudit:
    return LLMCallAudit(
        output_mode="NativeOutput(strict=True)",
        retries=RETRIES,
        output_retries=OUTPUT_RETRIES,
        validator_retry_count=validator_retry_count,
        model_name=_model_name(model),
        model_settings_snapshot={**STRICT_MODEL_SETTINGS, "native_output_strict": True},
        prompt_surface=prompt_surface,
    )


def build_requirement_extraction_prompt_surface(
    input_truth: SearchInputTruth,
    *,
    instructions_text: str,
) -> PromptSurfaceSnapshot:
    return _build_prompt_surface(
        "requirement_extraction",
        instructions_text,
        [
            _section(
                "Task Contract",
                [
                    "Extract a strict structured requirement draft from the provided hiring inputs.",
                    "Use only the provided job description and hiring notes.",
                    "Keep must-haves, preferred signals, exclusions, and hard constraints separated.",
                    "Do not upgrade ambiguous preferences into hard constraints.",
                ],
                [],
            ),
            _section(
                "Job Description",
                [input_truth.job_description or "None"],
                ["SearchInputTruth.job_description"],
                is_dynamic=True,
            ),
            _section(
                "Hiring Notes",
                [_or_none(input_truth.hiring_notes)],
                ["SearchInputTruth.hiring_notes"],
                is_dynamic=True,
            ),
            _section(
                "Return Fields",
                [
                    "Return role_title_candidate, role_summary_candidate, must_have_capability_candidates, preferred_capability_candidates, exclusion_signal_candidates, preference_candidates, hard_constraint_candidates, and scoring_rationale_candidate.",
                ],
                [],
            ),
        ],
    )


def build_bootstrap_keyword_generation_prompt_surface(
    requirement_sheet: RequirementSheet,
    routing_result: BootstrapRoutingResult,
    selected_knowledge_packs: list[DomainKnowledgePack],
    *,
    instructions_text: str,
) -> PromptSurfaceSnapshot:
    pack_lines = [
        (
            f"{pack.knowledge_pack_id} | {pack.label} | domain_summary={_or_none(pack.routing_text)} | "
            f"positive_hints={_comma_list(pack.include_keywords)} | negative_hints={_comma_list(pack.exclude_keywords)}"
        )
        for pack in selected_knowledge_packs
    ] or ["None"]
    return _build_prompt_surface(
        "bootstrap_keyword_generation",
        instructions_text,
        [
            _section(
                "Task Contract",
                [
                    "Generate round-0 seed intents from the provided requirement summary, routing result, and selected knowledge packs.",
                    "Do not use any runtime or candidate information outside this packet.",
                ],
                [],
            ),
            _section(
                "Requirement Summary",
                [
                    f"Role title: {requirement_sheet.role_title}",
                    f"Role focus: {_or_none(requirement_sheet.role_summary)}",
                    f"Must-have capabilities: {_comma_list(requirement_sheet.must_have_capabilities)}",
                    f"Preferred capabilities: {_comma_list(requirement_sheet.preferred_capabilities)}",
                    f"Exclusion signals: {_comma_list(requirement_sheet.exclusion_signals)}",
                    f"Locations: {_comma_list(requirement_sheet.hard_constraints.locations)}",
                    f"Min years: {_or_none(requirement_sheet.hard_constraints.min_years)}",
                    f"Max years: {_or_none(requirement_sheet.hard_constraints.max_years)}",
                    f"Company names: {_comma_list(requirement_sheet.hard_constraints.company_names)}",
                    f"School names: {_comma_list(requirement_sheet.hard_constraints.school_names)}",
                    f"Degree requirement: {_or_none(requirement_sheet.hard_constraints.degree_requirement)}",
                    f"Gender requirement: {_or_none(requirement_sheet.hard_constraints.gender_requirement)}",
                    f"Min age: {_or_none(requirement_sheet.hard_constraints.min_age)}",
                    f"Max age: {_or_none(requirement_sheet.hard_constraints.max_age)}",
                ],
                [
                    "RequirementSheet.role_title",
                    "RequirementSheet.role_summary",
                    "RequirementSheet.must_have_capabilities",
                    "RequirementSheet.preferred_capabilities",
                    "RequirementSheet.exclusion_signals",
                    "RequirementSheet.hard_constraints",
                ],
                is_dynamic=True,
            ),
            _section(
                "Routing Result",
                [
                    f"Routing mode: {routing_result.routing_mode}",
                    f"Selected knowledge pack ids: {_comma_list(routing_result.selected_knowledge_pack_ids)}",
                    f"Routing confidence: {routing_result.routing_confidence:.2f}",
                    f"Fallback reason: {_or_none(routing_result.fallback_reason)}",
                ],
                [
                    "BootstrapRoutingResult.routing_mode",
                    "BootstrapRoutingResult.selected_knowledge_pack_ids",
                    "BootstrapRoutingResult.routing_confidence",
                    "BootstrapRoutingResult.fallback_reason",
                ],
                is_dynamic=True,
            ),
            _section(
                "Selected Knowledge Packs",
                pack_lines,
                ["DomainKnowledgePack.knowledge_pack_id", "DomainKnowledgePack.label", "DomainKnowledgePack.routing_text", "DomainKnowledgePack.include_keywords", "DomainKnowledgePack.exclude_keywords"],
                is_dynamic=True,
            ),
            _section(
                "Return Fields",
                [
                    "Return candidate_seeds and negative_keywords.",
                    "Each candidate seed must include intent_type, keywords, source_knowledge_pack_ids, and reasoning.",
                ],
                [],
            ),
        ],
    )


def build_controller_prompt_surface(
    context: SearchControllerContext_t,
    *,
    instructions_text: str,
) -> PromptSurfaceSnapshot:
    sections = [
        _section(
            "Task Contract",
            [
                "Use only the provided controller context.",
                "Pick a legal operator from allowed_operator_names.",
                "Do not invent unsupported operators or donor ids outside the provided candidate list.",
            ],
            [],
        ),
        _section(
            "Role Summary",
            [
                f"Role title: {context.role_title}",
                f"Role focus: {_or_none(context.role_summary)}",
            ],
            [
                "SearchControllerContext_t.role_title",
                "SearchControllerContext_t.role_summary",
            ],
            is_dynamic=True,
        ),
        _section(
            "Active Frontier Node",
            [
                f"Frontier node id: {context.active_frontier_node_summary.frontier_node_id}",
                f"Current operator: {context.active_frontier_node_summary.selected_operator_name}",
                f"Query term pool: {_comma_list(context.active_frontier_node_summary.node_query_term_pool)}",
                f"Current node shortlist ids: {_comma_list(context.active_frontier_node_summary.node_shortlist_candidate_ids)}",
            ],
            ["SearchControllerContext_t.active_frontier_node_summary"],
            is_dynamic=True,
        ),
        _section(
            "Decision Snapshot",
            _controller_decision_snapshot_lines(context),
            [
                "SearchControllerContext_t.unmet_requirement_weights",
                "SearchControllerContext_t.operator_surface_unmet_must_haves",
                "SearchControllerContext_t.donor_candidate_node_summaries",
                "SearchControllerContext_t.runtime_budget_state",
                "SearchControllerContext_t.max_query_terms",
            ],
            is_dynamic=True,
        ),
        _section(
            "Donor Candidates",
            _controller_donor_lines(context),
            ["SearchControllerContext_t.donor_candidate_node_summaries"],
            is_dynamic=True,
        ),
        _section(
            "Allowed Operators",
            [
                f"Allowed operators: {_comma_list(context.allowed_operator_names)}",
                f"Operator surface override: {context.operator_surface_override_reason}",
                "Operator surface unmet must-haves: "
                f"{_comma_list(context.operator_surface_unmet_must_haves)}",
            ],
            [
                "SearchControllerContext_t.allowed_operator_names",
                "SearchControllerContext_t.operator_surface_override_reason",
                "SearchControllerContext_t.operator_surface_unmet_must_haves",
            ],
            is_dynamic=True,
        ),
        _section(
            "Rewrite Evidence",
            _controller_rewrite_evidence_lines(context),
            ["SearchControllerContext_t.rewrite_term_candidates"],
            is_dynamic=True,
        ),
        _section(
            "Operator Statistics",
            _controller_operator_stat_lines(context),
            ["SearchControllerContext_t.operator_statistics_summary"],
            is_dynamic=True,
        ),
        _section(
            "Fit Gates And Unmet Requirements",
            _controller_fit_and_requirement_lines(context),
                [
                    "SearchControllerContext_t.fit_gate_constraints",
                    "SearchControllerContext_t.unmet_requirement_weights",
                    "SearchControllerContext_t.max_query_terms",
                ],
                is_dynamic=True,
            ),
        _section(
            "Runtime Budget State",
            _controller_budget_lines(context.runtime_budget_state),
            ["SearchControllerContext_t.runtime_budget_state"],
            is_dynamic=True,
        ),
    ]
    if context.runtime_budget_state.near_budget_end:
        sections.append(
            _section(
                "Budget Warning",
                [
                    "The run is in the last 20% of total budget.",
                    "Favor high-yield precision moves.",
                    "Avoid speculative expansion unless must-have coverage is still missing.",
                ],
                ["SearchControllerContext_t.runtime_budget_state.near_budget_end"],
                is_dynamic=True,
            )
        )
    sections.append(
        _section(
            "Decision Request",
            [
                "Return action, selected_operator_name, operator_args, and expected_gain_hypothesis.",
                "The answer must target the active frontier node only.",
            ],
            [],
        )
    )
    return _build_prompt_surface(
        "search_controller_decision",
        instructions_text,
        sections,
    )


def build_branch_evaluation_prompt_surface(
    requirement_sheet: RequirementSheet,
    parent_node: FrontierNode_t,
    plan: SearchExecutionPlan_t,
    execution_result: SearchExecutionResult_t,
    scoring_result: SearchScoringResult_t,
    runtime_budget_state: RuntimeBudgetState,
    *,
    instructions_text: str,
) -> PromptSurfaceSnapshot:
    sections = [
        _section(
            "Evaluation Contract",
            [
                "Use only the provided branch evaluation packet.",
                "Do not rewrite runtime facts outside the draft fields.",
            ],
            [],
        ),
        _section(
            "Role Summary",
            [
                f"Role title: {requirement_sheet.role_title}",
                f"Role focus: {_or_none(requirement_sheet.role_summary)}",
                f"Must-have capabilities: {_comma_list(requirement_sheet.must_have_capabilities)}",
                f"Preferred capabilities: {_comma_list(requirement_sheet.preferred_capabilities)}",
            ],
            [
                "RequirementSheet.role_title",
                "RequirementSheet.role_summary",
                "RequirementSheet.must_have_capabilities",
                "RequirementSheet.preferred_capabilities",
            ],
            is_dynamic=True,
        ),
        _section(
            "Branch Facts",
            [
                f"Parent frontier node id: {parent_node.frontier_node_id}",
                f"Previous node shortlist ids: {_comma_list(parent_node.node_shortlist_candidate_ids)}",
                f"Donor frontier node id: {_or_none(plan.child_frontier_node_stub.donor_frontier_node_id)}",
                f"Knowledge pack ids: {_comma_list(plan.knowledge_pack_ids)}",
                f"Query terms: {_comma_list(plan.query_terms)}",
                f"Semantic hash: {plan.semantic_hash}",
            ],
            [
                "FrontierNode_t.frontier_node_id",
                "FrontierNode_t.node_shortlist_candidate_ids",
                "SearchExecutionPlan_t.child_frontier_node_stub.donor_frontier_node_id",
                "SearchExecutionPlan_t.knowledge_pack_ids",
                "SearchExecutionPlan_t.query_terms",
                "SearchExecutionPlan_t.semantic_hash",
            ],
            is_dynamic=True,
        ),
        _section(
            "Search And Scoring Summary",
            [
                f"Pages fetched: {execution_result.search_page_statistics.pages_fetched}",
                f"Duplicate rate: {execution_result.search_page_statistics.duplicate_rate:.2f}",
                f"Latency ms: {execution_result.search_page_statistics.latency_ms}",
                f"Node shortlist ids: {_comma_list(scoring_result.node_shortlist_candidate_ids)}",
                f"Average fusion score top three: {scoring_result.top_three_statistics.average_fusion_score_top_three:.2f}",
            ],
            [
                "SearchExecutionResult_t.search_page_statistics",
                "SearchScoringResult_t.node_shortlist_candidate_ids",
                "SearchScoringResult_t.top_three_statistics",
            ],
            is_dynamic=True,
        ),
        _section(
            "Derived Outcome Signals",
            _branch_outcome_signal_lines(parent_node, execution_result, scoring_result),
            [
                "FrontierNode_t.node_shortlist_candidate_ids",
                "SearchExecutionResult_t.search_observation",
                "SearchScoringResult_t.node_shortlist_candidate_ids",
                "SearchScoringResult_t.explanation_candidate_ids",
                "SearchScoringResult_t.scored_candidates",
            ],
            is_dynamic=True,
        ),
        _section(
            "Runtime Budget State",
            _branch_budget_lines(runtime_budget_state),
            ["RuntimeBudgetState"],
            is_dynamic=True,
        ),
    ]
    if runtime_budget_state.near_budget_end:
        sections.append(
            _section(
                "Budget Warning",
                [
                    "The run is near budget end.",
                    "If incremental upside is weak, be more conservative about marking the branch as still open.",
                ],
                ["RuntimeBudgetState.near_budget_end"],
                is_dynamic=True,
            )
        )
    sections.append(
        _section(
            "Return Fields",
            [
                "Return novelty_score, usefulness_score, branch_exhausted, repair_operator_hint, and evaluation_notes.",
            ],
            [],
        )
    )
    return _build_prompt_surface(
        "branch_outcome_evaluation",
        instructions_text,
        sections,
    )


def build_search_run_finalization_prompt_surface(
    requirement_sheet: RequirementSheet,
    frontier_state: FrontierState_t1,
    rounds: list[SearchRoundArtifact],
    stop_reason: str,
    *,
    instructions_text: str,
) -> PromptSurfaceSnapshot:
    return _build_prompt_surface(
        "search_run_finalization",
        instructions_text,
        [
            _section(
                "Task Contract",
                [
                    "Summarize the run outcome from the provided final shortlist state.",
                    "Do not invent candidates or runtime facts outside this packet.",
                ],
                [],
            ),
            _section(
                "Role Summary",
                [
                    f"Role title: {requirement_sheet.role_title}",
                    f"Role focus: {_or_none(requirement_sheet.role_summary)}",
                    f"Must-have capabilities: {_comma_list(requirement_sheet.must_have_capabilities)}",
                    f"Locations: {_comma_list(requirement_sheet.hard_constraints.locations)}",
                ],
                [
                    "RequirementSheet.role_title",
                    "RequirementSheet.role_summary",
                    "RequirementSheet.must_have_capabilities",
                    "RequirementSheet.hard_constraints.locations",
                ],
                is_dynamic=True,
            ),
            _section(
                "Run Facts",
                _finalization_run_fact_lines(requirement_sheet, frontier_state, rounds),
                [
                    "SearchRoundArtifact.controller_decision",
                    "SearchRoundArtifact.execution_plan",
                    "FrontierState_t1.run_shortlist_candidate_ids",
                ],
                is_dynamic=True,
            ),
            _section(
                "Final Shortlist State",
                [
                    f"Final shortlist candidate ids: {_comma_list(frontier_state.run_shortlist_candidate_ids)}",
                    f"Remaining budget: {frontier_state.remaining_budget}",
                    f"Open frontier node ids: {_comma_list(frontier_state.open_frontier_node_ids)}",
                    f"Closed frontier node ids: {_comma_list(frontier_state.closed_frontier_node_ids)}",
                ],
                [
                    "FrontierState_t1.run_shortlist_candidate_ids",
                    "FrontierState_t1.remaining_budget",
                    "FrontierState_t1.open_frontier_node_ids",
                    "FrontierState_t1.closed_frontier_node_ids",
                ],
                is_dynamic=True,
            ),
            _section(
                "Stop Reason",
                [stop_reason or "None"],
                ["SearchRunResult.stop_reason"],
                is_dynamic=True,
            ),
            _section(
                "Return Fields",
                ["Return run_summary."],
                [],
            ),
        ],
    )


def _build_prompt_surface(
    surface_id: str,
    instructions_text: str,
    sections: list[PromptSurfaceSection],
) -> PromptSurfaceSnapshot:
    input_text = "\n\n".join(
        [f"## {section.title}\n{section.body_text}" for section in sections]
    )
    return PromptSurfaceSnapshot(
        surface_id=surface_id,
        instructions_text=instructions_text,
        input_text=input_text,
        instructions_sha1=sha1(instructions_text.encode("utf-8")).hexdigest(),
        input_sha1=sha1(input_text.encode("utf-8")).hexdigest(),
        sections=sections,
    )


def _section(
    title: str,
    lines: list[str],
    source_paths: list[str],
    *,
    is_dynamic: bool = False,
) -> PromptSurfaceSection:
    return PromptSurfaceSection(
        title=title,
        body_text="\n".join(f"- {line}" for line in lines),
        source_paths=source_paths,
        is_dynamic=is_dynamic,
    )


def _controller_donor_lines(context: SearchControllerContext_t) -> list[str]:
    if not context.donor_candidate_node_summaries:
        return ["No legal donor candidates."]
    return [
        (
            f"{donor.frontier_node_id}: shared_anchor_terms={_comma_list(donor.shared_anchor_terms)}; "
            f"expected_incremental_coverage={_comma_list(donor.expected_incremental_coverage)}; "
            f"reward_score={donor.reward_score:.2f}"
        )
        for donor in context.donor_candidate_node_summaries
    ]


def _controller_operator_stat_lines(context: SearchControllerContext_t) -> list[str]:
    if not context.operator_statistics_summary:
        return ["No operator statistics."]
    preferred_order = list(context.allowed_operator_names)
    for operator_name in sorted(context.operator_statistics_summary):
        if operator_name not in preferred_order:
            preferred_order.append(operator_name)
    lines: list[str] = []
    for operator_name in preferred_order:
        stats = context.operator_statistics_summary.get(operator_name)
        if stats is None:
            continue
        lines.append(
            f"{operator_name}: average_reward={stats.average_reward:.2f}, times_selected={stats.times_selected}"
        )
    return lines or ["No operator statistics."]


def _controller_fit_and_requirement_lines(context: SearchControllerContext_t) -> list[str]:
    fit_gate = context.fit_gate_constraints
    lines = [
        "CTS keyword terms are conjunctive. More terms tighten the search.",
        f"Max query terms: {context.max_query_terms}",
        f"Locations: {_comma_list(fit_gate.locations)}",
        f"Min years: {_or_none(fit_gate.min_years)}",
        f"Max years: {_or_none(fit_gate.max_years)}",
        f"Companies: {_comma_list(fit_gate.company_names)}",
        f"Schools: {_comma_list(fit_gate.school_names)}",
        f"Degree requirement: {_or_none(fit_gate.degree_requirement)}",
        f"Gender requirement: {_or_none(fit_gate.gender_requirement)}",
        f"Min age: {_or_none(fit_gate.min_age)}",
        f"Max age: {_or_none(fit_gate.max_age)}",
        "Unmet requirement weights:",
    ]
    if context.unmet_requirement_weights:
        lines.extend(
            f"{item.capability}: weight={item.weight:.2f}"
            for item in context.unmet_requirement_weights
        )
    else:
        lines.append("None.")
    return lines


def _controller_budget_lines(runtime_budget_state: RuntimeBudgetState) -> list[str]:
    return [
        f"Initial round budget: {runtime_budget_state.initial_round_budget}",
        f"Runtime round index: {runtime_budget_state.runtime_round_index}",
        f"Remaining budget: {runtime_budget_state.remaining_budget}",
        f"Used ratio: {runtime_budget_state.used_ratio:.2f}",
        f"Remaining ratio: {runtime_budget_state.remaining_ratio:.2f}",
        f"Phase progress: {runtime_budget_state.phase_progress:.2f}",
        f"Search phase: {runtime_budget_state.search_phase}",
        f"Near budget end: {_bool_text(runtime_budget_state.near_budget_end)}",
    ]


def _controller_rewrite_evidence_lines(context: SearchControllerContext_t) -> list[str]:
    if not context.rewrite_term_candidates:
        return ["No rewrite evidence terms."]
    return [
        (
            f"{candidate.term}: support_count={candidate.support_count}; "
            f"source_fields={_comma_list(candidate.source_fields)}; "
            f"signal={_rewrite_signal_label(candidate)}"
        )
        for candidate in context.rewrite_term_candidates
    ]


def _finalization_run_fact_lines(
    requirement_sheet: RequirementSheet,
    frontier_state: FrontierState_t1,
    rounds: list[SearchRoundArtifact],
) -> list[str]:
    search_rounds = [round_artifact for round_artifact in rounds if round_artifact.execution_plan is not None]
    operators_used = stable_deduplicate(
        [
            round_artifact.controller_decision.selected_operator_name
            for round_artifact in search_rounds
        ]
    )
    final_query_terms = (
        search_rounds[-1].execution_plan.query_terms
        if search_rounds and search_rounds[-1].execution_plan is not None
        else []
    )
    must_have_capabilities = requirement_sheet.must_have_capabilities
    must_have_query_coverage = (
        sum(query_terms_hit(final_query_terms, capability) for capability in must_have_capabilities)
        / len(must_have_capabilities)
        if must_have_capabilities
        else 0.0
    )
    operator_sequence = [
        round_artifact.controller_decision.selected_operator_name
        for round_artifact in search_rounds
    ]
    rounds_with_net_new_shortlist_gain = sum(
        1
        for round_artifact in search_rounds
        if set(round_artifact.frontier_state_after.run_shortlist_candidate_ids)
        - set(round_artifact.frontier_state_before.run_shortlist_candidate_ids)
    )
    return [
        f"Search round count: {len(search_rounds)}",
        f"Final shortlist count: {len(frontier_state.run_shortlist_candidate_ids)}",
        f"Final must-have query coverage: {must_have_query_coverage:.2f}",
        f"Operators used: {_comma_list(operators_used)}",
        f"Operator sequence: {_comma_list(operator_sequence)}",
        f"Last operator: {_or_none(operator_sequence[-1] if operator_sequence else None)}",
        f"Last query terms: {_comma_list(final_query_terms)}",
        f"Rounds with net-new shortlist gain: {rounds_with_net_new_shortlist_gain}",
    ]


def _rewrite_signal_label(candidate) -> str:
    breakdown = candidate.score_breakdown
    if breakdown.must_have_bonus > 0:
        label = "must_have"
    elif breakdown.anchor_bonus > 0:
        label = "anchor"
    elif breakdown.pack_bonus > 0:
        label = "pack"
    elif any(field in {"title", "project_names"} for field in candidate.source_fields):
        label = "title_project"
    else:
        label = "mixed"
    if breakdown.generic_penalty > 0:
        return f"{label}+generic_penalty"
    return label


def _branch_budget_lines(runtime_budget_state: RuntimeBudgetState) -> list[str]:
    return [
        f"Runtime round index: {runtime_budget_state.runtime_round_index}",
        f"Remaining budget: {runtime_budget_state.remaining_budget}",
        f"Remaining ratio: {runtime_budget_state.remaining_ratio:.2f}",
        f"Phase progress: {runtime_budget_state.phase_progress:.2f}",
        f"Search phase: {runtime_budget_state.search_phase}",
        f"Near budget end: {_bool_text(runtime_budget_state.near_budget_end)}",
    ]


def _controller_decision_snapshot_lines(context: SearchControllerContext_t) -> list[str]:
    all_must_haves = [
        item.capability
        for item in context.unmet_requirement_weights
    ]
    unmet_must_haves = list(context.operator_surface_unmet_must_haves)
    covered_must_haves = [
        capability
        for capability in all_must_haves
        if capability not in set(unmet_must_haves)
    ]
    return [
        f"Must-have coverage in active query pool: {len(covered_must_haves)}/{len(all_must_haves)}",
        f"Covered must-haves: {_comma_list(covered_must_haves)}",
        f"Unmet must-have count: {len(unmet_must_haves)}",
        f"Legal donor count: {len(context.donor_candidate_node_summaries)}",
        (
            "Phase and term budget: "
            f"phase={context.runtime_budget_state.search_phase}, "
            f"max_query_terms={context.max_query_terms}, "
            f"near_budget_end={_bool_text(context.runtime_budget_state.near_budget_end)}"
        ),
    ]


def _branch_outcome_signal_lines(
    parent_node: FrontierNode_t,
    execution_result: SearchExecutionResult_t,
    scoring_result: SearchScoringResult_t,
) -> list[str]:
    parent_shortlist_ids = set(parent_node.node_shortlist_candidate_ids)
    current_shortlist_ids = list(scoring_result.node_shortlist_candidate_ids)
    new_shortlist_ids = [
        candidate_id
        for candidate_id in current_shortlist_ids
        if candidate_id not in parent_shortlist_ids
    ]
    overlap_ids = [
        candidate_id
        for candidate_id in current_shortlist_ids
        if candidate_id in parent_shortlist_ids
    ]
    shortlist_ids = set(current_shortlist_ids)
    shortlist_fit_pass_count = sum(
        1
        for row in scoring_result.scored_candidates
        if row.candidate_id in shortlist_ids and row.fit == 1
    )
    return [
        f"New shortlist ids vs parent: {_comma_list(new_shortlist_ids)}",
        f"Overlap with parent shortlist: {_comma_list(overlap_ids)}",
        f"Net new shortlist count: {len(new_shortlist_ids)}",
        f"Shortage after last page: {_bool_text(execution_result.search_observation.shortage_after_last_page)}",
        f"Explanation candidate count: {len(scoring_result.explanation_candidate_ids)}",
        (
            "Shortlist fit-pass count: "
            f"{shortlist_fit_pass_count}/{len(current_shortlist_ids)}"
        ),
    ]


def _comma_list(values: list[object]) -> str:
    items = [str(value).strip() for value in values if str(value).strip()]
    return ", ".join(items) if items else "None"


def _or_none(value: object) -> str:
    if value is None:
        return "None"
    text = str(value).strip()
    return text or "None"


def _bool_text(value: bool) -> str:
    return "yes" if value else "no"


def _model_name(model: Any | None) -> str:
    if model is None:
        return "default"
    for attr in ("model_name", "name"):
        value = getattr(model, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    return type(model).__name__


__all__ = [
    "build_bootstrap_keyword_generation_prompt_surface",
    "build_branch_evaluation_prompt_surface",
    "build_controller_prompt_surface",
    "build_llm_call_audit",
    "build_requirement_extraction_prompt_surface",
    "build_search_run_finalization_prompt_surface",
]
