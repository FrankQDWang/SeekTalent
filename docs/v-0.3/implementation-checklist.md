# SeekTalent v0.3 最终开工清单

## 0. 文档定位

本页是 `v0.3` 的最终开工清单。

- 它只定义阶段顺序、阶段目标、主要工作、交付物、可开工验收和阶段前置条件。
- 解释性内容统一链接到 [[design]]、[[workflow-explained]]、[[operator-map]] 和对应 `payloads/`、`operators/`、`runtime/`、`semantics/`。
- 本页不重复定义第二套 schema，不重复持有公式或 trace 模板。

## 1. 开工前提

以下内容视为当前 `v0.3` 已冻结基线，不再单独占用正式 phase：

1. `docs/v-0.3` 中的 `payloads/`、`operators/`、`runtime/`、`semantics/` owner 已冻结。
2. operator 展示风格以 [[operator-spec-style]] 为准。
3. CTS adapter / enum substrate 继续复用现有实现与测试；`v0.3` 文档只持边界，不复制第二份 code table。见 [[cts-projection-policy]]。
4. 5 个 LLM 调用点的 structured output contract 已冻结为 provider-native strict schema + `retries=0` + `output_retries=1`。
5. 5 个 LLM 调用点对应的 draft payload owner 已冻结，不允许边实现边发明匿名草稿 schema。
6. 知识库 contract 已冻结为 reviewed synthesis reports + compiled cards；更底层原始研究稿只作为 provenance。

## 2. Phase 1: Runtime Contracts 与 CTS Bridge

### 2.1 目标

先把 runtime 主链输入输出、候选对象分层和 CTS 投影边界落稳，避免后续实现边写边发明对象。

### 2.2 主要工作

1. 固化主链 I/O：
   `SearchInputTruth -> RequirementSheet -> SearchExecutionPlan_t -> SearchExecutionResult_t -> SearchScoringResult_t -> SearchRunResult`
2. 落实候选两层 schema：
   [[RetrievedCandidate_t]] 与 [[ScoringCandidate_t]]
3. 落实 `SearchExecutionResult_t.raw_candidates / deduplicated_candidates / scoring_candidates`
4. 固化 `SearchExecutionPlan_t.projected_filters`、`runtime_only_constraints` 和 [[cts-projection-policy]] 的 CTS bridge 边界
5. 明确 reranker 可见字段、runtime-only 字段、CTS 下推字段三者边界

### 2.3 交付物

1. runtime 主链对象已在 `payloads/` 中完整落地
2. CTS bridge 规则已能直接指导 adapter / runtime 接口实现
3. 候选对象 producer / consumer 边界已能直接指导 `ExecuteSearchPlan` 与 `ScoreSearchResults`

### 2.4 可开工验收

1. 实现者可以仅凭文档确定 runtime 主链 I/O，不再自行定义候选对象 shape。
2. CTS 下推字段、runtime-only 字段、reranker 可见字段边界清楚。
3. 不需要回查 `v0.2` 才能知道 CTS 投影怎么做。

### 2.5 下一阶段前提

1. [[SearchExecutionResult_t]]、[[RetrievedCandidate_t]]、[[ScoringCandidate_t]] 已稳定。
2. [[cts-projection-policy]] 已足以约束 CTS adapter 复用边界。

### 2.6 当前完成情况（截至当前 HEAD）

- 总体状态：Phase 1 已按“contract 先落稳”的目标完成收口；CLI / API 的 `run` 入口仍保持 gated，这不视为 Phase 1 未完成，因为本阶段验收标准是“后续实现者无需再做设计决策即可开工”，不是“bootstrap 或 ranking 主流程已跑通”。

对应 `2.2 主要工作`：

