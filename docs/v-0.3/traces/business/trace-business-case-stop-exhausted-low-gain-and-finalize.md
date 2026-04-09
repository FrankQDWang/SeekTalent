# Business Trace: case-stop-exhausted-low-gain-and-finalize

## 场景背景

- 场景：低增益 exhausted finalize
- 业务解释：空结果且 novelty/usefulness/reward 都偏低时，系统应以 exhausted_low_gain 收口。

## 关键信号

- 路由结果：`inferred_domain`
- 领域知识包：`llm_agent_rag_engineering`
- fallback_reason：`None`
- 终止原因：`exhausted_low_gain`
- shortlist：`[]`

## 业务解读

- 该 case 期望走 `inferred_domain`，实际路由为 `inferred_domain`。
- 该 case 期望 stop 为 `exhausted_low_gain`，实际 stop 为 `exhausted_low_gain`。
- 必须保留的事实：first search round has empty deduplicated candidates。
- 不应出现的事实：stop_reason = controller_stop。
