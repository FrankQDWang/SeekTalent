# Business Trace: case-bootstrap-inferred-top1

## 场景背景

- 场景：单领域 top1 路由
- 业务解释：岗位文本足够明确，reranker 应稳定命中单一领域知识包。

## 关键信号

- 路由结果：`inferred_domain`
- 领域知识包：`llm_agent_rag_engineering`
- fallback_reason：`None`
- 终止原因：`controller_stop`
- shortlist：`[]`

## 业务解读

- 该 case 期望走 `inferred_domain`，实际路由为 `inferred_domain`。
- 该 case 期望 stop 为 `controller_stop`，实际 stop 为 `controller_stop`。
- 必须保留的事实：selected_knowledge_pack_id is llm_agent_rag_engineering。
- 不应出现的事实：routing_mode = generic_fallback。