1. 主链 I/O 已落地为稳定对象：`SearchInputTruth`、`RequirementSheet`、`SearchExecutionPlan_t`、`SearchExecutionResult_t`、`SearchScoringResult_t`、`SearchRunResult` 均已在 `payloads/` 和当前代码实现中对齐。
2. 候选两层 schema 已落地：`RetrievedCandidate_t` 与 `ScoringCandidate_t` 已稳定；`scoring_text` 固定为自然文本面，不走 JSON dump。
3. `SearchExecutionResult_t.raw_candidates / deduplicated_candidates / scoring_candidates` 已落地；runtime-only 执行顺序已固定为 `negative_keywords` 过滤 -> `must_have_keywords` 审计打标 -> `candidate_id` 去重。`must_have_keywords` 当前只作为 runtime sidecar 审计事实存在，不进入稳定 payload。
4. `SearchExecutionPlan_t.projected_filters`、`runtime_only_constraints` 与 [[cts-projection-policy]] 的 CTS bridge 边界已落地。补充说明：经验 / 年龄区间下推以当前已验证 CTS enum substrate 为准，允许 cross-bucket 业务范围映射到最稳定可用 bucket；排序层 fit gate 继续承担更细粒度真值约束。
5. reranker 可见字段、runtime-only 字段、CTS 下推字段的边界已写实对齐：reranker 面固定为 `ScoringCandidate_t.scoring_text` 等评分侧字段；`negative_keywords / must_have_keywords` 不下推 CTS；`projected_filters + derived_position + derived_work_content` 继续作为 CTS adapter 的唯一读取入口。

对应 `2.3 交付物`：

1. runtime 主链对象已完整落地，并由现有测试覆盖 Phase 1 contract。
2. CTS bridge 规则已能直接指导 adapter / runtime 接口实现；文档已明确“真实 CTS bucket substrate 优先”的实际口径，不再把 cross-bucket 映射误写成严格 truth-preserving。
3. 候选对象 producer / consumer 边界已能直接指导 `ExecuteSearchPlan` 与 `ScoreSearchResults`：其中 `CareerStabilityProfile` 现已按 deterministic timeline parsing 产出，不再只是占位低置信度默认值。

对应 `2.4 可开工验收`：

1. 已满足。实现者可以仅凭当前文档与代码中的稳定对象 shape 开始 Phase 2 / Phase 3，不需要再补发明候选对象结构。
2. 已满足。CTS 下推字段、runtime-only 字段、reranker 可见字段边界已清楚，并与测试对齐。
3. 已满足。不需要回查 `v0.2` 才能知道 CTS 投影怎么做；`v0.2` 只保留为观测证据，不再是规范入口。

对应 `2.5 下一阶段前提`：

1. 已满足。`[[SearchExecutionResult_t]]`、`[[RetrievedCandidate_t]]`、`[[ScoringCandidate_t]]` 可视为 Phase 1 稳定基线；runtime sidecar 审计事实不改变这三个稳定 payload 的 shape。
2. 已满足。[[cts-projection-policy]] 已足以约束 CTS adapter 复用边界；后续阶段应继续复用当前 bucket substrate，而不是在 Phase 2+ 重新设计第二套 enum 规则。

## 3. Phase 2: Bootstrap Path

### 3.1 目标

把首轮启动从“裸 JD 长 query”变成可落地的 bootstrap 链。

### 3.2 主要工作

1. 实现 [[ExtractRequirements]]
2. 实现 [[RetrieveGroundingKnowledge]]
3. 实现 [[FreezeScoringPolicy]]
4. 实现 [[GenerateGroundingOutput]]
5. 实现 [[InitializeFrontierState]]

### 3.3 交付物

1. round-0 bootstrap 主链已可按 [[workflow-explained]] 执行
2. routing、knowledge retrieval、grounding、frontier init 的输入输出对象已能串通
3. run 内冻结评分口径对象 [[ScoringPolicy]] 已可供后续阶段直接消费
4. [[KnowledgeRetrievalResult]] 已作为 routing fields 的唯一 owner，不再存在独立 routing metadata payload

### 3.4 可开工验收

