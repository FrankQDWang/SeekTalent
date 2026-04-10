# ScoringCandidate_t

由候选基础结果进一步投影出的评分专用对象，是 `ScoreSearchResults` 的正式输入面。

```text
ScoringCandidate_t = {
  candidate_id,
  scoring_text,
  capability_signals,
  years_of_experience,
  age,
  gender,
  location_signals,
  work_experience_summaries,
  education_summaries,
  career_stability_profile
}
```

## 稳定字段组

- 候选唯一标识：`candidate_id`
- reranker 文本：`scoring_text`
- 能力信号：`capability_signals`
- 数值化工作年限：`years_of_experience`（`int | None`）
- 年龄：`age`
- 性别：`gender`
- 地点信号：`location_signals`
- 工作经历摘要：`work_experience_summaries`
- 教育摘要：`education_summaries`
- 职业稳定性画像：`career_stability_profile: CareerStabilityProfile`

## Direct Producer / Direct Consumers

- Direct producer：[[ExecuteSearchPlan]]（经 runtime scoring normalization）
- Direct consumers：[[ScoreSearchResults]]

## Invariants

- `candidate_id` 必须与对应的 `RetrievedCandidate_t.candidate_id` 一致。
- `scoring_text` 必须是自然文本，不是结构化对象或 JSON 序列化字符串。
- `capability_signals` 与 `location_signals` 允许为空数组，但不使用 `null`。
- `years_of_experience` 是 runtime 直接投影出的 numeric years；当前不要求额外 parser 或二次“归一化”步骤。
- `career_stability_profile` 必须始终存在；无法稳定解析时应保留低置信度 profile，而不是缺字段。

## 最小示例

```yaml
candidate_id: "c07"
scoring_text: "Agent platform backend lead. 6 years experience. Python, RAG, workflow orchestration."
capability_signals: ["Agent runtime", "RAG", "Python", "workflow orchestration"]
years_of_experience: 6
age: 29
gender: "男"
location_signals: ["上海", "杭州"]
work_experience_summaries:
  - "2021-至今 某Agent平台公司 后端负责人"
education_summaries: ["复旦大学 本科 计算机科学与技术"]
career_stability_profile:
  job_count_last_5y: 3
  short_tenure_count: 1
  median_tenure_months: 22
  current_tenure_months: 14
  parsed_experience_count: 4
  confidence_score: 0.82
```

## 相关

- [[RetrievedCandidate_t]]
- [[SearchExecutionResult_t]]
- [[ScoreSearchResults]]
- [[CareerStabilityProfile]]
