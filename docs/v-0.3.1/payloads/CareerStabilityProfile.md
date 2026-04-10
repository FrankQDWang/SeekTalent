# CareerStabilityProfile

由工作时间线解析得到的职业稳定性特征快照。

```text
CareerStabilityProfile = { job_count_last_5y, short_tenure_count, median_tenure_months, current_tenure_months, parsed_experience_count, confidence_score }
```

## 稳定字段组

- 最近五年岗位数：`job_count_last_5y`
- 短 tenure 次数：`short_tenure_count`
- tenure 中位数：`median_tenure_months`
- 当前 tenure：`current_tenure_months`
- 成功解析的经历数：`parsed_experience_count`
- 置信度：`confidence_score`

## Direct Producer / Direct Consumers

- Direct producer：resume normalization / timeline parser
- Direct consumers：[[ScoreSearchResults]]

## Invariants

- `confidence_score` 必须落在 `[0, 1]`。
- 时间线解析失败或冲突时，应降低 `confidence_score`，而不是强行推断。
- 低置信度 profile 默认不触发 stability penalty。

## 最小示例

```yaml
job_count_last_5y: 3
short_tenure_count: 1
median_tenure_months: 22
current_tenure_months: 14
parsed_experience_count: 4
confidence_score: 0.82
```

## 相关

- [[ScoreSearchResults]]
- [[NodeRewardBreakdown_t]]
