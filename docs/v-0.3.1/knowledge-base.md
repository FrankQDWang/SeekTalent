# SeekTalent v0.3.1 知识库规范

## 当前 contract

runtime 直接消费：

- `artifacts/runtime/active.json`
- `artifacts/knowledge/packs/<knowledge_pack_id>.json`

每个 pack 只保留：

- `knowledge_pack_id`
- `label`
- `routing_text`
- `include_keywords`
- `exclude_keywords`

## 当前用法

- reranker 先根据 `routing_text` 做 pack 路由
- routed path 最多带 2 个 packs 进入 bootstrap prompt
- `include_keywords` 只服务 seed 生成
- `exclude_keywords` 直接进入 round-0 `negative_terms`

## 当前边界

- 不做 card retrieval
- 不扩 pack schema
- 不在后续轮次重新读取 packs
