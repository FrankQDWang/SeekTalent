# reward-frontier-semantics

`ExecuteSearchPlan`、`ComputeNodeRewardBreakdown`、`UpdateFrontierState`、`EvaluateStopCondition` 使用的 deterministic helper 语义 owner。

## `apply_runtime_only_constraints`

处理顺序固定为：

1. `negative_keywords` 过滤：命中任一 negative keyword 的候选直接剔除
2. `must_have_keywords` 审计标签：为剩余候选打上 runtime audit tag，不做硬过滤
3. `candidate_id` 去重前不得改变原始命中顺序

## `deduplicate_by_candidate_id`

- 以首次出现的 `candidate_id` 为准

## `page_count`

- `pages_fetched = ceil(|raw_candidates| / max(1, target_new_candidate_count))`

## `duplicate_rate`

- `1 - |deduplicated_candidates| / max(1, |raw_candidates|)`

## `wall_clock_latency`

- 直接读取执行层观测值；文档 trace 里必须显式给出最终毫秒数
- 只用于 trace / audit / 观测，不进入 reward、operator 统计或 frontier 选点

## `candidate_ids`

- 返回候选集合的稳定 id 列表

## `parent_baseline_top_three_average`

- 读取 parent node 的 `node_shortlist_score_snapshot`
- 取其中最高 3 个 `fusion_score` 的平均值；空快照返回 `0`

## `must_have_coverage_gain`

- 只看当前轮 `y_t.scored_candidates`
- 从 `node_shortlist_candidate_ids` 中选出不在 `parent_shortlist_candidate_ids` 的候选
- 返回这些 net-new shortlist 候选的 `must_have_match_score` 平均值；为空返回 `0`

## `shortlist_diversity_gain`

- `net_new_ratio = |node_shortlist_candidate_ids - run_shortlist_candidate_ids| / max(1, |node_shortlist_candidate_ids|)`
- 直接返回 `net_new_ratio`

## `average_stability_risk`

- 对当前 node shortlist 候选的 `risk_score` 做平均；为空返回 `0`

## `hard_constraint_violation_rate`

- 对当前排序结果中 `fit = 0` 的候选占比做平均

## `search_cost_penalty`

- `cost_penalty = min(1.0, 0.15 * pages_fetched)`

## `weighted_sum`

用于 reward 合成时固定为：

```text
reward_score =
  2.0 * delta_top_three
  + 1.5 * must_have_gain
  + 0.6 * new_fit_yield
  + 0.5 * novelty
  + 0.5 * usefulness
  + 0.4 * diversity
  - 0.8 * stability_risk_penalty
  - 1.0 * hard_constraint_violation
  - 0.6 * duplicate_penalty
  - 0.4 * cost_penalty
```

## `shortlist_score_snapshot`

- 只缓存 `candidate_id -> fusion_score`

## `max_fusion_score_per_candidate`

- 读取历史所有 frontier node 的 `node_shortlist_score_snapshot`
- 取每个候选在全 run 中出现过的最大 `fusion_score`

## `stable_first_seen_rank`

- 已在 `run_shortlist_candidate_ids` 中的候选保留原 rank
- 新候选按本轮进入顺序追加 rank

## `replace_status`

- 只改目标 node 的 `status`

## `accumulate_operator_statistics`

- `times_selected += 1`
- `average_reward = ((old_average * old_times) + reward_score) / times_selected`

## `zeroed_operator_statistics`

- 对 [[OperatorCatalog]] 中的每个 operator 建立 `{average_reward: 0.0, times_selected: 0}`

## 相关

- [[ExecuteSearchPlan]]
- [[ComputeNodeRewardBreakdown]]
- [[UpdateFrontierState]]
- [[EvaluateStopCondition]]
- [[OperatorStatistics]]
