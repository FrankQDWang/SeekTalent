# ScoreSearchResults

在冻结评分口径下对搜索结果做 `rerank -> calibration -> deterministic fusion -> shortlist`。

## Signature

```text
ScoreSearchResults : (SearchExecutionResult_t, ScoringPolicy) -> SearchScoringResult_t
```

## Notation Legend

```text
P := ScoringPolicy
x_t := SearchExecutionResult_t
y_t := SearchScoringResult_t
C_t := x_t.scoring_candidates
c_i := C_t[i]
```

## Input Projection

```text
α = P.fusion_weights.rerank
β = P.fusion_weights.must_have
γ = P.fusion_weights.preferred
δ = P.fusion_weights.risk_penalty
k_explain = P.top_n_for_explanation
must_have_set_t = P.must_have_capabilities_snapshot
preferred_set_t = P.preferred_capabilities_snapshot
fit_gates_t = P.fit_gate_constraints
capability_signals_i = c_i.capability_signals
years_i = c_i.years_of_experience
age_i = c_i.age
gender_i = c_i.gender
work_summaries_i = c_i.work_experience_summaries
education_summaries_i = c_i.education_summaries
location_signals_i = c_i.location_signals
```

## Primitive Predicates / Matching Rules

```text
capability_hit_i(term) =
  1 if term is matched by c_i.scoring_text
      or allowlist_match(capability_signals_i, [term]) = 1
  else 0
```

```text
allowlist_match(text_set, allowlist) =
  1 if ∃ text in text_set, ∃ allow_term in allowlist :
        normalized(text) contains normalized(allow_term)
        or normalized(allow_term) contains normalized(text)
  else 0
```

```text
normalized(text) = trim(lowercase(text))
```

```text
degree_rank("大专") = 1
degree_rank("大专及以上") = 1
degree_rank("本科") = 2
degree_rank("本科及以上") = 2
degree_rank("硕士") = 3
degree_rank("硕士及以上") = 3
degree_rank("博士") = 4
degree_rank("博士及以上") = 4
degree_rank(null) = null
```

```text
highest_degree_rank_i =
  max({ degree_rank(d) | d parsed from education_summaries_i and degree_rank(d) != null })
  if at least one degree can be parsed
  else null
```

## Transformation

### Phase 1 — Text Conversion + Rerank Request

```text
rerank_request_t = {
  instruction: P.rerank_instruction,
  query: P.rerank_query_text,
  documents:
    [
      {candidate_id: c_i.candidate_id, text: c_i.scoring_text}
      for c_i in C_t
    ]
}

rerank_raw_scores_t = RerankService(rerank_request_t)
```

### Phase 2 — Calibration

若 `P.reranker_calibration_snapshot.normalization = "sigmoid"`，则：

```text
x_i = clip(
  rerank_raw_scores_t[c_i.candidate_id] + P.reranker_calibration_snapshot.offset,
  P.reranker_calibration_snapshot.clip_min,
  P.reranker_calibration_snapshot.clip_max
)

s_rerank_i =
  1 / (1 + exp(-(x_i / P.reranker_calibration_snapshot.temperature)))

rerank_normalized_scores_t[c_i.candidate_id] = s_rerank_i
```

### Phase 3 — Deterministic Signal Scoring

对每个 `c_i in C_t`：

```text
matched_must_count_i =
  Σ_{term in must_have_set_t} capability_hit_i(term)

matched_pref_count_i =
  Σ_{term in preferred_set_t} capability_hit_i(term)

s_must_raw_i =
  round(100 * matched_must_count_i / max(1, |must_have_set_t|))
s_must_i = s_must_raw_i / 100

s_pref_raw_i =
  round(100 * matched_pref_count_i / max(1, |preferred_set_t|))
s_pref_i = s_pref_raw_i / 100
```

```text
penalty_i =
  0
  if c_i.career_stability_profile.confidence_score
     < P.penalty_weights.job_hop_confidence_floor
  else
    min(
      1.0,
      min(
        1.0,
        c_i.career_stability_profile.short_tenure_count / 3
        + max(0, 18 - c_i.career_stability_profile.median_tenure_months) / 18
      )
      * P.penalty_weights.job_hop
    )
```

```text
s_risk_raw_i = round(100 * penalty_i)
s_risk_i = s_risk_raw_i / 100

risk_flags_i =
  ["frequent_job_changes"]
  if c_i.career_stability_profile.confidence_score
     >= P.penalty_weights.job_hop_confidence_floor
     and s_risk_raw_i > 0
  else []
```

```text
location_fit_i =
  1 if fit_gates_t.locations is empty
  else allowlist_match(location_signals_i, fit_gates_t.locations)

min_years_fit_i =
  1 if fit_gates_t.min_years = null
  else 1 if years_i = null
  else 1 if years_i >= fit_gates_t.min_years
  else 0

max_years_fit_i =
  1 if fit_gates_t.max_years = null
  else 1 if years_i = null
  else 1 if years_i <= fit_gates_t.max_years
  else 0

min_age_fit_i =
  1 if fit_gates_t.min_age = null
  else 1 if age_i = null
  else 1 if age_i >= fit_gates_t.min_age
  else 0

max_age_fit_i =
  1 if fit_gates_t.max_age = null
  else 1 if age_i = null
  else 1 if age_i <= fit_gates_t.max_age
  else 0

gender_fit_i =
  1 if fit_gates_t.gender_requirement = null
  else 1 if gender_i = null
  else 1 if normalized(gender_i) = normalized(fit_gates_t.gender_requirement)
  else 0

company_fit_i =
  1 if fit_gates_t.company_names is empty
  else allowlist_match(work_summaries_i, fit_gates_t.company_names)

school_fit_i =
  1 if fit_gates_t.school_names is empty
  else allowlist_match(education_summaries_i, fit_gates_t.school_names)

degree_fit_i =
  1 if fit_gates_t.degree_requirement = null
  else 1 if highest_degree_rank_i = null
  else
    1 if highest_degree_rank_i >= degree_rank(fit_gates_t.degree_requirement)
    else 0

fit_i =
  1
  if location_fit_i = 1
     and min_years_fit_i = 1
     and max_years_fit_i = 1
     and min_age_fit_i = 1
     and max_age_fit_i = 1
     and gender_fit_i = 1
     and company_fit_i = 1
     and school_fit_i = 1
     and degree_fit_i = 1
  else 0
```

