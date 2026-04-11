# Business Trace: case-bootstrap-explicit-pack

## 场景背景

- 场景：显式 pack bootstrap
- 业务解释：业务已明确指定领域，系统直接注入该领域知识包，不再猜领域。

## Observed Facts

- 路由结果：`explicit_pack`
- 领域知识包：`['llm_agent_rag_engineering']`
- fallback_reason：`None`
- 终止原因：`controller_stop`
- final_candidate_ids：`[]`

| round | phase | action | continue_flag | stop_reason | round_outcome |
| --- | --- | --- | --- | --- | --- |
| 0 | explore | stop | yes | None | stop rejected by phase gate |
| 1 | explore | stop | yes | None | stop rejected by phase gate |
| 2 | balance | stop | no | controller_stop | terminated |

## Case Expectations (spec-derived)

- expected_route：`explicit_pack`
- expected_stop_reason：`controller_stop`
- must_hold：selected_knowledge_pack_ids contains llm_agent_rag_engineering; pack_bridge remains legal in bootstrap
- must_not_hold：routing_mode = generic_fallback
