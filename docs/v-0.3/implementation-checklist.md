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
6. Phase 2+ 默认使用 `pydantic-ai` 作为 5 个 LLM 调用点的 typed request/response wrapper；不允许把它扩成 runtime orchestrator、frontier state owner、CTS tool runner、rerank runner 或 reward / stop controller。
7. `pydantic-ai` 调用风格已冻结为 `fresh request + instructions + NativeOutput + no tools + no cross-operator history`。
8. 知识库 contract 已冻结为 active knowledge packs；更底层研究稿和旧 compiled assets 不再进入 runtime 主链。

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

- 总体状态：Phase 1 已按“contract 先落稳”的目标完成收口；当前 HEAD 的公开 `run` 入口已由 Phase 5 接通，这不改变本阶段的验收口径，因为本阶段关注的始终是 contract 是否稳定，而不是主链是否已经串完。

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
2. 实现 [[RouteDomainKnowledgePack]]
3. 实现 [[FreezeScoringPolicy]]
4. 实现 [[GenerateBootstrapOutput]]
5. 实现 [[InitializeFrontierState]]

### 3.3 交付物

1. round-0 bootstrap 主链已可按 [[workflow-explained]] 执行
2. routing、bootstrap keyword generation、frontier init 的输入输出对象已能串通
3. run 内冻结评分口径对象 [[ScoringPolicy]] 已可供后续阶段直接消费
4. [[BootstrapRoutingResult]] 已作为 routing fields 的唯一 owner

### 3.4 可开工验收

1. round-0 固定为 `requirements -> route knowledge pack -> scoring freeze -> generate bootstrap output -> frontier init`。
2. routing 只有 `explicit_domain / inferred_domain / generic_fallback`。
3. routed path 最多生成 `3` 条 seeds；generic fallback 固定为 `2` 条。
4. [[ScoringPolicy]] 已作为 run 内冻结对象存在，而不是运行中散落参数。
5. 知识库只服务 bootstrap 关键词初始化，不参与评分冻结、reward、stop 或 finalize。
6. `RequirementExtractionLLM` 与 `BootstrapKeywordGenerationLLM` 的实现必须遵守统一 `pydantic-ai` 调用约束：fresh request、`NativeOutput`、禁用 tools、禁用跨 operator history。

### 3.5 下一阶段前提

1. [[FrontierState_t]] 可以由 bootstrap 主链稳定初始化。
2. [[ScoringPolicy]]、[[BootstrapOutput]]、[[BootstrapRoutingResult]] 已稳定可读。

### 3.6 当前完成情况（截至当前 HEAD）

- 总体状态：Phase 2 已按“round-0 bootstrap 内核先落地”的目标完成收口；当前 HEAD 的公开 `run` 入口已由 Phase 5 接通，这不改变本阶段的验收口径，因为本阶段关注的是 bootstrap 主链与稳定产物，而不是用户侧 runtime 是否已经对外开放。

对应 `3.2 主要工作`：

1. `[[ExtractRequirements]]` 已落地为现有 deterministic requirements 归一化主链与 `RequirementExtractionLLM` wrapper 的组合；`SearchInputTruth -> RequirementSheet` 的 owner 已固定，不再另起第二套 requirements schema。
2. `[[RouteDomainKnowledgePack]]` 已落地为稳定纯函数，routing 固定为 `explicit_domain / inferred_domain / generic_fallback` 三选一；未知 explicit `knowledge_pack_id_override` 直接 fail-fast。bootstrap 所需的最小 domain knowledge packs 已以内置 fixture 形式落地。
3. `[[FreezeScoringPolicy]]` 已落地为 run 内评分口径冻结步骤；fit gate 只允许在 truth 基础上收紧，fusion weights 会规范化到总和 `1.0`，并与 rerank instruction / query 一起形成稳定的 `[[ScoringPolicy]]`。
4. `[[GenerateBootstrapOutput]]` 已落地为稳定纯函数；当前 routed path 产出 `strict_core / must_have_alias / domain_company` 三条以内 seeds，generic fallback 只产出 `strict_core / must_have_alias` 两条 seeds。补充说明：`BootstrapKeywordGenerationLLM` 的 round-0 输出已收窄到 `BootstrapKeywordDraft`，不再让 LLM 直接写 operator patch。
5. `[[InitializeFrontierState]]` 已落地为 runtime-owned frontier init；bootstrap seeds 会被收敛成稳定的 `[[FrontierState_t]]`，不允许 LLM draft 直接写入 frontier runtime state。

