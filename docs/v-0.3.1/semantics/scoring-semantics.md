# scoring-semantics

`FreezeScoringPolicy` 与 `ScoreSearchResults` 使用的 deterministic helper 语义 owner。

## `merge_fit_gates`

- 只对 `HardConstraints` 中具备稳定候选侧信号的字段产出 gate；当前固定为 `locations`、`min_years`、`max_years`、`company_names`、`school_names`、`degree_requirement`、`gender_requirement`、`min_age`、`max_age`
- `locations / company_names / school_names`：若 truth 与 override 都非空，取交集；若交集为空则保留 truth；若其中一侧为空则取另一侧
- `min_years / min_age`：取 `max(truth, override)`；不能放宽 truth
- `max_years / max_age`：取 `min(non_null_values)`；不能放宽 truth
- `degree_requirement`：按 `null < 大专及以上 < 本科及以上 < 硕士及以上 < 博士及以上` 的最低学历顺序取更严格的一侧；显式“不限”在需求抽取阶段已折叠为 `null`
- `gender_requirement`：若 truth 为空则接受 override；若 override 为空则保留 truth；两者冲突时保留 truth
- `school_type_requirement` 默认不进入 fit gate，因为当前 `v0.3.1` 不假设存在稳定候选侧 school-type tag

## `normalize_weights`

- 只接受 `rerank / must_have / preferred / risk_penalty`
- 缺失键补默认值
- 最终归一化到总和 `1.0`

## `render_rerank_instruction`

- 固定输出英文 instruction
- 只表达招聘相关性排序任务、must-have 优先、preferred 次级、soft risk 不做 hard reject
- 不重复拼接完整岗位 query，不承载候选文本

## `build_rerank_query_text`

- owner：`seektalent.rerank_text.build_rerank_query_text(RequirementSheet) -> str`
- 顺序固定为：`role_title -> role_summary -> must-have -> 必要硬约束摘要 -> 短 preferred 补充`
- 输出按短句拼接，句尾稳定补 `.`，不输出 schema label dump
- `must-have` 必须完整保留原义，可中英混合
- 硬约束摘要当前只允许 job-relevant 且稳定、短文本的约束，如 `locations`、`min_years`、`max_years`、`degree_requirement`、`company_names`、`school_names`
- `gender_requirement`、`min_age`、`max_age`、`exclusion_signals` 不进入 rerank query
- `preferred` 只允许 1 句短补充，不得盖过 must-have
- 输出必须是自然语言 query text，不是审计散文

## `synthesize_ranking_audit_notes`

- 固定输出 1 段短说明，强调 must-have 优先、preferred 次级、低置信度稳定性不处罚
- 只服务 explanation / audit，不参与 rerank request

## `calibrate_scores`

- `x = clip(raw + offset, clip_min, clip_max)`
- `sigmoid` 模式：`normalized = 1 / (1 + exp(-(x / temperature)))`

## `deterministic_must_have_score_raw`

- 对 `RequirementSheet.must_have_capabilities` 做逐项命中
- 文本命中 owner 与 runtime 共用 `seektalent.query_terms.query_terms_hit(...)`
- 命中来源只允许 `candidate_t.scoring_text`、标准化 skill/title tokens、结构化标签
- `candidate_t.scoring_text` 在 rerank 和 deterministic scoring 两个语境下都指候选自然文本表达，不是 JSON dump
- `raw = round(100 * matched_count / max(1, total_must_have_count))`

## `deterministic_preferred_score_raw`

- 与 must-have 同法，但读取 `preferred_capabilities`
- 文本命中语义与 must-have、selection、rewrite evidence 保持同源，不允许保留 scoring 专用 substring matcher

## `stability_penalty`

- 若 `candidate_t.career_stability_profile.confidence_score < P.penalty_weights.job_hop_confidence_floor`，返回 `0`
- 否则：
  - `base = min(1.0, short_tenure_count / 3 + max(0, 18 - median_tenure_months) / 18)`
  - `penalty = min(1.0, base * P.penalty_weights.job_hop)`

## `deterministic_risk_score_raw`

- `raw = round(100 * stability_penalty(...))`

## `risk_flags_from`

- 只有在 confidence 过线时才允许输出 `frequent_job_changes`

## `passes_fit_gate`

- `locations` 不匹配则 `fit = 0`
- `years_of_experience < min_years` 则 `fit = 0`
- `years_of_experience > max_years` 则 `fit = 0`
- `age < min_age` 或 `age > max_age` 则 `fit = 0`
- `gender_requirement` 非空且 `candidate_t.gender` 明确不匹配时，`fit = 0`
- `company_names` 非空且 `candidate_t.work_experience_summaries` 中没有任何 token-aware allowlist 命中时，`fit = 0`
- `school_names` 非空且 `candidate_t.education_summaries` 中没有任何 token-aware allowlist 命中时，`fit = 0`
- `degree_requirement` 非空且能从 `candidate_t.education_summaries` 解析出最高学历，但该学历低于 gate 时，`fit = 0`
- 以上判断都只基于稳定结构化字段或可审计文本命中；缺失或无法解析的候选侧信号不自动判负
- 其余情况 `fit = 1`

## `top_k_fit_candidate_ids`

- 返回所有 `fit = 1` 的候选 id，保持 `fusion_score` 稳定顺序

## `top_n_candidate_ids`

- 返回排序前 `limit` 个候选 id，不要求 `fit = 1`

## `average_fusion_score`

- 对输入候选的 `fusion_score` 做算术平均
- 空输入返回 `0`

## 相关

- [[FreezeScoringPolicy]]
- [[ScoreSearchResults]]
- [[ScoringPolicy]]
- [[ScoredCandidate_t]]
