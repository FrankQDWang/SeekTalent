# requirement-semantics

`ExtractRequirements` 使用的 deterministic helper 语义 owner。

## `clean_title`

- 输入：任意标题候选字符串
- 处理：去首尾空白、压缩连续空白、去掉开头的 `招聘` / `诚聘` / `急招` 等招聘前缀
- 输出：单行稳定标题字符串；空字符串返回 `null`

## `normalize_summary`

- 优先使用 `RequirementExtractionDraft.role_summary_candidate`
- 若草稿为空，则取 `job_description` 的首个非空句，再按需拼接 `hiring_notes`
- 最终统一做空白压缩，截断到 `240` 个字符

## `coerce_preference_map`

- 只保留 `preferred_domains` 与 `preferred_backgrounds`
- 值必须是字符串数组；空值删除，重复值保序去重
- 缺失字段回写为空数组

## `coerce_constraint_map`

- 只保留 `locations`、`min_years`、`max_years`、`company_names`、`school_names`、`degree_requirement`、`school_type_requirement`、`gender_requirement`、`min_age`、`max_age`
- `locations`、`company_names`、`school_names`、`school_type_requirement` 都必须是字符串数组并保序去重
- `min_years`、`max_years`、`min_age`、`max_age` 都必须是 `>= 0` 的整数；无法解析时回写 `null`
- `degree_requirement` 必须收敛为单个 canonical 文本值，固定集合为 `大专及以上 / 本科及以上 / 硕士及以上 / 博士及以上`；无法确定时回写 `null`
- `gender_requirement` 必须收敛为单个 canonical 文本值；无法确定时回写 `null`
- 若 `min_years` 与 `max_years` 同时存在且 `min_years > max_years`，则交换两者
- 若 `min_age` 与 `max_age` 同时存在且 `min_age > max_age`，则交换两者
- 空数组、空字符串与显式“不限”都按“该字段不设硬约束”处理

## `title_from`

- 从 `SearchInputTruth.job_description` 取第一个非空行
- 结果必须再经过 `clean_title(...)`

## 相关

- [[ExtractRequirements]]
- [[RequirementSheet]]
- [[RequirementPreferences]]
- [[HardConstraints]]
