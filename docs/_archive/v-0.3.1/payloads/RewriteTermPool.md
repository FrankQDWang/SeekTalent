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
- `accepted[*]` 必须按 `accepted_term_score` 降序稳定排序
- evidence terms 仍然只服务 rewrite，不直接扩张 CTS query

## 最小示例

```yaml
accepted:
  - term: "ranking"
    source_candidate_ids: ["c32", "c44"]
    source_fields: ["project_names", "work_summaries"]
    support_count: 2
    accepted_term_score: 5.3
    score_breakdown:
      support_score: 2.0
      candidate_quality_score: 0.9
      field_weight_score: 0.9
      must_have_bonus: 1.5
      anchor_bonus: 0.0
      pack_bonus: 0.0
      generic_penalty: 0.0
rejected:
  - term: "DeepSpeed"
    source_candidate_ids: ["c32", "c44"]
    source_fields: ["search_text", "work_summaries"]
    reason: "topic_drift"
```
