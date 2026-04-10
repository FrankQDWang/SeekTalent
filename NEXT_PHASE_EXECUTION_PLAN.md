# Next Phase Execution Plan

## Goal

下一阶段只做一件事：把当前 runtime 升级成 budget-aware phased search，并保持现有 deterministic owner 边界不变。

这轮不追求“做更多功能”，只追求把 `5~12` 轮预算的行为语义做对。

## Target Output

本阶段交付完成后，代码和文档应满足：

1. round budget 可按 run 输入，并限制在 `5~12`。
2. runtime 每轮都有显式 `phase_progress` 和 `search_phase`。
3. active node selection 变成 phase-aware。
4. allowed operator surface 变成 phase-aware。
5. term budget policy 改成 phase-aware。
6. stop policy 改成 phase-aware。
7. trace 能解释这些行为。

## Files To Change

第一批明确改这些文件：

- `src/seektalent/models.py`
- `src/seektalent/bootstrap_assets.py`
- `src/seektalent/frontier_ops.py`
- `src/seektalent/runtime_ops.py`
- `src/seektalent/runtime/orchestrator.py`
- `docs/v-0.3.1/weights-and-thresholds-index.md`
- `docs/v-0.3.1/operators/SelectActiveFrontierNode.md`
- `docs/v-0.3.1/operators/EvaluateStopCondition.md`
- `docs/v-0.3.1/payloads/SearchControllerContext_t.md`

测试至少覆盖：

- `tests/test_frontier_ops.py`
- `tests/test_runtime_ops.py`
- `tests/test_runtime_orchestrator.py`

如果缺文件，就按现有测试布局补最小新增文件。

## Step 1: 统一 Round Budget 语义

目标：把现在“默认 5 轮”的实现，改成“run 级 budget，允许 5~12”。

执行：

1. 在 `RuntimeSearchBudget` owner 里保留 `initial_round_budget` 字段，但明确它表示“本次 run 的实际预算”。
2. 在 runtime 入口加一层 clamp：
   - `< 5` 直接拉到 `5`
   - `> 12` 直接拉到 `12`
3. 所有 phase 计算都基于 clamp 后的 `initial_round_budget`。
4. 文档中删掉“默认就是 5 轮”的叙述，改成“默认值可以是 5，但运行预算语义是 5~12”。

验收：

- `bundle.json` 里能看到本次实际 round budget。

## Step 2: 新增 Phase Owner

目标：引入统一的 phase 派生值，避免在多个函数里各算各的。

执行：

1. 在 `models.py` 里新增：
   - `SearchPhase = Literal["explore", "balance", "harvest"]`
2. 在 `FrontierHeadSummary` 或等价上下文里增加：
   - `initial_round_budget`
   - `phase_progress`
   - `search_phase`
3. 在 runtime 内统一使用：

```text
phase_progress = runtime_round_index / max(1, initial_round_budget - 1)
```

4. phase 切分固定为：
   - `< 0.34` -> `explore`
   - `< 0.67` -> `balance`
   - 其余 -> `harvest`

验收：

- controller context 已显式携带 phase 信息。
- trace 中能直接看到 phase，不需要外部再推导。

## Step 3: 改 SelectActiveFrontierNode

目标：让 active node 选择真正体现“前期探索，后期收益”。

执行：

1. 保持现有三部分结构不变：
   - `frontier_priority`
   - `unmet_requirement_bonus`
   - `saturation_penalty`
2. 只改合成时的 phase 系数：

| phase | frontier_priority | unmet_requirement_bonus | saturation_penalty |
| --- | ---: | ---: | ---: |
| explore | `0.8x` | `1.3x` | `0.7x` |
| balance | `1.0x` | `1.0x` | `1.0x` |
| harvest | `1.2x` | `0.6x` | `1.4x` |

3. 把 selection breakdown 一起挂进 trace 可读字段。

验收：

- 同样 frontier 下，`explore` 和 `harvest` 期能选出不同 active node。
- 单测能证明后期更偏已有高 reward node，前期更偏未覆盖 must-have 的 node。

## Step 4: 改 Allowed Operator Surface

