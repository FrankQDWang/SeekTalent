# Runtime Phased Search Design

Historical working design note. The current canonical spec is [`SYSTEM_MODEL.md`](/Users/frankqdwang/Agents/SeekTalent/SYSTEM_MODEL.md).

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

- `initial_round_budget` 现在已经是 run 级输入，并会 clamp 到 `5~12`。
- `phase_progress / search_phase` 已经是稳定 owner。
- frontier selection 已经 phase-aware。
- allowed operator surface 已经 phase-aware。
- term budget 已经切到 phase-frozen `max_query_terms`。
- non-crossover operator 已经切到 final `query_terms` rewrite。
- round-0 bootstrap seed cap 已经直接复用 `explore_max_query_terms`。
- stop policy 已经切到 phase-gated owner。
- `eval.json` 已经包含 phased search diagnostics，用于观测 coverage、net-new shortlist 和 phase 内 operator 分布。
- offline replay tuning 已经落地为 deterministic case harness，只支持 canonical + tuning suite，不支持任意历史 bundle 反事实 replay。
- rewrite evidence scoring 已经切到 discriminative term ranking，accepted terms 会显式记录 support、candidate quality、field weight 和 bonus / penalty 拆解。
- rewrite coherence scoring 已经强化为 seed-anchor preservation + evidence-aware coherence + provenance overlap 的组合评分。
- controller draft validation 与 runtime rewrite normalization 现在共享同一组 `RewriteFitnessWeights`。
- non-crossover rewrite round 会显式落 `rewrite_choice_trace`，finalization prompt 也会带 compact `Run Facts`。

也就是说，当前设计文档的重点不再是“把 phase 引入 runtime”，而是“在 CTS 交集语义已经纠正后，继续做 diagnostics、tuning 和局部优化”。

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
  - 开放 `must_have_alias / generic_expansion / core_precision / relaxed_floor`
  - 若有 pack，再开放 `pack_expansion / cross_pack_bridge`
  - 永不开放 `crossover_compose`
- `balance`:
  - base 保留 `core_precision / must_have_alias / relaxed_floor / generic_expansion`
  - 若有 pack，再开放 `pack_expansion / cross_pack_bridge`
  - `crossover_compose` 只在 legal donor candidates 非空时开放
- `harvest`:
  - base 只保留 `core_precision`
  - legal donor candidates 非空时，才开放 `crossover_compose`
  - 默认关闭 `relaxed_floor`
  - 默认关闭 `pack_expansion / cross_pack_bridge`
  - 仅当 active node 仍有 unmet must-have 时，才临时开放 `must_have_alias / generic_expansion`

这里的意思不是硬编码“必须选某个 operator”，而是 phase 改写 allowed operator surface，让 controller 在正确的局部动作空间里工作。

这里的 must-have gap 语义必须与 selection 层同源：

- `coverage_opportunity_score`
- `unmet_requirement_weights`
- `harvest repair override`

都共用同一个 capability-hit helper。

### 3. Step 5R: Term Budget Policy

在真实 CTS 上，keyword 用空格拼接后是交集语义。  
所以 term budget 不应继续只靠 `remaining_budget >= 4 / >= 2 / else` 这种绝对阈值，也不应继续沿用“更多词=更探索”的旧假设。

建议改为 phase owner：

- `explore`: `3`
- `balance`: `4`
- `harvest`: `6`

语义很直接：

- 前期 query 更短，降低交集约束，便于探索。
- 后期 query 更长，在已知高价值方向上做提纯。

实现上要再加一条硬约束：

- `SelectActiveFrontierNode` 先冻结 `max_query_terms`
- `MaterializeSearchExecutionPlan` 只消费这个冻结值
- 不允许 materialization 再次根据 `remaining_budget` 自行推导
- round-0 bootstrap seed 也直接复用 `explore_max_query_terms`

### 3.5 Step 5.5: Non-Crossover Query Rewrite

真实 CTS 是交集语义后，non-crossover operator 不能再按“`active_pool + additional_terms` 追加词”理解。

正确语义应改为：

- non-crossover 直接产出最终 `query_terms`
- `GenerateSearchControllerDecision` 只做 rewrite normalization
- `MaterializeSearchExecutionPlan` 不再把 parent pool 自动拼回去
- `UpdateFrontierState` 让 child node 直接继承本轮最终 query

operator contract 也要跟着切换：

- `core_precision`: active pool 的非空子集
- `relaxed_floor`: active pool 的非空真子集
- `must_have_alias / generic_expansion / pack_expansion / cross_pack_bridge`: 必须同时存在共享词、新增词、被替换掉的旧词

这一步的目的不是改 operator 名称，而是把它们的物化语义从 append 改成 rewrite。

### 3.6 Step 5.6: GA-lite Rewrite Candidate Search

如果后续要继续提高 non-crossover rewrite 的质量，最合适的方向不是把整场 runtime 改成 GA，而是在 query rewrite 内部增加一个 **GA-lite**。

这里的 GA-lite 只表示：

- 单 active node
- 单 operator
- 少量 rewrite 候选
- 1~2 轮局部搜索

它不是：

- 全局 population runtime
- 替代 `search_phase`
- 替代 `active node selection`
- 替代 `allowed operator surface`

最小设计如下：

1. 只作用于：
   - `must_have_alias`
   - `generic_expansion`
   - `pack_expansion`
   - `cross_pack_bridge`
2. 染色体直接定义为最终 `query_terms: list[str]`
3. 初始种群固定 `4~6` 个，并且必须包含 controller 已给出的合法 rewrite
4. mutation 只允许：
   - `replace_one_term`
   - `drop_one_term`
   - `swap_one_term`
