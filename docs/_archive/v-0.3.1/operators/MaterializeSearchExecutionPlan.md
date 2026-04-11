# MaterializeSearchExecutionPlan

把控制器决策物化成可执行 child 检索计划。

## Signature

```text
MaterializeSearchExecutionPlan : (FrontierState_t, RequirementSheet, SearchControllerDecision_t, max_query_terms, RuntimeSearchBudget, CrossoverGuardThresholds) -> SearchExecutionPlan_t
```

## 当前规则

### 非 crossover

- 直接读取规范化后的 `operator_args.query_terms`
- 不再把 parent `node_query_term_pool` 自动拼回去
- 按传入的冻结 `max_query_terms` 裁切

### crossover

- donor 必须存在
- `shared_anchor_terms` 必须真的同时出现在 parent / donor query pool
- 共享锚点不足时直接 fail-fast
- query terms 由 `shared_anchor_terms + donor_terms_used` 组成

### 统一收口

- `projected_filters = RequirementSheet.hard_constraints`
- `runtime_only_constraints.must_have_keywords = must_have_capabilities + query_terms`
- `runtime_only_constraints.negative_keywords = parent.negative_terms + donor.negative_terms`
- `knowledge_pack_id` 直接沿用 parent node 的 provenance
- `semantic_hash` 固定由 operator、query terms、filters、runtime constraints 生成

## Term Budget Owner

这里不再接收 `RuntimeTermBudgetPolicy`，也不再读取 `remaining_budget`。

`max_query_terms` 必须来自 controller context 中已经冻结好的有效值。  
也就是说，controller normalization 和 materialization 共享同一个 query budget owner。

## 关键边界

- 这里只处理 `action = search_cts`
- 这里不会重新做领域路由
- provenance 只保留 `knowledge_pack_id`，不再合并 `source_card_ids`

## 相关

- [[SearchExecutionPlan_t]]
- [[RuntimeSearchBudget]]
- [[CrossoverGuardThresholds]]
- [[cts-projection-policy]]
