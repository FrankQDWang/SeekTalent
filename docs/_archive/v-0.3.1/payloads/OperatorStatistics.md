# OperatorStatistics

frontier 运行态里每个 operator 的统计桶。

```text
OperatorStatistics = { average_reward, times_selected }
```

## 稳定字段组

- 平均 reward：`average_reward`
- 选中次数：`times_selected`

## Invariants

- `times_selected` 必须是非负整数。
- `average_reward` 只由 `accumulate_operator_statistics(...)` 更新。

## 最小示例

```yaml
average_reward: 3.8
times_selected: 1
```

## 相关

- [[FrontierState_t]]
- [[UpdateFrontierState]]
