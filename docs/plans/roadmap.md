# SeekTalent Roadmap

## 结论

SeekTalent 下一阶段不应该改成完全开放式 agent。当前正确方向是继续保持受控、可审计 workflow，把搜索表示、失败归因、预算纪律和 eval 做硬，再逐步释放局部 agent 自主性。

当前主线：

1. 先保证搜索失败可诊断。
2. 再让搜索词经过 deterministic compiler 准入。
3. 再做 controller 的预算和停止策略。
4. 再做 benchmark 扩展、term/surface attribution、generic-only baseline 和去特异化。
5. 再做 schema 精简、reasoning model A/B、bounded reflection discovery、独立 verifier、session memory 和网页/app action harness。
6. 最后才进入数据驱动的领域适应循环；不维护人工领域词表。

不要先做的事：

- 不把整个 runtime 改成 open-ended agent。
- 不先抽一批 `CatalogBuilder` / `RewritePolicy` / `ReflectionDiscovery` / `Verifier` / `RetrievalAdapter` 抽象接口。
- 不用 prompt-only 修搜索表示。
- 不恢复“每个 query 必须包含 exact title anchor”的旧合同。
- 不在 term audit schema 稳定前把 query compiler 拆成微服务。
- 不把 `AI Agent -> Agent`、`MultiAgent 架构 -> MultiAgent` 这类表面词规则直接写成 active policy。
- 不要求人工在 replay 前预先写完整“理想搜索答案”。
- 不把领域词表、领域 alias、领域 router 或 domain overlay 放到当前主线。

外部方向依据只作为设计 rationale：Anthropic 的 [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)、[Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)、[Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)、[Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) 都支持同一个判断：简单可组合 workflow 优先，agent 自主性要被 harness、context、tool 和 eval 约束。

## 当前状态

Repo 当前 public shape 是 deterministic Python Agent。`docs/architecture.md` 明确写到设计目标是 controlled behavior 和 auditability，而不是 open-ended agent autonomy。主链路是 requirement extraction -> controlled CTS retrieval -> scoring -> reflection -> finalization。

状态总览：

| 阶段 | 状态 | 说明 |
| --- | --- | --- |
| Phase 1: Search Diagnostics | Done | `search_diagnostics.json` 已落地，搜索失败可归因。 |
| Phase 2: Lexical Compiler | Done | compiler metadata、queryability、family/query-plan admission 已落地。 |
| Phase 2.1: Budget and Precision Tuning | Done | budget context、stop guidance、high-signal query ordering 已落地并完成 0.4.4 replay。 |
| Phase 2.1.1: Completed-Round Stop Fix | Done | `retrieval_rounds_completed`、基于已完成检索轮数的 `min_rounds` guidance、forced-continue 测试已落地，并已用 0.4.5 完成 6 条 eval replay；stop quality gate 不在本阶段默认扩大。 |
| Phase 2.1.2: Stop Quality Gate | Done | 0.4.6 已加入 80% budget stop threshold、strong-fit quality gate、budget reminder 和 judge cache reuse report metric，并完成 6 条 eval replay。 |
| Phase 2.2: Benchmark Expansion and Term Attribution | Done (pilot) | 12 条混合 JD disable-eval smoke 已完成，并新增 `term_surface_audit.json`；本阶段只收集证据，不升级清洗规则。 |
| Phase 2.2.1: Generic Retrieval Baseline and De-specialization | Done | 0.4.7 已移除 Agent/LLM active 特例并完成 12 条 eval replay；作为后续 generic baseline。 |
| Phase 2.3+ | Pending | 先做 2.3A artifact slimming，再做 2.3B reflection/finalizer draft slimming 和 2.3C scoring schema experiment；之后才进入 reasoning model A/B、bounded reflection、verifier、session/action layers。 |
| Final: Data-Driven Domain Adaptation Loop | Last | 只有 generic baseline 稳定后，才做数据驱动领域适应；不靠人工维护词表。 |

Phase 1 已完成：`search_diagnostics.json` 已成为 run artifact。它把每轮 query、filter、CTS recall、dedup、scoring、reflection、controller response 和 LLM schema pressure 汇总到一个跨 round 诊断账本里。对应归档计划是 `docs/plans/completed/phase-1-search-diagnostics.md`，artifact 说明在 `docs/outputs.md`。

