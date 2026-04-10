# SeekTalent v0.3.1 LLM Context Surfaces

> 本页描述 5 个 LLM 调用点真正看到的 prompt surface，而不是内部 typed payload 全量结构。

## 总结论

`v0.3.1` 现在统一使用：

- markdown prompt 文件作为 `instructions_text`
- sectioned text 作为 `input_text`
- `PromptSurfaceSnapshot` 作为唯一 prompt 审计 owner

不再允许：

- raw JSON payload 直接喂 LLM
- `sort_keys=True` 按字母序排 prompt
- 只保留 instruction hash 的 hash-only audit

## 当前 5 个调用点

| 调用点 | 真正看到的 surface | 明确看不到的内容 |
| --- | --- | --- |
| `RequirementExtractionLLM` | `Task Contract / Job Description / Hiring Notes / Return Fields` | routing、frontier、候选、评分 |
| `BootstrapKeywordGenerationLLM` | `Task Contract / Requirement Summary / Routing Result / Selected Knowledge Packs / Return Fields` | 后续轮次状态、候选、CTS 结果、reward |
| `SearchControllerDecisionLLM` | `Task Contract / Role Summary / Active Frontier Node / Donor Candidates / Allowed Operators / Operator Statistics / Fit Gates And Unmet Requirements / Runtime Budget State / Budget Warning? / Decision Request` | 整份 frontier、原始候选文本、CTS payload |
| `BranchOutcomeEvaluationLLM` | `Evaluation Contract / Role Summary / Branch Facts / Search And Scoring Summary / Runtime Budget State / Budget Warning? / Return Fields` | 全量运行历史、未来轮次状态、stop owner |
| `SearchRunFinalizationLLM` | `Task Contract / Role Summary / Final Shortlist State / Stop Reason / Return Fields` | 排序改写权、CTS 原始观测 |

`Budget Warning` 只在 `near_budget_end=true` 时出现。

`search_phase` 与 `phase_progress` 当前只是 runtime 事实：

- 由 `RuntimeBudgetState` 统一计算
- 自动进入 context、prompt surface、bundle trace
- Step 2 不让它们直接改 selection / operator / stop 策略

## 审计形态

每次 LLM 调用都会在 bundle 中保存完整 `PromptSurfaceSnapshot`：

- `surface_id`
- `instructions_text`
- `input_text`
- `instructions_sha1`
- `input_sha1`
- `sections[*]`

每个 section 都会保存：

- `title`
- `body_text`
- `source_paths`
- `is_dynamic`

所以 `bundle.json` 单独就能回答：

1. 模型看到了什么
2. 这些内容按什么 section 排列
3. 每个 section 从哪些 typed 字段抽出来

## 统一执行约束

所有 5 个调用点都遵守：

- `fresh request`
- `NativeOutput(strict=True)`
- `retries=0`
- `output_retries=1`
- `allow_text_output = false`
- `allow_image_output = false`
- no tools
- no cross-operator history

## 相关

- [[workflow-explained]]
- [[GenerateSearchControllerDecision]]
- [[EvaluateBranchOutcome]]
- [[PromptSurfaceSnapshot]]
- [[LLMCallAudit]]
