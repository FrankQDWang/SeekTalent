# SelectActiveFrontierNode

从当前 frontier 里选出最值得扩展的节点，并打包控制器上下文。

## Signature

```text
SelectActiveFrontierNode :
  (FrontierState_t, RequirementSheet, ScoringPolicy, CrossoverGuardThresholds, RuntimeTermBudgetPolicy, RuntimeBudgetState)
  -> SearchControllerContext_t
```

## Input Projection

```text
open_nodes_t =
  [
    FrontierState_t.frontier_nodes[node_id]
    for node_id in FrontierState_t.open_frontier_node_ids
    if FrontierState_t.frontier_nodes[node_id].status = "open"
  ]
```

如果 `open_nodes_t` 为空，直接报错。  
如果任一 `open_nodes_t` 满足 `previous_branch_evaluation.branch_exhausted = true`，直接报错 `open_frontier_node_marked_exhausted`。

## Active Node Selection

每个 eligible open node 都计算 [[FrontierSelectionBreakdown]]：

```text
selection_score(node_t) =
  w_exploit * operator_exploitation_score(node_t)
  + w_explore * operator_exploration_bonus(node_t)
  + w_coverage * coverage_opportunity_score(node_t)
  + w_incremental * incremental_value_score(node_t)
  + w_fresh * fresh_node_bonus(node_t)
  - w_redundancy * redundancy_penalty(node_t)
```

phase 权重只来自 `RuntimeBudgetState.search_phase`。

```text
selection_ranking_t =
  stable_sort_desc(
    eligible open nodes,
    key = (final_selection_score, open_frontier_node_ids order)
  )
```

active node 就是 `selection_ranking_t[0]`。

## Donor Candidate Projection

donor 规则不变：

- `donor.status = "open"`
- `donor.reward_breakdown != null`
- `donor.reward_breakdown.reward_score >= CrossoverGuardThresholds.min_reward_score`
- `shared_anchor_terms` 满足最小阈值
- `expected_incremental_coverage` 非空

## Output Assembly

```text
SearchControllerContext_t.active_frontier_node_summary = active node summary
SearchControllerContext_t.donor_candidate_node_summaries = donor candidate list
SearchControllerContext_t.frontier_head_summary = {
  open_node_count,
  remaining_budget,
  highest_selection_score
}
SearchControllerContext_t.active_selection_breakdown = selection_ranking_t[0].breakdown
SearchControllerContext_t.selection_ranking = selection_ranking_t
SearchControllerContext_t.unmet_requirement_weights = current unmet requirement weights
SearchControllerContext_t.operator_statistics_summary = FrontierState_t.operator_statistics
SearchControllerContext_t.allowed_operator_names = current operator surface
SearchControllerContext_t.term_budget_range = current term budget range
SearchControllerContext_t.fit_gate_constraints = ScoringPolicy.fit_gate_constraints
SearchControllerContext_t.runtime_budget_state = RuntimeBudgetState
```

## Read Set

- `FrontierState_t.open_frontier_node_ids`
- `FrontierState_t.frontier_nodes`
- `FrontierState_t.run_shortlist_candidate_ids`
- `FrontierState_t.operator_statistics`
- `FrontierState_t.remaining_budget`
- `RequirementSheet.must_have_capabilities`
- `ScoringPolicy.fit_gate_constraints`
- `CrossoverGuardThresholds`
- `RuntimeTermBudgetPolicy`
- `RuntimeBudgetState.search_phase`

## Write Set

- `SearchControllerContext_t.active_frontier_node_summary`
- `SearchControllerContext_t.donor_candidate_node_summaries`
- `SearchControllerContext_t.frontier_head_summary`
- `SearchControllerContext_t.active_selection_breakdown`
- `SearchControllerContext_t.selection_ranking`
- `SearchControllerContext_t.unmet_requirement_weights`
- `SearchControllerContext_t.operator_statistics_summary`
- `SearchControllerContext_t.allowed_operator_names`
- `SearchControllerContext_t.term_budget_range`
- `SearchControllerContext_t.fit_gate_constraints`
- `SearchControllerContext_t.runtime_budget_state`

## 相关

- [[selection-plan-semantics]]
- [[SearchControllerContext_t]]
- [[FrontierSelectionBreakdown]]