Phase 2 已完成：lexical compiler 和 query-plan admission 已落地。当前 `QueryTermCandidate` 已有 `retrieval_role`、`queryability`、`family`；`src/seektalent/retrieval/query_compiler.py` 负责编译 term pool；`src/seektalent/retrieval/query_plan.py` 负责拒绝非 admitted term、family 重复和缺少 compiler-admitted anchor 的 query。对应归档计划是 `docs/plans/completed/phase-2-search-lexical-compiler.md`。

Phase 2.1 已完成：controller context 现在包含预算状态和 deterministic stop guidance；runtime 会拒绝不符合 guidance 的 stop decision；query planner 会优先 `core_skill` / `framework_tool` 这类 high-signal non-anchor families；controller prompt 已要求遵守 stop guidance 和 near-budget exploit/narrowing。对应归档计划是 `docs/plans/completed/phase-2-1-search-budget-precision-tuning.md`。

Phase 2.1.1 已完成：0.4.4 replay 暴露出 `min_rounds` 的语义歧义。原实现按 pending controller round 判断，例如 controller round 3 可以在只完成 2 个检索轮后 stop。当前代码已让 stop guidance 暴露 `retrieval_rounds_completed`，并按已完成检索轮数判断是否允许停止；`tests/test_runtime_state_flow.py::test_runtime_min_rounds_count_completed_retrieval_rounds` 覆盖了 controller round 3 但只完成 2 个检索轮时必须 forced continue 的行为。0.4.5 是 completed-round stop semantics 的版本标签，6 条 eval replay 已完成且每条 `rounds_executed >= 3`。stop quality gate 不在本阶段默认扩大，后续只有在新证据显示低质量早停仍存在时再单独处理。

Phase 2.1.2 已完成：0.4.5 trace 证明部分低分样本并不是轮次上限导致停止，而是 `usable` top pool 在强匹配不足时仍允许停。0.4.6 将 near-budget stop threshold 调整为 `ceil(max_rounds * 0.8)`，在 80% budget 前要求低质量 `usable` pool 继续尝试未覆盖 admitted family；如果没有剩余 admitted family，则允许以 `low_quality_exhausted` 停止，避免空转。controller context 末尾新增 `budget_reminder`，W&B report 版本均值表新增 judge cache reuse percentage。

Phase 2.2 pilot 已完成：当前 12 条混合 JD disable-eval smoke 跑通，包括 8 条 Agent/LLM App、2 条 LLM 训推/推理 infra、2 条 bigdata 对照样本。每条 run 都产出 `term_surface_audit.json`，benchmark summary 能指向该 artifact。该阶段证明 audit 机制可用，但没有启用 eval，因此不能得出 precision/ndcg 结论，也不能把任何 surface alias promoted 为 active rule。

Phase 2.2 pilot 的关键发现是：系统跨 12 条样本可以稳定产出 final 10 candidates，但许多细粒度术语和 notes 词不适合直接做 CTS keyword search。主岗位 anchor 通常是最稳定召回来源；过窄新术语、寻访备注、公司范围、合规/沟通问题容易产生 0 recall 或弱贡献。下一步不应该把这些现象沉淀成人工领域词表，而应该回到通用清洗策略：区分召回词、筛选条件、评分信号、寻访备注和候选人沟通问题。

Phase 2.2.1 已完成：0.4.7 移除 Agent/LLM 专属 active 策略，包括 Agent anchor 特判、LLM/Agent broad-domain 注入和手工 known framework/domain family 优先级。保留的是通用 hygiene 和 runtime contract：文本清理、去重、硬约束不进 keyword、明显软素质/沟通要求不进 keyword、notes/寻访问题不进 keyword、compiler-admitted-only、每轮少量词、family 不重复、补拉去重和诊断审计。

Phase 2.2.1 replay summary 在 `runs/phase_2_2_1_generic_baseline_0_4_7_zz_combined_20260420_172644/benchmark_summary_20260420_172644.json`。本轮使用当前 12 条 Phase 2.2 pilot，启用 eval/judge，benchmark row 并行度为 3，每个 run 的 judge 并行度为 2。由于并发 W&B/Weave logging 暴露全局 run-state 串扰，本地接受版关闭了 W&B/Weave side effect；本地 artifact 和 eval summary 完整。

Phase 2.2.1 结果：

