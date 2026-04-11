# ScoringPolicy

冻结后的评分口径，供排序与 shortlist 稳定复用。

```text
ScoringPolicy = { fit_gate_constraints, must_have_capabilities_snapshot, preferred_capabilities_snapshot, fusion_weights, penalty_weights, top_n_for_explanation, rerank_instruction, rerank_query_text, reranker_calibration_snapshot, ranking_audit_notes }
```

## 稳定字段组

- 过门槛约束：`fit_gate_constraints: FitGateConstraints`
- must-have 快照：`must_have_capabilities_snapshot`
- preferred 快照：`preferred_capabilities_snapshot`
- 融合权重：`fusion_weights`
- penalty 权重：`penalty_weights`
- 解释候选上限：`top_n_for_explanation`
- rerank 指令：`rerank_instruction`
- rerank 查询文本：`rerank_query_text`
- rerank 校准快照：`reranker_calibration_snapshot`
- 排序审计说明：`ranking_audit_notes`

## Direct Producer / Direct Consumers

- Direct producer：[[FreezeScoringPolicy]]
- Direct consumers：[[SelectActiveFrontierNode]]、[[ScoreSearchResults]]

## Invariants

- `ScoringPolicy` 在单次 run 内冻结，不允许中途漂移。
- `fit_gate_constraints` 只表达稳定门槛，不承担搜索控制。
- `must_have_capabilities_snapshot` 与 `preferred_capabilities_snapshot` 是排序层唯一读取的结构化需求快照，不允许靠 prompt 文本反推。
- `fusion_weights` 必须经过 `normalize_weights(...)` 后总和为 `1.0`。
- `rerank_instruction` 与 `rerank_query_text` 只服务 `RerankService` 的 text-only contract。
- `rerank_query_text` 由 `build_rerank_query_text(RequirementSheet)` 唯一生成，不允许在其他 operator 内散拼。
- `rerank_query_text` 是岗位目标的简洁自然语言表达，不是审计散文。
- `rerank_query_text` 当前包含 `role_title`、`role_summary`、must-have、job-relevant hard constraints（`locations / years / degree / company / school`）与短 preferred 补充。
- `rerank_query_text` 当前明确排除 `gender_requirement`、`min_age`、`max_age` 与 `exclusion_signals`。
- `reranker_calibration_snapshot` 必须来自 `RerankerCalibration` 的 run 内快照，而不是业务 prompt 自由生成。
- `ranking_audit_notes` 只服务审计与解释，不进入 reranker query。
- `penalty_weights.job_hop_confidence_floor` 以下的稳定性解析结果不得触发 penalty。

## 最小示例

```yaml
fit_gate_constraints:
  locations: ["Shanghai"]
  min_years: 6
  max_years: 10
  company_names: ["阿里巴巴", "蚂蚁集团"]
  school_names: ["复旦大学"]
  degree_requirement: "本科及以上"
  gender_requirement: null
  min_age: null
  max_age: 35
must_have_capabilities_snapshot:
  - "Python backend"
  - "LLM application"
  - "retrieval or ranking experience"
preferred_capabilities_snapshot:
  - "workflow orchestration"
  - "to-b delivery"
fusion_weights:
  rerank: 0.55
  must_have: 0.25
  preferred: 0.10
  risk_penalty: 0.10
penalty_weights:
  job_hop: 1.0
  job_hop_confidence_floor: 0.6
top_n_for_explanation: 5
rerank_instruction: "Given a hiring requirement, judge how well the candidate resume matches the role. Prioritize must-have capabilities, use preferred capabilities as secondary evidence, and do not over-penalize weak soft-risk signals."
rerank_query_text: "Hiring for Senior Python / LLM Engineer. Role summary: Build Python, LLM, and retrieval systems. Must have Python backend, LLM application, retrieval or ranking experience. Location: Shanghai. Minimum 6 years of experience. Degree requirement: 本科及以上. Target company background: 阿里巴巴, 蚂蚁集团. Target school background: 复旦大学. Preferred workflow orchestration, to-b delivery."
reranker_calibration_snapshot:
  model_id: "qwen3-8b-reranker"
  normalization: "sigmoid"
  temperature: 2.4
  offset: 0.0
  clip_min: -12
  clip_max: 12
  calibration_version: "2026-04-07-v1"
ranking_audit_notes: "must-have 优先于背景加分；低置信度稳定性风险不处罚。"
```

## 相关

- [[FreezeScoringPolicy]]
- [[FitGateConstraints]]
- [[RerankerCalibration]]
