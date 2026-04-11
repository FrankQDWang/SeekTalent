# FrontierState_t

当前一次扩展开始前的 frontier 运行态。

```text
FrontierState_t = {
  frontier_nodes: map[frontier_node_id -> FrontierNode_t],
  open_frontier_node_ids,
  closed_frontier_node_ids,
  run_term_catalog,
  run_shortlist_candidate_ids,
  semantic_hashes_seen,
  operator_statistics,
  remaining_budget
}
```

## 稳定字段组

- 节点表：`frontier_nodes (map[frontier_node_id -> FrontierNode_t])`
- 待扩展节点 id：`open_frontier_node_ids`
- 已关闭节点 id：`closed_frontier_node_ids`
- run 级词表：`run_term_catalog`
- run 级 shortlist：`run_shortlist_candidate_ids`
- 语义哈希去重表：`semantic_hashes_seen`
- operator 统计：`operator_statistics`
- 剩余预算：`remaining_budget`

## Direct Producer / Direct Consumers

- Direct producer：[[InitializeFrontierState]]、previous round [[FrontierState_t1]] after runtime round shift
- Direct consumers：[[SelectActiveFrontierNode]]、[[CarryForwardFrontierState]]、[[MaterializeSearchExecutionPlan]]、[[EvaluateBranchOutcome]]、[[ComputeNodeRewardBreakdown]]、[[UpdateFrontierState]]

## Invariants

- `run_shortlist_candidate_ids` 与节点局部 shortlist 不同义。
- `run_shortlist_candidate_ids` 的顺序是 run 内最佳已观测 `fusion_score` 的稳定排序事实。
- `semantic_hashes_seen` 只增不减。
- 只有 runtime 路径能消费 `remaining_budget`。
- frontier node 必须持有足够的 provenance，以支持 `knowledge_pack_id` 与可选 donor lineage 回溯。
- `operator_statistics` 的 value shape 由 [[OperatorStatistics]] 唯一持有。
- `FrontierState_t(next round) := FrontierState_t1(previous round)` after runtime round shift。

## 最小示例

```yaml
frontier_nodes:
  child_agent_core_01:
    frontier_node_id: "child_agent_core_01"
    parent_frontier_node_id: "seed_agent_core"
    donor_frontier_node_id: null
    selected_operator_name: "must_have_alias"
    node_query_term_pool: ["agent engineer", "rag", "python"]
    knowledge_pack_id: "llm_agent_rag_engineering"
    seed_rationale: null
    negative_terms: ["data analyst"]
    parent_shortlist_candidate_ids: ["c17", "c32"]
    node_shortlist_candidate_ids: ["c17", "c32", "c91"]
    node_shortlist_score_snapshot:
      c17: 0.791
      c32: 0.610
      c91: 0.580
    previous_branch_evaluation: [[BranchEvaluation_t]]
    reward_breakdown: [[NodeRewardBreakdown_t]]
    status: "open"
  seed_search_domain:
    frontier_node_id: "seed_search_domain"
    parent_frontier_node_id: null
    donor_frontier_node_id: null
    selected_operator_name: "core_precision"
    node_query_term_pool: ["retrieval engineer", "ranking"]
    knowledge_pack_id: "search_ranking_retrieval_engineering"
    seed_rationale: "补 retrieval/ranking 方向。"
    negative_terms: ["data analyst"]
    parent_shortlist_candidate_ids: []
    node_shortlist_candidate_ids: []
    node_shortlist_score_snapshot: {}
    previous_branch_evaluation: null
    reward_breakdown: null
    status: "open"
open_frontier_node_ids: ["child_agent_core_01", "seed_search_domain"]
closed_frontier_node_ids: ["seed_agent_core", "seed_company_background"]
run_term_catalog: ["agent engineer", "rag", "python", "retrieval engineer", "ranking"]
run_shortlist_candidate_ids: ["c17", "c32", "c91"]
semantic_hashes_seen: ["hash_seed_01", "hash_seed_02"]
remaining_budget: 4
```

## 相关

- [[InitializeFrontierState]]
- [[CarryForwardFrontierState]]
- [[SelectActiveFrontierNode]]
- [[OperatorStatistics]]
- [[FrontierState_t1]]
