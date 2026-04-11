# BootstrapKeywordDraft

`BootstrapKeywordGenerationLLM` 现在输出的是候选 seeds，而不是固定坑位关键词。

```text
SeedIntent = {
  intent_type,
  keywords,
  source_knowledge_pack_ids,
  reasoning
}

BootstrapKeywordDraft = {
  candidate_seeds,
  negative_keywords
}
```

## 稳定字段组

- `candidate_seeds`：`5-8` 条候选 seed intents
- `negative_keywords`：全局负向词

## Invariants

- 至少包含 `core_precision` 和 `relaxed_floor`
- `generic_fallback` 下不得出现 `pack_expansion / cross_pack_bridge`
- `inferred_multi_pack` 下必须出现 `cross_pack_bridge`
- 每条 intent 的 `keywords` 必须能物化成非空 seed

## 相关

- [[BootstrapRoutingResult]]
- [[GenerateBootstrapOutput]]
