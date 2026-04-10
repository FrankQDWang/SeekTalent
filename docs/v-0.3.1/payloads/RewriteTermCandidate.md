# RewriteTermCandidate

单个被接受的 rewrite evidence term。

```text
RewriteTermCandidate = {
  term,
  source_candidate_ids,
  source_fields
}
```

## Producer / Consumer

- Direct producer：`build_rewrite_term_pool(...)`
- Direct consumer：`SearchControllerContext_t.rewrite_term_candidates`

## Invariants

- `term` 必须是可 materialize 的单词或短语
- 它只作为 rewrite operator 的词源池，不会直接 append 到 CTS `keyword`
- trace owner 是 `RewriteTermPool.accepted[*]`

## 最小示例

```yaml
term: "ranking"
source_candidate_ids: ["c32", "c44"]
source_fields: ["project_names", "work_summaries"]
```
