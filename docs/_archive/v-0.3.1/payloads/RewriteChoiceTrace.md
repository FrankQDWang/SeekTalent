# RewriteChoiceTrace

一轮 non-crossover rewrite 在 GA-lite 排序后的紧凑胜出轨迹。

```text
RewriteChoiceTrace = {
  seed_query_terms,
  selected_query_terms,
  candidate_count,
  selected_total_score,
  selected_breakdown,
  runner_up_query_terms,
  runner_up_total_score
}
```

## Producer / Consumer

- Direct producer：`_ga_lite_query_rewrite(...)`
- Trace owner：`SearchRoundArtifact.rewrite_choice_trace`

## Invariants

- 只在 non-crossover rewrite operator 且存在至少一个合法候选时写入。
- `seed_query_terms` 是 controller draft 进入 GA-lite 前的 rewrite intent，不是 raw active node pool。
- 只保留胜出候选与 runner-up，不落完整 population。
- `runner_up_*` 在合法候选少于 2 个时为 `null`。

## 最小示例

```yaml
seed_query_terms: ["python backend", "ranking", "rag"]
selected_query_terms: ["python backend", "ranking", "retrieval"]
candidate_count: 3
selected_total_score: 2.66
selected_breakdown:
  must_have_repair_score: 1.0
  anchor_preservation_score: 1.0
  rewrite_coherence_score: 0.83
  provenance_coherence_score: 0.79
  query_length_penalty: 0.25
  redundancy_penalty: 0.1
runner_up_query_terms: ["python backend", "rag", "retrieval"]
runner_up_total_score: 2.34
```