| 指标 | 0.4.7 generic baseline |
| --- | ---: |
| 样本数 | 12 |
| 平均 rounds | 3.67 |
| 平均 final candidates | 8.75 |
| final candidate count 为 0 的样本 | 1 |
| 平均 unique new candidates | 23.67 |
| 平均 final total | 0.6052 |
| 平均 final precision@10 | 0.6250 |
| 平均 final ndcg@10 | 0.5589 |
| 平均 round1 total | 0.3050 |
| 平均 round1 precision@10 | 0.2833 |
| 平均 round1 ndcg@10 | 0.3557 |

与 Phase 2.2 disable-eval pilot 相比，final candidate count 从每条 10 人下降到平均 8.75 人，主要 regression 是 `agent_jd_007` 从 10 人变为 0 人、`agent_jd_004` 从 10 人变为 5 人。其余 10 条仍有 10 人 final shortlist。这个下降记录为去特异化成本，不用 Agent/LLM 领域规则补回。

Phase 2 replay 结果：

| 指标 | Phase 1 | Phase 2 | 变化 |
| --- | ---: | ---: | --- |
| 6 个样本 final candidates 总数 | 41 | 60 | 明显提升 |
| final candidate count 为 0 的样本 | 1 | 0 | 修复 |
| `agent_jd_002` | 0 candidates / 0.0 precision | 10 candidates / 0.2 precision | 关键修复 |
| `agent_jd_006` | 1 candidate / 0.0 precision | 10 candidates / 0.9 precision | 关键修复 |
| 平均 final precision@10 | 约 0.42 | 约 0.55 | 整体提升 |
| `agent_jd_001` | 0.9 precision | 0.5 precision | recall 放宽后的 precision tradeoff |
| `agent_jd_003` | 1.0 precision | 0.8 precision | recall 放宽后的 precision tradeoff |

当前判断：Phase 2 达成主要目标。它把系统从“会被脏词和 exact title anchor 卡死”推进到“稳定有召回、可审计、可调参”的状态。后续要处理 precision tradeoff，但不能通过回滚 compiler 或恢复 exact title anchor 来处理。

Phase 2.1 replay 结果：

| 指标 | 0.4.4 |
| --- | ---: |
| 样本数 | 6 |
| 平均 rounds | 3.00 |
| 平均 final total | 0.6334 |
| 平均 final precision@10 | 0.6333 |
| 平均 final ndcg@10 | 0.6337 |
| 平均 round1 total | 0.2999 |
| 平均 round1 precision@10 | 0.2833 |
| 平均 round1 ndcg@10 | 0.3385 |

W&B report 已验证 `0.4.4` run count 为 6，`0.4.3` run count 回到 6。Phase 2.1 的 replay summary 在 `runs/phase_2_1_budget_precision_replay_0_4_4_20260419_151845/benchmark_summary_20260419_160948.json`。

Phase 2.1.1 replay 结果：

| 指标 | 0.4.5 |
| --- | ---: |
| 样本数 | 6 |
| 平均 rounds | 3.00 |
| 平均 final total | 0.5883 |
| 平均 final precision@10 | 0.5833 |
| 平均 final ndcg@10 | 0.5999 |
| 平均 final candidates | 10.00 |

W&B 中旧的单样本 `0.4.5` run 已删除；当前 `0.4.5` run count 为 6，和本地 benchmark summary 对齐。Phase 2.1.1 的 replay summary 在 `runs/phase_2_1_1_completed_round_stop_replay_0_4_5_20260420_101429/benchmark_summary_20260420_111000.json`。

Phase 2.1.2 replay 结果：

| 指标 | 0.4.6 |
| --- | ---: |
| 样本数 | 6 |
| 平均 rounds | 3.17 |
| 平均 final total | 0.6167 |
| 平均 final precision@10 | 0.6167 |
| 平均 final ndcg@10 | 0.6167 |
| judge cache reuse | 54.76% |

0.4.6 中 W&B run count 为 6，和本地 benchmark summary 对齐。Phase 2.1.2 的 replay summary 在 `runs/stop_quality_gate_replay_0_4_6_20260420_120041/benchmark_summary_20260420_125431.json`。本轮 terminal stop status 为 2 条 `low_quality_exhausted`、4 条 `pass`；没有发现“低质量且仍有未尝试 admitted family 却提前 stop”的样本。

## What We Learned

