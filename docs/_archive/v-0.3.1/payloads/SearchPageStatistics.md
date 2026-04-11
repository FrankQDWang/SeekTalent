# SearchPageStatistics

搜索执行后的成本事实快照。

```text
SearchPageStatistics = { pages_fetched, duplicate_rate, latency_ms }
```

## 稳定字段组

- 抓取页数：`pages_fetched`
- 重复率：`duplicate_rate`
- 延迟：`latency_ms`

## Invariants

- `duplicate_rate` 必须落在 `[0, 1]`。
- `latency_ms` 是 wall-clock 事实值，不允许推断。

## 最小示例

```yaml
pages_fetched: 2
duplicate_rate: 0.25
latency_ms: 1800
```

## 相关

- [[SearchExecutionResult_t]]
- [[ExecuteSearchPlan]]
