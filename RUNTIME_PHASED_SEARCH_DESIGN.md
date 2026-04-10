# Runtime Phased Search Design

## Scope

这份文档只讨论 `bootstrap seeds` 之后的 runtime loop 优化，不重写已确认的 round-0 主链。

覆盖范围：

- active frontier node selection
- operator 调度与 donor 使用
- term budget / round budget 语义
- stop policy
- trace / eval 可观测性

不覆盖：

- RequirementSheet 抽取
- seeds 生成与确认
- reranker 文本面
- reward 主公式重写

## Current Baseline

当前实现已经有稳定骨架：

- `WorkflowRuntime` 负责完整 loop 编排。
- `SelectActiveFrontierNode` 是 deterministic 纯函数。
- controller 只做局部 operator 决策，不拥有整份 frontier。
- reward / stop / frontier update 都是 deterministic owner。

这条主线是对的，不应该推翻成 full GA 或 full MCTS。

当前真正的问题不是“没有搜索框架”，而是 runtime 仍然默认按 `5` 轮心智来工作：

- `initial_round_budget` 默认仍是 `5`。
- term budget policy 按 `remaining_budget` 的绝对值切档。
- frontier selection 还没有 phase-aware 行为。
- stop policy 还没有区分“探索期”和“收割期”。

当 round budget 提升到 `5~12` 时，这些默认语义会失真。

## Design Decision

总体方向：保持现有 deterministic frontier runtime，不做大框架重写，只把它升级成 budget-aware phased search。

核心判断：

1. 不做 full GA / full MCTS。
2. 保持 shallow search，不追求深树。
3. reward 主公式先保持稳定，不在本阶段做退火。
4. 退火或阶段差异只放在 policy 面，不放在 rerank 或 fusion 面。
5. 所有 phase 相关派生值都必须进 trace。

这意味着下一阶段的重点不是“引入更复杂算法”，而是让已有 runtime 在 `5~12` 轮内具备明确的阶段行为。

## Search Phase Model

定义：

```text
phase_progress = runtime_round_index / max(1, initial_round_budget - 1)
```

阶段切分：

- `explore`: `0.00 <= phase_progress < 0.34`
- `balance`: `0.34 <= phase_progress < 0.67`
- `harvest`: `0.67 <= phase_progress <= 1.00`

这里优先采用三段式 phase，而不是一开始就做连续退火公式。原因很简单：

- 更容易 trace 和评估。
- 更容易写测试。
- 更容易解释为什么某一轮放宽或收紧。
- 先把行为做对，再考虑把三段式平滑成连续函数。

## Owner Boundaries

### 1. Selection Policy

frontier 选点应该 phase-aware。

原则：

- `explore` 期提高 coverage / novelty 导向。
- `balance` 期保持当前基线。
- `harvest` 期提高 reward / saturation 导向。

建议直接切到 operator-level UCB + branch utility：

| phase | exploit | explore | coverage | incremental | fresh | redundancy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `explore` | `0.6` | `1.6` | `1.2` | `0.2` | `0.8` | `0.4` |
| `balance` | `1.0` | `1.0` | `0.8` | `0.8` | `0.3` | `0.8` |
| `harvest` | `1.4` | `0.3` | `0.2` | `1.2` | `0.0` | `1.2` |

设计含义：

- 前期优先让未充分尝试的 operator 和 partial coverage 机会拿到预算。
- 中期回到均衡。
- 后期优先吃高 exploitation 与高 net-new value 的 node。

### 2. Operator Policy

operator whitelist 不应在全程保持同一语义。

建议：

- `explore`:
  - 优先 `must_have_alias`
  - 优先 `generic_expansion`
  - 若有 pack，优先 `pack_expansion` / `cross_pack_bridge`
  - 暂不开放 `crossover_compose`
- `balance`:
  - 恢复完整 operator catalog
  - `crossover_compose` 仍需走现有 donor guard