LLM 抽词不能直接进入 CTS search。LLM 很容易抽出语义相关但检索很脏的词，例如 `任务拆解`、`长链路业务问题`、`AgentLoop调优`、`211`。这些词适合 scoring、filter 或 explanation，不适合早期 keyword search。

职位名不是天然好 anchor。`AI Agent工程师`、`Agent算法工程师` 这类 title 对 JD 很自然，但候选人简历里不一定这么写。早期检索更应该使用简历侧常见表达，例如 `AI Agent`、`Agent`、`大模型`、`RAG`、`LangChain`。

Compiler/family/queryability 是正确边界。controller 不应该自由拼任意 LLM term，而应该只能从 compiler-admitted term 和合法 family 组合里选择。

真实 CTS replay 比单测更重要。单测证明合同正确，但只有真实 replay 能暴露 precision/recall tradeoff。Phase 2 的结果说明：zero/low recall 大幅改善，同时 broad anchor 会让部分原高精度样本回退。

后续 precision tuning 不能恢复 mandatory exact title anchor。正确方向是优化 budget context、stop policy、family coverage 和 query ordering，让系统先保证召回，再有纪律地收紧。

0.4.4/0.4.5 的平均 rounds 都是 3.00，这本身不异常。合理的 3 轮 stop 应该表示：核心 query family 已覆盖、top pool 稳定、继续搜索边际收益低。真正的问题不是“轮次太少”，而是低分样本也可能因为内部 top pool 被判为 `usable` 而早停。后续 stop policy 不能只看 productive rounds 和 `usable` pool，还要看强匹配数量、风险、未尝试 high-signal family，以及最终 precision 风险。

`usable` top pool 不是业务质量充分条件。它只说明内部候选池有一定可用信号，不等价于 benchmark judge 或人工 reviewer 会接受最终 shortlist。低分样本早停时，优先检查 stop quality gate 和简历侧 alias/phrase inventory，而不是简单增加轮数或恢复 exact title anchor。

0.4.6 的 stop quality gate 说明：继续增加轮次不是默认解法。低质量样本如果还有未尝试 admitted family，应该继续；如果 search space 已耗尽，则应该记录为 `low_quality_exhausted` 并把下一步转向 alias/family/benchmark attribution，而不是让 controller 跑满 10 轮。

不存在“对任何 JD 都完整正确”的搜索词清洗策略。当前阶段应只保留通用 hygiene 和 runtime contract：空白清理、去重、职位后缀清理、filter-only 约束词、明显软素质降级、notes/寻访问题降级、compiler-admitted-only、每轮少量词、family 不重复、同轮补拉去重和可归因 audit。不要把领域词表、领域 alias 或 domain overlay 放进 active path。

12 条 Phase 2.2 pilot trace 说明：主岗位 anchor 仍是最稳定召回来源；细粒度新术语和产品名常常低召回；寻访备注、目标公司范围、合规/沟通问题不应进入 keyword search；硬约束应走 CTS filter 或 runtime constraint；软素质和项目方法论更适合 scoring/evidence。下一步应把这些观察抽象成通用分类规则，而不是维护 Agent、LLM、大数据各自的词表。

JD 和简历之间存在表达延迟。行业可能已经不用某个框架，但候选人简历仍大量保留；JD 可能写新概念，简历可能写旧表达。因此 benchmark 不应要求人工在 replay 前预先写完整理想搜索概念，也不应靠人工维护 alias。长期如需领域适应，应由数据驱动循环自动提出候选、用 replay/eval 验证、满足 gate 后再启用。

语义词和检索表面词必须分离，但当前不应做 active surface canonicalization。`AI Agent`、`MultiAgent 架构` 这类词可以准确表达 JD 语义，但不一定是 CTS 里最高召回的简历表面词。它们现在只作为 audit 现象和后期数据驱动候选，不进入通用 baseline 的主动改写。

## Priority Roadmap

### Phase 2.1: Search Budget and Precision Tuning

状态：已完成，见 `docs/plans/completed/phase-2-1-search-budget-precision-tuning.md`。

目标：解决“有召回后停得太早”和 broad anchor 带来的 precision tradeoff。

要做：

- 给 controller context 增加预算状态：current round、min/max rounds、remaining rounds、budget-used ratio。
- 由 runtime 提供 stop guidance：当前是否允许停止、为什么、哪些 high-priority admitted families 还没试。
- 调整 query ordering：优先 broad anchor + high-signal framework/core skill，降低 broad anchor + 泛 domain phrase 的优先级。
- 保持 compiler 和 admitted-anchor 合同，不恢复 exact title anchor。

