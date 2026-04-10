# Business Trace: case-stop-controller-direct-accepted

## 场景背景

- 场景：controller stop 直接接受
- 业务解释：达到最小 round 门槛后，controller stop 可以直接终止 run。

## 关键信号

- 路由结果：`inferred_single_pack`
- 领域知识包：`['llm_agent_rag_engineering']`
- fallback_reason：`None`
- 终止原因：`controller_stop`
- shortlist：`[]`

## 业务解读

- 该 case 期望走 `inferred_single_pack`，实际路由为 `inferred_single_pack`。
- 该 case 期望 stop 为 `controller_stop`，实际 stop 为 `controller_stop`。
- 必须保留的事实：round 0 stop_reason is controller_stop。
- 不应出现的事实：search_cts round exists。
