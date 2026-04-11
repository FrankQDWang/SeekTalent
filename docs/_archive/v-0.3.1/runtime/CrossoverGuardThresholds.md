# CrossoverGuardThresholds

限制 donor 候选与交叉计划合法性的 runtime guard。

```text
CrossoverGuardThresholds = { min_shared_anchor_terms, min_reward_score, max_donor_candidates }
```

## 默认值

```yaml
min_shared_anchor_terms: 1
min_reward_score: 1.5
max_donor_candidates: 2
```

## Invariants

- donor 节点必须 `status = open`。
- donor 节点必须 `reward_breakdown != null`。
- donor 节点必须满足 `reward_breakdown.reward_score >= min_reward_score`。
- 交叉计划必须满足共享锚点数量 `>= min_shared_anchor_terms`。
- `max_donor_candidates` 是控制器看到的 donor 摘要上限，而不是允许交叉的最终数量。

## 相关

- [[SelectActiveFrontierNode]]
- [[MaterializeSearchExecutionPlan]]
