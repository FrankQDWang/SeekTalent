# SearchRunSummaryDraft_t

`SearchRunFinalizationLLM` 输出的最终总结草稿。

```text
SearchRunSummaryDraft_t = { run_summary }
```

## 稳定字段组

- 运行总结草稿：`run_summary`

## Direct Producer / Direct Consumers

- Direct producer：SearchRunFinalizationLLM
- Direct consumers：[[FinalizeSearchRun]]

## Invariants

- `SearchRunSummaryDraft_t` 只承载解释性总结，不承载 shortlist 事实或 stop fact。
- 它必须通过 provider-native strict structured output 产出，不允许退回自由文本或 prompt JSON。
- 最终 `final_shortlist_candidate_ids` 与 `stop_reason` 由 runtime 持有并直接写入 `SearchRunResult`，不接受 LLM 改写。

## Implementation Surface

- Phase 2+ 默认使用 `pydantic-ai` 实现 `SearchRunFinalizationLLM`，但它只作为 typed request/response wrapper。
- 调用方式固定为 `fresh request`：使用 `instructions` 承载调用点级规则，`finalization_context_t` 作为当前 user content，默认不继承任何 message history。
- 输出模式固定为 `NativeOutput` strict schema；`allow_text_output = false`、`allow_image_output = false`。
- 禁用 `function_tools`、`builtin_tools`、任意 MCP/tool calling 与 fallback model chain。
- 默认不额外加业务型 validator retry；它只能写总结文本字段，不能接管 shortlist 或 stop facts。

## 最小示例

```yaml
run_summary: "must-have 已覆盖，ranking 背景得到补强，当前 shortlist 可进入人工审阅。"
```

## 相关

- [[FinalizeSearchRun]]
- [[SearchRunResult]]
- [[FrontierState_t1]]
