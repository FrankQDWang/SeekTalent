# SearchControllerDecisionDraft_t

`SearchControllerDecisionLLM` 输出的控制器决策草稿。

```text
SearchControllerDecisionDraft_t = {
  action,
  selected_operator_name,
  operator_args,
  expected_gain_hypothesis
}
```

## 稳定字段组

- 动作草稿：`action`
- operator 名草稿：`selected_operator_name`
- operator 参数草稿：`operator_args`
- 预期增益假设草稿：`expected_gain_hypothesis`

## Direct Producer / Direct Consumers

- Direct producer：SearchControllerDecisionLLM
- Direct consumers：[[GenerateSearchControllerDecision]]

## Invariants

- `SearchControllerDecisionDraft_t` 只是 LLM 草稿，不是最终 branch-level patch。
- 它必须通过 provider-native strict structured output 产出，不允许退回自由文本或 prompt JSON。
- `target_frontier_node_id` 不由 LLM 直接写出，最终由 runtime 绑定到当前 active node。
- `selected_operator_name`、`operator_args` 与 `action` 都必须经过 `GenerateSearchControllerDecision` 的 whitelist / trim / budget clamp 才能进入主链。

## Implementation Surface

- Phase 2+ 默认使用 `pydantic-ai` 实现 `SearchControllerDecisionLLM`，但它只作为 typed request/response wrapper。
- 调用方式固定为 `fresh request`：使用 `instructions` 承载调用点级规则，`SearchControllerContext_t` 作为当前 user content，默认不继承任何 cross-operator history。
- 输出模式固定为 `NativeOutput` strict schema；`allow_text_output = false`、`allow_image_output = false`。
- 禁用 `function_tools`、`builtin_tools`、任意 MCP/tool calling 与 fallback model chain。
- 它是唯一允许单次 bounded `output_validator + ModelRetry` 的调用点；补充校验边界仅限“能物化非空 query terms”与 runtime canonicalization。

## 最小示例

```yaml
action: "search_cts"
selected_operator_name: "crossover_compose"
operator_args:
  donor_frontier_node_id: "child_search_domain_01"
  crossover_rationale: "active node 已覆盖 Agent 与 Python，donor 可补 retrieval/ranking。"
  shared_anchor_terms: ["rag"]
  donor_terms_used: ["retrieval engineer", "ranking"]
expected_gain_hypothesis: "用共享锚点维持语义聚焦，同时补足 retrieval/ranking 覆盖。"
```

## 相关

- [[SearchControllerContext_t]]
- [[GenerateSearchControllerDecision]]
- [[SearchControllerDecision_t]]
