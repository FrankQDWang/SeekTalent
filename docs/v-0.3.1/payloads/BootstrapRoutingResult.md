# BootstrapRoutingResult

round-0 pack 路由结果。

```text
BootstrapRoutingResult = {
  routing_mode,
  selected_knowledge_pack_ids,
  routing_confidence,
  fallback_reason,
  pack_scores
}
```

## 稳定字段组

- `routing_mode`：`explicit_pack / inferred_single_pack / inferred_multi_pack / generic_fallback`
- `selected_knowledge_pack_ids`：最终带入 bootstrap 的 pack 列表，最多 2 个
- `routing_confidence`：top1 校准分
- `fallback_reason`：只有 generic fallback 才会填写
- `pack_scores`：所有 active packs 的分数

## Invariants

- `generic_fallback` 下 `selected_knowledge_pack_ids = []`
- `explicit_pack` 下只允许 1 个 pack
- `inferred_multi_pack` 只允许 top2 同时入选

## 相关

- [[RouteDomainKnowledgePack]]
- [[BootstrapOutput]]
