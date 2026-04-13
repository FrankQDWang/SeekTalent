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
    output_mode: str = "NativeOutput(strict=True)",
    model_name: str | None = None,
) -> LLMCallAudit:
    mode_settings = dict(STRICT_MODEL_SETTINGS)
    if output_mode == "NativeOutput(strict=True)":
        mode_settings["native_output_strict"] = True
    elif output_mode == "ToolOutput(strict=True)":
        mode_settings["tool_output_strict"] = True
    elif output_mode == "PromptedOutput":
        mode_settings["prompted_output"] = True
    return LLMCallAudit(
        output_mode=output_mode,
        retries=RETRIES,
        output_retries=OUTPUT_RETRIES,
        validator_retry_count=validator_retry_count,
        model_name=model_name or _model_name(model),
        model_settings_snapshot=mode_settings,
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
                "任务约束",
                [
                    "从提供的招聘输入里提取严格的结构化 requirement draft。",
                    "只能使用职位描述和寻访 notes。",
                    "必须条件、优先信号、排除信号、硬约束必须分开表达。",
                    "不要把模糊的偏好升级成硬约束。",
                ],
                [],
            ),
            _section(
                "职位描述",
                [input_truth.job_description or "None"],
                ["SearchInputTruth.job_description"],
                is_dynamic=True,
            ),
            _section(
                "寻访备注",
                [_or_none(input_truth.hiring_notes)],
                ["SearchInputTruth.hiring_notes"],
                is_dynamic=True,
            ),
            _section(
                "返回字段",
                [
                    "返回 role_title_candidate、role_summary_candidate、must_have_capability_candidates、preferred_capability_candidates、exclusion_signal_candidates、preference_candidates、hard_constraint_candidates、scoring_rationale_candidate。",
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
                "任务约束",
                [
                    "根据 requirement 摘要、routing 结果和已选 knowledge packs 生成 round-0 seed intents。",
                    "不要使用这个 packet 之外的 runtime 或候选人信息。",
                ],
                [],
            ),
            _section(
                "Requirement 摘要",
                [
                    f"岗位名称：{requirement_sheet.role_title}",
                    f"岗位重点：{_or_none(requirement_sheet.role_summary)}",
                    f"必须条件：{_comma_list(requirement_sheet.must_have_capabilities)}",
                    f"优先条件：{_comma_list(requirement_sheet.preferred_capabilities)}",
                    f"排除信号：{_comma_list(requirement_sheet.exclusion_signals)}",
                    f"工作地点：{_comma_list(requirement_sheet.hard_constraints.locations)}",
                    f"最低工作年限：{_or_none(requirement_sheet.hard_constraints.min_years)}",
                    f"最高工作年限：{_or_none(requirement_sheet.hard_constraints.max_years)}",
                    f"目标公司：{_comma_list(requirement_sheet.hard_constraints.company_names)}",
                    f"目标学校：{_comma_list(requirement_sheet.hard_constraints.school_names)}",
                    f"学历要求：{_or_none(requirement_sheet.hard_constraints.degree_requirement)}",
                    f"性别要求：{_or_none(requirement_sheet.hard_constraints.gender_requirement)}",
                    f"最低年龄：{_or_none(requirement_sheet.hard_constraints.min_age)}",
                    f"最高年龄：{_or_none(requirement_sheet.hard_constraints.max_age)}",
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
                "Routing 结果",
                [
                    f"路由模式：{routing_result.routing_mode}",
                    f"已选 knowledge pack ids：{_comma_list(routing_result.selected_knowledge_pack_ids)}",
                    f"路由置信度：{routing_result.routing_confidence:.2f}",
                    f"回退原因：{_or_none(routing_result.fallback_reason)}",
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
                "已选 Knowledge Packs",
                pack_lines,
                ["DomainKnowledgePack.knowledge_pack_id", "DomainKnowledgePack.label", "DomainKnowledgePack.routing_text", "DomainKnowledgePack.include_keywords", "DomainKnowledgePack.exclude_keywords"],
                is_dynamic=True,
            ),
            _section(
                "返回字段",
                [
                    "返回 candidate_seeds 和 negative_keywords。",
                    "每个 candidate seed 都必须包含 intent_type、keywords、source_knowledge_pack_ids、reasoning。",
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
            "任务约束",
            [
                "只能使用提供的 controller context。",
                "必须从 allowed_operator_names 里选择合法 operator。",
                "不要发明候选列表之外的 donor id，也不要发明不支持的 operator。",
            ],
            [],
        ),
        _section(
            "岗位摘要",
            [
                f"岗位名称：{context.role_title}",
                f"岗位重点：{_or_none(context.role_summary)}",
            ],
            [
                "SearchControllerContext_t.role_title",
                "SearchControllerContext_t.role_summary",
            ],
            is_dynamic=True,
        ),
        _section(
            "当前 Frontier 节点",
            [
                f"Frontier node id：{context.active_frontier_node_summary.frontier_node_id}",
                f"当前 operator：{context.active_frontier_node_summary.selected_operator_name}",
                f"当前 query term pool：{_comma_list(context.active_frontier_node_summary.node_query_term_pool)}",
                f"当前节点 shortlist ids：{_comma_list(context.active_frontier_node_summary.node_shortlist_candidate_ids)}",
            ],
            ["SearchControllerContext_t.active_frontier_node_summary"],
            is_dynamic=True,
        ),
        _section(
            "决策快照",
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
            "Donor 候选",
            _controller_donor_lines(context),
            ["SearchControllerContext_t.donor_candidate_node_summaries"],
            is_dynamic=True,
        ),
        _section(
            "允许的 Operators",
            [
                f"允许的 operators：{_comma_list(context.allowed_operator_names)}",
                f"operator surface override：{context.operator_surface_override_reason}",
                "operator surface 未覆盖的 must-have："
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
            "Rewrite 证据",
            _controller_rewrite_evidence_lines(context),
            ["SearchControllerContext_t.rewrite_term_candidates"],
            is_dynamic=True,
        ),
        _section(
            "Operator 统计",
            _controller_operator_stat_lines(context),
            ["SearchControllerContext_t.operator_statistics_summary"],
            is_dynamic=True,
        ),
        _section(
            "Fit Gate 与未覆盖要求",
            _controller_fit_and_requirement_lines(context),
                [
                    "SearchControllerContext_t.fit_gate_constraints",
                    "SearchControllerContext_t.unmet_requirement_weights",
                    "SearchControllerContext_t.max_query_terms",
                ],
                is_dynamic=True,
            ),
        _section(
            "运行预算状态",
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
                    "当前 run 已进入最后 20% 预算。",
                    "优先选择高收益的精确动作。",
                    "除非 must-have 仍明显缺失，否则不要做投机性扩展。",
                ],
                ["SearchControllerContext_t.runtime_budget_state.near_budget_end"],
                is_dynamic=True,
            )
        )
    sections.append(
        _section(
            "决策请求",
            [
                "返回 action、selected_operator_name、operator_args、expected_gain_hypothesis。",
                "答案只能针对当前 active frontier node。",
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
                "Final Candidate State",
                [
                    f"Final candidate ids derived from shortlist state: {_comma_list(frontier_state.run_shortlist_candidate_ids)}",
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
        return ["没有合法 donor 候选。"]
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
        "CTS 关键词检索是 conjunctive 的，词越多通常越收紧。",
        f"最大 query terms：{context.max_query_terms}",
        f"工作地点：{_comma_list(fit_gate.locations)}",
        f"最低工作年限：{_or_none(fit_gate.min_years)}",
        f"最高工作年限：{_or_none(fit_gate.max_years)}",
        f"目标公司：{_comma_list(fit_gate.company_names)}",
        f"目标学校：{_comma_list(fit_gate.school_names)}",
        f"学历要求：{_or_none(fit_gate.degree_requirement)}",
        f"性别要求：{_or_none(fit_gate.gender_requirement)}",
        f"最低年龄：{_or_none(fit_gate.min_age)}",
        f"最高年龄：{_or_none(fit_gate.max_age)}",
        "未覆盖要求权重：",
    ]
    if context.unmet_requirement_weights:
        lines.extend(
            f"{item.capability}: weight={item.weight:.2f}"
            for item in context.unmet_requirement_weights
        )
    else:
        lines.append("无。")
    return lines


def _controller_budget_lines(runtime_budget_state: RuntimeBudgetState) -> list[str]:
    return [
        f"初始 round budget：{runtime_budget_state.initial_round_budget}",
        f"当前 round index：{runtime_budget_state.runtime_round_index}",
        f"剩余预算：{runtime_budget_state.remaining_budget}",
        f"已用比例：{runtime_budget_state.used_ratio:.2f}",
        f"剩余比例：{runtime_budget_state.remaining_ratio:.2f}",
        f"阶段进度：{runtime_budget_state.phase_progress:.2f}",
        f"搜索阶段：{runtime_budget_state.search_phase}",
        f"是否接近预算尾部：{_bool_text(runtime_budget_state.near_budget_end)}",
    ]


def _controller_rewrite_evidence_lines(context: SearchControllerContext_t) -> list[str]:
    if not context.rewrite_term_candidates:
        return ["没有 rewrite 证据词。"]
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
        f"当前 query pool 的 must-have 覆盖：{len(covered_must_haves)}/{len(all_must_haves)}",
        f"已覆盖 must-have：{_comma_list(covered_must_haves)}",
        f"未覆盖 must-have 数量：{len(unmet_must_haves)}",
        f"合法 donor 数量：{len(context.donor_candidate_node_summaries)}",
        (
            "阶段与 term 预算："
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
