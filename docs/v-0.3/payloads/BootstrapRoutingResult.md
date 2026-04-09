# BootstrapRoutingResult

round-0 领域路由结果。

```text
BootstrapRoutingResult = { routing_mode, selected_knowledge_pack_id, routing_confidence, fallback_reason, pack_scores }
```

## 稳定字段组

- 路由模式：`routing_mode`
- 命中的知识包：`selected_knowledge_pack_id`
- 置信度：`routing_confidence`
- fallback 原因：`fallback_reason`
- 所有 pack 的分数：`pack_scores`

## Direct Producer / Direct Consumers

- Direct producer：[[RouteDomainKnowledgePack]]
- Direct consumers：[[GenerateBootstrapOutput]]

## Invariants

- `routing_mode` 只允许 `explicit_domain / inferred_domain / generic_fallback`。
- `generic_fallback` 下，`selected_knowledge_pack_id` 必须为空。
- routed path 下最多只允许命中一个 knowledge pack。

## 相关

- [[DomainKnowledgePack]]
- [[BootstrapOutput]]
