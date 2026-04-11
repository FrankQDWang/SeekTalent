# Business Trace: case-bootstrap-out-of-domain-generic

## 场景背景

- 场景：低分 out-of-domain generic
- 业务解释：JD 缺少领域锚点时，不强行路由任何领域知识包。

## Observed Facts

- 路由结果：`generic_fallback`
- 领域知识包：`[]`
- fallback_reason：`top1_confidence_below_floor`
- 终止原因：`controller_stop`
- final_candidate_ids：`[]`

| round | phase | action | continue_flag | stop_reason | round_outcome |
| --- | --- | --- | --- | --- | --- |
| 0 | explore | stop | yes | None | stop rejected by phase gate |
| 1 | explore | stop | yes | None | stop rejected by phase gate |
| 2 | balance | stop | no | controller_stop | terminated |

## Case Expectations (spec-derived)

- expected_route：`generic_fallback`
- expected_stop_reason：`controller_stop`
- must_hold：fallback_reason is top1_confidence_below_floor; selected_knowledge_pack_ids is empty
- must_not_hold：routing_mode = inferred_single_pack
