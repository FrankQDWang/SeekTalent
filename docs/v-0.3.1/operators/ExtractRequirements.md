# ExtractRequirements

把原始输入和需求抽取草稿固化为业务真相 `RequirementSheet`。

## Signature

```text
ExtractRequirements : (SearchInputTruth, RequirementExtractionDraft) -> RequirementSheet
```

## Notation Legend

```text
SIT := SearchInputTruth
D := RequirementExtractionDraft
R := RequirementSheet
```

## Input Projection

```text
job_description_t = SIT.job_description
hiring_notes_t = SIT.hiring_notes
role_title_candidate_t = D.role_title_candidate
role_summary_candidate_t = D.role_summary_candidate
must_have_candidates_t = D.must_have_capability_candidates
preferred_candidates_t = D.preferred_capability_candidates
exclusion_candidates_t = D.exclusion_signal_candidates
preference_candidates_t = D.preference_candidates
hard_constraint_candidates_t = D.hard_constraint_candidates
scoring_rationale_candidate_t = D.scoring_rationale_candidate
```

## Primitive Predicates / Matching Rules

```text
normalized_text(text) =
  trim(compress_whitespace(text))
```

```text
non_empty_list(values) =
  [normalized_text(v) for v in values if normalized_text(v) != ""]
```

```text
deduplicated_list(values) =
  stable_deduplicate(non_empty_list(values))
```

```text
cleaned_title(text) =
  null
  if normalized_text(text) = ""
  else
    normalized_text(text)
    with leading prefixes {"招聘", "诚聘", "急招"} removed once
```

```text
canonical_degree(text) =
  null if normalized_text(text) in {"", "不限"}
  else "博士及以上" if normalized_text(text) contains "博士"
  else "硕士及以上" if normalized_text(text) contains "硕士"
  else "本科及以上" if normalized_text(text) contains "本科"
  else "大专及以上" if normalized_text(text) contains "大专"
  else null
```

```text
canonical_gender(text) =
  null if normalized_text(text) in {"", "不限"}
  else "男" if normalized_text(text) contains "男"
  else "女" if normalized_text(text) contains "女"
  else null
```

```text
non_negative_int_or_null(value) =
  parsed_integer(value) if value can be parsed and parsed_integer(value) >= 0
  else null
```

```text
first_non_empty_line(text) =
  first(line in split_lines(text) where normalized_text(line) != "")
  else ""
```

```text
first_non_empty_sentence(text) =
  first(
    segment in split_on_any(text, ["。", "！", "？", ".", "!", "?", "\n"])
    where normalized_text(segment) != ""
  )
  else ""
```

## Transformation

### Phase 1 — Draft Cleanup

```text
draft_role_title_t = cleaned_title(role_title_candidate_t)

fallback_role_title_t = cleaned_title(first_non_empty_line(job_description_t))

draft_role_summary_t =
  normalized_text(role_summary_candidate_t)
  if normalized_text(role_summary_candidate_t) != ""
  else
    substring(
      normalized_text(
        first_non_empty_sentence(job_description_t)
        + " "
        + normalized_text(hiring_notes_t)
      ),
      0,
      240
    )

draft_must_have_capabilities_t = deduplicated_list(must_have_candidates_t)
draft_preferred_capabilities_t = deduplicated_list(preferred_candidates_t)
draft_exclusion_signals_t = deduplicated_list(exclusion_candidates_t)
draft_scoring_rationale_t = normalized_text(scoring_rationale_candidate_t)
```

### Phase 2 — Preference Projection

```text
draft_preferred_domains_t =
  deduplicated_list(preference_candidates_t.preferred_domains)

draft_preferred_backgrounds_t =
  deduplicated_list(preference_candidates_t.preferred_backgrounds)
```

### Phase 3 — Hard Constraint Projection

