# RuntimeTermBudgetPolicy

根据剩余 frontier 预算裁定每轮 query term 数量范围。

```text
RuntimeTermBudgetPolicy = {
  high_budget_range,
  medium_budget_range,
  low_budget_range
}
```

## 默认值

```yaml
high_budget_range: [2, 6]
medium_budget_range: [2, 5]
low_budget_range: [2, 4]
```

## Derived Rule

- `remaining_budget >= 4`：使用 `high_budget_range`
- `remaining_budget in [2, 3]`：使用 `medium_budget_range`
- `remaining_budget <= 1`：使用 `low_budget_range`

## Invariants

- `clamp_term_budget(...)` 必须保序去重后再裁切。
- 如果可用 term 数量少于当前区间下界，不得凭空补词；直接保留实际数量。

## 相关

- [[SelectActiveFrontierNode]]
- [[MaterializeSearchExecutionPlan]]
