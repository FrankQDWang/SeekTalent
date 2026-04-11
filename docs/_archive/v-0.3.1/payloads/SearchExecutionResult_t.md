# SearchExecutionResult_t

执行检索计划后返回的候选集合与页面统计。

```text
SearchExecutionResult_t = {
  raw_candidates,
  deduplicated_candidates,
  scoring_candidates,
  search_page_statistics,
  search_observation
}
```

## 稳定字段组

- 原始候选：`raw_candidates: list[RetrievedCandidate_t]`
- 去重候选：`deduplicated_candidates: list[RetrievedCandidate_t]`
- 评分候选：`scoring_candidates: list[ScoringCandidate_t]`
- 页面统计：`search_page_statistics: SearchPageStatistics`
- 搜索观察：`search_observation: SearchObservation`

## Direct Producer / Direct Consumers

- Direct producer：[[ExecuteSearchPlan]]
- Direct consumers：[[ScoreSearchResults]]、[[EvaluateBranchOutcome]]、[[ComputeNodeRewardBreakdown]]

## Invariants

- `raw_candidates` 与 `deduplicated_candidates` 都是 `list[RetrievedCandidate_t]`。
- `deduplicated_candidates` 必须是 `raw_candidates` 的去重子集。
- `scoring_candidates` 必须与 `deduplicated_candidates` 在 `candidate_id` 上一一对齐。
- `search_page_statistics` 记录成本事实，不承担语义推断。
- `search_observation.unique_candidate_ids` 必须与 `deduplicated_candidates` 对齐。

## 最小示例

```yaml
raw_candidates:
  - candidate_id: "c07"
    now_location: "上海"
deduplicated_candidates:
  - candidate_id: "c07"
    now_location: "上海"
scoring_candidates:
  - candidate_id: "c07"
    scoring_text: "Agent platform backend lead. 6 years experience."
    location_signals: ["上海", "杭州"]
search_page_statistics:
  pages_fetched: 2
  duplicate_rate: 0.25
  latency_ms: 1800
search_observation:
  unique_candidate_ids: ["c07"]
  shortage_after_last_page: false
```

## 相关

- [[SearchExecutionPlan_t]]
- [[RetrievedCandidate_t]]
- [[ScoringCandidate_t]]
- [[SearchPageStatistics]]
- [[SearchObservation]]
- [[ExecuteSearchPlan]]