对应 `3.3 交付物`：

1. round-0 bootstrap 主链已可按 [[workflow-explained]] 执行；当前实现入口为 `bootstrap_round0_async(...)` 与同步薄包装 `bootstrap_round0(...)`。
2. routing、bootstrap keyword generation、frontier init 的输入输出对象已能串通；`BootstrapArtifacts` 已稳定持有 `SearchInputTruth`、`RequirementSheet`、`BootstrapRoutingResult`、`ScoringPolicy`、`BootstrapOutput`、`FrontierState_t`。
3. run 内冻结评分口径对象 `[[ScoringPolicy]]` 已可供后续阶段直接消费，不再依赖运行中散落参数。
4. `[[BootstrapRoutingResult]]` 已成为 routing fields 的唯一 owner；不存在独立 routing metadata payload。

对应 `3.4 可开工验收`：

1. 已满足。round-0 顺序已固定为 `requirements -> route knowledge pack -> scoring freeze -> generate bootstrap output -> frontier init`，bootstrap orchestration 不再留白。
2. 已满足。routing 只有 `explicit_domain / inferred_domain / generic_fallback` 三种模式；当前测试已覆盖 explicit、inferred、generic 三条路径以及相关 fail-fast 边界。
3. 已满足。routed path 最多生成 `3` 条 seeds；generic fallback 固定为 `2` 条。
4. 已满足。`[[ScoringPolicy]]` 已作为 run 内冻结对象存在，而不是运行中散落参数。
5. 已满足。知识库当前只服务 bootstrap 关键词初始化；不参与评分冻结、reward、stop 或 finalize。
6. 已满足。`RequirementExtractionLLM` 与 `BootstrapKeywordGenerationLLM` 已按统一 `pydantic-ai` 约束实现：fresh request、`NativeOutput`、禁用 tools、禁用跨 operator history、`retries=0`、`output_retries=1`。

对应 `3.5 下一阶段前提`：

1. 已满足。`[[FrontierState_t]]` 现已可由 bootstrap 主链稳定初始化；seed id、初始 node status、remaining budget、run term catalog 等关键字段已有确定行为。
2. 已满足。`[[ScoringPolicy]]`、`[[BootstrapOutput]]`、`[[BootstrapRoutingResult]]` 已稳定可读，可直接作为 Phase 3 / Phase 4 的输入基线继续推进。

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

### 4.6 当前完成情况（截至当前 HEAD）

- 总体状态：Phase 3 已按“search execution 与 ranking 先收成稳定 deterministic 切片”的目标完成收口；当前 HEAD 的公开 `run` 入口已由 Phase 5 接通，这不改变本阶段的验收口径，因为本阶段关注的是 execution / scoring operator 是否稳定，而不是 runtime 全链路何时开放。

对应 `4.2 主要工作`：

