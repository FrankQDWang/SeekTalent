# FrontierSelectionBreakdown

active node selection 的单节点打分拆解。

```text
FrontierSelectionBreakdown = {
  search_phase,
  operator_exploitation_score,
  operator_exploration_bonus,
  coverage_opportunity_score,
  incremental_value_score,
  fresh_node_bonus,
  redundancy_penalty,
  final_selection_score
}
```

## Semantics

- `search_phase` 来自 [[RuntimeBudgetState]]
- `operator_exploitation_score` 是 `average_reward` 的有界投影：`x / (1 + x)`
- `operator_exploration_bonus` 是 operator-level UCB exploration bonus
- `coverage_opportunity_score` 只奖励 partial coverage；`0-hit` 与 `full-hit` 都是 `0`
- `incremental_value_score` 来自 `new_fit_yield` 与 `diversity`
- `fresh_node_bonus` 只奖励从未执行过 branch evaluation 的 node
- `redundancy_penalty` 是 node shortlist 与 run shortlist 的 overlap ratio

## Invariants

- 所有原始分量都必须是非负数。
- `final_selection_score` 是 phase 权重加权后的最终结果。
- 这个 payload 只用于 trace / 审计，不直接进入 controller prompt text。

## 相关

- [[FrontierSelectionCandidateSummary]]
- [[SearchControllerContext_t]]
- [[selection-plan-semantics]]
