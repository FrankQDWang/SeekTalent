# Agent Trace: case-stop-controller-direct-rejected

## Trace Meta

```yaml
case_id: case-stop-controller-direct-rejected
routing_mode: inferred_domain
selected_knowledge_pack_id: llm_agent_rag_engineering
stop_reason: controller_stop
run_dir: /Users/frankqdwang/Agents/SeekTalent/artifacts/runtime/cases/case-stop-controller-direct-rejected
```

## Bootstrap

- routing_mode: `inferred_domain`
- selected_knowledge_pack_id: `llm_agent_rag_engineering`
- fallback_reason: `None`
- seed_count: `2`

## Runtime Rounds

| round | action | operator | knowledge_pack_id | stop_reason |
| --- | --- | --- | --- | --- |
| 0 | stop | must_have_alias |  |  |
| 1 | stop | must_have_alias |  | controller_stop |

## Final Result

- shortlist: `[]`
- run_summary: Controller stop was accepted after one retry round.
