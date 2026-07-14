# GPT-5.6 Sol 发布后 SeekTalent 是否仍需 Superpowers

日期：2026-07-13

## 结论

**不再有必要把 Superpowers 当作 SeekTalent 的全量默认生命周期；但仍有必要保留它最有价值的质量约束。**

建议从“所有任务都经过 Superpowers”改成“Codex/GPT-5.6 原生执行 + 少量按风险触发的 Superpowers 门禁”：

1. 始终保留 `verification-before-completion` 的语义：没有本轮新鲜证据，不得声称完成。
2. Bug、失败测试和异常行为保留 `systematic-debugging`：先复现、定位根因、形成单一假设，再修。
3. 行为变化保留 TDD 的 red-green 核心，但不必照搬它所有强硬措辞和仪式。
4. 只有跨合同、存储、UI、迁移或多模块的非平凡变更才进入设计/计划，并在开工前用 `plan-review-lite`。
5. 原生 subagent 主要用于并行只读探索、测试、审计和独立复核；代码写入仍由一个执行 owner 负责。
6. `ship-readiness-lite` 只在用户明确询问交付、合并或发布准备度时使用；推送、PR、合并、发布和清理继续单独授权。

应从默认链路移除：`using-superpowers` 的“1% 可能适用就必须加载”、每个创意任务都走完整 `brainstorming`、每个小步骤都创建实现 subagent + 审查 subagent 的 `subagent-driven-development`、以及对简单修复也强制生成超细计划。

本报告只是研究结论，没有修改当前项目/会话指令；在用户明确调整规则前，现有“SeekTalent 默认使用 Superpowers”的指令仍然有效。

## 先纠正两个前提

### 1. “GPT-5.6 Sol”是官方公开名称

这次不存在名称上的不确定性。OpenAI 官方模型页列出 `gpt-5.6-sol`，并说明 `gpt-5.6` 别名路由到 Sol；官方 2026 年 7 月 6–10 日更新也把 Sol 定义为 GPT-5.6 系列中面向复杂编码、计算机使用、研究和安全工作的旗舰型号。

