# SearchInputTruth

运行入口接收的原始业务输入真相。

```text
SearchInputTruth = { job_description, hiring_notes, job_description_sha256, hiring_notes_sha256 }
```

## 稳定字段组

- 岗位正文：`job_description`
- 补充说明：`hiring_notes`
- 岗位正文哈希：`job_description_sha256`
- 补充说明哈希：`hiring_notes_sha256`

## Direct Producer / Direct Consumers

- Direct producer：CLI / runtime bootstrap
- Direct consumers：[[ExtractRequirements]]

## Invariants

- `SearchInputTruth` 是整次运行的唯一原始需求来源。
- 结构化对象只能从这里派生，不能绕过它补写真相。

## 最小示例

```yaml
job_description: "Senior Python / LLM Engineer"
hiring_notes: "必须有 retrieval 或 ranking 经验，地点在上海。"
job_description_sha256: "sha256:job"
hiring_notes_sha256: "sha256:notes"
```

## 相关

- [[operator-map]]
- [[ExtractRequirements]]
- [[RequirementExtractionDraft]]
- [[RequirementSheet]]
