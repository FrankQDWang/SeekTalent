# MaterializeSearchExecutionPlan

把控制器决策物化成可执行 child 检索计划。

## Signature

```text
MaterializeSearchExecutionPlan : (FrontierState_t, RequirementSheet, SearchControllerDecision_t, RuntimeTermBudgetPolicy, RuntimeSearchBudget, CrossoverGuardThresholds) -> SearchExecutionPlan_t
```

## Notation Legend

```text
R := RequirementSheet
F_t := FrontierState_t
d_t := SearchControllerDecision_t
p_t := SearchExecutionPlan_t
```

## Input Projection

```text
parent_node_t = F_t.frontier_nodes[d_t.target_frontier_node_id]
remaining_budget_t = F_t.remaining_budget
selected_operator_name_t = d_t.selected_operator_name
operator_args_t = d_t.operator_args
```

## Primitive Predicates / Matching Rules

```text
term_budget_range_t =
  RuntimeTermBudgetPolicy.high_budget_range
  if remaining_budget_t >= 4
  else RuntimeTermBudgetPolicy.medium_budget_range
  if remaining_budget_t >= 2
  else RuntimeTermBudgetPolicy.low_budget_range
```

```text
clamped_terms_t(terms_t) =
  stable_deduplicate(terms_t)[0 : term_budget_range_t[1]]
```

```text
child_id_t(parent_id_t, semantic_hash_t) =
  "child_" + parent_id_t + "_" + semantic_hash_t[0 : 8]
```

## Transformation

### Phase 1 — Query Term Materialization

```text
if selected_operator_name_t != "crossover_compose":
  base_query_terms_t = parent_node_t.node_query_term_pool
  additional_terms_t = stable_deduplicate(drop_empty(operator_args_t.additional_terms))
  query_terms_t = clamped_terms_t(base_query_terms_t + additional_terms_t)
  donor_frontier_node_id_t = null
  source_card_ids_t = parent_node_t.source_card_ids
  donor_negative_terms_t = []
else:
  donor_node_t = F_t.frontier_nodes[operator_args_t.donor_frontier_node_id]
  shared_anchor_terms_t =
    [
      term_t
      for term_t in stable_deduplicate(operator_args_t.shared_anchor_terms)
      if term_t in intersect(parent_node_t.node_query_term_pool, donor_node_t.node_query_term_pool)
    ]
  donor_terms_t =
    [
      term_t
      for term_t in stable_deduplicate(operator_args_t.donor_terms_used)
      if term_t in (set(donor_node_t.node_query_term_pool) - set(parent_node_t.node_query_term_pool))
    ]
  if |shared_anchor_terms_t| < CrossoverGuardThresholds.min_shared_anchor_terms:
    fail("crossover_requires_shared_anchor")
  query_terms_t = clamped_terms_t(shared_anchor_terms_t + donor_terms_t)
  donor_frontier_node_id_t = donor_node_t.frontier_node_id
  source_card_ids_t = stable_deduplicate(parent_node_t.source_card_ids + donor_node_t.source_card_ids)
  donor_negative_terms_t = donor_node_t.negative_terms
```

### Phase 2 — Constraint Projection

```text
projected_filters_t = R.hard_constraints

runtime_only_constraints_t = {
  must_have_keywords: stable_deduplicate(R.must_have_capabilities + query_terms_t),
  negative_keywords:
    stable_deduplicate(parent_node_t.negative_terms + donor_negative_terms_t)
}

derived_position_t = non_empty(R.role_title)

derived_work_content_t =
  join(
    " | ",
    stable_deduplicate(R.must_have_capabilities)[0 : 4]
  )
  if |stable_deduplicate(R.must_have_capabilities)| > 0
  else null
```

### Phase 3 — Search Budget Freeze

```text
target_new_t =
  min(
    coalesce(
      operator_args_t.target_new_candidate_count,
      RuntimeSearchBudget.default_target_new_candidate_count
    ),
    RuntimeSearchBudget.max_target_new_candidate_count
  )
```

### Phase 4 — Stable Child Identity

```text
semantic_hash_t =
  sha1(
    serialize(
      selected_operator_name_t,
      query_terms_t,
      projected_filters_t,
      runtime_only_constraints_t
    )
  )
```

### Field-Level Output Assembly

```text
p_t.query_terms = query_terms_t
p_t.projected_filters = projected_filters_t
p_t.runtime_only_constraints = runtime_only_constraints_t
p_t.target_new_candidate_count = target_new_t
p_t.semantic_hash = semantic_hash_t
p_t.source_card_ids = source_card_ids_t
p_t.child_frontier_node_stub = {
  frontier_node_id: child_id_t(parent_node_t.frontier_node_id, semantic_hash_t),
  parent_frontier_node_id: parent_node_t.frontier_node_id,
  donor_frontier_node_id: donor_frontier_node_id_t,
  selected_operator_name: selected_operator_name_t
}
p_t.derived_position = derived_position_t
p_t.derived_work_content = derived_work_content_t
```

## Defaults / Thresholds Used Here

```text
RuntimeSearchBudget defaults = {
  default_target_new_candidate_count: 10,
  max_target_new_candidate_count: 20
}
```

```text
RuntimeTermBudgetPolicy defaults = {
  high_budget_range: [2, 6],
  medium_budget_range: [2, 5],
  low_budget_range: [2, 4]
}
```

## Read Set

- `FrontierState_t.frontier_nodes`
- `FrontierState_t.remaining_budget`
- `RequirementSheet.hard_constraints`
- `RequirementSheet.must_have_capabilities`
- `RequirementSheet.role_title`
- `SearchControllerDecision_t.target_frontier_node_id`
- `SearchControllerDecision_t.selected_operator_name`
- `SearchControllerDecision_t.operator_args`
- `RuntimeTermBudgetPolicy`
- `RuntimeSearchBudget`
- `CrossoverGuardThresholds`

## Write Set

- `SearchExecutionPlan_t.query_terms`
- `SearchExecutionPlan_t.projected_filters`
- `SearchExecutionPlan_t.runtime_only_constraints`
- `SearchExecutionPlan_t.target_new_candidate_count`
- `SearchExecutionPlan_t.semantic_hash`
- `SearchExecutionPlan_t.source_card_ids`
- `SearchExecutionPlan_t.child_frontier_node_stub`
- `SearchExecutionPlan_t.derived_position`
- `SearchExecutionPlan_t.derived_work_content`

## 输入 payload

- [[FrontierState_t]]
- [[RequirementSheet]]
- [[SearchControllerDecision_t]]

## 输出 payload

- [[SearchExecutionPlan_t]]

## 不确定性边界 / 说明

- 这一步只处理 `d_t.action = "search_cts"` 的路径；`stop` 动作走 carry-forward / stop guard 支路。
- `projected_filters_t` 是稳定业务约束，不等于真实 CTS payload；真实协议映射继续由 [[cts-projection-policy]] 持有。
- `derived_position_t` / `derived_work_content_t` 在这里显式冻结，是为了让执行层只依赖 `SearchExecutionPlan_t`，不再回头读取 `RequirementSheet`。

## 相关

- [[operator-spec-style]]
- [[FrontierState_t]]
- [[FrontierNode_t]]
- [[RequirementSheet]]
- [[SearchControllerDecision_t]]
- [[SearchExecutionPlan_t]]
- [[RuntimeSearchBudget]]
- [[RuntimeTermBudgetPolicy]]
- [[CrossoverGuardThresholds]]
- [[cts-projection-policy]]