验收：

- zero-final 保持为 0。
- `agent_jd_002` / `agent_jd_006` 的修复不回退。
- `agent_jd_001` / `agent_jd_003` precision 有恢复或至少不继续明显恶化。
- replay 中能解释每次 stop 的预算和 coverage 原因。

### Phase 2.1.1: Completed-Round Stop Semantics

状态：已完成 completed-round stop semantics；0.4.5 replay 已作为版本化验收完成；不在本阶段扩大 stop quality gate。

目标：让“3-10 轮”的业务约束按已完成检索轮数生效。低质量 `usable` top pool 早停是后续风险项，但不是本阶段默认要改的策略。

已吸收发现：

- `round_no` 是 pending controller round，不等于已完成检索轮数。
- 0.4.4 中 `agent_jd_005` 在只完成 2 个检索轮后 stop，说明旧语义不符合“至少 3 轮检索”的业务预期。
- 平均 3 轮不天然是问题；低分样本早停才是问题。
- `usable` top pool 不能直接视为 shortlist quality 达标。

已完成：

- 给 `ControllerContext` 增加 `retrieval_rounds_completed`。
- stop guidance 的 `min_rounds` 判断基于已完成检索轮数。
- 增加测试：`min_rounds=3` 时，controller round 3 的 stop 必须被 forced continue，直到完成 3 个检索轮。

0.4.5 验收：

- 版本已 bump 到 `0.4.5`。
- 6 条 benchmark 均有 eval result。
- 6 条 benchmark 的 `rounds_executed` 均为 3，符合 completed-round stop semantics。
- W&B report 数据源中 `0.4.5` run count 为 6，不再保留旧单样本 run。

后续条件项：

- `retrieval_rounds_completed` 修复视为已实现，不再作为待探索事项。
- 如果后续真实 replay 或人工检查证明低分样本仍早停，再小范围增加 stop quality gate，例如 strong-fit count、high-risk fit count、untried high-signal family。
- 不能用盲目增加轮次换指标；新增轮次必须带来新 family、新 alias 或明确验证 exhaustion。

验收：

- `ControllerContext.retrieval_rounds_completed` 已存在并进入 controller context。
- stop guidance 的 `min_rounds` 判断使用已完成检索轮数，而不是 pending controller round。
- `test_runtime_min_rounds_count_completed_retrieval_rounds` 通过。

### Phase 2.1.2: Stop Quality Gate and Judge Cache Metric

状态：已完成；0.4.6 replay 已作为版本化验收完成。

目标：解决低质量 `usable` top pool 在预算仍充足时过早停止的问题，并让 W&B report 暴露 judge cache 复用率。

已完成：

- `near_budget_limit` 使用 `ceil(max_rounds * 0.8)`，默认 10 轮时从 controller round 8 起允许 budget stop。
- stop guidance 增加 `fit_count`、`strong_fit_count`、`high_risk_fit_count`、`quality_gate_status`。
- 在 80% budget 前，`usable` pool 且 `strong_fit_count < 3` 且仍有 untried admitted family 时强制继续。
- 如果低质量但无 untried admitted family，则允许停止并标记 `low_quality_exhausted`。
- `ControllerContext` 末尾新增 `budget_reminder`，提示当前 round、已完成检索轮、max rounds、80% threshold 和剩余预算。
- W&B run summary 增加 `judge_candidate_count`、`judge_cache_hit_count`、`judge_cache_hit_rate_pct`，report 版本均值表新增 `Judge cache reuse %`。

0.4.6 验收：

- targeted tests 通过：`tests/test_context_builder.py`、`tests/test_runtime_state_flow.py`、`tests/test_controller_contract.py`、`tests/test_evaluation.py`。
- 6 条 benchmark 均有 eval result，且 W&B 中 `0.4.6` run count 为 6。
- 平均 final total 从 0.4.5 的 0.5883 到 0.4.6 的 0.6167。
- 平均 rounds 从 3.00 到 3.17，没有靠跑满 10 轮换指标。
- judge cache reuse 为 54.76%。

### Phase 2.2: Benchmark Expansion and Term/Surface Audit Pilot

状态：已完成 pilot。