1. `[[MaterializeSearchExecutionPlan]]` 已落地为稳定纯函数；当前实现会直接消费 `FrontierState_t`、`RequirementSheet`、`SearchControllerDecision_t`、`RuntimeTermBudgetPolicy`、`RuntimeSearchBudget` 与 `CrossoverGuardThresholds`，固定执行 query term materialization、runtime-only constraints 冻结、target-new clamp、semantic hash 与 stable child identity 生成，不再把这些规则散落在后续 runtime 中。
2. `[[ExecuteSearchPlan]]` 已落地为“CTS 调用 + 现有候选投影 owner 复用”的薄执行层；它只读取 `SearchExecutionPlan_t` 并调用现有 `CTSClientProtocol.search(...)`，随后复用 `SearchExecutionResult_t.raw_candidates / deduplicated_candidates / scoring_candidates` 的既有投影逻辑，不再另起第二套候选转换 path。补充说明：`search_page_statistics.pages_fetched` 现已严格按 owner 语义 `ceil(|raw_candidates| / max(1, target_new_candidate_count))` 计算，空结果保持 `0`，作为后续 reward 成本事实的直接输入。
3. `[[ScoreSearchResults]]` 已落地为稳定纯排序层；当前实现固定执行 `rerank -> calibration -> deterministic signal scoring -> deterministic fusion -> shortlist`，并直接产出稳定的 `[[SearchScoringResult_t]]`。
4. reranker text conversion 与 text-only contract 已落实；rerank request surface 现已固定为 `instruction / query / documents[*].text`，其中文档文本直接读取 `ScoringCandidate_t.scoring_text`，明确不做 JSON dump，也不把结构化 metadata 序列化进 rerank 面。
5. `[[CareerStabilityProfile]]` 的评分侧接入已落实；跳槽风险现只作为 risk penalty 进入 deterministic fusion，不进入检索层硬过滤，也不绕过 `fit gate` 单独改写 shortlist 事实。

对应 `4.3 交付物`：

1. `SearchExecutionPlan_t -> SearchExecutionResult_t -> SearchScoringResult_t` 已可直接串通；当前实现入口分别为 `materialize_search_execution_plan(...)`、`execute_search_plan(...)` 与 `score_search_results(...)`。
2. reranker request surface 已稳定为 `instruction / query / document-text`；当前实现通过显式注入的 async rerank callable 消费 `RerankRequest` / `RerankResponse`，未引入额外 runtime manager / wrapper / factory。
3. 候选已先进入 `scoring_candidates` 再进入评分；评分层只读取 `SearchExecutionResult_t.scoring_candidates`，不再直接消费 CTS 原始候选或 `raw_payload`。

对应 `4.4 可开工验收`：

1. 已满足。reranker 输入 contract 已固定为 `instruction / query / document-text`。
2. 已满足。`document` 当前明确是候选自然文本面，不是 JSON dump。
3. 已满足。候选必须先进入 `scoring_candidates` 再进入评分；相关对齐行为已由测试覆盖。
4. 已满足。`rerank -> calibration -> deterministic fusion -> shortlist` 已成为唯一主排序链；排序事实不再交给 LLM 或其他隐式分支决定。
5. 已满足。跳槽风险只作为 risk penalty 生效；当前实现不会把 stability 信号提前下推到检索层硬过滤。
6. 已满足。`degree_requirement` canonical 已继续复用 Phase 1 / Phase 2 既有上游归一化口径；评分层只读取 `null / 大专及以上 / 本科及以上 / 硕士及以上 / 博士及以上` 这组稳定值。

对应 `4.5 下一阶段前提`：

1. 已满足。`[[SearchScoringResult_t]]` 现已可稳定产出 shortlist 与 fused score；相关 calibration、risk penalty、fit gate 与排序稳定性已由新增测试覆盖。
2. 已满足。`ExecuteSearchPlan` 与 `ScoreSearchResults` 的边界已不再含糊：前者只负责 CTS 调用和候选投影，后者只负责 rerank、校准、deterministic fusion 与 shortlist 组装。

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
5. `SearchControllerDecisionLLM` 必须遵守统一 `pydantic-ai` 调用约束；它是唯一允许单次业务型 validator retry 的调用点。

### 5.5 下一阶段前提

1. [[SearchControllerDecision_t]]、[[SearchExecutionPlan_t]]、[[FrontierState_t1]] 已能形成闭环。
2. crossover 的合法性和 lineage 已可审计。

### 5.6 当前完成情况（截至当前 HEAD）

