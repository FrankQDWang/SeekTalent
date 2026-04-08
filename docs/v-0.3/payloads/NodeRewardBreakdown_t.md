# NodeRewardBreakdown_t

一次扩展的 deterministic reward 分项。

```text
NodeRewardBreakdown_t = { delta_top_three, must_have_gain, new_fit_yield, novelty, usefulness, diversity, stability_risk_penalty, hard_constraint_violation, duplicate_penalty, cost_penalty, reward_score }
```

## 稳定字段组

- top three 增益：`delta_top_three`
- must-have 增益：`must_have_gain`
- 新增 fit 产出：`new_fit_yield`
- 新颖度：`novelty`
- 有用度：`usefulness`
- 多样性：`diversity`
- 稳定性风险惩罚：`stability_risk_penalty`
- 硬约束违例：`hard_constraint_violation`
- 重复惩罚：`duplicate_penalty`
- 成本惩罚：`cost_penalty`
- 合成 reward：`reward_score`

## Direct Producer / Direct Consumers

- Direct producer：[[ComputeNodeRewardBreakdown]]
- Direct consumers：[[UpdateFrontierState]]、[[EvaluateStopCondition]]

## Invariants

- `reward_score` 必须由固定公式从各分项合成。
- 每个分项都必须能追溯到上游 payload 字段。
- `delta_top_three` 读的是 fused score，不再读生成式 LLM 的 base score。
- `cost_penalty` 只能依赖 deterministic runtime counters；不得读取 wall-clock latency。

## 最小示例

```yaml
delta_top_three: 0.18
must_have_gain: 0.67
new_fit_yield: 1.0
novelty: 0.66
usefulness: 0.74
diversity: 0.44
stability_risk_penalty: 0.12
hard_constraint_violation: 0.0
duplicate_penalty: 0.25
cost_penalty: 0.30
reward_score: 4.23
```

## 相关

- [[ComputeNodeRewardBreakdown]]
- [[CareerStabilityProfile]]
- [[FrontierState_t1]]
- [[EvaluateStopCondition]]
