# SearchControllerContext_t

送入控制器 draft layer 的只读上下文快照。

```text
SearchControllerContext_t = {
  role_title,
  role_summary,
  active_frontier_node_summary,
  donor_candidate_node_summaries,
  frontier_head_summary,
  active_selection_breakdown,
  selection_ranking,
  unmet_requirement_weights,
  operator_statistics_summary,
  allowed_operator_names,
  term_budget_range,
  fit_gate_constraints,
  runtime_budget_state
}
```

## 稳定字段组

- 角色摘要：`role_title`, `role_summary`
- active node 摘要：`active_frontier_node_summary`
- donor 候选摘要：`donor_candidate_node_summaries`
- frontier 头部摘要：`frontier_head_summary`
- 当前选中节点的完整打分拆解：`active_selection_breakdown`
- 当前轮所有 eligible open nodes 的打分排序：`selection_ranking`
- 未满足需求权重：`unmet_requirement_weights`
- operator 统计摘要：`operator_statistics_summary`
- 当前轮允许的 operator：`allowed_operator_names`
- term 预算范围：`term_budget_range`
- fit gate 约束：`fit_gate_constraints`
- runtime 预算态：`runtime_budget_state`

## Direct Producer / Direct Consumer

- Direct producer：[[SelectActiveFrontierNode]]
- Direct consumer：[[GenerateSearchControllerDecision]]

## Invariants

- `SearchControllerContext_t` 是只读快照，不是可回写状态。
- `active_selection_breakdown` 与 `selection_ranking[0].breakdown` 必须一致。
- `selection_ranking` 只包含 eligible open nodes。
- `selection_ranking` 必须按 `final_selection_score` 降序；打平按 `open_frontier_node_ids` 顺序稳定。
- `runtime_budget_state` 是 phase 的唯一 owner。
- `selection_ranking` 只进入 trace，不进入 controller prompt surface。

## Prompt Surface Projection

`SearchControllerContext_t` 不再直接作为 raw JSON user content 发给 LLM。

它会被投影成固定 section 顺序的 prompt surface：

1. `Task Contract`
2. `Role Summary`
3. `Active Frontier Node`
4. `Donor Candidates`
5. `Allowed Operators`
6. `Operator Statistics`
7. `Fit Gates And Unmet Requirements`
8. `Runtime Budget State`
9. `Budget Warning`，仅当 `runtime_budget_state.near_budget_end = true`
10. `Decision Request`

`active_selection_breakdown` 与 `selection_ranking` 不会投影到 prompt text。

## 最小示例

```yaml
role_title: "Senior Python Agent Engineer"
role_summary: "Build ranking systems."
active_frontier_node_summary:
  frontier_node_id: "seed_agent_core"
  selected_operator_name: "must_have_alias"
  node_query_term_pool: ["agent engineer", "rag", "python"]
  node_shortlist_candidate_ids: ["c32", "c44"]
donor_candidate_node_summaries:
  - frontier_node_id: "child_search_domain_01"
    shared_anchor_terms: ["rag"]
    expected_incremental_coverage:
      - "retrieval or ranking experience"
    reward_score: 3.9
frontier_head_summary:
  open_node_count: 3
  remaining_budget: 4
  highest_selection_score: 3.61
active_selection_breakdown:
  search_phase: "explore"
  operator_exploitation_score: 0.0
  operator_exploration_bonus: 1.48
  coverage_opportunity_score: 0.5
  incremental_value_score: 0.0
  fresh_node_bonus: 1.0
  redundancy_penalty: 0.0
  final_selection_score: 3.61
selection_ranking:
  - frontier_node_id: "seed_agent_core"
    selected_operator_name: "must_have_alias"
    breakdown:
      search_phase: "explore"
      operator_exploitation_score: 0.0
      operator_exploration_bonus: 1.48
      coverage_opportunity_score: 0.5
      incremental_value_score: 0.0
      fresh_node_bonus: 1.0
      redundancy_penalty: 0.0
      final_selection_score: 3.61
unmet_requirement_weights:
  - capability: "retrieval_or_ranking_experience"
    weight: 1.0
operator_statistics_summary:
  must_have_alias:
    average_reward: 3.8
    times_selected: 1
allowed_operator_names:
  - "core_precision"
  - "must_have_alias"
  - "relaxed_floor"
  - "generic_expansion"
  - "crossover_compose"
term_budget_range: [2, 6]
fit_gate_constraints:
  locations: ["Shanghai"]
  min_years: 6
runtime_budget_state:
  initial_round_budget: 10
  runtime_round_index: 8
  remaining_budget: 2
  used_ratio: 0.8
  remaining_ratio: 0.2
  phase_progress: 0.89
  search_phase: "harvest"
  near_budget_end: true
```

## 相关

- [[FrontierSelectionBreakdown]]
- [[FrontierSelectionCandidateSummary]]
- [[RuntimeBudgetState]]
- [[GenerateSearchControllerDecision]]
