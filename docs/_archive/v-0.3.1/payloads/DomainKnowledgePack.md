# DomainKnowledgePack

单个领域的运行时知识包。

```text
DomainKnowledgePack = { knowledge_pack_id, label, routing_text, include_keywords, exclude_keywords }
```

## 稳定字段组

- 包 id：`knowledge_pack_id`
- 展示标签：`label`
- 路由文本：`routing_text`
- 建议关键词：`include_keywords`
- 排除关键词：`exclude_keywords`

## Direct Producer / Direct Consumers

- Direct producer：runtime artifact files
- Direct consumers：[[RouteDomainKnowledgePack]]、[[GenerateBootstrapOutput]]

## Invariants

- `routing_text`、`include_keywords`、`exclude_keywords` 都不能为空。
- 它只服务 round-0 bootstrap，不进入后续 search / ranking / reward / stop。

## 相关

- [[BootstrapRoutingResult]]
- [[BootstrapOutput]]
