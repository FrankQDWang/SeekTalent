# SeekTalent v0.3 知识库规范

## 0. 定位

当前 `HEAD` 的运行时知识库已经收缩成非常简单的形态：

- 每个领域一个 `DomainKnowledgePack`
- 每个 knowledge pack 只服务 round-0 关键词生成
- 运行时不再做 card retrieval

## 1. 当前运行时 contract

runtime 只直接消费：

- `artifacts/runtime/active.json`
- `artifacts/knowledge/packs/<knowledge_pack_id>.json`

每个 pack 必须包含：

```json
{
  "knowledge_pack_id": "llm_agent_rag_engineering",
  "label": "LLM / Agent 应用研发",
  "routing_text": "agent backend, rag, tool calling, workflow orchestration, retrieval pipeline",
  "include_keywords": ["agent engineer", "rag", "tool calling", "python"],
  "exclude_keywords": ["pure prompt operation", "algorithm research only"]
}
```

## 2. 运行时怎么使用它

### 2.1 路由

bootstrap 固定走三选一：

1. `explicit_domain`
2. `inferred_domain`
3. `generic_fallback`

规则如下：

- 如果 `BusinessPolicyPack.knowledge_pack_id_override` 非空，直接命中对应 pack
- 否则用 reranker 对所有 active packs 的 `routing_text` 打分，取 top1
- top1 分数太低，或 top1 / top2 太接近时，走 `generic_fallback`

### 2.2 关键词生成

选中的 knowledge pack 只会进入 round-0 的关键词生成 prompt。

- `include_keywords` 用来帮助模型补全更像该领域的关键词
- `exclude_keywords` 会直接投影到 seed 的 `negative_terms`

generic fallback 下不选任何 pack，也不允许模型发明领域背景。

## 3. active manifest

`artifacts/runtime/active.json` 现在至少要绑定：

- `phase`
- `knowledge_pack_ids`
- `policy_id`
- `calibration_id`

它决定当前 runtime 真正使用哪一组知识包。

## 4. 强约束

- active manifest 中的每个 `knowledge_pack_id` 都必须能找到文件
- `routing_text` 不能为空
- `include_keywords` 不能为空
- `exclude_keywords` 不能为空

## 5. 已移除的旧层

以下对象已经退出当前运行时主链：

- reviewed synthesis reports
- compiled cards
- compiled snapshots
- `GroundingKnowledgeCard`
- `GroundingKnowledgeBaseSnapshot`
- `KnowledgeRetrievalResult`

这些历史资产如果还保留在仓库里，也只用于归档，不再作为 runtime contract。

## 6. 当前领域包

当前 active 运行时默认准备 3 个领域：

- `llm_agent_rag_engineering`
- `search_ranking_retrieval_engineering`
- `finance_risk_control_ai`

## 7. 非职责

知识库当前不负责：

- 后续轮次 repair / crossover 决策
- 排序层信号扩展
- 直接生成最终 shortlist
- 在线联网检索
- 替代 `RequirementSheet`
