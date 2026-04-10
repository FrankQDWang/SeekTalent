# SelectActiveFrontierNode

从当前 frontier 里选出最值得扩展的节点，并打包控制器上下文。

## Signature

```text
SelectActiveFrontierNode : (FrontierState_t, RequirementSheet, ScoringPolicy, CrossoverGuardThresholds, RuntimeTermBudgetPolicy) -> SearchControllerContext_t
```

## Notation Legend

```text
R := RequirementSheet
P := ScoringPolicy
F_t := FrontierState_t
n_t := active frontier node
```

## Input Projection

```text
open_nodes_t = [F_t.frontier_nodes[node_id] for node_id in F_t.open_frontier_node_ids]
must_have_t = R.must_have_capabilities
fit_gate_t = P.fit_gate_constraints
remaining_budget_t = F_t.remaining_budget
```

## Primitive Predicates / Matching Rules

```text
normalized(text) = trim(lowercase(text))
```

```text
operator_average_reward_t(operator_name) =
  F_t.operator_statistics[operator_name].average_reward
  if operator_name in F_t.operator_statistics
  else 0.0
```

```text
query_pool_hit_t(node_t, capability_t) =
  1 if ∃ term_t in node_t.node_query_term_pool :
        normalized(term_t) contains normalized(capability_t)
        or normalized(capability_t) contains normalized(term_t)
  else 0
```

```text
frontier_priority_t(node_t) =
  (1.5 if node_t.parent_frontier_node_id = null else 0.0)
  + 0.8 * operator_average_reward_t(node_t.selected_operator_name)
  + (0.4 if node_t.previous_branch_evaluation = null else 0.0)
  - (1.0 if node_t.previous_branch_evaluation != null and node_t.previous_branch_evaluation.branch_exhausted else 0.0)
```

```text
unmet_requirement_bonus_t(node_t) =
  0.6 * Σ_{capability_t in must_have_t} (1 - query_pool_hit_t(node_t, capability_t))
```

```text
saturation_penalty_t(node_t) =
  0
  if |node_t.node_shortlist_candidate_ids| = 0
  else
    1.2
    * |set(node_t.node_shortlist_candidate_ids) ∩ set(F_t.run_shortlist_candidate_ids)|
    / |node_t.node_shortlist_candidate_ids|
```

```text
priority_score_t(node_t) =
  frontier_priority_t(node_t)
  + unmet_requirement_bonus_t(node_t)
  - saturation_penalty_t(node_t)
```

```text
unmet_must_haves_supported_by_t(donor_t, active_t) =
  [
    capability_t
    for capability_t in must_have_t
    if query_pool_hit_t(active_t, capability_t) = 0
       and query_pool_hit_t(donor_t, capability_t) = 1
  ]
```

## Transformation

### Phase 1 — Active Node Selection

```text
n_t = argmax_{node_t in open_nodes_t} priority_score_t(node_t)
```

### Phase 2 — Donor Candidate Projection

```text
eligible_donor_nodes_t =
  [
    donor_t
    for donor_t in open_nodes_t
    if donor_t.frontier_node_id != n_t.frontier_node_id
       and donor_t.status = "open"
       and donor_t.reward_breakdown != null
       and donor_t.reward_breakdown.reward_score >= CrossoverGuardThresholds.min_reward_score
       and |intersect(n_t.node_query_term_pool, donor_t.node_query_term_pool)|
           >= CrossoverGuardThresholds.min_shared_anchor_terms
       and |unmet_must_haves_supported_by_t(donor_t, n_t)| > 0
  ]
```

```text
donor_candidate_nodes_t =
  stable_sort_desc(
    [
      {
        frontier_node_id: donor_t.frontier_node_id,
        shared_anchor_terms:
          intersect(n_t.node_query_term_pool, donor_t.node_query_term_pool),
        expected_incremental_coverage:
          unmet_must_haves_supported_by_t(donor_t, n_t),
        reward_score: donor_t.reward_breakdown.reward_score
      }
      for donor_t in eligible_donor_nodes_t
    ],
    key = (|expected_incremental_coverage|, reward_score)
  )[0 : CrossoverGuardThresholds.max_donor_candidates]
```

### Phase 3 — Operator and Budget Projection

