# FrontierSelectionCandidateSummary

frontier 选点时对单个 eligible open node 的审计摘要。

```text
FrontierSelectionCandidateSummary = {
  frontier_node_id,
  selected_operator_name,
  breakdown
}
```

## Semantics

- `frontier_node_id` 是被排序的节点 id
- `selected_operator_name` 是这个节点当前 provenance 对应的 operator
- `breakdown` 是 [[FrontierSelectionBreakdown]]

## Invariants

- `selection_ranking` 中的每一项都必须来自当前轮 eligible open nodes。
- `selection_ranking[0]` 就是 active node。

## 相关

- [[FrontierSelectionBreakdown]]
- [[SearchControllerContext_t]]
