# CarryForwardFrontierState

在 direct-stop 路径中把当前 frontier 原样投影为 stop guard 可消费的 `FrontierState_t1`。

## Signature

```text
CarryForwardFrontierState : FrontierState_t -> FrontierState_t1
```

## Notation Legend

```text
F_t := FrontierState_t
F_{t+1} := FrontierState_t1
```

## Input Projection

```text
frontier_nodes_t = F_t.frontier_nodes
open_ids_t = F_t.open_frontier_node_ids
closed_ids_t = F_t.closed_frontier_node_ids
run_term_catalog_t = F_t.run_term_catalog
run_shortlist_t = F_t.run_shortlist_candidate_ids
semantic_hashes_seen_t = F_t.semantic_hashes_seen
operator_statistics_t = F_t.operator_statistics
remaining_budget_t = F_t.remaining_budget
```

## Transformation

### Field-Level Output Assembly

```text
F_{t+1}.frontier_nodes = frontier_nodes_t
F_{t+1}.open_frontier_node_ids = open_ids_t
F_{t+1}.closed_frontier_node_ids = closed_ids_t
F_{t+1}.run_term_catalog = run_term_catalog_t
F_{t+1}.run_shortlist_candidate_ids = run_shortlist_t
F_{t+1}.semantic_hashes_seen = semantic_hashes_seen_t
F_{t+1}.operator_statistics = operator_statistics_t
F_{t+1}.remaining_budget = remaining_budget_t
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

## 输出 payload

- [[FrontierState_t1]]

## 不确定性边界 / 说明

- 这是 identity carry-forward，不新增 child node，不消耗额外 frontier 更新逻辑。

## 相关

- [[operator-spec-style]]
- [[FrontierState_t]]
- [[FrontierState_t1]]
- [[EvaluateStopCondition]]
