# SearchObservation

单次执行计划产生的结果观察。

```text
SearchObservation = { unique_candidate_ids, shortage_after_last_page }
```

## 稳定字段组

- 唯一候选 id：`unique_candidate_ids`
- 是否低于目标新增数：`shortage_after_last_page`

## Invariants

- `unique_candidate_ids` 必须与 `deduplicated_candidates` 一致。
- `shortage_after_last_page` 只表达执行结果，不推断 branch 价值。

## 最小示例

```yaml
unique_candidate_ids: ["c07", "c19", "c51", "c77"]
shortage_after_last_page: false
```

## 相关

- [[SearchExecutionResult_t]]
- [[ExecuteSearchPlan]]
