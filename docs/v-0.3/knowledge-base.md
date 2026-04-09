# SeekTalent v0.3 知识库规范

## 0. 定位

本页定义 `v0.3` 运行时知识库的输入格式、编译规则和使用边界。

`v0.3` 使用的是“本地只读知识库 + 结构化检索”，不是通用文档切块检索架构。

## 1. 全链路边界与运行时两层制

对完整资料链路，`v0.3` 明确区分 3 层边界：

1. 外部原始研究稿：只做研究输入，不是 `v0.3` runtime contract
2. reviewed synthesis reports：保留为 Markdown，用于人工审核、冲突比对、追溯和编译输入冻结
3. 编译后 knowledge cards / snapshot：保留为结构化对象，供 runtime 检索与 bootstrap 使用

对运行时正式知识库 contract 而言，仍然只有“reviewed synthesis reports + compiled cards”两层；runtime 只直接消费第 3 层。
reviewed synthesis reports 不能直接进入 runtime prompt。
当前 reviewed synthesis reports 统一存放在 `artifacts/knowledge/reviewed_reports/*.md`。
编译后的 cards / snapshot 统一存放在 `artifacts/knowledge/compiled/`。

更底层的多模型原始研究稿只作为研究输入与 provenance，不再是当前 `v0.3` runtime contract 的正式层。

## 2. Reviewed Synthesis Report 格式

每份 reviewed synthesis report 一个 Markdown 文件。文件名可以使用描述性命名，runtime 不再依赖固定文件名契约，正式身份由 YAML 头决定。
这些文件属于跨版本共享运行时资产，不再放在 `docs/v-0.3/` 下。

YAML 头必须包含：

```yaml
report_id: string
report_type: role_family | business_vertical | negative_confusion | company_background
domain_id: string
title: string
source_model: string
generated_on: YYYY-MM-DD
language: zh-CN
confidence_summary: high | medium | low
source_reports: list[string]
```

正文必须包含 8 个 section：

1. `Summary`
2. `Canonical Terms`
3. `Alias Map`
4. `Positive Signals`
5. `Negative/Confusion Signals`
6. `Seed Branch Suggestions`
7. `Rerank Cues`
8. `Open Questions`

补充边界：

- `Rerank Cues` 属于 reviewed synthesis report 的审核/编译输入内容，不等于 runtime 直接读取的知识库字段。
- 如果未来要把某类 `Rerank Cues` 真正下放到排序层，必须新增明确 owner；不能借 report section 隐式进入 runtime contract。

## 3. 编译后 Knowledge Card 格式

运行时使用的 `GroundingKnowledgeCard` 必须至少包含：

```json
{
  "card_id": "role_alias.llm_agent_rag_engineering.backend_agent_engineer",
  "domain_id": "llm_agent_rag_engineering",
  "report_type": "role_family",
  "card_type": "role_alias",
  "title": "LLM/Agent 后端工程师",
  "summary": "面向 Agent/RAG/LLM 应用的后端与平台研发角色。",
  "canonical_terms": ["agent engineer", "rag engineer"],
  "aliases": ["llm application engineer", "ai backend engineer"],
  "positive_signals": ["tool calling", "workflow orchestration", "retrieval pipeline"],
  "negative_signals": ["pure prompt运营", "纯算法论文研究"],
  "query_terms": ["agent engineer", "rag", "python"],
  "must_have_links": ["cap.tool_orchestration", "cap.retrieval_pipeline"],
  "preferred_links": ["bg.to_b_delivery"],
  "confidence": "high",
  "source_report_ids": ["report.role_family.llm_agent_rag_engineering.codex_synthesis_2026_04_07"],
  "source_model_votes": 2,
  "freshness_date": "2026-04-07"
}
```

## 4. 编译规则

多模型研究报告编译为 knowledge cards 时，固定遵循以下规则：

1. 同一 claim 至少 2 份报告一致才标 `high`
2. 只有 1 份报告支持则标 `medium`
3. 报告间明显冲突则标 `low`
4. `low` confidence claim 不能进入 must-have gate
5. `negative_signals` 与 `query_terms` 必须都可追溯到 `source_report_ids`；更深一层原始来源继续保留在 synthesis report 的 `source_reports`
6. 编译时必须保留 `source_model_votes`

## 5. 必须准备的报告类型

- `role_family`：角色家族、title 别名、能力锚点、典型职责边界
- `business_vertical`：垂直业务域、行业术语、业务问题、常见公司与背景信号
- `negative_confusion`：可选扩展；只有当混淆面明显超出主报告承载能力时才单独拆出
- `company_background`：可选，只有当业务明确把公司背景当作强偏好时才需要

首版硬要求不是“每个 domain pack 都有独立 `negative_confusion` 文件”，而是“每份 active reviewed synthesis report 都必须包含 `Negative/Confusion Signals` section”。

## 6. 初始 Domain Packs

知识库第一版默认准备 3 个 domain packs：

- `llm_agent_rag_engineering`
- `search_ranking_retrieval_engineering`
- `finance_risk_control_ai`

如果当前业务不招聘金融风控方向，则第三个 pack 替换成当前最高优先级垂类。

首版验收口径改为 section coverage，而不是按文件数量计数：

- 每个 active domain pack 至少有一份 reviewed synthesis report
- 该报告必须包含 8 个必备 section
- 其中 `Negative/Confusion Signals` section 不得缺失
- 编译后 card 的 `source_report_ids` 必须能回溯到 report header 的 `report_id`
- `GroundingKnowledgeBaseSnapshot.compiled_report_ids` 必须全部能解析到 `artifacts/knowledge/reviewed_reports/` 下的 report header

## 6.1 Artifact 位置

- reviewed reports: `artifacts/knowledge/reviewed_reports/`
- compiled cards: `artifacts/knowledge/compiled/cards.json`
- compiled snapshots: `artifacts/knowledge/compiled/snapshots/<snapshot_id>.json`

## 7. 运行时使用边界

运行时知识库只直接服务一件事：

1. 在 bootstrap 阶段为关键词初始化提供受限上下文，帮助 `GroundingGenerationLLM` 与 `GenerateGroundingOutput` 产出更稳的 round-0 seed branches

以下事情不属于运行时知识库职责：

- repair 阶段的额外策略控制
- 直接作为排序层 runtime 输入去驱动 signal expansion / confusion suppression
- 在线联网检索
- generic long-context RAG
- 直接生成最终排序结论
- 替代 `RequirementSheet`
