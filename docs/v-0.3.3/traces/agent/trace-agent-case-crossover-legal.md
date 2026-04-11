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

| round | phase | action | operator | continue_flag | stop_reason | round_outcome |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | explore | search_cts | core_precision | yes | None | continued |
| 1 | explore | search_cts | must_have_alias | yes | None | continued |
| 2 | balance | search_cts | crossover_compose | yes | None | continued |
| 3 | harvest | stop | must_have_alias | no | controller_stop | terminated |

## Final Result

- final_candidate_ids: `['candidate-crossover-3', 'candidate-crossover-1', 'candidate-crossover-2']`
- stop_reason: `controller_stop`
- Bundle Run Summary: Legal crossover produced an expanded shortlist.
