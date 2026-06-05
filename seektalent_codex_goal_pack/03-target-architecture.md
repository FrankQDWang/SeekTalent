# 目标架构

## 总原则

核心 runtime 只做：

- 需求理解
- 查询意图生成
- 源计划编排
- 源结果合并
- 候选人身份合并
- 评分、反思、最终排序
- 运行状态、事件、artifact

runtime 不做：

- CTS 查询构造细节
- Liepin worker/OpenCLI 细节
- provider-specific error code
- provider-specific budget field
- provider-specific detail lease shape
- 前端 DTO 投影
- BFF 响应裁剪

## 目标分层

```text
apps/web-svelte/
  Frontend only.
  只消费 BFF API 和本地 view model。

src/seektalent_ui/
  BFF/API only.
  负责 auth、session、projection、frontend contract、SSE、Workbench persistence。
  不把 backend 内部模型直接暴露给 frontend。

src/seektalent/
  核心 runtime 与通用业务逻辑。
  不 import seektalent_ui。
  不 import concrete provider package。

src/seektalent/sources/
  Runtime-neutral source contracts and registry.
  只包含通用源协议、注册、计划、结果、公共 reason code 规范。
  不包含 CTS/Liepin 实现。

src/seektalent/providers/cts/
  CTS external source implementation.

src/seektalent/providers/liepin/
  Liepin external source implementation.

apps/liepin-worker/
  Liepin browser worker implementation.
```

## Source 调用方向

目标依赖方向：

```text
runtime  ───────► sources/contracts
runtime  ───────► sources/registry interface
BFF/API  ───────► runtime
BFF/API  ───────► concrete provider bootstrap
CTS      ───────► sources/contracts + core retrieval + clients/cts
Liepin   ───────► sources/contracts + worker client/store/mapper
worker   ───────► Playwright/browser
```

禁止方向：

```text
runtime  ─X────► providers/cts
runtime  ─X────► providers/liepin
runtime  ─X────► clients/cts transport
providers ─X───► runtime/source_lanes
providers ─X───► BFF
frontend ─X────► backend internal models
```

## Runtime 与 Source 的关系

runtime 接收一个 source registry。registry 中每个 source 提供：

- source id
- label
- capabilities
- budget defaults
- readiness/posture
- plan builder
- card lane runner
- optional detail lane runner
- public event/reason mapping
- optional source-specific admin/BFF hooks outside runtime

runtime 不知道 source id 的含义。`cts`、`liepin` 只是注册进来的字符串。

## BFF 与 Frontend 的关系

BFF 是前端唯一数据契约。backend 数据结构变化只影响 BFF mapper，不直接影响 frontend component。

目标：

- 前端组件消费 `apps/web-svelte/src/lib/workbench/viewModels.ts` 一类本地 view model。
- OpenAPI/generated 类型只允许出现在 API client 或 BFF adapter 边界。
- SSE event schema 版本稳定。
- 数据增量更新在 BFF/frontend data layer 处理，组件不关心 backend 事件细节。

## 文档目标

活跃文档只描述完成后的真实结构：

- `docs/architecture.md`
- `docs/data-flow.md`
- `docs/source-contracts.md`
- `docs/ui.md`
- `docs/development.md`
- `docs/governance/ai-coding-policy.md`

旧分析性文档若仍有价值，移入 `docs/archive/`；无价值直接删除。
