# Business Trace: case-bootstrap-out-of-domain-generic

## 场景背景

- 场景：低分 out-of-domain generic
- 业务解释：JD 缺少领域锚点时，不强行路由任何领域知识包。

## 关键信号

- 路由结果：`generic_fallback`
- 领域知识包：`[]`
- fallback_reason：`top1_confidence_below_floor`
- 终止原因：`controller_stop`
- shortlist：`[]`

## 业务解读

- 该 case 期望走 `generic_fallback`，实际路由为 `generic_fallback`。
- 该 case 期望 stop 为 `controller_stop`，实际 stop 为 `controller_stop`。
- 必须保留的事实：fallback_reason is top1_confidence_below_floor; selected_knowledge_pack_ids is empty。
- 不应出现的事实：routing_mode = inferred_single_pack。
