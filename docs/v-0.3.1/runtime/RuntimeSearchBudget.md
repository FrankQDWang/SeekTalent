# RuntimeSearchBudget

frontier 初始化与单次检索计划共用的 runtime 预算。

```text
RuntimeSearchBudget = { initial_round_budget, default_target_new_candidate_count, max_target_new_candidate_count }
```

## 默认值

```yaml
initial_round_budget: 5
default_target_new_candidate_count: 10
max_target_new_candidate_count: 20
```

## Invariants

- `remaining_budget` 初始值必须来自 `initial_round_budget`。
- `target_new_candidate_count` 缺失时默认取 `default_target_new_candidate_count`。
- 任意 operator 都不得把 `target_new_candidate_count` 提升到 `max_target_new_candidate_count` 以上。

## 相关

- [[InitializeFrontierState]]
- [[MaterializeSearchExecutionPlan]]
- [[SearchExecutionPlan_t]]
