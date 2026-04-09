# RequirementExtractionDraft

`RequirementExtractionLLM` 生成的需求抽取草稿。

```text
RequirementExtractionDraft = {
  role_title_candidate,
  role_summary_candidate,
  must_have_capability_candidates,
  preferred_capability_candidates,
  exclusion_signal_candidates,
  preference_candidates,
  hard_constraint_candidates,
  scoring_rationale_candidate
}
```

## 稳定字段组

- 岗位标题草稿：`role_title_candidate`
- 岗位摘要草稿：`role_summary_candidate`
- must-have 候选列表：`must_have_capability_candidates`
- preferred 候选列表：`preferred_capability_candidates`
- 排除信号候选列表：`exclusion_signal_candidates`
- 偏好候选：`preference_candidates`
- 硬约束候选列表：`hard_constraint_candidates`
- 评分说明草稿：`scoring_rationale_candidate`

## Direct Producer / Direct Consumers

- Direct producer：RequirementExtractionLLM
- Direct consumers：[[ExtractRequirements]]

## Invariants

- `RequirementExtractionDraft` 只是 LLM 草稿，不是最终业务真相。
- 必须经过 `ExtractRequirements` 的 deterministic normalization 才能进入主链。
- 它不得直接写出 `knowledge_pack_id_override` 或 routing 结果。

## Implementation Surface

- Phase 2+ 默认使用 `pydantic-ai` 实现 `RequirementExtractionLLM`，但它只作为 typed request/response wrapper。
- 调用方式固定为 `fresh request`：使用 `instructions` 承载调用点级规则，`SearchInputTruth` 作为当前 user content，默认不继承任何 message history。
- 输出模式固定为 `NativeOutput` strict schema；`allow_text_output = false`、`allow_image_output = false`。
- 禁用 `function_tools`、`builtin_tools`、任意 MCP/tool calling 与 fallback model chain。
- retry 只允许用于 schema / output parse failure；默认不额外加业务型 validator retry。

## 最小示例

```yaml
role_title_candidate: "Senior Python / LLM Engineer"
role_summary_candidate: "负责 Agent / RAG 类产品的后端与检索工程。"
must_have_capability_candidates:
  - "Python backend"
  - "LLM application"
  - "retrieval or ranking experience"
preferred_capability_candidates:
  - "workflow orchestration"
  - "to-b delivery"
exclusion_signal_candidates:
  - "pure algorithm research only"
preference_candidates:
  preferred_domains: ["enterprise ai"]
  preferred_backgrounds: ["search platform"]
hard_constraint_candidates:
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
scoring_rationale_candidate: "must-have 先过，再看 ranking 背景"
```

## 相关

- [[SearchInputTruth]]
- [[ExtractRequirements]]
- [[RequirementSheet]]