### Phase 4 — Deterministic Fusion

```text
fusion_score_i =
  α * rerank_normalized_scores_t[c_i.candidate_id]
  + β * s_must_i
  + γ * s_pref_i
  - δ * s_risk_i
```

```text
ranked_score_cards_t =
  stable_sort_desc(
    [
      {
        candidate_id: c_i.candidate_id,
        rerank_raw: rerank_raw_scores_t[c_i.candidate_id],
        rerank_normalized: rerank_normalized_scores_t[c_i.candidate_id],
        must_have_match_score_raw: s_must_raw_i,
        must_have_match_score: s_must_i,
        preferred_match_score_raw: s_pref_raw_i,
        preferred_match_score: s_pref_i,
        risk_score_raw: s_risk_raw_i,
        risk_score: s_risk_i,
        risk_flags: risk_flags_i,
        fit: fit_i,
        fusion_score: fusion_score_i
      }
      for c_i in C_t
    ],
    key = fusion_score
  )
```

### Field-Level Output Assembly

```text
y_t.scored_candidates = ranked_score_cards_t

y_t.node_shortlist_candidate_ids =
  [row.candidate_id for row in ranked_score_cards_t if row.fit = 1]

y_t.explanation_candidate_ids =
  [row.candidate_id for row in ranked_score_cards_t[0 : k_explain]]

top_three_rows_t = ranked_score_cards_t[0 : 3]

y_t.top_three_statistics.average_fusion_score_top_three =
  0 if |top_three_rows_t| = 0
  else Σ_{row in top_three_rows_t} row.fusion_score / |top_three_rows_t|
```

## Defaults / Thresholds Used Here

```text
α = P.fusion_weights.rerank         (default 0.55 from FreezeScoringPolicy)
β = P.fusion_weights.must_have      (default 0.25 from FreezeScoringPolicy)
γ = P.fusion_weights.preferred      (default 0.10 from FreezeScoringPolicy)
δ = P.fusion_weights.risk_penalty   (default 0.10 from FreezeScoringPolicy)
```

```text
P.penalty_weights.job_hop_confidence_floor
  is run-frozen from BusinessPolicyPack.stability_policy.confidence_floor
  and commonly defaults to 0.6 under the default stability policy
```

```text
P.reranker_calibration_snapshot.temperature / offset / clip_min / clip_max
  come from runtime calibration registry;
  they are frozen per run but are not global platform constants
```

## Read Set

- `SearchExecutionResult_t.scoring_candidates`
- `ScoringPolicy.fit_gate_constraints`
- `ScoringPolicy.must_have_capabilities_snapshot`
- `ScoringPolicy.preferred_capabilities_snapshot`
- `ScoringPolicy.fusion_weights`
- `ScoringPolicy.penalty_weights`
- `ScoringPolicy.top_n_for_explanation`
- `ScoringPolicy.rerank_instruction`
- `ScoringPolicy.rerank_query_text`
- `ScoringPolicy.reranker_calibration_snapshot`

## Write Set

- `SearchScoringResult_t.scored_candidates`
- `SearchScoringResult_t.node_shortlist_candidate_ids`
- `SearchScoringResult_t.explanation_candidate_ids`
- `SearchScoringResult_t.top_three_statistics`

## 输入 payload

- [[SearchExecutionResult_t]]
- [[ScoringPolicy]]

## 输出 payload

- [[SearchScoringResult_t]]

## 不确定性边界 / 说明

- 黑盒边界只在 `RerankService(rerank_request_t)`；其余排序、校准、risk penalty 与 shortlist 组装都必须是 deterministic。
- `rerank_request_t` 必须满足 text-only contract：`instruction`、`query` 与 `documents[*].text` 都是自然语言文本。
- `ScoringCandidate_t.scoring_text` 在 rerank 语境下是候选自然文本表达，不是 JSON dump，也不承载任意结构化元数据序列化。
- `allowlist_match(...)`、`capability_hit_i(...)` 与 `degree_rank(...)` 是本页局部原子谓词，不是外部 helper owner。
- `*_score_raw` 都是 `[0,100]` 的审计值；进入 `fusion_score` 前必须除以 `100` 归一化到 `[0,1]`。
- fit gate 缺失候选侧信号时默认不判负；这和 `scoring-semantics` 当前 gate 口径保持一致。

## 相关

- [[SearchExecutionResult_t]]
- [[ScoringPolicy]]
- [[SearchScoringResult_t]]
- [[ScoringCandidate_t]]
- [[CareerStabilityProfile]]
- [[ScoredCandidate_t]]
- [[weights-and-thresholds-index]]
- [[operator-spec-style]]
- [[scoring-semantics]]