目标：让 controller 在不同阶段看到不同但可解释的动作空间。

执行：

1. `explore` 期：
   - 保留 `core_precision`
   - 保留 `must_have_alias`
   - 保留 `relaxed_floor`
   - 保留 `generic_expansion`
   - 若有 pack，保留 `pack_expansion` / `cross_pack_bridge`
   - 不开放 `crossover_compose`
2. `balance` 期：
   - 恢复完整 catalog
3. `harvest` 期：
   - 保留 `core_precision`
   - 保留 `relaxed_floor`
   - 保留 `crossover_compose`
   - `generic_expansion` 只在仍存在明显 unmet must-have 时保留

验收：

- 同一 frontier node，在不同 phase 下 controller context 的 `allowed_operator_names` 会变化。
- 早期不会过早把预算打到 crossover 上。

## Step 5: 改 Term Budget Policy

目标：不要再用绝对 `remaining_budget` 切 term budget。

执行：

1. 把 term budget owner 改成 phase-aware。
2. 这轮先用固定三档，不上连续公式：
   - `explore`: `[2, 6]`
   - `balance`: `[2, 5]`
   - `harvest`: `[2, 4]`
3. `materialize_search_execution_plan()` 与 controller normalization 使用同一档位。

验收：

- 同样 node，在后期 query terms 上限会更小。
- `12` 轮运行时，不会长期停留在旧逻辑的“high budget range”。

## Step 6: 改 Stop Policy

目标：避免长预算下探索期过早终止整次 run。

执行：

1. `exhausted_low_gain` 在 `explore` 期不具备 run-level stop 权限。
2. `controller_stop` 允许阈值改成与预算比例相关，而不是固定 `min_round_index = 2`。
3. 建议规则：
   - `controller_stop` 至少要到 `balance` 期才可能生效。
   - `exhausted_low_gain` 至少要到 `balance` 期才可能生效。
4. `harvest` 期允许比现在更积极地收口。

验收：

- `12` 轮 run 在前几轮即便遇到局部空结果，也不会直接因为低收益结束。
- `5` 轮 run 不会被无限拖长。

## Step 7: 补 Trace 字段

目标：让 phase-aware 行为可回放、可解释、可比对。

执行：

1. 每轮 trace 至少写出：
   - `initial_round_budget`
   - `phase_progress`
   - `search_phase`
   - `effective_term_budget_range`
   - `selection_score_breakdown`
   - `effective_stop_guard`
2. 不要求新增另一套日志文件，仍然走现有 `bundle.json` owner。

验收：

- 单看 `bundle.json` 就能解释 runtime 为什么在那一轮探索或收敛。

## Step 8: 补 Tests

最少补下面这些测试：

1. `5` 轮预算下，phase 序列正确。
2. `12` 轮预算下，phase 序列正确。
3. `explore` 与 `harvest` 对同一组 open nodes 的选点不同。
4. `explore` 期 `crossover_compose` 不可用。
5. `harvest` 期 `crossover_compose` 可用，但仍必须满足 donor guard。
6. `explore` 期不会触发 `exhausted_low_gain` stop。
7. `harvest` 期可以触发 `exhausted_low_gain` stop。

## Step 9: 补文档与 Canonical Artifacts

执行：

1. 更新 `weights-and-thresholds-index.md`。
2. 更新 operator / payload 文档。
3. 如 canonical cases 依赖了固定 round 语义，就刷新 case bundle。

验收：

- 文档与代码中的 budget / phase / stop 语义一致。
- trace 示例不再固化“默认就是 5 轮”的旧心智。

## Explicit Non-Goals

这轮明确不做：

1. 不重写 reward 主公式。
2. 不改 reranker / scoring owner。
3. 不引入 full MCTS。
4. 不引入 full GA population。
5. 不引入多线程并发 search runtime。

## Done Definition

这轮完成的标准不是“代码改完”，而是下面四件事同时成立：

1. `5` 轮和 `12` 轮都能跑。
2. phase-aware selection / operator / stop 已生效。
3. trace 能解释每轮 phase 决策。
4. 测试覆盖早期探索、后期收割、长预算不早停这三个关键行为。