- 总体状态：Phase 4 的 operator contract、controller draft 收口与局部 search / direct-stop slice 已完成；这些能力现已被 Phase 5 runtime loop 直接消费。当前应把本阶段理解为“frontier decision 面已落稳，并已作为完整 runtime 的中段组成部分投入使用”。

对应 `5.2 主要工作`：

1. `[[SelectActiveFrontierNode]]` 已落地为稳定纯函数；当前实现固定执行 active node priority scoring、donor candidate packing、generic provenance 下 `domain_company` 禁用、term budget range 冻结与 unmet requirement weight 投影，不再把这些规则散落在 runtime 其他位置。
2. `[[GenerateSearchControllerDecision]]` 已落地为 deterministic normalization 层；当前实现固定把控制器草稿收口为 `search_cts / stop`、operator 白名单回退、non-crossover `additional_terms` 裁剪与 crossover donor whitelist，不允许 LLM 自由改写 `target_frontier_node_id` 或旁路 donor。补充说明：controller validator 当前只校验“归一化后是否仍可物化出合法且非空的 query terms”以及 crossover donor / shared-anchor 合法性，不再额外要求 non-crossover `additional_terms` 在裁剪后仍保持非空。
3. `[[CarryForwardFrontierState]]` 已落地为 identity carry-forward；direct-stop 路径当前会直接把 `FrontierState_t` 原样投影为 `FrontierState_t1`，不新增 child node、不消耗 budget、不写旁路状态。
4. `crossover_compose` 已在 Phase 4 / Phase 3 接缝上落地：controller 侧会限制合法 donor id 与 crossover args，plan materialization 侧继续负责 shared-anchor guard、donor lineage 与 `knowledge_pack_id` provenance 传递，不再有第二套 crossover path。
5. donor legality 与 shared-anchor guard 已落地并由新增测试覆盖；Phase 4 自身冻结了 `SearchExecutionPlan_t.semantic_hash` 与 stable child identity，随后由当前 HEAD 的 Phase 5 `[[UpdateFrontierState]]` 继续把 `semantic_hashes_seen` 推进成真实 run-state。

对应 `5.3 交付物`：

1. 已满足。active-node selection、controller patch、search path、direct-stop path 已能通过 `select_active_frontier_node(...) -> generate_search_controller_decision(...) -> materialize_search_execution_plan(...)` / `carry_forward_frontier_state(...)` 的函数组合与集成测试形成统一 slice；`WorkflowRuntime.run*` 现已在 Phase 5 中接成公开 runtime loop。
2. 已满足。crossover 已有清晰 donor 和 guard 边界；donor legality、shared-anchor、donor lineage 与 `knowledge_pack_id` provenance 都已固定。
3. 已满足。runtime 选点与 controller 局部决策职责已分离；当前 frontier 选点是 deterministic 纯函数，控制器只看到 `SearchControllerContext_t` 局部快照。

对应 `5.4 可开工验收`：

1. 已满足。runtime 先选 active node，控制器只做 branch-level patch；当前测试已覆盖 search path 与 direct-stop path。
2. 已满足。donor 必须满足 `open + reward_breakdown != null + reward 过线 + shared anchor 过线`，且还必须补 active node 未覆盖的 must-have。
3. 已满足。generic provenance 下不会放开 `domain_company`；当前实现继续以 `knowledge_pack_id is null` 作为唯一 generic provenance 判据。
4. 已满足。direct-stop 路径与 search 路径当前都使用统一的 `SearchControllerDecision_t` / `FrontierState_t` / `FrontierState_t1` 对象，不再新开旁路状态；当前 HEAD 中 stop guard 与 finalize 也已由 Phase 5 接通，但这不改变 Phase 4 自身的 owner 边界。
5. 已满足。`SearchControllerDecisionLLM` 当前已按统一 `pydantic-ai` 约束实现：fresh request、`NativeOutput` strict schema、禁用 tools、禁用 cross-operator history、`retries=0`、`output_retries=1`；它也是当前唯一启用单次业务型 validator retry 的调用点，且该 retry 现已收窄到 owner 允许的边界：只处理“最终 query 不可物化/为空”与 crossover donor legality 问题，不再附加更强的草稿字段形状约束。

