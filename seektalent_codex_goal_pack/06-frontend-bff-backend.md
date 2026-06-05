# 前端 / BFF / 后端三层解耦

## 目标

前端将由设计师大改。前端结构变化不应影响 backend；backend 数据结构变化也不应直接破坏前端。数据形状变化集中在 BFF。

## 目标职责

### Backend runtime

负责：

- 业务事实
- source execution
- scoring
- identity merging
- final top candidates
- artifacts
- domain events

不负责：

- 前端展示 DTO
- 前端布局字段
- 前端 loading/flicker 策略
- Graph/board/list UI 结构

### BFF `src/seektalent_ui`

负责：

- auth/session/security
- Workbench persistence
- backend -> frontend contract mapping
- frontend-specific aggregation/cropping
- SSE event contract
- detail approval API
- source connection API
- source policy API
- OpenAPI schema generation

BFF 可以依赖 runtime 和 provider bootstrap；runtime 不能依赖 BFF。

### Frontend `apps/web-svelte`

负责：

- UI
- route-level loading
- local interaction state
- view model rendering
- query cache behavior
- design system

不负责：

- backend model normalization
- provider-specific error mapping
- candidate identity merging
- final ranking
- source budgets
- raw API schema scattering across components

## BFF contract 层

建议结构：

```text
src/seektalent_ui/bff/
  contracts.py
  session_projection.py
  candidate_projection.py
  source_projection.py
  event_projection.py
  graph_projection.py
  resume_snapshot_projection.py
```

如果现有文件更适合保留名称，也可以不强行改目录；但必须形成清晰边界：

- route 只做 HTTP/input/auth 调度。
- store 只做 persistence。
- projection/contract 层负责 frontend response。
- backend/runtime model 不直接穿透到 response。

## Frontend view model 层

建议结构：

```text
apps/web-svelte/src/lib/api/
  client.ts
  workbench.ts
  generated schema only at API boundary

apps/web-svelte/src/lib/workbench/
  viewModels.ts
  eventStream.ts
  queries.ts
  runtimeGraphView.ts
```

规则：

- Svelte components 不直接 import generated OpenAPI schema 类型。
- generated schema 只在 API adapter 层使用。
- API response 立即转换成 frontend view model。
- 设计师改 UI 时优先改 components/viewModels，不改 backend runtime。

## 修复加载慢和闪烁

必须至少处理：

### 1. Event stream 增量追加

对应 issue `#59`。

当前模式是 SSE 后 invalidates `sessionEvents(sessionId, 0)`，导致从头拉事件并重新处理。目标：

- 用 latest `globalSeq` 增量 append。
- 显式 dedupe 和排序。
- 只对影响 session summary/graph/candidates 的事件 invalidate 对应 query。
- 长 session fixture 覆盖无重复、无丢失。

### 2. Graph layout 缓存和 O(n²) 扫描

对应 issue `#62`。

目标：

- 按 graph structure identity + bounds 缓存/debounce。
- deterministic business layout 足够时不启动 ELK。
- collision separation 使用 lane/column bucket，避免全量扫描。
- 保留 manual drag 行为和可读性。

### 3. 初始加载分层

不要把所有数据塞进一个大响应。BFF 应提供合理聚合：

- session summary
- candidate list/page
- graph summary/page
- resume snapshot lazy load
- event stream incremental updates

不要在本目标中引入 GraphQL。当前阶段 BFF endpoints + view model 就够。

## BFF 验收

- 前端 component 主要消费 view model。
- BFF contract tests 覆盖核心 response。
- 修改 backend runtime model 字段时，前端只需改 BFF projection 或 view model adapter。
- `bun run test` 和 `bun run test:e2e` 通过。
- OpenAPI schema 生成仍可用，但不再把 generated schema 泄漏到组件层。
