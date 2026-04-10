# GenerateSearchControllerDecision

在 active node 语境中生成 operator-centered 决策。

## Signature

```text
GenerateSearchControllerDecision : SearchControllerContext_t -> SearchControllerDecision_t
```

## Notation Legend

```text
d_t := SearchControllerDecision_t
draft_decision_t := SearchControllerDecisionDraft_t
SCC := SearchControllerContext_t
active_node_t := SCC.active_frontier_node_summary
```

## Input Projection

```text
active_node_id_t = active_node_t.frontier_node_id
active_operator_name_t = active_node_t.selected_operator_name
active_query_pool_t = active_node_t.node_query_term_pool
allowed_operator_names_t = SCC.allowed_operator_names
term_budget_range_t = SCC.term_budget_range
donor_candidate_ids_t =
  [
    donor_summary_t.frontier_node_id
    for donor_summary_t in SCC.donor_candidate_node_summaries
  ]
```

## Primitive Predicates / Matching Rules

```text
normalized_text(text) = trim(compress_whitespace(text))
```

## Transformation

### Phase 1 — Prompt Packing

```text
controller_prompt_t = {
  active_frontier_node_summary: SCC.active_frontier_node_summary,
  donor_candidate_node_summaries: SCC.donor_candidate_node_summaries,
  frontier_head_summary: SCC.frontier_head_summary,
  unmet_requirement_weights: SCC.unmet_requirement_weights,
  operator_statistics_summary: SCC.operator_statistics_summary,
  allowed_operator_names: allowed_operator_names_t,
  term_budget_range: term_budget_range_t,
  fit_gate_constraints: SCC.fit_gate_constraints
}
```

### Phase 2 — LLM Draft

```text
draft_decision_t = SearchControllerDecisionLLM(controller_prompt_t)
```

### Phase 3 — Deterministic Normalization

```text
normalized_action_t =
  "stop" if draft_decision_t.action = "stop"
  else "search_cts"
```

```text
requested_operator_name_t = draft_decision_t.selected_operator_name

normalized_operator_name_t =
  whitelist(
    requested_operator_name_t,
    allowed_operator_names_t,
    fallback = active_operator_name_t
  )
```

```text
normalized_operator_args_t =
  if normalized_action_t = "stop"
  then {}
  else if normalized_operator_name_t != "crossover_compose"
  then
    requested_additional_terms_t =
      deduplicate(drop_empty(draft_decision_t.operator_args.additional_terms))
    max_additional_terms_t =
      max(0, term_budget_range_t[1] - |active_query_pool_t|)
    {
      additional_terms: requested_additional_terms_t[0 : max_additional_terms_t]
    }
  else {
    donor_frontier_node_id:
      whitelist_or_null(
        draft_decision_t.operator_args.donor_frontier_node_id,
        donor_candidate_ids_t
      ),
    crossover_rationale:
      normalized_text(draft_decision_t.operator_args.crossover_rationale),
    shared_anchor_terms:
      deduplicate(drop_empty(draft_decision_t.operator_args.shared_anchor_terms)),
    donor_terms_used:
      deduplicate(drop_empty(draft_decision_t.operator_args.donor_terms_used))
  }
```

### Field-Level Output Assembly

```text
d_t.action = normalized_action_t
d_t.target_frontier_node_id = active_node_id_t
d_t.selected_operator_name = normalized_operator_name_t
d_t.operator_args = normalized_operator_args_t
d_t.expected_gain_hypothesis =
  normalized_text(draft_decision_t.expected_gain_hypothesis)
```

## Defaults / Thresholds Used Here

```text
normalized_action_t defaults to "search_cts"
unless the LLM draft explicitly requests "stop"
```

```text
normalized_operator_name_t defaults to active_operator_name_t
when draft_decision_t.selected_operator_name falls outside allowed_operator_names_t
```

```text
term_budget_range_t is frozen upstream by SelectActiveFrontierNode.
If runtime defaults are used, RuntimeTermBudgetPolicy provides:
  high_budget_range   = [2, 6]
  medium_budget_range = [2, 5]
  low_budget_range    = [2, 4]
```

```text
max_additional_terms_t =
  max(0, term_budget_range_t[1] - |active_query_pool_t|)
```

## Read Set

- `SearchControllerContext_t.active_frontier_node_summary`
- `SearchControllerContext_t.donor_candidate_node_summaries`
- `SearchControllerContext_t.frontier_head_summary`
- `SearchControllerContext_t.unmet_requirement_weights`
- `SearchControllerContext_t.operator_statistics_summary`
- `SearchControllerContext_t.allowed_operator_names`
- `SearchControllerContext_t.term_budget_range`
- `SearchControllerContext_t.fit_gate_constraints`

## Write Set

- `SearchControllerDecision_t.action`
- `SearchControllerDecision_t.target_frontier_node_id`
- `SearchControllerDecision_t.selected_operator_name`
- `SearchControllerDecision_t.operator_args`
- `SearchControllerDecision_t.expected_gain_hypothesis`

## 输入 payload

- [[SearchControllerContext_t]]

## 输出 payload

- [[SearchControllerDecision_t]]

## 不确定性边界 / 说明

- 唯一黑盒是 `SearchControllerDecisionLLM(controller_prompt_t)`；它必须先产出 [[SearchControllerDecisionDraft_t]]，再经过 whitelist / clamp / normalize 收口进入 `SearchControllerDecision_t`。
- `SearchControllerDecisionLLM` 必须使用 provider-native strict structured output，固定 `retries=0`、`output_retries=1`。
- 只有 schema 之外的真实业务约束才允许 bounded `output_validator + ModelRetry`；当前允许的补充校验边界仅包括：`search_cts` 时 operator args 必须足以物化非空 query terms、terms 必须可被 runtime canonicalize。
- `controller_prompt_t` 是控制器真正看到的 prompt payload；它不再读取整份 frontier。
- `target_frontier_node_id` 不接受 LLM 自由改写，固定绑定当前 active node。
- `donor_frontier_node_id` 只能来自 runtime 打包的 donor candidate 列表。
- generic provenance 下即使 LLM 提议 `pack_expansion / cross_pack_bridge`，也会因 `allowed_operator_names_t` 白名单而被 runtime 回退。

## 相关

- [[SearchControllerContext_t]]
- [[SearchControllerDecisionDraft_t]]
- [[SearchControllerDecision_t]]
- [[OperatorCatalog]]
- [[RuntimeTermBudgetPolicy]]
- [[weights-and-thresholds-index]]
- [[operator-spec-style]]
