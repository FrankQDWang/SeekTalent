# InitializeFrontierState

用 `BootstrapOutput` 初始化第一版 frontier。

## Signature

```text
InitializeFrontierState : (BootstrapOutput, RuntimeSearchBudget, OperatorCatalog) -> FrontierState_t
```

## 当前规则

- 每个 `FrontierSeedSpecification` 变成一个 seed node
- `knowledge_pack_id` 直接写入 node provenance
- round-0 seed 的 `node_query_term_pool` 直接继承已经按 `explore_max_query_terms` 截断后的 `seed_terms`
- seed node 的 `reward_breakdown` 和 `previous_branch_evaluation` 固定为 `null`
- `remaining_budget` 直接取 `RuntimeSearchBudget.initial_round_budget`

## 关键边界

- frontier 初始化是 runtime-owned 行为，不允许 LLM 直接写 frontier state
- seed node 在拿到第一份 reward 之前不能作为 donor

## 相关

- [[BootstrapOutput]]
- [[FrontierState_t]]
- [[RuntimeSearchBudget]]
