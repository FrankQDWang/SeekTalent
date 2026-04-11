# Business Trace: case-stop-controller-direct-rejected

## 场景背景

- 场景：controller stop 先拒绝后接受
- 业务解释：在 explore 期 stop 会被拒绝，直到 balance 期才被 runtime 接受。

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
- must_hold：round 0 stop_reason is null; round 1 stop_reason is null; round 2 stop_reason is controller_stop
- must_not_hold：round count equals 1