- `harvest`:
  - 优先 `core_precision`
  - 优先 `relaxed_floor`
  - 允许 `crossover_compose`
  - `generic_expansion` 只在仍有明显 unmet must-have 时保留

这里的意思不是硬编码“必须选某个 operator”，而是 phase 改写 allowed operator surface，让 controller 在正确的局部动作空间里工作。

### 3. Term Budget Policy

term budget 不应继续只靠 `remaining_budget >= 4 / >= 2 / else` 这种绝对阈值。

建议改为 phase owner：

- `explore`: `[2, 6]`
- `balance`: `[2, 5]`
- `harvest`: `[2, 4]`

语义很直接：

- 前期允许更宽 query 扩张，制造支路和 donor。
- 后期主动收窄，减少 query bloat。

### 4. Stop Policy

stop policy 必须区分探索期和收割期。

建议：

- `explore` 期禁止整个 run 因 `exhausted_low_gain` 提前结束。
- `balance` 期恢复当前 stop guard。
- `harvest` 期允许更积极地因为低收益而收口。

更具体地说：

- `controller_stop` 的允许轮次不再写死成固定数字，而是按预算比例决定。
- `exhausted_low_gain` 至少要等 runtime 进入 `balance` 期才具备 run-level 终止资格。

### 5. Reward Policy

本阶段不改 reward 主公式。

原因：

- 当前 reward 已经是 deterministic，且不读 wall-clock latency。
- 如果同时改 phase policy 和 reward 公式，会让 offline attribution 变脏。
- 下一阶段真正要验证的是“更长预算下的搜索节奏”，不是 reward 重写。

本阶段允许做的只有一件事：把“reward 不参与 phase 退火”明确写入文档和 trace 语义。

## Why Not Full MCTS / GA

当前系统最值钱的部分是：

- typed payload 清楚
- owner 边界清楚
- trace 可回放
- reward / stop / frontier update 可归因

如果直接改成 full MCTS / GA，会同时损失三件事：

1. trace 解释成本暴涨
2. 小样本调参会很慢
3. 代码量和状态复杂度会明显上升

因此更好的路径是：

- 先做 `phase-aware frontier runtime`
- 选点已经切到 `operator-level UCB + branch utility`
- 后续继续补 operator surface / term budget / stop policy

## Trace Requirements

phase policy 必须进入真实 run trace，也就是 `runs/<run_id>/bundle.json`。

至少要落这些字段：

- `initial_round_budget`
- `phase_progress`
- `search_phase`
- `active_selection_breakdown`
- `selection_ranking`
- `effective_term_budget_range`
- `effective_stop_guard`

要求：

- 同一轮为什么选了某个 node，必须能从 trace 解释。
- 同一轮为什么禁用了某个 operator，必须能从 trace 解释。
- 同一轮为什么没有提前 stop，也必须能从 trace 解释。

## Non-Goals

这轮不做下面这些事：

1. 不重写 reward 公式。
2. 不改 reranker / fusion 语义。
3. 不做 full MCTS 回传。
4. 不做 full GA population 演化。
5. 不加第二套 parallel search runtime。

## Recommended Order

推荐顺序：

1. 先把 budget 语义从固定 `5` 升级成 `5~12` 的 run 级输入。
2. 再把 `phase_progress` 和 `search_phase` 建模出来。
3. 再改 selection / operator / term budget / stop。
4. 最后补 trace、tests、canonical artifacts。

## Success Criteria

这个设计落地后，应该出现下面这些可观察变化：

1. 在 `12` 轮预算下，前 `3~4` 轮不会过早 stop，且会明显偏探索。
2. 在 `12` 轮预算下，后段 operator 选择会明显向 precision / crossover 收敛。
3. 在 `5` 轮预算下，整体行为仍然接近当前 baseline，不出现大回归。
4. trace 能明确解释 phase-aware 决策，而不是只剩黑箱结果。
