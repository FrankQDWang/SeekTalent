# 文档刷新计划

## 目标

文档只为 code agent 和维护者服务。要短、准、结构清楚，不包含大量代码，不重复实现细节。

## 当前文档问题

- 一些文档描述 CTS-only runtime sequence。
- 一些文档描述旧的 provider registry 和 runtime/provider 依赖方向。
- 分层分析文档过长，更像一次审计报告，不适合作为长期 source of truth。
- 文档里有具体版本号、端点清单、代码片段，容易快速过期。
- 文档和真实代码不一致时会误导 code agent。

## 活跃文档目标结构

保留或更新：

```text
docs/README.md
docs/architecture.md
docs/data-flow.md
docs/source-contracts.md
docs/ui.md
docs/development.md
docs/configuration.md
docs/outputs.md
docs/governance/ai-coding-policy.md
docs/governance/github-ruleset-checklist.md
```

可选保留：

```text
docs/governance/agent-goals/source-decoupling-2026-06.md
```

删除或归档：

- 过长的分层分析报告。
- 旧版本计划。
- 与当前代码不一致的 generated plans。
- 只包含已完成 TODO 的文档。
- 描述 CTS/Liepin 硬编码旧路径的文档。

## 每个文档的内容边界

### `docs/architecture.md`

只描述：

- 三层结构。
- source registry 关系。
- runtime sequence 的 source-agnostic 流程。
- BFF 与 frontend 边界。
- 禁止依赖方向。

不要列大段端点清单和代码。

### `docs/data-flow.md`

描述：

- input -> requirements -> query intents -> source plans -> source results -> identity merge -> scoring -> finalization。
- Workbench persistence -> BFF projection -> frontend view model。
- event stream incremental flow。
- detail approval flow。

不要贴具体函数实现。

### `docs/source-contracts.md`

描述：

- source id。
- capabilities。
- plan/request/result/evidence/event。
- public reason code。
- registration。
- provider-specific data 如何被隔离。

### `docs/ui.md`

描述：

- 本地 Workbench 启动。
- 前端/BFF 运行命令。
- 事件流和 cache 行为的高层说明。
- Liepin 登录/connection 操作说明。

### `docs/development.md`

更新：

- 新 verification commands。
- 新 source boundary checks。
- major refactor goal mode。
- 普通 PR 规则仍适用的说明。

### `docs/governance/ai-coding-policy.md`

更新：

- 普通 PR 规则。
- major refactor exception。
- required manifest。
- red-zone verification。
- 删除旧代码和文档的要求。

## 写作规则

- 面向 code agent，不面向市场。
- 每个文档开头说明“这份文档回答什么问题”。
- 使用路径名和数据流，不贴长代码。
- 不写愿景口号。
- 不保留“未来可以”类空泛内容。
- 文档中出现的命令必须能运行。
- 文档中出现的架构边界必须有对应测试或检查。

## 文档验收

- `docs/README.md` 中列出的 active docs 全部与代码一致。
- 不再有 active docs 把 CTS 当成唯一 source。
- 不再有 active docs 描述 runtime import concrete provider。
- 不再有 active docs 建议前端直接消费 backend internal DTO。
- `docs/archive/` 中的旧材料明确不是 source of truth。
