# SearchControllerDecision_t

控制器规范化后的分支级决策对象。

```text
SearchControllerDecision_t = { action, target_frontier_node_id, selected_operator_name, operator_args, expected_gain_hypothesis }
```

## 稳定字段组

- 动作：`action`
- 目标节点 id：`target_frontier_node_id`
- operator 名称：`selected_operator_name`
- operator 参数：`operator_args`
- 预期增益假设：`expected_gain_hypothesis`

## Direct Producer / Direct Consumers

- Direct producer：[[GenerateSearchControllerDecision]]
- Direct consumers：[[MaterializeSearchExecutionPlan]]、[[EvaluateStopCondition]]

## Invariants

- `target_frontier_node_id` 必须绑定当前 active node。
- `selected_operator_name` 必须来自 [[SearchControllerContext_t]].allowed_operator_names。
- `action` 只能是 `search_cts` 或 `stop`。
- 当 `action = "stop"` 时，`operator_args` 必须是 `{}`。
- 当 `action != "stop"` 且 `selected_operator_name != "crossover_compose"` 时，`operator_args` 必须包含 `additional_terms`。
- 当 `action != "stop"` 且 `selected_operator_name = "crossover_compose"` 时，`operator_args` 必须包含 `donor_frontier_node_id`、`crossover_rationale`、`shared_anchor_terms`、`donor_terms_used`。

## 最小示例

```yaml
action: "search_cts"
target_frontier_node_id: "seed_agent_core"
selected_operator_name: "crossover_compose"
operator_args:
  donor_frontier_node_id: "child_search_domain_01"
  crossover_rationale: "active node 已覆盖 Agent 与 Python，donor 可补 retrieval/ranking。"
  shared_anchor_terms: ["rag"]
  donor_terms_used: ["retrieval engineer", "ranking"]
expected_gain_hypothesis: "用共享锚点维持语义聚焦，同时补足 retrieval/ranking 覆盖。"
```

## 相关

- [[SearchControllerContext_t]]
- [[SearchControllerDecisionDraft_t]]
- [[OperatorCatalog]]
- [[GenerateSearchControllerDecision]]
