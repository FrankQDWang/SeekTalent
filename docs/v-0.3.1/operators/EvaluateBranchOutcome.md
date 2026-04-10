# EvaluateBranchOutcome

对当前扩展给出 dual-critic 判断，为 reward 与 stop 提供分支级信号。

## Signature

```text
EvaluateBranchOutcome : (RequirementSheet, FrontierState_t, SearchExecutionPlan_t, SearchExecutionResult_t, SearchScoringResult_t) -> BranchEvaluation_t
```

## Notation Legend

```text
R := RequirementSheet
F_t := FrontierState_t
p_t := SearchExecutionPlan_t
x_t := SearchExecutionResult_t
y_t := SearchScoringResult_t
draft_branch_evaluation_t := BranchEvaluationDraft_t
a_t := BranchEvaluation_t
```

## Input Projection

```text
parent_node_t = F_t.frontier_nodes[p_t.child_frontier_node_stub.parent_frontier_node_id]
allowed_repair_operator_names_t =
  ["core_precision", "must_have_alias", "relaxed_floor", "generic_expansion", "crossover_compose"]
  if |p_t.knowledge_pack_ids| = 0
  else
    ["core_precision", "must_have_alias", "relaxed_floor", "generic_expansion", "pack_expansion", "cross_pack_bridge", "crossover_compose"]
```

## Primitive Predicates / Matching Rules

```text
normalized_text(text) = trim(compress_whitespace(text))
```

```text
whitelisted_repair_hint_t(hint_t) =
  hint_t if hint_t in allowed_repair_operator_names_t
  else null
```

## Transformation

### Phase 1 — Prompt Packing

```text
branch_evaluation_packet_t = {
  must_have_capabilities: R.must_have_capabilities,
  parent_frontier_node_id: parent_node_t.frontier_node_id,
  previous_node_shortlist_candidate_ids: parent_node_t.node_shortlist_candidate_ids,
  donor_frontier_node_id: p_t.child_frontier_node_stub.donor_frontier_node_id,
  knowledge_pack_ids: p_t.knowledge_pack_ids,
  query_terms: p_t.query_terms,
  semantic_hash: p_t.semantic_hash,
  search_page_statistics: x_t.search_page_statistics,
  node_shortlist_candidate_ids: y_t.node_shortlist_candidate_ids,
  top_three_statistics: y_t.top_three_statistics
}
```

### Phase 2 — LLM Draft

```text
draft_branch_evaluation_t =
  BranchOutcomeEvaluationLLM(branch_evaluation_packet_t)
```

### Phase 3 — Deterministic Normalization

```text
normalized_novelty_t =
  min(1.0, max(0.0, draft_branch_evaluation_t.novelty_score))

normalized_usefulness_t =
  min(1.0, max(0.0, draft_branch_evaluation_t.usefulness_score))

normalized_branch_exhausted_t =
  draft_branch_evaluation_t.branch_exhausted
  or (|y_t.node_shortlist_candidate_ids| = 0)

normalized_repair_operator_hint_t =
  whitelisted_repair_hint_t(draft_branch_evaluation_t.repair_operator_hint)

normalized_evaluation_notes_t =
  normalized_text(draft_branch_evaluation_t.evaluation_notes)
```

### Field-Level Output Assembly

```text
a_t.novelty_score = normalized_novelty_t
a_t.usefulness_score = normalized_usefulness_t
a_t.branch_exhausted = normalized_branch_exhausted_t
a_t.repair_operator_hint = normalized_repair_operator_hint_t
a_t.evaluation_notes = normalized_evaluation_notes_t
```

## Defaults / Thresholds Used Here

```text
branch_exhausted is forced to true
when the current round produces zero fit shortlist candidates,
even if the LLM draft does not request exhaustion.
```

## Read Set

- `RequirementSheet.must_have_capabilities`
- `FrontierState_t.frontier_nodes`
- `SearchExecutionPlan_t.query_terms`
- `SearchExecutionPlan_t.semantic_hash`
- `SearchExecutionPlan_t.knowledge_pack_ids`
- `SearchExecutionPlan_t.child_frontier_node_stub`
- `SearchExecutionResult_t.search_page_statistics`
- `SearchScoringResult_t.node_shortlist_candidate_ids`
- `SearchScoringResult_t.top_three_statistics`

## Write Set

- `BranchEvaluation_t.novelty_score`
- `BranchEvaluation_t.usefulness_score`
- `BranchEvaluation_t.branch_exhausted`
- `BranchEvaluation_t.repair_operator_hint`
- `BranchEvaluation_t.evaluation_notes`

## 输入 payload

- [[RequirementSheet]]
- [[FrontierState_t]]
- [[SearchExecutionPlan_t]]
- [[SearchExecutionResult_t]]
- [[SearchScoringResult_t]]

## 输出 payload

- [[BranchEvaluation_t]]

## 不确定性边界 / 说明

- 唯一黑盒是 `BranchOutcomeEvaluationLLM(branch_evaluation_packet_t)`；它必须先产出 [[BranchEvaluationDraft_t]]，再经过 clamp / whitelist / normalize 收口进入 `BranchEvaluation_t`。
- `BranchOutcomeEvaluationLLM` 必须使用 provider-native strict structured output，固定 `retries=0`、`output_retries=1`。
- 默认不额外要求 `output_validator`；若未来引入，只允许补充 schema 无法表达且不会改写 runtime fact 的 branch-level business 约束。
- 它判断 branch 价值，但不直接修改 frontier，也不直接决定 stop。
- `pack_expansion / cross_pack_bridge` 是否允许修复，只取决于 `knowledge_pack_ids` 是否非空。

## 相关

- [[operator-spec-style]]
- [[RequirementSheet]]
- [[FrontierState_t]]
- [[SearchExecutionPlan_t]]
- [[SearchExecutionResult_t]]
- [[SearchScoringResult_t]]
- [[BranchEvaluationDraft_t]]
- [[BranchEvaluation_t]]
