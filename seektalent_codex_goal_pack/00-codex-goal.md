# Codex Goal：SeekTalent 源解耦、稳定化与重构

## 目标

一次性完成 SeekTalent 的重要拆分、清理和重构，使系统从“CTS/Liepin 被 runtime 硬编码驱动”变成“runtime 只依赖通用源契约，CTS、Liepin 和后续新数据源通过外部注册接入”。

## 执行控制

后续使用 Codex Goal 执行时，必须先读取：

1. `13-execution-control.md`
2. `12-execution-sequence.md`
3. `10-acceptance.md`

在修改产品代码前，必须按 `13-execution-control.md` 创建 progress ledger，并记录分支、HEAD、`origin/main`、merge-base、dirty state、stash 列表和第一阶段 preflight 结果。未关联本目标的 dirty files 不得覆盖；如果当前阶段必须编辑已 dirty 的产品文件，先暂停并询问。

本目标必须同时完成：

1. CTS、Liepin 与核心 runtime 完全解耦。
2. Liepin 搜索路径稳定化，尤其是 OpenCLI 与 Liepin worker 的边界收敛。
3. 前端、BFF、后端三层边界重新整理，前端重设计不再牵动 backend。
4. 删除 AI coding 累积的兼容、fallback、重复、防御性、耦合代码。
5. 一次性修复 GitHub issues `#58` 到 `#69`。
6. 精简并刷新文档，使文档与真实代码一致。
7. 在改产品代码前，重新校准 AI coding harness：既要防止执行漂移，也不能用过重流程压死重构自由度。

## 不可妥协项

- 不允许交付 MVP、骨架、空接口、空适配器、占位实现、TODO 驱动实现。
- 不允许只把旧代码包一层新 facade，然后保留核心耦合。
- 不允许继续让 runtime 中出现 provider-specific 分支，例如 `if source == "liepin"`、`if source == "cts"`。
- 不允许把 OpenCLI、Liepin worker、CTS transport 的细节留在 runtime。
- 不允许为“兼容旧版本”保留未上线阶段的旧路径；该删就删。
- 不允许把 BFF 做成薄透传层；BFF 必须吸收前端数据结构变化。
- 不允许为了过 type/lint/test 增加无意义 fallback、防御性分支或全局 ignore。
- 不允许改完后只跑局部测试；必须跑目标验收命令。
- 不允许文档继续描述旧架构。

## 完成定义

完成后应满足：

- runtime 可以在不修改 runtime 代码的情况下接入一个第三方测试源。
- CTS 与 Liepin 都通过同一类源注册机制被启用、规划、执行、合并、审计。
- Liepin card/detail 路径仍可在测试/fake/worker 模式中完整通过，且浏览器生命周期不再每次请求启动 Chromium。
- 前端组件主要消费前端 view model；BFF contract 改动集中在 BFF，不要求 backend 模型随前端设计变动。
- 12 个 issue 对应测试全部更新或新增，并通过。
- 旧文档被删除、归档或改写；活跃文档短、准、面向 code agent。
- harness 允许本次跨层重构，但用目标清单、架构检查和验证证据约束漂移。

## 输出要求

最终 PR 必须包含：

- 产品代码修改。
- 测试和机器检查修改。
- harness/governance 修改。
- 文档修改。
- 删除旧代码和旧文档的 diff。
- 验证命令结果摘要。

这是一项完整重构，不是阶段 1。
