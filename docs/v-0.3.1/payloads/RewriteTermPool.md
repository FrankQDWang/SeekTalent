# RewriteTermPool

一轮 execution 后产出的 rewrite evidence term pool。

```text
RewriteTermPool = {
  accepted,
  rejected
}
```

## Producer / Consumer

- Direct producer：`build_rewrite_term_pool(...)`
- Direct consumer：`SearchRoundArtifact.rewrite_term_pool`

## Invariants

- `accepted` 是 controller 可见的 rewrite 词源池
- `rejected` 只用于 trace explainability
- `accepted[*]` 与 `rejected[*]` 都必须保留来源候选与来源字段

## 最小示例

```yaml
accepted:
  - term: "ranking"
    source_candidate_ids: ["c32", "c44"]
    source_fields: ["project_names", "work_summaries"]
rejected:
  - term: "DeepSpeed"
    source_candidate_ids: ["c32", "c44"]
    source_fields: ["search_text", "work_summaries"]
    reason: "topic_drift"
```
