# RewriteTermCandidate

单个被接受的 rewrite evidence term。

```text
RewriteTermCandidate = {
  term,
  source_candidate_ids,
  source_fields,
  support_count,
  accepted_term_score,
  score_breakdown
}
```

## Producer / Consumer

- Direct producer：`build_rewrite_term_pool(...)`
- Direct consumer：`SearchControllerContext_t.rewrite_term_candidates`

## Invariants

- `term` 必须是可 materialize 的单词或短语
- 它只作为 rewrite operator 的词源池，不会直接 append 到 CTS `keyword`
- trace owner 是 `RewriteTermPool.accepted[*]`
- `accepted_term_score` 只用于 evidence term 排序和 trace explainability，不直接下推到 prompt
- `score_breakdown` 必须显式记录 support、candidate quality、field weight、must-have / anchor / pack bonus 与 generic penalty

## 最小示例

```yaml
term: "ranking"
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
```
