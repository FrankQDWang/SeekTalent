# FinalizeSearchRun

读取 run 级 shortlist，生成最终运行结果摘要。

## Signature

```text
FinalizeSearchRun : (RequirementSheet, FrontierState_t1, completed_rounds, stop_reason) -> SearchRunResult
```

## Notation Legend

```text
R := RequirementSheet
F_{t+1} := FrontierState_t1
rounds_t := completed_rounds
draft_run_summary_t := SearchRunSummaryDraft_t
```

## Input Projection

```text
ranked_shortlist_t = F_{t+1}.run_shortlist_candidate_ids
completed_rounds_t = rounds_t
stop_reason_t = stop_reason
```

## Primitive Predicates / Matching Rules

```text
normalized_text(text) = trim(compress_whitespace(text))
```

## Transformation

### Phase 1 — Prompt Packing

```text
finalization_context_t = {
  role_title: R.role_title,
  must_have_capabilities: R.must_have_capabilities,
  hard_constraints: R.hard_constraints,
  rounds: completed_rounds_t,
  ranked_candidates: ranked_shortlist_t,
  stop_reason: stop_reason_t
}
```

### Phase 2 — LLM Draft

```text
draft_run_summary_t = SearchRunFinalizationLLM(finalization_context_t)
```

### Field-Level Output Assembly

```text
SearchRunResult.final_shortlist_candidate_ids = ranked_shortlist_t
SearchRunResult.run_summary = normalized_text(draft_run_summary_t.run_summary)
SearchRunResult.stop_reason = stop_reason_t
```

## Read Set

- `RequirementSheet.role_title`
- `RequirementSheet.must_have_capabilities`
- `RequirementSheet.hard_constraints`
- `completed_rounds[*].controller_decision.selected_operator_name`
- `completed_rounds[*].execution_plan.query_terms`
- `FrontierState_t1.run_shortlist_candidate_ids`
- `stop_reason`

## Write Set

- `SearchRunResult.final_shortlist_candidate_ids`
- `SearchRunResult.run_summary`
- `SearchRunResult.stop_reason`

## 输入 payload

- [[RequirementSheet]]
- [[FrontierState_t1]]
- `completed_rounds`
- `stop_reason`

## 输出 payload

- [[SearchRunResult]]

## 不确定性边界 / 说明

- 唯一黑盒是 `SearchRunFinalizationLLM(finalization_context_t)`；它必须先产出 [[SearchRunSummaryDraft_t]]，再由 runtime 写回 `SearchRunResult.run_summary`。
- `SearchRunFinalizationLLM` 必须使用 provider-native strict structured output，固定 `retries=0`、`output_retries=1`。
- 默认不额外要求 `output_validator`；如未来 finalizer draft 扩展到 summary 之外，也只能用于保护 runtime 已冻结的 shortlist 与 stop facts，不允许回写事实对象。
- LLM 可以写总结，但不能改写 `final_shortlist_candidate_ids` 或 `stop_reason`。
- finalization prompt 仍然是 run-level summary surface，不进入候选解释或 CTS raw payload。
- prompt 现在会额外看到 `Run Facts`，其中包含：
  - search round count
  - final shortlist count
  - final must-have query coverage
  - operators used
  - stop reason

## 相关

- [[operator-spec-style]]
- [[RequirementSheet]]
- [[FrontierState_t1]]
- [[SearchRunSummaryDraft_t]]
- [[SearchRunResult]]
