# Business Trace: case-crossover-illegal-reject

## 场景背景

- 场景：非法 crossover 拒绝
- 业务解释：控制器给出非法 donor 时，structured validator 立即拒绝并要求重试。

## 关键信号

- 路由结果：`inferred_single_pack`
- 领域知识包：`['llm_agent_rag_engineering']`
- fallback_reason：`None`
- 终止原因：`controller_stop`
- shortlist：`['candidate-illegal-1']`

## 业务解读

- 该 case 期望走 `inferred_single_pack`，实际路由为 `inferred_single_pack`。
- 该 case 期望 stop 为 `controller_stop`，实际 stop 为 `controller_stop`。
- 必须保留的事实：controller validator retry count equals 1。
- 不应出现的事实：illegal crossover reaches execution_plan。
