# Search Diagnostics

## Goal

用最小代码改动给每次 SeekTalent run 生成一份可归因的搜索诊断账本，让业务人员和后续 coding agent 能回答：一条 JD 找不到合适候选人时，问题主要发生在搜索词、筛选条件、CTS 召回、去重、评分，还是反思/控制器决策。

第一阶段只照亮现有漏斗，不改变搜索策略。

## Why Now

当前业务判断是：SeekTalent 的下一阶段瓶颈不是开放式 agent 自主性，而是搜索表示层脏、实验边界混、失败原因不可归因。仓库当前已经写出大量 run artifacts，但缺少一份跨 round 聚合的业务诊断视图。

仓库证据：

- `README.md` 描述当前主链路为 requirement extraction -> controlled CTS retrieval -> scoring -> reflection -> finalization。
- `docs/outputs.md` 已列出 per-round artifacts，但没有单一 `search_diagnostics.json`。
- `src/seektalent/runtime/orchestrator.py` 已在每轮写出 `retrieval_plan.json`、`cts_queries.json`、`search_observation.json`、`search_attempts.json`、`scorecards.jsonl`、`reflection_advice.json`。
- `src/seektalent/models.py` 中 LLM 输出 schema 偏重，尤其 `RequirementExtractionDraft`、`ScoredCandidate`、`FinalResult` 字段较多；但第一阶段只记录 schema 压力信号，不做 schema 瘦身。

## Non-goals

- 不改变 CTS query term selection、explore query 推导、location execution、filter projection 或 scoring 排序逻辑。
- 不把 reflection 改成 discovery agent。
- 不引入新的 agent 框架、接口层、抽象基类或多模块重构。
- 不瘦身 `RequirementExtractionDraft`、`ScoredCandidate`、`FinalResult` schema；这些放到后续阶段。
- 不新增外部服务、数据库、W&B/Weave 依赖或 UI 面板。
- 不修改 benchmark 评价口径，除非本计划先更新。

## Done Criteria

- 每次成功 run 都在 run 根目录写出 `search_diagnostics.json`。
- `docs/outputs.md` 记录该 artifact 的用途。
- 诊断账本能按 round 展示搜索词、筛选条件、CTS 召回、去重/短缺、评分保留、reflection/controller 响应的核心信号。
- 账本包含 LLM structured-output 风险信号：各阶段 `output_retries` 与 `validator_retry_count`，用于后续 schema 瘦身判断。
- 现有行为不变：final shortlist、sent query、scorecards 不因新增诊断账本而变化。
- 相关 tests 和 validation commands 通过，或记录明确的 unrelated/pre-existing failure。

## Repo Entrypoints

Read first:

1. `AGENTS.md`
2. `README.md`
3. `docs/outputs.md`
4. `src/seektalent/runtime/orchestrator.py`
5. `src/seektalent/runtime/context_builder.py`
6. `src/seektalent/models.py`
7. `tests/test_runtime_audit.py`
8. `tests/test_runtime_state_flow.py`
9. `tests/test_query_plan.py`

Likely edit:

- `src/seektalent/models.py`
- `src/seektalent/runtime/orchestrator.py`
- `docs/outputs.md`
- `tests/test_runtime_audit.py`
- `tests/test_runtime_state_flow.py`

Allowed only if a narrow helper makes the code smaller and clearer:

- `src/seektalent/runtime/context_builder.py`
- New file under `src/seektalent/runtime/` for pure diagnostic assembly, only if keeping it inside `orchestrator.py` becomes less readable.

Do not edit unless this plan is updated first:

- `src/seektalent/requirements/`
- `src/seektalent/controller/`
- `src/seektalent/reflection/`
- `src/seektalent/scoring/`
- `src/seektalent/retrieval/query_plan.py`
- `src/seektalent/retrieval/filter_projection.py`
- `experiments/`
- `apps/web-svelte/`

Ignore unless evidence says otherwise:

- `runs/`
- `dist/`
- `.venv/`
- `.tmp-seektalent-venv/`
- `.pytest_cache/`
- `__pycache__/`

## Current Reality

Observed behavior:

- Each round already writes separate audit artifacts under `rounds/round_xx/`.
- `sent_query_history.json` exists at run root, but it is query metadata only.
- `round_review.md` is human-readable per round, but not a machine-readable cross-round funnel.
- LLM calls already record `output_retries`; controller/finalizer snapshots may include `validator_retry_count`.

Known invariants:

