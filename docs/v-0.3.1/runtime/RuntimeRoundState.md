# RuntimeRoundState

stop guard 与 dual-view trace 共享的 round 级运行态。

```text
RuntimeRoundState = { runtime_round_index }
```

## 默认约定

- bootstrap 完成、第一次进入 `SelectActiveFrontierNode` 前，`runtime_round_index = 0`
- 每完成一次 `UpdateFrontierState` 或 `CarryForwardFrontierState`，下一轮 `runtime_round_index += 1`

## Invariants

- `runtime_round_index` 是 0-based 整数。
- 它只表达运行轮次，不重复持有预算或 frontier 快照。

## 相关

- [[EvaluateStopCondition]]
- [[trace-index]]
