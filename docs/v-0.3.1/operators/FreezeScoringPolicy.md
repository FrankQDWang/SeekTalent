# FreezeScoringPolicy

从 `RequirementSheet + BusinessPolicyPack + RerankerCalibration` 冻结出本次 run 的评分口径。

## Signature

```text
FreezeScoringPolicy : (RequirementSheet, BusinessPolicyPack, RerankerCalibration) -> ScoringPolicy
```

## Notation Legend

```text
B := BusinessPolicyPack
C := RerankerCalibration
R := RequirementSheet
P := ScoringPolicy
```

## Input Projection

```text
truth_gate_t = R.hard_constraints
override_gate_t = B.fit_gate_overrides
weight_pref_t = B.fusion_weight_preferences
stability_policy_t = B.stability_policy
explanation_pref_t = B.explanation_preferences
calibration_t = C
```

## Primitive Predicates / Matching Rules

```text
normalized(text) = trim(compress_whitespace(text))
```

```text
degree_rank("大专及以上") = 1
degree_rank("本科及以上") = 2
degree_rank("硕士及以上") = 3
degree_rank("博士及以上") = 4
degree_rank(null) = null
```

```text
normalized_weight(value, fallback) =
  value if value != null and value >= 0
  else fallback
```

```text
merged_allowlist_t(truth_values, override_values) =
  stable_deduplicate(override_values)
  if |truth_values| = 0
  else stable_deduplicate(truth_values)
  if |override_values| = 0
  else stable_deduplicate(truth_values ∩ override_values)
  if |truth_values ∩ override_values| > 0
  else stable_deduplicate(truth_values)
```

## Transformation

### Phase 1 — Fit Gate Merge

```text
fit_gate_locations_t =
  merged_allowlist_t(truth_gate_t.locations, override_gate_t.locations)

fit_gate_min_years_t =
  max(truth_gate_t.min_years, override_gate_t.min_years)
  if truth_gate_t.min_years != null and override_gate_t.min_years != null
  else coalesce(override_gate_t.min_years, truth_gate_t.min_years)

fit_gate_max_years_t =
  min(truth_gate_t.max_years, override_gate_t.max_years)
  if truth_gate_t.max_years != null and override_gate_t.max_years != null
  else coalesce(override_gate_t.max_years, truth_gate_t.max_years)

fit_gate_company_names_t =
  merged_allowlist_t(truth_gate_t.company_names, override_gate_t.company_names)

fit_gate_school_names_t =
  merged_allowlist_t(truth_gate_t.school_names, override_gate_t.school_names)

fit_gate_degree_requirement_t =
  truth_gate_t.degree_requirement
  if override_gate_t.degree_requirement = null
  else override_gate_t.degree_requirement
  if truth_gate_t.degree_requirement = null
  else
    truth_gate_t.degree_requirement
    if degree_rank(truth_gate_t.degree_requirement) >= degree_rank(override_gate_t.degree_requirement)
    else override_gate_t.degree_requirement

fit_gate_gender_requirement_t =
  truth_gate_t.gender_requirement
  if override_gate_t.gender_requirement = null
  else override_gate_t.gender_requirement
  if truth_gate_t.gender_requirement = null
  else truth_gate_t.gender_requirement
  if truth_gate_t.gender_requirement = override_gate_t.gender_requirement
  else truth_gate_t.gender_requirement

fit_gate_min_age_t =
  max(truth_gate_t.min_age, override_gate_t.min_age)
  if truth_gate_t.min_age != null and override_gate_t.min_age != null
  else coalesce(override_gate_t.min_age, truth_gate_t.min_age)

fit_gate_max_age_t =
  min(truth_gate_t.max_age, override_gate_t.max_age)
  if truth_gate_t.max_age != null and override_gate_t.max_age != null
  else coalesce(override_gate_t.max_age, truth_gate_t.max_age)
```

### Phase 2 — Weight Freeze

```text
raw_rerank_weight_t = normalized_weight(weight_pref_t.rerank, 0.55)
raw_must_weight_t = normalized_weight(weight_pref_t.must_have, 0.25)
raw_pref_weight_t = normalized_weight(weight_pref_t.preferred, 0.10)
raw_risk_weight_t = normalized_weight(weight_pref_t.risk_penalty, 0.10)

raw_weight_sum_t =
  raw_rerank_weight_t + raw_must_weight_t + raw_pref_weight_t + raw_risk_weight_t

fusion_weights_t = {
  rerank: raw_rerank_weight_t / raw_weight_sum_t,
  must_have: raw_must_weight_t / raw_weight_sum_t,
  preferred: raw_pref_weight_t / raw_weight_sum_t,
  risk_penalty: raw_risk_weight_t / raw_weight_sum_t
}

penalty_weights_t = {
  job_hop: coalesce(stability_policy_t.penalty_weight, 1.0),
  job_hop_confidence_floor: coalesce(stability_policy_t.confidence_floor, 0.6)
}

top_n_for_explanation_t =
  coalesce(explanation_pref_t.top_n_for_explanation, 5)
```

### Phase 3 — Rerank Surface Freeze

