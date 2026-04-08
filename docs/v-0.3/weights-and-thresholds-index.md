# SeekTalent v0.3 Weights And Thresholds Index

> 本页是 `v0.3` 的一页式索引，集中汇总：
> 1. 已在规格中写死的系数与阈值
> 2. runtime 提供的默认值
> 3. run 级可配置的业务权重
> 4. calibration registry 提供的模型校准参数
>
> 本页只做索引与汇总，不替代原 owner。真正说了算的仍然是 `semantics/`、`runtime/`、`payloads/` 与对应 operator。

## 1. 先看这张表

| 类别 | 是否全局写死 | owner |
| --- | --- | --- |
| routing / card matching 系数 | 是 | [[retrieval-semantics]] |
| frontier 选点系数 | 是 | [[selection-plan-semantics]] |
| reward 合成系数 | 是 | [[reward-frontier-semantics]] |
| crossover / stop / budget 默认阈值 | 是，按 runtime 默认值 | `runtime/` |
| fusion 权重 | 有默认值，但可被业务配置覆盖 | [[FreezeScoringPolicy]] + [[BusinessPolicyPack]] |
| stability penalty 强度与置信度 floor | 有公式，但强度和 floor 可配置 | [[scoring-semantics]] + [[BusinessPolicyPack]] |
| reranker calibration 参数 | 不是全局常数，来自 calibration registry | [[RerankerCalibration]] |
| trace 里的具体数值 | 不是 owner，只是 case example | [[trace-index]] |

## 2. 固定写死的公式系数

### 2.1 Routing 与 Knowledge Retrieval

owner: [[retrieval-semantics]]

#### 领域路由 `pack_score`

```text
pack_score =
  4 * title_alias_hit_any
  + 3 * must_have_link_hits
  + 1 * preferred_link_hits
  - 3 * exclusion_conflicts
```

#### 路由判定阈值

- 单领域：`top1 >= 5` 且 `margin(top1, top2) >= 2`
- 双领域：`top1/top2 >= 5` 且 `margin < 2`
- 其他情况：进入 `generic_fallback`

#### 路由置信度

- `explicit_domain = 1.0`
- 单领域 `inferred_domain = 0.8`
- 双领域 `inferred_domain = 0.7`
- `generic_fallback = 0.3`

#### 领域卡排序 `card_score`

```text
card_score =
  4 * title_or_alias_overlap
  + 2 * must_have_overlap
  + 1 * preferred_overlap
  + 1 * query_term_overlap
  - 2 * negative_signal_conflict
```

### 2.2 Frontier 选点与 donor 打包

owner: [[selection-plan-semantics]]

#### `frontier_priority`

```text
frontier_priority(n) =
  1.5 if n.parent_frontier_node_id = null else 0.0
  + 0.8 * operator_average_reward(n.selected_operator_name)
  + 0.4 if n.previous_branch_evaluation = null else 0.0
  - 1.0 if n.previous_branch_evaluation.branch_exhausted else 0.0
```

#### `unmet_requirement_bonus`

- 每个当前 query pool 尚未触达的 must-have：`+0.6`

#### `saturation_penalty`

```text
saturation_penalty = 1.2 * overlap_ratio
```

#### `compute_unmet_requirement_weights`

- 尚未覆盖的 must-have：`1.0`
- 已覆盖但仍可能补强的 must-have：`0.3`
- 输出形状：保序 `list[{capability, weight}]`

### 2.3 排序层固定公式

owners: [[scoring-semantics]]、[[ScoreSearchResults]]

#### `normalize_weights`

- 只接受 `rerank / must_have / preferred / risk_penalty`
- 缺失键补默认值
- 最终总和归一化到 `1.0`

#### `calibrate_scores`

```text
x = clip(raw + offset, clip_min, clip_max)
normalized = 1 / (1 + exp(-(x / temperature)))   # sigmoid mode
```

#### `deterministic_must_have_score_raw`

```text
raw = round(100 * matched_count / max(1, total_must_have_count))
```

#### `deterministic_preferred_score_raw`

- 与 must-have 同法，只是输入换成 preferred snapshot

#### `stability_penalty`

```text
base = min(1.0, short_tenure_count / 3 + max(0, 18 - median_tenure_months) / 18)
penalty = min(1.0, base * P.penalty_weights.job_hop)
```

前置 guard：

- 若 `confidence_score < job_hop_confidence_floor`，直接返回 `0`

#### `deterministic_risk_score_raw`

```text
raw = round(100 * stability_penalty(...))
```

### 2.4 Reward 合成

owner: [[reward-frontier-semantics]]

#### `search_cost_penalty`

```text
cost_penalty = min(1.0, 0.15 * pages_fetched)
```

#### `reward_score`

```text
reward_score =
  2.0 * delta_top_three
  + 1.5 * must_have_gain
  + 0.6 * new_fit_yield
  + 0.5 * novelty
  + 0.5 * usefulness
  + 0.4 * diversity
  - 0.8 * stability_risk_penalty
  - 1.0 * hard_constraint_violation
  - 0.6 * duplicate_penalty
  - 0.4 * cost_penalty
```

#### `accumulate_operator_statistics`

```text
average_reward = ((old_average * old_times) + reward_score) / times_selected
```

## 3. runtime 默认值