```text
locations_t = deduplicated_list(hard_constraint_candidates_t.locations)
company_names_t = deduplicated_list(hard_constraint_candidates_t.company_names)
school_names_t = deduplicated_list(hard_constraint_candidates_t.school_names)

min_years_t = non_negative_int_or_null(hard_constraint_candidates_t.min_years)
max_years_t = non_negative_int_or_null(hard_constraint_candidates_t.max_years)
min_age_t = non_negative_int_or_null(hard_constraint_candidates_t.min_age)
max_age_t = non_negative_int_or_null(hard_constraint_candidates_t.max_age)

degree_requirement_t = canonical_degree(hard_constraint_candidates_t.degree_requirement)
school_type_requirement_t =
  deduplicated_list(hard_constraint_candidates_t.school_type_requirement)
gender_requirement_t = canonical_gender(hard_constraint_candidates_t.gender_requirement)
```

```text
normalized_min_years_t =
  min_years_t if max_years_t = null or min_years_t = null or min_years_t <= max_years_t
  else max_years_t

normalized_max_years_t =
  max_years_t if max_years_t = null or min_years_t = null or min_years_t <= max_years_t
  else min_years_t

normalized_min_age_t =
  min_age_t if max_age_t = null or min_age_t = null or min_age_t <= max_age_t
  else max_age_t

normalized_max_age_t =
  max_age_t if max_age_t = null or min_age_t = null or min_age_t <= max_age_t
  else min_age_t
```

### Field-Level Output Assembly

```text
R.role_title = coalesce(draft_role_title_t, fallback_role_title_t)
R.role_summary = draft_role_summary_t
R.must_have_capabilities = draft_must_have_capabilities_t
R.preferred_capabilities = draft_preferred_capabilities_t
R.exclusion_signals = draft_exclusion_signals_t
R.preferences = {
  preferred_domains: draft_preferred_domains_t,
  preferred_backgrounds: draft_preferred_backgrounds_t
}
R.hard_constraints = {
  locations: locations_t,
  min_years: normalized_min_years_t,
  max_years: normalized_max_years_t,
  company_names: company_names_t,
  school_names: school_names_t,
  degree_requirement: degree_requirement_t,
  school_type_requirement: school_type_requirement_t,
  gender_requirement: gender_requirement_t,
  min_age: normalized_min_age_t,
  max_age: normalized_max_age_t
}
R.scoring_rationale = draft_scoring_rationale_t
```

## Defaults / Thresholds Used Here

```text
role_summary is truncated to at most 240 characters
after whitespace normalization.
```

```text
empty arrays, empty strings, and explicit "不限"
collapse to [] or null instead of creating a hard constraint.
```

## Read Set

- `SearchInputTruth.job_description`
- `SearchInputTruth.hiring_notes`
- `RequirementExtractionDraft.role_title_candidate`
- `RequirementExtractionDraft.role_summary_candidate`
- `RequirementExtractionDraft.must_have_capability_candidates`
- `RequirementExtractionDraft.preferred_capability_candidates`
- `RequirementExtractionDraft.exclusion_signal_candidates`
- `RequirementExtractionDraft.preference_candidates`
- `RequirementExtractionDraft.hard_constraint_candidates`
- `RequirementExtractionDraft.scoring_rationale_candidate`

## Write Set

- `RequirementSheet.role_title`
- `RequirementSheet.role_summary`
- `RequirementSheet.must_have_capabilities`
- `RequirementSheet.preferred_capabilities`
- `RequirementSheet.exclusion_signals`
- `RequirementSheet.hard_constraints`
- `RequirementSheet.preferences`
- `RequirementSheet.scoring_rationale`

## 输入 payload

- [[SearchInputTruth]]
- [[RequirementExtractionDraft]]

## 输出 payload

- [[RequirementSheet]]

## 不确定性边界 / 说明

- `ExtractRequirements` 自身不调用 LLM；LLM 黑盒已经停留在 `RequirementExtractionDraft` 上游。
- 本页只把草稿固化为唯一业务真相，不引入第二份 truth。

## 相关

- [[operator-spec-style]]
- [[SearchInputTruth]]
- [[RequirementExtractionDraft]]
- [[RequirementSheet]]
- [[RequirementPreferences]]
- [[HardConstraints]]
- [[requirement-semantics]]
