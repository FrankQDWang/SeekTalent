# Agent Trace: case-crossover-illegal-reject

## Trace Meta

```yaml
case_id: case-crossover-illegal-reject
routing_mode: inferred_single_pack
selected_knowledge_pack_ids: ['llm_agent_rag_engineering']
stop_reason: controller_stop
run_dir: /Users/frankqdwang/Agents/SeekTalent/artifacts/runtime/cases/case-crossover-illegal-reject
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
| 2 | balance | stop | must_have_alias | no | controller_stop | terminated |

## Final Result

- final_candidate_ids: `['candidate-illegal-2', 'candidate-illegal-1']`
- stop_reason: `controller_stop`
- Bundle Run Summary: Illegal crossover was rejected and the run stopped on retry.
