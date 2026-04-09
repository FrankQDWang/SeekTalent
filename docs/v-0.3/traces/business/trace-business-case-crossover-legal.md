# Business Trace: case-crossover-legal

## 场景背景

- 场景：合法 crossover
- 业务解释：已有合法 donor 且 shared anchor 成立时，控制器可发起 crossover 搜索。

## 关键信号

- 路由结果：`inferred_domain`
- 领域知识包：`llm_agent_rag_engineering`
- fallback_reason：`None`
- 终止原因：`controller_stop`
- shortlist：`['candidate-crossover-2', 'candidate-crossover-1']`

## 业务解读

- 该 case 期望走 `inferred_domain`，实际路由为 `inferred_domain`。
- 该 case 期望 stop 为 `controller_stop`，实际 stop 为 `controller_stop`。
- 必须保留的事实：round 1 uses crossover_compose with donor_frontier_node_id。
- 不应出现的事实：missing donor candidate list。
