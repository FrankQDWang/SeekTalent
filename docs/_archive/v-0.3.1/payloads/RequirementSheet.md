# RequirementSheet

结构化后的岗位需求真相，是后续所有 canonical 读取的业务基线。

```text
RequirementSheet = { role_title, role_summary, must_have_capabilities, preferred_capabilities, exclusion_signals, hard_constraints, preferences, scoring_rationale }
```

## 稳定字段组

- 岗位标题：`role_title`
- 岗位摘要：`role_summary`
- 必须能力：`must_have_capabilities`
- 优先能力：`preferred_capabilities`
- 排除信号：`exclusion_signals`
- 硬约束：`hard_constraints: HardConstraints`
- 偏好槽位：`preferences: RequirementPreferences`
- 评分说明：`scoring_rationale`

## Direct Producer / Direct Consumers

- Direct producer：[[ExtractRequirements]]
- Direct consumers：[[RouteDomainKnowledgePack]]、[[FreezeScoringPolicy]]、[[GenerateBootstrapOutput]]、[[SelectActiveFrontierNode]]、[[MaterializeSearchExecutionPlan]]、[[EvaluateBranchOutcome]]、[[FinalizeSearchRun]]

## Invariants

- `RequirementSheet` 是运行期唯一允许传播的结构化需求真相。
- 后续模块可以补充解释，但不能改写这里的需求边界。
- 业务偏好必须通过 `BusinessPolicyPack` 进入主链，不能反写到 `RequirementSheet`。
- 所有字段都必须由 `ExtractRequirements` 显式生产，不允许下游猜默认值。

## 最小示例

```yaml
role_title: "Senior Python / LLM Engineer"
role_summary: "负责 Agent / RAG 类产品的后端与检索工程。"
must_have_capabilities:
  - "Python backend"
  - "LLM application"
  - "retrieval or ranking experience"
preferred_capabilities:
  - "workflow orchestration"
  - "to-b delivery"
exclusion_signals:
  - "pure algorithm research only"
hard_constraints:
  locations: ["Shanghai"]
  min_years: 5
  max_years: 10
  company_names: ["阿里巴巴", "蚂蚁集团"]
  school_names: ["复旦大学", "上海交通大学"]
  degree_requirement: "本科及以上"
  school_type_requirement: ["985", "211"]
  gender_requirement: null
  min_age: null
  max_age: 35
preferences:
  preferred_domains: ["enterprise ai"]
  preferred_backgrounds: ["search platform"]
scoring_rationale: "must-have 优先，先看 retrieval/ranking，再看业务落地。"
```

## 相关

- [[ExtractRequirements]]
- [[HardConstraints]]
- [[RequirementPreferences]]
