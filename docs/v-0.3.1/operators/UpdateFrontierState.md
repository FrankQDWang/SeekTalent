# UpdateFrontierState

把一次扩展的结果回写到 frontier，并生成下一状态。

## Signature

```text
UpdateFrontierState : (FrontierState_t, SearchExecutionPlan_t, SearchScoringResult_t, BranchEvaluation_t, NodeRewardBreakdown_t) -> FrontierState_t1
```

## Notation Legend

```text
F_t := FrontierState_t
F_{t+1} := FrontierState_t1
p_t := SearchExecutionPlan_t
y_t := SearchScoringResult_t
a_t := BranchEvaluation_t
b_t := NodeRewardBreakdown_t
```

## Input Projection

```text
parent_node_t = F_t.frontier_nodes[p_t.child_frontier_node_stub.parent_frontier_node_id]
parent_node_id_t = parent_node_t.frontier_node_id
child_stub_t = p_t.child_frontier_node_stub
node_shortlist_t = y_t.node_shortlist_candidate_ids
run_shortlist_t = F_t.run_shortlist_candidate_ids
```

## Primitive Predicates / Matching Rules

```text
current_node_shortlist_score_snapshot_t =
  {
    row_t.candidate_id: row_t.fusion_score
    for row_t in y_t.scored_candidates
    if row_t.candidate_id in set(node_shortlist_t)
  }
```

```text
historical_snapshots_t =
  [
    node_t.node_shortlist_score_snapshot
    for node_t in values(F_t.frontier_nodes)
  ]
```

```text
candidate_best_fusion_scores_t =
  max_by_key(
    historical_snapshots_t + [current_node_shortlist_score_snapshot_t],
    key = candidate_id,
    value = fusion_score
  )
```

```text
candidate_first_seen_rank_t =
  {
    candidate_id: index
    for index, candidate_id in enumerate(run_shortlist_t + [c for c in node_shortlist_t if c not in set(run_shortlist_t)])
  }
```

## Transformation

```text
child_frontier_node_t = {
  frontier_node_id: child_stub_t.frontier_node_id,
  parent_frontier_node_id: child_stub_t.parent_frontier_node_id,
  donor_frontier_node_id: child_stub_t.donor_frontier_node_id,
  selected_operator_name: child_stub_t.selected_operator_name,
  node_query_term_pool: stable_deduplicate(parent_node_t.node_query_term_pool + p_t.query_terms),
  knowledge_pack_id: p_t.knowledge_pack_id,
  seed_rationale: null,
  negative_terms: p_t.runtime_only_constraints.negative_keywords,
  parent_shortlist_candidate_ids: parent_node_t.node_shortlist_candidate_ids,
  node_shortlist_candidate_ids: node_shortlist_t,
  node_shortlist_score_snapshot: current_node_shortlist_score_snapshot_t,
  previous_branch_evaluation: a_t,
  reward_breakdown: b_t,
  status: "closed" if a_t.branch_exhausted else "open"
}
```

```text
updated_frontier_nodes_t =
  {
    node_id_t:
      (
        {
          frontier_node_id: F_t.frontier_nodes[node_id_t].frontier_node_id,
          parent_frontier_node_id: F_t.frontier_nodes[node_id_t].parent_frontier_node_id,
          donor_frontier_node_id: F_t.frontier_nodes[node_id_t].donor_frontier_node_id,
          selected_operator_name: F_t.frontier_nodes[node_id_t].selected_operator_name,
          node_query_term_pool: F_t.frontier_nodes[node_id_t].node_query_term_pool,
          knowledge_pack_id: F_t.frontier_nodes[node_id_t].knowledge_pack_id,
          seed_rationale: F_t.frontier_nodes[node_id_t].seed_rationale,
          negative_terms: F_t.frontier_nodes[node_id_t].negative_terms,
          parent_shortlist_candidate_ids: F_t.frontier_nodes[node_id_t].parent_shortlist_candidate_ids,
          node_shortlist_candidate_ids: F_t.frontier_nodes[node_id_t].node_shortlist_candidate_ids,
          node_shortlist_score_snapshot: F_t.frontier_nodes[node_id_t].node_shortlist_score_snapshot,
          previous_branch_evaluation: F_t.frontier_nodes[node_id_t].previous_branch_evaluation,
          reward_breakdown: F_t.frontier_nodes[node_id_t].reward_breakdown,
          status: "closed" if node_id_t = parent_node_id_t else F_t.frontier_nodes[node_id_t].status
        }
      )
    for node_id_t in keys(F_t.frontier_nodes)
  }

updated_frontier_nodes_t[child_frontier_node_t.frontier_node_id] = child_frontier_node_t
```

