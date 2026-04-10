# FrontierNode_t

`frontier_nodes` 中单个节点的 canonical schema owner。

```text
FrontierNode_t = {
  frontier_node_id,
  parent_frontier_node_id,
  donor_frontier_node_id,
  selected_operator_name,
  node_query_term_pool,
  knowledge_pack_id,
  seed_rationale,
  negative_terms,
  parent_shortlist_candidate_ids,
  node_shortlist_candidate_ids,
  node_shortlist_score_snapshot,
  previous_branch_evaluation,
  reward_breakdown,
  status
}
```

## 稳定字段组

- 节点 id：`frontier_node_id`
- parent lineage：`parent_frontier_node_id`
- donor lineage：`donor_frontier_node_id`
- operator：`selected_operator_name`
- 节点 query term 池：`node_query_term_pool`
- bootstrap provenance：`knowledge_pack_id`
- seed 理由快照：`seed_rationale`
- 负向词：`negative_terms`
- parent shortlist：`parent_shortlist_candidate_ids`
- 节点 shortlist：`node_shortlist_candidate_ids`
- 节点 shortlist 分数快照：`node_shortlist_score_snapshot`
- 上一轮分支判断：`previous_branch_evaluation`
- reward：`reward_breakdown`
- 节点状态：`status`

## Invariants

- seed node 的 `parent_frontier_node_id` 与 `donor_frontier_node_id` 必须为 `null`。
- seed node 的 `previous_branch_evaluation` 与 `reward_breakdown` 必须为 `null`。
- child node 的 `parent_frontier_node_id` 必须非空；只有 `crossover_compose` 才允许 `donor_frontier_node_id` 非空。
- `knowledge_pack_id` 只表示 bootstrap 来源，不再承载 card-level provenance。

## 相关

- [[FrontierState_t]]
- [[FrontierState_t1]]