来源：[GPT-5.6 Sol 模型页](https://developers.openai.com/api/docs/models/gpt-5.6-sol)、[OpenAI What's new](https://learn.chatgpt.com/docs/whats-new#choose-the-right-gpt-56-model)。

### 2. “自带使用 subagent 的习惯”只对了一半

原生能力确实已经完整：Codex 可以创建、调度、等待、汇总和关闭 subagent；Ultra 模式会用 subagent 并行处理可拆分任务。但官方文档同时明确：

- 当前本地 Codex 在用户直接要求，或项目/skill 指令要求时才会委派；并非所有普通任务都会无条件 fan-out。
- subagent 比同类单 agent 运行消耗更多 token。
- “大多数任务不需要 Max 或 Ultra”。
- 官方建议先把 subagent 用于探索、测试、triage、总结等只读工作；并行写代码更容易冲突并增加协调成本。

来源：[触发 subagent](https://learn.chatgpt.com/docs/agent-configuration/subagents#triggering-subagent-workflows)、[编排与线程控制](https://learn.chatgpt.com/docs/agent-configuration/subagents#orchestration-and-thread-controls)、[subagent 的适用形状](https://learn.chatgpt.com/docs/agent-configuration/subagents#why-subagent-workflows-help)、[Max 与 Ultra](https://learn.chatgpt.com/docs/models#know-when-to-use-max-or-ultra)。

因此，GPT-5.6/Codex 原生能力替代的是 Superpowers 的**编排机制**，不是自动替代测试、根因分析、验收标准和授权边界。

## “模型更谨慎”不能替代哪些东西

官方文档没有把“谨慎”定义为一个可依赖的产品保证。更准确的官方表述是：GPT-5.6 能主动且持续地完成多步任务，因此应明确授权和批准边界。官方编码建议仍要求在提示中定义工程角色、工具工作流、彻底测试和补丁验证；长任务仍建议明确规划、持久执行和 TODO 跟踪。

这说明更强模型减少的是执行摩擦，不是流程责任。至少以下约束不能因为模型更强而删除：

| 能力 | GPT-5.6/Codex 原生能力 | 仍需项目约束的部分 |
|---|---|---|
| 规划与持续执行 | 强，官方称适合含规划、工具、验证和 follow-through 的多步工作 | 必须定义完成标准、范围和停止条件 |
| subagent 编排 | 已原生支持，Ultra 可自动并行 | 必须限制何时 fan-out、谁能写代码、如何复核 |
| 谨慎与授权 | 可按明确边界持续执行 | 外部写入、破坏性动作、发布、合并仍需硬授权 |
| 测试 | 模型能写和运行测试 | 官方仍要求显式指示测试与验证；能力不等于 red-green 证据 |
| 调试 | 推理更强、上下文更大 | 根因优先、单一假设、最小实验不是自动保证 |
| 完成声明 | 能汇总工作 | 仍必须读取新鲜命令输出，不能信任自己或 subagent 的成功口头报告 |

来源：[GPT-5.6 prompting best practices](https://developers.openai.com/api/docs/guides/latest-model#prompting-best-practices)、[Prompt engineering: Coding](https://developers.openai.com/api/docs/guides/prompt-engineering#coding)、[Subagent model choice](https://learn.chatgpt.com/docs/agent-configuration/subagents#model-choice)。

## Superpowers 中仍然有独立价值的部分

### 保留：验证门禁

`verification-before-completion` 要求先确定能证明声明的命令，运行完整命令，读取输出和退出码，再决定能否声称成功；它也明确要求独立验证 subagent 的报告。这比“模型一般比较谨慎”更具体、更可审计。

本地一手来源：`/Users/frankqdwang/.codex/plugins/cache/openai-curated-remote/superpowers/6.1.1/skills/verification-before-completion/SKILL.md:16-38,40-50,102-106`。

### 保留：系统调试

`systematic-debugging` 固化了 SeekTalent 特别需要的行为：先读完整错误、稳定复现、检查最近变更、跨组件收集边界证据、追踪数据源头，再用单一假设做最小实验，最后加回归测试修根因。

本地一手来源：`/Users/frankqdwang/.codex/plugins/cache/openai-curated-remote/superpowers/6.1.1/skills/systematic-debugging/SKILL.md:16-23,46-120,145-190`。

### 保留核心、放松仪式：TDD

`test-driven-development` 最有价值的是“先看到测试因正确原因失败，再写最小实现，然后看到全绿”。这一点能证明回归测试确实捕获了缺失行为。应保留这个核心，尤其是公共行为、关键路径和 bugfix；但没有必要把“任何重构都必须删掉先写的代码重新开始”等所有绝对规则原样作为全项目默认。

本地一手来源：`/Users/frankqdwang/.codex/plugins/cache/openai-curated-remote/superpowers/6.1.1/skills/test-driven-development/SKILL.md:8-14,31-45,113-128,168-183,327-340`。

## 已经高度冗余或成本过高的部分

### `using-superpowers` 的全局强制触发

该 skill 要求只要有 1% 可能适用，就必须在任何回复、澄清或文件检查前加载 skill。这与 OpenAI 对 GPT-5.6 的最新建议发生明显张力：官方建议减少重复指令、只暴露与任务相关的工具，并在代表性任务上逐步删减提示；其内部编码 agent 样本中，精简提示同时改善分数并显著减少 token，但官方也提醒结果需按本项目实测。

本地一手来源：`/Users/frankqdwang/.codex/plugins/cache/openai-curated-remote/superpowers/6.1.1/skills/using-superpowers/SKILL.md:10-24,33-50`。官方来源：[Favor leaner prompts](https://developers.openai.com/api/docs/guides/latest-model#favor-leaner-prompts)。

### 完整 brainstorming 对所有创意工作一刀切

当前 `brainstorming` 不论任务大小都强制：逐题澄清、2–3 个方案、分节审批、写 spec、提交 spec、再让用户审核、最后才写计划。它适合真正开放的产品或架构问题，但对明确的小修复、配置、文档和单模块变更会形成不必要停顿。

本地一手来源：`/Users/frankqdwang/.codex/plugins/cache/openai-curated-remote/superpowers/6.1.1/skills/brainstorming/SKILL.md:10-18,20-32,102-131`。

### 每任务“实现者 + 审查者”的 subagent-driven development

Codex 已经原生拥有相同的线程编排能力。Superpowers 版本仍规定每个计划任务都启用新实现 subagent、任务审查 subagent、修复循环和最终全分支审查；skill 自身也承认成本是更多 subagent 调用、控制器准备和审查迭代。

本地一手来源：`/Users/frankqdwang/.codex/plugins/cache/openai-curated-remote/superpowers/6.1.1/skills/subagent-driven-development/SKILL.md:6-17,45-82,335-365`。

对 SeekTalent 更合适的是：一个写入 owner；只读探索、测试、审计可并行；对 red/yellow zone 或大改动安排一次独立复核。这样同时符合官方对并行写入的警告和本项目治理要求。

## SeekTalent 当前 repo truth

当前仓库本身已经提供了比“模型谨慎”更可靠的项目边界：

- `AGENTS.md` 要求直接、简单、低仪式的 Python，实现最小完整改动，避免无收益的抽象和流程表演：`AGENTS.md:5-19,104-128,110-118`。
- 它同时要求公共行为、失败模式和关键路径带测试或更新测试，重要路径不得在没有验证时冒险修改：`AGENTS.md:96-102`。
- 输出纪律只要求非平凡 feature 在动代码前给一个短编号 checklist，并明确反对大改写和长解释：`AGENTS.md:142-146`。
- 治理文档已经按 red/yellow/green 路径定义 owner review、合同测试、Workbench 验证和 red-zone 验证：`docs/governance/ai-coding-policy.md:3-36,38-56`。

这些规则与“精简 Superpowers，而不是删除质量门禁”完全一致。

另一方面，仓库也显示全量 Superpowers 已经产生明显的流程体量。2026-07-13 本地统计命令：

```bash
find docs/superpowers/specs docs/superpowers/plans -type f -name '*.md' -print0 | xargs -0 wc -l
```

结果为 23,407 行正式 spec/plan；最大的 `docs/superpowers/plans/2026-07-11-candidate-quality-first-page-expansion.md` 为 4,446 行。该计划确实留下了严格执行、独立 Gate R 和生产验收证据，说明旧流程在高风险跨层项目中有真实价值；但同一文件的体量也说明它不应再是普通任务的默认成本。

本地一手来源：`docs/superpowers/plans/2026-07-11-candidate-quality-first-page-expansion.md:11-16,50-52,4117-4126`。

## 推荐的新默认工作流

### A. 简单、范围清楚的任务

直接由一个 Codex owner 执行：读 live repo truth → 做最小修改 → 跑相关验证 → 报告证据。无需 brainstorming、完整 spec、超细 plan 或 subagent。

### B. Bug / 失败测试 / 异常行为

使用 `systematic-debugging` 的四段核心；修复前建立能失败的回归证明；修复后用新鲜输出验收。必要时让只读 subagent 并行查日志或映射调用链，但写入 owner 仍只有一个。

### C. 非平凡 feature，尤其合同、存储、UI、迁移、多模块

先做短设计和实施计划；使用 `plan-review-lite` 检查 scope、边界、合同、测试、可观测性和 rollout。计划不必默认包含逐行实现代码，也不必默认一任务一 commit/一 reviewer；复杂或高风险 slice 再按需升级。

本地一手来源：`/Users/frankqdwang/.codex/skills/plan-review-lite/SKILL.md:8-27,29-49`。

### D. Red/yellow zone 或重要集成

遵守 `docs/governance/ai-coding-policy.md` 的 owner review 和对应验证。可使用一次独立 review subagent；只有当任务天然可分、写集合不冲突且收益足够大时才增加更多 agent。

### E. 准备交付

用户明确问 readiness 时才用 `ship-readiness-lite`，检查 live branch/status/diff/tests/CI/docs/risk/rollback；任何推送、PR、合并、部署、发布和清理都保持单独批准。

本地一手来源：`/Users/frankqdwang/.codex/skills/ship-readiness-lite/SKILL.md:8-25,27-40`。

## 最小保留集

如果目标是最简洁、同时不降低 SeekTalent 的安全变更质量，建议把 Superpowers 收缩为：

- `systematic-debugging`
- `test-driven-development`（保留 red-green 核心，按风险使用）
- `verification-before-completion`
- `writing-plans`（仅非平凡任务，并采用更短计划）
- `requesting-code-review`（仅 major/red-zone/merge 前，非每任务）
- `using-git-worktrees`（仅需要隔离时）

再配合本机的两个 gstack-lite gate：

- `plan-review-lite`
- `ship-readiness-lite`

不再作为默认链路：

- `using-superpowers`
- 全任务强制 `brainstorming`
- 全任务强制 `subagent-driven-development`
- 每任务固定双重 review loop
- 对简单任务的 spec → 超细 plan → 多次审批链

## 最终判断

**Superpowers 的“机制层”已经大部分被 GPT-5.6 + Codex 原生能力替代；它的“纪律层”仍然必要。**

所以最合理的变化不是“完全卸载 Superpowers”，而是取消它的全局默认控制权，留下三条硬纪律——根因优先、red-green、证据后声明——再按 SeekTalent 的实际风险区和任务形状选择性加载规划、审查和 readiness skill。这样既利用 GPT-5.6 Sol 的强规划、持久执行和原生 subagent，又避免把 2026 年初为较弱 agent 设计的完整仪式继续施加到每一个小任务上。
