# Business Trace: case-bootstrap-inferred-single-pack

## 场景背景

- 场景：单领域 top1 路由
- 业务解释：岗位文本足够明确，reranker 应稳定命中单一领域知识包。

## Observed Facts

- 路由结果：`inferred_single_pack`
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

- expected_route：`inferred_single_pack`
- expected_stop_reason：`controller_stop`
- must_hold：selected_knowledge_pack_ids contains llm_agent_rag_engineering
- must_not_hold：routing_mode = generic_fallback