目标：避免在 6 条 Agent JD 上过拟合，并为后续 generic baseline 收集可归因证据。Phase 2.2 只收集证据和产出 term/surface audit artifact，不直接改变 compiler 清洗规则。

已完成：

- 从 6 条 Agent JD 扩到 12 条混合 JD：
  - 8 条 Agent / LLM App JD
  - 2 条 LLM training / inference infra JD
  - 2 条 bigdata 对照 JD
- 新增每个 run 的 `term_surface_audit.json`。
- benchmark summary row 增加 `term_surface_audit_path`。
- `docs/outputs.md` 记录该 artifact。
- 12 条 disable-eval smoke 全部完成；summary path：`runs/phase_2_2_term_surface_pilot_20260420_152036/benchmark_summary_20260420_155444.json`。

pilot 结果：

- 12/12 条都产出 final 10 candidates。
- rounds 范围为 3-4，平均 3.25。
- real CTS raw candidates 总数 415；去重后 unique new candidates 308。
- 总 term 数 108，实际用于 query 的 term 为 93。
- 5 条 run 产生 `AI Agent -> Agent` candidate surface rule，但 eval 关闭，不能 promoted。
- `MultiAgent 架构 -> MultiAgent` 只在测试覆盖，12 条真实 pilot 中没有足够证据。

从 12 条 trace 得到的通用策略候选：

- 保留职位主语作为召回 anchor，但 anchor 生成必须通用：清理职位后缀，不写 Agent/LLM 专属 anchor 改写。
- 硬约束不进 keyword：学历、学校类型、年龄、性别、工作年限、城市、薪资、目标公司等应进入 CTS filter、runtime constraint 或 scoring context。
- 寻访备注不进 keyword：是否接受创业公司、是否接受出差/出国限制、期望薪资、离职原因、面试流程、目标公司范围、候选人沟通问题都应降级为 notes/scoring/filter 信号。
- 软素质不进 keyword：沟通协作、责任感、自驱、抗压、推动落地、技术热情等应进入 scoring/evidence。
- 过窄新术语先不做强召回词：`上下文工程`、`Agent记忆系统`、`AI研发效能体系`、`Atlas Vector Search`、`沙箱技术`、`推理加速` 等应先通过 trace 判断贡献，不靠手工白名单。
- explicit 技术名可以作为候选召回词，但不靠领域词表排序；只按通用信号判断，例如是否来自 JD must-have、是否过长、是否来自 notes、是否连续低召回。
- surface alias 只记录，不生效：`AI Agent -> Agent` 这类规则留在 audit 中，不进入 generic baseline。

不做：

- 不从 12 条 pilot 推出领域特定 active rule。
- 不把 `AI Agent -> Agent`、`MultiAgent 架构 -> MultiAgent` 升级为 active rule。
- 不维护 Agent/LLM/bigdata 领域词表。
- 不把 domain router 接入 runtime 决策。
- 不把 query compiler 拆成微服务。

### Phase 2.2.1: Generic Retrieval Baseline and De-specialization

状态：已完成；0.4.7 combined replay 已作为 generic baseline。

目标：去掉领域特异 active 策略，只保留通用搜索卫生规则和通用 runtime contract。允许短期 benchmark 下降，用这一版作为后续所有改动的 generic baseline。

要移除或降级：

- Agent 专属 role anchor 改写，例如根据 title 中的 `agent` 特判生成 `AI Agent` 或 `Agent`。
- Agent/LLM 专属 broad-domain 注入，例如自动加入 `大模型`。
- 手工 known framework / known skill / domain family 对 query ordering 的领域偏置。
- active retrieval surface canonicalization；候选 alias 只能写入 audit，不改变 query。
- 任何需要人工维护的领域词表、领域 alias、domain overlay。

要保留：

- 文本空白清理、去重、casefold、职位后缀清理。
- compiler-admitted-only：controller 不能自由发明 query term。
- 每轮少量词：一个职位 anchor + 1-2 个非 anchor。
- family 不重复，避免一轮 query 被同类词堆满。
- hard constraints 和 notes 与 keyword search 分离。
- filter-only、score-only、blocked 的通用分类。
- 同轮补拉、跨轮去重、shortage/no-progress 记录。
- `search_diagnostics.json` 和 `term_surface_audit.json`。

验收：

