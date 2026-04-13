# Business Trace: case-bootstrap-close-high-score-multi-pack

## 场景背景

- 场景：接近高分触发 multi-pack
- 业务解释：两个领域都很强且分数接近时，同时注入 top2 packs 做 bootstrap。

## Observed Facts

- 路由结果：`inferred_multi_pack`
- 领域知识包：`['llm_agent_rag_engineering', 'finance_risk_control_ai']`
- fallback_reason：`None`
- 终止原因：`controller_stop`
- final_candidate_ids：`[]`

| round | phase | action | continue_flag | stop_reason | round_outcome |
| --- | --- | --- | --- | --- | --- |
| 0 | explore | stop | yes | None | stop rejected by phase gate |
| 1 | explore | stop | yes | None | stop rejected by phase gate |
| 2 | balance | stop | no | controller_stop | terminated |

## Case Expectations (spec-derived)

- expected_route：`inferred_multi_pack`
- expected_stop_reason：`controller_stop`
- must_hold：selected_knowledge_pack_ids contains llm_agent_rag_engineering; selected_knowledge_pack_ids contains finance_risk_control_ai
- must_not_hold：routing_mode = generic_fallback
