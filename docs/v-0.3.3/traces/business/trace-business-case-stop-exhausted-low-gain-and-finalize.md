# Business Trace: case-stop-exhausted-low-gain-and-finalize

## 场景背景

- 场景：低增益 exhausted finalize
- 业务解释：进入 harvest 后，空结果且 novelty/usefulness/reward 都偏低时，系统应以 exhausted_low_gain 收口。

## Observed Facts

- 路由结果：`inferred_single_pack`
- 领域知识包：`['llm_agent_rag_engineering']`
- fallback_reason：`None`
- 终止原因：`exhausted_low_gain`
- final_candidate_ids：`[]`

| round | phase | action | continue_flag | stop_reason | round_outcome |
| --- | --- | --- | --- | --- | --- |
| 0 | explore | search_cts | yes | None | continued |
| 1 | explore | search_cts | yes | None | continued |
| 2 | balance | search_cts | yes | None | continued |
| 3 | harvest | search_cts | no | exhausted_low_gain | terminated |

## Case Expectations (spec-derived)

- expected_route：`inferred_single_pack`
- expected_stop_reason：`exhausted_low_gain`
- must_hold：round 3 stop_reason is exhausted_low_gain
- must_not_hold：stop_reason = controller_stop
