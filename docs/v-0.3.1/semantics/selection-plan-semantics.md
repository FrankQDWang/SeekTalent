# selection-plan-semantics

`SelectActiveFrontierNode` 与 `MaterializeSearchExecutionPlan` 使用的 deterministic helper 语义 owner。

## `frontier_priority`

```text
frontier_priority(n) =
  1.5 if n.parent_frontier_node_id = null else 0.0
  + 0.8 * operator_average_reward(n.selected_operator_name)
  + 0.4 if n.previous_branch_evaluation = null else 0.0
  - 1.0 if n.previous_branch_evaluation.branch_exhausted else 0.0
```

- `operator_average_reward` 来自 [[OperatorStatistics]]
- seed 节点默认享有启动优先级，但不会永久压制高 reward child 节点

## `unmet_requirement_bonus`

- 以 `node_query_term_pool` 是否已经覆盖 must-have 的 lexical proxy 计算
- 每个尚未被当前 query pool 触达的 must-have 贡献 `+0.6`

## `saturation_penalty`

- `overlap_ratio = |node_shortlist_candidate_ids ∩ run_shortlist_candidate_ids| / max(1, |node_shortlist_candidate_ids|)`
- `saturation_penalty = 1.2 * overlap_ratio`
- seed 节点 shortlist 为空时 penalty 为 `0`

## `unmet_must_haves_supported_by`

- 输入：donor `node_query_term_pool`、`R.must_have_capabilities`、active `node_query_term_pool`
- 输出：被 donor query terms 覆盖、但未被 active query terms 覆盖的 must-have 列表

## `compute_unmet_requirement_weights`

- 对 active node 尚未覆盖的 must-have 赋权 `1.0`
- 已覆盖但仍可能补强的 must-have 赋权 `0.3`
- 输出必须是保序的 `list[{capability, weight}]`，顺序与 `R.must_have_capabilities` 一致

## `crossover_ready`

同时满足以下条件时返回 `true`：

- `m.status = "open"`
- `m.reward_breakdown != null`
- `m.reward_breakdown.reward_score >= CrossoverGuardThresholds.min_reward_score`
- `|intersect(active.node_query_term_pool, m.node_query_term_pool)| >= CrossoverGuardThresholds.min_shared_anchor_terms`
- `unmet_must_haves_supported_by(...)` 非空

seed 节点因 `reward_breakdown = null` 默认不能成为 donor

## `derive_term_budget_range`

- 直接读取 [[RuntimeTermBudgetPolicy]] 的预算分层

## `clamp_term_budget`

- 对输入 terms 保序去重
- 若长度超过当前上界，裁到上界
- 若长度低于当前下界，保持实际数量，不补词

## `whitelist_terms`

- 保序过滤，只保留出现在 allowed set 中的 term

## `derive_seed_id`

- `seed_{operator_name}_{stable_hash(seed_terms,target_location)[:8]}`

## `derive_child_id`

- `child_{parent_frontier_node_id}_{semantic_hash[:8]}`

## 相关

- [[SelectActiveFrontierNode]]
- [[MaterializeSearchExecutionPlan]]
- [[RuntimeTermBudgetPolicy]]
- [[CrossoverGuardThresholds]]
