# SeekTalent v0.3.1 LLM Context Surfaces

> 本页只总结当前 5 个 LLM 调用点真正能看到什么。

## 1. 总结论

`v0.3.1` 现在的 LLM surface 比旧方案更窄：

- requirement 抽取只看原始输入
- bootstrap 关键词生成只看 `RequirementSheet + routing_result + selected knowledge pack`
- controller 只看局部 frontier context
- branch evaluator 只看单轮 branch packet
- finalizer 只看最终 shortlist 事实

排序、路由、reward、stop 都不是生成式 LLM owner。

## 2. 当前调用点

| 调用点 | 真正看到的 context | 明确看不到的内容 |
| --- | --- | --- |
| `RequirementExtractionLLM` | `SearchInputTruth` | routing、frontier、候选、评分 |
| `BootstrapKeywordGenerationLLM` | `RequirementSheet + BootstrapRoutingResult + selected DomainKnowledgePack \| null` | 后续轮次状态、候选、排序偏好、CTS 细节 |
| `SearchControllerDecisionLLM` | `SearchControllerContext_t` | 整份 frontier、原始候选文本、CTS payload |
| `BranchOutcomeEvaluationLLM` | 单轮 branch packet | 全量运行历史、未来轮次状态 |
| `SearchRunFinalizationLLM` | `RequirementSheet + FrontierState_t1 + stop_reason` | 排序改写权、CTS 原始观测 |

## 3. 关键边界

### 3.1 bootstrap 关键词生成

它现在不是“让模型自由拼 query”，而是：

- 只在 round-0 使用
- 只基于选中的单个 knowledge pack 或 generic fallback
- 产出 `BootstrapKeywordDraft`
- 再由 runtime deterministic 地映射成 seeds

### 3.2 reranker 不是 LLM surface

当前 reranker 只消费：

- `instruction`
- `query`
- `document-text`

它不是结构化 JSON scorer，也不负责生成关键词。

### 3.3 当前不再存在的旧 surface

以下旧调用面已经退出当前主链：

- `RetrieveGroundingKnowledge`
- `GroundingGenerationLLM` 读取 `KnowledgeRetrievalResult`
- `GroundingDraft`
- `GroundingOutput`

## 4. 统一执行约束

所有 5 个调用点都遵守：

- `fresh request`
- `NativeOutput(strict=True)`
- `retries=0`
- `output_retries=1`
- `allow_text_output = false`
- `allow_image_output = false`
- no tools
- no cross-operator history

## 5. 推荐阅读

- [[design]]
- [[workflow-explained]]
- [[operator-map]]
- [[ExtractRequirements]]
- [[RouteDomainKnowledgePack]]
- [[GenerateBootstrapOutput]]
- [[GenerateSearchControllerDecision]]
- [[EvaluateBranchOutcome]]
- [[FinalizeSearchRun]]