- 12 条 pilot 能重新 replay，且没有大面积 zero-final。
- `search_diagnostics.json` 能说明每条失败或 shortage 来自 term、filter、CTS recall、dedup、stop 还是 scoring。
- `term_surface_audit.json` 仍能记录低召回 term 和候选 surface alias，但这些 alias 不改变 query。
- 结果中如果 Agent 样本下降，应记录为去特异化成本，不立刻用领域规则补回。
- 新增通用清洗规则必须能用 12 条 trace 中的多个跨领域例子解释，而不是只服务一个领域。

### Phase 2.3: Full-Chain Schema and Artifact Slimming

目标：降低全链路 JSON artifact 体积和 LLM structured-output 压力，但不改变 retrieval strategy、CTS filters、stop guidance、ranking、eval/judge 语义或业务可读 trace。

Phase 2.3 拆成三个独立 gate，避免把低风险 artifact 清理、中风险 draft schema 改造和高风险 scoring schema 实验混在一次 replay 中判断：

- Phase 2.3A Artifact Slimming Only：只清理持久化 artifact 重复内容。重点是 `LLMCallSnapshot` 元数据化、call artifact 不再嵌完整 payload/output、重复 normalized resume 和 context dump 改成 refs/hash/summary、`top_pool_snapshot.json` 改 slim 而不是删除、`events.jsonl` payload capped。
- Phase 2.3B Reflection + Finalizer Draft Slimming：Reflection 删除 prose assessment / critique 字段；Finalizer 引入 model-facing draft，由 runtime materialize 现有 public `FinalResult`。必须保留 finalizer validator 合同：不增删 candidate、不乱序、不重复、不引入 unknown id。
- Phase 2.3C Scoring Schema Experiment：单独评估 scoring schema 删除或派生 `evidence`、`confidence`、`strengths`、`weaknesses` 的影响。必须先定义 public `strengths` / `weaknesses` 如何生成，并用 eval/cached judge gate 验收。

验收：

- 2.3A 只用 no-eval replay 和 artifact size gate；overlap baseline rows 的 JSON/JSONL/MD 总体积至少下降 40%。
- 2.3A 之后 call artifact 不再包含完整 `user_payload` 或完整 `structured_output`，但必须保留 input/output artifact refs、sha256、char counts 和短摘要。
- 2.3B 必须保持 public `FinalResult` / `FinalCandidate` shape contract。
- 2.3C 必须跑 eval/cached judge，并比较 precision/nDCG、final ids、sort key stability 和抽样业务 trace 可读性。
- 所有子阶段都必须保持可审计和可归因；行为变化必须能归因到本阶段改动，否则回退对应改动。

### Phase 2.4: Reasoning Model A/B

目标：验证 controller/reflection 是否从 reasoning model 中获益。

顺序：

- 先试 reflection，再试 controller。
- 保持 structured output 小，不因为换 reasoning model 就扩 JSON schema。
- 如果结构化输出不稳定，先记录失败，不立刻做多模型 fallback 链。

验收：

- 与 deepseek-chat v3.2 非推理模型对比。
- 看困难 JD recall lift、precision、stop quality、token 成本、validator retry。
- 只有 context 和 stop policy 已经清楚时，reasoning model 的结果才有解释价值。

### Phase 3: Bounded Reflection Discovery

目标：把 reflection 从受限 critic 升级为有提案权的 discovery agent，但不让它直接执行。

边界：

- Reflection 只能输出小的 hypothesis proposal。
- Proposal 类型控制在 alias、family swap、bundle shift、stop hypothesis。
- Compiler 保留准入权，controller 保留选择权，verifier 保留否决/复核权。

验收：

- 提案被 compiler 接受的比例可衡量。
- 困难 JD 的 recall/precision 有 lift。
- 每单位 token 成本换来的 qualified candidate 数量可解释。

### Phase 4: Independent Verifier

目标：把最终 shortlist 的复核从 sourcing/scoring 主链路中分离出来，减少自我背书。

要做：

- verifier 不负责找人，只负责挑错。
- 检查 false positives、解释漏洞、硬约束一致性和候选排序风险。

验收：

- 人工 reviewer accept rate 提升。
- 明显 false positive 能被抓出。
- final explanation 更一致、更可信。

### Phase 5: Recruiter Session Memory

目标：支持招聘搜索中的 seeking + refinding，而不是只做单次检索。

要做：

- 保存历史 query path。
- 支持 backoff、重复搜索复用、候选人回看路径、保存搜索和订阅更新。
- 记录哪些 family/bundle 在相似 JD 上成功过。

