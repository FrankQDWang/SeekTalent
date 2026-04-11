# Business Trace: case-bootstrap-close-high-score-multi-pack

## 场景背景

- 场景：接近高分触发 multi-pack
- 业务解释：两个领域都很强且分数接近时，同时注入 top2 packs 做 bootstrap。

## 关键信号

- 路由结果：`inferred_multi_pack`
- 领域知识包：`['llm_agent_rag_engineering', 'search_ranking_retrieval_engineering']`
- fallback_reason：`None`
- 终止原因：`controller_stop`
- shortlist：`[]`

## 业务解读

- 该 case 期望走 `inferred_multi_pack`，实际路由为 `inferred_multi_pack`。
- 该 case 期望 stop 为 `controller_stop`，实际 stop 为 `controller_stop`。
- 必须保留的事实：selected_knowledge_pack_ids contains llm_agent_rag_engineering; selected_knowledge_pack_ids contains search_ranking_retrieval_engineering。
- 不应出现的事实：routing_mode = generic_fallback。
