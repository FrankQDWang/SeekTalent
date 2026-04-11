# RouteDomainKnowledgePack

为 round-0 选择 0-2 个 knowledge packs。

## Signature

```text
RouteDomainKnowledgePack : (
  RequirementSheet,
  BusinessPolicyPack,
  DomainKnowledgePack[],
  RerankerCalibration
) -> BootstrapRoutingResult
```

## 当前规则

1. `knowledge_pack_id_override` 非空时，直接返回 `explicit_pack`
2. 否则对所有 active packs 的 `routing_text` 做 rerank
3. `top1 < 0.55` 时走 `generic_fallback`
4. 否则至少保留 top1
5. 若 `top2 >= 0.55` 且 `top1 - top2 <= 0.08`，追加 top2，返回 `inferred_multi_pack`
6. 其余 routed 情况返回 `inferred_single_pack`

## 相关

- [[BootstrapRoutingResult]]
- [[DomainKnowledgePack]]