验收：

- time-to-first-acceptable-candidate 下降。
- 重复搜索复用率上升。
- 人工重写次数下降。

### Phase 6: Web/App Action Harness

目标：在 CTS 策略稳定后，把同一个 harness 接到猎聘网页和 Boss app 的动作层。

顺序：

- 先猎聘网页，后 Boss app。
- Boss app 放最后，因为 app 自动化的不确定性和调试成本更高。

边界：

- 用户授权、动作受控、日志可回放、失败可恢复。
- 动作集保持窄：输入关键词、点筛选、翻页、抽取结果、保存证据。
- 不给泛浏览器 agent 到处乱逛。

验收：

- DOM/action 成功率、回放一致性、失败恢复率、账号风险事件、单次任务完成时长可衡量。

### Final Phase: Data-Driven Domain Adaptation Loop

状态：最后阶段，当前不启动。

目标：如果 generic baseline 已经稳定，且真实业务需要领域适应，再建立数据驱动循环。这个阶段不是人工维护词表，而是让系统从大量真实 JD、简历、judge/人工接受结果中提出候选策略，用 replay/eval 验证后再启用。

启动条件：

- generic-only baseline 已经建立，并有清晰 regression suite。
- benchmark 样本规模明显大于当前 12 条，且覆盖多个岗位族。
- term/surface audit、eval、人工接受/拒绝样本能形成稳定证据。
- 已有明确的策略回滚方式，能证明某条规则只在有收益时启用。

可以做：

- 自动发现长期低召回、低贡献或高误伤 term。
- 自动提出 candidate alias、candidate demotion、candidate filter-only/score-only 分类。
- 对候选策略做 A/B surface probe 或 replay gate。
- 只有通过多样本、多领域、eval/人工结果验证后，才允许策略 promoted。
- 人只 review 高影响策略和异常样本，不维护词表。

不做：

- 不手工维护领域词表。
- 不把单条 JD 或单次 trace 的发现直接变成 active rule。
- 不让 LLM 在线修改 active policy。
- 不引入必须依赖网络服务的 runtime policy lookup，除非本地 schema 和回滚机制已经稳定。

验收：

- 每条 promoted 策略都有数据来源、样本覆盖、前后对比、收益/风险、回滚路径。
- 策略启用后对 generic benchmark 没有明显回退。
- 人工工作量集中在 review 和风险确认，不是持续维护词表。

## Explicit Non-Goals

- 不把整个 SeekTalent runtime 改成开放式 agent。
- 不在第二个实现出现前创建大型抽象接口层。
- 不用 prompt-only patch 替代 compiler/query-plan 合同。
- 不恢复 mandatory exact title anchor。
- 不把 schema slimming 或 reasoning model A/B 排在 stop policy 和 precision tuning 前面。
- 不在 generic-only baseline 建立前做 query policy learning 或自动规则升级。
- 不在 generic-only baseline 建立前把 retrieval surface canonicalization 做成 active rule。
- 不维护人工领域词表、领域 alias 或 domain overlay。
- 不在 term audit schema 和本地接口稳定前引入 query policy 微服务。
- 不在 CTS 阶段混入网页/app 自动化的不确定性。
- 不用 6 条 Agent JD 的局部结果直接宣称生产稳定。

## Decision Gates

每个阶段开始前必须有独立计划，至少写清：

- 要改的模块。
- 不改的模块。
- replay 命令。
- acceptance metrics。
- stop rules。

每个阶段完成后必须记录：

- 测试结果。
- 如果该阶段包含真实 CTS replay，记录 summary 路径。
- 关键指标变化。
- regressions 和取舍判断。

任何 feature 都应尽量单独验证。不要再一次性混入多个 feature 后再用总指标判断成败。

如果指标冲突，优先级如下：

1. 避免 zero-final / zero-recall 硬失败。
2. 保持可审计和可归因。
3. 提升 precision。
4. 降低 token/CTS 成本。
5. 增加 agent 自主性。

当前下一步建议：进入 Phase 2.3A Artifact Slimming Only，先解决持久化 artifact 重复和体积问题，再按 2.3B/2.3C 分别处理 model-facing draft schema 和 scoring schema 实验。不要为了修复单个 Agent 样本而恢复领域词表、domain router、retrieval surface active rule、自动规则升级或 query policy 微服务。
