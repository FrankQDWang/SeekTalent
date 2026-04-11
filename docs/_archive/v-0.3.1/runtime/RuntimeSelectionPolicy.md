# RuntimeSelectionPolicy

runtime selection tuning owner。

```text
RuntimeSelectionPolicy = {
  explore: { exploit, explore, coverage, incremental, fresh, redundancy },
  balance: { exploit, explore, coverage, incremental, fresh, redundancy },
  harvest: { exploit, explore, coverage, incremental, fresh, redundancy }
}
```

## 默认值

```yaml
explore:
  exploit: 0.6
  explore: 1.6
  coverage: 1.2
  incremental: 0.2
  fresh: 0.8
  redundancy: 0.4
balance:
  exploit: 1.0
  explore: 1.0
  coverage: 0.8
  incremental: 0.8
  fresh: 0.3
  redundancy: 0.8
harvest:
  exploit: 1.4
  explore: 0.3
  coverage: 0.2
  incremental: 1.2
  fresh: 0.0
  redundancy: 1.2
```

## Invariants

- `SelectActiveFrontierNode` 只允许按 `search_phase` 读取对应 phase 的一组权重。
- 这组权重属于 runtime tuning，不属于 `BusinessPolicyPack`。
- `selection-plan-semantics` 仍然定义公式；`RuntimeSelectionPolicy` 只定义默认值与调参 owner。

## 相关

- [[selection-plan-semantics]]
- [[SelectActiveFrontierNode]]
- [[weights-and-thresholds-index]]
