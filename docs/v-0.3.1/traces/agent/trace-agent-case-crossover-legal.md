# Agent Trace: case-crossover-legal

## Trace Meta

```yaml
case_id: case-crossover-legal
routing_mode: inferred_single_pack
selected_knowledge_pack_ids: ['llm_agent_rag_engineering']
stop_reason: controller_stop
run_dir: /Users/frankqdwang/Agents/SeekTalent/artifacts/runtime/cases/case-crossover-legal
```

## Bootstrap

- routing_mode: `inferred_single_pack`
- selected_knowledge_pack_ids: `['llm_agent_rag_engineering']`
- fallback_reason: `None`
- seed_count: `5`

## Runtime Rounds

| round | action | operator | knowledge_pack_ids | stop_reason |
| --- | --- | --- | --- | --- |
| 0 | search_cts | core_precision | ['llm_agent_rag_engineering'] |  |
| 1 | search_cts | must_have_alias | ['llm_agent_rag_engineering'] |  |
| 2 | search_cts | crossover_compose | ['llm_agent_rag_engineering'] |  |
| 3 | stop | must_have_alias |  | controller_stop |

## Final Result

- shortlist: `['candidate-crossover-3', 'candidate-crossover-1', 'candidate-crossover-2']`
- run_summary: Legal crossover produced an expanded shortlist.
