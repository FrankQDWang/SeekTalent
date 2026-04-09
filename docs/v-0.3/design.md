# SeekTalent v0.3 设计文档

## 0. 文档定位

- 版本：`v0.3`
- 状态：`design / current-head`
- 本文只持有高层边界、主链和命名规则；字段级 owner 仍然在 `payloads/`、`operators/`、`runtime/`、`semantics/`。

## 1. 当前设计结论

当前 `HEAD` 的 `v0.3` 不是 card-retrieval runtime，而是：

`single-domain knowledge-pack bootstrap + frontier runtime + offline run artifacts`

核心规则只有 6 条：

1. 每个领域只有一个 `DomainKnowledgePack`。
2. 知识库只在 round-0 关键词生成时使用，后续轮次不再继续读取知识包。
3. bootstrap routing 固定为 `explicit_domain -> inferred_domain(top1) -> generic_fallback`。
4. `qwen3-8b-reranker` 同时服务 bootstrap 路由与候选排序，但只做打分，不做生成。
5. 排序主链固定为 `rerank -> calibration -> deterministic fusion -> shortlist`。
6. 运行结果以 `SearchRunBundle` 为单事实源，trace / eval 都从 bundle 派生。

## 2. 不变边界

- 原始业务输入仍然只有 `SearchInputTruth.job_description + hiring_notes`
- CTS adapter 继续复用现有实现
- runtime 继续拥有预算、停止、审计与状态推进权
- 运行时知识库只读，不做在线联网扩展
- 5 个 LLM 调用点继续使用 provider-native strict structured output

## 3. 当前主链

### 3.1 Bootstrap

1. `ExtractRequirements`
2. `RouteDomainKnowledgePack`
3. `FreezeScoringPolicy`
4. `GenerateBootstrapOutput`
5. `InitializeFrontierState`

### 3.2 Single expansion

1. `SelectActiveFrontierNode`
2. `GenerateSearchControllerDecision`
3. `MaterializeSearchExecutionPlan`
4. `ExecuteSearchPlan`
5. `ScoreSearchResults`
6. `EvaluateBranchOutcome`
7. `ComputeNodeRewardBreakdown`
8. `UpdateFrontierState`
9. `EvaluateStopCondition`

### 3.3 Finalization

`FinalizeSearchRun` 基于 `RequirementSheet + FrontierState_t1 + stop_reason` 生成 `SearchRunResult`，再由 runtime 组装成 `SearchRunBundle`。

## 4. 当前高层对象

### 4.1 `BusinessPolicyPack`

表达业务偏好，不表达需求真相。它现在只允许单个 `knowledge_pack_id_override`。

### 4.2 `DomainKnowledgePack`

运行时知识包。每个领域一个文件，只保留：

- `routing_text`
- `include_keywords`
- `exclude_keywords`

它的唯一职责是帮助 round-0 关键词生成。

### 4.3 `BootstrapRoutingResult`

bootstrap 路由结果，持有：

- `routing_mode`
- `selected_knowledge_pack_id`
- `routing_confidence`
- `fallback_reason`
- `pack_scores`

### 4.4 `BootstrapOutput`

round-0 的稳定输出，核心就是 `frontier_seed_specifications`。它替代了旧的 `GroundingOutput`。

### 4.5 `ScoringPolicy`

run 级冻结评分口径，持有：

- fit gate
- fusion weights
- rerank instruction
- rerank query text
- reranker calibration snapshot

### 4.6 `FrontierState_t / FrontierState_t1`

run 级 frontier 状态。节点 provenance 现在只保留：

- `knowledge_pack_id`
- parent lineage
- optional donor lineage

不再保留 `source_card_ids`。

### 4.7 `SearchRunBundle`

Phase 6 的正式运行产物。它持有：

- bootstrap artifact
- per-round artifacts
- final result
- eval

trace、judge packet 和 eval 都应该从 bundle 或 canonical case bundle 渲染，不再维护第二份事实。

## 5. 知识库边界

当前 runtime 不再消费：

- reviewed synthesis reports
- compiled cards
- compiled snapshots

这些旧层已经退出运行时主链。

当前 runtime 只消费：

- `artifacts/runtime/active.json`
- `artifacts/knowledge/packs/*.json`
- `artifacts/runtime/policies/*.json`
- `artifacts/runtime/calibrations/*.json`

### 5.1 Routing 规则

- `explicit_domain`：`BusinessPolicyPack.knowledge_pack_id_override` 非空时直接命中
- `inferred_domain`：reranker 在 active knowledge packs 上取 top1
- `generic_fallback`：top1 分数不够高，或 top1 / top2 太接近时触发

### 5.2 Generic fallback 边界

- 不选任何领域包
- 不生成 `domain_company` seed
- 只用 `RequirementSheet` 生成 `strict_core` 和 `must_have_alias`
- 不允许模型发明未提供的领域上下文

## 6. LLM contract

- 5 个调用点都必须使用 strict structured output
- `retries=0`、`output_retries=1`
- `fresh request`
- `NativeOutput`
- 禁用 tools、跨 operator history 和 fallback model chain

## 7. 评估与 artifacts

- ad hoc run 固定写盘到 `runs/<run_id>/bundle.json`、`final_result.json`、`eval.json`
- canonical cases 固定写盘到 `artifacts/runtime/cases/<case_id>/`
- `trace-index.md` 和 `docs/v-0.3/traces/*` 是 checked-in offline artifacts，不是独立事实源

## 8. 旧设计已移除

以下内容只保留历史名称，不再是当前契约：

- `RetrieveGroundingKnowledge`
- `GenerateGroundingOutput`
- `GroundingKnowledgeBaseSnapshot`
- `KnowledgeRetrievalResult`
- `GroundingDraft`
- `GroundingOutput`
- `GroundingKnowledgeCard`
- `GroundingEvidenceCard`

## 相关

- [[workflow-explained]]
- [[knowledge-base]]
- [[operator-map]]
- [[llm-context-surfaces]]
- [[evaluation]]
- [[implementation-checklist]]

## 8. Node Reward 与 Stop Guard

`v0.3` 的 stop 仍然由 runtime guard 统一裁决：

- 预算耗尽
- 没有 open node
- 当前 branch 已枯竭且低增益
- 控制器建议 stop 且通过 runtime guard

对应地，reward 也必须拆成可审计的 deterministic breakdown。新的 reward 继续消费 novelty / usefulness / diversity / cost，但 top-three 增益改读 fused score，且允许纳入稳定性风险 penalty。
