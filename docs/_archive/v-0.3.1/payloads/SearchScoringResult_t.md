# SearchScoringResult_t

在冻结评分口径下产生的节点局部排序结果。

```text
SearchScoringResult_t = { scored_candidates, node_shortlist_candidate_ids, explanation_candidate_ids, top_three_statistics }
```

## 稳定字段组

- 候选排序明细：`scored_candidates: list[ScoredCandidate_t]`
- 节点 shortlist id：`node_shortlist_candidate_ids`
- explanation 候选 id：`explanation_candidate_ids`
- top three 统计：`top_three_statistics`

## Direct Producer / Direct Consumers

- Direct producer：[[ScoreSearchResults]]
- Direct consumers：[[EvaluateBranchOutcome]]、[[ComputeNodeRewardBreakdown]]、[[UpdateFrontierState]]

## Invariants

- `node_shortlist_candidate_ids` 是 node-local，不等于 run-global shortlist。
- `explanation_candidate_ids` 必须是排序结果的稳定子集。
- `top_three_statistics` 描述的是 fused score，而不是 LLM 自由分数。
- `scored_candidates[*]` 必须同时携带原始审计分与进入融合用的 `[0,1]` 归一化分。

## 最小示例

```yaml
scored_candidates:
  - candidate_id: "c07"
    fit: 1
    rerank_raw: 5.11
    rerank_normalized: 0.91
    must_have_match_score_raw: 94
    must_have_match_score: 0.94
    preferred_match_score_raw: 80
    preferred_match_score: 0.80
    risk_score_raw: 12
    risk_score: 0.12
    risk_flags: []
    fusion_score: 0.804
  - candidate_id: "c19"
    fit: 1
    rerank_raw: 4.62
    rerank_normalized: 0.88
    must_have_match_score_raw: 90
    must_have_match_score: 0.90
    preferred_match_score_raw: 72
    preferred_match_score: 0.72
    risk_score_raw: 18
    risk_score: 0.18
    risk_flags: ["frequent_job_changes"]
    fusion_score: 0.763
node_shortlist_candidate_ids: ["c07", "c19"]
explanation_candidate_ids: ["c07", "c19", "c51"]
top_three_statistics:
  average_fusion_score_top_three: 0.735
```

## 相关

- [[ScoredCandidate_t]]
- [[ScoreSearchResults]]
- [[CareerStabilityProfile]]
