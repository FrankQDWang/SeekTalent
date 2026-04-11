# SearchRunResult

整次搜索运行结束后的最终输出对象。

```text
SearchRunResult = { final_shortlist_candidate_ids, run_summary, stop_reason }
```

## 稳定字段组

- 最终 shortlist id：`final_shortlist_candidate_ids`
- 运行总结：`run_summary`
- 停止原因：`stop_reason`

## Direct Producer / Direct Consumers

- Direct producer：[[FinalizeSearchRun]]
- Direct consumers：CLI、audit、downstream review

## Invariants

- `final_shortlist_candidate_ids` 必须受 `FrontierState_t1.run_shortlist_candidate_ids` 事实基础约束。
- `final_shortlist_candidate_ids` 的顺序来自 run 内最佳已观测 `fusion_score`，不得被 finalizer 二次改写。
- `run_summary` 可以是解释性文本，但不能改写排序事实。

## 最小示例

```yaml
final_shortlist_candidate_ids: ["c07", "c17", "c19"]
run_summary: "must-have 已覆盖，ranking 背景得到补强"
stop_reason: "budget_exhausted"
```

## 相关

- [[operator-map]]
- [[SearchRunSummaryDraft_t]]
- [[FinalizeSearchRun]]
- [[FrontierState_t1]]
- [[evaluation]]
