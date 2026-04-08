# ExecuteSearchPlan

执行单次 CTS 搜索计划，返回候选与页面统计。

## Signature

```text
ExecuteSearchPlan : SearchExecutionPlan_t -> SearchExecutionResult_t
```

## Notation Legend

```text
p_t := SearchExecutionPlan_t
x_t := SearchExecutionResult_t
```

## Input Projection

```text
query_terms_t = p_t.query_terms
projected_filters_t = p_t.projected_filters
runtime_constraints_t = p_t.runtime_only_constraints
target_new_t = p_t.target_new_candidate_count
derived_position_t = p_t.derived_position
derived_work_content_t = p_t.derived_work_content
```

## Primitive Predicates / Matching Rules

```text
normalized(text) = trim(lowercase(text))
```

```text
candidate_text_t(r_i) =
  normalized(
    r_i.search_text
    + " "
    + join(" ", r_i.work_summaries)
    + " "
    + join(" ", r_i.project_names)
  )
```

```text
negative_hit_t(r_i) =
  1 if ∃ term_t in runtime_constraints_t.negative_keywords :
        normalized(term_t) != ""
        and candidate_text_t(r_i) contains normalized(term_t)
  else 0
```

```text
must_have_audit_tags_t(r_i) =
  [
    term_t
    for term_t in runtime_constraints_t.must_have_keywords
    if normalized(term_t) != ""
       and candidate_text_t(r_i) contains normalized(term_t)
  ]
```

```text
stability_profile_t(r_i) =
  deterministic CareerStabilityProfile derived from
  r_i.work_experience_summaries by runtime timeline parsing
```

## Transformation

### Phase 1 — CTS Request

```text
cts_request_t = {
  query_terms: query_terms_t,
  projected_filters: projected_filters_t,
  derived_position: derived_position_t,
  derived_work_content: derived_work_content_t,
  target_new_candidate_count: target_new_t
}

raw_candidates_t = CTS.search(cts_request_t)
```

### Phase 2 — Runtime-Only Filtering

```text
runtime_filtered_candidates_t =
  [
    r_i
    for r_i in raw_candidates_t
    if negative_hit_t(r_i) = 0
  ]
```

```text
runtime_audit_tags_t =
  {
    r_i.candidate_id: must_have_audit_tags_t(r_i)
    for r_i in runtime_filtered_candidates_t
  }
```

### Phase 3 — Dedup and Scoring View Projection

```text
deduplicated_candidates_t =
  [
    runtime_filtered_candidates_t[index_t]
    for index_t in range(0, |runtime_filtered_candidates_t|)
    if runtime_filtered_candidates_t[index_t].candidate_id
       not in {
         runtime_filtered_candidates_t[j].candidate_id
         for j in range(0, index_t)
       }
  ]
```

```text
scoring_candidates_t =
  [
    {
      candidate_id: r_i.candidate_id,
      scoring_text: r_i.search_text,
      capability_signals:
        stable_deduplicate(drop_empty(r_i.project_names + r_i.work_summaries)),
      years_of_experience: r_i.years_of_experience_raw,
      age: r_i.age,
      gender: r_i.gender,
      location_signals:
        stable_deduplicate(drop_empty([r_i.now_location, r_i.expected_location])),
      work_experience_summaries: r_i.work_experience_summaries,
      education_summaries: r_i.education_summaries,
      career_stability_profile: stability_profile_t(r_i)
    }
    for r_i in deduplicated_candidates_t
  ]
```

### Field-Level Output Assembly

```text
x_t.raw_candidates = raw_candidates_t
x_t.deduplicated_candidates = deduplicated_candidates_t
x_t.scoring_candidates = scoring_candidates_t
x_t.search_page_statistics = {
  pages_fetched: ceil(|raw_candidates_t| / max(1, target_new_t)),
  duplicate_rate: 1 - |deduplicated_candidates_t| / max(1, |raw_candidates_t|),
  latency_ms: runtime_observed_latency_ms
}
x_t.search_observation = {
  unique_candidate_ids: [r_i.candidate_id for r_i in deduplicated_candidates_t],
  shortage_after_last_page: |deduplicated_candidates_t| < target_new_t
}
```

## Defaults / Thresholds Used Here

```text
apply_runtime_only_constraints order is fixed:
negative keyword filter -> must-have audit tagging -> candidate_id dedup
```

## Read Set

- `SearchExecutionPlan_t.query_terms`
- `SearchExecutionPlan_t.projected_filters`
- `SearchExecutionPlan_t.runtime_only_constraints`
- `SearchExecutionPlan_t.target_new_candidate_count`
- `SearchExecutionPlan_t.derived_position`
- `SearchExecutionPlan_t.derived_work_content`

## Write Set

- `SearchExecutionResult_t.raw_candidates`
- `SearchExecutionResult_t.deduplicated_candidates`
- `SearchExecutionResult_t.scoring_candidates`
- `SearchExecutionResult_t.search_page_statistics`
- `SearchExecutionResult_t.search_observation`

## 输入 payload

- [[SearchExecutionPlan_t]]

## 输出 payload

- [[SearchExecutionResult_t]]

## 不确定性边界 / 说明

- `CTS.search(cts_request_t)` 是这里唯一外部系统黑盒；地点 dispatch、分页补拉与协议级 enum 转码继续复用现有 runtime / adapter 能力。
- `CTS.search(cts_request_t)` 只能读取 `SearchExecutionPlan_t`；不得再回头读取 `RequirementSheet` 来补 `position` / `workContent`。
- `runtime_audit_tags_t` 只承担本轮 runtime 审计，不进入稳定 payload。

## 相关

- [[operator-spec-style]]
- [[SearchExecutionPlan_t]]
- [[SearchExecutionResult_t]]
- [[RetrievedCandidate_t]]
- [[ScoringCandidate_t]]
- [[SearchPageStatistics]]
- [[SearchObservation]]
- [[cts-projection-policy]]
