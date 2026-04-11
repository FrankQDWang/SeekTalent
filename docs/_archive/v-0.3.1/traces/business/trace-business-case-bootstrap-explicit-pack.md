# Business Trace: case-bootstrap-explicit-pack

## 场景背景

- 场景：显式 pack bootstrap
- 业务解释：业务已明确指定领域，系统直接注入该领域知识包，不再猜领域。

## 关键信号

- 路由结果：`explicit_pack`
- 领域知识包：`['llm_agent_rag_engineering']`
- fallback_reason：`None`
- 终止原因：`controller_stop`
- shortlist：`[]`

## 业务解读

- 该 case 期望走 `explicit_pack`，实际路由为 `explicit_pack`。
- 该 case 期望 stop 为 `controller_stop`，实际 stop 为 `controller_stop`。
- 必须保留的事实：selected_knowledge_pack_ids contains llm_agent_rag_engineering; pack_expansion remains legal in bootstrap。
- 不应出现的事实：routing_mode = generic_fallback。
