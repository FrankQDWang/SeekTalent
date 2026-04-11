# SeekTalent v0.3.1 核心流程详解

## 主链

`SearchInputTruth -> RequirementSheet -> BootstrapRoutingResult -> BootstrapKeywordDraft -> BootstrapOutput -> FrontierState -> runtime loop -> SearchRunResult`

## Bootstrap

1. 抽取并归一化 `RequirementSheet`
2. rerank active packs，得到 `explicit_pack / inferred_single_pack / inferred_multi_pack / generic_fallback`
3. 把 `RequirementSheet + selected_knowledge_packs` 交给 bootstrap LLM，生成 `5-8` 条 candidate seeds
4. runtime 做 deterministic prune，得到 `4/5` 条 final seeds

## Runtime

1. 选 active frontier node
2. controller 生成 operator patch
3. 物化 `SearchExecutionPlan_t`
4. CTS 搜索 + rerank
5. branch evaluation + reward
6. 更新 frontier，直到 stop
