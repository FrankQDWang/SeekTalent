# RuntimeTermBudgetPolicy

根据 `search_phase` 裁定每轮 CTS keyword term 上限。

```text
RuntimeTermBudgetPolicy = {
  explore_max_query_terms,
  balance_max_query_terms,
  harvest_max_query_terms
}
```

## 默认值

```yaml
explore_max_query_terms: 3
balance_max_query_terms: 4
harvest_max_query_terms: 6
```

## Derived Rule

- `search_phase = explore`：使用 `explore_max_query_terms`
- `search_phase = balance`：使用 `balance_max_query_terms`
- `search_phase = harvest`：使用 `harvest_max_query_terms`

## Invariants

- `derive_max_query_terms(...)` 只允许按 `search_phase` 读取这三个字段，不得回读 `remaining_budget`。
- `MaterializeSearchExecutionPlan` 不得重新从 `remaining_budget` 推导 term budget；只允许消费已冻结的 `max_query_terms`。
- round-0 bootstrap seed cap 必须直接复用 `explore_max_query_terms`；不再保留 bootstrap 私有 4-term cap。

## 相关

- [[SelectActiveFrontierNode]]
- [[MaterializeSearchExecutionPlan]]
- [[GenerateBootstrapOutput]]
