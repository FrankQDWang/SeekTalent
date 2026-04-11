# RewriteChoiceScoreBreakdown

GA-lite 在合法 rewrite 候选之间排序时使用的紧凑打分拆解。

```text
RewriteChoiceScoreBreakdown = {
  must_have_repair_score,
  anchor_preservation_score,
  rewrite_coherence_score,
  provenance_coherence_score,
  query_length_penalty,
  redundancy_penalty
}
```

## Producer / Consumer

- Direct producer：`_rewrite_score_breakdown(...)`
- Direct consumer：`RewriteChoiceTrace.selected_breakdown`

## Invariants

- 这是 trace explainability owner，不是新的 runtime tuning owner。
- `anchor_preservation_score` 围绕 controller draft `seed_query_terms` 的 anchor 保留率计算。
- `rewrite_coherence_score` 与 `provenance_coherence_score` 都是归一化子分数，最终仍由 `RewriteFitnessWeights` 加权。
- `query_length_penalty` 与 `redundancy_penalty` 是惩罚项，数值越大越不利于候选。

## 最小示例

```yaml
must_have_repair_score: 1.0
anchor_preservation_score: 1.0
rewrite_coherence_score: 0.82
provenance_coherence_score: 0.78
query_length_penalty: 0.25
redundancy_penalty: 0.1
```
