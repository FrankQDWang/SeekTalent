# SearchExecutionPlan_t

`MaterializeSearchExecutionPlan` 物化出的可执行检索计划。

```text
SearchExecutionPlan_t = {
  query_terms,
  projected_filters,
  runtime_only_constraints,
  target_new_candidate_count,
  semantic_hash,
  knowledge_pack_ids,
  child_frontier_node_stub,
  derived_position,
  derived_work_content
}
```

## Invariants

- `knowledge_pack_ids` 直接沿用 parent node provenance
- `crossover_compose` 不重新路由 pack
- `semantic_hash`、`target_new_candidate_count` 在这里冻结

## 相关

- [[MaterializeSearchExecutionPlan]]
- [[FrontierState_t]]
