# PromptSurfaceSnapshot

单次 LLM 调用真正看到的完整 prompt surface 审计快照。

```text
PromptSurfaceSnapshot = {
  surface_id,
  instructions_text,
  input_text,
  instructions_sha1,
  input_sha1,
  sections
}
```

## 字段说明

- `surface_id`: 调用点标识，例如 `search_controller_decision`
- `instructions_text`: markdown prompt 文件内容
- `input_text`: 最终 user content 文本
- `instructions_sha1`: `instructions_text` 的内容摘要
- `input_sha1`: `input_text` 的内容摘要
- `sections`: 有序 `PromptSurfaceSection[]`

## Invariants

- `PromptSurfaceSnapshot` 是唯一 prompt 审计 owner
- `input_text` 必须由 `sections` 按固定顺序拼接得到
- 不保留 raw JSON prompt fallback
- 不保留 hash-only 审计替代完整文本

## 相关

- [[PromptSurfaceSection]]
- [[LLMCallAudit]]
- [[llm-context-surfaces]]
