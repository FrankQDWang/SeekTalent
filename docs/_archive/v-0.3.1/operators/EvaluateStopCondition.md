# EvaluateStopCondition

统一裁决是否停止，而不是把 stop 决策下放给单个 prompt。

## Signature

```text
EvaluateStopCondition : (FrontierState_t1, SearchControllerDecision_t, BranchEvaluation_t | null, NodeRewardBreakdown_t | null, StopGuardThresholds, RuntimeBudgetState) -> {stop_reason, continue_flag}
```

## Notation Legend

```text
F_{t+1} := FrontierState_t1
d_t := SearchControllerDecision_t
a_t := BranchEvaluation_t | null
b_t := NodeRewardBreakdown_t | null
```

## Input Projection

```text
remaining_budget_t = F_{t+1}.remaining_budget
open_node_ids_t = F_{t+1}.open_frontier_node_ids
search_phase_t = RuntimeBudgetState.search_phase
```

## Transformation

```text
budget_exhausted_t = (remaining_budget_t <= 0)

no_open_node_t = (|open_node_ids_t| = 0)

low_gain_branch_t =
  a_t != null
  and b_t != null
  and a_t.branch_exhausted
  and a_t.novelty_score < StopGuardThresholds.novelty_floor
  and a_t.usefulness_score < StopGuardThresholds.usefulness_floor
  and b_t.reward_score < StopGuardThresholds.reward_floor

controller_stop_requested_t = (d_t.action = "stop")

controller_stop_accepted_t =
  controller_stop_requested_t
  and search_phase_t in {"balance", "harvest"}

low_gain_stop_accepted_t =
  low_gain_branch_t
  and search_phase_t = "harvest"
```

### Field-Level Output Assembly

```text
stop_reason =
  "budget_exhausted" if budget_exhausted_t
  else "no_open_node" if no_open_node_t
  else "exhausted_low_gain" if low_gain_stop_accepted_t
  else "controller_stop" if controller_stop_accepted_t
  else null

continue_flag = (stop_reason = null)
```

## Defaults / Thresholds Used Here

```text
StopGuardThresholds defaults = {
  novelty_floor: 0.25,
  usefulness_floor: 0.25,
  reward_floor: 1.5
}
```

## Read Set

- `FrontierState_t1.remaining_budget`
- `FrontierState_t1.open_frontier_node_ids`
- `SearchControllerDecision_t.action`
- `BranchEvaluation_t.branch_exhausted` when `BranchEvaluation_t != null`
- `BranchEvaluation_t.novelty_score` when `BranchEvaluation_t != null`
- `BranchEvaluation_t.usefulness_score` when `BranchEvaluation_t != null`
- `NodeRewardBreakdown_t.reward_score` when `NodeRewardBreakdown_t != null`
- `StopGuardThresholds`
- `RuntimeBudgetState.search_phase`

## Write Set

- `stop_reason`
- `continue_flag`

## 输入 payload

- [[FrontierState_t1]]
- [[SearchControllerDecision_t]]
- [[BranchEvaluation_t]] | `null`
- [[NodeRewardBreakdown_t]] | `null`
- [[StopGuardThresholds]]
- [[RuntimeBudgetState]]

## 输出 payload

- `stop_reason / continue_flag`
- [[EffectiveStopGuard]] 会由同一 runtime owner 同轮生成，并写入 `SearchRoundArtifact.effective_stop_guard`；它不是临时 sidecar。

## 不确定性边界 / 说明

- `budget_exhausted` 与 `no_open_node` 永远先于控制器 stop 建议。
- `direct-stop` 路径允许传入 `BranchEvaluation_t = null` 与 `NodeRewardBreakdown_t = null`；此时只评估 budget / open-node / controller-stop guard。
- `controller_stop` 只在 `search_phase ∈ {balance, harvest}` 时允许被 runtime 接受。
- `exhausted_low_gain` 只在 branch evaluation 与 reward breakdown 都已存在，且 `search_phase = harvest` 时可触发。
- stop 最终由 runtime guard 决定，控制器只能建议，不能直接终止 run。

## 相关

- [[operator-spec-style]]
- [[FrontierState_t1]]
- [[SearchControllerDecision_t]]
- [[BranchEvaluation_t]]
- [[NodeRewardBreakdown_t]]
- [[StopGuardThresholds]]
- [[RuntimeBudgetState]]
