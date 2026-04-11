# RetrievedCandidate_t

经 CTS adapter 与 runtime 基础归一化后进入本轮搜索结果的候选对象。

```text
RetrievedCandidate_t = {
  candidate_id,
  age,
  gender,
  now_location,
  expected_location,
  years_of_experience_raw,
  education_summaries,
  work_experience_summaries,
  project_names,
  work_summaries,
  search_text,
  raw_payload
}
```

## 稳定字段组

- 候选唯一标识：`candidate_id`
- 年龄：`age`
- 性别：`gender`
- 当前地点：`now_location`
- 期望地点：`expected_location`
- 原始工作年限：`years_of_experience_raw`
- 教育摘要：`education_summaries`
- 工作经历摘要：`work_experience_summaries`
- 项目名：`project_names`
- 工作摘要：`work_summaries`
- 搜索文本：`search_text`
- 原始 CTS payload：`raw_payload`

## Direct Producer / Direct Consumers

- Direct producer：[[ExecuteSearchPlan]]
- Direct consumers：[[SearchExecutionResult_t]]

## Invariants

- `candidate_id` 必须非空。
- `education_summaries`、`work_experience_summaries`、`project_names`、`work_summaries` 允许为空数组，但不使用 `null`。
- `search_text` 必须是可直接进入评分归一化层的自然文本，不是任意 JSON dump。
- `raw_payload` 仅承担 CTS 审计与追溯职责，不直接进入 reranker。

## 最小示例

```yaml
candidate_id: "c07"
age: 29
gender: "男"
now_location: "上海"
expected_location: "杭州"
years_of_experience_raw: 6
education_summaries: ["复旦大学 本科 计算机科学与技术"]
work_experience_summaries:
  - "2021-至今 某Agent平台公司 后端负责人"
project_names: ["Workflow Orchestrator", "RAG Platform"]
work_summaries: ["负责 Agent runtime 与检索增强平台建设"]
search_text: "6年经验，Agent runtime / RAG / Python 后端负责人..."
raw_payload:
  source: "cts"
```

## 相关

- [[ExecuteSearchPlan]]
- [[SearchExecutionResult_t]]
- [[ScoringCandidate_t]]
- [[cts-projection-policy]]
