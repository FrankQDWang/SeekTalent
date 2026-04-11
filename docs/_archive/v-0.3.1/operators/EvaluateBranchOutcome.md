# EvaluateBranchOutcome

对当前扩展给出 dual-critic 判断，为 reward 与 stop 提供分支级信号。

## Signature

```text
EvaluateBranchOutcome : (RequirementSheet, FrontierState_t, SearchExecutionPlan_t, SearchExecutionResult_t, SearchScoringResult_t, RuntimeBudgetState) -> BranchEvaluation_t
```

## Prompt Surface

branch evaluator 不再读取 `branch_evaluation_packet_t` 形式的 raw JSON。

输入会先被投影成固定顺序的 prompt surface：

1. `Evaluation Contract`
2. `Role Summary`
3. `Branch Facts`
4. `Search And Scoring Summary`
5. `Runtime Budget State`
6. `Budget Warning`，仅当 `near_budget_end = true`
7. `Return Fields`

这个 prompt surface 会完整落入 `branch_evaluation_audit.prompt_surface`。

## Deterministic Normalization

`BranchOutcomeEvaluationLLM` 先产出 `BranchEvaluationDraft_t`，然后 runtime 再做 deterministic 收口：

- `novelty_score` clamp 到 `[0, 1]`
- `usefulness_score` clamp 到 `[0, 1]`
- `branch_exhausted` 在当前轮 shortlist 为空时会被强制拉成 `true`
- `repair_operator_hint` 只允许落在 runtime whitelist 内
- `evaluation_notes` 会被规范化成稳定文本

## Read Set

- `RequirementSheet.role_title`
- `RequirementSheet.role_summary`
- `RequirementSheet.must_have_capabilities`
- `RequirementSheet.preferred_capabilities`
- `FrontierState_t.frontier_nodes`
- `SearchExecutionPlan_t.query_terms`
- `SearchExecutionPlan_t.semantic_hash`
- `SearchExecutionPlan_t.knowledge_pack_ids`
- `SearchExecutionPlan_t.child_frontier_node_stub`
- `SearchExecutionResult_t.search_page_statistics`
- `SearchScoringResult_t.node_shortlist_candidate_ids`
- `SearchScoringResult_t.top_three_statistics`
- `RuntimeBudgetState`

## Write Set

- `BranchEvaluation_t.novelty_score`
- `BranchEvaluation_t.usefulness_score`
- `BranchEvaluation_t.branch_exhausted`
- `BranchEvaluation_t.repair_operator_hint`
- `BranchEvaluation_t.evaluation_notes`

## 不确定性边界

- 唯一黑盒是 `BranchOutcomeEvaluationLLM`；它只能返回 `BranchEvaluationDraft_t`
- provider-native strict structured output 固定为 `retries=0`、`output_retries=1`
- evaluator 只判断 branch 价值，不直接改 frontier，也不直接持有 stop owner
- 预算信号只影响尾段评论口径，不改写 runtime fact 集合

## 相关

- [[BranchEvaluationDraft_t]]
- [[BranchEvaluation_t]]
- [[RuntimeBudgetState]]
- [[PromptSurfaceSnapshot]]