对应 `5.5 下一阶段前提`：

1. 已满足。`[[SearchControllerDecision_t]]`、`[[SearchExecutionPlan_t]]`、`[[FrontierState_t1]]` 现已可通过函数组合闭合 search / direct-stop 分支，并已在当前 HEAD 中被 Phase 5 runtime loop 直接消费。
2. 已满足。crossover 的 donor 合法性、shared-anchor 与 lineage 当前都可审计；`semantic_hashes_seen` 的 run-state 去重也已在当前 HEAD 的 Phase 5 中完成推进。

补充说明：

- 本轮同时补齐了 `SearchControllerContext_t`、`SearchControllerDecisionDraft_t`、`BranchEvaluation_t`、`NodeRewardBreakdown_t` 的代码侧 typed payload，frontier node 上不再继续使用裸 dict 挂载 branch evaluation / reward。
- 公共 phase 口径现已同步到代码事实：`inspect` / `doctor` / package description 都以 Phase 5 runtime loop 为当前公开状态，Phase 4 不再对外单独作为独立公开阶段暴露。
- 当前新增测试已覆盖 `tests/test_bootstrap_ops.py`、`tests/test_controller_llm.py`、`tests/test_search_ops.py`、`tests/test_runtime_llm.py`、`tests/test_runtime_ops.py`、`tests/test_runtime_orchestrator.py`、`tests/test_cli.py`、`tests/test_cli_packaging.py` 与 `tests/test_api.py`；截至当前 HEAD，全量测试状态以 `./.venv/bin/python -m pytest -q` 的实际结果为准，不在此处硬编码通过数。

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
5. `BranchOutcomeEvaluationLLM` 与 `SearchRunFinalizationLLM` 必须遵守统一 `pydantic-ai` 调用约束，不允许 tools 或 cross-operator history。

### 6.5 下一阶段前提

1. 单次 run 已可从 bootstrap 执行到 `SearchRunResult`。
2. 所有关键 runtime artifacts 已具备回放价值。
3. runtime artifacts 已足以渲染同一 case 的 `Agent Trace` 与 `Business Trace`。

### 6.6 当前完成情况（截至当前 HEAD）

- 总体状态：Phase 5 已完成。`bootstrap -> execution/ranking -> frontier decision -> reward/frontier update/stop/finalize` 已接成单次完整 runtime loop；CLI、Python API、package metadata 与测试均已对齐到公开可运行状态。

对应 `6.2 主要工作`：

1. `[[EvaluateBranchOutcome]]` 已落地为稳定纯函数；当前实现会 clamp `novelty_score / usefulness_score` 到 `[0, 1]`，限制 `repair_operator_hint` 只允许 owner 白名单，并在本轮 shortlist 为空时直接把 `branch_exhausted` 置为 `true`。
2. `[[ComputeNodeRewardBreakdown]]` 已落地为稳定纯函数；当前 reward 公式只读取 `fusion_score` 与 deterministic runtime facts，不再回读旧 base score，也不把 `latency_ms` 混进 reward。
3. `[[UpdateFrontierState]]` 已落地为稳定纯函数；当前实现固定关闭 parent、按 `branch_exhausted` 决定 child open/closed、更新 `run_shortlist_candidate_ids`、`semantic_hashes_seen`、`operator_statistics` 与 `remaining_budget`，缺失 operator statistics key 时直接 fail-fast。
4. `[[EvaluateStopCondition]]` 已落地为稳定纯函数；当前 stop 优先级固定为 `budget_exhausted > no_open_node > exhausted_low_gain > controller_stop`，控制器只提供建议，不具备最终裁决权。
5. `[[FinalizeSearchRun]]` 已落地为稳定纯函数 + LLM summary wrapper 组合；finalizer 只写 `run_summary`，不会改写 final shortlist 顺序或 stop reason。

