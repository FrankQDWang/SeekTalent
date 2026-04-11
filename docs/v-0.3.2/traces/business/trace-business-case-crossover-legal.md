# Business Trace: case-crossover-legal

## 场景背景

- 场景：合法 crossover
- 业务解释：进入 balance 期且已有合法 donor 时，控制器可发起 crossover 搜索。

## 关键信号

- 路由结果：`inferred_single_pack`
- 领域知识包：`['llm_agent_rag_engineering']`
- fallback_reason：`None`
- 终止原因：`controller_stop`
- shortlist：`['candidate-crossover-3', 'candidate-crossover-1', 'candidate-crossover-2']`

## 业务解读

- 该 case 期望走 `inferred_single_pack`，实际路由为 `inferred_single_pack`。
- 该 case 期望 stop 为 `controller_stop`，实际 stop 为 `controller_stop`。
- 必须保留的事实：round 2 uses crossover_compose with donor_frontier_node_id。
- 不应出现的事实：missing donor candidate list。