- Runtime owns orchestration, budgets, pagination, dedup, normalization, scoring fan-out, reflection, finalization, and artifact writing.
- Query budget and canonicalization are enforced by runtime/retrieval code, not by diagnostics.
- Scoring policy is frozen from requirements and must not drift because of diagnostics.
- This repo favors small, direct Python over speculative abstractions.

## Target Behavior

Successful run root includes:

```text
runs/<timestamp>_<run_id>/search_diagnostics.json
```

The JSON should be compact, stable, and business-readable:

```json
{
  "run_id": "...",
  "input": {
    "job_title": "...",
    "jd_sha256": "...",
    "notes_sha256": "..."
  },
  "summary": {
    "rounds_executed": 3,
    "total_sent_queries": 5,
    "total_raw_candidates": 42,
    "total_unique_new_candidates": 18,
    "final_candidate_count": 10,
    "stop_reason": "..."
  },
  "llm_schema_pressure": [
    {
      "stage": "controller",
      "call_id": "controller-r01",
      "output_retries": 2,
      "validator_retry_count": 0
    }
  ],
  "rounds": [
    {
      "round_no": 1,
      "query_terms": ["Agent", "Python"],
      "keyword_query": "Agent Python",
      "query_term_details": [
        {
          "term": "Python",
          "source": "jd",
          "category": "tooling"
        }
      ],
      "filters": {
        "projected_cts_filters": {},
        "runtime_only_constraints": [],
        "adapter_notes": []
      },
      "search": {
        "raw_candidate_count": 0,
        "unique_new_count": 0,
        "shortage_count": 10,
        "duplicate_count": 0,
        "fetch_attempt_count": 1,
        "exhausted_reason": "cts_exhausted"
      },
      "scoring": {
        "newly_scored_count": 0,
        "top_pool_count": 0,
        "fit_count": 0,
        "not_fit_count": 0,
        "top_pool_snapshot": []
      },
      "reflection": {
        "suggest_stop": false,
        "suggested_activate_terms": [],
        "suggested_drop_terms": [],
        "suggested_drop_filter_fields": []
      },
      "controller_response_to_previous_reflection": null
    }
  ]
}
```

Keep the final schema smaller if implementation proves a field is not backed by current runtime state.

## Milestones

### M1. Confirm Change Surface

Steps:

- Read `RunState`, `RoundState`, `SearchObservation`, `SearchAttempt`, `RoundRetrievalPlan`, `ReflectionAdvice`, and LLM call snapshot models.
- Trace where final artifacts are written in `WorkflowRuntime.run_async`.
- Confirm whether duplicate count can be computed from existing `SearchAttempt.batch_duplicate_count`; do not add new search behavior.
- Decide whether diagnostic assembly stays in `orchestrator.py` or moves to one small helper file under `src/seektalent/runtime/`.

Deliverables:

- Updated decision log in this plan if the edit surface differs from the likely edit list.
- Exact field list for `search_diagnostics.json`.

Acceptance:

- Every planned diagnostic field has a known source in existing state or is removed from the target schema.
- No edit to retrieval strategy, scoring logic, or prompt text is needed for M1.

Validation:

```bash
uv run pytest tests/test_runtime_audit.py tests/test_runtime_state_flow.py
```

Expected: existing tests pass before implementation, or pre-existing failures are recorded before proceeding.

### M2. Add Diagnostic Assembly and Artifact

Steps:

- Add a small Pydantic model or plain JSON assembly function for search diagnostics.
- Write `search_diagnostics.json` near the end of successful `run_async`, after final result exists and before `run_finished`.
- Use existing `run_state.round_history`, `sent_query_history`, `search_attempts`, `constraint_projection_result`, `top_pool_ids`, `scorecards_by_resume_id`, final result, and LLM call snapshots already written in memory where available.
- If LLM call snapshot data is easiest to capture during stage completion, add only minimal local tracking needed for `stage`, `call_id`, `output_retries`, and `validator_retry_count`.
- Do not parse arbitrary markdown or prompt text.

Deliverables:

- `search_diagnostics.json` written for successful runs.
- No behavior change to final shortlist or existing artifact filenames.

Acceptance:

- Runtime audit test can assert the file exists.
- Test can assert at least one round contains query terms, search counts, scoring counts, and reflection summary fields.
- LLM schema pressure section includes requirements/controller/scoring/reflection/finalizer stages when those stages ran.

Validation:

```bash
uv run pytest tests/test_runtime_audit.py tests/test_runtime_state_flow.py
```

