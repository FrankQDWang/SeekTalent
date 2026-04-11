# RewriteTermRejected

单个被 evidence mining 丢弃的 candidate term 及其原因。

```text
RewriteTermRejected = {
  term,
  source_candidate_ids,
  source_fields,
  reason
}
```

## Producer / Consumer

- Direct producer：`build_rewrite_term_pool(...)`
- Direct consumer：`SearchRoundArtifact.rewrite_term_pool.rejected`

## Invariants

- `reason` 只允许：
  - `already_in_query`
  - `generic_junk`
  - `topic_drift`
  - `low_support`
- rejected 只进入 trace，不进入 controller prompt text

## 最小示例

```yaml
term: "DeepSpeed"
source_candidate_ids: ["c-1", "c-2"]
source_fields: ["work_summaries", "search_text"]
reason: "topic_drift"
```
