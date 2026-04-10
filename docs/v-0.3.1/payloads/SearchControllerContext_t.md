# SearchControllerContext_t

送入控制器的当前分支上下文快照。

```text
SearchControllerContext_t = { active_frontier_node_summary, donor_candidate_node_summaries, frontier_head_summary, unmet_requirement_weights, operator_statistics_summary, allowed_operator_names, term_budget_range, fit_gate_constraints }
```

## 稳定字段组

- active node 摘要：`active_frontier_node_summary`
- donor 候选摘要：`donor_candidate_node_summaries`
- frontier 头部摘要：`frontier_head_summary`
- 未满足需求权重：`unmet_requirement_weights`
- operator 统计摘要：`operator_statistics_summary`
- 当前轮允许的 operator：`allowed_operator_names`
- term 预算范围：`term_budget_range`
- fit gate 约束：`fit_gate_constraints: FitGateConstraints`

## Direct Producer / Direct Consumer Operator

- Direct producer：[[SelectActiveFrontierNode]]
- Direct consumers：[[GenerateSearchControllerDecision]]

补充说明：

- `SearchControllerDecisionLLM` 是 [[GenerateSearchControllerDecision]] 内部的 draft layer。
- payload 文档按 operator 粒度记录 direct consumer，不单独把中间 LLM 节点记成 payload consumer。

## Invariants

- `SearchControllerContext_t` 是只读快照，不是可回写状态。
- 它只暴露控制器真正需要的字段，不暴露整份 frontier。
- `donor_candidate_node_summaries` 只提供合法 donor 候选，不等于已选 donor。
- `allowed_operator_names` 必须是 [[OperatorCatalog]] 的子集；当 active node 没有领域 provenance 时不得包含 `pack_expansion / cross_pack_bridge`。
- `term_budget_range` 必须来自 [[RuntimeTermBudgetPolicy]]。
- `unmet_requirement_weights` 必须是保序的 `list[{capability, weight}]`，不允许用 map 形状替代。

## 最小示例

```yaml
active_frontier_node_summary:
  frontier_node_id: "seed_agent_core"
  selected_operator_name: "must_have_alias"
  node_query_term_pool: ["agent engineer", "rag", "python"]
  node_shortlist_candidate_ids: ["c32", "c44"]
donor_candidate_node_summaries:
  - frontier_node_id: "child_search_domain_01"
    shared_anchor_terms: ["rag"]
    expected_incremental_coverage:
      - "retrieval or ranking experience"
    reward_score: 3.9
frontier_head_summary:
  open_node_count: 3
  remaining_budget: 4
  highest_priority_score: 4.12
unmet_requirement_weights:
  - capability: "retrieval_or_ranking_experience"
    weight: 1.0
operator_statistics_summary:
  must_have_alias:
    average_reward: 3.8
    times_selected: 1
  core_precision:
    average_reward: 2.9
    times_selected: 2
  crossover_compose:
    average_reward: 0.0
    times_selected: 0
allowed_operator_names:
  - "core_precision"
  - "must_have_alias"
  - "relaxed_floor"
  - "generic_expansion"
  - "crossover_compose"
term_budget_range: [2, 6]
fit_gate_constraints:
  locations: ["Shanghai"]
  min_years: 6
  max_years: 10
  company_names: ["阿里巴巴", "蚂蚁集团"]
  school_names: ["复旦大学"]
  degree_requirement: "本科及以上"
  gender_requirement: null
  min_age: null
  max_age: 35
```

## 相关

- [[FrontierState_t]]
- [[FitGateConstraints]]
- [[OperatorCatalog]]
- [[SelectActiveFrontierNode]]