```text
rerank_instruction_t =
  normalized(
    "Given a hiring requirement, judge how well the candidate resume matches the role. "
    + "Prioritize must-have capabilities, use preferred capabilities as secondary evidence, "
    + "and do not over-penalize weak soft-risk signals."
  )

rerank_query_text_t = build_rerank_query_text(R)

ranking_audit_notes_t =
  normalized(
    coalesce(R.scoring_rationale, "")
    + " "
    + "must-have 优先于 preferred；低置信度稳定性风险不处罚。"
  )
```

其中：

```text
build_rerank_query_text(R) =
  sentence_join([
    "Hiring for " + R.role_title,
    "Role summary: " + R.role_summary
      if normalized(R.role_summary) != "" else "",
    "Must have " + join(", ", stable_deduplicate(R.must_have_capabilities))
      if |R.must_have_capabilities| > 0 else "",
    "Location: " + join(", ", stable_deduplicate(R.hard_constraints.locations))
      if |R.hard_constraints.locations| > 0 else "",
    "Minimum " + R.hard_constraints.min_years + " years of experience"
      if R.hard_constraints.min_years != null else "",
    "Maximum " + R.hard_constraints.max_years + " years of experience"
      if R.hard_constraints.max_years != null else "",
    "Degree requirement: " + R.hard_constraints.degree_requirement
      if R.hard_constraints.degree_requirement != null else "",
    "Target company background: " + join(", ", stable_deduplicate(R.hard_constraints.company_names))
      if |R.hard_constraints.company_names| > 0 else "",
    "Target school background: " + join(", ", stable_deduplicate(R.hard_constraints.school_names))
      if |R.hard_constraints.school_names| > 0 else "",
    "Preferred " + join(", ", stable_deduplicate(R.preferred_capabilities))
      if |R.preferred_capabilities| > 0 else ""
  ])
```

### Field-Level Output Assembly

```text
P.fit_gate_constraints = {
  locations: fit_gate_locations_t,
  min_years: fit_gate_min_years_t,
  max_years: fit_gate_max_years_t,
  company_names: fit_gate_company_names_t,
  school_names: fit_gate_school_names_t,
  degree_requirement: fit_gate_degree_requirement_t,
  gender_requirement: fit_gate_gender_requirement_t,
  min_age: fit_gate_min_age_t,
  max_age: fit_gate_max_age_t
}
P.must_have_capabilities_snapshot = R.must_have_capabilities
P.preferred_capabilities_snapshot = R.preferred_capabilities
P.fusion_weights = fusion_weights_t
P.penalty_weights = penalty_weights_t
P.top_n_for_explanation = top_n_for_explanation_t
P.rerank_instruction = rerank_instruction_t
P.rerank_query_text = rerank_query_text_t
P.reranker_calibration_snapshot = {
  model_id: calibration_t.model_id,
  normalization: calibration_t.normalization,
  temperature: calibration_t.temperature,
  offset: calibration_t.offset,
  clip_min: calibration_t.clip_min,
  clip_max: calibration_t.clip_max,
  calibration_version: calibration_t.calibration_version
}
P.ranking_audit_notes = ranking_audit_notes_t
```

## Defaults / Thresholds Used Here

```text
default fusion weights = {
  rerank: 0.55,
  must_have: 0.25,
  preferred: 0.10,
  risk_penalty: 0.10
}
```

```text
default stability penalty = {
  job_hop: 1.0,
  job_hop_confidence_floor: 0.6
}
```

```text
default top_n_for_explanation = 5
```

## Read Set

- `RequirementSheet.hard_constraints`
- `RequirementSheet.must_have_capabilities`
- `RequirementSheet.preferred_capabilities`
- `RequirementSheet.scoring_rationale`
- `RequirementSheet.role_title`
- `BusinessPolicyPack.fusion_weight_preferences`
- `BusinessPolicyPack.fit_gate_overrides`
- `BusinessPolicyPack.stability_policy`
- `BusinessPolicyPack.explanation_preferences`
- `RerankerCalibration.*`

## Write Set

- `ScoringPolicy.fit_gate_constraints`
- `ScoringPolicy.must_have_capabilities_snapshot`
- `ScoringPolicy.preferred_capabilities_snapshot`
- `ScoringPolicy.fusion_weights`
- `ScoringPolicy.penalty_weights`
- `ScoringPolicy.top_n_for_explanation`
- `ScoringPolicy.rerank_instruction`
- `ScoringPolicy.rerank_query_text`
- `ScoringPolicy.reranker_calibration_snapshot`
- `ScoringPolicy.ranking_audit_notes`

## 输入 payload

- [[RequirementSheet]]
- [[BusinessPolicyPack]]
- [[RerankerCalibration]]

## 输出 payload

- [[ScoringPolicy]]

## 不确定性边界 / 说明

- 本页冻结 run 内唯一评分口径；下游 branch 扩展不能再改权重、gate 或 rerank surface。
- `fit_gate_overrides` 只能收紧 truth gate；allowlist 类字段按交集收缩，冲突的 `gender_requirement` 保留 truth。
- 本页不读取知识卡或 routing 结果；知识库只参与 bootstrap 关键词初始化，不参与评分口径冻结。

## 相关

- [[operator-spec-style]]
- [[RequirementSheet]]
- [[BusinessPolicyPack]]
- [[RerankerCalibration]]
- [[ScoringPolicy]]
- [[FitGateConstraints]]
- [[scoring-semantics]]
