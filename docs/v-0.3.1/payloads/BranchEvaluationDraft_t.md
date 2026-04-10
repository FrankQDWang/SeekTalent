# BranchEvaluationDraft_t

`BranchOutcomeEvaluationLLM` 输出的分支评价草稿。

```text
BranchEvaluationDraft_t = {
  novelty_score,
  usefulness_score,
  branch_exhausted,
  repair_operator_hint,
  evaluation_notes
}
```

## 稳定字段组

- 新颖度草稿：`novelty_score`
- 有用度草稿：`usefulness_score`
- 枯竭判断草稿：`branch_exhausted`
- repair operator 草稿：`repair_operator_hint`
- 评价说明草稿：`evaluation_notes`

## Direct Producer / Direct Consumers

- Direct producer：BranchOutcomeEvaluationLLM
- Direct consumers：[[EvaluateBranchOutcome]]

## Invariants

- `BranchEvaluationDraft_t` 只是 LLM judgment 草稿，不是最终 reward / stop 可消费信号。
- 它必须通过 provider-native strict structured output 产出，不允许退回自由文本或 prompt JSON。
- `novelty_score` 与 `usefulness_score` 进入主链前必须被 clamp 到 `[0, 1]`。
- `repair_operator_hint` 进入主链前必须经过 runtime whitelist；未通过时回退为 `null`。

## Implementation Surface

- Phase 2+ 默认使用 `pydantic-ai` 实现 `BranchOutcomeEvaluationLLM`，但它只作为 typed request/response wrapper。
- 调用方式固定为 `fresh request`：使用 `instructions` 承载调用点级规则，`branch_evaluation_packet_t` 作为当前 user content，默认不继承任何 message history。
- 输出模式固定为 `NativeOutput` strict schema；`allow_text_output = false`、`allow_image_output = false`。
- 禁用 `function_tools`、`builtin_tools`、任意 MCP/tool calling 与 fallback model chain。
- 默认不额外加业务型 validator retry；reward 与 stop 的最终裁决仍由 runtime deterministic logic 持有。

## 最小示例

```yaml
novelty_score: 0.66
usefulness_score: 0.74
branch_exhausted: false
repair_operator_hint: "core_precision"
evaluation_notes: "ranking 背景补强有效"
```

## 相关

- [[EvaluateBranchOutcome]]
- [[BranchEvaluation_t]]
- [[SearchExecutionPlan_t]]
