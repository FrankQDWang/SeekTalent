# Business Trace: case-crossover-legal

## 场景背景

- 场景：合法 crossover
- 业务解释：进入 balance 期且已有合法 donor 时，控制器可发起 crossover 搜索。

## Observed Facts

- 路由结果：`inferred_single_pack`
- 领域知识包：`['llm_agent_rag_engineering']`
- fallback_reason：`None`
- 终止原因：`controller_stop`
- final_candidate_ids：`['candidate-crossover-3', 'candidate-crossover-1', 'candidate-crossover-2']`

| round | phase | action | continue_flag | stop_reason | round_outcome |
| --- | --- | --- | --- | --- | --- |
| 0 | explore | search_cts | yes | None | continued |
| 1 | explore | search_cts | yes | None | continued |
| 2 | balance | search_cts | yes | None | continued |
| 3 | harvest | stop | no | controller_stop | terminated |

## Case Expectations (spec-derived)

- expected_route：`inferred_single_pack`
- expected_stop_reason：`controller_stop`
- must_hold：round 2 uses crossover_compose with donor_frontier_node_id
- must_not_hold：missing donor candidate list
