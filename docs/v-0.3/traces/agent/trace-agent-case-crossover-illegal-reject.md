# Agent Trace: case-crossover-illegal-reject

## Trace Meta

```yaml
case_id: case-crossover-illegal-reject
routing_mode: inferred_domain
selected_knowledge_pack_id: llm_agent_rag_engineering
stop_reason: controller_stop
run_dir: /Users/frankqdwang/Agents/SeekTalent/artifacts/runtime/cases/case-crossover-illegal-reject
```

## Bootstrap

- routing_mode: `inferred_domain`
- selected_knowledge_pack_id: `llm_agent_rag_engineering`
- fallback_reason: `None`
- seed_count: `2`

## Runtime Rounds

| round | action | operator | knowledge_pack_id | stop_reason |
| --- | --- | --- | --- | --- |
| 0 | search_cts | strict_core | llm_agent_rag_engineering |  |
| 1 | stop | must_have_alias |  | controller_stop |

## Final Result

- shortlist: `['candidate-illegal-1']`
- run_summary: Illegal crossover was rejected and the run stopped on retry.
