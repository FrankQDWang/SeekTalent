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
| `SearchControllerDecisionLLM` | `Task Contract / Role Summary / Active Frontier Node / Donor Candidates / Allowed Operators / Rewrite Evidence / Operator Statistics / Fit Gates And Unmet Requirements / Runtime Budget State / Budget Warning? / Decision Request` | 整份 frontier、原始候选文本、CTS payload |
| `BranchOutcomeEvaluationLLM` | `Evaluation Contract / Role Summary / Branch Facts / Search And Scoring Summary / Runtime Budget State / Budget Warning? / Return Fields` | 全量运行历史、未来轮次状态、stop owner |
| `SearchRunFinalizationLLM` | `Task Contract / Role Summary / Run Facts / Final Shortlist State / Stop Reason / Return Fields` | 候选全文、CTS 原始观测、候选级解释写权 |

`Budget Warning` 只在 `near_budget_end=true` 时出现。

`Allowed Operators` section 现在固定包含：

- 最终 `allowed_operator_names`
- `operator_surface_override_reason`
- `operator_surface_unmet_must_haves`

`Rewrite Evidence` section 会显式列出 `rewrite_term_candidates`。这些 evidence terms 只作为 rewrite operator 的词源池，不会直接 append 到 CTS `keyword`。
`rewrite_term_candidates` 本身已经在 trace 中稳定落盘，对应 [[RewriteTermCandidate]] / [[RewriteTermPool]]。
controller prompt 不会看到原始 `accepted_term_score`，只会看到：

- `term`
- `support_count`
- `source_fields`
- 紧凑 `signal`

这样可以把 evidence 质量 cue 暴露给 controller，但不把完整 score math 塞进 prompt。

controller 的 runtime budget section 也不再使用 range 语义，而是会明确投影：

- `CTS keyword terms are conjunctive. More terms tighten the search.`
- `Max query terms: N`

也就是说，controller 看到的不是静态 operator catalog，而是已经过 phase-aware action surface 收口后的结果；看到的也不是“可随意扩张的 recall budget”，而是 CTS 交集语义下冻结好的 query 上限。

round-0 bootstrap 也不再保留独立 4-term 心智；seed cap 直接复用 `explore_max_query_terms`。

`search_phase` 与 `phase_progress` 当前只是 runtime 事实：

- 由 `RuntimeBudgetState` 统一计算
- 自动进入 context、prompt surface、bundle trace
- Step 2 只建立 phase 事实
- Step 3 消费 phase 改写 active node selection
- Step 4 消费 phase 改写 allowed operator surface

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

对于 rewrite 类 non-crossover round，bundle 还会额外保存 `rewrite_choice_trace`，用来解释为什么某个合法 rewrite 候选最终胜出。

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