### Field-Level Output Assembly

```text
F_{t+1}.frontier_nodes = updated_frontier_nodes_t
F_{t+1}.open_frontier_node_ids =
  stable_deduplicate(
    [node_id for node_id in F_t.open_frontier_node_ids if node_id != parent_node_id_t]
    + ([child_frontier_node_t.frontier_node_id] if child_frontier_node_t.status = "open" else [])
  )
F_{t+1}.closed_frontier_node_ids =
  stable_deduplicate(F_t.closed_frontier_node_ids + [parent_node_id_t])
F_{t+1}.run_term_catalog = set(F_t.run_term_catalog) ∪ set(p_t.query_terms)
F_{t+1}.run_shortlist_candidate_ids =
  stable_sort_desc(
    stable_deduplicate(run_shortlist_t + node_shortlist_t),
    key = candidate_best_fusion_scores_t[candidate_id],
    tie_break = -candidate_first_seen_rank_t[candidate_id]
  )
F_{t+1}.semantic_hashes_seen = F_t.semantic_hashes_seen ∪ {p_t.semantic_hash}
F_{t+1}.operator_statistics =
  {
    operator_name:
      {
        average_reward:
          (
            F_t.operator_statistics[operator_name].average_reward
            * F_t.operator_statistics[operator_name].times_selected
            + (b_t.reward_score if operator_name = child_frontier_node_t.selected_operator_name else 0.0)
          )
          / (
            F_t.operator_statistics[operator_name].times_selected
            + (1 if operator_name = child_frontier_node_t.selected_operator_name else 0)
          ),
        times_selected:
          F_t.operator_statistics[operator_name].times_selected
          + (1 if operator_name = child_frontier_node_t.selected_operator_name else 0)
      }
    for operator_name in keys(F_t.operator_statistics)
  }
F_{t+1}.remaining_budget = F_t.remaining_budget - 1
```

## Defaults / Thresholds Used Here

```text
run-global shortlist is always ordered by best observed fusion score,
with first-seen order as the stable tie-breaker.
```

## Read Set

- `FrontierState_t.frontier_nodes`
- `FrontierState_t.open_frontier_node_ids`
- `FrontierState_t.closed_frontier_node_ids`
- `FrontierState_t.run_term_catalog`
- `FrontierState_t.run_shortlist_candidate_ids`
- `FrontierState_t.semantic_hashes_seen`
- `FrontierState_t.operator_statistics`
- `FrontierState_t.remaining_budget`
- `SearchExecutionPlan_t.query_terms`
- `SearchExecutionPlan_t.semantic_hash`
- `SearchExecutionPlan_t.knowledge_pack_id`
- `SearchExecutionPlan_t.runtime_only_constraints`
- `SearchExecutionPlan_t.child_frontier_node_stub`
- `SearchScoringResult_t.scored_candidates`
- `SearchScoringResult_t.node_shortlist_candidate_ids`
- `BranchEvaluation_t.*`
- `NodeRewardBreakdown_t.*`

## Write Set

- `FrontierState_t1.frontier_nodes`
- `FrontierState_t1.open_frontier_node_ids`
- `FrontierState_t1.closed_frontier_node_ids`
- `FrontierState_t1.run_term_catalog`
- `FrontierState_t1.run_shortlist_candidate_ids`
- `FrontierState_t1.semantic_hashes_seen`
- `FrontierState_t1.operator_statistics`
- `FrontierState_t1.remaining_budget`

## 输入 payload

- [[FrontierState_t]]
- [[SearchExecutionPlan_t]]
- [[SearchScoringResult_t]]
- [[BranchEvaluation_t]]
- [[NodeRewardBreakdown_t]]

## 输出 payload

- [[FrontierState_t1]]

## 不确定性边界 / 说明

- 这是纯 runtime 状态推进步骤，不允许由 LLM 直接回写 frontier。

## 相关

- [[operator-spec-style]]
- [[FrontierState_t]]
- [[FrontierNode_t]]
- [[SearchExecutionPlan_t]]
- [[SearchScoringResult_t]]
- [[BranchEvaluation_t]]
- [[NodeRewardBreakdown_t]]
- [[FrontierState_t1]]
- [[OperatorStatistics]]
- [[reward-frontier-semantics]]
