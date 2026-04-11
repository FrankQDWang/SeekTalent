# BranchEvaluation_t

对当前分支扩展结果做出的 dual-critic 判断。

```text
BranchEvaluation_t = { novelty_score, usefulness_score, branch_exhausted, repair_operator_hint, evaluation_notes }
```

## 稳定字段组

- 新颖度：`novelty_score`
- 有用度：`usefulness_score`
- 分支是否枯竭：`branch_exhausted`
- 修复建议 operator：`repair_operator_hint`
- 说明文字：`evaluation_notes`

## Direct Producer / Direct Consumers

- Direct producer：[[EvaluateBranchOutcome]]
- Direct consumers：[[ComputeNodeRewardBreakdown]]、[[UpdateFrontierState]]、[[EvaluateStopCondition]]

## Invariants

- `novelty_score` 与 `usefulness_score` 必须被 clamp 到 `[0, 1]`。
- `repair_operator_hint` 只能来自 operator catalog 或 `null`。

## 最小示例

```yaml
novelty_score: 0.66
usefulness_score: 0.74
branch_exhausted: false
repair_operator_hint: "core_precision"
evaluation_notes: "ranking 背景补强有效"
```

## 相关

- [[operator-map]]
- [[BranchEvaluationDraft_t]]
- [[EvaluateBranchOutcome]]
- [[NodeRewardBreakdown_t]]
- [[EvaluateStopCondition]]
