# RuntimeBudgetState

runtime 每轮统一构造的预算态快照。

```text
RuntimeBudgetState = {
  initial_round_budget,
  runtime_round_index,
  remaining_budget,
  used_ratio,
  remaining_ratio,
  phase_progress,
  search_phase,
  near_budget_end
}
```

## 字段说明

- `initial_round_budget`: 本次 run 的实际预算
- `runtime_round_index`: 当前轮序号，从 `0` 开始
- `remaining_budget`: 当前剩余轮数
- `used_ratio`: 已用预算比例
- `remaining_ratio`: 剩余预算比例
- `phase_progress`: 归一化 phase 进度
- `search_phase`: `explore / balance / harvest`
- `near_budget_end`: 尾段预算告警标志

## Invariants

- `RuntimeBudgetState` 是 phase 的唯一 owner
- `phase_progress = runtime_round_index / max(1, initial_round_budget - 1)`
- `phase_progress` 必须 clamp 到 `[0.0, 1.0]`
- `search_phase` 固定切分：
  - `< 0.34` -> `explore`
  - `< 0.67` -> `balance`
  - 其余 -> `harvest`
- `near_budget_end` 继续使用 `used_ratio >= 0.8`
- Step 2 只建立 phase 事实，不直接改变 runtime 策略

## Direct Producer / Direct Consumers

- Direct producer：`build_runtime_budget_state(...)`
- Direct consumers：[[SelectActiveFrontierNode]]、[[GenerateSearchControllerDecision]]、[[EvaluateBranchOutcome]]

## 相关

- [[SearchControllerContext_t]]
- [[llm-context-surfaces]]
