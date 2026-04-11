# Next Phase Execution Plan

Historical working plan. The current canonical spec is [`SYSTEM_MODEL.md`](/Users/frankqdwang/Agents/SeekTalent/SYSTEM_MODEL.md).

## Goal

下一阶段只做一件事：把当前 runtime 升级成 budget-aware phased search，并保持现有 deterministic owner 边界不变。

这轮不追求“做更多功能”，只追求把 `5~12` 轮预算的行为语义做对。

## Target Output

本阶段交付完成后，代码和文档应满足：

1. round budget 可按 run 输入，并限制在 `5~12`。
2. runtime 每轮都有显式 `phase_progress` 和 `search_phase`。
3. active node selection 变成 phase-aware。
4. allowed operator surface 变成 phase-aware。
5. `Step 5R`：term budget 改成 phase-frozen `max_query_terms`。
6. `Step 5.5`：non-crossover operator 改成完整 query rewrite。
7. stop policy 改成 phase-aware。
8. trace 能解释这些行为。
9. round-0 seed cap 与 `explore_max_query_terms` 完全同源，不保留 bootstrap 私有 4-term 语义。

## Files To Change

第一批明确改这些文件：

- `src/seektalent/models.py`
- `src/seektalent/bootstrap_assets.py`
- `src/seektalent/frontier_ops.py`
- `src/seektalent/runtime_ops.py`
- `src/seektalent/runtime/orchestrator.py`
- `docs/_archive/v-0.3.1/weights-and-thresholds-index.md`
- `docs/_archive/v-0.3.1/operators/SelectActiveFrontierNode.md`
- `docs/_archive/v-0.3.1/operators/EvaluateStopCondition.md`
- `docs/_archive/v-0.3.1/payloads/SearchControllerContext_t.md`

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

1. 删除旧的三项线性启发式结构。
2. 改成 phase-aware 的 operator-level UCB + branch utility：
   - `operator_exploitation_score`
   - `operator_exploration_bonus`
   - `coverage_opportunity_score`
   - `incremental_value_score`
   - `fresh_node_bonus`
   - `redundancy_penalty`
3. phase 权重固定为：

| phase | exploit | explore | coverage | incremental | fresh | redundancy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `explore` | `0.6` | `1.6` | `1.2` | `0.2` | `0.8` | `0.4` |
| `balance` | `1.0` | `1.0` | `0.8` | `0.8` | `0.3` | `0.8` |
| `harvest` | `1.4` | `0.3` | `0.2` | `1.2` | `0.0` | `1.2` |

4. 把 `active_selection_breakdown` 和 `selection_ranking` 一起挂进 trace。

验收：

- 同样 frontier 下，`explore` 和 `harvest` 期能选出不同 active node。
- 单测能证明后期更偏已有高 reward node，前期更偏未覆盖 must-have 的 node。

## Step 4: 改 Allowed Operator Surface

目标：让 controller 在不同阶段看到不同但可解释的动作空间。

说明：

- Step 3 已经决定“优先扩展哪个 node”。
- Step 4 不再参与第二次选点，只负责裁剪 controller 的动作空间。
- 也就是说，`allowed_operator_names` 是 phase-aware action surface，不是第二套 selection policy。

执行：

1. `explore` 期：
   - 保留 `must_have_alias`
   - 保留 `generic_expansion`
   - 保留 `core_precision`
   - 保留 `relaxed_floor`
   - 若有 pack，保留 `pack_expansion` / `cross_pack_bridge`
   - 不开放 `crossover_compose`
2. `balance` 期：
   - base: `core_precision / must_have_alias / relaxed_floor / generic_expansion`
   - 若有 pack，再开放 `pack_expansion / cross_pack_bridge`
   - 仅当 legal donor candidates 非空时，开放 `crossover_compose`
3. `harvest` 期：
   - 保留 `core_precision`
   - 若 donor surface 非空，保留 `crossover_compose`
   - 默认关闭 `relaxed_floor`
   - 默认关闭 `pack_expansion / cross_pack_bridge`
   - 只有 active node 仍存在 unmet must-have 时，临时开放 `must_have_alias / generic_expansion`
