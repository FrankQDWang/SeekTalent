# StopGuardThresholds

统一 stop guard 使用的低增益与轮次阈值。

```text
StopGuardThresholds = { novelty_floor, usefulness_floor, reward_floor, min_round_index }
```

## 默认值

```yaml
novelty_floor: 0.25
usefulness_floor: 0.25
reward_floor: 1.5
min_round_index: 2
```

## Invariants

- `budget_exhausted` 与 `no_open_node` 永远先于其他 stop 条件判定。
- `controller_stop` 只有在 `runtime_round_index >= min_round_index` 时才允许被 runtime 接受。

## 相关

- [[EvaluateStopCondition]]
- [[RuntimeRoundState]]
