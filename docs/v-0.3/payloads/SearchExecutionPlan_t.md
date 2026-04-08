# SearchExecutionPlan_t

`MaterializeSearchExecutionPlan` 物化出的可执行检索计划。

```text
SearchExecutionPlan_t = {
  query_terms,
  projected_filters,
  runtime_only_constraints,
  target_new_candidate_count,
  semantic_hash,
  source_card_ids,
  child_frontier_node_stub,
  derived_position,
  derived_work_content
}
```

## 稳定字段组

- 检索词：`query_terms`
- 可下推过滤：`projected_filters: HardConstraints`
- 仅 runtime 使用的约束：`runtime_only_constraints: RuntimeOnlyConstraints`
- 目标新增候选数：`target_new_candidate_count`
- 语义哈希：`semantic_hash`
- 来源知识卡：`source_card_ids`
- child frontier node 草稿：`child_frontier_node_stub: ChildFrontierNodeStub`
- derived 职位信号：`derived_position`
- derived 工作内容信号：`derived_work_content`

## Direct Producer / Direct Consumers

- Direct producer：[[MaterializeSearchExecutionPlan]]
- Direct consumers：[[ExecuteSearchPlan]]、[[EvaluateBranchOutcome]]、[[ComputeNodeRewardBreakdown]]、[[UpdateFrontierState]]

## Invariants

- `semantic_hash` 一旦生成不可改。
- `target_new_candidate_count` 在这里冻结，后续搜索不再读取游离外部变量。
- `runtime_only_constraints` 不要求 CTS 原生支持。
- `projected_filters` 是业务层稳定约束；真实 CTS 请求字段由 [[cts-projection-policy]] 决定。
- `child_frontier_node_stub.donor_frontier_node_id` 仅在 `crossover_compose` 下非空。
- `derived_position` / `derived_work_content` 一旦写入 plan，就成为 CTS adapter 的唯一读取入口；执行层不得再回头读取 `RequirementSheet`。

## 最小示例

```yaml
query_terms: ["rag", "retrieval engineer", "ranking"]
projected_filters:
  locations: ["Shanghai"]
  min_years: 6
  max_years: 10
  company_names: ["阿里巴巴", "蚂蚁集团"]
  school_names: ["复旦大学"]
  degree_requirement: "本科及以上"
  school_type_requirement: ["985", "211"]
  gender_requirement: null
  min_age: null
  max_age: 35
runtime_only_constraints:
  must_have_keywords: ["Python backend", "LLM application", "retrieval or ranking experience"]
  negative_keywords: ["data analyst", "pure algorithm research"]
target_new_candidate_count: 10
semantic_hash: "hash_crossover_03"
source_card_ids:
  - "role_alias.llm_agent_rag_engineering.backend_agent_engineer"
  - "role_alias.search_ranking_retrieval_engineering.retrieval_engineer"
child_frontier_node_stub:
  frontier_node_id: "child_crossover_03"
  parent_frontier_node_id: "seed_agent_core"
  donor_frontier_node_id: "child_search_domain_01"
  selected_operator_name: "crossover_compose"
derived_position: "Senior Python Engineer"
derived_work_content: "Python backend | LLM application | retrieval or ranking experience"
```

## 相关

- [[HardConstraints]]
- [[RuntimeOnlyConstraints]]
- [[ChildFrontierNodeStub]]
- [[MaterializeSearchExecutionPlan]]
- [[cts-projection-policy]]
