# RouteDomainKnowledgePack

为 round-0 选择单个领域知识包，或者显式回退到 generic。

## Signature

```text
RouteDomainKnowledgePack : (RequirementSheet, BusinessPolicyPack, DomainKnowledgePack[], RerankerCalibration) -> BootstrapRoutingResult
```

## 当前规则

1. 若 `BusinessPolicyPack.knowledge_pack_id_override` 非空，直接命中对应 pack。
2. 否则对所有 active packs 的 `routing_text` 发起 rerank。
3. top1 分数低于 floor，或 top1 / top2 分差太小，则返回 `generic_fallback`。
4. 否则返回 top1 对应 pack。

## 关键边界

- 只选一个 pack，不再支持 dual-domain。
- routing query 只由 `role_title + must_have_capabilities + preferred_capabilities` 组成。
- 这里不做 card retrieval。

## 相关

- [[DomainKnowledgePack]]
- [[BootstrapRoutingResult]]

