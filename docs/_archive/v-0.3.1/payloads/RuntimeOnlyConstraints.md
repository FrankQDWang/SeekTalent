# RuntimeOnlyConstraints

检索执行层不要求 CTS 原生支持、但 runtime 仍要坚持的约束。

```text
RuntimeOnlyConstraints = { must_have_keywords, negative_keywords }
```

## 稳定字段组

- must-have 审计词：`must_have_keywords`
- 负向排除词：`negative_keywords`

## Invariants

- `negative_keywords` 命中时直接过滤。
- `must_have_keywords` 只用于 runtime audit，不是硬过滤。

## 最小示例

```yaml
must_have_keywords: ["Python backend", "LLM application"]
negative_keywords: ["data analyst", "pure algorithm research"]
```

## 相关

- [[SearchExecutionPlan_t]]
- [[ExecuteSearchPlan]]
