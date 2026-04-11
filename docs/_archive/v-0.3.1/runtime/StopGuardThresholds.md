# StopGuardThresholds

统一 stop guard 使用的低增益阈值。

```text
StopGuardThresholds = { novelty_floor, usefulness_floor, reward_floor }
```

## 默认值

```yaml
novelty_floor: 0.25
usefulness_floor: 0.25
reward_floor: 1.5
```

## Invariants

- `budget_exhausted` 与 `no_open_node` 永远先于其他 stop 条件判定。
- `controller_stop` 是否可接受由 `RuntimeBudgetState.search_phase` 决定，不再由固定 round index 决定。
- `exhausted_low_gain` 的低收益阈值仍由本对象提供，但只有在 `search_phase = harvest` 时才具备 run-level stop 资格。

## 相关

- [[EvaluateStopCondition]]
- [[RuntimeBudgetState]]
