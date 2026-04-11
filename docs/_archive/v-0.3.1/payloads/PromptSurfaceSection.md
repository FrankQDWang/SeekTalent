# PromptSurfaceSection

单个 prompt section 的审计快照。

```text
PromptSurfaceSection = {
  title,
  body_text,
  source_paths,
  is_dynamic
}
```

## 字段说明

- `title`: section 标题，保序
- `body_text`: section 正文，已经是最终发给 LLM 的文本
- `source_paths`: 该 section 读取的 typed 字段路径
- `is_dynamic`: 该 section 是否属于运行态动态内容

## Invariants

- section 顺序固定，由 `PromptSurfaceSnapshot.sections` 持有
- `body_text` 是最终文本，不再需要二次渲染
- `source_paths` 只记录 owner 字段来源，不做兼容 alias

## 相关

- [[PromptSurfaceSnapshot]]
- [[LLMCallAudit]]
