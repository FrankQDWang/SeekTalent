# FrontierSeedSpecification

用于初始化 frontier 的单条 round-0 seed。

```text
FrontierSeedSpecification = {
  operator_name,
  seed_terms,
  seed_rationale,
  knowledge_pack_ids,
  negative_terms,
  target_location
}
```

## Invariants

- round-0 只允许 `core_precision / must_have_alias / relaxed_floor / pack_expansion / cross_pack_bridge / generic_expansion`
- generic fallback 下 `knowledge_pack_ids = []`
- routed path 下 `knowledge_pack_ids` 继承 pack provenance，可为 1 或 2 个
- `seed_terms` 上限与 `RuntimeTermBudgetPolicy.explore_max_query_terms` 完全同源；round-0 直接按 explore 语义执行

## 相关

- [[BootstrapOutput]]
- [[OperatorCatalog]]
- [[RuntimeTermBudgetPolicy]]
