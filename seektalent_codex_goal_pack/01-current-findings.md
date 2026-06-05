# 当前仓库发现

以下发现来自当前 `FrankQDWang/SeekTalent` 仓库主分支代码和开放 issue。它们是本目标的输入事实，不是最终设计。

## 仓库入口和权限

- 目标仓库：`FrankQDWang/SeekTalent`
- 默认分支：`main`
- 当前仓库处于未上线、高速迭代阶段，因此不应为未发布旧结构支付长期兼容成本。

## Runtime 与源强耦合

当前 runtime 仍直接知道 CTS、Liepin 和 OpenCLI 细节。

关键证据：

- `src/seektalent/runtime/source_lanes.py`
  - `SourceKind = Literal["cts", "liepin"]`
  - `RuntimeSourceBudgetPolicy` 直接包含 `max_cts_pages`、`cts_page_size`、`liepin_*` 预算字段。
  - runtime safe reason code 中包含大量 `liepin_opencli_*`。
  - `RuntimeApprovedDetailLease` 默认 `source="liepin"`，并拒绝非 Liepin。
  - `normalize_source_kinds()` 只允许 `cts` 和 `liepin`。
  - `build_runtime_source_plan()` 直接为 CTS 和 Liepin 构建 plan。

- `src/seektalent/runtime/orchestrator.py`
  - 直接 import `seektalent.providers.liepin.runtime_lane`、`LiepinWorkerClient`、`project_constraints_to_cts`。
  - `_run_source_lane_safely()` 对 `cts` 和 `liepin` 做显式分支。
  - `_run_cts_source_lane()` 在 runtime 内构造 CTS 查询。
  - `_run_liepin_source_lane_request()` 在 runtime 内调用 Liepin lane。

- `src/seektalent/providers/registry.py`
  - registry 通过 `if source == "cts"`、`if source == "liepin"` 分支创建 provider。
  - 该 registry 仍是硬编码工厂，不是可扩展源注册表。

## Provider 与 runtime 反向依赖

当前 `tach.toml` 允许：

- `seektalent.runtime` 依赖 `seektalent.providers`
- `seektalent.providers` 依赖 `seektalent.runtime`

这形成 provider/runtime 双向依赖。目标状态必须打断这个循环。

典型证据：

- `src/seektalent/providers/liepin/filter_compiler.py` 从 runtime import `RuntimeSourceBudgetPolicy` 和 `RuntimeSourceQueryIntent`。
- `src/seektalent/providers/liepin/runtime_lane.py` 从 `runtime.source_lanes` import 大量 runtime lane DTO。

## Liepin 脆弱点

当前 Liepin 的 fragile root 不是单点 bug，而是多变量混在一起：

- runtime 知道 Liepin backend mode 和 OpenCLI safe reason。
- Liepin provider 知道 runtime DTO。
- worker mode 同时影响调度并发、错误语义、浏览器生命周期。
- `apps/liepin-worker/src/server.ts` 的生产 card/detail handler 每次请求都 `chromium.launch()`，然后关闭 browser。
- OpenCLI 模式在 logical query bundle 中通过顺序执行特殊处理；该特殊性应留在 Liepin provider/worker 边界，而不是 runtime。

## 前端/BFF/backend 分层现状

当前结构大体是：

- Frontend：`apps/web-svelte`
- BFF/API：`src/seektalent_ui`
- Backend/runtime：`src/seektalent`

主要问题：

- 前端导入后端 OpenAPI 生成 schema，容易被 BFF DTO 改动牵动。
- BFF 中存在响应转换、SQLite 访问、权限、投影、运行时图构建混杂。
- 前端通过 SSE 事件触发全量 query invalidation，长会话中容易反复拉全量事件。
- 图布局在前端可能重复计算和 O(n²) 碰撞扫描。
- 目前文档中已有分层分析，但它是分析材料，不是完成后的 source of truth。

## AI coding 累积问题

当前治理文档已经强调：

- 不要防御性 fallback spam。
- 不要保留没有价值的兼容层。
- 不要把 prompt/runtime/provider/BFF/frontend/config 混在普通 PR 中。

但本次需求恰好是一次必须跨层完成的大重构。当前 harness 对普通 PR 合理，对这次 one-shot goal 过强；同时对 provider/runtime 解耦、BFF contract、文档真实性这些关键边界又不够强。