1. round-0 固定为 `requirements -> routing/retrieval -> scoring freeze -> grounding -> frontier init`。
2. routing 只有 `explicit_domain / inferred_domain / generic_fallback`。
3. seed branches 固定 `3-5` 条，每条 `2-4` 个 terms。
4. [[ScoringPolicy]] 已作为 run 内冻结对象存在，而不是运行中散落参数。
5. 知识库只服务 bootstrap 关键词初始化，不参与评分冻结、reward、stop 或 finalize。

### 3.5 下一阶段前提

1. [[FrontierState_t]] 可以由 bootstrap 主链稳定初始化。
2. [[ScoringPolicy]]、[[GroundingOutput]]、[[KnowledgeRetrievalResult]] 已稳定可读。

## 4. Phase 3: Search Execution 与 Ranking

### 4.1 目标

把“搜到简历之后如何进入评分”收成一个稳定实现切片。

### 4.2 主要工作

1. 实现 [[MaterializeSearchExecutionPlan]]
2. 实现 [[ExecuteSearchPlan]]
3. 实现 [[ScoreSearchResults]]
4. 落实 reranker text conversion 与 text-only contract
5. 落实 [[CareerStabilityProfile]] 的评分侧接入

### 4.3 交付物

1. `SearchExecutionPlan_t -> SearchExecutionResult_t -> SearchScoringResult_t` 已可直接串通
2. reranker request surface 已稳定为 `instruction / query / document-text`
3. 候选已先进入 `scoring_candidates` 再进入评分

### 4.4 可开工验收

1. reranker 输入 contract 固定为 `instruction / query / document-text`。
2. `document` 明确不是 JSON dump。
3. 候选必须先进入 `scoring_candidates` 再进入评分。
4. `rerank -> calibration -> deterministic fusion -> shortlist` 是唯一主排序链。
5. 跳槽风险只作为 risk penalty，不直接变成检索层硬过滤。
6. `degree_requirement` canonical 固定为 `null / 大专及以上 / 本科及以上 / 硕士及以上 / 博士及以上`；显式“不限”在上游折叠为 `null`。

### 4.5 下一阶段前提

1. [[SearchScoringResult_t]] 已可稳定产出 shortlist 和 fused score。
2. `ExecuteSearchPlan` 和 `ScoreSearchResults` 的边界已不再含糊。

## 5. Phase 4: Frontier Decision Loop

### 5.1 目标

把“选哪条 branch、怎么扩、何时允许 crossover”做成受控 runtime 循环。

### 5.2 主要工作

1. 实现 [[SelectActiveFrontierNode]]
2. 实现 [[GenerateSearchControllerDecision]]
3. 实现 [[CarryForwardFrontierState]]
4. 落实 `crossover_compose`
5. 落实 donor legality、shared-anchor guard、semantic dedupe

### 5.3 交付物

1. active-node selection、controller patch、search path、direct-stop path 已形成统一 loop
2. crossover 已有清晰的 donor 和 guard 边界
3. runtime 选点与 controller 局部决策职责已分离

### 5.4 可开工验收

1. runtime 先选 active node，控制器只做 branch-level patch。
2. donor 必须满足 `open + reward_breakdown != null + reward 过线 + shared anchor 过线`。
3. generic provenance 下不得放开 `domain_company`。
4. direct-stop 路径与 search 路径使用统一状态对象，不再有旁路状态。

### 5.5 下一阶段前提

1. [[SearchControllerDecision_t]]、[[SearchExecutionPlan_t]]、[[FrontierState_t1]] 已能形成闭环。
2. crossover 的合法性和 lineage 已可审计。

## 6. Phase 5: Reward / Frontier Update / Stop / Finalize

### 6.1 目标

把 branch value、状态推进和 run 结束统一收口成 runtime deterministic 控制面。

### 6.2 主要工作

1. 实现 [[EvaluateBranchOutcome]]
2. 实现 [[ComputeNodeRewardBreakdown]]
3. 实现 [[UpdateFrontierState]]
4. 实现 [[EvaluateStopCondition]]
5. 实现 [[FinalizeSearchRun]]

