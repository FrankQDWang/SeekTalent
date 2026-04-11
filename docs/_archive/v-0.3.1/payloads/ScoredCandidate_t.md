# ScoredCandidate_t

`SearchScoringResult_t.scored_candidates[*]` 的 canonical schema owner。

```text
ScoredCandidate_t = {
  candidate_id,
  fit,
  rerank_raw,
  rerank_normalized,
  must_have_match_score_raw,
  must_have_match_score,
  preferred_match_score_raw,
  preferred_match_score,
  risk_score_raw,
  risk_score,
  risk_flags,
  fusion_score
}
```

## 稳定字段组

- 候选 id：`candidate_id`
- fit 结果：`fit`
- rerank 审计分与归一化分：`rerank_raw`、`rerank_normalized`
- must-have 审计分与归一化分：`must_have_match_score_raw`、`must_have_match_score`
- preferred 审计分与归一化分：`preferred_match_score_raw`、`preferred_match_score`
- risk 审计分与归一化分：`risk_score_raw`、`risk_score`
- 风险标记：`risk_flags`
- 融合分：`fusion_score`

## Invariants

- 所有 `*_raw` 必须是 `[0, 100]`。
- 所有归一化分必须落在 `[0, 1]`。
- `fusion_score` 只由 deterministic fusion 产出。

## 相关

- [[SearchScoringResult_t]]
- [[ScoreSearchResults]]
