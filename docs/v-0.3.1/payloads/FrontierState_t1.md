# FrontierState_t1

一次扩展完成后的 frontier 新状态。

```text
FrontierState_t1 = {
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

- Direct producer：[[UpdateFrontierState]]、[[CarryForwardFrontierState]]
- Direct consumers：[[EvaluateStopCondition]]、[[FinalizeSearchRun]]、runtime round shift

## Invariants

- `FrontierState_t1` 的对象形状与 `FrontierState_t` 相同，但字段值不同。
- 当前轮里它服务于 stop / finalize；进入下一轮前会经 runtime round shift 重绑定为 `FrontierState_t`。
- child node 的 provenance、`knowledge_pack_id` 与 `donor_frontier_node_id` 必须在这里完整保留。
- `operator_statistics` 的 value shape 由 [[OperatorStatistics]] 唯一持有。
- `run_shortlist_candidate_ids` 的顺序已经在 `UpdateFrontierState` 中冻结，不允许 `FinalizeSearchRun` 再重排。

## 最小示例

```yaml
frontier_nodes:
  child_search_domain_01:
    frontier_node_id: "child_search_domain_01"
    parent_frontier_node_id: "seed_search_domain"
    donor_frontier_node_id: null
    selected_operator_name: "core_precision"
    node_query_term_pool: ["retrieval engineer", "ranking"]
    knowledge_pack_id: "search_ranking_retrieval_engineering"
    seed_rationale: null
    negative_terms: ["data analyst"]
    parent_shortlist_candidate_ids: ["c11", "c44"]
    node_shortlist_candidate_ids: ["c11", "c44"]
    node_shortlist_score_snapshot:
      c11: 0.742
      c44: 0.701
    previous_branch_evaluation: [[BranchEvaluation_t]]
    reward_breakdown: [[NodeRewardBreakdown_t]]
    status: "open"
  child_crossover_03:
    frontier_node_id: "child_crossover_03"
    parent_frontier_node_id: "seed_agent_core"
    donor_frontier_node_id: "child_search_domain_01"
    selected_operator_name: "crossover_compose"
    node_query_term_pool: ["agent engineer", "rag", "python", "retrieval engineer", "ranking"]
    knowledge_pack_id: "llm_agent_rag_engineering"
    seed_rationale: null
    negative_terms: ["data analyst", "pure algorithm research"]
    parent_shortlist_candidate_ids: ["c32", "c44"]
    node_shortlist_candidate_ids: ["c07", "c19", "c51"]
    node_shortlist_score_snapshot:
      c07: 0.804
      c19: 0.763
      c51: 0.637
    previous_branch_evaluation: [[BranchEvaluation_t]]
    reward_breakdown: [[NodeRewardBreakdown_t]]
    status: "open"
open_frontier_node_ids: ["child_search_domain_01", "child_crossover_03"]
closed_frontier_node_ids: ["seed_company_background", "seed_search_domain", "seed_agent_core"]
run_shortlist_candidate_ids: ["c07", "c17", "c19", "c51", "c32", "c91"]
semantic_hashes_seen: ["hash_seed_01", "hash_seed_02", "hash_crossover_03"]
remaining_budget: 3
```

## 相关

- [[UpdateFrontierState]]
- [[CarryForwardFrontierState]]
- [[OperatorStatistics]]
- [[EvaluateStopCondition]]
- [[SearchRunResult]]
