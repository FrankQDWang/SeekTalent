# ComputeNodeRewardBreakdown

把扩展结果压缩为 deterministic reward breakdown。

## Signature

```text
ComputeNodeRewardBreakdown : (FrontierState_t, SearchExecutionPlan_t, SearchExecutionResult_t, SearchScoringResult_t, BranchEvaluation_t) -> NodeRewardBreakdown_t
```

## Notation Legend

```text
F_t := FrontierState_t
p_t := SearchExecutionPlan_t
x_t := SearchExecutionResult_t
y_t := SearchScoringResult_t
a_t := BranchEvaluation_t
b_t := NodeRewardBreakdown_t
```

## Input Projection

```text
parent_node_t = F_t.frontier_nodes[p_t.child_frontier_node_stub.parent_frontier_node_id]
parent_shortlist_t = parent_node_t.node_shortlist_candidate_ids
node_shortlist_t = y_t.node_shortlist_candidate_ids
run_shortlist_t = F_t.run_shortlist_candidate_ids
scored_rows_t = y_t.scored_candidates
```

## Primitive Predicates / Matching Rules

```text
score_snapshot_t =
  {candidate_id: fusion_score for candidate_id, fusion_score in parent_node_t.node_shortlist_score_snapshot}
```

```text
parent_baseline_top_three_average_t =
  0
  if |score_snapshot_t| = 0
  else
    Σ_{score_t in top3_desc(values(score_snapshot_t))} score_t
    / |top3_desc(values(score_snapshot_t))|
```

```text
net_new_shortlist_rows_t =
  [
    row_t
    for row_t in scored_rows_t
    if row_t.candidate_id in set(node_shortlist_t) - set(parent_shortlist_t)
  ]
```

## Transformation

```text
delta_top_three_t =
  y_t.top_three_statistics.average_fusion_score_top_three
  - parent_baseline_top_three_average_t

must_have_gain_t =
  0
  if |net_new_shortlist_rows_t| = 0
  else
    Σ_{row_t in net_new_shortlist_rows_t} row_t.must_have_match_score
    / |net_new_shortlist_rows_t|

new_fit_yield_t =
  |set(node_shortlist_t) - set(run_shortlist_t)|

diversity_t =
  |set(node_shortlist_t) - set(run_shortlist_t)| / max(1, |node_shortlist_t|)

shortlist_rows_t =
  [row_t for row_t in scored_rows_t if row_t.candidate_id in set(node_shortlist_t)]

stability_risk_penalty_t =
  0
  if |shortlist_rows_t| = 0
  else
    Σ_{row_t in shortlist_rows_t} row_t.risk_score
    / |shortlist_rows_t|

hard_constraint_violation_t =
  0
  if |scored_rows_t| = 0
  else
    |{row_t for row_t in scored_rows_t if row_t.fit = 0}| / |scored_rows_t|

duplicate_penalty_t = x_t.search_page_statistics.duplicate_rate

cost_penalty_t =
  min(
    1.0,
    0.15 * x_t.search_page_statistics.pages_fetched
  )

reward_score_t =
  2.0 * delta_top_three_t
  + 1.5 * must_have_gain_t
  + 0.6 * new_fit_yield_t
  + 0.5 * a_t.novelty_score
  + 0.5 * a_t.usefulness_score
  + 0.4 * diversity_t
  - 0.8 * stability_risk_penalty_t
  - 1.0 * hard_constraint_violation_t
  - 0.6 * duplicate_penalty_t
  - 0.4 * cost_penalty_t
```

### Field-Level Output Assembly

```text
b_t.delta_top_three = delta_top_three_t
b_t.must_have_gain = must_have_gain_t
b_t.new_fit_yield = new_fit_yield_t
b_t.novelty = a_t.novelty_score
b_t.usefulness = a_t.usefulness_score
b_t.diversity = diversity_t
b_t.stability_risk_penalty = stability_risk_penalty_t
b_t.hard_constraint_violation = hard_constraint_violation_t
b_t.duplicate_penalty = duplicate_penalty_t
b_t.cost_penalty = cost_penalty_t
b_t.reward_score = reward_score_t
```

## Defaults / Thresholds Used Here

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

## Read Set

- `FrontierState_t.frontier_nodes`
- `FrontierState_t.run_shortlist_candidate_ids`
- `SearchExecutionPlan_t.child_frontier_node_stub`
- `SearchExecutionResult_t.search_page_statistics`
- `SearchScoringResult_t.scored_candidates`
- `SearchScoringResult_t.node_shortlist_candidate_ids`
- `SearchScoringResult_t.top_three_statistics`
- `BranchEvaluation_t.novelty_score`
- `BranchEvaluation_t.usefulness_score`

## Write Set

- `NodeRewardBreakdown_t.delta_top_three`
- `NodeRewardBreakdown_t.must_have_gain`
- `NodeRewardBreakdown_t.new_fit_yield`
- `NodeRewardBreakdown_t.novelty`
- `NodeRewardBreakdown_t.usefulness`
- `NodeRewardBreakdown_t.diversity`
- `NodeRewardBreakdown_t.stability_risk_penalty`
- `NodeRewardBreakdown_t.hard_constraint_violation`
- `NodeRewardBreakdown_t.duplicate_penalty`
- `NodeRewardBreakdown_t.cost_penalty`
- `NodeRewardBreakdown_t.reward_score`

## 输入 payload

- [[FrontierState_t]]
- [[SearchExecutionPlan_t]]
- [[SearchExecutionResult_t]]
- [[SearchScoringResult_t]]
- [[BranchEvaluation_t]]

## 输出 payload

- [[NodeRewardBreakdown_t]]

## 不确定性边界 / 说明

- 这里消费 `BranchEvaluation_t` 的 LLM judgement，但 reward 合成本身保持 deterministic。
- `x_t.search_page_statistics.latency_ms` 允许进入 trace / audit，但不得参与 reward 合成。

## 相关

- [[operator-spec-style]]
- [[FrontierState_t]]
- [[SearchExecutionPlan_t]]
- [[SearchExecutionResult_t]]
- [[SearchScoringResult_t]]
- [[BranchEvaluation_t]]
- [[NodeRewardBreakdown_t]]
- [[reward-frontier-semantics]]