这些值不是“业务偏好”，而是 `v0.3` runtime 默认配置。

### 3.1 Knowledge Retrieval Budget

owner: [[KnowledgeRetrievalBudget]]

```yaml
max_cards: 8
max_inferred_domain_packs: 2
```

### 3.2 Search Budget

owner: [[RuntimeSearchBudget]]

```yaml
initial_round_budget: 5
default_target_new_candidate_count: 10
max_target_new_candidate_count: 20
```

### 3.3 Term Budget Policy

owner: [[RuntimeTermBudgetPolicy]]

```yaml
high_budget_range: [2, 6]
medium_budget_range: [2, 5]
low_budget_range: [2, 4]
```

分层规则：

- `remaining_budget >= 4`：`high_budget_range`
- `remaining_budget in [2, 3]`：`medium_budget_range`
- `remaining_budget <= 1`：`low_budget_range`

### 3.4 Crossover Guard Thresholds

owner: [[CrossoverGuardThresholds]]

```yaml
min_shared_anchor_terms: 1
min_reward_score: 1.5
max_donor_candidates: 2
```

### 3.5 Stop Guard Thresholds

owner: [[StopGuardThresholds]]

```yaml
novelty_floor: 0.25
usefulness_floor: 0.25
reward_floor: 1.5
min_round_index: 2
```

## 4. run 级可配置项

这些值在 `v0.3` 里语义已经明确，但具体数值不一定全局固定；它们可以按业务包或 run 配置变化。

### 4.1 Fusion Weights

owners: [[BusinessPolicyPack]]、[[FreezeScoringPolicy]]

默认值：

```yaml
rerank: 0.55
must_have: 0.25
preferred: 0.10
risk_penalty: 0.10
```

说明：

- 这组默认值会经过 `normalize_weights(...)`
- 允许被 `BusinessPolicyPack.fusion_weight_preferences` 覆盖
- 因此它们是“默认值明确”，不是“全平台唯一常数”

### 4.2 Stability Policy

owners: [[BusinessPolicyPack]]、[[scoring-semantics]]

示例默认：

```yaml
penalty_weight: 1.0
confidence_floor: 0.6
```

说明：

- `penalty_weight` 决定稳定性风险的最终力度
- `confidence_floor` 决定何时允许真正处罚
- 公式固定，但这两个数可按业务策略调整

### 4.3 Fit Gate Overrides

owners: [[BusinessPolicyPack]]、[[scoring-semantics]]

说明：

- 这不是一组单独“权重”，而是一类可配置 gate 收紧器
- 它只能收紧 truth gate，不能放宽 `RequirementSheet.hard_constraints`

### 4.4 Explanation Preferences

owners: [[BusinessPolicyPack]]、[[ScoringPolicy]]

示例默认：

```yaml
top_n_for_explanation: 5
```

说明：

- 它影响 explanation surface 的候选上限
- 不改变 shortlist 排序事实

## 5. calibration registry 提供的参数

owner: [[RerankerCalibration]]

这些参数不是业务层权重，而是模型校准快照。

示例：

```yaml
normalization: "sigmoid"
temperature: 2.4
offset: 0.0
clip_min: -12
clip_max: 12
```

说明：

- `temperature / offset / clip` 属于 calibration registry
- 它们会被 `FreezeScoringPolicy` snapshot 进 `ScoringPolicy`
- 同一 `calibration_version` 下，相同 raw score 必须映射到稳定 normalized score

## 6. 哪些值是“公式写死”，哪些只是“示例”

### 6.1 公式写死

- `retrieval-semantics` 里的 `pack_score / card_score / routing_confidence`
- `selection-plan-semantics` 里的 `frontier_priority / unmet_requirement_bonus / saturation_penalty / unmet_requirement_weights`
- `reward-frontier-semantics` 里的 `search_cost_penalty / reward_score`

### 6.2 默认值明确，但允许配置覆盖

- `fusion_weight_preferences`
- `stability_policy.penalty_weight`
- `stability_policy.confidence_floor`
- `top_n_for_explanation`
- 所有 `runtime/` 里的默认预算与 guard

### 6.3 只是示例，不是 owner

- [[trace-index]]
- paired `Agent Trace / Business Trace`
- payload 最小示例中的数值

## 7. 当前实现者最该盯住的地方

如果目标是“按 `v0.3` 直接开工，不在权重上犹豫”，实现时优先看这 6 处：

1. [[retrieval-semantics]]
2. [[selection-plan-semantics]]
3. [[scoring-semantics]]
4. [[reward-frontier-semantics]]
5. `runtime/` 下的 5 份默认值文档
6. [[RerankerCalibration]]

## 相关

- [[design]]
- [[workflow-explained]]
- [[evaluation]]
- [[implementation-checklist]]
- [[trace-spec]]
- [[trace-index]]
- [[retrieval-semantics]]
- [[selection-plan-semantics]]
- [[scoring-semantics]]
- [[reward-frontier-semantics]]
- [[KnowledgeRetrievalBudget]]
- [[RuntimeSearchBudget]]
- [[RuntimeTermBudgetPolicy]]
- [[CrossoverGuardThresholds]]
- [[StopGuardThresholds]]
- [[BusinessPolicyPack]]
- [[RerankerCalibration]]
