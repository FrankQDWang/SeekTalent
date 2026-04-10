# Agent Trace: case-bootstrap-out-of-domain-generic

## Trace Meta

```yaml
case_id: case-bootstrap-out-of-domain-generic
routing_mode: generic_fallback
selected_knowledge_pack_ids: []
stop_reason: controller_stop
run_dir: /Users/frankqdwang/Agents/SeekTalent/artifacts/runtime/cases/case-bootstrap-out-of-domain-generic
```

## Bootstrap

- routing_mode: `generic_fallback`
- selected_knowledge_pack_ids: `[]`
- fallback_reason: `top1_confidence_below_floor`
- seed_count: `4`

## Runtime Rounds

| round | action | operator | knowledge_pack_ids | stop_reason |
| --- | --- | --- | --- | --- |
| 0 | stop | must_have_alias |  | controller_stop |

## Final Result

- shortlist: `[]`
- run_summary: Out-of-domain route fell back to generic bootstrap.