4. `coverage_opportunity_score`、`unmet_requirement_weights` 与 `harvest repair override` 共用同一个 capability-hit helper。

验收：

- 同一 frontier node，在不同 phase 下 controller context 的 `allowed_operator_names` 会变化。
- `explore` 期不会过早把预算打到 crossover 上。
- `harvest` 期不会因为 pack provenance 自动重新开放发散型 pack operator。

## Step 5R: 改 Term Budget Policy

目标：不要再用绝对 `remaining_budget` 切 term budget，并明确 CTS keyword 是交集语义。

执行：

1. 把 term budget owner 改成 phase-aware。
2. `RuntimeTermBudgetPolicy` 字段直接切成：
   - `explore_max_query_terms`
   - `balance_max_query_terms`
   - `harvest_max_query_terms`
3. 这轮先用固定三档，不上连续公式：
   - `explore`: `3`
   - `balance`: `4`
   - `harvest`: `6`
4. `SelectActiveFrontierNode` 先冻结 `max_query_terms`。
5. `materialize_search_execution_plan()` 不再接收 policy；只接收 controller context 里已经冻结好的 `max_query_terms`。
6. round-0 bootstrap 直接复用 `explore_max_query_terms`，不再保留独立 seed cap。

验收：

- 同样 node，在后期 query terms 上限会更大，因为 CTS 多词更紧。
- `12` 轮运行时，不会继续沿用“更多词=更探索”的旧假设。
- controller normalization 与 materialization 不会再各自推一遍 term budget。

## Step 5.5: 改 Non-Crossover Query Rewrite

目标：把 non-crossover operator 从“追加词”改成“直接产出最终 query_terms”。

执行：

1. 废弃 non-crossover `operator_args.additional_terms`。
2. 改成 non-crossover `operator_args.query_terms`。
3. `GenerateSearchControllerDecision` 只做最终 query rewrite normalization：
   - 去空
   - 去重
   - 保序
   - 按 `max_query_terms` 截断
4. `MaterializeSearchExecutionPlan` 对 non-crossover 不再把 parent `node_query_term_pool` 自动拼回去。
5. `UpdateFrontierState` 让 child node 直接继承本轮最终 query，而不是旧的 append 语义。
6. 各 operator contract 改成 rewrite：
   - `core_precision`: active pool 非空子集
   - `relaxed_floor`: active pool 非空真子集
   - `must_have_alias / generic_expansion / pack_expansion / cross_pack_bridge`: 必须既有共享词，也有新增词和移除词

验收：

- non-crossover 再也不会出现 `additional_terms`。
- `MaterializeSearchExecutionPlan` 不会把 parent pool 自动加回去。
- canonical bundles 和 trace 中，non-crossover 决策统一只出现 `query_terms`。

## Step 5.6: 引入 GA-lite Rewrite Candidate Search

目标：在不改 runtime 主框架的前提下，为 non-crossover rewrite operator 增加一个有界的局部候选搜索器。

原则：

1. 这不是整场 runtime 的 GA。
2. 它只在单个 active node 内工作。
3. 它只服务 non-crossover rewrite operator。
4. 它必须先过 contract，再算 fitness。

执行：

1. 只对下面 4 个 operator 启用：
   - `must_have_alias`
   - `generic_expansion`
   - `pack_expansion`
   - `cross_pack_bridge`
2. `core_precision / relaxed_floor / crossover_compose` 继续保持纯 deterministic，不接入 GA-lite。
3. 染色体直接定义为最终 `query_terms: list[str]`，不是 `additional_terms`，也不是 query patch。
4. 初始种群固定为 `4~6` 个候选，且必须包含 controller 已给出的合法 `query_terms` 作为 seed candidate。
5. mutation 只允许 3 类：
   - `replace_one_term`
   - `drop_one_term`
   - `swap_one_term`
6. 最多只跑 `1~2` 轮局部搜索，不做无限迭代，不做复杂 population crossover。
7. 所有候选先走硬约束过滤：
   - 长度 `<= max_query_terms`
   - 至少保留 `1` 个 active anchor
   - rewrite operator 必须同时满足“共享旧词 + 新增词 + 删除旧词”
   - pack operator 必须有合法 pack provenance