### 6.3 交付物

1. branch judgment、reward、frontier update、stop、finalize 已形成完整 run 末端闭环
2. run-global shortlist 的排序事实和 stop reason 已可回放
3. 最终结果对象 [[SearchRunResult]] 已能稳定生成

### 6.4 可开工验收

1. reward 明确读 fused score，不读旧 base score。
2. run-global shortlist 由最佳已观测 `fusion_score` 决定。
3. stop 统一由 runtime guard 裁决，控制器只能建议。
4. finalizer 只能写总结，不能改 shortlist 事实。

### 6.5 下一阶段前提

1. 单次 run 已可从 bootstrap 执行到 `SearchRunResult`。
2. 所有关键 runtime artifacts 已具备回放价值。
3. runtime artifacts 已足以渲染同一 case 的 `Agent Trace` 与 `Business Trace`。

## 7. Phase 6: Offline Eval 与 Knowledge Base Compile Loop

### 7.1 目标

补齐可回放、可评估、可持续更新知识库的闭环，并把 trace 破坏式升级为双轨 artifact，但不阻塞前 5 个 runtime 阶段开工。

### 7.2 主要工作

1. 按 [[evaluation]] 准备 offline eval matrix
2. 实现 [[trace-spec]] 与 [[trace-index]]，固化 `Agent Trace / Business Trace` 双轨模板与 `Judge Packet`
3. 补齐 9 个 `case_id` 的 paired trace，不再保留旧 worked trace 体系
4. 固化原始报告 -> compiled cards -> snapshot 的审核与编译规则
5. 固化知识库 snapshot、policy snapshot、calibration snapshot 与 trace bundle 的审计产物

### 7.3 交付物

1. `Agent Trace`、`Business Trace`、[[trace-index]]、`Judge Packet` 模板已稳定
2. 关键场景可回放，且同一 case 可同时服务 replay/judge 与业务复盘
3. reranker、routing、crossover、reward 的离线评估 artifacts 已可归因
4. knowledge base compile loop 已有稳定输入、审核点和输出 snapshot
5. LLM call 审计产物至少保留 `output_mode / retries / output_retries / validator_retry_count`
6. reviewed synthesis report 具备稳定 `report_id`；compiled cards 的 `source_report_ids` 已追溯到 synthesis report header

### 7.4 可开工验收

1. `trace-index` 可以作为双轨 trace 的唯一导航入口。
2. 9 个 `case_id` 都同时存在 `Agent Trace` 与 `Business Trace`，且 operator 顺序与 terminal outcome 对齐。
3. `Agent Trace` 可直接服务 replay 与 llm-as-a-judge。
4. `Business Trace` 仍保留每个算子的输入、输出、工具调用与业务含义。
5. eval artifacts 能支撑 reranker、routing、crossover、reward 的分项归因。
6. knowledge base compile loop 有明确输入、审核点和 snapshot 产物。
7. runtime 只承认 reviewed synthesis reports + compiled cards，不再要求把更底层原始研究稿作为 `docs/v-0.3/knowledge-base` 的正式 contract。

## 8. 使用规则

1. 每阶段只按“目标 -> 主要工作 -> 交付物 -> 可开工验收 -> 下一阶段前提”推进，不在本页扩写字段定义。
2. 解释性内容统一链接到 [[design]]、[[workflow-explained]]、[[operator-map]] 和对应 owner 文档。
3. 验收标准统一解释为“实现者无需再做设计决策即可开工”，不是“必须已跑通全部代码”。
4. 如果未来新增阶段或重排顺序，优先保持 runtime 主链不变，再调整旁路线工作。

## 9. 相关

- [[design]]
- [[workflow-explained]]
- [[operator-map]]
- [[evaluation]]
- [[trace-spec]]
- [[trace-index]]
- [[operator-spec-style]]
- [[cts-projection-policy]]
