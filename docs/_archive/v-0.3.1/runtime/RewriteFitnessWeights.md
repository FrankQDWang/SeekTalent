# RewriteFitnessWeights

runtime rewrite tuning owner。

```text
RewriteFitnessWeights = {
  must_have_repair,
  anchor_preservation,
  rewrite_coherence,
  provenance_coherence,
  query_length_penalty,
  redundancy_penalty
}
```

## 默认值

```yaml
must_have_repair: 1.4
anchor_preservation: 1.0
rewrite_coherence: 1.2
provenance_coherence: 0.8
query_length_penalty: 0.35
redundancy_penalty: 0.45
```

## Invariants

- 这组权重只影响 GA-lite rewrite fitness，不改 non-crossover rewrite contract。
- `GenerateSearchControllerDecision` 里的 contract filter 先执行，`RewriteFitnessWeights` 只在合法候选之间排序。
- 这组权重属于 runtime tuning，不属于 `BusinessPolicyPack`。
- `anchor_preservation` 现在围绕 controller draft `seed_query_terms` 的 anchor 保留率打分，不再只看 raw active pool overlap。
- `rewrite_coherence` 内部会综合 evidence strength、term alignment 和多 term source agreement。
- `provenance_coherence` 内部会综合 field strength、support strength 和 source overlap。

## 相关

- [[GenerateSearchControllerDecision]]
- [[weights-and-thresholds-index]]
