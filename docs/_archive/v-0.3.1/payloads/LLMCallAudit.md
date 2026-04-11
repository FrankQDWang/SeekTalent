# LLMCallAudit

单次 LLM 调用的完整审计对象。

```text
LLMCallAudit = {
  output_mode,
  retries,
  output_retries,
  validator_retry_count,
  model_name,
  model_settings_snapshot,
  prompt_surface
}
```

## 字段说明

- `output_mode`: 当前固定为 `NativeOutput(strict=True)`
- `retries`: agent retries
- `output_retries`: native output retries
- `validator_retry_count`: 业务型 validator 实际触发次数
- `model_name`: provider/model 标识
- `model_settings_snapshot`: 当前调用的固定模型设置快照
- `prompt_surface`: 完整 `PromptSurfaceSnapshot`

## Invariants

- 不保留 `instruction_id_or_hash`
- 不保留 `message_history_mode`
- 不保留 `tools_enabled`
- prompt 审计只认 `prompt_surface`

## 相关

- [[PromptSurfaceSnapshot]]
- [[llm-context-surfaces]]
