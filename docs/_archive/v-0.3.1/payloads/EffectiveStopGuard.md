# EffectiveStopGuard

当前轮真正生效的 stop guard 快照。

```text
EffectiveStopGuard = {
  search_phase,
  controller_stop_allowed,
  exhausted_low_gain_allowed,
  novelty_floor,
  usefulness_floor,
  reward_floor
}
```

## Producer / Consumer

- Direct producer：`build_effective_stop_guard(...)`
- Direct consumer：`evaluate_stop_condition(...)`
- Trace owner：`SearchRoundArtifact.effective_stop_guard`

## Invariants

- stop gate owner 是 `RuntimeBudgetState.search_phase`
- `controller_stop_allowed` 只在 `balance / harvest` 为 `true`
- `exhausted_low_gain_allowed` 只在 `harvest` 为 `true`

## 最小示例

```yaml
search_phase: "harvest"
controller_stop_allowed: true
exhausted_low_gain_allowed: true
novelty_floor: 0.25
usefulness_floor: 0.25
reward_floor: 1.5
```