对应 `6.3 交付物`：

1. 已满足。branch judgment、reward、frontier update、stop、finalize 已形成完整 run 末端闭环。
2. 已满足。run-global shortlist 的排序事实和 stop reason 已可回放；当前 run-shortlist 排序固定为“最佳已观测 `fusion_score` 降序 + first-seen tie-break”。
3. 已满足。最终结果对象 `[[SearchRunResult]]` 已能稳定生成，并已接通到 CLI 与 Python API。

对应 `6.4 可开工验收`：

1. 已满足。reward 明确读 fused score，不读旧 base score。
2. 已满足。run-global shortlist 由最佳已观测 `fusion_score` 决定。
3. 已满足。stop 统一由 runtime guard 裁决，控制器只能建议。
4. 已满足。finalizer 只能写总结，不能改 shortlist 事实。
5. 已满足。`BranchOutcomeEvaluationLLM` 与 `SearchRunFinalizationLLM` 已按统一 `pydantic-ai` 约束实现：fresh request、`NativeOutput` strict schema、禁用 tools、禁用 cross-operator history、`retries=0`、`output_retries=1`。

对应 `6.5 下一阶段前提`：

1. 已满足。单次 run 已可从 bootstrap 执行到 `SearchRunResult`。
2. 已满足到内存态 runtime 事实层面。关键 runtime objects 已具备回放价值，但磁盘 artifact writer 仍留给 Phase 6。
3. 部分满足。runtime objects 已足以支撑后续 `Agent Trace / Business Trace` 设计，但正式双轨 artifact 与索引写盘仍属于 Phase 6 范围。

补充说明：

- 当前公开阶段边界已经很简单：Phase 1-5 均已完成，下一阶段只剩 Phase 6 的 offline eval、artifact 写盘与 trace index。
- Phase 5 的实现仍坚持 `v0.3` 实验项目口径：不引入 compatibility shim、不增加 fallback/retry chain、不预埋 Phase 6 的 artifact writer。

## 7. Phase 6: Offline Eval 与 Trace Artifacts

### 7.1 目标

补齐可回放、可评估的离线闭环，并把 trace 固化为双轨 artifact，但不阻塞前 5 个 runtime 阶段开工。

### 7.2 主要工作

1. 按 [[evaluation]] 准备 offline eval matrix
2. 实现 [[trace-spec]] 与 [[trace-index]]，固化 `Agent Trace / Business Trace` 双轨模板与 `Judge Packet`
3. 补齐 9 个 `case_id` 的 paired trace，不再保留旧 worked trace 体系
4. 固化 policy snapshot、calibration snapshot 与 trace bundle 的审计产物

### 7.3 交付物

1. `Agent Trace`、`Business Trace`、[[trace-index]]、`Judge Packet` 模板已稳定
2. 关键场景可回放，且同一 case 可同时服务 replay/judge 与业务复盘
3. reranker、routing、crossover、reward 的离线评估 artifacts 已可归因
4. LLM call 审计产物至少保留 `output_mode / retries / output_retries / validator_retry_count`，并补充 `model_name / instruction_id_or_hash / message_history_mode / tools_enabled / model_settings_snapshot`

### 7.4 可开工验收

1. `trace-index` 可以作为双轨 trace 的唯一导航入口。
2. 9 个 `case_id` 都同时存在 `Agent Trace` 与 `Business Trace`，且 operator 顺序与 terminal outcome 对齐。
3. `Agent Trace` 可直接服务 replay 与 llm-as-a-judge。
4. `Business Trace` 仍保留每个算子的输入、输出、工具调用与业务含义。
5. eval artifacts 能支撑 reranker、routing、crossover、reward 的分项归因。
6. runtime 只承认 `active.json`、packs、policy、calibration 与 school type registry 这些当前主链资产。

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
