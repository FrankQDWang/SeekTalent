# GenerateSearchControllerDecision

在 active node 语境中生成 operator-centered 决策。

## Signature

```text
GenerateSearchControllerDecision : SearchControllerContext_t -> SearchControllerDecision_t
```

## Prompt Surface

控制器不再读取 raw JSON payload。

`SearchControllerContext_t` 会先被投影成固定顺序的 prompt surface：

1. `Task Contract`
2. `Role Summary`
3. `Active Frontier Node`
4. `Donor Candidates`
5. `Allowed Operators`
6. `Rewrite Evidence`
7. `Operator Statistics`
8. `Fit Gates And Unmet Requirements`
9. `Runtime Budget State`
10. `Budget Warning`，仅当 `runtime_budget_state.near_budget_end = true`
11. `Decision Request`

这个 prompt surface 会完整落入 `controller_audit.prompt_surface`。

其中 `Allowed Operators` section 会显式投影三行：

- `Allowed operators: ...`
- `Operator surface override: ...`
- `Operator surface unmet must-haves: ...`

`Rewrite Evidence` section 会显式列出 `rewrite_term_candidates`；它是 rewrite operator 的词源池，不会直接下推 CTS query。
这些 term 直接来自 `RewriteTermPool.accepted`，现在已经是稳定 trace owner，不再是隐含 sidecar。
controller prompt 只会投影紧凑 cue：

- `term`
- `support_count`
- `source_fields`
- `signal`

其中 `signal` 只暴露粗粒度 evidence 质量标签：

- `must_have`
- `anchor`
- `pack`
- `title_project`
- `mixed`

如果 term 带有 generic 惩罚，还会追加 `+generic_penalty`。

这里的 override 只是解释当前 phase-aware action surface 为什么被临时放宽；它不是第二套 selection policy。

## Deterministic Normalization

控制器 LLM 先产出 `SearchControllerDecisionDraft_t`，然后 runtime 再做 deterministic 收口：

- `action` 只允许 `search_cts / stop`
- `selected_operator_name` 只能来自 `allowed_operator_names`
- 非 `crossover_compose` 只接受最终 `query_terms`
- `query_terms` 会保序去重并按 `max_query_terms` 裁剪
- `core_precision / relaxed_floor / must_have_alias / generic_expansion / pack_expansion / cross_pack_bridge` 都按 query rewrite contract 校验，不允许退回追加词语义
- rewrite 类 operator 在 contract 校验通过后，允许基于 `rewrite_term_candidates` 做 bounded local search；它只能改最终 `query_terms`，不能改 operator 选择
- bounded local search 的胜出结果会同步落入 `SearchRoundArtifact.rewrite_choice_trace`
- `crossover_compose` 的 donor 只能来自合法 donor candidate 列表
- `target_frontier_node_id` 固定绑定 active node，不接受 LLM 改写

## Read Set

- `SearchControllerContext_t.role_title`
- `SearchControllerContext_t.role_summary`
- `SearchControllerContext_t.active_frontier_node_summary`
- `SearchControllerContext_t.donor_candidate_node_summaries`
- `SearchControllerContext_t.frontier_head_summary`
- `SearchControllerContext_t.unmet_requirement_weights`
- `SearchControllerContext_t.operator_statistics_summary`
- `SearchControllerContext_t.allowed_operator_names`
- `SearchControllerContext_t.operator_surface_override_reason`
- `SearchControllerContext_t.operator_surface_unmet_must_haves`
- `SearchControllerContext_t.rewrite_term_candidates`
- `SearchControllerContext_t.max_query_terms`
- `SearchControllerContext_t.fit_gate_constraints`
- `SearchControllerContext_t.runtime_budget_state`

## Write Set

- `SearchControllerDecision_t.action`
- `SearchControllerDecision_t.target_frontier_node_id`
- `SearchControllerDecision_t.selected_operator_name`
- `SearchControllerDecision_t.operator_args`
- `SearchControllerDecision_t.expected_gain_hypothesis`

## 不确定性边界

- 唯一黑盒是 `SearchControllerDecisionLLM`；它只能返回 `SearchControllerDecisionDraft_t`
- provider-native strict structured output 固定为 `retries=0`、`output_retries=1`
- 仅允许单次业务型 validator retry，且只用于“能物化合法 query terms / crossover args”
- prompt 审计唯一 owner 是 `controller_audit.prompt_surface`

## 相关

- [[SearchControllerContext_t]]
- [[SearchControllerDecisionDraft_t]]
- [[SearchControllerDecision_t]]
- [[PromptSurfaceSnapshot]]
- [[RewriteTermCandidate]]
