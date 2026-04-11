# Business Trace: case-crossover-illegal-reject

## 场景背景

- 场景：非法 crossover 拒绝
- 业务解释：控制器给出非法 donor 时，structured validator 立即拒绝并要求重试。

## Observed Facts

- 路由结果：`inferred_single_pack`
- 领域知识包：`['llm_agent_rag_engineering']`
- fallback_reason：`None`
- 终止原因：`controller_stop`
- final_candidate_ids：`['candidate-illegal-2', 'candidate-illegal-1']`

| round | phase | action | continue_flag | stop_reason | round_outcome |
| --- | --- | --- | --- | --- | --- |
| 0 | explore | search_cts | yes | None | continued |
| 1 | explore | search_cts | yes | None | continued |
| 2 | balance | stop | no | controller_stop | terminated |

## Case Expectations (spec-derived)

- expected_route：`inferred_single_pack`
- expected_stop_reason：`controller_stop`
- must_hold：controller validator retry count equals 1
- must_not_hold：illegal crossover reaches execution_plan
