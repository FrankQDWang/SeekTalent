# selection-plan-semantics

`SelectActiveFrontierNode` 与 `MaterializeSearchExecutionPlan` 使用的 deterministic helper 语义 owner。

## `operator_exploitation_score`

```text
avg_reward(n) =
  FrontierState_t.operator_statistics[n.selected_operator_name].average_reward
  if present
  else 0.0

operator_exploitation_score(n) =
  max(avg_reward(n), 0.0) / (1.0 + max(avg_reward(n), 0.0))
```

## `operator_exploration_bonus`

```text
total_operator_pulls =
  Σ FrontierState_t.operator_statistics[*].times_selected

operator_pulls(n) =
  FrontierState_t.operator_statistics[n.selected_operator_name].times_selected
  if present
  else 0

operator_exploration_bonus(n) =
  sqrt(2.0 * log(total_operator_pulls + 2.0) / (operator_pulls(n) + 1.0))
```

bandit arm 固定是 `selected_operator_name`，不是 `frontier_node_id`。

## `coverage_opportunity_score`

```text
hit_count(n) =
  Σ query_pool_hit(n, capability)
  for capability in RequirementSheet.must_have_capabilities

coverage_ratio(n) =
  hit_count(n) / max(1, |RequirementSheet.must_have_capabilities|)

coverage_opportunity_score(n) =
  coverage_ratio(n) if 0.0 < coverage_ratio(n) < 1.0 else 0.0
```

这里只奖励 partial coverage；`0-hit` 和 `full-hit` 都返回 `0.0`。

## `incremental_value_score`

```text
bounded_new_fit_yield(n) =
  n.reward_breakdown.new_fit_yield / (1.0 + n.reward_breakdown.new_fit_yield)

incremental_value_score(n) =
  0.7 * bounded_new_fit_yield(n) + 0.3 * n.reward_breakdown.diversity
```

如果 `reward_breakdown = null`，则 `incremental_value_score = 0.0`。

## `fresh_node_bonus`

- `1.0` if `n.previous_branch_evaluation = null`
- `0.0` otherwise

## `redundancy_penalty`

```text
redundancy_penalty(n) =
  0.0
  if |n.node_shortlist_candidate_ids| = 0
  else
    |set(n.node_shortlist_candidate_ids) ∩ set(FrontierState_t.run_shortlist_candidate_ids)|
    / |n.node_shortlist_candidate_ids|
```

## `selection_score`

phase 权重来自 `RuntimeBudgetState.search_phase`：

| phase | exploit | explore | coverage | incremental | fresh | redundancy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `explore` | `0.6` | `1.6` | `1.2` | `0.2` | `0.8` | `0.4` |
| `balance` | `1.0` | `1.0` | `0.8` | `0.8` | `0.3` | `0.8` |
| `harvest` | `1.4` | `0.3` | `0.2` | `1.2` | `0.0` | `1.2` |

```text
selection_score(n) =
  w_exploit * operator_exploitation_score(n)
  + w_explore * operator_exploration_bonus(n)
  + w_coverage * coverage_opportunity_score(n)
  + w_incremental * incremental_value_score(n)
  + w_fresh * fresh_node_bonus(n)
  - w_redundancy * redundancy_penalty(n)
```

## `selection_ranking`

- 只包含 eligible open nodes
- 按 `final_selection_score` 降序
- 分数打平时按 `open_frontier_node_ids` 顺序稳定

## `compute_unmet_requirement_weights`

- 对 active node 尚未覆盖的 must-have 赋权 `1.0`
- 已覆盖但仍可能补强的 must-have 赋权 `0.3`
- 输出必须是保序的 `list[{capability, weight}]`

## `crossover_ready`

同时满足以下条件时返回 `true`：

- `m.status = "open"`
- `m.reward_breakdown != null`
- `m.reward_breakdown.reward_score >= CrossoverGuardThresholds.min_reward_score`
- `|intersect(active.node_query_term_pool, m.node_query_term_pool)| >= CrossoverGuardThresholds.min_shared_anchor_terms`
- `unmet_must_haves_supported_by(...)` 非空

seed 节点因 `reward_breakdown = null` 默认不能成为 donor。

## `derive_term_budget_range`

- 直接读取 [[RuntimeTermBudgetPolicy]] 的预算分层

## `clamp_term_budget`

- 对输入 terms 保序去重
- 若长度超过当前上界，裁到上界
- 若长度低于当前下界，保持实际数量，不补词

## 相关

- [[SelectActiveFrontierNode]]
- [[MaterializeSearchExecutionPlan]]
- [[RuntimeBudgetState]]