5. 所有候选先过 contract filter：
   - `len(query_terms) <= max_query_terms`
   - 至少保留 `1` 个 active anchor
   - rewrite operator 必须同时满足“共享旧词 + 新增词 + 删除旧词”
   - pack operator 必须有合法 provenance
6. 不做真 CTS probe；第一版只做 deterministic surrogate fitness

fitness 至少应包含：

- `must_have_repair_score`
- `anchor_preservation_score`
- `rewrite_coherence_score`
- `provenance_coherence_score`
- `query_length_penalty`
- `redundancy_penalty`

其中最关键的不是 `must_have_repair_score`，而是 `rewrite_coherence_score`。  
如果没有好的 coherence 评分，GA-lite 很容易学会“为了拿分而凑词”，形式合法但语义违和。

因此更稳的工程顺序是：

1. 先保留 Step 5.5 的 deterministic rewrite 作为 baseline
2. 再在 rewrite operator 内部引入 bounded local search
3. 最终只输出一个 best `query_terms`
4. 后续仍然走现有 materialization / CTS / scoring / frontier update

### 3.7 Step 5.7: Evidence-Mining Rewrite Term Pool

真实业务里，从本轮高质量候选中提取共性技能词是有价值的，但在当前 CTS 上必须改造成 **evidence mining for rewrite**，不能做成传统的“高频词直接扩搜”。

原因很简单：

- 真实 CTS 的 keyword 用空格拼接后是交集语义
- 新词一旦直接追加进 query，只会让检索更紧
- 因此共性词只能进入 rewrite 候选池，不能直接进入 CTS keyword

正确的位置是：

```text
high-quality candidates
  -> evidence term extraction
  -> rewrite_term_candidates
  -> Step 5.5 / 5.6 rewrite selection
  -> final query_terms
```

而不是：

```text
high-quality candidates
  -> common terms
  -> append to CTS keyword
```

第一版建议保持克制：

1. 只读取高质量小集合：
   - 当前 round 中 `fit == 1` 且 `fusion_score` 高的候选
   - 数量控制在 `top 3 ~ top 5`
2. 只从强信号字段提词：
   - `work_summaries`
   - `project_names`
   - `work_experience_summaries`
   - title / job category
   - `search_text` 中的技能短语
3. 只提“技能 / 工具 / 专有名词”，不提泛化动作词和软素质词
4. evidence term 必须通过至少一条 relevance gate：
   - 能补当前 unmet must-have
   - 与 active anchor 语义一致
   - 与 pack provenance 一致

这样它扮演的是：

- rewrite operator 的词源池
- GA-lite 的候选弹药库

而不是：

- 新一轮 CTS 搜索的直接 query patch

当前实现里，trace 至少能解释：

- evidence term 从哪些候选、哪些字段提出来
- 为什么被保留
- 为什么没有直接下推 CTS

### 4. Stop Policy

stop policy 必须区分探索期和收割期。

建议：

- `explore` 期禁止整个 run 因 `exhausted_low_gain` 提前结束。
- `balance` 期允许 `controller_stop`，但仍不允许 `exhausted_low_gain` 提前结束整次 run。
- `harvest` 期允许更积极地因为低收益而收口。

更具体地说：

- `budget_exhausted` 和 `no_open_node` 继续保持最高优先级。
- `controller_stop` 不再写死成 `min_round_index`，而是按 phase gate 决定：`balance / harvest` 可接受。
- `exhausted_low_gain` 不再在 `explore / balance` 生效；只在 `harvest` 具备 run-level 终止资格。
- 这一步先不调 `novelty / usefulness / reward` floors，只改“何时允许 stop”。

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

如果后面真的要引入 GA-lite，它也不该接管整场 runtime。

更合适的位置是：

- 只放在 non-crossover query rewrite 内部
- 只在单个 active node 上生成少量 rewrite 候选
- 只作为 operator-internal candidate generator
- 不替换 `search_phase`、`active node selection`、`allowed operator surface` 这些 deterministic owner

## Trace Requirements

phase policy 必须进入真实 run trace，也就是 `runs/<run_id>/bundle.json`。

至少要落这些字段：

- `initial_round_budget`
- `phase_progress`
- `search_phase`
- `active_selection_breakdown`
- `selection_ranking`
- `max_query_terms`
- `effective_stop_guard`
- non-crossover `operator_args.query_terms`

要求：

- 同一轮为什么选了某个 node，必须能从 trace 解释。
- 同一轮为什么禁用了某个 operator，必须能从 trace 解释。
- 同一轮为什么没有提前 stop，也必须能从 trace 解释。
- trace 中不应再出现 non-crossover `additional_terms`。

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

## Future Experiments

下面这些属于明确的后续实验项，不应在当前已稳定的 phased runtime 主链上直接插入：

1. `continuous annealing`
   - 把当前三段式 `explore / balance / harvest` 平滑成连续退火
2. `optional CTS probe reranking`
   - 对 top rewrite candidates 做一次轻量 probe，再决定最终 query
3. `multi-query fan-out / beam search`
   - 在 CTS 交集语义下，探索“多个短 query”是否优于“单个长 query”
4. `capability alias ontology / skill graph`
   - 把 `query_terms_hit` 之上的语义映射独立成专门 owner
5. `reward formula rewrite`
   - 等 phased policy 稳定后，再单独评估是否要重写 reward
6. `final candidate presentation layer`
   - 补最终 top candidates 的推荐理由、不符合点和可读报告层