```text
allowed_operator_names_t =
  ["core_precision", "must_have_alias", "relaxed_floor", "generic_expansion", "crossover_compose"]
  if |n_t.knowledge_pack_ids| = 0
  else
    ["core_precision", "must_have_alias", "relaxed_floor", "generic_expansion", "pack_expansion", "cross_pack_bridge", "crossover_compose"]
```

```text
term_budget_range_t =
  RuntimeTermBudgetPolicy.high_budget_range
  if remaining_budget_t >= 4
  else RuntimeTermBudgetPolicy.medium_budget_range
  if remaining_budget_t >= 2
  else RuntimeTermBudgetPolicy.low_budget_range
```

```text
unmet_requirement_weights_t =
  [
    {
      capability: capability_t,
      weight: 1.0 if query_pool_hit_t(n_t, capability_t) = 0 else 0.3
    }
    for capability_t in must_have_t
  ]
```

### Field-Level Output Assembly

```text
SearchControllerContext_t.active_frontier_node_summary = {
  frontier_node_id: n_t.frontier_node_id,
  selected_operator_name: n_t.selected_operator_name,
  node_query_term_pool: n_t.node_query_term_pool,
  node_shortlist_candidate_ids: n_t.node_shortlist_candidate_ids
}
SearchControllerContext_t.donor_candidate_node_summaries = donor_candidate_nodes_t
SearchControllerContext_t.frontier_head_summary = {
  open_node_count: |F_t.open_frontier_node_ids|,
  remaining_budget: F_t.remaining_budget,
  highest_priority_score: priority_score_t(n_t)
}
SearchControllerContext_t.unmet_requirement_weights = unmet_requirement_weights_t
SearchControllerContext_t.operator_statistics_summary = F_t.operator_statistics
SearchControllerContext_t.allowed_operator_names = allowed_operator_names_t
SearchControllerContext_t.term_budget_range = term_budget_range_t
SearchControllerContext_t.fit_gate_constraints = fit_gate_t
```

## Defaults / Thresholds Used Here

```text
frontier_priority =
  1.5 * seed_start_bonus
  + 0.8 * operator_average_reward
  + 0.4 * no_previous_branch_bonus
  - 1.0 * exhausted_branch_penalty
```

```text
each unmet must-have contributes +0.6
saturation penalty = 1.2 * shortlist overlap ratio
```

```text
CrossoverGuardThresholds defaults = {
  min_shared_anchor_terms: 1,
  min_reward_score: 1.5,
  max_donor_candidates: 2
}
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

## Write Set

- `SearchControllerContext_t.active_frontier_node_summary`
- `SearchControllerContext_t.donor_candidate_node_summaries`
- `SearchControllerContext_t.frontier_head_summary`
- `SearchControllerContext_t.unmet_requirement_weights`
- `SearchControllerContext_t.operator_statistics_summary`
- `SearchControllerContext_t.allowed_operator_names`
- `SearchControllerContext_t.term_budget_range`
- `SearchControllerContext_t.fit_gate_constraints`

## 不确定性边界 / 说明

- `pack_expansion / cross_pack_bridge` 是否允许，只取决于 active node 是否带 `knowledge_pack_ids`。
- 这里不会读取知识包正文，也不会重新做领域路由。

- `SearchControllerContext_t.active_frontier_node_summary`
- `SearchControllerContext_t.donor_candidate_node_summaries`
- `SearchControllerContext_t.frontier_head_summary`
- `SearchControllerContext_t.unmet_requirement_weights`
- `SearchControllerContext_t.operator_statistics_summary`
- `SearchControllerContext_t.allowed_operator_names`
- `SearchControllerContext_t.term_budget_range`
- `SearchControllerContext_t.fit_gate_constraints`

## 输入 payload

- [[FrontierState_t]]
- [[RequirementSheet]]
- [[ScoringPolicy]]

## 输出 payload

- [[SearchControllerContext_t]]

## 不确定性边界 / 说明

- 真正的搜索主控权在 runtime 选点逻辑，不在控制器自由改写 query 的能力。
- seed 节点因 `reward_breakdown = null` 默认不能成为 donor。

## 相关

- [[operator-spec-style]]
- [[FrontierState_t]]
- [[FrontierNode_t]]
- [[RequirementSheet]]
- [[ScoringPolicy]]
- [[SearchControllerContext_t]]
- [[CrossoverGuardThresholds]]
- [[RuntimeTermBudgetPolicy]]
