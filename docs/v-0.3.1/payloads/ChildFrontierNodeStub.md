# ChildFrontierNodeStub

`SearchExecutionPlan_t` 中 child 节点 lineage 草稿的 canonical owner。

```text
ChildFrontierNodeStub = { frontier_node_id, parent_frontier_node_id, donor_frontier_node_id, selected_operator_name }
```

## 稳定字段组

- child 节点 id：`frontier_node_id`
- parent 节点 id：`parent_frontier_node_id`
- donor 节点 id：`donor_frontier_node_id`
- 产生该 child 的 operator：`selected_operator_name`

## Invariants

- `donor_frontier_node_id` 仅在 `crossover_compose` 下非空。
- `selected_operator_name` 必须来自 [[OperatorCatalog]]。

## 相关

- [[SearchExecutionPlan_t]]
- [[MaterializeSearchExecutionPlan]]