8. fitness 只用 deterministic surrogate，不做真 CTS probe。第一版固定分量为：
   - `must_have_repair_score`
   - `anchor_preservation_score`
   - `rewrite_coherence_score`
   - `provenance_coherence_score`
   - `query_length_penalty`
   - `redundancy_penalty`
9. 第一版推荐总分：
   - `1.2 * must_have_repair_score`
   - `1.0 * anchor_preservation_score`
   - `1.0 * rewrite_coherence_score`
   - `0.8 * provenance_coherence_score`
   - `-0.7 * query_length_penalty`
   - `-0.9 * redundancy_penalty`
10. 最终只输出 1 个 best `query_terms`，继续走现有 `MaterializeSearchExecutionPlan` 与后续 runtime。

验收：

- GA-lite 不会改变 `search_phase`、`active node selection`、`allowed operator surface`、`stop policy` 的 owner。
- 任一时刻 population size 都保持在 `<= 6`。
- trace 能解释为什么某个 rewrite 候选被选中。
- 如果 fitness 缺少 coherence，Step 5.6 不应继续推进到代码实现。

## Step 5.7: 引入 Evidence-Mining Rewrite Term Pool

目标：从本轮高质量候选中提取可复用的专有技能词，作为后续 rewrite operator 的候选词源，而不是直接追加到 CTS keyword。

原则：

1. 这不是传统 PRF 的“高频词直接扩搜”。
2. 在真实 CTS 交集语义下，新词绝不能直接拼进下一轮 query。
3. 它只产出 `rewrite_term_candidates`，供 Step 5.5 / 5.6 使用。
4. 它不改 `search_phase`、`selection`、`allowed operator surface`、`stop policy`。

执行：

1. 输入只取当前 round 的高质量小集合：
   - `fit == 1`
   - 且 `fusion_score` 较高
   - 数量上限建议 `top 3 ~ top 5`
2. 优先从强信号字段提词，不做整篇自由摘要：
   - `work_summaries`
   - `project_names`
   - `work_experience_summaries`
   - title / expected job category
   - `search_text` 中的技能短语
3. 第一版只抽“技能 / 工具 / 专有名词”，不抽泛化动词和软素质词。
4. 对候选词做轻量去噪：
   - 去重
   - 去停用泛词
   - 去明显无业务区分度词
5. 对候选词做 relevance gate，至少满足一条才保留：
   - 能补当前 `unmet must-have`
   - 与 active node 保留锚点语义一致
   - 与当前 pack provenance 一致
6. 输出 sidecar：
   - `rewrite_term_candidates: list[RewriteTermCandidate]`
7. 这个 sidecar 只允许喂给：
   - `must_have_alias`
   - `generic_expansion`
   - `pack_expansion`
   - `cross_pack_bridge`
8. 明确禁止：
   - 直接把 `rewrite_term_candidates` 追加到 CTS `keyword`
   - 把这些词提升成 CTS 侧硬过滤
   - 把整篇简历交给 LLM 在线概括后直接改 query

验收：

- 新 feature 不会直接改变 `SearchExecutionPlan_t.query_terms` 的 owner。
- 任一 evidence term 都能解释其来源候选和来源字段。
- topic drift 风险被 gate 在 rewrite 候选池内部，而不是直接污染 CTS query。
- trace 能看出哪些 evidence terms 被采纳，哪些被丢弃。

## Step 6: 改 Stop Policy

目标：避免长预算下探索期过早终止整次 run。

执行：

1. `exhausted_low_gain` 在 `explore` 期不具备 run-level stop 权限。
2. `controller_stop` 允许阈值改成与预算比例相关，而不是固定 `min_round_index = 2`。
3. 建议规则：
   - `controller_stop` 至少要到 `balance` 期才可能生效。
   - `exhausted_low_gain` 只在 `harvest` 期才可能生效。
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
   - `max_query_terms`
   - `active_selection_breakdown`
   - `selection_ranking`
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