Expected: all targeted tests pass.

### M3. Document Artifact and Guard No-Behavior-Change

Steps:

- Update `docs/outputs.md` to include `search_diagnostics.json`.
- Add test assertions that existing key artifacts still exist.
- Compare a controlled mock CTS run before/after implementation if needed using existing tests rather than generated run directories.

Deliverables:

- Updated docs.
- Tests protecting artifact presence and broad shape.

Acceptance:

- A new agent can read `docs/outputs.md` and know when to inspect `search_diagnostics.json`.
- Existing per-round artifacts remain documented and unchanged.

Validation:

```bash
uv run pytest tests/test_runtime_audit.py tests/test_runtime_state_flow.py tests/test_cli.py
```

Expected: all targeted tests pass.

### M4. Run Final Validation

Steps:

- Run focused tests first.
- Run broader repo validation only after focused tests pass.
- Fix task-caused failures before finalizing.
- Do not fix unrelated failures without user approval.

Acceptance:

- No known task-related failures remain.
- `git diff` shows only planned files changed.
- This plan status and decision log are updated.

Validation:

```bash
uv run pytest tests/test_runtime_audit.py tests/test_runtime_state_flow.py tests/test_query_plan.py tests/test_reflection_contract.py tests/test_controller_contract.py
uv run ruff check src/seektalent tests
```

Expected: all commands pass, or unrelated pre-existing failures are documented with command output summary.

## Decision Log

- 2026-04-18: Chose a diagnostic ledger as Phase 1 because current artifacts are rich but fragmented; the business needs a cross-round funnel before changing query compiler, reflection authority, or scoring/finalizer schemas.
- 2026-04-18: Chose no behavior change for Phase 1 because previous multi-feature runs made attribution difficult.
- 2026-04-18: Chose to record schema pressure signals instead of immediately slimming schemas because structured-output failure should be measured before changing multiple model contracts.
- 2026-04-18: Implemented diagnostic assembly inside `WorkflowRuntime` instead of adding a new runtime helper file; the logic is local to successful artifact writing and did not require retrieval, scoring, reflection, or finalizer behavior changes.
- 2026-04-18: Chose to read LLM schema-pressure signals from existing structured `*_call.json` and `scoring_calls.jsonl` artifacts; this keeps scorer/controller APIs unchanged while still capturing every LLM stage that ran.
- 2026-04-18: Final diagnostic field sources are `InputTruth`, `FinalResult`, `RunState.retrieval_state.sent_query_history`, `RoundState.retrieval_plan`, `RoundState.search_observation`, `RoundState.search_attempts`, `RoundState.top_candidates`, `RoundState.reflection_advice`, terminal controller state, and existing LLM call snapshots.
- 2026-04-18: Omitted per-round `query_term_details.active` because the runtime mutates the shared term pool after each reflection; without a per-round term-pool snapshot, `active` would describe final state rather than historical round state.

## Risks and Unknowns

- Duplicate count is currently summed from `SearchAttempt.batch_duplicate_count`; no new search counters were added.
- Scoring failure data may not currently flow into `ReflectionContext.scoring_failures`; do not invent data. Either omit from diagnostics or record as unavailable.
- LLM schema pressure tracking did not require broad API changes because existing structured call artifacts already contain the needed retry fields.
- `query_term_details` intentionally records only stable term metadata (`term`, `source`, `category`) until a real per-round term-pool snapshot exists.
- If tests rely on exact artifact lists, update only the relevant tests and docs.
- `search_diagnostics.json` uses compact top-pool summaries rather than full resumes.

## Stop Rules

- Stop and update this plan if implementing diagnostics requires changing query planning, scoring decisions, reflection prompts, or finalizer behavior.
- Do not proceed to M2 while M1 validation has unexplained failures.
- Do not widen the edit surface outside the listed files without recording the reason here.
- Do not parse generated markdown as a source of truth if structured state is available.
- Do not add new external dependencies.
- Do not commit generated `runs/` artifacts.

## Status

- Current milestone: Complete
- Last completed: M4 final validation.
- Next action: None.
- Blockers: None known.

## Done Checklist

- [x] Goal satisfied
- [x] Non-goals preserved
- [x] `search_diagnostics.json` implemented
- [x] `docs/outputs.md` updated
- [x] Focused tests pass
- [x] Decision log updated after implementation
- [x] Risks and unknowns updated after implementation
- [x] Status reflects final state
