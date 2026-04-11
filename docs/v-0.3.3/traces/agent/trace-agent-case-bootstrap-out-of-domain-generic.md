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

| round | phase | action | operator | continue_flag | stop_reason | round_outcome |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | explore | stop | must_have_alias | yes | None | stop rejected by phase gate |
| 1 | explore | stop | must_have_alias | yes | None | stop rejected by phase gate |
| 2 | balance | stop | must_have_alias | no | controller_stop | terminated |

## Final Result

- final_candidate_ids: `[]`
- stop_reason: `controller_stop`
- Bundle Run Summary: Out-of-domain route fell back to generic bootstrap.
